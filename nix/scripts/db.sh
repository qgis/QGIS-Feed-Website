#!/usr/bin/env bash
# Run PostgreSQL on a unix socket only, creating the cluster on first start.
set -euo pipefail

mkdir -p "${PGHOST}"

if [ ! -f "${PGDATA}/PG_VERSION" ]; then
    rm -rf "${PGDATA}.init"
    initdb --pgdata="${PGDATA}.init" --locale=C --encoding=UTF8 --auth=trust
    mv "${PGDATA}.init" "${PGDATA}"
fi

exec postgres -D "${PGDATA}" -k "${PGHOST}" -c listen_addresses=''
