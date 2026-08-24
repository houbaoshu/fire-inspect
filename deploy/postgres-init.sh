#!/bin/sh
set -eu

: "${POSTGRES_DB:?set POSTGRES_DB}"
: "${POSTGRES_USER:?set POSTGRES_USER}"
: "${FIRE_INSPECT_DB_MIGRATION_PASSWORD:?set FIRE_INSPECT_DB_MIGRATION_PASSWORD}"
: "${FIRE_INSPECT_DB_RUNTIME_PASSWORD:?set FIRE_INSPECT_DB_RUNTIME_PASSWORD}"

# Values are passed as psql variables and then quoted with :'name'. Do not
# interpolate passwords into SQL: production passwords may contain URL and SQL
# punctuation, whitespace, or other shell-significant characters.
psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=database_name="$POSTGRES_DB" \
  --set=migration_password="$FIRE_INSPECT_DB_MIGRATION_PASSWORD" \
  --set=runtime_password="$FIRE_INSPECT_DB_RUNTIME_PASSWORD" <<'SQL'
CREATE ROLE fire_inspect_migrator
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'migration_password';
CREATE ROLE fire_inspect_runtime
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'runtime_password';

ALTER DATABASE :"database_name" OWNER TO fire_inspect_migrator;
ALTER SCHEMA public OWNER TO fire_inspect_migrator;

REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO fire_inspect_runtime;
GRANT USAGE ON SCHEMA public TO fire_inspect_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE fire_inspect_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO fire_inspect_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE fire_inspect_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO fire_inspect_runtime;
SQL
