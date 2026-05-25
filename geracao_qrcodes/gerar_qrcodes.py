"""
Módulo 2: Geração em Massa de QR Codes
Lê uma planilha CSV/Excel e gera uma imagem PNG de QR Code por ativo.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
import qrcode
from PIL import Image, ImageDraw, ImageFont

BASE_URL = "http://192.168.1.102:5000/scan"
OUTPUT_DIR = Path("qrcodes_gerados")

REQUIRED_COLUMNS = {"ID_Ativo", "Descricao"}


def _carregar_planilha(caminho: str) -> pd.DataFrame:
    p = Path(caminho)
    if not p.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho}")
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p, dtype=str)
    return pd.read_excel(p, dtype=str)


def _validar_colunas(df: pd.DataFrame) -> None:
    faltando = REQUIRED_COLUMNS - set(df.columns)
    if faltando:
        raise ValueError(f"Colunas obrigatórias ausentes na planilha: {faltando}")


def _gerar_imagem_etiqueta(id_ativo: str, descricao: str, categoria: str) -> Image.Image:
    url = f"{BASE_URL}?id_ativo={id_ativo}"

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # 30% de redundância — resiste a sujeira/rasgo
        box_size=8,
        border=3,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    # Monta etiqueta com texto abaixo do QR Code
    qr_w, qr_h = qr_img.size
    label_height = 60
    etiqueta = Image.new("RGB", (qr_w, qr_h + label_height), color="white")
    etiqueta.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(etiqueta)
    try:
        font_id = ImageFont.truetype("arial.ttf", 11)
        font_desc = ImageFont.truetype("arial.ttf", 10)
    except OSError:
        font_id = ImageFont.load_default()
        font_desc = font_id

    # Trunca descrição longa para caber na etiqueta
    descricao_curta = descricao[:38] + "…" if len(descricao) > 38 else descricao
    draw.text((4, qr_h + 4),  f"ID: {id_ativo[:18]}…" if len(id_ativo) > 18 else f"ID: {id_ativo}", fill="black", font=font_id)
    draw.text((4, qr_h + 22), descricao_curta, fill="black", font=font_desc)
    draw.text((4, qr_h + 40), f"[{categoria}]", fill="#555555", font=font_desc)

    return etiqueta


def gerar_qrcodes(caminho_planilha: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df = _carregar_planilha(caminho_planilha)
    _validar_colunas(df)

    total = len(df)
    gerados = 0
    erros = []

    print(f"Processando {total} ativos de '{caminho_planilha}'...\n")

    for idx, row in df.iterrows():
        id_ativo  = str(row["ID_Ativo"]).strip()
        descricao = str(row.get("Descricao", "")).strip()
        categoria = str(row.get("Categoria", "Insumo")).strip()

        if not id_ativo:
            erros.append((idx, "ID_Ativo vazio"))
            continue

        try:
            etiqueta = _gerar_imagem_etiqueta(id_ativo, descricao, categoria)
            destino = OUTPUT_DIR / f"{id_ativo}.png"
            etiqueta.save(destino, dpi=(300, 300))
            gerados += 1
            print(f"  [OK] {destino.name}  —  {descricao[:50]}")
        except Exception as exc:
            erros.append((id_ativo, str(exc)))
            print(f"  [ERRO] {id_ativo}: {exc}", file=sys.stderr)

    print(f"\n{'='*50}")
    print(f"Concluído: {gerados}/{total} QR Codes gerados em '{OUTPUT_DIR}/'")
    if erros:
        print(f"Erros ({len(erros)}):")
        for ref, msg in erros:
            print(f"  - {ref}: {msg}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera QR Codes em massa a partir de planilha CSV/Excel.")
    parser.add_argument("planilha", nargs="?", default="ativos.csv", help="Caminho para o arquivo CSV ou Excel")
    args = parser.parse_args()
    gerar_qrcodes(args.planilha)
