"""Settings for the Nix development environment.

Almost everything is inherited. settings.py reads the database connection,
MEDIA_ROOT, STATIC_ROOT, GEOIP_PATH and the GDAL and GEOS library paths from
the environment, and nix/scripts/common.sh exports all of them pointing at the
project-local cluster and state directory - so this module only has to relax
ALLOWED_HOSTS for local browsing.

settings_dev.py is deliberately not reused: it hardcodes HOST = "postgis",
which only exists inside the docker compose network.
"""

from .settings import *  # noqa: F401,F403

# The dev server is reached as localhost or 127.0.0.1 depending on the browser.
ALLOWED_HOSTS = ["*"]
