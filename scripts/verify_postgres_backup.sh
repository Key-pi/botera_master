#!/bin/sh

set -eu

if [ "$#" -ne 2 ]; then
  printf '%s\n' "Usage: $0 <dev|prod> <backup.dump>" >&2
  exit 2
fi

mode=$1
backup_path=$2
case "$mode" in
  dev|development|prod|production) ;;
  *)
    printf '%s\n' "Unknown Docker mode: $mode" >&2
    exit 2
    ;;
esac

if [ ! -s "$backup_path" ]; then
  printf '%s\n' "Backup does not exist or is empty: $backup_path" >&2
  exit 1
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"$script_dir/compose.sh" "$mode" exec -T postgres sh -c \
  'pg_restore --list' <"$backup_path" >/dev/null

printf '%s\n' "PostgreSQL backup archive is readable: $backup_path"
