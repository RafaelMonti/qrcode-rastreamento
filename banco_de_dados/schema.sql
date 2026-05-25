-- =============================================================
-- Sistema de Rastreamento de Materiais via QR Code
-- Módulo 1: Schema do Banco de Dados Relacional
-- Compatível com PostgreSQL 14+
-- =============================================================

-- Habilita extensão para UUID (PostgreSQL)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- -------------------------------------------------------------
-- Tabela: Ativo
-- Representa cada insumo ou maquinário cadastrado no sistema
-- -------------------------------------------------------------
CREATE TABLE Ativo (
    ID_Ativo        UUID            NOT NULL DEFAULT gen_random_uuid(),
    Descricao       VARCHAR(255)    NOT NULL,
    Categoria       VARCHAR(20)     NOT NULL,
    Centro_Custo    VARCHAR(50)     NOT NULL,
    Status_Atual    VARCHAR(30)     NOT NULL DEFAULT 'No almoxarifado',
    Codigo_Externo  VARCHAR(100),           -- ID no ERP/Sienge
    Data_Cadastro   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    Ultima_Atualizacao TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_ativo PRIMARY KEY (ID_Ativo),
    CONSTRAINT chk_categoria CHECK (Categoria IN ('Insumo', 'Maquinário')),
    CONSTRAINT chk_status CHECK (Status_Atual IN ('Em obra', 'No almoxarifado', 'Em manutenção'))
);

-- Índices de busca frequente
CREATE INDEX idx_ativo_categoria    ON Ativo(Categoria);
CREATE INDEX idx_ativo_status       ON Ativo(Status_Atual);
CREATE INDEX idx_ativo_centro_custo ON Ativo(Centro_Custo);
CREATE INDEX idx_ativo_cod_externo  ON Ativo(Codigo_Externo) WHERE Codigo_Externo IS NOT NULL;

-- Trigger para atualizar Ultima_Atualizacao automaticamente
CREATE OR REPLACE FUNCTION fn_atualizar_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.Ultima_Atualizacao = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ativo_timestamp
    BEFORE UPDATE ON Ativo
    FOR EACH ROW EXECUTE FUNCTION fn_atualizar_timestamp();

-- -------------------------------------------------------------
-- Tabela: Movimentacao
-- Log imutável de todas as entradas e saídas de ativos
-- Projetada para alto volume (particionamento por mês recomendado)
-- -------------------------------------------------------------
CREATE TABLE Movimentacao (
    ID_Movimentacao     BIGSERIAL       NOT NULL,
    ID_Ativo            UUID            NOT NULL,
    Data_Hora           TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    Tipo                VARCHAR(10)     NOT NULL,
    Localizacao         VARCHAR(255),
    Usuario             VARCHAR(100)    NOT NULL,
    Observacao          TEXT,
    ID_Pedido_Compra    VARCHAR(100),           -- Referência no ERP
    Sincronizado_ERP    BOOLEAN         NOT NULL DEFAULT FALSE,
    Tentativas_ERP      SMALLINT        NOT NULL DEFAULT 0,

    CONSTRAINT pk_movimentacao PRIMARY KEY (ID_Movimentacao),
    CONSTRAINT fk_mov_ativo FOREIGN KEY (ID_Ativo) REFERENCES Ativo(ID_Ativo) ON DELETE RESTRICT,
    CONSTRAINT chk_tipo CHECK (Tipo IN ('Entrada', 'Saída'))
);

-- Índices críticos para consultas de alto volume
CREATE INDEX idx_mov_ativo      ON Movimentacao(ID_Ativo);
CREATE INDEX idx_mov_data       ON Movimentacao(Data_Hora DESC);
CREATE INDEX idx_mov_tipo       ON Movimentacao(Tipo);
CREATE INDEX idx_mov_usuario    ON Movimentacao(Usuario);
-- Índice parcial: só registros pendentes de sync (muito menor que o total)
CREATE INDEX idx_mov_sync_pendente ON Movimentacao(ID_Movimentacao)
    WHERE Sincronizado_ERP = FALSE;

-- -------------------------------------------------------------
-- Tabela: Fila_Sincronizacao_ERP (Dead Letter Queue)
-- Garante que nenhuma movimentação seja perdida se o ERP estiver fora
-- -------------------------------------------------------------
CREATE TABLE Fila_Sincronizacao_ERP (
    ID                  BIGSERIAL       NOT NULL,
    ID_Movimentacao     BIGINT          NOT NULL,
    Payload             JSONB           NOT NULL,
    Tentativas          SMALLINT        NOT NULL DEFAULT 0,
    Max_Tentativas      SMALLINT        NOT NULL DEFAULT 5,
    Ultimo_Erro         TEXT,
    Criado_Em           TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    Proximo_Retry       TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    Resolvido           BOOLEAN         NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_fila PRIMARY KEY (ID),
    CONSTRAINT fk_fila_mov FOREIGN KEY (ID_Movimentacao) REFERENCES Movimentacao(ID_Movimentacao)
);

CREATE INDEX idx_fila_retry ON Fila_Sincronizacao_ERP(Proximo_Retry)
    WHERE Resolvido = FALSE AND Tentativas < Max_Tentativas;

-- -------------------------------------------------------------
-- View: Posição atual de todos os ativos (útil para dashboard)
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW vw_posicao_atual AS
SELECT
    a.ID_Ativo,
    a.Descricao,
    a.Categoria,
    a.Centro_Custo,
    a.Status_Atual,
    a.Codigo_Externo,
    m.Data_Hora         AS Ultima_Movimentacao,
    m.Tipo              AS Ultimo_Tipo,
    m.Localizacao       AS Ultima_Localizacao,
    m.Usuario           AS Ultimo_Usuario
FROM Ativo a
LEFT JOIN LATERAL (
    SELECT Data_Hora, Tipo, Localizacao, Usuario
    FROM Movimentacao
    WHERE ID_Ativo = a.ID_Ativo
    ORDER BY Data_Hora DESC
    LIMIT 1
) m ON TRUE;

-- -------------------------------------------------------------
-- Dados de exemplo para testes
-- -------------------------------------------------------------
INSERT INTO Ativo (ID_Ativo, Descricao, Categoria, Centro_Custo, Status_Atual, Codigo_Externo) VALUES
    ('a1b2c3d4-0001-0001-0001-000000000001', 'Betoneira 400L - Marca X',    'Maquinário', 'CC-001-FUNDACAO', 'Em obra',           'ERP-MAQ-001'),
    ('a1b2c3d4-0002-0002-0002-000000000002', 'Cimento CP-II 50kg (Pallet)', 'Insumo',     'CC-001-FUNDACAO', 'No almoxarifado',   'ERP-INS-100'),
    ('a1b2c3d4-0003-0003-0003-000000000003', 'Andaime Multidirecional 1,5m','Maquinário', 'CC-002-ESTRUTURA', 'Em manutenção',    'ERP-MAQ-002'),
    ('a1b2c3d4-0004-0004-0004-000000000004', 'Vergalhão CA-50 12mm (100kg)','Insumo',     'CC-002-ESTRUTURA', 'Em obra',           'ERP-INS-101');
