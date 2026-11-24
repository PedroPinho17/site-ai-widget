"""Where a client database connection may point (SSRF guard) and how we build it."""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError

CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 5000

# Letters, digits, dots and dashes only: no commas (libpq would try several hosts),
# no slashes (Unix socket paths), no spaces, quotes or IPv6 colons.
_HOST_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")


# libpq sslmode values we accept from the panel (write-only; stored only in the DSN).
SSL_MODES = frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"})
DEFAULT_SSL_MODE = "prefer"


@dataclass(frozen=True)
class ConnectionParams:
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)  # never shown in prints or logs
    sslmode: str = DEFAULT_SSL_MODE


def resolve_target(host: str, port: int) -> str:
    """Check where a connection may go and return the IP to connect to.

    The name is resolved once and we connect to that exact IP (hostaddr),
    so the DNS answer cannot change between the check and the connection.
    """
    host = host.strip().lower()
    if not _HOST_RE.fullmatch(host) or ".." in host or not 1 <= port <= 65535:
        raise ApiError("db_host_invalid", 422)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise ApiError("db_host_unresolvable", 422) from None
    ips = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}
    if not ips:
        raise ApiError("db_host_unresolvable", 422)
    allowed = (host, port) in get_settings().clientdb_allowlist()
    if not allowed and not all(ip.is_global and not ip.is_multicast for ip in ips):
        raise ApiError("db_host_not_allowed", 422)
    # Prefer IPv4 when the name has both.
    return str(min(ips, key=lambda ip: ip.version))


def build_conninfo(params: ConnectionParams, hostaddr: str | None = None) -> str:
    """libpq connection string. Every session is read-only and has a short timeout."""
    options = f"-c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT_MS}"
    extra = {"hostaddr": hostaddr} if hostaddr else {}
    sslmode = params.sslmode if params.sslmode in SSL_MODES else DEFAULT_SSL_MODE
    return make_conninfo(
        host=params.host,
        port=params.port,
        dbname=params.database,
        user=params.username,
        password=params.password,
        sslmode=sslmode,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        application_name="site-ai-widget",
        options=options,
        **extra,
    )


def params_from_conninfo(conninfo: str) -> ConnectionParams:
    """Rebuild the parameters from a saved (decrypted) connection string."""
    info = conninfo_to_dict(conninfo)
    sslmode = str(info.get("sslmode") or DEFAULT_SSL_MODE)
    if sslmode not in SSL_MODES:
        sslmode = DEFAULT_SSL_MODE
    return ConnectionParams(
        host=str(info["host"]),
        port=int(info["port"]),
        database=str(info["dbname"]),
        username=str(info["user"]),
        password=str(info["password"]),
        sslmode=sslmode,
    )
