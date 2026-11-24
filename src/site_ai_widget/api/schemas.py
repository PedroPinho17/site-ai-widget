"""Pydantic request/response models for the panel API."""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DNS_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
POSITIONS = ("bottom-right", "bottom-left")
DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_ORIGINS = 50
MAX_ORIGIN_LEN = 300


class RegisterRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=120)
    company_slug: str = Field(min_length=2, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    default_locale: str = Field(default="pt", max_length=10)

    @field_validator("company_slug")
    @classmethod
    def slug_format(cls, value: str) -> str:
        value = value.strip().lower()
        if not SLUG_RE.match(value):
            raise ValueError("slug must be lowercase letters, numbers, and hyphens")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    company_id: str


class CompanyResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    default_locale: str
    created_at: datetime


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    default_locale: str | None = Field(default=None, max_length=10)


class WidgetSettingsResponse(BaseModel):
    company_id: UUID
    primary_color: str
    greeting: str
    locale: str
    position: str
    assistant_name: str


class WidgetSettingsUpdate(BaseModel):
    primary_color: str | None = None
    greeting: str | None = Field(default=None, max_length=500)
    locale: str | None = Field(default=None, max_length=10)
    position: str | None = None
    assistant_name: str | None = Field(default=None, max_length=80)

    @field_validator("primary_color")
    @classmethod
    def color_hex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not HEX_COLOR.match(value):
            raise ValueError("primary_color must be #RRGGBB")
        return value.lower()

    @field_validator("position")
    @classmethod
    def position_ok(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in POSITIONS:
            raise ValueError(f"position must be one of {POSITIONS}")
        return value


def _valid_dns_name(host: str) -> bool:
    if len(host) > 253:
        return False
    labels = host.split(".")
    if not all(DNS_LABEL.match(label) for label in labels):
        return False
    # A numeric final label is never a real TLD (e.g. "999.1.1.1" is not a host)
    return not labels[-1].isdigit()


def _ipv4_or_dns_host(host: str) -> str | None:
    """Canonical IPv4 or lowercase ASCII (punycode) DNS name; None if invalid."""
    try:
        return str(ipaddress.IPv4Address(host))
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    return ascii_host if _valid_dns_name(ascii_host) else None


def normalize_origin(raw: str) -> str:
    """Validate one browser origin and return ``scheme://host[:port]``.

    Rejects wildcards, userinfo, paths (other than ``/``), query strings,
    fragments and anything that is not a real DNS name / IPv4 / [IPv6] host.
    The host is lowercased (IDN → punycode) and default ports are dropped so
    the value compares equal to a browser's ``Origin`` header.
    """
    if not isinstance(raw, str):
        raise ValueError("origin must be a string")
    text = raw.strip()
    if not text or len(text) > MAX_ORIGIN_LEN:
        raise ValueError(f"invalid origin: {raw!r}")
    if any(ch.isspace() or ord(ch) < 0x20 or ch in "\\*" for ch in text):
        raise ValueError(f"invalid origin (illegal character): {raw!r}")
    if "?" in text or "#" in text:
        raise ValueError(f"origin must not have a query or fragment: {raw!r}")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise ValueError(f"invalid origin: {raw!r}") from None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise ValueError(f"origin scheme must be http or https: {raw!r}")
    if "@" in parts.netloc:
        raise ValueError(f"origin must not contain userinfo: {raw!r}")
    if parts.path not in ("", "/"):
        raise ValueError(f"origin must not have a path: {raw!r}")
    host = parts.hostname or ""
    if not host:
        raise ValueError(f"origin must have a host: {raw!r}")

    if parts.netloc.startswith("["):
        if "%" in host:
            raise ValueError(f"origin must not use an IPv6 zone id: {raw!r}")
        try:
            host_out = f"[{ipaddress.IPv6Address(host).compressed}]"
        except ValueError:
            raise ValueError(f"invalid IPv6 host in origin: {raw!r}") from None
    else:
        host_out = _ipv4_or_dns_host(host)
        if host_out is None:
            raise ValueError(f"invalid host in origin: {raw!r}")

    if port == 0:
        raise ValueError(f"invalid port in origin: {raw!r}")
    if port is None or port == DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host_out}"
    return f"{scheme}://{host_out}:{port}"


def normalize_origins(values: list[str]) -> list[str]:
    """Normalize and de-duplicate (order kept). Empty list = no origin allowed."""
    out: list[str] = []
    for value in values:
        origin = normalize_origin(value)
        if origin not in out:
            out.append(origin)
    return out


def parse_panel_origins(raw: str) -> tuple[str, ...]:
    """Comma-separated panel origins. Empty means none. Never ``*``."""
    if not isinstance(raw, str):
        raise ValueError("PANEL_ORIGINS must be a string")
    return tuple(normalize_origins([item for item in raw.split(",") if item.strip()]))


class WidgetKeyCreate(BaseModel):
    # Empty list means NO origin is allowed (deny-all), never allow-all.
    allowed_origins: list[str] = Field(default_factory=list, max_length=MAX_ORIGINS)

    @field_validator("allowed_origins")
    @classmethod
    def origins_ok(cls, value: list[str]) -> list[str]:
        return normalize_origins(value)


class WidgetKeyUpdate(BaseModel):
    allowed_origins: list[str] | None = Field(default=None, max_length=MAX_ORIGINS)
    is_active: bool | None = None

    @field_validator("allowed_origins")
    @classmethod
    def origins_ok(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return normalize_origins(value)


class WidgetKeyResponse(BaseModel):
    id: UUID
    public_key: str
    allowed_origins: list[str]
    is_active: bool
    created_at: datetime


# Phase 3: client database connection


class DbConnectionSave(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=63)
    username: str = Field(min_length=1, max_length=63)
    password: str = Field(min_length=1, max_length=200, repr=False)
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = "prefer"

    @field_validator("host", "database", "username", "password")
    @classmethod
    def no_control_chars(cls, value: str) -> str:
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("control characters are not allowed")
        return value


class DbConnectionResponse(BaseModel):
    """Never contains the password or the DSN."""

    host: str
    port: int
    database: str
    username: str
    status: str
    last_error: str | None = None
    last_checked_at: datetime | None = None
    last_introspected_at: datetime | None = None
    readable_tables: int | None = None


class SchemaApprovalUpdate(BaseModel):
    """Tables the widget may query. Others that are still present become pending."""

    approved_table_ids: list[UUID] = Field(default_factory=list, max_length=2000)

    @field_validator("approved_table_ids")
    @classmethod
    def unique_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate table ids")
        return value


class IntrospectResponse(BaseModel):
    tables: int
    columns: int
    removed_tables: int
    needs_review: int


class SuggestResponse(BaseModel):
    suggested: int
    remaining: int


class SchemaColumnResponse(BaseModel):
    id: UUID
    name: str
    data_type: str
    is_nullable: bool
    ordinal: int
    status: str
    is_sensitive: bool
    description: str | None = None
    is_present: bool


class SchemaTableResponse(BaseModel):
    id: UUID
    schema_name: str
    name: str
    kind: str
    status: str
    ai_suggested: bool
    description: str | None = None
    is_present: bool
    columns: list[SchemaColumnResponse]


class SchemaResponse(BaseModel):
    tables: list[SchemaTableResponse]


SchemaStatus = Literal["pending", "approved", "excluded"]


class SchemaTableUpdate(BaseModel):
    """Only the fields that are sent change. An empty description clears it."""

    status: SchemaStatus | None = None
    description: str | None = Field(default=None, max_length=500)

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("control characters are not allowed")
        return value.strip()


class SchemaColumnUpdate(SchemaTableUpdate):
    is_sensitive: bool | None = None
