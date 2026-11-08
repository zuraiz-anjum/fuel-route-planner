"""
Django settings for the fuel route planner.

Anything that differs between machines is read from the environment with a
development default, so a fresh checkout runs with no configuration at all.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("set DJANGO_SECRET_KEY when running with DJANGO_DEBUG=0")
    SECRET_KEY = "dev-only-key"
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
if not DEBUG:
    # Deployed behind a proxy that terminates TLS, sets X-Forwarded-Proto and
    # strips it from clients. Redirecting to HTTPS and HSTS are the proxy's job.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "planner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "fuelroute.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "fuelroute.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# WhiteNoise serves the static files, so gunicorn needs nothing in front of
# it for the admin and the browsable API; collectstatic fills STATIC_ROOT
# when the image is built.
STATIC_ROOT = BASE_DIR / "staticfiles"
# Until collectstatic has run (development, tests) WhiteNoise serves from the
# app directories instead of warning about the missing folder.
WHITENOISE_AUTOREFRESH = WHITENOISE_USE_FINDERS = not STATIC_ROOT.is_dir()
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Geocoding results and routes are cached so that repeating a request does not
# go back to the external services. Files survive restarts, which matters with
# runserver reloading on every edit; swap in Redis or memcached for a real
# deployment with several workers.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": BASE_DIR / ".cache",
    }
}

# The API is Django REST Framework. Anonymous clients get sixty requests a
# minute, which is plenty for a person and stops one script from spending
# the public OSRM server's goodwill; errors keep this project's {"error": ...}
# shape rather than the framework's {"detail": ...}. The throttle keys on the
# client address, which behind a proxy is the last entry of X-Forwarded-For
# the proxy wrote, hence NUM_PROXIES; with none, the header is ignored.
REST_FRAMEWORK = {
    "DEFAULT_THROTTLE_CLASSES": ["planner.throttling.AnonThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": "60/min"},
    "NUM_PROXIES": int(os.environ.get("NUM_PROXIES", "0")),
    "EXCEPTION_HANDLER": "planner.views.api_exception",
}

# Fuel planner.

FUEL_PRICES_CSV = DATA_DIR / "fuel-prices.csv"
CITY_COORDINATES_CSV = DATA_DIR / "city_coordinates.csv"
CITY_OVERRIDES_CSV = DATA_DIR / "city_overrides.csv"
US_CITIES_CSV = DATA_DIR / "us_cities.csv"
US_OUTLINE_JSON = DATA_DIR / "us_outline.json"
# Downloaded by geocode_cities and extract_outline on first use; not committed.
GEONAMES_ZIP = DATA_DIR / "geonames-US.zip"
NATURAL_EARTH_COUNTRIES = DATA_DIR / "ne_50m_admin_0_countries.geojson"

OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
PHOTON_BASE_URL = os.environ.get("PHOTON_BASE_URL", "https://photon.komoot.io")
# Public OSM servers want to know who is calling.
USER_AGENT = os.environ.get("USER_AGENT", "fuelroute/1.0")
# Connect and read timeouts. Three calls happen in series on a cold request,
# so each one is kept short.
EXTERNAL_TIMEOUT = (3, 10)

VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10

# Stations are located by city centroid, which can sit a few miles from the
# actual truck stop, so the corridor is wider than it would be with exact
# coordinates.
ROUTE_CORRIDOR_MILES = 10

# The departure fill-up is priced at the cheapest station this close to the start.
DEPARTURE_RADIUS_MILES = 10

# What the planner charges itself for pulling off the highway, so that it does
# not add a stop to save fifty cents. Not part of the reported fuel cost;
# override per request with ?stop_cost=.
DEFAULT_STOP_COST = 10.0
