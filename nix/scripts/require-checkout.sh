#!/usr/bin/env bash
# Runs ahead of `dev`, which keeps its state under the project root. `nix run`
# works from anywhere, so make sure that root really is a checkout before
# creating a database in it.

if [ ! -f "${MANAGE}" ]; then
    echo "error: ${0##*/} must run from inside a QGIS Feed checkout." >&2
    echo "Looked for ${MANAGE}" >&2
    exit 1
fi
