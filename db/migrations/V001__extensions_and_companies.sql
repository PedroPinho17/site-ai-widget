-- Phase 1 core tables: companies + users
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

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
