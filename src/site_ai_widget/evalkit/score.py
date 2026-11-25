"""Score one evaluation item against an AskResponse-like object."""
from __future__ import annotations

import re
from typing import Any

from site_ai_widget.answer_checks import is_no_information
from site_ai_widget.evalkit.compare import DEFAULT_TOLERANCE, rows_match, strip_accents

MAX_RECORDED_ROWS = 10


def _json_cell(value: Any) -> Any:
    """Keep JSON scalars; Decimal/date/time and the rest become text."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)

_BUSY_MARKERS = (
    "muitos pedidos",
    "many requests",
    "try again in a minute",
    "daqui a um minuto",
)


def _text(value: Any) -> str:
    return str(value or "")


def _facts(reference: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    if isinstance(reference.get("fact"), str) and reference["fact"].strip():
        facts.append(reference["fact"].strip())
    raw = reference.get("facts")
    if isinstance(raw, list):
        facts.extend(str(item).strip() for item in raw if str(item).strip())
    return facts


_TIME_RANGE_FACT = re.compile(r"^\s*(\d{1,2})[:h](\d{2})\s*[-–—]\s*(\d{1,2})[:h](\d{2})\s*$")
# A clock time in an answer: 08:00, 8:00, 8h00, 8h, 8.00 (not part of a longer number).
_CLOCK = re.compile(r"(?<![\d,.])(\d{1,2})\s*(?:[:h.]\s*(\d{2})(?!\d)|h(?![a-z]))", re.IGNORECASE)


def _clock_times(text: str) -> list[tuple[int, int]]:
    return [(int(h), int(m or 0)) for h, m in _CLOCK.findall(text)]


def _contains_time_range(answer: str, fact: str) -> bool:
    """``08:00-20:00`` is in the answer as two clock times in that order, in any
    wording ("das 08:00 às 20:00", "8h00 – 20h00", "from 8:00 to 20:00")."""
    match = _TIME_RANGE_FACT.match(fact)
    if not match:
        return False
    opens = (int(match[1]), int(match[2]))
    closes = (int(match[3]), int(match[4]))
    times = _clock_times(answer)
    return any(t == opens and closes in times[i + 1 :] for i, t in enumerate(times))


def _contains_fact(answer: str, fact: str) -> bool:
    hay = strip_accents(answer).casefold()
    needle = strip_accents(fact).casefold()
    compact_hay = hay.replace(" ", "").replace(":", "")
    compact_needle = needle.replace(" ", "").replace(":", "")
    if needle and needle in hay:
        return True
    if _contains_time_range(answer, fact):
        return True
    return bool(compact_needle) and compact_needle in compact_hay


def cited_pages(response: Any) -> list[int]:
    pages: list[int] = []
    sources = getattr(response, "sources", None)
    if sources is None and isinstance(response, dict):
        sources = response.get("sources")
    for source in sources or []:
        page = source.get("page") if isinstance(source, dict) else getattr(source, "page", None)
        if isinstance(page, int):
            pages.append(page)
        elif isinstance(page, str) and page.isdigit():
            pages.append(int(page))
    return pages


def is_busy_answer(response: Any) -> bool:
    notice = getattr(response, "notice", None)
    if notice is None and isinstance(response, dict):
        notice = response.get("notice")
    if notice == "busy":
        return True
    answer = _text(getattr(response, "answer", None) if not isinstance(response, dict) else response.get("answer"))
    low = strip_accents(answer).casefold()
    return any(marker in low for marker in _BUSY_MARKERS)


def _mode(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("mode") or "")
    return str(getattr(response, "mode", "") or "")


def _answer(response: Any) -> str:
    if isinstance(response, dict):
        return _text(response.get("answer") or response.get("message"))
    return _text(getattr(response, "answer", None) or getattr(response, "message", None))


def _rows(response: Any) -> list[list[Any]]:
    if isinstance(response, dict):
        return list(response.get("rows") or [])
    return list(getattr(response, "rows", None) or [])


def score_sql(response: Any, reference: dict[str, Any], *, sql_executed: bool) -> dict[str, Any]:
    expected = reference.get("rows") or []
    actual = _rows(response)
    ok = _mode(response) == "sql" and rows_match(
        actual,
        expected,
        tolerance=float(reference.get("tolerance") or DEFAULT_TOLERANCE),
    )
    return {
        "correct": bool(ok),
        "reason": "ok" if ok else "sql_mismatch",
        "sql_executed": sql_executed,
        # First rows the assistant returned (fictional demo data), to debug mismatches.
        "rows": [[_json_cell(cell) for cell in row] for row in actual[:MAX_RECORDED_ROWS]],
    }


def score_doc(response: Any, reference: dict[str, Any]) -> dict[str, Any]:
    answer = _answer(response)
    pages = cited_pages(response)
    if reference.get("unknown"):
        unknown = _mode(response) in {"none", "chat"} or is_no_information(answer)
        no_sources = not pages
        ok = unknown and no_sources
        return {
            "correct": ok,
            "reason": "ok" if ok else "expected_unknown",
            "pages": pages,
        }
    facts = _facts(reference)
    has_fact = any(_contains_fact(answer, fact) for fact in facts) if facts else bool(answer)
    expected_page = reference.get("page")
    page_ok = True
    if pages and expected_page is not None:
        page_ok = expected_page in pages
    ok = has_fact and page_ok and not is_busy_answer(response)
    reason = "ok"
    if not has_fact:
        reason = "missing_fact"
    elif not page_ok:
        reason = "wrong_page"
    return {"correct": ok, "reason": reason, "pages": pages}


def score_refusal(response: Any, *, sql_executed: bool) -> dict[str, Any]:
    answer = _answer(response)
    unknown = _mode(response) in {"none", "chat"} or is_no_information(answer)
    ok = (not sql_executed) and unknown
    reason = "ok"
    if sql_executed:
        reason = "sql_executed"
    elif not unknown:
        reason = "not_refused"
    return {"correct": ok, "reason": reason, "sql_executed": sql_executed}


def score_item(
    item: dict[str, Any],
    response: Any,
    *,
    sql_executed: bool,
) -> dict[str, Any]:
    kind = item.get("type")
    reference = item.get("reference") or {}
    if kind == "sql":
        result = score_sql(response, reference, sql_executed=sql_executed)
    elif kind == "doc":
        result = score_doc(response, reference)
    elif kind == "refusal":
        result = score_refusal(response, sql_executed=sql_executed)
    else:
        result = {"correct": False, "reason": "unknown_type"}
    result["id"] = item.get("id")
    result["type"] = kind
    return result
