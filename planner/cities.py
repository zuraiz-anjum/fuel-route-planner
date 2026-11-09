"""
Resolving "City, ST" without a geocoder.

Nearly every start and finish this API is given is a city and a state. A
table of US cities extracted from GeoNames (data/us_cities.csv, built by the
extract_cities command) answers those instantly and keeps working when the
public geocoder does not. Anything else, a street address or a bare city
name, still goes to Photon. The same table is what the map page offers as
you type, so a place picked there resolves to the same spot.
"""

import csv
from collections import namedtuple
from functools import lru_cache

from django.conf import settings

from .geonames import loose_name, normalize_name
from .photon import Place
from .states import STATE_NAMES, state_code

City = namedtuple("City", "lat lon name state population")


@lru_cache(maxsize=1)
def city_table():
    """{(normalised name, state): City} twice, by exact and by loose name; the most populous first."""
    exact, loose = {}, {}
    with settings.US_CITIES_CSV.open(newline="", encoding="utf-8") as fh:
        rows = sorted(csv.DictReader(fh), key=lambda row: -int(row["population"]))
    for row in rows:
        if "," in row["name"]:
            # Cannot be written as "City, ST". GeoNames has one, a hospital campus.
            continue
        city = City(float(row["lat"]), float(row["lon"]), row["name"], row["state"], int(row["population"]))
        exact.setdefault((normalize_name(city.name), city.state), city)
        loose.setdefault((loose_name(city.name), city.state), city)
    return exact, loose


def find_city(query):
    """A Place for "Dallas, TX", "Dallas, Texas", "Dallas TX" or "Dallas, TX, USA"; None otherwise."""
    exact, loose = city_table()
    for name, state in readings(query):
        city = exact.get((normalize_name(name), state)) or loose.get((loose_name(name), state))
        if city:
            return Place(lat=city.lat, lon=city.lon, label=f"{city.name}, {STATE_NAMES[state]}, United States")
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


def suggest(query, limit=8):
    """Up to `limit` cities for a partly typed place, as "Dallas, TX" Places.

    "dal" lists the cities whose names begin that way, the most populous
    first and an exact name ahead of a longer one. "dallas, t" keeps the
    states that begin with T; "dallas tx" and "dallas, texas" keep Texas.
    Sixteen thousand comparisons per reading, a millisecond or two.
    """
    exact, _ = city_table()
    found = {}
    for prefix, states in _readings_of_a_prefix(query):
        if not prefix:
            continue
        for (folded, state), city in exact.items():
            if folded.startswith(prefix) and (states is None or state in states):
                found.setdefault((folded, state), (folded != prefix, -city.population, city.name, city))
    offered, spots = [], set()
    for *_, city in sorted(found.values(), key=lambda item: item[:3]):
        # "New York" and "New York City" are one row twice over; offer the spot once.
        if (city.state, city.lat, city.lon) not in spots:
            spots.add((city.state, city.lat, city.lon))
            offered.append(Place(lat=city.lat, lon=city.lon, label=f"{city.name}, {city.state}"))
    return offered[:limit]


def _readings_of_a_prefix(query):
    """(folded name prefix, states it may be in or None for any) for each way to read what was typed."""
    parts = [part.strip() for part in query.split(",")]
    if len(parts) > 1:
        # After the comma, any state whose code or name begins with what is there so far.
        typed = parts[1]
        states = None
        if typed:
            states = {
                code for code, name in STATE_NAMES.items()
                if code.startswith(typed.upper()) or name.casefold().startswith(typed.casefold())
            }
        yield normalize_name(parts[0]), states
    else:
        yield normalize_name(parts[0]), None
        for name, state in readings(parts[0]):
            yield normalize_name(name), {state}
