#!/bin/sh
set -eu
umask 077

: "${FIRE_INSPECT_DATA_ROOT:?set FIRE_INSPECT_DATA_ROOT}"
: "${FIRE_INSPECT_RESTORE_SOURCE:?set FIRE_INSPECT_RESTORE_SOURCE}"
: "${FIRE_INSPECT_RESTORE_ACK:?set FIRE_INSPECT_RESTORE_ACK}"

if [ "${FIRE_INSPECT_DATABASE_ROLE:-}" != "fire_inspect_migrator" ]; then
  echo "restore requires the migration database role" >&2
  exit 2
fi

if [ -z "${FIRE_INSPECT_DATABASE_URL:-}" ]; then
  : "${PGHOST:?set FIRE_INSPECT_DATABASE_URL or PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD}"
  : "${PGPORT:?set PGPORT}"
  : "${PGDATABASE:?set PGDATABASE}"
  : "${PGUSER:?set PGUSER}"
  : "${PGPASSWORD:?set PGPASSWORD}"
fi

if [ "$FIRE_INSPECT_RESTORE_ACK" != "RESTORE_IN_EMPTY_TEST_ENVIRONMENT" ]; then
  echo "restore acknowledgement is missing" >&2
  exit 2
fi
case "$FIRE_INSPECT_DATA_ROOT" in
  /|"") echo "unsafe data root" >&2; exit 2 ;;
esac

python manage.py postgres_version_gate
python manage.py verify_backup_bundle --source "$FIRE_INSPECT_RESTORE_SOURCE"

if [ -d "$FIRE_INSPECT_DATA_ROOT" ] && [ -n "$(find "$FIRE_INSPECT_DATA_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "restore data root must be empty" >&2
  exit 2
fi
mkdir -p "$FIRE_INSPECT_DATA_ROOT"
python manage.py assert_empty_restore_target
if [ -n "${FIRE_INSPECT_DATABASE_URL:-}" ]; then
  pg_restore \
    --dbname "$FIRE_INSPECT_DATABASE_URL" \
    --exit-on-error \
    --no-owner \
    --no-privileges \
    "$FIRE_INSPECT_RESTORE_SOURCE/database.dump"
else
  pg_restore \
    --exit-on-error \
    --no-owner \
    --no-privileges \
    "$FIRE_INSPECT_RESTORE_SOURCE/database.dump"
fi
python manage.py restore_file_archive \
  --source "$FIRE_INSPECT_RESTORE_SOURCE" \
  --data-root "$FIRE_INSPECT_DATA_ROOT"
python manage.py migrate --noinput
python manage.py configure_database_privileges
python manage.py migrate --check
python manage.py production_preflight
python manage.py check --deploy
