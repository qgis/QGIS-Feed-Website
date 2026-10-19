#!/usr/bin/env bash
# Fail if the models no longer match the migrations on disk.
#
# makemigrations opens no database connection - it compares the model state
# against the migration graph - so this stays in the fast, hermetic tier.
#
# Without this check a model change reaches a deployment as a migrate that does
# nothing, and the mismatch only surfaces as a column that is missing at
# runtime.
#
# shellcheck disable=SC2154  # app, manageProgram and out are supplied as
# derivation attributes by nix/checks.nix.
set -euo pipefail

export MEDIA_ROOT="${TMPDIR}/media"
export STATIC_ROOT="${TMPDIR}/static"
mkdir -p "${MEDIA_ROOT}" "${STATIC_ROOT}"

if ! "${app}/bin/${manageProgram}" makemigrations --check --dry-run; then
    cat >&2 <<'EOF'

The models and the migrations have diverged. Generate the missing migration in
a development shell and commit it with the model change:

    nix develop -c qgisfeed-manage makemigrations

Review it before committing: a migration that rewrites a primary key or a
column type locks the table on the production database.
EOF
    exit 1
fi

touch "${out}"
