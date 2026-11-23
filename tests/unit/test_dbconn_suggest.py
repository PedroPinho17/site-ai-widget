"""AI table suggestions: prompt and answer parsing (no DB, no LLM)."""
from __future__ import annotations

import json

import pytest

from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn.suggest import (
    MAX_COLUMNS_IN_PROMPT,
    MAX_DESCRIPTION,
    TableForAi,
    build_messages,
    parse_suggestions,
)

PRODUCTS = TableForAi("id-1", "public", "products", "table", ("id integer", "name text", "price numeric"))
CUSTOMERS = TableForAi("id-2", "public", "customers", "table", ("id integer", "email text", "nif text"))


def _payload(messages: list[dict[str, str]]) -> list[dict]:
    return json.loads(messages[1]["content"])["tables"]


def test_prompt_has_refs_names_and_types_only():
    messages = build_messages([PRODUCTS, CUSTOMERS])
    assert messages[0]["role"] == "system"
    tables = _payload(messages)
    assert tables[0] == {
        "ref": "t1",
        "table": "public.products",
        "kind": "table",
        "columns": ["id integer", "name text", "price numeric"],
    }
    assert tables[1]["ref"] == "t2"
    assert "id-1" not in messages[1]["content"]  # our internal ids never go to the LLM


def test_prompt_caps_columns():
    wide = TableForAi("id-3", "public", "wide", "table", tuple(f"c{i} text" for i in range(60)))
    assert len(_payload(build_messages([wide]))[0]["columns"]) == MAX_COLUMNS_IN_PROMPT


def test_parse_accepts_fenced_json_and_skips_bad_items():
    items = [
        {"ref": "t1", "useful": True, "description": "Products\nwith\x00 prices. "},
        {"ref": "t2", "useful": "no", "description": "Wrong type for useful."},
        {"ref": "t9", "useful": True, "description": "Unknown ref."},
        {"ref": "t1", "useful": False, "description": "Duplicate ref."},
        "not an object",
    ]
    answer = "```json\n" + json.dumps({"tables": items}) + "\n```"
    result = parse_suggestions(answer, [PRODUCTS, CUSTOMERS])
    assert len(result) == 1
    assert result[0].table_id == "id-1"
    assert result[0].useful is True
    assert result[0].description == "Products with prices."


def test_parse_truncates_long_description():
    answer = json.dumps({"tables": [{"ref": "t1", "useful": False, "description": "x" * 1000}]})
    assert len(parse_suggestions(answer, [PRODUCTS])[0].description) == MAX_DESCRIPTION


@pytest.mark.parametrize(
    "answer",
    ["not json", "[]", '{"tables": "x"}', '{"tables": []}', '{"other": 1}'],
)
def test_parse_bad_answer_raises(answer):
    with pytest.raises(ApiError) as err:
        parse_suggestions(answer, [PRODUCTS])
    assert err.value.code == "schema_suggest_failed"
    assert err.value.status == 502
