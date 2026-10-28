#!/usr/bin/env bash
# devShell entry hook. Sourced, never executed, so no `set -e`: a non-zero
# status here would close the user's terminal.

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export PROJECT_ROOT

export DJANGO_DIR="${PROJECT_ROOT}/qgisfeedproject"
export MANAGE="${DJANGO_DIR}/manage.py"
export PYTHONPATH="${DJANGO_DIR}"

# libpq reads a path as a socket directory.
export PGDATA="${PROJECT_ROOT}/.nix/pgdata"
export PGHOST="${PROJECT_ROOT}/.nix/run"
export DB_HOST="${PGHOST}"

export GEOIP_DIR="${PROJECT_ROOT}/.nix/geoip"
export GEOIP_PATH="${GEOIP_DIR}/"

tests() {
    manage test "${@:-qgisfeed}"
}

dev-help() {
    cat <<'HELP'

QGIS Feed development environment

  dev           start everything: database, webpack, the site on :8000
  manage        run a Django management command
  tests         run the Django test suite
  db-reset      drop and recreate the database, migrate, load the fixtures
  db-restore    restore the database from a pg_dump archive
  fetch-geoip   download the GeoLite2 City database

  dev-help      show this again

First time here? Run db-reset once to load the fixtures.
HELP
}

if [ -f "${PROJECT_ROOT}/.pre-commit-config.yaml" ] && [ -d "${PROJECT_ROOT}/.git" ]; then
    pre-commit install --install-hooks >/dev/null 2>&1 &&
        echo "pre-commit hook installed."
fi

dev-help
