-- Reference schema for site-ai-widget (Phase 1).
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
