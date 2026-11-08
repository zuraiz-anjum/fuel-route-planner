"""
Resolving "City, ST" without a geocoder.

Nearly every start and finish this API is given is a city and a state. A
table of US cities extracted from GeoNames (data/us_cities.csv, built by the
extract_cities command) answers those instantly and keeps working when the
public geocoder does not. Anything else, a street address or a bare city
name, still goes to Photon.
"""

import csv
from functools import lru_cache

from django.conf import settings

from .geonames import loose_name, normalize_name
from .photon import Place
from .states import STATE_NAMES, state_code


@lru_cache(maxsize=1)
def city_table():
    """{(normalised name, state): (lat, lon, name)}, the most populous first."""
    exact, loose = {}, {}
    with settings.US_CITIES_CSV.open(newline="", encoding="utf-8") as fh:
        rows = sorted(csv.DictReader(fh), key=lambda row: -int(row["population"]))
    for row in rows:
        place = (float(row["lat"]), float(row["lon"]), row["name"])
        exact.setdefault((normalize_name(row["name"]), row["state"]), place)
        loose.setdefault((loose_name(row["name"]), row["state"]), place)
    return exact, loose


def find_city(query):
    """A Place for "Dallas, TX", "Dallas, Texas", "Dallas TX" or "Dallas, TX, USA"; None otherwise."""
    exact, loose = city_table()
    for city, state in readings(query):
        place = exact.get((normalize_name(city), state)) or loose.get((loose_name(city), state))
        if place:
            lat, lon, name = place
            return Place(lat=lat, lon=lon, label=f"{name}, {STATE_NAMES[state]}, United States")
    return None


def readings(query):
    """The ways to read a query as a city and a state."""
    parts = [part for part in (p.strip() for p in query.split(",")) if part]
    if len(parts) > 1:
        for part in parts[1:]:
            state = state_code(part)
            if state:
                yield parts[0], state
    elif parts:
        # No comma: "Dallas TX", "Dallas Texas", "Charleston West Virginia".
        words = parts[0].split()
        for count in (2, 1):
            state = state_code(" ".join(words[-count:])) if len(words) > count else None
            if state:
                yield " ".join(words[:-count]), state
