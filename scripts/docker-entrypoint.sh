#!/bin/sh
set -eu
umask 027

require_database_role() {
  if [ "${FIRE_INSPECT_DATABASE_ROLE:-}" != "$1" ]; then
    echo "database role does not match the requested service" >&2
    exit 2
  fi
}

case "${1:-web}" in
  web)
    require_database_role fire_inspect_runtime
    python manage.py production_preflight
    python manage.py migrate --check
    python manage.py check --deploy
    exec gunicorn fire_inspect.config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${FIRE_INSPECT_WEB_WORKERS:-3}" \
      --timeout "${FIRE_INSPECT_WEB_TIMEOUT:-600}" \
      --graceful-timeout "${FIRE_INSPECT_WEB_GRACEFUL_TIMEOUT:-30}" \
      --access-logfile - \
      --access-logformat 'method=%(m)s path=%(U)s status=%(s)s duration_us=%(D)s' \
      --error-logfile -
    ;;
  worker)
    require_database_role fire_inspect_runtime
    python manage.py production_preflight
    python manage.py migrate --check
    python manage.py check --deploy
    exec python manage.py runworker --concurrency "${FIRE_INSPECT_WORKER_CONCURRENCY:-2}"
    ;;
  migrate)
    require_database_role fire_inspect_migrator
    python manage.py production_preflight
    python manage.py migrate --noinput
    python manage.py configure_database_privileges
    python manage.py migrate --check
    ;;
  backup)
    require_database_role fire_inspect_runtime
    exec /usr/local/bin/fire-inspect-backup
    ;;
  restore)
    require_database_role fire_inspect_migrator
    exec /usr/local/bin/fire-inspect-restore
    ;;
  *)
    exec "$@"
    ;;
esac
