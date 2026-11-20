#!/usr/bin/env bash
set -euo pipefail
umask 077

: "${TARGET_DATABASE_URL:?Set TARGET_DATABASE_URL to a separate, empty PostgreSQL database}"
: "${BACKUP_FILE:?Set BACKUP_FILE to an existing custom-format pg_dump archive}"

if [[ ! -f "$BACKUP_FILE" || ! -f "$BACKUP_FILE.sha256" ]]; then
  echo "Backup archive or SHA256 manifest is missing." >&2
  exit 1
fi

backup_dir="$(cd "$(dirname "$BACKUP_FILE")" && pwd)"
backup_name="$(basename "$BACKUP_FILE")"
(
  cd "$backup_dir"
  sha256sum --check "$(basename "$BACKUP_FILE").sha256"
)

existing_tables="$(psql "$TARGET_DATABASE_URL" -X -v ON_ERROR_STOP=1 -Atqc \
  "SELECT COUNT(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')")"
if [[ "$existing_tables" != "0" ]]; then
  echo "Restore target is not empty; refusing to overwrite existing tables." >&2
  exit 1
fi

pg_restore --exit-on-error --no-owner --no-privileges \
  --dbname="$TARGET_DATABASE_URL" "$BACKUP_FILE"
psql "$TARGET_DATABASE_URL" -X -v ON_ERROR_STOP=1 -c "ANALYZE"
echo "Restore completed into the separate target database."
