#!/usr/bin/env bash
# Download the GeoLite2 City database the geofence feature needs.
#
#   fetch-geoip.sh [destination-directory]
#
# Self-contained, because it also runs on deployment hosts with no checkout.
set -euo pipefail

URL="${GEOIP_URL:-https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb}"

die() {
    echo "error: $*" >&2
    exit 1
}

DEST="${1:-${GEOIP_DIR:-}}"
if [ -z "${DEST}" ]; then
    root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    [ -n "${root}" ] || die 'no destination given, GEOIP_DIR is unset and this
is not a git checkout. Pass the directory to write to, for example:
  fetch-geoip.sh /var/lib/qgisfeed/geoip'
    DEST="${root}/.nix/geoip"
fi

TARGET="${DEST}/GeoLite2-City.mmdb"

mkdir -p "${DEST}"

# Already there, so nothing to do. Delete the file to fetch a fresh copy.
if [ -s "${TARGET}" ] && [ "$(wc -c <"${TARGET}")" -ge 1000000 ]; then
    echo "GeoIP database already present at ${TARGET}"
    exit 0
fi

echo "Fetching GeoLite2-City.mmdb"
# Download to a temporary file so an interrupted transfer cannot leave a
# truncated database in place; the final move is atomic within the directory.
tmp="$(mktemp "${DEST}/.GeoLite2-City.mmdb.XXXXXX")"
trap 'rm -f "${tmp}"' EXIT

curl --fail --location --silent --show-error --output "${tmp}" "${URL}"

# A valid mmdb is several megabytes. Anything tiny is an error page.
size="$(wc -c <"${tmp}")"
if [ "${size}" -lt 1000000 ]; then
    die "downloaded file is only ${size} bytes, that is not a valid mmdb database"
fi

chmod 0644 "${tmp}"
mv "${tmp}" "${TARGET}"
trap - EXIT

echo "GeoIP database installed at ${TARGET} ($((size / 1024 / 1024)) MB)"
