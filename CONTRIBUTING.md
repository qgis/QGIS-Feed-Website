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

The flake gives you the whole development stack: Python, Node, and a
project-local PostgreSQL with PostGIS. You do not need Docker if you work this
way.

```sh
nix develop     # direnv users just need to cd into the checkout
dev             # starts the database, webpack and the site on :8000
```

`dev` is the only command you need to run the site. It brings up PostgreSQL,
downloads the GeoIP database if it is missing, applies the migrations, builds
the webpack bundles and starts the development server, in that order.

Run `db-reset` once to load the fixtures, which create the same users as the
Docker path. Then open <http://localhost:8000>.

The shell prints what it provides on entry, and `dev-help` shows it again:

| Command | What it does |
|---|---|
| `dev` | Start the database, webpack and the development server |
| `manage` | Run a Django management command |
| `tests` | Run the Django test suite |
| `db-reset` | Drop and recreate the database, migrate, load the fixtures |
| `db-restore` | Restore the database from a `pg_dump` archive |
| `fetch-geoip` | Download the GeoLite2 City database |

`dev` only exists inside the shell, and its processes inherit the environment
from it. It is deliberately not a flake output, because one started without
that environment would fail.

`nix build .#qgisfeed` works from anywhere. `nix fmt` formats the Nix and shell
files and lints the Python with ruff, all through treefmt. Python formatting
belongs to the pre-commit hook, which runs black and isort over the files you
touch.

`nix flake check` runs three things: the Django test suite against a PostGIS
cluster in the build sandbox, the application closure under systemd in a NixOS
virtual machine, and treefmt in check mode. That is also what CI runs. The
virtual machine check needs KVM, so it is Linux only.

#### How it is put together

