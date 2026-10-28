#!/usr/bin/env bash
# Recreate the local development database from scratch: drop it, recreate it,
# apply migrations and load the fixtures.
#
# DESTRUCTIVE. This throws away every row in the local development database.
# It only ever touches the project-local cluster under .nix/pgdata; it cannot
# reach a docker volume, a staging box or production. It still asks first,
# because "I meant to run db-start" is an easy mistake to make.
#
# Pass --force to skip the prompt (for use in scripted/CI contexts).
set -euo pipefail

# Resolve the repository root so this works both when run directly from a
# checkout and when installed into the Nix store by writeShellApplication.
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export PROJECT_ROOT
# shellcheck source=./common.sh
. "${PROJECT_ROOT}/nix/scripts/common.sh"

FORCE=0
if [ "${1:-}" = "--force" ]; then
    FORCE=1
fi

if ! pg_ctl status --pgdata="${PGDATA}" >/dev/null 2>&1; then
    die "PostgreSQL is not running. Start it first: ./nix/scripts/db-start.sh"
fi

if [ "${FORCE}" -ne 1 ]; then
    echo "About to DROP and recreate the database '${PGDATABASE}'"
    echo "  cluster: ${PGDATA}"
    echo "  socket:  ${PGSOCKET}"
    echo
    printf "All local development data will be lost. Continue? [y/N] "
    read -r reply
    case "${reply}" in
    [yY] | [yY][eE][sS]) ;;
    *)
        echo "Aborted."
        exit 1
        ;;
    esac
fi

echo "Dropping ${PGDATABASE}"
dropdb --if-exists "${PGDATABASE}"

echo "Creating ${PGDATABASE}"
createdb "${PGDATABASE}"
psql --dbname="${PGDATABASE}" --quiet \
    --command="CREATE EXTENSION IF NOT EXISTS postgis;"

# Mirrors entrypoint_testing.sh so the Nix and docker paths seed identically.
echo "Applying migrations"
python "${MANAGE}" migrate

echo "Loading fixtures"
python "${MANAGE}" loaddata \
    "${DJANGO_DIR}/qgisfeed/fixtures/users.json" \
    "${DJANGO_DIR}/qgisfeed/fixtures/qgisfeed.json"

echo "Database reset complete"
