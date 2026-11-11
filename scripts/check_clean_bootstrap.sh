#!/bin/sh

set -eu

if [ "$#" -gt 1 ]; then
  printf '%s\n' "Usage: $0 [development-env-file]" >&2
  exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
compose_script="$script_dir/compose.sh"
env_file=${1:-${BOTERA_ACCEPTANCE_ENV_FILE:-$project_root/.env}}
project_name=${BOTERA_ACCEPTANCE_PROJECT_NAME:-botera-master-acceptance}
web_port=${BOTERA_ACCEPTANCE_WEB_PORT:-18081}

if [ ! -f "$env_file" ]; then
  printf '%s\n' "Acceptance env file not found: $env_file" >&2
  exit 1
fi

case "$project_name" in
  botera-master-acceptance*) ;;
  *)
    printf '%s\n' \
      "BOTERA_ACCEPTANCE_PROJECT_NAME must start with botera-master-acceptance" >&2
    exit 2
    ;;
esac

export BOTERA_ENV_FILE="$env_file"
export BOTERA_COMPOSE_PROJECT_NAME="$project_name"
export WEB_PORT_ON_HOST="$web_port"

cleanup() {
  if [ "${KEEP_ACCEPTANCE_STACK:-false}" = "true" ]; then
    printf '%s\n' "Keeping acceptance stack: $project_name"
    return
  fi
  "$compose_script" dev down --volumes --remove-orphans >/dev/null 2>&1 || true
}

trap cleanup 0 HUP INT TERM

printf '%s\n' "==> Starting isolated clean-bootstrap stack: $project_name"
"$compose_script" dev up --build --detach --wait

printf '%s\n' "==> Checking Django and migration state"
"$compose_script" dev exec -T web python manage.py check
"$compose_script" dev exec -T web python manage.py migrate --check

printf '%s\n' "==> Re-running idempotent startup synchronizers"
"$compose_script" dev exec -T web python manage.py sync_system_templates
"$compose_script" dev exec -T web python manage.py sync_partner_owner_access --dry-run
"$compose_script" dev exec -T web python manage.py seed_pilot_demo

printf '%s\n' "==> Checking empty-token runner and internal health endpoint"
"$compose_script" dev exec -T web python manage.py run_partner_bots --dry-run
"$compose_script" dev exec -T web python -c \
  "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=5).read() == b'ok'"

printf '%s\n' "Clean Compose bootstrap accepted: $project_name"
