-- Public widget visitor sessions. The raw token is never stored, only its SHA-256 hex.
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
    mode        text CHECK (mode IS NULL OR mode IN ('sql', 'docs', 'none')),
    row_count   integer CHECK (row_count IS NULL OR row_count >= 0),
    created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (session_id, company_id)
        REFERENCES visitor_sessions (id, company_id) ON DELETE CASCADE
);

CREATE INDEX visitor_messages_session_seq_idx ON visitor_messages (session_id, seq);
