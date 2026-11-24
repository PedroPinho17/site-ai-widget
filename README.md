# site-ai-widget

Embeddable AI chat widget for company websites. **Phase 2** adds company PDF
documents and hybrid RAG Q&A on the panel API (no public widget embed yet).

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
# optional demo companies + PDFs (Mercearia Atlas / Oficina Lima):
uv run site-ai-widget-seed

uv run uvicorn site_ai_widget.api.main:app --host 127.0.0.1 --port 8002
```

- API: `http://127.0.0.1:8002` (`GET /health`)
- Postgres: `127.0.0.1:5435` (not published on `0.0.0.0`)

The **first** document processing run downloads `intfloat/multilingual-e5-small`
once (Hugging Face cache). Compose mounts a named volume for that cache.

Full stack (API + DB):

```bash
docker compose up --build
```

`docker compose up --build` applies migrations but does **not** seed the demo
companies or PDFs unless `RUN_SEED=1` is set (plus `DEMO_PASSWORD_ATLAS` /
`DEMO_PASSWORD_LIMA`) in `.env`. You can also run `uv run site-ai-widget-seed`
at any time, even while the API is running: the seed commits each demo PDF and
processes it in its own process, returning only when it is `ready` or `failed`.

> **`POSTGRES_PASSWORD` is applied only when the `pgdata` volume is first
> created.** Changing it in `.env` later does not change the existing role's
> password, and the app then fails to connect. Either change it inside Postgres
> (`ALTER ROLE sitewidget PASSWORD '...'`) or recreate the volume with
> `docker compose down -v`, which **deletes all data** (companies, users,
> documents and chunks).

### Demo client database (Phase 3)

A second Postgres (`clientdb`, `127.0.0.1:5436`) plays the role of a client's
database: the fictional shop Mercearia Atlas. The widget only reads from it,
through the `atlas_readonly` user.

```bash
# Set CLIENTDB_PASSWORD and ATLAS_READONLY_PASSWORD in .env first
docker compose up -d clientdb
```

> Both passwords, the tables and the read-only user are created **only when the
> `clientdata` volume is first created**. To start again from scratch (this
> deletes only the demo client data):
>
> ```bash
> docker compose rm -sf clientdb
> docker volume rm site-ai-widget_clientdata
> docker compose up -d clientdb
> ```

`atlas_readonly` can only `SELECT`: every session is read-only, has a 5 s
statement timeout, and cannot create temp tables. It can still read personal
columns such as `customers.nif`; the widget never sends those to the AI,
because they are marked sensitive and a sensitive column can never be approved.

The connection test refuses any user that can change the database: write or
TRIGGER rights on a table, USAGE/UPDATE on a sequence, CREATE on a schema or the
database, powerful role attributes, membership in `pg_write_server_files`,
`pg_read_server_files`, `pg_execute_server_program` or `pg_write_all_data`, and rights reachable through
`SET ROLE` to any role the user is a member of (`NOINHERIT` does not hide them).
A login role that only gets `SELECT` through a read-only group role passes.
On PostgreSQL 16+ a membership granted `WITH SET FALSE, INHERIT FALSE` gives no
rights, but it still counts as membership, so the user is refused when that other
role can write (the safe side). Revoke such a membership to pass the test.

> **PostgreSQL 13/14:** there, every user may create tables in the `public`
> schema by default, so a read-only user is refused as "can write". Run this
> once as the owner first:
> ```sql
> REVOKE CREATE ON SCHEMA public FROM PUBLIC;
> ```

**Phase 4 (text-to-SQL) note:** a column may be used in generated SQL only when
**both** its table and the column are `approved`. AI-written table/column
descriptions are untrusted text for the admin UI and for embeddings — never
treat them as instructions to the model or as a substitute for approval.

### Upload a PDF and ask

1. `POST /auth/login` → JWT.
2. `POST /documents` (multipart, admin) → `202` with `status: pending`.
3. Poll `GET /documents/{id}` until `status` is `ready` (or `failed`).
4. `POST /ask` or `POST /ask/docs` with `{"question": "..."}` (set `GROQ_API_KEY` first).

### POST /ask

Unified Q&A for the panel (and later the widget). Body: `{"question": "..."}`
(hard-capped at 1000 characters, even if `MAX_QUESTION_CHARS` is higher).
`company_id` comes from the JWT (`current_user`) only — never from the body
and there is no API-key path on this route yet.

