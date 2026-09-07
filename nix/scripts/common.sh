#!/usr/bin/env bash
# Shared environment for the Nix-based QGIS Feed development stack.
#
# Sourced by every nix/scripts/*.sh helper and by the devShell hook. Everything
# here is derived from the repository location at runtime so that no Nix store
# path is ever baked into a committed file.

# Repository root. Prefer git so the helpers work from any subdirectory.
if [ -z "${PROJECT_ROOT:-}" ]; then
    PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
    export PROJECT_ROOT
fi

# .env is the same file docker compose reads (env.template documents it). It is
# git-ignored and holds the site's own values: backup location, SMTP settings,
# social tokens, Sentry DSN. Loading it here means a variable added for docker
# is automatically available to the Nix stack too.
#
# It is parsed, not sourced: sourcing would execute whatever is in the file, and
# docker compose does not give .env shell semantics either. Precedence is
#   explicit shell environment  >  .env  >  the defaults set below
# so `QGISFEED_BACKUP_VOLUME=/tmp ./nix/scripts/db-restore.sh` still works.
#
# Keys describing the docker stack are ignored: taking them would point the
# Nix path at the docker database role and at the docker settings override
# (which hardcodes MEDIA_ROOT=/shared-volume/media and fails outside a
# container). Add to this list, not to the parser, if a new docker-only key
# appears in env.template.
_qgisfeed_env_is_docker_only() {
    case "$1" in
    DB_HOST | DB_USER | DB_PASSWORD | \
        QGISFEED_DOCKER_DBHOST | QGISFEED_DOCKER_DBUSER | QGISFEED_DOCKER_DBPASSWORD | \
        QGISFEED_DOCKER_SHARED_VOLUME | QGISFEED_DOCKER_IMAGE | METABASE_DOCKER_IMAGE | \
        DJANGO_LOCAL_SETTINGS | DJANGO_SETTINGS_MODULE | GEOIP_PATH)
        return 0
        ;;
    esac
    return 1
}

