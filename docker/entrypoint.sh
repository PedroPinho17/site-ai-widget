#!/bin/sh
set -eu

echo "Waiting for Postgres..."
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${POSTGRES_USER:-sitewidget}" >/dev/null 2>&1; do
  sleep 1
done

echo "Applying migrations..."
python -m site_ai_widget.db.migrate

if [ "${RUN_SEED:-0}" = "1" ]; then
  echo "Seeding demo data..."
  python -m site_ai_widget.seed || true
fi

# Listen on all interfaces inside the container; compose publishes only 127.0.0.1 on the host.
# --no-server-header: responses do not advertise the server software.
exec uvicorn site_ai_widget.api.main:app --host 0.0.0.0 --port 8002 --no-server-header
