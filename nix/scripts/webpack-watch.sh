#!/usr/bin/env bash
# Build the webpack bundles and keep rebuilding them as the sources change.
set -euo pipefail

cd "${PROJECT_ROOT}"

# npm ci installs exactly what package-lock.json pins.
if [ ! -d node_modules ]; then
    echo "Installing npm dependencies"
    npm ci
fi

exec npm run start