if [ -f "${PROJECT_ROOT}/.env" ]; then
    while IFS= read -r _line || [ -n "${_line}" ]; do
        # Skip blanks, comments and anything without an assignment.
        case "${_line}" in
        '' | '#'*) continue ;;
        *=*) ;;
        *) continue ;;
        esac

        _key="${_line%%=*}"
        _key="${_key# }"
        _key="${_key%% *}"
        _key="${_key#export }"
        _val="${_line#*=}"

        # Reject anything that is not a plain shell identifier.
        case "${_key}" in
        '' | *[!A-Za-z0-9_]*) continue ;;
        esac

        _qgisfeed_env_is_docker_only "${_key}" && continue

        # Strip one layer of matching quotes, or an unquoted trailing comment.
        case "${_val}" in
        \"*)
            _val="${_val#\"}"
            _val="${_val%%\"*}"
            ;;
        \'*)
            _val="${_val#\'}"
            _val="${_val%%\'*}"
            ;;
        *)
            _val="${_val%%[[:space:]]#*}"
            _val="${_val%"${_val##*[![:space:]]}"}"
            ;;
        esac

        # Only set what the caller has not already set explicitly.
        if [ -z "${!_key:-}" ]; then
            export "${_key}=${_val}"
        fi
    done <"${PROJECT_ROOT}/.env"
    unset _line _key _val
fi

# All mutable state lives under .nix/ which is git-ignored.
#
# Do NOT name this NIX_STATE_DIR: that is a reserved Nix variable naming the
# Nix static state directory (normally /nix/var/nix). Exporting it into the
# interactive shell made every nix command treat the project folder as its
# state directory, so the client stopped routing through nix-daemon and tried
# to write to the read-only /nix/store, failing with
# 'opening lock file "/nix/store/...-source.lock": Read-only file system'.
export QGISFEED_STATE_DIR="${PROJECT_ROOT}/.nix"
export PGDATA="${QGISFEED_STATE_DIR}/pgdata"
export PGSOCKET="${QGISFEED_STATE_DIR}/run"
export GEOIP_DIR="${QGISFEED_STATE_DIR}/geoip"

# Where db-restore.sh looks for dumps. QGISFEED_BACKUP_VOLUME is the name the
# docker compose files bind to /backups and is already in env.template, so a
# site that has set it in .env needs no extra configuration here.
export QGISFEED_BACKUP_DIR="${QGISFEED_BACKUP_VOLUME:-${PROJECT_ROOT}/backups}"

# Django project layout.
export DJANGO_DIR="${PROJECT_ROOT}/qgisfeedproject"
export MANAGE="${DJANGO_DIR}/manage.py"

# Uploaded media. QGISFEED_MEDIA_VOLUME is the host directory docker compose
# bind-mounts to /shared-volume/media, so the Nix path uses it directly as
# MEDIA_ROOT and both stacks see the same uploads - which also means media
# restored alongside a database dump is picked up without extra configuration.
# Unset, it falls back to the media directory in the checkout, which is where
# the repository ships the test fixture images.
# MEDIA_ROOT and STATIC_ROOT are the names settings.py reads, and the same ones
# the deployment infrastructure passes, so the dev shell uses them too rather
# than inventing a parallel QGISFEED_* spelling.
export MEDIA_ROOT="${MEDIA_ROOT:-${QGISFEED_MEDIA_VOLUME:-${DJANGO_DIR}/media}}"
export STATIC_ROOT="${STATIC_ROOT:-${QGISFEED_STATE_DIR}/static}"

# Django does not create MEDIA_ROOT itself, and an upload into a missing
# directory fails at request time. Creating it here is cheap and non
# destructive; note that a typo in QGISFEED_MEDIA_VOLUME will therefore
# silently create an empty directory rather than raise.
mkdir -p "${MEDIA_ROOT}" "${STATIC_ROOT}"

# Database connection. settings.py reads these same names, and libpq treats a
# path as a unix socket directory, so Django connects over the socket.
export DB_NAME="${DB_NAME:-qgisfeed}"
export DB_USER="${DB_USER:-$(id -un)}"
export DB_PASSWORD="${DB_PASSWORD:-}"
export DB_HOST="${PGSOCKET}"

# psql/pg_ctl use these directly.
export PGHOST="${PGSOCKET}"
export PGDATABASE="${DB_NAME}"
export PGUSER="${DB_USER}"

# Nix-specific Django settings: inherits settings.py (whose DATABASES block is
# already environment driven) and only relaxes ALLOWED_HOSTS for local work.
# settings_dev.py is deliberately NOT reused: it hardcodes HOST = "postgis",
# which only exists inside the docker compose network.
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-qgisfeedproject.settings_nix}"

# settings.py ends by loading a gitignored local override, defaulting to
# settings_local_override.py. On a machine that also runs the docker stack that
# file is the docker one: it hardcodes MEDIA_ROOT=/shared-volume/media and calls
# os.mkdir on it at import time, which raises FileNotFoundError outside the
# container and takes every manage.py command with it.
#
# Point the Nix path at its own filename instead. settings.py silently skips a
# missing override (the ImportError it raises is caught), so by default nothing
# loads and the settings.py defaults apply - MEDIA_ROOT then resolves to
# qgisfeedproject/media, which is where the repository ships the test fixtures.
# Create settings_local_override_nix.py if you want Nix-local overrides.
export DJANGO_LOCAL_SETTINGS="${DJANGO_LOCAL_SETTINGS:-settings_local_override_nix.py}"

# The GeoLite2 database is fetched on demand by fetch-geoip.sh. Django expects a
# directory, with a trailing slash.
export GEOIP_PATH="${GEOIP_DIR}/"

# Django needs to import the project package.
export PYTHONPATH="${DJANGO_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

# OIDC auth
export QGIS_AUTH_URL="${QGIS_AUTH_URL:-https://auth.qgis.org}"
export OIDC_RP_CLIENT_ID="${OIDC_RP_CLIENT_ID:-feed-qgis-org}"

# Emit an error and exit non-zero. Used by the helpers below.
die() {
    echo "error: $*" >&2
    exit 1
}
