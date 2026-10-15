#!/usr/bin/env bash
# Run the development stack: webpack in watch mode plus the Django dev server.
#
# Mirrors what the "webpack" and "qgisfeed" docker compose services do together,
# but in a single foreground process. Ctrl-C stops both.
set -euo pipefail

# Resolve the repository root so this works both when run directly from a
# checkout and when installed into the Nix store by writeShellApplication.
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export PROJECT_ROOT
# shellcheck source=./common.sh
. "${PROJECT_ROOT}/nix/scripts/common.sh"

cd "${PROJECT_ROOT}"

if ! pg_ctl status --pgdata="${PGDATA}" >/dev/null 2>&1; then
    die "PostgreSQL is not running. Start it first: ./nix/scripts/db-start.sh"
fi

if [ ! -d "${PROJECT_ROOT}/node_modules" ]; then
    echo "Installing npm dependencies"
    npm install
fi

# Kill the whole process group on exit so webpack does not survive Ctrl-C.
webpack_pid=""
cleanup() {
    if [ -n "${webpack_pid}" ] && kill -0 "${webpack_pid}" 2>/dev/null; then
        kill "${webpack_pid}" 2>/dev/null || true
        wait "${webpack_pid}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

echo "Starting webpack in watch mode"
npm run start &
webpack_pid=$!

# webpack-stats.json must exist before Django renders a page using
# {% render_bundle %}, otherwise webpack_loader raises.
echo "Waiting for the initial webpack build"
for _ in $(seq 1 60); do
    [ -f "${PROJECT_ROOT}/webpack-stats.json" ] && break
    sleep 1
done

echo "Starting the Django development server on http://localhost:8000"
python "${MANAGE}" runserver 0.0.0.0:8000
