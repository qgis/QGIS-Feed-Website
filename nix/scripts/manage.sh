#!/usr/bin/env bash
# Run a Django management command. Arguments pass through to manage.py.
set -euo pipefail

if [ "$#" -eq 0 ]; then
    echo "usage: ${0##*/} <command> [args...]" >&2
    echo "Try '${0##*/} help' for the list of management commands." >&2
    exit 2
fi

if ! pg_isready --quiet; then
    echo "warning: PostgreSQL is not running (start the stack with: dev)" >&2
fi

exec python "${MANAGE}" "$@"
