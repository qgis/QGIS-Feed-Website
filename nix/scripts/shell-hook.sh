#!/usr/bin/env bash
# devShell entry hook. Sourced by `nix develop`, not executed directly.
#
# Note: no `set -euo pipefail` here. This runs inside the user's interactive
# shell, where a stray non-zero status must not close their terminal.

# shellcheck source=./common.sh
. "${PROJECT_ROOT_HINT:-$(pwd)}/nix/scripts/common.sh"

mkdir -p "${QGISFEED_STATE_DIR}"

# Every Python dependency comes from the Nix environment - see nix/python.nix
# and nix/python-packages.nix. There is no pip step and no virtualenv.

if [ -f "${PROJECT_ROOT}/.pre-commit-config.yaml" ] && [ -d "${PROJECT_ROOT}/.git" ]; then
    pre-commit install --install-hooks >/dev/null 2>&1 &&
        echo "pre-commit hook installed."
fi

# The helpers are advertised as direct script calls rather than `nix run`:
# inside this shell every tool they need is already on PATH, so running them
# directly avoids a redundant flake re-evaluation on each invocation.
cat <<'BANNER'

QGIS Feed - Nix development environment
_________________________________________________________________
Command                        : Description
_________________________________________________________________
./nix/scripts/db-start.sh      : Start the local PostgreSQL/PostGIS
./nix/scripts/db-stop.sh       : Stop it
./nix/scripts/db-reset.sh      : Recreate the DB, migrate, load fixtures
./nix/scripts/db-restore.sh    : Restore the DB from a pg_dump archive
./nix/scripts/manage.sh <cmd>  : Run a Django management command
./nix/scripts/dev.sh           : Run webpack watch + Django dev server
./nix/scripts/test.sh          : Run the Django test suite
./nix/scripts/fetch-geoip.sh   : Download the GeoLite2 City database
_________________________________________________________________
From outside this shell the same helpers are `nix run .#db-start` etc.
_________________________________________________________________
BANNER

if [ ! -f "${GEOIP_DIR}/GeoLite2-City.mmdb" ]; then
    echo "note: GeoLite2-City.mmdb is missing; the geofence feature will not"
    echo "      resolve locations until you run: ./nix/scripts/fetch-geoip.sh"
    echo
fi
