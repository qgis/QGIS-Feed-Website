#!/usr/bin/env bash
# Run settings_contract.py against the settings module the package installs,
# rather than against the checkout, so the test covers what is actually
# deployed.
#
# shellcheck disable=SC2154  # appPythonPath, testScript and out are supplied
# as derivation attributes by nix/checks.nix.
set -euo pipefail

export PYTHONPATH="${appPythonPath}"
export PYTHONDONTWRITEBYTECODE=1

python "${testScript}"

touch "${out}"
