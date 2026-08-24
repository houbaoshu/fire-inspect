#!/bin/sh
set -eu
umask 077

: "${FIRE_INSPECT_DATA_ROOT:?set FIRE_INSPECT_DATA_ROOT}"
: "${FIRE_INSPECT_BACKUP_DIR:?set FIRE_INSPECT_BACKUP_DIR}"

if [ -z "${FIRE_INSPECT_DATABASE_URL:-}" ]; then
  : "${PGHOST:?set FIRE_INSPECT_DATABASE_URL or PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD}"
  : "${PGPORT:?set PGPORT}"
  : "${PGDATABASE:?set PGDATABASE}"
  : "${PGUSER:?set PGUSER}"
  : "${PGPASSWORD:?set PGPASSWORD}"
fi

maintenance_file=${FIRE_INSPECT_MAINTENANCE_FILE:-"$FIRE_INSPECT_DATA_ROOT/.maintenance"}

case "$FIRE_INSPECT_DATA_ROOT" in
  /|"") echo "unsafe data root" >&2; exit 2 ;;
esac
case "$FIRE_INSPECT_BACKUP_DIR" in
  /|"") echo "unsafe backup root" >&2; exit 2 ;;
esac

mkdir -p "$FIRE_INSPECT_DATA_ROOT" "$FIRE_INSPECT_BACKUP_DIR"
python manage.py postgres_version_gate
if [ -e "$maintenance_file" ]; then
  echo "maintenance marker already exists; refusing to change another maintenance window" >&2
  exit 2
fi
: > "$maintenance_file"
cleanup_maintenance() {
  rm -f "$maintenance_file"
}
trap cleanup_maintenance EXIT HUP INT TERM

python manage.py wait_for_worker_idle --timeout "${FIRE_INSPECT_BACKUP_WAIT_SECONDS:-300}"

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
final_target="$FIRE_INSPECT_BACKUP_DIR/$timestamp"
target="$FIRE_INSPECT_BACKUP_DIR/.incomplete-$timestamp-$$"
if [ -e "$final_target" ] || [ -e "$target" ]; then
  echo "backup target already exists" >&2
  exit 2
fi
mkdir "$target"

if [ -n "${FIRE_INSPECT_DATABASE_URL:-}" ]; then
  pg_dump \
    --dbname "$FIRE_INSPECT_DATABASE_URL" \
    --format=custom \
    --no-owner \
    --no-privileges \
    --exclude-table-data=django_session \
    --file "$target/database.dump"
else
  pg_dump \
    --format=custom \
    --no-owner \
    --no-privileges \
    --exclude-table-data=django_session \
    --file "$target/database.dump"
fi

python manage.py backup_manifest create \
  --data-root "$FIRE_INSPECT_DATA_ROOT" \
  --output "$target/manifest.json"

set --
for directory in originals derived knowledge templates documents render-previews; do
  if [ -e "$FIRE_INSPECT_DATA_ROOT/$directory" ]; then
    set -- "$@" "$directory"
  fi
done
# macOS bsdtar otherwise inserts AppleDouble ``._*`` metadata entries. They
# are neither application data nor present in the per-file manifest, so keep
# the archive deterministic and portable by disabling that platform feature.
export COPYFILE_DISABLE=1
if [ "$#" -gt 0 ]; then
  tar \
    --exclude='.env' \
    --exclude='.env.*' \
    -czf "$target/files.tar.gz" \
    -C "$FIRE_INSPECT_DATA_ROOT" \
    "$@"
else
  tar -czf "$target/files.tar.gz" --files-from /dev/null
fi

(
  cd "$target"
  sha256sum database.dump files.tar.gz manifest.json > SHA256SUMS
)
python manage.py verify_backup_bundle --source "$target"
mv "$target" "$final_target"
echo "$final_target"
