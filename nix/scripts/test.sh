#!/usr/bin/env bash
# Run the Django test suite against the project-local cluster.
#
# Any extra arguments are passed through to manage.py test, so you can narrow
# the run, for example:
#   ./nix/scripts/test.sh qgisfeed.tests.FeedsItemFormTestCase
# or, from outside the dev shell:
#   nix run .#test -- qgisfeed.tests.FeedsItemFormTestCase
set -euo pipefail

# Resolve the repository root so this works both when run directly from a
# checkout and when installed into the Nix store by writeShellApplication.
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export PROJECT_ROOT
# shellcheck source=./common.sh
. "${PROJECT_ROOT}/nix/scripts/common.sh"

if ! pg_ctl status --pgdata="${PGDATA}" >/dev/null 2>&1; then
    die "PostgreSQL is not running. Start it first: ./nix/scripts/db-start.sh"
fi

# Several tests open MEDIA_ROOT/feedimages/rust.png. The repository ships that
# fixture under qgisfeedproject/media, so make sure it is present wherever
# MEDIA_ROOT points before running.
media_root="$(python "${MANAGE}" shell --command \
    'from django.conf import settings; print(settings.MEDIA_ROOT)' 2>/dev/null || true)"
if [ -n "${media_root}" ] && [ -d "${media_root}" ]; then
    if [ ! -f "${media_root}/feedimages/rust.png" ]; then
        echo "Seeding the test fixture image into ${media_root}/feedimages"
        mkdir -p "${media_root}/feedimages"
        cp "${DJANGO_DIR}/media/feedimages/rust.png" \
            "${media_root}/feedimages/rust.png"
    fi
fi

exec python "${MANAGE}" test "${@:-qgisfeed}"
