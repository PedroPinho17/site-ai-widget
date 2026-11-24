-- Suggested questions typed by an admin are kept: document upload/delete only
-- regenerates the cache while this flag is false. Additive, safe default.
-- Rollback: ALTER TABLE widget_settings DROP COLUMN IF EXISTS suggestions_manual;

ALTER TABLE widget_settings
    ADD COLUMN IF NOT EXISTS suggestions_manual boolean NOT NULL DEFAULT false;
