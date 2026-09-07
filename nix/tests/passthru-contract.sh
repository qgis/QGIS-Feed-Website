#!/usr/bin/env bash
# Assert that the facts published in the package's passthru match what the
# package actually installs.
#
# A deployment reads these attributes instead of hardcoding names, so a stale
# one evaluates cleanly and only fails on the host. That is exactly what would
# have happened when the gunicorn entry point was removed while passthru still
# advertised gunicornProgram.
#
# shellcheck disable=SC2154  # app, out and the *Program names are supplied as
# derivation attributes by nix/checks.nix; shellcheck cannot see them.
set -euo pipefail

fail() {
    echo "passthru contract violated: $*" >&2
    exit 1
}

for program in "${manageProgram}" "${uwsgiProgram}"; do
    [ -x "${app}/bin/${program}" ] ||
        fail "${program} is not an executable in ${app}/bin"
done

[ -f "${uwsgiIni}" ] || fail "uwsgiIni does not exist: ${uwsgiIni}"
grep -q '^\[uwsgi\]' "${uwsgiIni}" || fail "uwsgiIni has no [uwsgi] section"

# The socket, pidfile and chdir are host decisions and are documented as absent
# from the shipped ini. A deployment appends its own section, and uWSGI would
# take the last value for a duplicated key - so a socket added here would
# silently override the host's.
if grep -qE '^[[:space:]]*(socket|http-socket|pidfile|chdir)[[:space:]]*=' "${uwsgiIni}"; then
    fail "uwsgiIni must not set socket, pidfile or chdir"
fi

[ -x "${fetchGeoip}/bin/fetch-geoip" ] ||
    fail "fetchGeoip does not install bin/fetch-geoip"

# The module names must be importable by the interpreter that ships with the
# application, not merely be plausible strings.
export MEDIA_ROOT="${TMPDIR}/media"
export STATIC_ROOT="${TMPDIR}/static"
mkdir -p "${MEDIA_ROOT}" "${STATIC_ROOT}"

"${app}/bin/${manageProgram}" shell --command \
    "import importlib
for name in ('${settingsModule}', '${wsgiModule}'):
    importlib.import_module(name)
" || fail "settingsModule or wsgiModule is not importable"

echo "passthru contract holds"
touch "${out}"
