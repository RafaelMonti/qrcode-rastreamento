"""
Módulo 3 — Back-end: API Flask para rastreamento de ativos via QR Code.
Rotas:
  GET  /scan?id_ativo=<uuid>          Retorna dados do ativo (chamada do QR Code)
  GET  /api/ativo/<id>                JSON com detalhes completos + histórico
  POST /api/movimentacao              Registra entrada ou saída
  GET  /                              Interface web para celular
"""
import os
import sys
import logging
from datetime import datetime
from functools import wraps
from pathlib import Path

# Garante que a pasta raiz do projeto esteja no path do Python
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
import psycopg2.extras
from flask import Flask, g, jsonify, render_template, request, abort
from dotenv import load_dotenv

from integracao_erp.middleware import ERPMiddleware, SiengeClient

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Conexão com banco de dados (uma por request via Flask g)
# ------------------------------------------------------------------

def get_db():
    if "db" not in g:
        # DATABASE_URL aceita o formato completo do Supabase:
        # postgresql://postgres:[SENHA]@db.[REF].supabase.co:5432/postgres
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            g.db = psycopg2.connect(db_url, sslmode="require", cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            g.db = psycopg2.connect(
                host=os.environ["DB_HOST"],
                port=os.environ.get("DB_PORT", 5432),
                dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"],
                password=os.environ["DB_PASSWORD"],
                sslmode=os.environ.get("DB_SSLMODE", "prefer"),
                cursor_factory=psycopg2.extras.RealDictCursor,
            )
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def get_erp_middleware():
    client = SiengeClient(
        base_url=os.environ["ERP_BASE_URL"],
        api_key=os.environ["ERP_API_KEY"],
    )
    return ERPMiddleware(erp_client=client, db_conn=get_db())


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def require_json(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not request.is_json:
            return jsonify({"erro": "Content-Type deve ser application/json"}), 415
        return f(*args, **kwargs)
    return decorated


def buscar_ativo(id_ativo: str) -> dict | None:
    cur = get_db().cursor()
    cur.execute("SELECT * FROM Ativo WHERE ID_Ativo = %s", (id_ativo,))
    return cur.fetchone()


def buscar_historico(id_ativo: str, limite: int = 20) -> list:
    cur = get_db().cursor()
    cur.execute(
        """
        SELECT ID_Movimentacao, Data_Hora, Tipo, Localizacao, Usuario, Observacao
        FROM Movimentacao
        WHERE ID_Ativo = %s
        ORDER BY Data_Hora DESC
        LIMIT %s
        """,
        (id_ativo, limite),
    )
    return cur.fetchall()


# ------------------------------------------------------------------
# Rotas
# ------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("scanner.html")


@app.route("/scan")
def scan_qrcode():
    """Chamada ao ler o QR Code — redireciona para a interface com o ID pré-carregado."""
    id_ativo = request.args.get("id_ativo", "").strip()
    if not id_ativo:
        abort(400, "Parâmetro id_ativo obrigatório.")
    return render_template("scanner.html", id_ativo=id_ativo)


@app.route("/api/ativo/<id_ativo>")
def api_ativo(id_ativo: str):
    ativo = buscar_ativo(id_ativo)
    if not ativo:
        return jsonify({"erro": "Ativo não encontrado"}), 404

    historico = buscar_historico(id_ativo)
    return jsonify({
        "ativo": dict(ativo),
        "historico": [dict(m) for m in historico],
    })


@app.route("/api/movimentacao", methods=["POST"])
@require_json
def registrar_movimentacao():
    data = request.get_json()

    id_ativo  = (data.get("id_ativo") or "").strip()
    tipo      = (data.get("tipo") or "").strip()
    localizacao = (data.get("localizacao") or "").strip()
    usuario   = (data.get("usuario") or "Operador Anônimo").strip()
    observacao = data.get("observacao", "")

    if not id_ativo or tipo not in ("Entrada", "Saída"):
        return jsonify({"erro": "Campos 'id_ativo' e 'tipo' (Entrada|Saída) são obrigatórios"}), 400

    ativo = buscar_ativo(id_ativo)
    if not ativo:
        return jsonify({"erro": "Ativo não encontrado"}), 404

    db = get_db()
    cur = db.cursor()

    # Mapeia tipo de movimentação para novo status do ativo
    novo_status = "Em obra" if tipo == "Entrada" else "No almoxarifado"

    cur.execute(
        """
        INSERT INTO Movimentacao (ID_Ativo, Tipo, Localizacao, Usuario, Observacao, ID_Pedido_Compra)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING ID_Movimentacao, Data_Hora
        """,
        (id_ativo, tipo, localizacao, usuario, observacao, ativo.get("Codigo_Externo")),
    )
    row = cur.fetchone()
    id_mov = row["id_movimentacao"]
    data_hora = row["data_hora"]

    cur.execute(
        "UPDATE Ativo SET Status_Atual = %s WHERE ID_Ativo = %s",
        (novo_status, id_ativo),
    )
    db.commit()

    # Dispara integração ERP assincronamente apenas em Entradas
    if tipo == "Entrada":
        try:
            mw = get_erp_middleware()
            mw.processar_entrada(
                id_movimentacao=id_mov,
                id_ativo=id_ativo,
                descricao=ativo["descricao"],
                centro_custo=ativo["centro_custo"],
                id_pedido_compra=ativo.get("codigo_externo"),
                usuario=usuario,
            )
        except Exception as exc:
            logger.error("Falha ao acionar middleware ERP: %s", exc)
            # Não propaga: a movimentação já foi gravada com Sincronizado_ERP=FALSE

    return jsonify({
        "mensagem": f"{tipo} registrada com sucesso.",
        "id_movimentacao": id_mov,
        "data_hora": data_hora.isoformat(),
        "novo_status": novo_status,
    }), 201


# ------------------------------------------------------------------
# Health check
# ------------------------------------------------------------------

@app.route("/health")
def health():
    try:
        get_db().cursor().execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    status = 200 if db_ok else 503
    return jsonify({"db": "ok" if db_ok else "erro", "ts": datetime.utcnow().isoformat()}), status


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
