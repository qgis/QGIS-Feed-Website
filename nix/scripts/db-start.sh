#!/usr/bin/env bash
# Start the project-local PostgreSQL/PostGIS cluster.
#
# The cluster is owned by the invoking user, lives under .nix/pgdata and listens
# on a unix socket inside the repository only. Nothing is bound to a network
# interface, so this cannot collide with a system postgres or be reached from
# outside the machine.
set -euo pipefail

# Resolve the repository root so this works both when run directly from a
# checkout and when installed into the Nix store by writeShellApplication.
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export PROJECT_ROOT
# shellcheck source=./common.sh
. "${PROJECT_ROOT}/nix/scripts/common.sh"

mkdir -p "${PGSOCKET}"

if [ ! -d "${PGDATA}" ]; then
    echo "Initialising a new cluster in ${PGDATA}"
    initdb --username="${PGUSER}" --auth=trust --encoding=UTF8 --locale=C \
        --pgdata="${PGDATA}" >/dev/null

    # Written into postgresql.conf rather than passed through pg_ctl --options,
    # where the nested quoting needed for an empty listen_addresses is fragile.
    # listen_addresses='' means the server accepts unix socket connections only,
    # so it can never clash with a system PostgreSQL or be reached remotely.
    cat >>"${PGDATA}/postgresql.conf" <<EOF

# Added by nix/scripts/db-start.sh
listen_addresses = ''
unix_socket_directories = '${PGSOCKET}'
EOF
fi

if pg_ctl status --pgdata="${PGDATA}" >/dev/null 2>&1; then
    echo "PostgreSQL is already running (socket: ${PGSOCKET})"
    exit 0
fi

echo "Starting PostgreSQL"
pg_ctl start --pgdata="${PGDATA}" --log="${QGISFEED_STATE_DIR}/postgres.log" \
    --wait >/dev/null

# createdb fails if the database already exists, which is fine on restart.
if ! psql --dbname=postgres --tuples-only --no-align \
    --command="SELECT 1 FROM pg_database WHERE datname = '${PGDATABASE}'" |
    grep -q 1; then
    echo "Creating database ${PGDATABASE}"
    createdb "${PGDATABASE}"
fi

# PostGIS is required: settings.py uses the postgis backend and the models use
# PolygonField.
psql --dbname="${PGDATABASE}" --quiet \
    --command="CREATE EXTENSION IF NOT EXISTS postgis;"

echo "PostgreSQL ready on ${PGSOCKET} (database: ${PGDATABASE})"
