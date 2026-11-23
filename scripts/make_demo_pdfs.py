"""Generate fictional demo PDFs for Mercearia Atlas and Oficina Lima."""
from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo"


def _pdf() -> FPDF:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Helvetica", size=12)
    return pdf


def _page(pdf: FPDF, title: str, lines: list[str]) -> None:
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 10, title)
    pdf.ln(4)
    pdf.set_font("Helvetica", size=12)
    for line in lines:
        pdf.multi_cell(0, 8, line)
        pdf.ln(1)


def make_atlas(path: Path) -> None:
    pdf = _pdf()
    _page(
        pdf,
        "Mercearia Atlas - Manual do Cliente",
        [
            "Bem-vindo a Mercearia Atlas, a sua mercearia de bairro em Lisboa.",
            "Morada inventada: Rua das Amendoeiras 48, 1170-000 Lisboa.",
            "Telefone: +351 210 000 111 (ficticio).",
        ],
    )
    _page(
        pdf,
        "Horario de funcionamento",
        [
            "Segunda a Sexta: 08:00-20:00.",
            "Sabado: 09:00-19:00.",
            "Domingo: 10:00-14:00 (aberto ao domingo de manha).",
            "Feriados nacionais: fechado, excepto 1 de Maio (10:00-13:00).",
        ],
    )
    _page(
        pdf,
        "Politica de devolucoes e entregas",
        [
            "Devolucoes: produtos nao pereciveis ate 14 dias com talao.",
            "Pereciveis so no dia da compra se a embalagem estiver intacta.",
            "Zonas de entrega: Arroios, Anjos, Penha de Franca e Areeiro.",
            "Taxa de entrega: 2,50 EUR; gratis acima de 35 EUR.",
            "Cartao de fidelizacao Atlas+: 1 ponto por cada euro; 100 pontos = 5 EUR.",
        ],
    )
    _page(
        pdf,
        "Produtos em destaque",
        [
            "Azeite Atlas Premium 750 ml.",
            "Queijo da Serra da Estrela (curado) - peca de 200 g.",
            "Cafe Moagem da Baixa - embalagem de 250 g.",
            "Doce de abobora caseiro Atlas - frasco 300 g.",
            "Para duvidas sobre stock, pergunte ao balcao ou use o assistente.",
        ],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))


def make_lima(path: Path) -> None:
    pdf = _pdf()
    _page(
        pdf,
        "Oficina Lima - Guia rapido",
        [
            "Oficina Lima - reparacao de bicicletas e trotinetes em Coimbra.",
            "Morada inventada: Largo do Aqueduto 7, 3000-000 Coimbra.",
            "Horario: Segunda a Sabado 09:00-18:00. Domingo fechado.",
            "Diagnostico gratuito em 24 horas uteis.",
        ],
    )
    _page(
        pdf,
        "Servicos e pecas",
        [
            "Revisao completa Lima Care: 45 EUR (travoes, mudancas, rodas).",
            "Substituicao de camara de ar: a partir de 8 EUR.",
            "Pecas originais Shimano e SRAM sob encomenda (3-5 dias).",
            "Garantia de mao-de-obra: 90 dias sobre reparacoes.",
            "Contacto: oficina@lima.example (ficticio).",
        ],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))


def main() -> None:
    atlas = OUT / "mercearia-atlas-manual.pdf"
    lima = OUT / "oficina-lima-manual.pdf"
    make_atlas(atlas)
    make_lima(lima)
    print(f"Wrote {atlas}")
    print(f"Wrote {lima}")


if __name__ == "__main__":
    main()
