"""Embedding interface: real e5-small model or a deterministic fake for tests."""
from __future__ import annotations

import hashlib
import math
import os
import threading
from typing import Protocol

from site_ai_widget.api.config import get_settings

EMBED_DIM = 384


class Embedder(Protocol):
    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeEmbedder:
    """Deterministic 384-d embeddings from text hashes (no model download)."""

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [_fake_vec(f"passage: {t}") for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return _fake_vec(f"query: {text}")


def _fake_vec(seed: str) -> list[float]:
    digest = hashlib.sha256(seed.encode()).digest()
    raw: list[float] = []
    while len(raw) < EMBED_DIM:
        digest = hashlib.sha256(digest + seed.encode()).digest()
        for b in digest:
            raw.append((b / 127.5) - 1.0)
            if len(raw) >= EMBED_DIM:
                break
    return _l2_normalize(raw[:EMBED_DIM])


class E5Embedder:
    """``intfloat/multilingual-e5-small`` with passage:/query: prefixes."""

    def __init__(self, model_name: str | None = None):
        from sentence_transformers import SentenceTransformer

        name = model_name or get_settings().embedding_model
        self._model = SentenceTransformer(name)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        prefixed = [f"passage: {t}" for t in texts]
        vectors = self._model.encode(prefixed, normalize_embeddings=True, show_progress_bar=False)
        return [list(map(float, row)) for row in vectors]

    def embed_query(self, text: str) -> list[float]:
        vectors = self._model.encode(
            [f"query: {text}"],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return list(map(float, vectors[0]))


_lock = threading.Lock()
_embedder: Embedder | None = None


def reset_embedder() -> None:
    global _embedder
    with _lock:
        _embedder = None


def get_embedder() -> Embedder:
    """Lazy singleton. Set ``SITE_AI_WIDGET_FAKE_EMBEDDER=1`` for tests."""
    global _embedder
    if _embedder is not None:
        return _embedder
    with _lock:
        if _embedder is not None:
            return _embedder
        if os.environ.get("SITE_AI_WIDGET_FAKE_EMBEDDER", "").strip() in ("1", "true", "yes"):
            _embedder = FakeEmbedder()
        else:
            _embedder = E5Embedder()
        return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Override the process embedder (tests)."""
    global _embedder
    with _lock:
        _embedder = embedder
