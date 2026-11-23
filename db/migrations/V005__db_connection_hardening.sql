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
