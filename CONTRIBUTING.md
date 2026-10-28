# ✨ Contributing to QGIS-Feed

Thank you for considering contributing to QGIS Feed!
We welcome contributions of all kinds, including bug fixes, feature requests,
documentation improvements, and more. Please follow the guidelines below to
ensure a smooth contribution process.

![-----------------------------------------------------](./img/green-gradient.png)


## 🧑💻 Development

For development purposes only, you can run this application in debug mode with docker compose. Some of the docker compose commands are already configured in the Makefile.

## 🏃Before you start

This project requires [Docker](https://www.docker.com/) and [Docker Compose](https://docs.docker.com/compose/) to run the development and production environments.
Please ensure both are installed on your system before proceeding.

![Docker logo](https://www.docker.com/wp-content/uploads/2022/03/Moby-logo.png)

You can check your installation with:
```bash
docker --version
docker-compose --version
```

![-----------------------------------------------------](./img/green-gradient.png)

## 🛒 Getting the Code

- Clone git repo `git clone https://github.com/qgis/QGIS-Feed-Website.git`
- Run `$ pwd` in order to get your current directory
- Path to your repo should be `<your current directory>/QGIS-Feed-Website `
- Go to dockerize directory `cd QGIS-Feed-Website`

![-----------------------------------------------------](./img/green-gradient.png)

## Pre-commit hooks

This repository uses [pre-commit](https://pre-commit.com/) to automate code quality checks before each commit. To set it up:

1. Install pre-commit (if not already installed):
    ```sh
    pip install pre-commit
    ```

2. Install the hooks defined in `.pre-commit-config.yaml`:
    ```sh
    pre-commit install
    ```

Now, the configured checks (such as linting and formatting) will run automatically when you commit changes.

![-----------------------------------------------------](./img/green-gradient.png)


### ❄️ Nix

The flake provides a complete development environment: Python, Node, and a
project-local PostgreSQL/PostGIS cluster. **Docker is not required** if you
develop this way.

```sh
nix develop                     # enter the environment (direnv users: just `cd` in)
./nix/scripts/db-start.sh       # start the local PostgreSQL/PostGIS
./nix/scripts/db-reset.sh       # create the schema and load the fixtures
./nix/scripts/fetch-geoip.sh    # download the GeoLite2 City database (once)
./nix/scripts/dev.sh            # webpack watch + Django dev server on :8000
```

Then open <http://localhost:8000>. The fixtures create the same users as the
docker path.

#### Commands

Run the helpers directly when you are inside `nix develop`; every tool they need
is already on `PATH`. The `nix run` column is for invoking them from an ordinary
shell without entering the environment first.

| Inside `nix develop` | From outside | Description |
|---|---|---|
| `./nix/scripts/db-start.sh` | `nix run .#db-start` | Start the local PostgreSQL/PostGIS cluster |
| `./nix/scripts/db-stop.sh` | `nix run .#db-stop` | Stop it |
| `./nix/scripts/db-reset.sh` | `nix run .#db-reset` | Drop and recreate the DB, migrate, load fixtures (prompts first) |
| `./nix/scripts/db-restore.sh` | `nix run .#db-restore` | Restore the DB from a `pg_dump` archive (prompts first) |
| `./nix/scripts/manage.sh <cmd>` | `nix run .#manage -- <cmd>` | Run any Django management command |
| `./nix/scripts/dev.sh` | `nix run .#dev` | Run webpack in watch mode plus the Django dev server |
| `./nix/scripts/test.sh` | `nix run .#test` | Run the Django test suite |
| `./nix/scripts/fetch-geoip.sh` | `nix run .#fetch-geoip` | Download `GeoLite2-City.mmdb` |
| `./nix/scripts/format.sh` | `nix fmt` | Format every `.nix` file in the tree |
| — | `nix flake check` | Evaluate every output and run the checks |

Both columns work from anywhere; the script form simply skips a flake
re-evaluation on every call.

#### How it is put together

- **State** lives in `.nix/` (git-ignored): the PostgreSQL cluster in
  `.nix/pgdata`, its socket in `.nix/run`, the GeoIP database in `.nix/geoip`.
  Delete the directory to start over.
- **The database** is an unprivileged cluster owned by you, listening on a unix
  socket inside the repository only. Nothing is bound to a network interface,
  so it cannot clash with a system PostgreSQL.
- **Settings** come from `qgisfeedproject/settings_nix.py`. It inherits
  `settings.py`, whose `DATABASES` block is already environment driven;
  `nix/scripts/common.sh` points `QGISFEED_DOCKER_DBHOST` at the local socket.
  (`settings_dev.py` is not reused because it hardcodes `HOST = "postgis"`,
  which only exists inside the docker compose network.)
- **Python dependencies** come from nixpkgs, except three that nixpkgs cannot
  supply at the version this project needs; those are built from real
  derivations in `nix/python-packages.nix`. There is no pip step and no
  virtualenv.
- **nixpkgs** is pinned centrally for all QGIS repositories via
  [qgis-nixpkgs-version](https://github.com/QGIS/qgis-nixpkgs-version). Bump it
  there, not here.
- **Shell helpers** are ordinary scripts in `nix/scripts/`, wrapped by the
  flake. Nothing but wiring lives in the `.nix` files.

#### Configuration (`.env`)

`nix/scripts/common.sh` reads the same git-ignored `.env` that docker compose
uses, so site configuration is defined once. `env.template` is the committed
reference. To add a variable, put it in `env.template` and in your `.env`; the
Nix helpers pick it up with no further wiring.

Precedence is **explicit shell environment > `.env` > built-in defaults**, so a
one-off override works:

```sh
QGISFEED_BACKUP_VOLUME=/mnt/other ./nix/scripts/db-restore.sh
```

The file is parsed rather than sourced, so nothing in it is executed.

Docker-only keys are ignored by the Nix path — importing them would point it at
the docker database role and at the docker settings override, which hardcodes
`MEDIA_ROOT=/shared-volume/media` and fails outside a container. The ignore list
is `_qgisfeed_env_is_docker_only` in `nix/scripts/common.sh`.

Note that `.env` also carries SMTP and social-media credentials. Loading it
exports those into your development shell, exactly as it does for docker
compose. Production does not use this path at all: the NixOS module passes
secrets via `EnvironmentFile` so they never enter the Nix store.

#### GeoIP data

The geofence feature needs `GeoLite2-City.mmdb`, which is MaxMind licensed and
therefore not committed or vendored. `./nix/scripts/fetch-geoip.sh` downloads it into
`.nix/geoip/`. Without it, location lookups silently return no result.

#### Deployment

This flake exports **no NixOS module**. The QGIS infrastructure already has a
generic `qgis.djangoApp` module that owns PostgreSQL, nginx, Metabase, ACME and
the state directories; a second module here would be a competing implementation.
The flake's job is to produce the application closure.

`nix build` gives:

| Binary | Purpose |
|---|---|
| `qgisfeed-manage` | `manage.py` wrapper - `migrate`, `collectstatic`, … |
| `qgisfeed-uwsgi` | uWSGI with the python3 plugin, speaking the uwsgi protocol to nginx |

There is deliberately no gunicorn entry point: nginx uses `uwsgiPass`, and the
docker image runs gunicorn from its own entrypoint rather than from this
closure. For a local production-like run over plain HTTP, give uWSGI a socket:
`qgisfeed-uwsgi --ini "$(nix build .#qgisfeed.uwsgiIni --no-link --print-out-paths)" --http-socket 127.0.0.1:8000`.

Both bake in `GDAL_LIBRARY_PATH`, `GEOS_LIBRARY_PATH`, `PROJ_LIB` and
`PYTHONPATH`, so GeoDjango finds its libraries in the Nix store. Never set
those from the outside.

Configuration is split the way the infrastructure already splits it:

- **Environment** for values the deployment knows: `DEBUG`,
  `DJANGO_SETTINGS_MODULE`, `MEDIA_ROOT`, `STATIC_ROOT`,
  `QGISFEED_DOCKER_DB*`, `SENTRY_DSN`, `SENTRY_RATE`. `settings.py` reads all
  of these directly, so `qgisfeedproject.settings` is a usable production
  settings module.
- **`settings_local_override.py`** for everything else, including secrets,
  selected by `DJANGO_LOCAL_SETTINGS` and applied last so it wins. See the note
  above: `.env` is not supported by the production infrastructure.

Moving a host off the container means replacing three units - the image load,
the `-manage` one-shot and the `oci-containers` service - with
`qgisfeed-manage migrate`, `qgisfeed-manage collectstatic` and `qgisfeed-uwsgi`
run from the store. PostgreSQL, nginx and Metabase are already native and need
no change; because `qgisfeed-uwsgi` speaks the uwsgi protocol, the existing
`uwsgiPass` nginx block keeps working as is.

#### What the package exposes

Application facts are attached to the derivation, so a host configuration does
not hardcode them and several hosts cannot drift apart:

| `passthru` attribute | Value |
|---|---|
| `manageProgram` | `qgisfeed-manage` |
| `uwsgiProgram` | `qgisfeed-uwsgi` |
| `wsgiModule` | `qgisfeedproject.wsgi` |
| `settingsModule` | `qgisfeedproject.settings` |
| `uwsgiIni` | base worker tuning, `nix/uwsgi.ini` |
| `fetchGeoip` | package that downloads `GeoLite2-City.mmdb` |

`uwsgiIni` carries `workers`, `cheaper` and `harakiri` but deliberately no
socket, pidfile or chdir: worker counts follow the application's memory profile
and belong to this repository, while the socket is a host decision. Append a
second `--ini`, or pass `--socket`, to add it.

`fetchGeoip` takes the destination directory as its argument and needs no
checkout, so a host can run it from a systemd timer:

```
${qgisfeed.fetchGeoip}/bin/fetch-geoip /var/lib/qgisfeed/geoip
```

#### Environment contract

Every variable `qgisfeedproject/settings.py` reads. Anything not listed here is
not configurable through the environment.

| Variable | Default | Notes |
|---|---|---|
| `DEBUG` | `True` | Set `False` in production; error pages otherwise expose settings and environment |
| `DOMAIN_NAME` | – | Comma separated; drives `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ORIGIN_WHITELIST`. `QGIS_FEED_PROD_URL` is the older name and still works |
| `MEDIA_ROOT` | `<checkout>/qgisfeedproject/media` | Uploaded files |
| `STATIC_ROOT` | `<checkout>/qgisfeedproject/static_collected` | `collectstatic` destination |
| `GEOIP_PATH` | `/var/opt/maxmind/` | Directory holding `GeoLite2-City.mmdb` |
| `DB_NAME` | `qgisfeed` | |
| `DB_USER` | `qgisfeed` | |
| `DB_PASSWORD` | empty | Not needed with peer or trust authentication |
| `DB_HOST` | `/var/run/postgresql` | A path is a unix socket directory |
| `DB_PORT` | `5432` | |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL` | see `settings.py` | |
| `EMAIL_HOST_PASSWORD` | empty | Secret |
| `SENTRY_DSN` | empty | Secret |
| `SENTRY_RATE` | `1.0` | |
| `MASTODON_API_BASE_URL`, `BLUESKY_HANDLE`, `TELEGRAM_CHAT_ID` | – | Non-secret halves of the syndication settings |
| `MASTODON_ACCESS_TOKEN`, `BLUESKY_PASSWORD`, `TELEGRAM_BOT_TOKEN` | empty | Secrets |
| `DJANGO_LOCAL_SETTINGS` | `settings_local_override.py` | Path to the settings file below; absolute paths are accepted |

The `DB_*` names replace `QGISFEED_DOCKER_DB*`, which are still accepted as a
fallback. Nothing about the deployment is docker specific any more.

`SECRET_KEY` and `QGISFEED_MAX_RECORDS` are deliberately absent: the first is a
secret and the second has no environment variable, so both come from the
settings file.

This table is enforced, not just documented - see `settings-contract` below.
Change a variable name and that check fails, rather than a deployment breaking
silently.

#### Formatting

Nix files are formatted with [nixfmt](https://github.com/NixOS/nixfmt). It is
not configurable, so there is nothing to agree on - run it and commit the
result:

```bash
nix fmt                    # the whole tree
nix fmt nix/package.nix    # one file
```

`nix fmt` goes through `nix/scripts/format.sh` rather than calling `nixfmt`
directly. `nixfmt` only accepts files, and `nix fmt` hands its formatter the
tree root as a bare `.`; given that, `nixfmt` finds no files, falls back to
reading stdin and hangs with no output. The wrapper expands directories first.

There is no pre-commit hook for this, on purpose. This is a Django application
and the flake is a utility for running it, so a commit should not require a Nix
toolchain from contributors who never touch a `.nix` file. Formatting is
enforced by the `nixfmt` flake check and by the `nix-checks` workflow on every
pull request, which is where it concerns the people actually changing those
files. The same reasoning applies to `shellcheck`.

#### Tests

`nix flake check` runs everything. The logic lives in `nix/tests/`, never in the
`.nix` files.

| Check | What it proves | Needs a database |
|---|---|---|
| `shellcheck` | Every script in `nix/scripts` and `nix/tests` is clean | no |
| `nixfmt` | The `.nix` files are formatted, even without the pre-commit hooks | no |
| `passthru-contract` | Every program, ini and module name in `passthru` exists and imports | no |
| `settings-contract` | The environment table above, including the `QGISFEED_DOCKER_*` fallback and the override precedence rule | no |
| `migration-drift` | The models match the committed migrations | no |
| `uwsgi-app-load` | The WSGI callable imports inside uWSGI's own embedded interpreter | no |
| `integration` | `migrate`, `collectstatic`, the Django suite, and one real HTTP request served by uWSGI | yes |

The first six finish in seconds. `integration` starts its own PostGIS cluster in
the build sandbox on a unix socket in `$TMPDIR`, so it cannot touch a developer's
PostgreSQL, and takes minutes. Run one on its own with:

```bash
nix build .#checks.x86_64-linux.settings-contract -L
```

Reach for that when a run fails: `nix flake check` stops at the first failing
check, so a formatting slip can mask whether the application still imports and
serves. The `nix-checks` workflow runs each check as its own job for the same
reason.

Two things worth knowing if you edit these:

- `passthru-contract` reads the program names off `passthru` rather than
  hardcoding them, so renaming one surfaces as a failure instead of a check
  that quietly tests nothing.
- `integration` installs PostGIS into `template1`. No migration runs
  `CreateExtension`, so without that the test database Django creates has no
  `geometry` type and the run dies before the first test.
- `integration` fetches `GeoLite2-City.mmdb` from the same mirror as
  `Dockerfile`, pinned by hash to a dated release tag. `signals.py` constructs
  a `GeoIP2()` on every visit and the geofencing tests assert real lookups, so
  the suite cannot run without it. The file must keep that exact name: given a
  directory, Django looks for the filenames in `GEOIP_SETTINGS` rather than
  scanning for any `.mmdb`. `Dockerfile` tracks the mirror's `download` branch,
  which is force-pushed whenever MaxMind publishes; the check pins a release
  asset instead, so the database moves only when someone picks a newer tag from
  <https://github.com/P3TERX/GeoLite.mmdb/releases> and regenerates the hash
  with `nix store prefetch-file --name GeoLite2-City.mmdb <url>`.

For a normal development run against the project-local cluster, use
`./nix/scripts/test.sh` (or `nix run .#test`) instead - it is much faster than
rebuilding the closure.

### ⚡️ Quick Start
- Build the docker the container
```bash
$ make dev-build
```

#### Environment setup
- Create `settings_local.py` int the `qgisfeedproject` directory, configure the media folder as in the example below:

```python
# Settings local for docker compose production settings
import os

MEDIA_ROOT = '/shared-volume/media/'
MEDIA_URL = '/media/'
STATIC_ROOT = '/shared-volume/static/'
STATIC_URL = '/static/'


if not os.path.exists(MEDIA_ROOT):
    os.mkdir(MEDIA_ROOT)

if not os.path.exists(STATIC_ROOT):
    os.mkdir(STATIC_ROOT)
```

- Generate the `.env` from `env.template` and edit it with your email variables:
```sh
cp env.template .env
nano .env
```
Don't forget to specify the QGISFEED_MEDIA_VOLUME with your media directory

See https://docs.djangoproject.com/en/2.2/topics/email/#module-django.core.mail for further email configuration.

- To prevent DDOS attacks there is limit in the number of returned records (defaults to 20): it can be configured by overriding the settings in `settings_local.py` with:

```python
QGISFEED_MAX_RECORDS=40  # default value is 20
```

**IMPORTANT NOTE**: For new Django variables, please use the `settings_local_override.py` as the `.env` file is not supported by the new production infrastructure.

#### Django Local Settings Override (`settings_local_override.py`)

- Create settings_local_override.py file
```bash
$ cp settings_local_override.py.templ settings_local_override.py
```

- Edit settings_local_override.py file and set your environment variables

**IMPORTANT NOTE**: As we are migrating to a declarative based infrastructure, it is preferable to use the `settings_local_override.py` file for all new Django variables. This file is ignored when commiting so please make sure you define your new variables with an example value (**NOT THE REAL ONE FOR SECRETS AND PASSWORDS**) inside the `settings_local_override.py.templ` file.

#### Spin up the development environment

- Start the docker the container
```bash
$ make dev-start
```

- Run migrations:
```bash
$ make dev-migrate
```

- Create an admin user and set a password:
```bash
$ make dev-createsuperuser
```

- Show the development server logs:
```bash
$ make dev-logs
```


A set of test data will be automatically loaded and the application will be available at http://localhost:8000

To enter the control panel http://localhost:8000/admin, two test users are available:

- Super Admin: the credentials are `admin`/`admin`
- Staff (News Entry Author): the credentials are `staff`/`staff`

</details>


![-----------------------------------------------------](./img/green-gradient.png)

## 🧪 Running Django Unit Tests
<details>
    <summary><strong>🧪 Run all tests</strong></summary>
        </br>

To run all tests cases in the qgisfeed app, from the main directory:
```sh
$ make dev-runtests
```
</details>

<details>
    <summary><strong>🧪 Run a specific test</strong></summary>
        </br>

To run each test case class in the qgisfeed app:
```sh
$ docker-compose -f docker-compose.dev.yml exec qgisfeed python qgisfeedproject/manage.py test qgisfeed.tests.QgisFeedEntryTestCase
$ docker-compose -f docker-compose.dev.yml exec qgisfeed python qgisfeedproject/manage.py test qgisfeed.tests.QgisUserVisitTestCase
$ docker-compose -f docker-compose.dev.yml exec qgisfeed python qgisfeedproject/manage.py test qgisfeed.tests.HomePageTestCase
$ docker-compose -f docker-compose.dev.yml exec qgisfeed python qgisfeedproject/manage.py test qgisfeed.tests.LoginTestCase
$ docker-compose -f docker-compose.dev.yml exec qgisfeed python qgisfeedproject/manage.py test qgisfeed.tests.FeedsItemFormTestCase
$ docker-compose -f docker-compose.dev.yml exec qgisfeed python qgisfeedproject/manage.py test qgisfeed.tests.FeedsListViewTestCase
```
</details>

## 🚀 Production Environment


<details>
    <summary><strong>⚙️ Production Environment Installation</strong></summary>
    </br>
For production, you can run this application with make commands or docker compose:

Docker configuration should be present in `.env` file in the main directory,
an example is provided in `env.template`:

```bash
# This file can be used as a template for .env
# The values in this file are also the default values.

# Host machine persistent storage directory, this path
# must be an existent directory with r/w permissions for
# the users from the Docker containers.
QGISFEED_DOCKER_SHARED_VOLUME=/shared-volume

# Number of Gunicorn workers (usually: number of cores * 2 + 1)
QGISFEED_GUNICORN_WORKERS=4

# Database name
QGISFEED_DOCKER_DBNAME=qgisfeed
# Database user
QGISFEED_DOCKER_DBUSER=docker
# Database password
QGISFEED_DOCKER_DBPASSWORD=docker
```

```bash
$ make start
```

A set of test data will be automatically loaded and the application will be available at http://localhost:80

To enter the control panel http://localhost:80/admin, two test users are available:

- Super Admin: the credentials are `admin`/`admin`
- Staff (News Entry Author): the credentials are `staff`/`staff`

### Enable SSL Certificate on production using Docker

1. Generate key using openssl in dhparam directory
```bash
openssl dhparam -out /home/web/qgis-feed/dhparam/dhparam-2048.pem 2048
```

2. Run the container
```bash
$ make start
```

3. Update `config/nginx/qgisfeed.conf` to include the new config file in `config/nginx/ssl/qgisfeed.conf`
```
include conf.d/ssl/*.conf;
```

4. Restart nginx service
```
nginx -s reload
```

5. To enable a cronjob to automatically renew ssl cert, add `scripts/renew_ssl.sh` to crontab file.

</details>

<details>
    <summary><strong>📧 Email-sending setup</strong></summary>
    </br>


- Generate the `.env` from `env.template` and edit it with the production email variables:
```sh
cp env.template .env
nano .env
```

</details>

<details>
    <summary><strong>🛠️ Troubleshooting SSL in production</strong></summary>
        </br>

Sometimes it seems our cron does not refresh the certificate. We can fix like this:

**Gentle Way**

```
ssh feed.qgis.org
cd /home/web/qgis-feed
scripts/renew_ssl.sh
```

Now check if your browser is showing the site opening with no SSL errors: https://feed.qgis.org

**More crude way**

```
ssh feed.qgis.org
cd /home/web/qgis-feed
make start c=certbot
make restart c=nginx
```

Now check if your browser is showing the site opening with no SSL errors: https://feed.qgis.org

</details>

> Please visit the private Sysadmin documentation for more details about the deployment of https://feed.qgis.org

![-----------------------------------------------------](./img/green-gradient.png)


## Backups

If something goes terribly wrong, we keep 7 nights of backups on hetzner and daily backups on a storage box.

If those are also not useful there are a collection of snapshot backups on hetzner and on a storage box

Last resort: Tim and Lova makes backups to his local machine on a semi-regular basis.


![-----------------------------------------------------](./img/green-gradient.png)
