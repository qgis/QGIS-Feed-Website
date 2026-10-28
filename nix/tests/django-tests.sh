#!/usr/bin/env bash
# Runs the Django test suite against a PostGIS cluster in the build sandbox.
#
# shellcheck disable=SC2154  # app, manageProgram, fixtureImage, settingsTemplate,
# geoipDb and out come from the derivation.
set -euo pipefail

export HOME="${TMPDIR}/home"
export PYTHONDONTWRITEBYTECODE=1
export PGDATA="${TMPDIR}/pgdata"
export PGHOST="${TMPDIR}/pgsocket"
export PGUSER="qgisfeed"
export PGDATABASE="qgisfeed"
mkdir -p "${HOME}" "${PGHOST}"

echo "== Starting PostgreSQL =="
initdb --username="${PGUSER}" --auth=trust --encoding=UTF8 --locale=C \
    --pgdata="${PGDATA}" >/dev/null

cat >>"${PGDATA}/postgresql.conf" <<CONF

listen_addresses = ''
unix_socket_directories = '${PGHOST}'
fsync = off
CONF

pg_ctl start --pgdata="${PGDATA}" --log="${TMPDIR}/postgres.log" --wait >/dev/null
trap 'pg_ctl stop --pgdata="${PGDATA}" --mode=immediate >/dev/null 2>&1 || true' EXIT

# Into template1, so the database the test runner creates inherits it.
psql --dbname=template1 --quiet --command="CREATE EXTENSION IF NOT EXISTS postgis;"
createdb "${PGDATABASE}"

export DB_NAME="${PGDATABASE}"
export DB_USER="${PGUSER}"
export DB_HOST="${PGHOST}"
export DB_PORT="5432"
export DB_PASSWORD=""

export MEDIA_ROOT="${TMPDIR}/media"
export STATIC_ROOT="${TMPDIR}/static"
export GEOIP_PATH="${TMPDIR}/geoip"
mkdir -p "${MEDIA_ROOT}/feedimages" "${STATIC_ROOT}" "${GEOIP_PATH}"

# GeoIP2() is built on every UserVisit save, outside the try that guards the
# lookup, so the suite needs a database present. Django looks for this exact
# filename.
cp "${geoipDb}" "${GEOIP_PATH}/GeoLite2-City.mmdb"

# Several tests open this; the package drops the media directory.
cp "${fixtureImage}" "${MEDIA_ROOT}/feedimages/rust.png"
chmod u+w "${MEDIA_ROOT}/feedimages/rust.png"

# Must be a .py name: spec_from_file_location picks its loader by extension.
export DJANGO_LOCAL_SETTINGS="${TMPDIR}/settings_local.py"
cp "${settingsTemplate}" "${DJANGO_LOCAL_SETTINGS}"

echo "== migrate =="
"${app}/bin/${manageProgram}" migrate --noinput

echo "== test =="
"${app}/bin/${manageProgram}" test qgisfeed --noinput

touch "${out}"
