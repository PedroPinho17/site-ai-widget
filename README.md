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

### Upload a PDF and ask

1. `POST /auth/login` → JWT.
2. `POST /documents` (multipart, admin) → `202` with `status: pending`.
3. Poll `GET /documents/{id}` until `status` is `ready` (or `failed`).
4. `POST /ask/docs` with `{"question": "..."}` (set `GROQ_API_KEY` first).

Without a Groq key the API still starts; `/ask/docs` returns **503**
`llm_not_configured` when it would call the LLM. Provider errors (timeouts,
5xx, all keys rate-limited) return **503** `llm_unavailable`. A second key in
`GROQ_API_KEY_FALLBACK` is tried only when the first one hits a 429 / quota.

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
| POST | `/ask/docs` | hybrid RAG answer + citations |

Cross-company resources return **404** (not 403); that includes
`/ask/docs` with another company's `document_ids`.

Roles: `admin` can do everything above; `member` is read-only (writes to
`/company`, `/company/widget-settings`, `/widget-keys` and `/documents`
return **403**). Members may still ask questions via `/ask/docs`.

`allowed_origins` entries must be bare origins (`https://shop.example`,
`http://localhost:3000`): no wildcards, paths, query, fragment or userinfo.
They are stored as canonical `scheme://host[:port]` (host lowercased, default
port dropped). An empty list allows **no** origin.