- **The stack** is a [process-compose](https://github.com/F1bonacc1/process-compose)
  configuration, with PostgreSQL coming from
  [services-flake](https://github.com/juspay/services-flake). Nothing here
  starts or stops a database by hand. Ctrl-C stops everything.
- **State** lives in `.nix/`, which is git-ignored: the cluster in
  `.nix/pgdata`, its socket in `.nix/run`, the GeoIP database in `.nix/geoip`.
  Delete the directory to start over.
- **The database** is an unprivileged cluster owned by you, and Django connects
  as your own user, because the cluster has no other role. It listens on a
  unix socket inside the repository and binds no network interface, so it
  cannot clash with a system PostgreSQL or be reached from another machine.
  PostGIS is installed into `template1`, so the throwaway database the Django
  test runner creates inherits it.
- **Settings** come from `qgisfeedproject/settings_nix.py`, which inherits
  `settings.py`, relaxes `ALLOWED_HOSTS` and picks the database role.
  Everything else arrives through the environment the dev shell exports.
- **Python dependencies** come from nixpkgs, except three that nixpkgs cannot
  supply at the version this project needs. Those are built from real
  derivations in `nix/python-packages.nix`. There is no pip step and no
  virtualenv.
- **nixpkgs** is pinned centrally for every QGIS repository through
  [qgis-nixpkgs-version](https://github.com/QGIS/qgis-nixpkgs-version). Bump it
  there, not here.
- **Shell helpers** are ordinary scripts in `nix/scripts/`, wrapped by the
  flake. Nothing but wiring lives in the `.nix` files.
- **The flake is split by output.** `flake.nix` holds the inputs and nothing
  else, and each output class lives in its own file:

  | File | Produces |
  |---|---|
  | `flake/packages.nix` | The derivations, and the values the other parts share |
  | `flake/checks.nix` | Everything `nix flake check` runs |
  | `flake/shells.nix` | The development shell |
  | `flake/formatter.nix` | `nix fmt` |

  `flake/` wires outputs together. `nix/` holds the things that build, which
  includes `nix/dev-stack.nix`, the process-compose definition behind `dev`. To
  add a helper you only need `flake/shells.nix` and `nix/scripts/`.

The flake supports `x86_64-linux` and `aarch64-darwin`. nixpkgs has dropped
`x86_64-darwin`, and nothing here needs `aarch64-linux`.

#### Configuration

The dev stack runs with no configuration at all. When you do want to change
something, there is one file, and you create it by copying the template:

```sh
cd qgisfeedproject/qgisfeedproject
cp settings_local.py.templ settings_local.py
```

`settings_local.py` is git-ignored, and `settings.py` applies it last, so
anything you set there wins. Every value in the template is commented out, so
a fresh copy changes nothing until you edit it.

The two settings worth knowing about let you work against a copy of the live
site:

```python
MEDIA_ROOT = '/home/you/data/feed.qgis.org/media'
QGISFEED_BACKUP_DIR = '/home/you/data/feed.qgis.org/dbbackups'
```

Then `db-restore` finds `latest-qgisfeed.dmp` in that directory, and the
uploads the restored rows refer to resolve against the same media. Secrets go
here too: SMTP, the social tokens, the OIDC client secret. Keeping them in a
settings file rather than the environment means they are not exported into
every shell you open.

The Docker stack's `.env` is deliberately not read. It sets
`DJANGO_LOCAL_SETTINGS` to the Docker override, which calls `os.mkdir` on a
path that exists only inside a container and takes every management command
down with it.

Production uses neither. Secrets reach the systemd unit through
`EnvironmentFile`, so they never enter the world-readable Nix store.

#### GeoIP data

The geofence feature needs `GeoLite2-City.mmdb`. It is MaxMind licensed, so we
neither commit nor vendor it. `dev` fetches it on first start, and
`fetch-geoip` does it on demand. Delete the file to get a fresh copy. Without
it, location lookups return no result.

#### Deployment

The flake exports no NixOS module. The QGIS infrastructure already has a
generic `qgis.djangoApp` module that owns PostgreSQL, nginx, Metabase, ACME and
the state directories, and a second module here would compete with it. This
flake's job is to produce the application closure.

`nix build` gives you two entry points:

| Binary | Purpose |
|---|---|
| `qgisfeed-manage` | The `manage.py` wrapper, for `migrate` and `collectstatic` |
| `qgisfeed-uwsgi` | uWSGI with the python3 plugin, speaking the uwsgi protocol to nginx |

There is deliberately no gunicorn entry point. nginx uses `uwsgiPass`, and the
Docker image runs gunicorn from its own entrypoint rather than from this
closure.

The package publishes a `passthru` set so a deployment reads the program names,
the WSGI module and the base uWSGI tuning off the derivation instead of
restating them. The `nixos-service` check boots a virtual machine and runs the
closure under systemd, which is the only place a unit that cannot write its
state directory or reach PostgreSQL would show up before a host does.

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
$ cp settings_local.py.templ settings_local_override.py
```

- Edit settings_local_override.py file and set your environment variables

**IMPORTANT NOTE**: As we are migrating to a declarative based infrastructure, it is preferable to use the `settings_local_override.py` file for all new Django variables. This file is ignored when commiting so please make sure you define your new variables with an example value (**NOT THE REAL ONE FOR SECRETS AND PASSWORDS**) inside the `settings_local.py.templ` file.

#### Where each setting comes from

The application reads its configuration from two places, and it helps to know
which one owns a given setting.

The **environment** owns anything that changes between a laptop, the docker
stack and production, and that is not a secret. `settings.py` reads these:

| Variable | Default | What it does |
|---|---|---|
| `DEBUG` | `True` | Turn it off anywhere you would not want a stack trace on screen. |
| `DOMAIN_NAME` | empty | Public hostnames, comma separated. `CSRF_TRUSTED_ORIGINS` is derived from it. |
| `QGIS_FEED_PROD_URL` | empty | The older name for the same thing, still read as a fallback. |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | `qgisfeed`, `qgisfeed`, empty, `/var/run/postgresql`, `5432` | The database connection. The `QGISFEED_DOCKER_*` names still work as fallbacks. |
| `STATIC_ROOT` | `qgisfeedproject/static_collected` | Where `collectstatic` writes. |
| `MEDIA_ROOT` | `qgisfeedproject/media` | Where uploads are kept. |
| `GEOIP_PATH` | `/var/opt/maxmind/` | The directory holding `GeoLite2-City.mmdb`. |
| `GDAL_LIBRARY_PATH`, `GEOS_LIBRARY_PATH` | unset | Explicit paths to libgdal and libgeos. Leave them unset unless the usual library search fails. |

The default database host is a path, which libpq reads as a unix socket
directory. That suits a local PostgreSQL, so the compose files pass a hostname
of their own.

**`settings_local_override.py`** owns the secrets and the handful of settings
that have no environment variable. It is applied last, so anything you name in
it wins over the environment, including an empty string. Only list the settings
you mean to set. See `settings_local.py.templ` for the full list.

`SECRET_KEY` deserves a note. The value in `settings.py` is committed, so it is
public, and anyone can forge a session or a password reset token against a site
still using it. It is named `INSECURE_DEFAULT_SECRET_KEY` so a deployment can
compare `SECRET_KEY` against it and refuse to start. Django's own deployment
check will not catch this for you, because the committed key is long and varied
enough to pass `security.W009`.

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
