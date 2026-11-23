# site-ai-widget

Embeddable AI chat widget for company websites. **Phase 1** covers the panel API: auth, company settings, and widget keys (no public `/ask` yet).

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Docker (Postgres 17 + pgvector)

## Local setup

```bash
cp .env.example .env
# Set JWT_SECRET (>= 32 chars), POSTGRES_PASSWORD, DATABASE_URL, and demo passwords
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"

docker compose up -d db
uv sync
uv run site-ai-widget-migrate
# optional demo companies (Mercearia Atlas / Oficina Lima):
uv run site-ai-widget-seed

uv run uvicorn site_ai_widget.api.main:app --host 127.0.0.1 --port 8002
```

- API: `http://127.0.0.1:8002` (`GET /health`)
- Postgres: `127.0.0.1:5435` (not published on `0.0.0.0`)

Full stack (API + DB):

```bash
docker compose up --build
```

`docker compose up --build` applies migrations but does **not** seed the demo
companies. To seed them, set `RUN_SEED=1` (plus `DEMO_PASSWORD_ATLAS` /
`DEMO_PASSWORD_LIMA`) in `.env`, or run `uv run site-ai-widget-seed` against the DB.

> **`POSTGRES_PASSWORD` is applied only when the `pgdata` volume is first
> created.** Changing it in `.env` later does not change the existing role's
> password; the app then fails to connect. Either change it inside Postgres
> (`ALTER ROLE sitewidget PASSWORD '...'`) or recreate the volume with
> `docker compose down -v` (this **deletes all data**).

### Client IP and proxies

Rate limits key on the client IP. `CF-Connecting-IP` / `X-Forwarded-For` are
honoured only when the direct peer is in `TRUSTED_PROXY_IPS`, which defaults
to loopback only (`127.0.0.1,::1`). Under Docker Compose every request on the
published port arrives from the Docker bridge gateway, which is **not**
trusted by default, so spoofed headers are ignored. When a real reverse proxy
or tunnel sits in front, set `TRUSTED_PROXY_IPS` to that proxy's IP/CIDR (add
the Docker bridge CIDR only when the proxy runs on it and is the only way in).

## Tests

```bash
uv run ruff check src tests
uv run pytest tests/unit -q
# with Postgres up:
uv run pytest tests -q
```

## Phase 1 API (panel)

| Method | Path | Notes |
|--------|------|--------|
| POST | `/auth/register` | company + admin |
| POST | `/auth/login` | JWT |
| GET | `/auth/me` | current user |
| POST | `/auth/change-password` | bumps `token_version` (old JWTs die); returns a fresh token |
| GET/PATCH | `/company` | own company only |
| GET/PATCH | `/company/widget-settings` | greeting, colours, … |
| GET/POST | `/widget-keys` | list / create (`wk_…`) |
| PATCH | `/widget-keys/{id}` | `allowed_origins`, `is_active` |
| POST | `/widget-keys/{id}/revoke` | deactivate |

Cross-company resources return **404** (not 403).

Roles: `admin` can do everything above; `member` is read-only (writes to
`/company`, `/company/widget-settings` and `/widget-keys` return **403**).

`allowed_origins` entries must be bare origins (`https://shop.example`,
`http://localhost:3000`): no wildcards, paths, query, fragment or userinfo.
They are stored as canonical `scheme://host[:port]` (host lowercased, default
port dropped). An empty list allows **no** origin.
