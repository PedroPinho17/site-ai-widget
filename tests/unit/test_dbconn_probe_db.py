"""Connection test against the demo client DB (docker compose up -d clientdb)."""
import os

import psycopg
import pytest

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn.probe import probe_connection
from site_ai_widget.dbconn.target import ConnectionParams

pytestmark = pytest.mark.integration

HOST, PORT, DB = "127.0.0.1", 5436, "atlas"


def readonly_params(password: str | None = None, database: str = DB, port: int = PORT) -> ConnectionParams:
    pw = password if password is not None else os.environ.get("ATLAS_READONLY_PASSWORD", "")
    return ConnectionParams(HOST, port, database, "atlas_readonly", pw)


@pytest.fixture(scope="module")
def clientdb_up():
    ro_pw = os.environ.get("ATLAS_READONLY_PASSWORD")
    if not ro_pw:
        pytest.skip("ATLAS_READONLY_PASSWORD is not set")
    try:
        with psycopg.connect(
            host=HOST, port=PORT, dbname=DB, user="atlas_readonly", password=ro_pw, connect_timeout=1
        ):
            pass
    except psycopg.Error:
        pytest.skip("clientdb is not running (docker compose up -d clientdb)")


@pytest.fixture
def allow_demo(settings_env, clientdb_up):
    settings_env.setenv("CLIENTDB_ALLOWED_HOSTS", f"{HOST}:{PORT},{HOST}:5999")
    clear_settings_cache()
    return settings_env


def test_readonly_user_passes(allow_demo):
    result = probe_connection(readonly_params())
    assert result.readable_tables == 6
    assert result.server_version >= 170000


def test_owner_is_refused_because_it_can_write(allow_demo):
    owner_pw = os.environ.get("CLIENTDB_PASSWORD")
    if not owner_pw:
        pytest.skip("CLIENTDB_PASSWORD is not set")
    with pytest.raises(ApiError) as exc:
        probe_connection(ConnectionParams(HOST, PORT, DB, "atlas_owner", owner_pw))
    assert exc.value.code == "db_user_can_write"


def test_wrong_password_leaks_nothing(allow_demo):
    with pytest.raises(ApiError) as exc:
        probe_connection(readonly_params(password="wrong-secretpart"))
    assert exc.value.code == "db_auth_failed"
    assert "secretpart" not in str(exc.value)
    assert exc.value.__suppress_context__  # "from None": the libpq text is not chained


def test_missing_database(allow_demo):
    with pytest.raises(ApiError) as exc:
        probe_connection(readonly_params(database="nao_existe"))
    assert exc.value.code == "db_not_found"


def test_closed_port_is_unreachable(allow_demo):
    with pytest.raises(ApiError) as exc:
        probe_connection(readonly_params(port=5999))
    assert exc.value.code == "db_unreachable"


def test_demo_is_blocked_without_allowlist(settings_env, clientdb_up):
    settings_env.setenv("CLIENTDB_ALLOWED_HOSTS", "")
    clear_settings_cache()
    with pytest.raises(ApiError) as exc:
        probe_connection(readonly_params())
    assert exc.value.code == "db_host_not_allowed"


def test_column_level_update_is_refused(allow_demo):
    """A user that can UPDATE only one column must still fail the read-only probe."""
    owner_pw = os.environ.get("CLIENTDB_PASSWORD")
    if not owner_pw:
        pytest.skip("CLIENTDB_PASSWORD is not set")
    role = "probe_col_writer"
    password = "probe-col-writer-temp"
    with psycopg.connect(
        host=HOST, port=PORT, dbname=DB, user="atlas_owner", password=owner_pw, autocommit=True
    ) as conn:
        conn.execute(f"DROP ROLE IF EXISTS {role}")
        # PASSWORD cannot be a bound parameter in CREATE ROLE.
        conn.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
        conn.execute(f"GRANT CONNECT ON DATABASE {DB} TO {role}")
        conn.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}")
        conn.execute(f"GRANT UPDATE (price) ON products TO {role}")
        try:
            with pytest.raises(ApiError) as exc:
                probe_connection(ConnectionParams(HOST, PORT, DB, role, password))
            assert exc.value.code == "db_user_can_write"
        finally:
            conn.execute(f"DROP OWNED BY {role}")
            conn.execute(f"DROP ROLE IF EXISTS {role}")


