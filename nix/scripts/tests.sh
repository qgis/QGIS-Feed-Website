#!/usr/bin/env bash
# Run the Django test suite, or the test labels passed as arguments.
set -euo pipefail

cd "${PROJECT_ROOT}"

exec manage test "${@:-qgisfeed}"
