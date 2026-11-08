"""
Client for the public Nominatim geocoder.

Two kinds of lookup go through here: free-text places typed by the user
("Dallas, TX", "Flagstaff") and the city/state pairs in the price file. Both
use the same request helper, which keeps to Nominatim's one request per
second and sends the identifying User-Agent its usage policy asks for.
"""

import hashlib
import threading
import time
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

PLACE_CACHE_SECONDS = 24 * 3600


class GeocodeError(Exception):
    """Nominatim could not be reached or returned something unusable."""


@dataclass(frozen=True)
class Place:
    lat: float
    lon: float
    label: str


_session = requests.Session()
_pace_lock = threading.Lock()
_last_request_at = 0.0


def _search(params):
    global _last_request_at
    with _pace_lock:
        wait = _last_request_at + 1.0 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()
    try:
        response = _session.get(
            settings.NOMINATIM_BASE_URL + "/search",
            params={"format": "jsonv2", "limit": 1, "countrycodes": "us", **params},
            headers={"User-Agent": settings.GEOCODER_USER_AGENT},
            timeout=settings.EXTERNAL_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeocodeError(f"geocoder request failed: {exc}") from exc
    if not results:
        return None
    hit = results[0]
    return Place(lat=float(hit["lat"]), lon=float(hit["lon"]), label=hit["display_name"])


def geocode_place(query):
    """Resolve free text to a Place inside the US, or None if nothing matched."""
    normalized = " ".join(query.split()).casefold()
    key = "geocode:" + hashlib.sha1(normalized.encode()).hexdigest()
    found, place = cache.get(key, (False, None))
    if not found:
        place = _search({"q": query})
        cache.set(key, (True, place), PLACE_CACHE_SECONDS)
    return place


def geocode_city(city, state):
    """Resolve a city/state pair to the centre of the settlement."""
    place = _search({"q": f"{city}, {state}", "featureType": "settlement"})
    if place is None and city[:3].casefold() == "mc ":
        # The price file writes "Mc Calla" where the map says "McCalla".
        place = _search({"q": f"Mc{city[3:]}, {state}", "featureType": "settlement"})
    return place
