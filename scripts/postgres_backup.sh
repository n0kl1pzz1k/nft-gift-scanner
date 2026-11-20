#!/usr/bin/env bash
set -euo pipefail
umask 077

: "${DATABASE_URL:?Set DATABASE_URL to the PostgreSQL connection string}"
: "${BACKUP_DIR:?Set BACKUP_DIR to a private backup directory}"

mkdir -p "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="$BACKUP_DIR/hassle-postgres-$timestamp.dump"
temporary="$final.tmp"
manifest="$final.sha256"

if [[ -e "$final" || -e "$manifest" || -e "$temporary" ]]; then
  echo "Backup destination already exists; refusing to overwrite." >&2
  exit 1
fi

cleanup() { rm -f "$temporary"; }
trap cleanup EXIT

pg_dump --format=custom --no-owner --no-privileges --file "$temporary" "$DATABASE_URL"
pg_restore --list "$temporary" >/dev/null
sha256sum "$temporary" | sed "s#  .*#  $(basename "$final")#" > "$manifest.tmp"
ln "$temporary" "$final"
mv "$manifest.tmp" "$manifest"
rm -f "$temporary"
trap - EXIT

printf 'PostgreSQL backup created: %s\n' "$(basename "$final")"
printf 'SHA256 manifest: %s\n' "$(basename "$manifest")"
