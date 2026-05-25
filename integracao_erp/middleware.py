"""
Módulo 4: Middleware de Integração com ERP (ex: Sienge)
Dispara atualização de Pedido de Compra após entrada física confirmada.
Implementa fila de retry com backoff exponencial e dead letter queue no banco.
"""
import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class PayloadEntregaERP:
    pedido_id: str
    status: str
    centro_custo: str
    data_recebimento: str
    id_ativo: str
    descricao_ativo: str
    responsavel_recebimento: str


class ERPIntegrationError(Exception):
    """ERP retornou erro não recuperável (4xx)."""


class ERPUnavailableError(Exception):
    """ERP inacessível — retry programado."""


class SiengeClient:
    """
    Cliente HTTP para a API REST do Sienge (ou qualquer ERP compatível).
    Substitua ERP_BASE_URL e os endpoints conforme a documentação do seu ERP.
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def atualizar_pedido_entregue(self, payload: PayloadEntregaERP) -> dict:
        """
        PUT /api/v1/purchase-orders/{pedido_id}
        Marca o pedido como 'Entregue' e associa ao centro de custo.
        """
        url = f"{self.base_url}/api/v1/purchase-orders/{payload.pedido_id}"
        body = {
            "status": payload.status,
            "costCenter": payload.centro_custo,
            "deliveryDate": payload.data_recebimento,
            "assetId": payload.id_ativo,
            "receivedBy": payload.responsavel_recebimento,
        }
        try:
            resp = self._session.put(url, json=body, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError as exc:
            raise ERPUnavailableError(f"ERP inacessível: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise ERPUnavailableError(f"Timeout ao conectar ao ERP: {exc}") from exc
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response else 0
            if 400 <= status < 500:
                raise ERPIntegrationError(f"Erro do cliente ERP ({status}): {exc.response.text}") from exc
            raise ERPUnavailableError(f"Erro do servidor ERP ({status})") from exc


class ERPMiddleware:
    """
    Orquestra o envio de eventos de movimentação para o ERP com:
    - Retry automático com backoff exponencial (até max_retries)
    - Persistência em fila de banco de dados para não perder dados offline
    """

    MAX_RETRIES = 5
    RETRY_DELAYS = [5, 15, 60, 300, 900]  # segundos: 5s, 15s, 1min, 5min, 15min

    def __init__(self, erp_client: SiengeClient, db_conn):
        self.erp = erp_client
        self.db = db_conn  # conexão psycopg2 ou similar

    def processar_entrada(
        self,
        id_movimentacao: int,
        id_ativo: str,
        descricao: str,
        centro_custo: str,
        id_pedido_compra: Optional[str],
        usuario: str,
    ) -> bool:
        """
        Chamado logo após INSERT na tabela Movimentacao com Tipo='Entrada'.
        Retorna True se sincronizado com sucesso, False se enfileirado para retry.
        """
        if not id_pedido_compra:
            logger.info("Ativo %s sem pedido de compra associado — sem sync ERP necessário.", id_ativo)
            return True

        payload = PayloadEntregaERP(
            pedido_id=id_pedido_compra,
            status="Entregue",
            centro_custo=centro_custo,
            data_recebimento=datetime.utcnow().isoformat() + "Z",
            id_ativo=id_ativo,
            descricao_ativo=descricao,
            responsavel_recebimento=usuario,
        )

        for tentativa in range(self.MAX_RETRIES):
            try:
                self.erp.atualizar_pedido_entregue(payload)
                self._marcar_sincronizado(id_movimentacao)
                logger.info("ERP sincronizado: movimentação %d, pedido %s", id_movimentacao, id_pedido_compra)
                return True

            except ERPIntegrationError as exc:
                # Erro de negócio (ex: pedido não existe) — não adianta tentar novamente
                logger.error("Erro permanente no ERP para movimentação %d: %s", id_movimentacao, exc)
                self._salvar_fila(id_movimentacao, payload, str(exc), tentativas=self.MAX_RETRIES)
                return False

            except ERPUnavailableError as exc:
                espera = self.RETRY_DELAYS[min(tentativa, len(self.RETRY_DELAYS) - 1)]
                logger.warning(
                    "ERP indisponível (tentativa %d/%d). Aguardando %ds. Erro: %s",
                    tentativa + 1, self.MAX_RETRIES, espera, exc,
                )
                if tentativa < self.MAX_RETRIES - 1:
                    time.sleep(espera)
                else:
                    self._salvar_fila(id_movimentacao, payload, str(exc), tentativas=tentativa + 1)
                    return False

        return False

    def reprocessar_fila(self) -> dict:
        """
        Job periódico (ex: cron a cada 10 min) que tenta reenviar registros pendentes.
        Retorna contagem de sucessos e falhas.
        """
        cursor = self.db.cursor()
        cursor.execute("""
            SELECT id, id_movimentacao, payload, tentativas
            FROM Fila_Sincronizacao_ERP
            WHERE resolvido = FALSE
              AND tentativas < max_tentativas
              AND proximo_retry <= NOW()
            ORDER BY criado_em
            LIMIT 50
        """)
        pendentes = cursor.fetchall()

        resultado = {"sucesso": 0, "falha": 0, "total": len(pendentes)}

        for fila_id, id_mov, payload_json, tentativas in pendentes:
            payload = PayloadEntregaERP(**payload_json)
            try:
                self.erp.atualizar_pedido_entregue(payload)
                self._marcar_sincronizado(id_mov)
                self._resolver_fila(fila_id)
                resultado["sucesso"] += 1
                logger.info("Reprocessado com sucesso: fila_id=%d, movimentação=%d", fila_id, id_mov)

            except (ERPUnavailableError, ERPIntegrationError) as exc:
                proximo = datetime.utcnow() + timedelta(seconds=self.RETRY_DELAYS[min(tentativas, 4)])
                self._incrementar_tentativa(fila_id, str(exc), proximo)
                resultado["falha"] += 1

        self.db.commit()
        return resultado

    # ------------------------------------------------------------------
    # Helpers de banco de dados
    # ------------------------------------------------------------------

    def _marcar_sincronizado(self, id_movimentacao: int) -> None:
        cur = self.db.cursor()
        cur.execute(
            "UPDATE Movimentacao SET Sincronizado_ERP = TRUE WHERE ID_Movimentacao = %s",
            (id_movimentacao,),
        )
        self.db.commit()

    def _salvar_fila(self, id_movimentacao: int, payload: PayloadEntregaERP, erro: str, tentativas: int) -> None:
        cur = self.db.cursor()
        cur.execute(
            """
            INSERT INTO Fila_Sincronizacao_ERP
                (id_movimentacao, payload, tentativas, ultimo_erro, proximo_retry)
            VALUES (%s, %s, %s, %s, NOW())
            """,
            (id_movimentacao, json.dumps(asdict(payload)), tentativas, erro),
        )
        self.db.commit()
        logger.warning("Movimentação %d enfileirada para retry posterior.", id_movimentacao)

    def _resolver_fila(self, fila_id: int) -> None:
        cur = self.db.cursor()
        cur.execute("UPDATE Fila_Sincronizacao_ERP SET resolvido = TRUE WHERE id = %s", (fila_id,))

    def _incrementar_tentativa(self, fila_id: int, erro: str, proximo_retry: datetime) -> None:
        cur = self.db.cursor()
        cur.execute(
            """
            UPDATE Fila_Sincronizacao_ERP
            SET tentativas = tentativas + 1,
                ultimo_erro = %s,
                proximo_retry = %s
            WHERE id = %s
            """,
            (erro, proximo_retry, fila_id),
        )
