#!/usr/bin/env bash
# Runs ahead of every dev helper and `dev` itself. They rely on the environment
# the dev shell exports, so say so rather than fail on an unbound variable.

if [ -z "${QGISFEED_DEV_SHELL:-}" ]; then
    echo "error: ${0##*/} must run inside the QGIS Feed dev shell." >&2
    echo "Enter it with: nix develop" >&2
    echo "(direnv users: cd into the checkout and run direnv allow)" >&2
    exit 1
fi
