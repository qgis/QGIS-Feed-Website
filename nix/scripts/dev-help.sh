#!/usr/bin/env bash
# List what the development shell provides.
set -euo pipefail

cat <<'HELP'

QGIS Feed development environment

  dev             start everything: database, webpack, the site on :8000
  manage          run a Django management command
  tests           run the Django test suite
  db-reset        drop and recreate the database, migrate, load the fixtures
  db-restore      restore the database from a pg_dump archive
  fetch-geoip     download the GeoLite2 City database
  runserver       run only the development server
  webpack-watch   run only the webpack watcher

  dev-help        show this again

First time here? Run db-reset once to load the fixtures.
HELP
