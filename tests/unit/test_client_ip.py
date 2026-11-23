"""Trusted-proxy client IP resolution (no Postgres)."""
from __future__ import annotations

from unittest.mock import MagicMock

from site_ai_widget.api.client_ip import (
    DEFAULT_TRUSTED_PROXIES,
    client_from_x_forwarded_for,
    client_ip,
    peer_is_trusted,
    trusted_proxy_networks,
)


def _request(peer: str | None, headers: dict[str, str] | None = None):
    req = MagicMock()
    if peer is None:
        req.client = None
    else:
        req.client = MagicMock(host=peer)
    req.headers = headers or {}
    return req


def test_default_networks_are_loopback_only():
    nets = trusted_proxy_networks("")
    assert peer_is_trusted("127.0.0.1", nets)
    assert peer_is_trusted("::1", nets)
    assert not peer_is_trusted("172.18.0.1", nets)  # Docker bridge gateway
    assert not peer_is_trusted("203.0.113.50", nets)
    assert trusted_proxy_networks(None) == nets


def test_docker_gateway_peer_cannot_spoof_cf_connecting_ip_by_default():
    """Compose: published-port traffic arrives from the bridge gateway.

    With the default trust list, a spoofed CF-Connecting-IP / XFF from that
    peer must be ignored so the strict auth rate limit cannot be bypassed.
    """
    req = _request(
        "172.18.0.1",
        {"cf-connecting-ip": "198.51.100.77", "x-forwarded-for": "198.51.100.78"},
    )
    assert client_ip(req) == "172.18.0.1"
    assert client_ip(req, trusted_raw=None) == "172.18.0.1"
    assert client_ip(req, trusted_raw="") == "172.18.0.1"


def test_docker_bridge_trusted_only_when_configured():
    req = _request("172.18.0.1", {"cf-connecting-ip": "198.51.100.77"})
    assert client_ip(req, trusted_raw="172.18.0.0/16") == "198.51.100.77"


def test_custom_trusted_proxy_list():
    nets = trusted_proxy_networks("10.0.0.0/8,192.0.2.1")
    assert peer_is_trusted("10.1.2.3", nets)
    assert peer_is_trusted("192.0.2.1", nets)
    assert not peer_is_trusted("127.0.0.1", nets)


def test_untrusted_peer_ignores_spoofed_xff():
    """Spoofed X-Forwarded-For from an untrusted peer must be ignored."""
    req = _request(
        "203.0.113.9",
        {"x-forwarded-for": "198.51.100.1, 203.0.113.9"},
    )
    assert client_ip(req, trusted_raw=DEFAULT_TRUSTED_PROXIES) == "203.0.113.9"


def test_untrusted_peer_ignores_cf_connecting_ip():
    req = _request(
        "203.0.113.9",
        {"cf-connecting-ip": "198.51.100.1"},
    )
    assert client_ip(req, trusted_raw=DEFAULT_TRUSTED_PROXIES) == "203.0.113.9"


def test_trusted_peer_uses_cf_connecting_ip():
    req = _request(
        "127.0.0.1",
        {"cf-connecting-ip": "198.51.100.7", "x-forwarded-for": "203.0.113.1"},
    )
    assert client_ip(req, trusted_raw=DEFAULT_TRUSTED_PROXIES) == "198.51.100.7"


def test_trusted_peer_uses_rightmost_untrusted_xff():
    """When the peer is trusted, walk XFF right→left and take first non-proxy."""
    # client, then a trusted hop (docker), then we are the edge
    req = _request(
        "172.18.0.5",
        {"x-forwarded-for": "198.51.100.20, 172.18.0.5"},
    )
    assert client_ip(req, trusted_raw="172.16.0.0/12") == "198.51.100.20"


def test_client_from_x_forwarded_for_rightmost_untrusted():
    nets = trusted_proxy_networks(DEFAULT_TRUSTED_PROXIES)
    assert (
        client_from_x_forwarded_for("198.51.100.1, 203.0.113.2, 127.0.0.1", nets)
        == "203.0.113.2"
    )


def test_trusted_peer_without_headers_returns_peer():
    req = _request("127.0.0.1", {})
    assert client_ip(req, trusted_raw=DEFAULT_TRUSTED_PROXIES) == "127.0.0.1"
