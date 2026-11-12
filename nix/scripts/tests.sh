#!/usr/bin/env bash
# Run the Django test suite, or the test labels passed as arguments.
set -euo pipefail

cd "${PROJECT_ROOT}"

[ "$#" -gt 0 ] || set -- qgisfeed qgis_sso
exec manage test "$@"
