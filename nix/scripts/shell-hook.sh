#!/usr/bin/env bash
# devShell entry hook. Sourced, never executed, so no `set -e`: a non-zero
# status here would close the user's terminal. Follows dev-env.sh.

if [ -f "${PROJECT_ROOT}/.pre-commit-config.yaml" ] && [ -d "${PROJECT_ROOT}/.git" ]; then
    pre-commit install --install-hooks >/dev/null 2>&1 &&
        echo "pre-commit hook installed."
fi

dev-help
