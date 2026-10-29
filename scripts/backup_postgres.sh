#!/bin/sh

set -eu

backup_dir=${BACKUP_DIR:-backups}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_path="${backup_dir}/botera_master_${timestamp}.dump"

mkdir -p "$backup_dir"

docker compose exec -T postgres sh -c \
  'pg_dump --format=custom --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
  >"$backup_path"

if [ ! -s "$backup_path" ]; then
  printf '%s\n' "Backup is empty: $backup_path" >&2
  exit 1
fi

docker compose exec -T postgres sh -c \
  'pg_restore --list' <"$backup_path" >/dev/null

printf '%s\n' "PostgreSQL backup created and verified: $backup_path"
