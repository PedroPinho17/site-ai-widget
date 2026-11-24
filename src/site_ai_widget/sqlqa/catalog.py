"""Catalog-question helpers shared by retrieval and the SQL pipeline."""
from __future__ import annotations

import re

# Question shape only, never product words: the same code serves every company.
# "Que queijos têm?", "Quais vinhos vendem?", "Têm queijos?", "What cheeses do you
# have?", "Do you sell bread?".
_CATALOG_QUESTION = re.compile(
    r"(?i)(?:\b(?:que|quais|what|which)\b.{0,48}\b(?:t[eê]m|vendem|have|sell)\b|"
    r"^\s*(?:t[eê]m|vendem|do\s+you\s+(?:have|sell)|have\s+you\s+got)\b)",
)

# Unambiguous money column names (shared with the answer formatter). "total" and
# "amount" are left out: they are often counts.
MONEY_COLUMN_NAMES: tuple[str, ...] = ("price", "preco", "unit_price", "valor", "cost", "custo")
# A catalog question must see the tables that hold sellable items. Those are found
# by shape (an approved price-like column), never by name: one client calls the
# table "products", another "produtos" or "artigos".
CATALOG_PIN_COLUMNS: tuple[str, ...] = (*MONEY_COLUMN_NAMES, "preço")
CATALOG_PIN_LIMIT = 2


def is_catalog_question(question: str) -> bool:
    """Visitor is asking which products exist (list from ``products``, not docs)."""
    return bool(_CATALOG_QUESTION.search(question or ""))
