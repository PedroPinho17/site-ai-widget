-- Non-secret TLS mode, returned to the panel so a re-save keeps it instead of
-- silently falling back to "prefer". The DSN (encrypted) stays the source used
-- to connect; rows saved before this migration show the default until re-saved.
ALTER TABLE db_connections
    ADD COLUMN sslmode text NOT NULL DEFAULT 'prefer'
    CHECK (sslmode IN ('disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'));
