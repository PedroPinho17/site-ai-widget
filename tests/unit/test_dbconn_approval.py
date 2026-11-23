"""Embedding text and conflict mapping for schema approval."""
from __future__ import annotations

import psycopg
import pytest

from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn import approval
from site_ai_widget.dbconn.approval import embedding_text, update_column


def test_embedding_text_has_name_description_and_columns():
    text = embedding_text("public", "products", "Products for sale.", ["id", "name", "price"])
    assert text == "Table public.products\nProducts for sale.\nColumns: id, name, price"


def test_embedding_text_without_description_or_columns():
    assert embedding_text("public", "products", None, []) == "Table public.products"


def test_update_column_maps_deadlock_to_conflict_retry(monkeypatch):
    def boom(*_args, **_kwargs):
        raise psycopg.errors.DeadlockDetected("deadlock detected")

    monkeypatch.setattr(approval, "_update_column", boom)
    with pytest.raises(ApiError) as exc:
        update_column(object(), "company", "column", {})  # type: ignore[arg-type]
    assert (exc.value.code, exc.value.status) == ("db_conflict_retry", 409)


def test_update_column_maps_serialization_failure(monkeypatch):
    def boom(*_args, **_kwargs):
        raise psycopg.errors.SerializationFailure("could not serialize")

    monkeypatch.setattr(approval, "_update_column", boom)
    with pytest.raises(ApiError) as exc:
        update_column(object(), "company", "column", {})  # type: ignore[arg-type]
    assert (exc.value.code, exc.value.status) == ("db_conflict_retry", 409)
