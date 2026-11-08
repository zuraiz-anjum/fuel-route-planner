"""
Reading the data files under data/.

The price file lists one row per price observation, so a station can appear
several times under slightly different names. Rows are collapsed to one record
per OPIS truckstop id with the mean of its prices. Canadian stations are
dropped; the planner only routes within the United States.

The coordinates file is produced by the geocode_cities command and maps each
distinct city/state pair in the price file to a point.
"""

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.conf import settings

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)


@dataclass(frozen=True)
class StationRecord:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    rack_id: int
    price: Decimal
    price_count: int


def normalize_city(city):
    """Collapse the padding and double spaces the file is full of."""
    return " ".join(city.split())


def city_key(city, state):
    """The key under which a city's coordinates are cached."""
    return normalize_city(city).casefold(), state.strip().upper()


def read_stations(path=None):
    """Return one StationRecord per US station, in file order."""
    path = Path(path or settings.FUEL_PRICES_CSV)
    rows_by_id = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            state = row["State"].strip().upper()
            if state not in US_STATES:
                continue
            rows_by_id[int(row["OPIS Truckstop ID"])].append(row)

    stations = []
    for opis_id, rows in rows_by_id.items():
        first = rows[0]
        prices = [Decimal(r["Retail Price"].strip()) for r in rows]
        names = Counter(r["Truckstop Name"].strip() for r in rows)
        stations.append(
            StationRecord(
                opis_id=opis_id,
                name=names.most_common(1)[0][0],
                address=first["Address"].strip(),
                city=normalize_city(first["City"]),
                state=first["State"].strip().upper(),
                rack_id=int(first["Rack ID"]),
                price=sum(prices) / len(prices),
                price_count=len(prices),
            )
        )
    return stations


def read_city_coordinates(path=None):
    """Return {city_key: (lat, lon) or None} from the geocoding cache file."""
    path = Path(path or settings.CITY_COORDINATES_CSV)
    coordinates = {}
    if not path.exists():
        return coordinates
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = city_key(row["city"], row["state"])
            if row["lat"]:
                coordinates[key] = (float(row["lat"]), float(row["lon"]))
            else:
                coordinates[key] = None
    return coordinates
