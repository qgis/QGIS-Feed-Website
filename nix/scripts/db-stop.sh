#!/usr/bin/env bash
# Stop the project-local PostgreSQL cluster.
set -euo pipefail

# Resolve the repository root so this works both when run directly from a
# checkout and when installed into the Nix store by writeShellApplication.
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export PROJECT_ROOT
# shellcheck source=./common.sh
. "${PROJECT_ROOT}/nix/scripts/common.sh"

if [ ! -d "${PGDATA}" ]; then
    echo "No cluster at ${PGDATA}, nothing to stop"
    exit 0
fi

if ! pg_ctl status --pgdata="${PGDATA}" >/dev/null 2>&1; then
    echo "PostgreSQL is not running"
    exit 0
fi

pg_ctl stop --pgdata="${PGDATA}" --mode=fast --wait >/dev/null
echo "PostgreSQL stopped"
