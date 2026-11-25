# Development

Commands below exist in this repository. Do not commit `.env`, dumps, or eval live output.

## Tests

From the repo root:

```bash
uv run ruff check .
uv run pytest -q
```

`tests/unit` does not need a live LLM. Full `pytest` uses Docker Postgres for DB-marked tests when `db` is up (fake embedder / mocked LLM unless `RUN_MODEL_TESTS=1`).

Optional real embeddings:

```bash
RUN_MODEL_TESTS=1 uv run pytest tests -m model -q
```

Panel (Vitest):

```bash
cd panel
npm test
npm run typecheck
npm run lint
```

Production panel bundle: `npm run build`.

## Migrations

SQL lives in `db/migrations/`. Apply:

```bash
uv run site-ai-widget-migrate
```

or `uv run python -m site_ai_widget.db.migrate`. Needs `DATABASE_URL` (see `.env.example`). The Compose **app** entrypoint (`docker/entrypoint.sh`) runs the same migrate step before uvicorn.

## Rebuild the API image

```bash
docker compose up -d --build app
```

That rebuilds the `app` service only (`Dockerfile`, listens on container port 8002, published as `127.0.0.1:${APP_PORT:-8002}`). Full stack including Postgres:

```bash
docker compose up -d --build
```

## Recreate the demo client database volume

Tables and `atlas_readonly` are created **only** when volume `clientdata` is first initialized (`demo/clientdb/` mounted as `/docker-entrypoint-initdb.d`).

This removes **only** the demo client data (not the app `pgdata` volume):

```bash
docker compose rm -sf clientdb
docker volume rm site-ai-widget_clientdata
docker compose up -d clientdb
```

The Compose project name defaults to the directory name `site-ai-widget`, so the volume is `site-ai-widget_clientdata`. If `docker volume ls` shows another prefix, use that name.

Set `CLIENTDB_PASSWORD` and `ATLAS_READONLY_PASSWORD` in `.env` before the first init. Changing them later does not update an existing volume.

## Seed

```bash
uv run site-ai-widget-seed
```

Requires `DEMO_PASSWORD_ATLAS` and `DEMO_PASSWORD_LIMA`. With Compose, `RUN_SEED=1` runs seed in the app container after migrate (`docker/entrypoint.sh`). Demo PDFs: `demo/mercearia-atlas-manual.pdf`, `demo/oficina-lima-manual.pdf` (regenerate with `uv run python scripts/make_demo_pdfs.py`).

## Evaluation (optional)

Do not run this in CI against Groq unless you intend to consume quota.

The saved client DSN uses the Compose hostname `clientdb` (port 5432 **inside** the network). That name does not resolve on the Windows host; the published port is `127.0.0.1:5436`. Eval therefore runs in the **app** container (same network as the API), not `uv run` on the host.

`docker-compose.yml` bind-mounts `./eval` → `/app/eval` and `./docs` → `/app/docs`, and sets `EVAL_ROOT=/app`. The image does not contain `eval/` or `docs/` (see `.dockerignore`). After changing those mounts, recreate the service so they attach:

```bash
docker compose up -d --build
docker compose exec app python -m site_ai_widget.evaluate --delay 0.5 --batch-size 5
```

Bring up **all** services: `docker compose up -d app` alone does not start `clientdb` (the app does not depend on it), and the image must be rebuilt after code changes because `src/` is copied into it.

SQL items use execution accuracy: same rows in any order, numeric tolerance 0.01, column names ignored. The generated query may add up to 2 identifying columns (e.g. `name, price` when only the price is expected); the row count must match. Each SQL record in `eval/results/*.json` keeps the first 10 returned rows to debug mismatches.

Flags: `--resume`, `--limit`, `--only doc|sql|refusal`. Writes host files `eval/results/<timestamp>.json`, `eval/history.csv`, and (full run only) `docs/evaluation.md`. If 3 SQL items in a row (or every SQL item of a shorter run) cannot reach the client DB, the process exits with `client DB unreachable` (exit code 2) and does **not** write those score files. Answers already collected stay in the checkpoint and the unreachable items are left pending, so after fixing the connection `--resume` continues from there. A single isolated failure is scored on that item and the run goes on. A run finished with `--resume` that covers every question counts as a full run and writes `docs/evaluation.md`. Outputs are gitignored except `eval/questions.json`.

## Troubleshooting

### Eval `client DB unreachable` / SQL `reason=db_error` in ~3s

The eval cannot open `clientdb:5432`. Run it with `docker compose exec app …` as above (the name does not resolve on the host). If it already runs in the container, check `docker compose ps`: when `clientdb` is stopped its name does not resolve either — `docker compose up -d clientdb`. Logs may show `error_class=ApiError api_code=db_host_unresolvable` or `error_class=OperationalError sqlstate_class=08` — never the DSN.

### Groq quota / HTTP 429

Set `GROQ_API_KEY_FALLBACK`. The client cools a key down (at least 300 s) and tries the next. If every key is cooling, unified `/ask` returns **200** with a short busy sentence (no provider names). Wait a minute and retry. Widget copy: “Estou com muitos pedidos…” / English equivalent.

### Stale API image

Python lives in the image. After pulling or editing `src/`, rebuild:

```bash
docker compose up -d --build app
```

`up -d` without `--build` keeps the old layers. Embed script is served from the running app (`GET /embed.js`); a stale container serves a stale widget.

### App cannot reach Postgres after changing `POSTGRES_PASSWORD`

The password is baked into `pgdata` on first boot. Either `ALTER ROLE` inside Postgres or `docker compose down -v` (deletes companies, users, documents).

### Connection test refuses the client user

The probe rejects any role that can write. `atlas_readonly` is created for SELECT only. On PostgreSQL 13/14, `REVOKE CREATE ON SCHEMA public FROM PUBLIC` may be required on a real client DB (the demo image is 17).

### Panel cannot call the API

`PANEL_ORIGINS` must include the Vite origin (`http://127.0.0.1:5173` is the default). `VITE_API_URL` must match the published API (`http://127.0.0.1:8002`). JWT is in memory + `sessionStorage` only.

### Widget 403 `origin_not_allowed`

Origin must equal an allowlisted entry byte-for-byte after canonicalisation. The demo page origin is `http://127.0.0.1:5500`, not `localhost`.
