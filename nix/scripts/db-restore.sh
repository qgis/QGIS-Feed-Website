#!/usr/bin/env bash
# Restore the local database from a pg_dump archive.
# DESTRUCTIVE, and only ever touches the cluster under .nix/pgdata.
#
# usage: db-restore.sh [--force] [path/to/dump.dmp]
set -euo pipefail

FORCE=0
DUMP=""
while [ "$#" -gt 0 ]; do
    case "$1" in
    --force) FORCE=1 ;;
    -*)
        echo "error: unknown option: $1" >&2
        exit 1
        ;;
    *) DUMP="$1" ;;
    esac
    shift
done

# QGISFEED_BACKUP_DIR is a Django setting, so settings_local.py is the only
# place this project is configured. Falling back keeps the error message useful
# if Django cannot be loaded.
if [ -z "${DUMP}" ]; then
    backup_dir="$(cd "${DJANGO_DIR}" && python -c '
import django

django.setup()
from django.conf import settings

print(settings.QGISFEED_BACKUP_DIR)
' 2>/dev/null)" || backup_dir=""
    DUMP="${backup_dir:-backups}/latest-qgisfeed.dmp"
fi

if [ ! -f "${DUMP}" ]; then
    echo "error: dump not found: ${DUMP}" >&2
    echo "Pass the path as an argument, or set QGISFEED_BACKUP_DIR in" >&2
    echo "qgisfeedproject/qgisfeedproject/settings_local.py." >&2
    exit 1
fi

if ! pg_isready --quiet; then
    echo "error: PostgreSQL is not running. Start the stack with: dev" >&2
    exit 1
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

# --no-owner --no-privileges because production dumps are owned by the docker
# role, which does not exist here.
echo "Restoring from ${DUMP}"
if ! pg_restore --dbname="${PGDATABASE}" --no-owner --no-privileges "${DUMP}"; then
    echo >&2
    echo "warning: pg_restore reported errors (see above)." >&2
    echo "Ownership and 'COMMENT ON EXTENSION' failures are usually harmless." >&2
    echo "Anything mentioning a missing table or relation is not." >&2
    exit 1
fi

echo "Applying any migrations newer than the dump"
python "${MANAGE}" migrate

echo "Database restored into ${PGDATABASE}"
