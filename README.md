# 🏗️ Sistema de Rastreamento de Materiais via QR Code

Sistema web para controle de entrada e saída de materiais em canteiros de obra, utilizando QR Codes colados nos insumos e maquinários.

---

## Como funciona

1. **Geração de QR Codes** — Um script Python lê uma planilha CSV/Excel com os ativos e gera uma imagem PNG de etiqueta por item.
2. **Etiqueta colada no material** — O QR Code impresso é fixado fisicamente no ativo.
3. **Leitura no canteiro** — O operador abre o site no celular, escaneia o QR Code e registra a entrada ou saída do material.
4. **Banco de dados na nuvem** — Todas as movimentações ficam salvas no Supabase (PostgreSQL) e podem ser consultadas a qualquer momento.

---

## Tecnologias utilizadas

| Camada | Tecnologia |
|--------|-----------|
| Back-end | Python 3.13 + Flask |
| Banco de dados | PostgreSQL (Supabase) |
| Front-end | HTML5 + CSS + JavaScript (jsQR) |
| Geração de QR Codes | qrcode + Pillow |
| Leitura de planilhas | pandas + openpyxl |
| Servidor de produção | Gunicorn |

---

## Estrutura do projeto

```
qrcode-rastreamento/
├── app_web/
│   ├── app.py               # API Flask (rotas e banco de dados)
│   └── templates/
│       └── scanner.html     # Interface web para celular
├── banco_de_dados/
│   └── schema.sql           # Schema PostgreSQL (rodar no Supabase)
├── geracao_qrcodes/
│   ├── gerar_qrcodes.py     # Script de geração em massa
│   └── ativos_exemplo.csv   # Planilha de exemplo
├── integracao_erp/
│   ├── __init__.py
│   └── middleware.py        # Integração com ERP Sienge
├── requirements.txt
├── Procfile                 # Configuração para deploy no Render
└── .env                     # Variáveis de ambiente (não versionado)
```

---

## Configuração e instalação

### 1. Clonar o repositório

```bash
git clone https://github.com/SEU_USUARIO/qrcode-rastreamento.git
cd qrcode-rastreamento
```

### 2. Instalar dependências

```bash
pip install -r requirements.txt
```

### 3. Configurar variáveis de ambiente

Crie um arquivo `.env` na raiz do projeto:

```
DATABASE_URL=postgresql://postgres:SENHA@db.REFERENCIA.supabase.co:5432/postgres
SECRET_KEY=uma-chave-secreta-longa
ERP_BASE_URL=https://api.sienge.com.br
ERP_API_KEY=sua-chave-api
```

### 4. Criar o banco de dados

Execute o arquivo `banco_de_dados/schema.sql` no SQL Editor do Supabase.

### 5. Gerar os QR Codes

```bash
cd geracao_qrcodes
python gerar_qrcodes.py ativos_exemplo.csv
```

### 6. Rodar o servidor

```bash
flask --app app_web/app.py run --host=0.0.0.0
```

---

## Deploy na nuvem (Render)

1. Faça o push do repositório para o GitHub
2. Crie uma conta em [render.com](https://render.com)
3. Novo serviço → Web Service → conecte este repositório
4. Configure as variáveis de ambiente (`DATABASE_URL`, `SECRET_KEY`, etc.)
5. O Render detecta o `Procfile` automaticamente e faz o deploy

---

## Rotas da API

| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/` | Interface web principal |
| GET | `/scan?id_ativo=<id>` | Abre interface com ativo pré-carregado |
| GET | `/api/ativo/<id>` | Retorna dados e histórico do ativo |
| POST | `/api/movimentacao` | Registra entrada ou saída |
| GET | `/health` | Health check do servidor |
