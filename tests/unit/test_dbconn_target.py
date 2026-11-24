"""SSRF guard, connection string and error mapping for client databases (no network)."""
import socket

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn import target
from site_ai_widget.dbconn.guard import map_connect_error
from site_ai_widget.dbconn.target import ConnectionParams, build_conninfo, params_from_conninfo, resolve_target


def fake_dns(*ips):
    """Replace socket.getaddrinfo with a fixed answer."""

    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    return fake


@pytest.fixture
def no_allowlist(settings_env):
    settings_env.setenv("CLIENTDB_ALLOWED_HOSTS", "")
    clear_settings_cache()
    return settings_env


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.0.0.5", "172.18.0.2", "192.168.1.10", "169.254.169.254", "100.64.0.1", "0.0.0.0"],
)
def test_internal_addresses_are_blocked(no_allowlist, ip):
    with pytest.raises(ApiError) as exc:
        resolve_target(ip, 5432)
    assert exc.value.code == "db_host_not_allowed"


def test_docker_service_name_is_blocked(no_allowlist):
    no_allowlist.setattr(target.socket, "getaddrinfo", fake_dns("172.18.0.3"))
    with pytest.raises(ApiError) as exc:
        resolve_target("db", 5432)
    assert exc.value.code == "db_host_not_allowed"


def test_mixed_dns_answer_is_blocked(no_allowlist):
    no_allowlist.setattr(target.socket, "getaddrinfo", fake_dns("8.8.8.8", "10.0.0.1"))
    with pytest.raises(ApiError) as exc:
        resolve_target("tricky.example.com", 5432)
    assert exc.value.code == "db_host_not_allowed"


def test_public_host_is_allowed(no_allowlist):
    no_allowlist.setattr(target.socket, "getaddrinfo", fake_dns("8.8.8.8"))
    assert resolve_target("DB.Example.com", 5432) == "8.8.8.8"


def test_allowlist_opens_only_that_port(settings_env):
    settings_env.setenv("CLIENTDB_ALLOWED_HOSTS", "127.0.0.1:5436")
    clear_settings_cache()
    assert resolve_target("127.0.0.1", 5436) == "127.0.0.1"
    with pytest.raises(ApiError) as exc:
        resolve_target("127.0.0.1", 5435)  # the widget's own database stays blocked
    assert exc.value.code == "db_host_not_allowed"


@pytest.mark.parametrize("host", ["", "a,b", "/var/run/postgresql", "host name", "h'ost", "-x", "::1", "a..b"])
def test_malformed_hosts_are_rejected(no_allowlist, host):
    with pytest.raises(ApiError) as exc:
        resolve_target(host, 5432)
    assert exc.value.code == "db_host_invalid"


@pytest.mark.parametrize("port", [0, 65536])
def test_bad_ports_are_rejected(no_allowlist, port):
    with pytest.raises(ApiError) as exc:
        resolve_target("8.8.8.8", port)
    assert exc.value.code == "db_host_invalid"


def test_unresolvable_host(no_allowlist):
    def fail(*args, **kwargs):
        raise socket.gaierror("no such host")

    no_allowlist.setattr(target.socket, "getaddrinfo", fail)
    with pytest.raises(ApiError) as exc:
        resolve_target("does-not-exist.invalid", 5432)
    assert exc.value.code == "db_host_unresolvable"


def test_conninfo_is_read_only_with_timeouts():
    params = ConnectionParams("db.example.com", 5432, "shop", "reader", "p a's\\s w")
    info = conninfo_to_dict(build_conninfo(params, hostaddr="8.8.8.8"))
    assert info["password"] == "p a's\\s w"
    assert info["hostaddr"] == "8.8.8.8"
    assert str(info["connect_timeout"]) == "5"
    assert "default_transaction_read_only=on" in info["options"]
    assert "statement_timeout=5000" in info["options"]


def test_params_repr_hides_password():
    params = ConnectionParams("db.example.com", 5432, "shop", "reader", "s3cret-Pa55")
    assert "s3cret" not in repr(params)


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ('connection failed: FATAL:  password authentication failed for user "reader"', "db_auth_failed"),
        ('FATAL:  role "database" does not exist', "db_auth_failed"),
        ('FATAL:  no pg_hba.conf entry for host "1.2.3.4"', "db_auth_failed"),
        ('FATAL:  database "nope" does not exist', "db_not_found"),
        ("connection timeout expired", "db_unreachable"),
    ],
)
def test_connect_errors_map_to_stable_codes(message, code):
    assert map_connect_error(psycopg.OperationalError(message)) == code


def test_other_errors_are_generic():
    assert map_connect_error(psycopg.ProgrammingError("x")) == "db_connection_failed"


def test_params_round_trip_through_conninfo():
    params = ConnectionParams(
        "db.example.com", 6543, "shop", "reader", "p a's\\s w", sslmode="require"
    )
    assert params_from_conninfo(build_conninfo(params)) == params
    info = conninfo_to_dict(build_conninfo(params))
    assert info["sslmode"] == "require"
