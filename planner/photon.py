"""
Client for the Photon geocoder (photon.komoot.io).

Photon serves OpenStreetMap data, needs no key, answers in a fraction of a
second and asks only for fair use. Two lookups go through here: typed-in
places the city table cannot answer, and price-file towns GeoNames does not
know. Both are restricted to the United States.
"""

import hashlib
import threading
import time
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from .states import STATE_NAMES

# Places do not move, and the public server is not always there when you need it.
PLACE_CACHE_SECONDS = 30 * 24 * 3600

# Hawaii to Maine. The box also covers bits of Canada and Mexico, so hits are
# checked by country code as well.
US_BBOX = "-180,18,-65,72"

SETTLEMENTS = {"city", "town", "village", "hamlet"}

class GeocodeError(Exception):
    """Photon could not be reached or returned something unusable."""


@dataclass(frozen=True)
class Place:
    lat: float
    lon: float
    label: str


_session = requests.Session()
_pace_lock = threading.Lock()
_last_request_at = 0.0


def _get(params):
    return _session.get(
        settings.PHOTON_BASE_URL + "/api/",
        params=params,
        headers={"User-Agent": settings.USER_AGENT},
        timeout=settings.EXTERNAL_TIMEOUT,
    )


def _search(query, interval=0.0, **params):
    """Hits inside the US for a query, as (properties, lat, lon) tuples."""
    global _last_request_at
    if interval:
        with _pace_lock:
            wait = _last_request_at + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_request_at = time.monotonic()
    params = {"q": query, "limit": 5, "bbox": US_BBOX, "lang": "en", **params}
    hits = []
    try:
        response = _get(params)
        if response.status_code in (429, 503):
            # Photon answers a burst this way and is usually fine a moment later.
            time.sleep(1)
            response = _get(params)
        response.raise_for_status()
        for feature in response.json()["features"]:
            properties = feature["properties"]
            if properties.get("countrycode") == "US":
                lon, lat = feature["geometry"]["coordinates"]
                hits.append((properties, lat, lon))
    except (requests.RequestException, ValueError, LookupError, TypeError) as exc:
        raise GeocodeError("geocoding service unavailable") from exc
    return hits


def _label(properties):
    parts = []
    for key in ("name", "city", "state", "country"):
        value = properties.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts)


def geocode_place(query):
    """Resolve free text to a Place inside the US, or None if nothing matched."""
    normalized = " ".join(query.split()).casefold()
    key = "geocode:" + hashlib.sha1(normalized.encode()).hexdigest()
    found, place = cache.get(key, (False, None))
    if not found:
        hits = _search(query)
        place = Place(lat=hits[0][1], lon=hits[0][2], label=_label(hits[0][0])) if hits else None
        cache.set(key, (True, place), PLACE_CACHE_SECONDS)
    return place


def geocode_city(city, state, interval=0.0):
    """Resolve a city/state pair to the centre of the settlement, or None."""
    state_name = STATE_NAMES[state]
    hits = [
        (properties, lat, lon)
        for properties, lat, lon in _search(f"{city}, {state_name}", interval, osm_tag="place")
        if properties.get("state") in (state_name, state)
    ]
    # A town beats a suburb or neighbourhood of the same name elsewhere in the state.
    hits.sort(key=lambda hit: hit[0].get("osm_value") not in SETTLEMENTS)
    if not hits:
        return None
    properties, lat, lon = hits[0]
    return Place(lat=lat, lon=lon, label=_label(properties))
