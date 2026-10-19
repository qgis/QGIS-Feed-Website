#!/usr/bin/env bash
# Run the Django test suite, and then serve a real request, against a
# PostGIS cluster started inside the build sandbox.
#
# This is the slow tier. Everything it needs is created in $TMPDIR and thrown
# away with it: the cluster listens on a unix socket only, so it cannot collide
# with a developer's own PostgreSQL or be reached from anywhere.
#
# shellcheck disable=SC2154  # app, manageProgram, uwsgiProgram, uwsgiIni,
# fixtureImage, settingsTemplate, geoipDb and out are supplied as derivation
# attributes by nix/checks.nix.
set -euo pipefail

export HOME="${TMPDIR}/home"
export PYTHONDONTWRITEBYTECODE=1
export PGDATA="${TMPDIR}/pgdata"
export PGHOST="${TMPDIR}/pgsocket"
export PGUSER="qgisfeed"
export PGDATABASE="qgisfeed"
mkdir -p "${HOME}" "${PGHOST}"

manage="${app}/bin/${manageProgram}"

echo "== Starting PostgreSQL =="
initdb --username="${PGUSER}" --auth=trust --encoding=UTF8 --locale=C \
    --pgdata="${PGDATA}" >/dev/null

# listen_addresses='' restricts the server to the unix socket below.
cat >>"${PGDATA}/postgresql.conf" <<EOF

# Added by nix/tests/integration.sh
listen_addresses = ''
unix_socket_directories = '${PGHOST}'
fsync = off
full_page_writes = off
EOF

pg_ctl start --pgdata="${PGDATA}" --log="${TMPDIR}/postgres.log" --wait >/dev/null
trap 'pg_ctl stop --pgdata="${PGDATA}" --mode=immediate >/dev/null 2>&1 || true' EXIT

# No migration in qgisfeed/migrations runs CreateExtension, so the extension
# has to exist before any database is used. Installing it into template1 means
# the test database that the Django runner creates inherits it - otherwise the
# run dies with 'type geometry does not exist' before a single test executes.
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

# GeoIP2() is constructed on every UserVisit save, outside the try that guards
# the lookup, so an empty GEOIP_PATH fails the suite outright. Given a
# directory, Django does not scan it: it looks for the two exact filenames in
# GEOIP_SETTINGS, so the copy below has to keep this name.
echo "== Installing the GeoIP database =="
cp "${geoipDb}" "${GEOIP_PATH}/GeoLite2-City.mmdb"

# Several tests open MEDIA_ROOT/feedimages/rust.png. The package deliberately
# drops the media directory, since it is runtime state, so the fixture is
# copied straight from the source tree.
cp "${fixtureImage}" "${MEDIA_ROOT}/feedimages/rust.png"
chmod u+w "${MEDIA_ROOT}/feedimages/rust.png"

# settings.py applies this last, so it has the final word over the environment
# exported above. The template is the only version of the file a checkout has -
# the real one is git-ignored - and it carries settings that exist nowhere else,
# so without it the run would leave OIDC_RP_CLIENT_SECRET undefined.
#
# It has to be copied under a .py name: spec_from_file_location picks its loader
# by extension, and a .templ path yields no loader, which fails as an
# AttributeError that apply_local_settings_override does not catch.
echo "== Installing the local settings override =="
export DJANGO_LOCAL_SETTINGS="${TMPDIR}/settings_local_override.py"
cp "${settingsTemplate}" "${DJANGO_LOCAL_SETTINGS}"

echo "== migrate =="
"${manage}" migrate --noinput

echo "== collectstatic =="
# Proves the webpack bundles and webpack-stats.json are where settings.py
# expects them relative to BASE_DIR, which is easy to get wrong in the store
# because the layout differs from a checkout.
"${manage}" collectstatic --noinput >/dev/null

echo "== test suite =="
"${manage}" test qgisfeed --noinput

echo "== serving a request =="
# The suite exercises Django through the test client, which bypasses the
# server entirely. This is the only place the deployment's actual entry point
# handles a request.
#
# 127.0.0.1 has to be an allowed host: Django compares the Host header, with
# the port stripped, against ALLOWED_HOSTS.
export DOMAIN_NAME="127.0.0.1"
log="${TMPDIR}/uwsgi.log"

"${app}/bin/${uwsgiProgram}" \
    --ini "${uwsgiIni}" \
    --http-socket 127.0.0.1:8391 \
    --need-app \
    --workers 1 \
    --cheaper 0 \
    --logto "${log}" &
uwsgi_pid=$!
trap 'kill "${uwsgi_pid}" 2>/dev/null || true
      pg_ctl stop --pgdata="${PGDATA}" --mode=immediate >/dev/null 2>&1 || true' EXIT

status=""
for _ in $(seq 1 60); do
    if ! kill -0 "${uwsgi_pid}" 2>/dev/null; then
        echo "uWSGI exited before it served anything:" >&2
        cat "${log}" >&2
        exit 1
    fi
    status="$(curl --silent --show-error --max-time 10 \
        --output "${TMPDIR}/response.json" \
        --write-out '%{http_code}' \
        "http://127.0.0.1:8391/?json=1" 2>/dev/null || true)"
    [ -n "${status}" ] && [ "${status}" != "000" ] && break
    sleep 1
done

if [ "${status}" != "200" ]; then
    echo "GET / returned '${status}', expected 200." >&2
    echo "--- response ---" >&2
    head -c 2000 "${TMPDIR}/response.json" >&2 || true
    echo >&2
    echo "--- uwsgi log ---" >&2
    cat "${log}" >&2
    exit 1
fi

# QgisEntriesView content-negotiates: it answers QGIS user agents with JSON and
# everything else with the HTML feed page, so json=1 is what makes curl get the
# API response the QGIS client consumes.
python -c "import json,sys; json.load(open(sys.argv[1]))" "${TMPDIR}/response.json"

echo "The application served GET /?json=1 with 200 and valid JSON"
touch "${out}"
