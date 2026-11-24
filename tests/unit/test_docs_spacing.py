"""Document answers must keep spaces from the demo manual address."""
from __future__ import annotations

from uuid import uuid4

from site_ai_widget.docs.ask import ask_documents
from site_ai_widget.docs.spacing import restore_source_spacing

# Exact line from scripts/make_demo_pdfs.py / the seeded Mercearia Atlas PDF.
ATLAS_ADDRESS_LINE = "Morada inventada: Rua das Amendoeiras 48, 1170-000 Lisboa."


def test_restore_spacing_for_atlas_manual_address():
    chunks = [
        {
            "n": 1,
            "document_id": "d1",
            "document_name": "Mercearia Atlas - Manual do Cliente.pdf",
            "page": 1,
            "content": ATLAS_ADDRESS_LINE,
        }
    ]
    glued = "A morada é Rua das Amendoeiras48, 1170-000 Lisboa. [1]"
    assert "Amendoeiras 48" in restore_source_spacing(glued, chunks)
    assert "Amendoeiras48" not in restore_source_spacing(glued, chunks)


def test_narrow_nbsp_between_street_and_number_becomes_a_normal_space():
    """PDF/LLM answers often use U+202F (NNBSP) instead of a real space."""
    chunks = [{"content": ATLAS_ADDRESS_LINE}]
    nnbsp = "A morada é Rua das Amendoeiras\u202f48, 1170-000 Lisboa."
    nbsp = "A morada é Rua das Amendoeiras\u00a048, 1170-000 Lisboa."
    for glued in (nnbsp, nbsp):
        out = restore_source_spacing(glued, chunks)
        assert "Amendoeiras 48" in out
        assert "\u202f" not in out
        assert "\u00a0" not in out
        assert "Amendoeiras48" not in out


def test_ask_documents_repairs_glued_atlas_address(monkeypatch):
    chunk = {
        "n": 1,
        "id": uuid4(),
        "document_id": uuid4(),
        "document_name": "Mercearia Atlas - Manual do Cliente.pdf",
        "page": 1,
        "chunk_index": 0,
        "content": ATLAS_ADDRESS_LINE,
    }
    monkeypatch.setattr(
        "site_ai_widget.docs.ask.hybrid_search",
        lambda *a, **k: [chunk],
    )
    out = ask_documents(
        company_id=uuid4(),
        question="Qual a morada?",
        llm_complete=lambda messages, temperature=0: (
            "A morada inventada é Rua das Amendoeiras48, 1170-000 Lisboa. [1]"
        ),
    )
    assert out["matched"] is True
    assert "Amendoeiras 48" in out["answer"]
    assert "Amendoeiras48" not in out["answer"]


def test_ask_documents_repairs_nnbsp_atlas_address(monkeypatch):
    chunk = {
        "n": 1,
        "id": uuid4(),
        "document_id": uuid4(),
        "document_name": "Mercearia Atlas - Manual do Cliente.pdf",
        "page": 1,
        "chunk_index": 0,
        "content": ATLAS_ADDRESS_LINE,
    }
    monkeypatch.setattr(
        "site_ai_widget.docs.ask.hybrid_search",
        lambda *a, **k: [chunk],
    )
    out = ask_documents(
        company_id=uuid4(),
        question="Como posso contactar-vos?",
        llm_complete=lambda messages, temperature=0: (
            "Pode contactar-nos em Rua das Amendoeiras\u202f48, 1170-000 Lisboa. [1]"
        ),
    )
    assert "Amendoeiras 48" in out["answer"]
    assert "\u202f" not in out["answer"]



def _real_manual_chunks():
    """Chunks built from the committed demo PDF, exactly like the upload pipeline."""
    from pathlib import Path

    from site_ai_widget.docs.chunk import chunk_pages
    from site_ai_widget.docs.process import extract_pages

    pdf = Path(__file__).resolve().parents[2] / "demo" / "mercearia-atlas-manual.pdf"
    pages = extract_pages(pdf)
    out = []
    for n, chunk in enumerate(chunk_pages(pages), start=1):
        out.append(
            {
                "n": n,
                "id": uuid4(),
                "document_id": uuid4(),
                "document_name": "Mercearia Atlas - Manual do Cliente.pdf",
                "page": getattr(chunk, "page", 1),
                "chunk_index": n - 1,
                "content": getattr(chunk, "text", str(chunk)),
            }
        )
    return out


def test_real_demo_manual_keeps_the_space_and_answers_are_repaired(monkeypatch):
    chunks = _real_manual_chunks()
    # The extracted text itself is fine: the glue only ever comes from the model.
    assert any("Rua das Amendoeiras 48" in c["content"] for c in chunks)
    assert not any("Amendoeiras48" in c["content"] for c in chunks)
    monkeypatch.setattr("site_ai_widget.docs.ask.hybrid_search", lambda *a, **k: chunks[:3])
    out = ask_documents(
        company_id=uuid4(),
        question="Qual a morada?",
        llm_complete=lambda messages, temperature=0: "A morada é Rua das Amendoeiras48, Lisboa. [1]",
    )
    assert "Rua das Amendoeiras 48" in out["answer"]
    assert "Amendoeiras48" not in out["answer"]


def test_only_whole_tokens_are_split():
    chunks = [{"content": "Fica no Lote 12 e na Rua Flores 7."}]
    text = "Lote123, XLote12 e Flores70 ficam iguais; Lote12 e Flores7 corrigem."
    out = restore_source_spacing(text, chunks)
    assert "Lote123" in out
    assert "XLote12" in out
    assert "Flores70" in out
    assert out.endswith("Lote 12 e Flores 7 corrigem.")
