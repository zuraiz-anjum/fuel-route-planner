"""
Django settings for the fuel route planner.

Anything that differs between machines is read from the environment with a
development default, so a fresh checkout runs with no configuration at all.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-key-change-me-in-production")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "planner",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Geocoding results and routes are cached so that repeating a request does not
# hit the external services again. In-process memory is enough here; point this
# at Redis or memcached if the app is ever run with more than one worker.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# --- Fuel planner -----------------------------------------------------------

FUEL_PRICES_CSV = DATA_DIR / "fuel-prices.csv"
CITY_COORDINATES_CSV = DATA_DIR / "city_coordinates.csv"

OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
PHOTON_BASE_URL = os.environ.get("PHOTON_BASE_URL", "https://photon.komoot.io")
# Sent to both services; public OSM servers want to know who is calling.
GEOCODER_USER_AGENT = os.environ.get("GEOCODER_USER_AGENT", "fuelroute/1.0")
EXTERNAL_TIMEOUT_SECONDS = 15

VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10

# Stations are located by city centroid, which can sit a few miles from the
# actual truck stop, so the corridor is wider than it would be with exact
# coordinates.
ROUTE_CORRIDOR_MILES = 10

# What the planner charges itself for pulling off the highway. Without it the
# cost-optimal plan happily adds a stop to save fifty cents. Not part of the
# reported fuel cost; override per request with ?stop_cost=.
DEFAULT_STOP_COST = 10.0
