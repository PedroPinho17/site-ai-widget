-- Reference schema for site-ai-widget (Phases 1–2).
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

CREATE TABLE widget_settings (
    company_id     uuid PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    primary_color  text NOT NULL DEFAULT '#2563eb',
    greeting       text NOT NULL DEFAULT 'Olá! Como posso ajudar?',
    locale         text NOT NULL DEFAULT 'pt',
    position       text NOT NULL DEFAULT 'bottom-right'
                   CHECK (position IN ('bottom-right', 'bottom-left')),
    assistant_name text NOT NULL DEFAULT 'Assistente'
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
