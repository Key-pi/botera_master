#!/bin/sh

set -eu

if [ "$#" -ne 1 ]; then
  printf '%s\n' "Usage: $0 <backup.dump>" >&2
  exit 2
fi

backup_path=$1

if [ ! -s "$backup_path" ]; then
  printf '%s\n' "Backup does not exist or is empty: $backup_path" >&2
  exit 1
fi

docker compose exec -T postgres sh -c \
  'pg_restore --list' <"$backup_path" >/dev/null

printf '%s\n' "PostgreSQL backup archive is readable: $backup_path"
