"""Redact signed-link signatures from uvicorn access logs.

A ``/v1/signed-documents/...?exp=..&sig=..`` line in the access log would let
anyone who can read logs open the PDF until the link expires.
"""
from __future__ import annotations

import logging
import re

_SIG = re.compile(r"([?&]sig=)[^&\s#]*", re.IGNORECASE)


def redact(text: str) -> str:
    return _SIG.sub(r"\1[redacted]", text)


class RedactSignedQuery(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str) and "sig=" in record.msg:
            record.msg = redact(record.msg)
        return True


def install() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactSignedQuery) for f in logger.filters):
        logger.addFilter(RedactSignedQuery())
