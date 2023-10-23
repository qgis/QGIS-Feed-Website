#!/bin/bash
# Configure and start the QGIS Feed Django application
#
# Input from environment:
#       - QGISFEED_GUNICORN_WORKERS default 4: Number of Gunicorn workers
#         (usually: number of cores * 2 + 1)
#

WORKERS=${QGISFEED_GUNICORN_WORKERS:-4}
LOCKFILE="/shared-volume/setup_done.lock"

# Build the bulma CSS bundle
npm install && npm run build

cd /code/qgisfeedproject

# Wait for postgres
wait-for-it -h postgis -p 5432 -t 60
sleep 10

python manage.py migrate
python manage.py collectstatic --noinput


if [ ! -e ${LOCKFILE} ]; then
    python manage.py loaddata qgisfeed/fixtures/users.json qgisfeed/fixtures/qgisfeed.json
    touch ${LOCKFILE}
fi

gunicorn qgisfeedproject.wsgi:application --error-logfile - --timeout 120 --workers=${WORKERS} -b 0.0.0.0:8000
