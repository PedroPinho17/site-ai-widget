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