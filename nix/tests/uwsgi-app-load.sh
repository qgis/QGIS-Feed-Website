#!/usr/bin/env bash
# Prove that uWSGI can load the WSGI application.
#
# `uwsgi --version` only proves the binary links. uWSGI embeds its own
# interpreter with a PYTHONPATH built by the wrapper in nix/package.nix, quite
# separately from the one qgisfeed-manage uses, so the application can import
# fine under manage.py and still fail to load under uWSGI. That failure would
# otherwise appear for the first time when the service starts on the host.
#
# --need-app makes uWSGI exit non-zero rather than serving 500s when the
# application cannot be imported.
#
# No database is needed: importing the WSGI callable does not connect. The
# request path is covered by the django-suite check, which has a cluster.
#
# shellcheck disable=SC2154  # app, uwsgiProgram, uwsgiIni and out are supplied
# as derivation attributes by nix/checks.nix.
set -euo pipefail

export MEDIA_ROOT="${TMPDIR}/media"
export STATIC_ROOT="${TMPDIR}/static"
mkdir -p "${MEDIA_ROOT}" "${STATIC_ROOT}"

log="${TMPDIR}/uwsgi.log"

# The shipped ini asks for 8 workers with cheaper=2; one worker is enough here,
# and cheaper must be lower than workers or uWSGI refuses to start.
"${app}/bin/${uwsgiProgram}" \
    --ini "${uwsgiIni}" \
    --http-socket 127.0.0.1:8391 \
    --need-app \
    --workers 1 \
    --cheaper 0 \
    --logto "${log}" &
uwsgi_pid=$!

trap 'kill "${uwsgi_pid}" 2>/dev/null || true' EXIT

for _ in $(seq 1 60); do
    if grep -q "WSGI app 0 (mountpoint='') ready" "${log}" 2>/dev/null; then
        echo "uWSGI loaded the WSGI application"
        touch "${out}"
        exit 0
    fi

    if ! kill -0 "${uwsgi_pid}" 2>/dev/null; then
        echo "uWSGI exited before the application was ready:" >&2
        cat "${log}" >&2
        exit 1
    fi

    sleep 1
done

echo "uWSGI did not load the application within 60 seconds:" >&2
cat "${log}" >&2
exit 1
