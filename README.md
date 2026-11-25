# site-ai-widget

[Português (PT)](README.pt.md)

![Demo: a visitor opens the widget on the fictional Mercearia Atlas site, asks the price of olive oil (answered from the database), asks about the returns policy (answered from the PDF with a page citation) and opens the cited PDF page](docs/media/demo.gif)

▶ [Watch the full demo video (widget + admin panel, MP4)](docs/media/demo.mp4)

Embeddable AI assistant for a company website. Visitors ask in a chat widget; answers come from **uploaded PDFs** (with page citations) and from the **company’s own database** through guarded text-to-SQL. Company staff use an **admin panel** (documents, schema approval, widget look, conversations, dashboard).

Demo data is fictional (Mercearia Atlas, Oficina Lima). There is no licence file.

**Author:** [Pedro Pinho](https://github.com/PedroPinho17) · [LinkedIn](https://www.linkedin.com/in/pedropinho19/)

## Screenshots

All screenshots come from the local demo with fictional data.

| | |
|---|---|
| <img src="docs/media/widget-price.png" width="420" alt="Chat widget on the demo site answering “Quanto custa o azeite?” with the olive oil price from the database"> | <img src="docs/media/widget-doc.png" width="420" alt="Chat widget answering the returns-policy question from the PDF manual, with a citation chip for page 3"> |
| **Database answer:** price question → safe text-to-SQL | **Document answer:** PDF facts with a page citation |
| <img src="docs/media/widget-fullscreen.png" width="420" alt="Widget in full-screen mode showing the whole conversation"> | <img src="docs/media/panel-dashboard.png" width="420" alt="Admin panel dashboard with document, table and key counts, questions per day and answer sources"> |
| **Full-screen widget** | **Panel dashboard:** usage and answer sources |
| <img src="docs/media/panel-documents.png" width="420" alt="Documents page with the uploaded PDF manual and an inline preview"> | <img src="docs/media/panel-widget.png" width="420" alt="Widget settings with colour picker, contrast badge, presets and a live preview of the widget"> |
| **Documents:** upload and inline preview | **Widget settings:** colour picker and live preview |
| <img src="docs/media/panel-test.png" width="420" alt="Test assistant page answering the returns-policy question with its document citation and response time"> | <img src="docs/media/panel-conversations.png" width="420" alt="Conversations page listing visitor chats with language, message count and answer source"> |
| **Test assistant:** try questions before going live | **Conversations:** visitor chats by date and source |

## Stack

- **API:** Python 3.13, FastAPI, uv  
- **Store:** PostgreSQL 17 + pgvector (app DB)  
- **LLM:** Groq (OpenAI-compatible; default model `openai/gpt-oss-120b`)  
- **Embeddings:** local `intfloat/multilingual-e5-small` (Hugging Face, CPU)  
- **SQL:** sqlglot validator, read-only client session  
- **Panel:** Vite, React, TypeScript  
- **Widget:** `embed.js` (closed Shadow DOM)

The Groq **free tier** and the local e5 model are enough to run the demo. No paid cloud is required beyond that optional LLM.

## Architecture

```mermaid
flowchart TB
  visitor[Visitor browser]
  panel[Admin panel]
  embed["GET /embed.js"]
  ask["answer_question"]
  sql["Text-to-SQL on approved schema"]
  docs["Hybrid PDF search + LLM"]
  none["Unknown answer, no sources"]
  clientdb[(Company Postgres)]
  appdb[(App Postgres + pgvector)]

  visitor --> embed
  embed -->|"POST /v1/widget/{key}/ask"| ask
  panel -->|"JWT panel API"| ask
  ask --> sql
  sql -->|rows| visitor
  sql -->|no usable rows| docs
  docs -->|grounded answer| visitor
  docs -->|no match / don't know| none
  sql --> clientdb
  docs --> appdb
```

## Features

- **Widget:** floating chat, optional full screen, PT/EN copy, colour + contrast, clear conversation, suggestion chips  
- **Documents:** PDF upload, hybrid search, page citations; optional short-lived signed PDF links for visitors  
- **Database:** save a read-only DSN, introspect, approve tables/columns, text-to-SQL  
- **Panel pages:** Dashboard, Documents, Database, Conversations, Test assistant, Widget, Keys & Origins  
- **i18n:** panel and widget in European Portuguese and English  
- **Suggestions:** cached questions from documents (admins can type their own)  
- **Retention:** visitor sessions deleted after the company’s window (default 30 days, 1–365)

## Quick start

Needs Docker, Node.js (for the panel), and Python 3.13 if you seed or test on the host.

1. Clone the repo and copy env templates. **Do not commit `.env`.** Fill your own values (never paste real production secrets into git):

   ```bash
   cp .env.example .env
   cp panel/.env.example panel/.env
   ```

   In `.env` set at least:

   - `JWT_SECRET` — at least 32 random characters (`uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`)  
   - `POSTGRES_PASSWORD` and `DATABASE_URL` — same password in both; host port **5435**  
   - `DEMO_PASSWORD_ATLAS` and `DEMO_PASSWORD_LIMA` — demo panel logins (required to seed; **not stored in the repo**)  
   - `CLIENTDB_PASSWORD` and `ATLAS_READONLY_PASSWORD` — demo client Postgres  
   - `CONNECTIONS_KEY` — Fernet key (`uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)  
   - `GROQ_API_KEY` — optional second key `GROQ_API_KEY_FALLBACK` for HTTP 429  
   - `CLIENTDB_ALLOWED_HOSTS` — locally `127.0.0.1:5436`; Compose already sets `clientdb:5432` for the **app** service  
   - `RUN_SEED=1` if you want Compose to seed Mercearia Atlas / Oficina Lima on start  

   `panel/.env` only needs `VITE_API_URL` (default `http://127.0.0.1:8002`).

2. Start the stack (app DB, demo client DB, API). Compose publishes the API on `127.0.0.1:8002` and Postgres on `127.0.0.1:5435` / `127.0.0.1:5436`:

   ```bash
   docker compose up -d --build
   ```

   Migrations run in the app entrypoint. With `RUN_SEED=1`, demo companies and PDFs are seeded there. You can also seed from the host: `uv run site-ai-widget-seed`.

3. Admin panel:

   ```bash
   cd panel
   npm install
   npm run dev
   ```

   Open `http://127.0.0.1:5173`. Demo emails (passwords are only what you put in `.env`):

   - `admin@mercearia-atlas.example`  
   - `admin@oficina-lima.example`

4. Demo shop page:

   ```bash
   cd examples
   python -m http.server 5500
   ```

   Open [http://127.0.0.1:5500/demo-site.html](http://127.0.0.1:5500/demo-site.html). Seeding Atlas allowlists `http://127.0.0.1:5500` on the public embed key.

`GET http://127.0.0.1:8002/health` checks the API.

`POSTGRES_PASSWORD` is applied only when the `pgdata` volume is first created. Changing it later in `.env` does not update Postgres; recreate volumes (`docker compose down -v`) only if you accept losing local data.

## Widget embed

Public demo key (embed id, not a password): `wk_demo_mercearia_atlas`.

```html
<script
  src="http://127.0.0.1:8002/embed.js"
  data-key="wk_demo_mercearia_atlas"
  data-lang="pt"
  async
></script>
```

The script is `GET /embed.js`. Origin must match the key’s allowlist exactly.

## Security

- **Tenancy:** `company_id` comes from the JWT or the widget key, never from the question body. Other companies’ rows return **404**.  
- **SQL runtime:** `SET TRANSACTION READ ONLY`, `statement_timeout` 5 s, lock timeout, named cursor, result cap **500** rows / 1 MiB.  
- **Validator:** sqlglot — single read query, approved tables/columns only, comments stripped, hard `LIMIT` (default 100, max 500). Writes, DDL and unknown functions are refused.  
- **Approval:** a column is usable only if **table and column** are `approved`. Sensitive names (e.g. password, NIF, email) cannot be approved.  
- **DSN:** stored with Fernet (`CONNECTIONS_KEY`); the password is never returned by the API.  
- **Origins:** widget CORS only on `/v1/widget/*`; allowlist is canonical `scheme://host[:port]`.  
- **Rate limits:** per-IP auth, ask (weight **3** per `/ask`), widget key, widget lookup, assistant test.  
- **Widget:** closed Shadow DOM, no `innerHTML` of model text, constructable stylesheets.  
- **PDFs:** visitor links are HMAC (`/v1/signed-documents/{id}`), default TTL 300 s, max 900 s, and only if `allow_visitor_documents` is on.  
- **Sessions:** raw visitor token returned once; only SHA-256 is stored.

## Evaluation

The evaluation set is `eval/questions.json` (40 fictional Atlas items). Run it **inside the app container** so the stored client DSN host `clientdb` resolves. Compose bind-mounts `./eval` and `./docs` into `/app`, so questions are read from the host and `eval/results/`, `eval/history.csv`, and `docs/evaluation.md` are written back to the host (not into the image). Start all services first (`docker compose up -d --build`; `up -d app` alone does not start `clientdb`).

```bash
docker compose exec app python -m site_ai_widget.evaluate --delay 0.5 --batch-size 5
```

If the client DB is unreachable, the runner stops with `client DB unreachable` and does not write `docs/evaluation.md` or `eval/history.csv`.

**Latest full run (25 Nov 2026, Groq free tier): 29/40 correct (72.5%)**

| Type | Correct | What is checked |
|---|---|---|
| SQL | 15/20 | Same rows as the reference query (order, column names and up to 2 identifying extra columns ignored; numeric tolerance 0.01) |
| Documents | 11/15 | Answer contains the reference fact and cites the right page when it cites |
| Refusals | 3/5 | No SQL executed and an unknown/refusal answer |

The per-run report (`docs/evaluation.md`) and raw results (`eval/results/`) are generated locally and are not committed.

## Docs

- [Architecture](docs/ARCHITECTURE.md)  
- [Development](docs/DEVELOPMENT.md)  
- [README in Portuguese](README.pt.md)
