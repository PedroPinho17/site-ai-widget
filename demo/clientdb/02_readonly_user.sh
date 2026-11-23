#!/bin/sh
# Cria o utilizador só de leitura que o widget vai usar.
set -e
: "${ATLAS_READONLY_PASSWORD:?Set ATLAS_READONLY_PASSWORD in .env}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'EOSQL'
-- A password vem do ambiente e não da linha de comandos (não aparece na lista de processos).
\getenv ro_pw ATLAS_READONLY_PASSWORD
CREATE ROLE atlas_readonly LOGIN PASSWORD :'ro_pw' CONNECTION LIMIT 5;

-- Limites de sessão: sempre só leitura, queries curtas, sem transações paradas.
ALTER ROLE atlas_readonly SET default_transaction_read_only = on;
ALTER ROLE atlas_readonly SET statement_timeout = '5s';
ALTER ROLE atlas_readonly SET idle_in_transaction_session_timeout = '10s';

-- Por defeito toda a gente (PUBLIC) pode criar tabelas temporárias. Tiramos isso.
REVOKE TEMPORARY ON DATABASE atlas FROM PUBLIC;

GRANT CONNECT ON DATABASE atlas TO atlas_readonly;
GRANT USAGE ON SCHEMA public TO atlas_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO atlas_readonly;

-- Tabelas que o atlas_owner criar no futuro também ficam só de leitura.
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO atlas_readonly;
EOSQL
