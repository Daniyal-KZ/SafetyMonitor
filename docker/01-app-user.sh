#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$SAFETY_MONITOR_DB_PASSWORD" <<'SQL'
CREATE ROLE safety_monitor LOGIN PASSWORD :'app_password';
GRANT CONNECT ON DATABASE safety_monitor TO safety_monitor;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA safety_monitor AUTHORIZATION safety_monitor;
ALTER ROLE safety_monitor SET search_path TO safety_monitor, public;
SQL