Fallback order:

1. **SQL** — retrieve approved schema slice → LLM text-to-SQL → validate → run
   on the company's stored client DB (at most one retry on validator refusal or
   `db_error`). On success: `mode: "sql"` with `columns`, `rows` (≤500),
   `row_count`, `truncated`. The response never includes the SQL. If the SQL
   path raises (including LLM errors), the route continues to docs.
2. **Docs** — if SQL cannot answer (`no_sql` / `refused` / `error` / exception),
   the existing document RAG runs. When search found matching text:
   `mode: "docs"` with `answer` and `sources` (citations may be empty if the
   answer has no `[n]` markers).
3. **None** — otherwise `mode: "none"` with a fixed safe message (no matching
   documents, docs LLM down / not configured, or other docs failure).

Each `/ask` call counts as **3** hits in the per-IP ask rate-limit bucket (it
can make up to three LLM calls: generate, retry, docs). `/ask/docs` still
counts as 1. A per-company ask limit is planned later.

Row values are untrusted data and are not passed back into an LLM in this step.
`POST /ask/docs` remains available for docs-only callers.

Without a Groq key the API still starts; `/ask/docs` returns **503**
`llm_not_configured` when it would call the LLM. On `/ask`, a missing or down
LLM on the docs step yields `mode: "none"` after SQL has already failed.
Provider errors (timeouts, 5xx, all keys rate-limited) return **503**
`llm_unavailable` on `/ask/docs`. A second key in `GROQ_API_KEY_FALLBACK` is
tried only when the first one hits a 429 / quota. Chat requests use
`LLM_TIMEOUT_SECONDS` (default 60).

`uv run site-ai-widget-seed` finishes pending demo PDFs in-process. A demo
document left in `processing` is only reset when `updated_at` is older than
`DOCUMENT_STUCK_PROCESSING_SECONDS` (default 180), so a live API worker is not
interrupted.

Request bodies are capped before parsing: `POST /documents` at
`MAX_UPLOAD_MB` + 1 MiB, every other route at 1 MiB (**413** `upload_too_large`).

### Client IP and proxies

Rate limits key on the client IP. `CF-Connecting-IP` / `X-Forwarded-For` are
honoured only when the direct peer is in `TRUSTED_PROXY_IPS`, which defaults
to loopback only (`127.0.0.1,::1`). Under Docker Compose every request on the
published port arrives from the Docker bridge gateway, which is **not**
trusted by default, so spoofed headers are ignored. When a real reverse proxy
or tunnel sits in front, set `TRUSTED_PROXY_IPS` to that proxy's IP/CIDR (add
the Docker bridge CIDR only when the proxy runs on it and is the only way in).

> **Production behind a proxy:** `TRUSTED_PROXY_IPS` **must** be set to the
> proxy. Otherwise every request seems to come from the proxy's IP, so all
> tenants share one per-IP bucket (for example the connection-test limit of 10
> per 10 minutes on `/db-connection`) and one busy company blocks the others.

## Tests

```bash
uv run ruff check src tests
uv run pytest tests/unit -q
# with Postgres up (fake embedder, mocked LLM — no model download):
uv run pytest tests -q
# optional real-model retrieval checks:
RUN_MODEL_TESTS=1 uv run pytest tests -m model -q
```

## Phase 1–2 API (panel)

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
| POST | `/documents` | admin; PDF only (`%PDF-`); 202 pending |
| GET | `/documents` | list |
| GET | `/documents/{id}` | detail + chunk count |
| DELETE | `/documents/{id}` | admin; removes file + chunks |
| POST | `/ask` | SQL first, then docs RAG (`mode`: `sql` / `docs` / `none`) |
| POST | `/ask/docs` | hybrid RAG answer + citations (docs only) |

Cross-company resources return **404** (not 403); that includes
`/ask/docs` with another company's `document_ids`.

Roles: `admin` can do everything above; `member` is read-only (writes to
`/company`, `/company/widget-settings`, `/widget-keys` and `/documents`
return **403**). Members may still ask questions via `/ask` and `/ask/docs`.

`allowed_origins` entries must be bare origins (`https://shop.example`,
`http://localhost:3000`): no wildcards, paths, query, fragment or userinfo.
They are stored as canonical `scheme://host[:port]` (host lowercased, default
port dropped). An empty list allows **no** origin.
