#!/bin/sh

set -eu

if [ "$#" -ne 1 ]; then
  printf '%s\n' "Usage: $0 <dev|prod>" >&2
  exit 2
fi

mode=$1
case "$mode" in
  dev|development|prod|production) ;;
  *)
    printf '%s\n' "Unknown Docker mode: $mode" >&2
    exit 2
    ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
compose_script="$script_dir/compose.sh"
backup_dir=${BACKUP_DIR:-backups}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_path="${backup_dir}/botera_master_${timestamp}.dump"
partial_path="${backup_path}.partial.$$"

cleanup_partial() {
  rm -f "$partial_path"
}

trap cleanup_partial 0 HUP INT TERM
umask 077

mkdir -p "$backup_dir"

"$compose_script" "$mode" exec -T postgres sh -c \
  'pg_dump --format=custom --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
  >"$partial_path"

if [ ! -s "$partial_path" ]; then
  printf '%s\n' "Backup is empty: $partial_path" >&2
  exit 1
fi

chmod 600 "$partial_path"
"$compose_script" "$mode" exec -T postgres sh -c \
  'pg_restore --list' <"$partial_path" >/dev/null
mv "$partial_path" "$backup_path"
trap - 0 HUP INT TERM

printf '%s\n' "PostgreSQL backup created and verified: $backup_path"
