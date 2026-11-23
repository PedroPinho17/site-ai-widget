"""Reciprocal Rank Fusion for hybrid retrieval."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[Any]],
    *,
    k: int = 60,
    top_n: int = 6,
    id_key: str = "id",
) -> list[Any]:
    """Fuse ranked result lists with RRF; return up to ``top_n`` unique items.

    Each item must be a mapping with ``id_key`` (default ``id``). Rank is 1-based.
    """
    scores: dict[Any, float] = {}
    best: dict[Any, Any] = {}
    for rows in ranked_lists:
        for rank, row in enumerate(rows, start=1):
            key = row[id_key] if isinstance(row, dict) else getattr(row, id_key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in best:
                best[key] = row
    ordered = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [best[key] for key in ordered[:top_n]]
