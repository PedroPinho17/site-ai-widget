-- Panel phase 6c: widget appearance, suggestions, visitor document links,
-- conversation retention, user panel locale, message timing.
-- Additive only (IF NOT EXISTS, defaults). Manual rollback, if ever needed:
--   DROP INDEX IF EXISTS visitor_sessions_company_seen_idx, visitor_messages_company_created_idx;
--   ALTER TABLE visitor_messages DROP COLUMN IF EXISTS unanswered, DROP COLUMN IF EXISTS elapsed_ms;
--   ALTER TABLE visitor_sessions DROP COLUMN IF EXISTS lang;
--   ALTER TABLE users DROP CONSTRAINT IF EXISTS users_panel_locale_chk, DROP COLUMN IF EXISTS panel_locale;
--   ALTER TABLE widget_settings DROP CONSTRAINT IF EXISTS widget_settings_locale_check,
--       DROP CONSTRAINT IF EXISTS widget_settings_retention_chk,
--       DROP COLUMN IF EXISTS show_suggestions, DROP COLUMN IF EXISTS show_fullscreen,
--       DROP COLUMN IF EXISTS allow_visitor_documents, DROP COLUMN IF EXISTS conversation_retention_days,
--       DROP COLUMN IF EXISTS suggested_questions;

ALTER TABLE widget_settings
    ADD COLUMN IF NOT EXISTS show_suggestions boolean NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS show_fullscreen boolean NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS allow_visitor_documents boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS conversation_retention_days integer NOT NULL DEFAULT 30,
    ADD COLUMN IF NOT EXISTS suggested_questions text[] NOT NULL DEFAULT '{}';

ALTER TABLE widget_settings
    DROP CONSTRAINT IF EXISTS widget_settings_retention_chk;
ALTER TABLE widget_settings
    ADD CONSTRAINT widget_settings_retention_chk
    CHECK (conversation_retention_days BETWEEN 1 AND 365);

-- Normalise legacy free-text locales before the CHECK: 'en-GB' / 'EN_us' stay
-- English, 'pt-PT' / 'pt_BR' stay Portuguese; only unknown values fall back to pt.
UPDATE widget_settings
SET locale = CASE
    WHEN lower(btrim(locale)) IN ('pt', 'en', 'auto') THEN lower(btrim(locale))
    WHEN lower(btrim(locale)) LIKE 'en%' THEN 'en'
    ELSE 'pt'
END
WHERE locale IS DISTINCT FROM lower(btrim(locale))
   OR lower(btrim(locale)) NOT IN ('pt', 'en', 'auto');

ALTER TABLE widget_settings
    DROP CONSTRAINT IF EXISTS widget_settings_locale_check;
ALTER TABLE widget_settings
    ADD CONSTRAINT widget_settings_locale_check
    CHECK (locale IN ('pt', 'en', 'auto'));

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS panel_locale text NOT NULL DEFAULT 'pt';

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS users_panel_locale_chk;
ALTER TABLE users
    ADD CONSTRAINT users_panel_locale_chk
    CHECK (panel_locale IN ('pt', 'en'));

ALTER TABLE visitor_sessions
    ADD COLUMN IF NOT EXISTS lang text;

ALTER TABLE visitor_messages
    ADD COLUMN IF NOT EXISTS unanswered boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS elapsed_ms integer;

CREATE INDEX IF NOT EXISTS visitor_messages_company_created_idx
    ON visitor_messages (company_id, created_at);

CREATE INDEX IF NOT EXISTS visitor_sessions_company_seen_idx
    ON visitor_sessions (company_id, last_seen);
