"""Resolve the client IP for rate limiting, honouring proxies only when trusted."""
from __future__ import annotations

import ipaddress
import logging

from fastapi import Request

log = logging.getLogger("site_ai_widget")

# Loopback only. Do NOT add the Docker bridge (172.16.0.0/12) by default: under
# Docker Compose every request through a published port arrives from the bridge
# gateway, so trusting it would let any client spoof CF-Connecting-IP /
# X-Forwarded-For and dodge per-IP rate limits. When a real reverse proxy or
# tunnel sits in front, set TRUSTED_PROXY_IPS to that proxy's IP/CIDR.
DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1"


def trusted_proxy_networks(raw: str | None = None) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse ``TRUSTED_PROXY_IPS`` (comma-separated). Empty/None → defaults."""
    text = DEFAULT_TRUSTED_PROXIES if raw is None or not str(raw).strip() else raw
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            log.warning("TRUSTED_PROXY_IPS: ignoring invalid entry %r", part)
    return nets


def peer_is_trusted(host: str, networks: list | None = None) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    nets = networks if networks is not None else trusted_proxy_networks()
    return any(ip in net for net in nets)


def client_from_x_forwarded_for(xff: str, networks: list | None = None) -> str | None:
    """Right → left in ``X-Forwarded-For``: first entry that is *not* a trusted proxy.

    A client cannot spoof by prepending a fake IP; trusted proxies on the right
    are skipped so we land on the original client.
    """
    nets = networks if networks is not None else trusted_proxy_networks()
    for part in reversed([p.strip() for p in xff.split(",") if p.strip()]):
        if not peer_is_trusted(part, nets):
            return part
    return None


def client_ip(request: Request, trusted_raw: str | None = None) -> str:
    """Client IP for rate limits.

    Trust ``CF-Connecting-IP`` / ``X-Forwarded-For`` only when the direct peer
    (``request.client.host``) is in ``TRUSTED_PROXY_IPS``. Otherwise use the peer
    and ignore those headers (anti-spoof).
    """
    peer = request.client.host if request.client else None
    if not peer:
        return "unknown"
    nets = trusted_proxy_networks(trusted_raw)
    if not peer_is_trusted(peer, nets):
        return peer
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf.split(",")[0].strip()
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return client_from_x_forwarded_for(xff, nets) or peer
    return peer
