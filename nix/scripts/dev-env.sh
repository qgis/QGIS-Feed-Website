#!/usr/bin/env bash
# The part of the development environment that depends on the checkout. Read by
# both the dev shell hook and `dev`, so no `set -e` and no `exit`: a non-zero
# status in the hook would close the user's terminal.

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export PROJECT_ROOT

export DJANGO_DIR="${PROJECT_ROOT}/qgisfeedproject"
export MANAGE="${DJANGO_DIR}/manage.py"
export PYTHONPATH="${DJANGO_DIR}"

# libpq reads a path as a socket directory.
export PGDATA="${PROJECT_ROOT}/.state/pgdata"
export PGHOST="${PROJECT_ROOT}/.state/run"
export DB_HOST="${PGHOST}"

export GEOIP_DIR="${PROJECT_ROOT}/.state/geoip"
export GEOIP_PATH="${GEOIP_DIR}/"
