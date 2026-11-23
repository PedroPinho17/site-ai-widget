"""Grouping and checking of introspection rows (no database needed)."""
from __future__ import annotations

import itertools

import pytest

from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn import introspect
from site_ai_widget.dbconn.introspect import ColumnInfo, collect_rows, group_rows

GOOD = ("public", "products", "r", "price", "numeric(10,2)", False, 6)


def test_group_rows_builds_tables_in_order():
    rows = [
        ("public", "products", "r", "id", "integer", False, 1),
        ("public", "products", "r", "name", "text", False, 2),
        ("public", "v_prices", "v", "price", "numeric(10,2)", True, 1),
        ("sales", "orders", "p", "id", "bigint", False, 1),
        ("public", "mv_stats", "m", "total", "bigint", True, 1),
    ]
    tables = group_rows(rows)
    assert [(t.schema, t.name, t.kind) for t in tables] == [
        ("public", "products", "table"),
        ("public", "v_prices", "view"),
        ("sales", "orders", "table"),
        ("public", "mv_stats", "view"),
    ]
    assert tables[0].columns == (
        ColumnInfo("id", "integer", False, 1),
        ColumnInfo("name", "text", False, 2),
    )


def test_group_rows_ignores_other_kinds():
    rows = [
        ("public", "my_seq", "S", "last_value", "bigint", False, 1),
        ("public", "remote", "f", "id", "integer", True, 1),
    ]
    assert group_rows(rows) == []


def test_collect_rows_keeps_good_rows():
    rows = [GOOD, ("public", "products", "r", "sku", "text", False, 3)]
    assert collect_rows(iter(rows)) == rows


def test_collect_rows_stops_even_if_the_server_ignores_limit(monkeypatch):
    monkeypatch.setattr(introspect, "MAX_COLUMNS", 3)
    endless = (("public", "t", "r", f"c{i}", "text", True, 1) for i in itertools.count())
    with pytest.raises(ApiError) as exc:
        collect_rows(endless)
    assert exc.value.code == "db_schema_too_large"


@pytest.mark.parametrize(
    "row",
    [
        ("public", "products", "r", "x" * 64, "text", False, 1),  # longer than PostgreSQL allows
        ("public", "products", "r", "bad\nname", "text", False, 1),  # control character
        ("public", "products", "r", "", "text", False, 1),  # empty name
        ("public", "products", "r", "price", "x" * 201, False, 1),  # absurd type
        ("public", "products", "r", "price", "text", False, 0),  # columns start at 1
        ("public", "products", "r", "price", "text", False, 1601),  # max 1600 columns
        ("public", "products", "S", "price", "text", False, 1),  # sequence: never asked for
        ("public", "products", "r", "price", "text", "yes", 1),  # not a bool
        ("public", "products", "r", 42, "text", False, 1),  # not a string
        ("public", "products", "r", "price", "text", False),  # a value is missing
    ],
)
def test_collect_rows_refuses_strange_values(row):
    with pytest.raises(ApiError) as exc:
        collect_rows([GOOD, row])
    assert exc.value.code == "db_schema_invalid"


def test_collect_rows_refuses_the_same_column_twice():
    with pytest.raises(ApiError) as exc:
        collect_rows([GOOD, GOOD])
    assert exc.value.code == "db_schema_invalid"
