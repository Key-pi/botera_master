#!/bin/sh
# Mode-safe Docker Compose entry point. Keeping the file selection here avoids
# accidentally starting the public stack with local Django settings or mounts.
set -eu

usage() {
    printf '%s\n' "Usage: $0 <dev|prod> <docker compose arguments...>" >&2
    printf '%s\n' "  $0 dev up --build -d" >&2
    printf '%s\n' "  $0 prod config --quiet" >&2
}

if [ "$#" -lt 2 ]; then
    usage
    exit 2
fi

mode=$1
shift
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"

case "$mode" in
    dev|development)
        env_file=${BOTERA_ENV_FILE:-.env}
        project_name=${BOTERA_COMPOSE_PROJECT_NAME:-botera-master}
        if [ ! -f "$env_file" ]; then
            printf '%s\n' "Development env file not found: $env_file" >&2
            printf '%s\n' "Create it from .env.example first." >&2
            exit 1
        fi
        export BOTERA_ENV_FILE="$env_file"
        exec docker compose \
            --project-name "$project_name" \
            --env-file "$env_file" \
            -f docker-compose.yml \
            "$@"
        ;;
    prod|production)
        env_file=${BOTERA_PRODUCTION_ENV_FILE:-.env.production}
        project_name=${BOTERA_PRODUCTION_COMPOSE_PROJECT_NAME:-botera-master-production}
        if [ ! -f "$env_file" ]; then
            printf '%s\n' "Production env file not found: $env_file" >&2
            printf '%s\n' "Create it from .env.production.example first." >&2
            exit 1
        fi
        export BOTERA_PRODUCTION_ENV_FILE="$env_file"
        exec docker compose \
            --project-name "$project_name" \
            --env-file "$env_file" \
            -f docker-compose.yml \
            -f docker-compose.production.yml \
            "$@"
        ;;
    *)
        printf '%s\n' "Unknown Docker mode: $mode" >&2
        usage
        exit 2
        ;;
esac