# --- Temporary roles (created and dropped by each test, fictional passwords) ---------

TEMP_PASSWORD = "probe-temp-role-pw"


def _owner_conn():
    owner_pw = os.environ.get("CLIENTDB_PASSWORD")
    if not owner_pw:
        pytest.skip("CLIENTDB_PASSWORD is not set")
    return psycopg.connect(
        host=HOST, port=PORT, dbname=DB, user="atlas_owner", password=owner_pw, autocommit=True
    )


def _drop_roles(conn, *roles: str) -> None:
    for role in roles:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if exists:
            conn.execute(f"DROP OWNED BY {role}")
            conn.execute(f"DROP ROLE {role}")


def _reader_login(conn, role: str, *, noinherit: bool = False) -> None:
    """A login role that can read every demo table (like atlas_readonly)."""
    inherit = "NOINHERIT" if noinherit else "INHERIT"
    # PASSWORD cannot be a bound parameter in CREATE ROLE.
    conn.execute(f"CREATE ROLE {role} LOGIN {inherit} PASSWORD '{TEMP_PASSWORD}'")
    conn.execute(f"GRANT CONNECT ON DATABASE {DB} TO {role}")
    conn.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}")


def _probe_as(role: str):
    return probe_connection(ConnectionParams(HOST, PORT, DB, role, TEMP_PASSWORD))


@pytest.mark.parametrize(
    ("role", "grant"),
    [
        ("probe_t_seq_update", "GRANT UPDATE ON SEQUENCE products_id_seq TO {role}"),
        ("probe_t_seq_usage", "GRANT USAGE ON SEQUENCE categories_id_seq TO {role}"),
        ("probe_t_trigger", "GRANT TRIGGER ON products TO {role}"),
        ("probe_t_file_writer", "GRANT pg_write_server_files TO {role}"),
    ],
)
def test_extra_write_rights_are_refused(allow_demo, role, grant):
    with _owner_conn() as conn:
        _drop_roles(conn, role)
        _reader_login(conn, role)
        conn.execute(grant.format(role=role))
        try:
            with pytest.raises(ApiError) as exc:
                _probe_as(role)
            assert exc.value.code == "db_user_can_write"
        finally:
            _drop_roles(conn, role)


def test_noinherit_member_of_a_writer_role_is_refused(allow_demo):
    """NOINHERIT hides the writer's rights from has_table_privilege, but SET ROLE works."""
    user, writer = "probe_t_noinherit_user", "probe_t_writer_group"
    with _owner_conn() as conn:
        _drop_roles(conn, user, writer)
        conn.execute(f"CREATE ROLE {writer} NOLOGIN")
        conn.execute(f"GRANT INSERT ON products TO {writer}")
        _reader_login(conn, user, noinherit=True)
        conn.execute(f"GRANT {writer} TO {user}")
        try:
            with pytest.raises(ApiError) as exc:
                _probe_as(user)
            assert exc.value.code == "db_user_can_write"
        finally:
            _drop_roles(conn, user, writer)


def test_member_of_a_read_only_group_role_passes(allow_demo):
    """The usual setup: the login role gets SELECT through a read-only group role."""
    user, readers = "probe_t_group_user", "probe_t_readers"
    with _owner_conn() as conn:
        _drop_roles(conn, user, readers)
        conn.execute(f"CREATE ROLE {readers} NOLOGIN")
        conn.execute(f"GRANT USAGE ON SCHEMA public TO {readers}")
        conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {readers}")
        conn.execute(f"CREATE ROLE {user} LOGIN PASSWORD '{TEMP_PASSWORD}'")
        conn.execute(f"GRANT CONNECT ON DATABASE {DB} TO {user}")
        conn.execute(f"GRANT {readers} TO {user}")
        try:
            result = _probe_as(user)
            assert result.readable_tables == 6
        finally:
            _drop_roles(conn, user, readers)
