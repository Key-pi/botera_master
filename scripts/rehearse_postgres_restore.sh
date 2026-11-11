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
compose_script="$script_dir/compose.sh"
rehearsal_database=botera_master_restore_check

drop_rehearsal_database() {
  "$compose_script" "$mode" exec -T postgres sh -c \
    'dropdb --if-exists --username="$POSTGRES_USER" botera_master_restore_check' \
    >/dev/null 2>&1 || true
}

trap drop_rehearsal_database 0 HUP INT TERM

"$script_dir/verify_postgres_backup.sh" "$mode" "$backup_path"
drop_rehearsal_database
"$compose_script" "$mode" exec -T postgres sh -c \
  'createdb --username="$POSTGRES_USER" botera_master_restore_check'
"$compose_script" "$mode" exec -T postgres sh -c \
  'pg_restore --exit-on-error --no-owner --no-privileges --username="$POSTGRES_USER" --dbname=botera_master_restore_check' \
  <"$backup_path"
"$compose_script" "$mode" exec -T postgres sh -c \
  'psql --username="$POSTGRES_USER" --dbname=botera_master_restore_check --tuples-only --no-align --command="SELECT COUNT(*) FROM django_migrations"' \
  >/dev/null
drop_rehearsal_database
trap - 0 HUP INT TERM

printf '%s\n' "PostgreSQL restore rehearsal succeeded in isolated database: $rehearsal_database"
