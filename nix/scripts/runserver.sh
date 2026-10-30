#!/usr/bin/env bash
# The Django development server. Binds to localhost; override with
# QGISFEED_RUNSERVER_ADDR.
set -euo pipefail

exec python "${MANAGE}" runserver "${QGISFEED_RUNSERVER_ADDR:-127.0.0.1:8000}"
