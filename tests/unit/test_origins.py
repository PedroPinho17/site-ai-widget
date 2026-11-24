"""allowed_origins validation and canonicalisation (no Postgres)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from site_ai_widget.api.schemas import (
    MAX_ORIGINS,
    WidgetKeyCreate,
    WidgetKeyUpdate,
    normalize_origin,
    parse_panel_origins,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://shop.example", "https://shop.example"),
        ("https://shop.example/", "https://shop.example"),
        ("  https://shop.example  ", "https://shop.example"),
        ("HTTPS://Shop.EXAMPLE", "https://shop.example"),
        ("https://shop.example:443", "https://shop.example"),
        ("http://shop.example:80/", "http://shop.example"),
        ("https://shop.example:8443", "https://shop.example:8443"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("https://[::1]:8443", "https://[::1]:8443"),
        ("https://[2001:DB8:0:0::1]", "https://[2001:db8::1]"),
        ("https://sub-domain.mercearia-atlas.example", "https://sub-domain.mercearia-atlas.example"),
        ("https://café.example", "https://xn--caf-dma.example"),
    ],
)
def test_accepts_and_canonicalises(raw, expected):
    assert normalize_origin(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "shop.example",  # no scheme
        "ftp://shop.example",
        "javascript://shop.example",
        "file:///etc/passwd",
        "https://",
        "https:///path",
        "https:shop.example",
        "https://*",
        "https://*.example.com",
        "https://shop.*.example",
        "*",
        "https://user@shop.example",
        "https://user:pw@shop.example",
        "https://@shop.example",
        "https://shop.example/path",
        "https://shop.example/a/",
        "https://shop.example//",
        "https://shop.example?x=1",
        "https://shop.example/?",
        "https://shop.example#frag",
        "https://shop.example/#",
        "https://shop.example:99999",
        "https://shop.example:0",
        "https://shop.example:abc",
        "https://shop..example",
        "https://-bad.example",
        "https://bad-.example",
        "https://under_score.example",
        "https://999.1.1.1",
        "https://1.2.3",
        "https://[not-ipv6]",
        "https://[fe80::1%25eth0]",
        "https://shop example",
        "https://shop.example\\evil.example",
        "https://" + "a" * 64 + ".example",
        "https://shop\n.example",
        "https://shop.example\t/",
    ],
)
def test_rejects_invalid(raw):
    with pytest.raises(ValueError):
        normalize_origin(raw)


def test_model_normalises_and_dedupes():
    body = WidgetKeyCreate(
        allowed_origins=["https://Shop.example/", "https://shop.example:443", "http://localhost:5173"]
    )
    assert body.allowed_origins == ["https://shop.example", "http://localhost:5173"]


def test_empty_list_is_allowed_and_means_no_origins():
    assert WidgetKeyCreate().allowed_origins == []
    assert WidgetKeyCreate(allowed_origins=[]).allowed_origins == []
    assert WidgetKeyUpdate(allowed_origins=[]).allowed_origins == []


def test_model_rejects_wildcard():
    with pytest.raises(ValidationError):
        WidgetKeyCreate(allowed_origins=["https://*.example.com"])
    with pytest.raises(ValidationError):
        WidgetKeyUpdate(allowed_origins=["https://ok.example", "https://*"])


def test_update_none_leaves_origins_untouched():
    body = WidgetKeyUpdate(is_active=False)
    assert body.allowed_origins is None
    assert "allowed_origins" not in body.model_dump(exclude_unset=True)


def test_too_many_origins_rejected():
    many = [f"https://s{i}.example" for i in range(MAX_ORIGINS + 1)]
    with pytest.raises(ValidationError):
        WidgetKeyCreate(allowed_origins=many)


def test_parse_panel_origins_canonicalises_and_skips_blanks():
    assert parse_panel_origins("http://127.0.0.1:5173, http://localhost:5173,") == (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    assert parse_panel_origins("") == ()
    assert parse_panel_origins("   ") == ()


@pytest.mark.parametrize("raw", ["*", "https://*", "https://*.example.com", "https://shop.example/path"])
def test_parse_panel_origins_rejects_wildcard_and_path(raw):
    with pytest.raises(ValueError):
        parse_panel_origins(raw)


def _shared_cases() -> list[dict]:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "fixtures" / "origin_cases.json"
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _shared_cases(), ids=lambda c: repr(c["input"]))
def test_shared_origin_cases_match_the_panel(case):
    """Same vectors as panel/src/test/origin.test.ts, so both sides agree."""
    if case["expected"] is None:
        with pytest.raises(ValueError):
            normalize_origin(case["input"])
    else:
        assert normalize_origin(case["input"]) == case["expected"]
