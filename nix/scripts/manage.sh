#!/usr/bin/env bash
# Run an arbitrary Django management command against the project-local stack.
#
# Every argument is passed straight through to manage.py, so this is the Nix
# equivalent of `docker compose exec qgisfeed python qgisfeedproject/manage.py`:
#   ./nix/scripts/manage.sh createsuperuser
#   ./nix/scripts/manage.sh migrate
#   ./nix/scripts/manage.sh shell
#   ./nix/scripts/manage.sh collectstatic --no-input
# or, from outside the dev shell:
#   nix run .#manage -- createsuperuser
set -euo pipefail

# Resolve the repository root so this works both when run directly from a
# checkout and when installed into the Nix store by writeShellApplication.
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export PROJECT_ROOT
# shellcheck source=./common.sh
. "${PROJECT_ROOT}/nix/scripts/common.sh"

if [ "$#" -eq 0 ]; then
    echo "usage: ${0##*/} <command> [args...]" >&2
    echo >&2
    echo "Runs qgisfeedproject/manage.py with the Nix environment configured." >&2
    echo "Try '${0##*/} help' for the list of management commands." >&2
    exit 2
fi

# Not every command needs the database (check, help, makemigrations), so this
# is a warning rather than a hard failure - let Django report the connection
# error itself if the command actually needs a cursor.
if ! pg_ctl status --pgdata="${PGDATA}" >/dev/null 2>&1; then
    echo "warning: PostgreSQL is not running (./nix/scripts/db-start.sh)" >&2
fi

exec python "${MANAGE}" "$@"
