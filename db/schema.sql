-- Reference schema for site-ai-widget (Phases 1–5).
-- Applied via versioned migrations in db/migrations/; this file is documentation.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    description text NOT NULL,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE companies (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name           text NOT NULL,
    slug           text NOT NULL UNIQUE,
    default_locale text NOT NULL DEFAULT 'pt',
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id     uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email          text NOT NULL,
    password_hash  text NOT NULL,
    role           text NOT NULL DEFAULT 'admin'
                   CHECK (role IN ('admin', 'member')),
    token_version  integer NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (email)
);

CREATE UNIQUE INDEX users_email_lower_idx ON users (lower(email));

CREATE TABLE widget_keys (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    public_key      text NOT NULL UNIQUE,
    allowed_origins text[] NOT NULL DEFAULT '{}',
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (public_key LIKE 'wk_%')
);

CREATE INDEX widget_keys_company_idx ON widget_keys (company_id);

-- Public widget visitors. token_hash is SHA-256 hex; the raw token is not stored.
-- visitor_messages.text is the question or the public answer, never generated SQL.
CREATE TABLE visitor_sessions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    token_hash  text NOT NULL CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, token_hash),
    UNIQUE (id, company_id)
);

CREATE INDEX visitor_sessions_company_idx ON visitor_sessions (company_id);

CREATE TABLE visitor_messages (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq         bigint GENERATED ALWAYS AS IDENTITY,
    session_id  uuid NOT NULL,
    company_id  uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    role        text NOT NULL CHECK (role IN ('user', 'assistant')),
    text        text,
    mode        text CHECK (mode IS NULL OR mode IN ('sql', 'docs', 'none', 'chat')),
    row_count   integer CHECK (row_count IS NULL OR row_count >= 0),
    created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (session_id, company_id)
        REFERENCES visitor_sessions (id, company_id) ON DELETE CASCADE
);

CREATE INDEX visitor_messages_session_seq_idx ON visitor_messages (session_id, seq);

CREATE TABLE widget_settings (
    company_id     uuid PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    primary_color  text NOT NULL DEFAULT '#2563eb',
    greeting       text NOT NULL DEFAULT 'Olá! Como posso ajudar?',
    locale         text NOT NULL DEFAULT 'pt',
    position       text NOT NULL DEFAULT 'bottom-right'
                   CHECK (position IN ('bottom-right', 'bottom-left')),
    assistant_name text NOT NULL DEFAULT 'Assistente',
    currency       text
                   CHECK (currency IS NULL OR currency IN ('EUR', 'USD', 'GBP', 'BRL'))
);

-- Phase 2: documents + hybrid RAG chunks
CREATE TABLE documents (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id    uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    original_name text NOT NULL,
    storage_name  text NOT NULL,
    size_bytes    bigint NOT NULL CHECK (size_bytes >= 0),
    sha256        text NOT NULL,
    page_count    integer,
    status        text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
    error         text,
    created_by    uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, sha256),
    UNIQUE (id, company_id)
);

CREATE INDEX documents_company_idx ON documents (company_id);
CREATE INDEX documents_company_status_idx ON documents (company_id, status);

CREATE TABLE document_chunks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    document_id uuid NOT NULL,
    page        integer NOT NULL CHECK (page >= 1),
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    content     text NOT NULL,
    embedding   vector(384) NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    FOREIGN KEY (document_id, company_id) REFERENCES documents(id, company_id) ON DELETE CASCADE,
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX document_chunks_embedding_hnsw_idx
    ON document_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX document_chunks_tsv_gin_idx
    ON document_chunks USING gin (tsv);
CREATE INDEX document_chunks_company_document_idx
    ON document_chunks (company_id, document_id);

-- Phase 3: client database connection, introspected schema and approval

-- One read-only connection per company. The full DSN (with password) is
-- encrypted with Fernet; host/port/database/user are kept for display only.
CREATE TABLE db_connections (
    company_id           uuid PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    dsn_encrypted        text NOT NULL,
    host                 text NOT NULL,
    port                 integer NOT NULL CHECK (port BETWEEN 1 AND 65535),
    database_name        text NOT NULL,
    username             text NOT NULL,
    status               text NOT NULL DEFAULT 'ok'
                         CHECK (status IN ('ok', 'failed')),
    last_error           text,
    last_checked_at      timestamptz,
    last_introspected_at timestamptz,
    sslmode              text NOT NULL DEFAULT 'prefer'
                         CHECK (sslmode IN ('disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full')),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

-- Tables and views found in the client database.
CREATE TABLE schema_tables (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    schema_name  text NOT NULL,
    table_name   text NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('table', 'view')),
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'approved', 'excluded')),
    ai_suggested boolean NOT NULL DEFAULT false,
    description  text,
    is_present   boolean NOT NULL DEFAULT true,
    embedding    vector(384),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (company_id, schema_name, table_name),
    UNIQUE (id, company_id)
);

CREATE INDEX schema_tables_company_status_idx ON schema_tables (company_id, status);
CREATE INDEX schema_tables_embedding_hnsw_idx
    ON schema_tables USING hnsw (embedding vector_cosine_ops);

-- Columns of each table. A sensitive column can never be approved.
CREATE TABLE schema_columns (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   uuid NOT NULL,
    table_id     uuid NOT NULL,
    column_name  text NOT NULL,
    data_type    text NOT NULL,
    is_nullable  boolean NOT NULL,
    ordinal      integer NOT NULL CHECK (ordinal >= 1),
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'approved', 'excluded')),
    is_sensitive boolean NOT NULL DEFAULT false,
    description  text,
    is_present   boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (table_id, company_id)
        REFERENCES schema_tables(id, company_id) ON DELETE CASCADE,
    UNIQUE (table_id, column_name),
    CHECK (NOT (is_sensitive AND status = 'approved'))
);

CREATE INDEX schema_columns_company_table_idx ON schema_columns (company_id, table_id);

-- Phase 3 hardening after review (V004 is already applied, so changes go here).

-- A connection only gets a status after it is tested; the code must always set it.
ALTER TABLE db_connections ALTER COLUMN status DROP DEFAULT;

-- Something that disappeared from the client database can't stay approved.
ALTER TABLE schema_tables
    ADD CONSTRAINT schema_tables_approved_present_chk
    CHECK (is_present OR status <> 'approved');

ALTER TABLE schema_columns
    ADD CONSTRAINT schema_columns_approved_present_chk
    CHECK (is_present OR status <> 'approved');
