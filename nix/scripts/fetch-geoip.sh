#!/usr/bin/env bash
# Download the GeoLite2 City database used by the geofence feature.
#
#   fetch-geoip.sh [destination-directory]
#
# The destination is taken from, in order: the first argument, $GEOIP_DIR, or
# <checkout>/.nix/geoip when run from a git checkout.
#
# Deliberately self-contained: this is installed as packages.fetchGeoip and run
# on deployment hosts, where there is no checkout and no common.sh to source.
# It is also a runtime fetch rather than a fixed-output derivation, because the
# upstream mirror re-publishes the file regularly and a pinned hash would break
# the build every time MaxMind refresh their data.
#
# The database is MaxMind licensed. We do not redistribute it; each developer
# and each host fetches its own copy, exactly as the Dockerfiles do at image
# build time.
set -euo pipefail

URL="${GEOIP_URL:-https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb}"

die() {
    echo "error: $*" >&2
    exit 1
}

DEST="${1:-${GEOIP_DIR:-}}"
if [ -z "${DEST}" ]; then
    root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    [ -n "${root}" ] || die "no destination given, \$GEOIP_DIR is unset and this
is not a git checkout. Pass the directory to write to, for example:
  fetch-geoip.sh /var/lib/qgisfeed/geoip"
    DEST="${root}/.nix/geoip"
fi

TARGET="${DEST}/GeoLite2-City.mmdb"

mkdir -p "${DEST}"

echo "Fetching GeoLite2-City.mmdb"
# Download to a temporary file so an interrupted transfer cannot leave a
# truncated database in place, and so a running service never reads a partial
# file: the final move is atomic within the directory.
tmp="$(mktemp "${DEST}/.GeoLite2-City.mmdb.XXXXXX")"
trap 'rm -f "${tmp}"' EXIT

curl --fail --location --silent --show-error --output "${tmp}" "${URL}"

# A valid mmdb is several megabytes; anything tiny means we fetched an error
# page or a redirect stub.
size="$(wc -c <"${tmp}")"
if [ "${size}" -lt 1000000 ]; then
    die "downloaded file is only ${size} bytes, that is not a valid mmdb database"
fi

chmod 0644 "${tmp}"
mv "${tmp}" "${TARGET}"
trap - EXIT

echo "GeoIP database installed at ${TARGET} ($((size / 1024 / 1024)) MB)"
