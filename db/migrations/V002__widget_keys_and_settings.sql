-- Widget API keys and per-company widget appearance
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
