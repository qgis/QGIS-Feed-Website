"""Settings for the Nix development environment.

Almost everything is inherited. settings.py reads the database connection and
the paths from the environment, which the dev shell exports, so this module
only has to relax ALLOWED_HOSTS, own the database role and say where db-restore
looks.

settings_dev.py is deliberately not reused: it hardcodes HOST = "postgis",
which exists only inside the docker compose network.
"""

import getpass
import os

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR, DATABASES, apply_local_settings_override

# The dev server answers on localhost or 127.0.0.1 depending on the browser.
ALLOWED_HOSTS = ["*"]

# The cluster is initialised with whoever created it as its only role, so
# connect as them rather than as the "qgisfeed" settings.py defaults to.
DATABASES["default"]["USER"] = os.environ.get("DB_USER") or getpass.getuser()

# Where db-restore looks for a dump. Point this at a copy of the live backups
# in settings_local.py.
QGISFEED_BACKUP_DIR = os.path.join(os.path.dirname(BASE_DIR), "backups")

# Applied again so settings_local.py still has the last word over the lines
# above.
apply_local_settings_override(globals())
