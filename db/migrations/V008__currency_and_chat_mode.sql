-- Per-company display currency (optional) and a chat mode for greetings.
ALTER TABLE widget_settings
    ADD COLUMN currency text
    CHECK (currency IS NULL OR currency IN ('EUR', 'USD', 'GBP', 'BRL'));

ALTER TABLE visitor_messages DROP CONSTRAINT IF EXISTS visitor_messages_mode_check;
ALTER TABLE visitor_messages
    ADD CONSTRAINT visitor_messages_mode_check
    CHECK (mode IS NULL OR mode IN ('sql', 'docs', 'none', 'chat'));
