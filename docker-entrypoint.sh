#!/bin/sh
# Get the database ready, then hand over to whatever the image was told to run.
set -e

if [ -z "$DJANGO_SECRET_KEY" ]; then
    # Nothing here signs anything a client keeps, so a key that changes on
    # every start costs nothing; set one to stay logged in to the admin
    # across restarts.
    DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
    export DJANGO_SECRET_KEY
fi

python manage.py migrate --noinput
python manage.py load_stations

exec "$@"
