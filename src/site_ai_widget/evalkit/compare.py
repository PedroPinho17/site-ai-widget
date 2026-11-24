"""Compare SQL result rows without depending on column order or row order."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from datetime import time as time_of_day
from decimal import Decimal
from typing import Any

_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
_NUMBER = re.compile(r"^-?\d+(?:[.,]\d+)?$")

DEFAULT_TOLERANCE = 0.01


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(" ", "").replace(",", ".")
        if _NUMBER.match(text):
            return float(text)
    return None


def _as_time_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time_of_day):
        return value.strftime("%H:%M:%S")
    if isinstance(value, str):
        match = _TIME.match(value.strip())
        if match:
            h, m, s = int(match[1]), int(match[2]), int(match[3] or 0)
            return f"{h:02d}:{m:02d}:{s:02d}"
    return None


def normalize_cell(value: Any, *, tolerance: float = DEFAULT_TOLERANCE) -> tuple:
    """Stable comparable key for one cell (type tag + canonical value)."""
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("bool", value)
    time_text = _as_time_text(value)
    if time_text is not None:
        return ("time", time_text)
    if isinstance(value, date) and not isinstance(value, datetime):
        return ("date", value.isoformat())
    number = _as_number(value)
    if number is not None:
        step = tolerance if tolerance > 0 else 1e-9
        bucket = round(number / step) * step
        return ("num", round(bucket, 8))
    text = strip_accents(str(value)).casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return ("str", text)


def _row_key(row: list[Any] | tuple[Any, ...], *, tolerance: float) -> tuple:
    cells = [normalize_cell(cell, tolerance=tolerance) for cell in row]
    return tuple(sorted(cells))


def rows_match(
    actual: list[list[Any]] | None,
    expected: list[list[Any]] | None,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> bool:
    """True when the two tables have the same cells per row (order-independent)."""
    left = actual or []
    right = expected or []
    if len(left) != len(right):
        return False
    left_keys = Counter(_row_key(row, tolerance=tolerance) for row in left)
    right_keys = Counter(_row_key(row, tolerance=tolerance) for row in right)
    return left_keys == right_keys
