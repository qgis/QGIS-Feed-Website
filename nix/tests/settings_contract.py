"""Assert the environment contract that settings.py publishes.

The NixOS infrastructure configures the application entirely through
environment variables and CONTRIBUTING.md documents which ones. Nothing
enforced that contract, so the infrastructure had to reverse-engineer it from
the os.environ.get calls, and renaming one here would break a deployment with
no warning at build time.

settings.py imports no Django code, so it can be loaded as a plain module and
re-loaded under a different environment. That keeps this fast and avoids
needing a database.
"""

import importlib
import os
import sys
import tempfile

MODULE = "qgisfeedproject.settings"

# Every variable the module reads. Cleared before each scenario so that one
# case cannot leak into the next, and so a variable set by the build sandbox
# cannot silently change a result.
TRACKED = (
    "DEBUG",
    "DOMAIN_NAME",
    "QGIS_FEED_PROD_URL",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_HOST",
    "DB_PORT",
    "QGISFEED_DOCKER_DBNAME",
    "QGISFEED_DOCKER_DBUSER",
    "QGISFEED_DOCKER_DBPASSWORD",
    "QGISFEED_DOCKER_DBHOST",
    "QGISFEED_DOCKER_DBPORT",
    "STATIC_ROOT",
    "MEDIA_ROOT",
    "GEOIP_PATH",
    "GDAL_LIBRARY_PATH",
    "GEOS_LIBRARY_PATH",
    "SENTRY_DSN",
    "DJANGO_LOCAL_SETTINGS",
)

failures = []


def load(**environment):
    """Import settings.py fresh under exactly the given environment."""
    for key in TRACKED:
        os.environ.pop(key, None)
    os.environ.update(environment)
    sys.modules.pop(MODULE, None)
    return importlib.import_module(MODULE)


def check(label, actual, expected):
    if actual != expected:
        failures.append(f"{label}: expected {expected!r}, got {actual!r}")


# A deployment that names no override file must still import cleanly. Pointing
# at a path that cannot exist also stops a stray file in the store from
# influencing any of the scenarios below.
NO_OVERRIDE = "/nonexistent-settings-override.py"


# --- What the NixOS deployment sets -----------------------------------------
settings = load(
    DEBUG="False",
    DOMAIN_NAME="feed.qgis.org",
    DB_NAME="qgisfeed",
    DB_USER="qgisfeed",
    DB_HOST="/var/run/postgresql",
    DB_PORT="5432",
    STATIC_ROOT="/var/lib/qgisfeed/static",
    MEDIA_ROOT="/var/lib/qgisfeed/media",
    GEOIP_PATH="/var/lib/qgisfeed/geoip",
    DJANGO_LOCAL_SETTINGS=NO_OVERRIDE,
)
check("DEBUG", settings.DEBUG, False)
check("ALLOWED_HOSTS", settings.ALLOWED_HOSTS, ["feed.qgis.org"])
check("CSRF_TRUSTED_ORIGINS", settings.CSRF_TRUSTED_ORIGINS, ["https://feed.qgis.org"])
check(
    "CORS_ORIGIN_WHITELIST", settings.CORS_ORIGIN_WHITELIST, ["https://feed.qgis.org"]
)
check("DATABASES NAME", settings.DATABASES["default"]["NAME"], "qgisfeed")
check("DATABASES HOST", settings.DATABASES["default"]["HOST"], "/var/run/postgresql")
check(
    "DATABASES ENGINE",
    settings.DATABASES["default"]["ENGINE"],
    "django.contrib.gis.db.backends.postgis",
)
check("STATIC_ROOT", settings.STATIC_ROOT, "/var/lib/qgisfeed/static")
check("MEDIA_ROOT", settings.MEDIA_ROOT, "/var/lib/qgisfeed/media")
check("GEOIP_PATH", settings.GEOIP_PATH, "/var/lib/qgisfeed/geoip")


# --- Several hostnames, comma separated -------------------------------------
settings = load(
    DOMAIN_NAME="feed.qgis.org, feed-staging.qgis.org",
    DJANGO_LOCAL_SETTINGS=NO_OVERRIDE,
)
check(
    "several ALLOWED_HOSTS",
    settings.ALLOWED_HOSTS,
    ["feed.qgis.org", "feed-staging.qgis.org"],
)


# --- The docker spelling still works ----------------------------------------
# These names predate the move off docker and the compose files still pass
# them, so they have to keep working.
settings = load(
    QGIS_FEED_PROD_URL="legacy.example.org",
    QGISFEED_DOCKER_DBNAME="legacydb",
    QGISFEED_DOCKER_DBHOST="postgis",
    DJANGO_LOCAL_SETTINGS=NO_OVERRIDE,
)
check("legacy ALLOWED_HOSTS", settings.ALLOWED_HOSTS, ["legacy.example.org"])
check("legacy DB NAME", settings.DATABASES["default"]["NAME"], "legacydb")
check("legacy DB HOST", settings.DATABASES["default"]["HOST"], "postgis")


# --- The current spelling wins over the docker one --------------------------
settings = load(
    DB_HOST="/run/postgresql",
    QGISFEED_DOCKER_DBHOST="postgis",
    DJANGO_LOCAL_SETTINGS=NO_OVERRIDE,
)
check("current name wins", settings.DATABASES["default"]["HOST"], "/run/postgresql")


# --- Defaults, with nothing configured --------------------------------------
settings = load(DJANGO_LOCAL_SETTINGS=NO_OVERRIDE)
check("default DEBUG", settings.DEBUG, True)
check("default ALLOWED_HOSTS", settings.ALLOWED_HOSTS, [])
check("default DB HOST", settings.DATABASES["default"]["HOST"], "/var/run/postgresql")
check("default DB NAME", settings.DATABASES["default"]["NAME"], "qgisfeed")
# GeoDjango falls back to searching the usual library names when these are
# unset, which is what the docker images rely on.
check("GDAL_LIBRARY_PATH unset", settings.GDAL_LIBRARY_PATH, None)
check("GEOS_LIBRARY_PATH unset", settings.GEOS_LIBRARY_PATH, None)
# Nothing replaced the committed key, so a deployment can still detect it.
check(
    "insecure default detectable",
    settings.SECRET_KEY,
    settings.INSECURE_DEFAULT_SECRET_KEY,
)


# --- The local override is applied last and wins ----------------------------
# This is the invariant the whole configuration split rests on, and it fails
# silently: if the override stopped being applied last, a deployment would run
# with the environment's value and no error anywhere.
with tempfile.TemporaryDirectory() as directory:
    override = os.path.join(directory, "override.py")
    with open(override, "w", encoding="utf-8") as handle:
        handle.write(
            "SECRET_KEY = 'from-the-override'\n"
            "MEDIA_ROOT = '/from/the/override'\n"
            "lowercase_names_are_ignored = 'yes'\n"
        )

    settings = load(
        MEDIA_ROOT="/from/the/environment",
        DJANGO_LOCAL_SETTINGS=override,
    )
    check("override SECRET_KEY", settings.SECRET_KEY, "from-the-override")
    check("override beats the environment", settings.MEDIA_ROOT, "/from/the/override")
    if hasattr(settings, "lowercase_names_are_ignored"):
        failures.append("override copied a name that is not upper case")


if failures:
    print("The settings environment contract is broken:", file=sys.stderr)
    for failure in failures:
        print(f"  - {failure}", file=sys.stderr)
    print(
        "\nIf this change is intended, update CONTRIBUTING.md and this test "
        "together: the infrastructure reads that documentation.",
        file=sys.stderr,
    )
    sys.exit(1)

print("The settings environment contract holds")
