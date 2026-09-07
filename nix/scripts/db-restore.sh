#!/usr/bin/env bash
# Restore the local development database from a pg_dump custom-format archive.
#
# The Nix equivalent of `make dev-dbrestore`, with two deliberate differences:
#
#   * Only the qgisfeed database is restored. The Makefile also restores
#     metabase, which is not part of the Nix stack - it is served by the
#     surrounding infrastructure.
#   * The dump is restored with --no-owner --no-privileges. Production dumps are
#     owned by the "docker" role, which does not exist in the project-local
#     cluster, and without these flags every statement would fail.
#
# DESTRUCTIVE. This drops the local development database. It only ever touches
# the project-local cluster under .nix/pgdata; it cannot reach a docker volume,
# a staging box or production. It asks first unless --force is given.
#
# usage: db-restore.sh [--force] [path/to/dump.dmp]
set -euo pipefail

# Resolve the repository root so this works both when run directly from a
# checkout and when installed into the Nix store by writeShellApplication.
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export PROJECT_ROOT
# shellcheck source=./common.sh
. "${PROJECT_ROOT}/nix/scripts/common.sh"

FORCE=0
DUMP=""
while [ "$#" -gt 0 ]; do
    case "$1" in
    --force)
        FORCE=1
        ;;
    -*)
        die "unknown option: $1"
        ;;
    *)
        DUMP="$1"
        ;;
    esac
    shift
done

# QGISFEED_BACKUP_DIR is resolved in common.sh from QGISFEED_BACKUP_VOLUME
# (set in .env, the same variable the docker compose files use).
DUMP="${DUMP:-${QGISFEED_BACKUP_DIR}/latest-qgisfeed.dmp}"

if [ ! -f "${DUMP}" ]; then
    die "dump not found: ${DUMP}
Place a pg_dump custom-format archive there, pass a path explicitly, or set
QGISFEED_BACKUP_VOLUME in .env to the directory holding latest-qgisfeed.dmp."
fi

if ! pg_ctl status --pgdata="${PGDATA}" >/dev/null 2>&1; then
    die "PostgreSQL is not running. Start it first: ./nix/scripts/db-start.sh"
fi

if [ "${FORCE}" -ne 1 ]; then
    echo "About to DROP and restore the database '${PGDATABASE}'"
    echo "  cluster: ${PGDATA}"
    echo "  dump:    ${DUMP} ($(du -h "${DUMP}" | cut -f1))"
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
dropdb --if-exists --force "${PGDATABASE}"

echo "Creating ${PGDATABASE}"
createdb "${PGDATABASE}"
psql --dbname="${PGDATABASE}" --quiet \
    --command="CREATE EXTENSION IF NOT EXISTS postgis;"

echo "Restoring from ${DUMP}"
# pg_restore exits non-zero if any statement failed. That is worth surfacing
# rather than hiding behind `|| true`, but a dump taken from a differently
# provisioned cluster commonly trips over a few ownership or extension-comment
# statements that do not affect the data, so say what to look at.
if ! pg_restore --dbname="${PGDATABASE}" --no-owner --no-privileges "${DUMP}"; then
    echo >&2
    echo "warning: pg_restore reported errors (see above)." >&2
    echo "Ownership and 'COMMENT ON EXTENSION' failures are usually harmless;" >&2
    echo "anything mentioning a missing table or relation is not." >&2
    echo "Verify with: ./nix/scripts/manage.sh showmigrations" >&2
    exit 1
fi

echo "Restore complete. Applying any migrations newer than the dump."
python "${MANAGE}" migrate

echo "Database restored into ${PGDATABASE}"
