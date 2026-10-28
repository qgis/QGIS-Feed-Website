#!/usr/bin/env bash
# Drop the local database, recreate it, migrate and load the fixtures.
# DESTRUCTIVE, and only ever touches the cluster under .nix/pgdata.
# Pass --force to skip the prompt.
set -euo pipefail

if ! pg_isready --quiet; then
    echo "error: PostgreSQL is not running. Start the stack with: dev" >&2
    exit 1
fi

if [ "${1:-}" != "--force" ]; then
    echo "About to DROP and recreate the database '${PGDATABASE}'"
    echo "  cluster: ${PGDATA}"
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

# Mirrors entrypoint_testing.sh so both stacks seed identically.
echo "Applying migrations"
python "${MANAGE}" migrate

echo "Loading fixtures"
python "${MANAGE}" loaddata \
    "${DJANGO_DIR}/qgisfeed/fixtures/users.json" \
    "${DJANGO_DIR}/qgisfeed/fixtures/qgisfeed.json"

echo "Database reset complete"
