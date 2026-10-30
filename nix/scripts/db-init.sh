#!/usr/bin/env bash
set -euo pipefail

psql --dbname=template1 --quiet --command="CREATE EXTENSION IF NOT EXISTS postgis;"

exists="$(psql --dbname=postgres --tuples-only --no-align \
    --command="SELECT 1 FROM pg_database WHERE datname = '${PGDATABASE}'")"
if [ -z "${exists}" ]; then
    createdb "${PGDATABASE}"
fi
