"""
Putting a trip together: resolve the two places, fetch the route, find the
stations within reach of it and choose where to buy fuel.

Besides geocoding the end points, the route is the only thing fetched per
request, so a trip costs at most three external calls, and none at all when
the end points are coordinates and the route is cached.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from django.conf import settings

from .geo import Route, decode_polyline, haversine_miles, simplify
from .models import Station
from .optimizer import Candidate, plan_fuel_stops
from .osrm import fetch_route
from .photon import Place, geocode_place

COORDINATES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

# Rough boxes around the lower 48, Alaska and Hawaii. Geocoded places are
# already restricted to the US; this catches coordinates typed in directly.
US_BOXES = [
    (24.3, 49.5, -125.0, -66.9),
    (51.0, 71.6, -180.0, -129.0),
    (18.8, 22.4, -160.4, -154.7),
]

# About fifty metres: invisible on a map of a whole route, but it takes a
# cross-country polyline from tens of thousands of points down to a few thousand.
RESPONSE_GEOMETRY_TOLERANCE_MILES = 0.03


class UnknownPlace(Exception):
    def __init__(self, query):
        super().__init__(f"could not find '{query}' in the United States")
        self.query = query


class StationsNotLoaded(Exception):
    def __init__(self):
        super().__init__("no stations in the database; run manage.py load_stations")


@dataclass(frozen=True)
class StationArrays:
    """Every located station packed into arrays for vectorised distance work."""

    ids: np.ndarray
    lats: np.ndarray
    lons: np.ndarray
    prices: np.ndarray

    def __len__(self):
        return len(self.ids)


@lru_cache(maxsize=1)
def station_arrays():
    """Loaded once per process; restart the server after reloading stations."""
    rows = list(Station.objects.located().values_list("opis_id", "lat", "lon", "price"))
    ids, lats, lons, prices = zip(*rows) if rows else ((), (), (), ())
    return StationArrays(
        ids=np.array(ids, dtype=int),
        lats=np.array(lats, dtype=float),
        lons=np.array(lons, dtype=float),
        prices=np.array(prices, dtype=float),
    )


def in_united_states(lat, lon):
    return any(south <= lat <= north and west <= lon <= east for south, north, west, east in US_BOXES)


def resolve_place(query):
    """Turn user input into a Place: either 'lat,lon' or something to geocode."""
    match = COORDINATES.match(query)
    if match:
        lat, lon = float(match[1]), float(match[2])
        place = Place(lat=lat, lon=lon, label=f"{lat:.5f}, {lon:.5f}")
    else:
        place = geocode_place(query)
    if place is None or not in_united_states(place.lat, place.lon):
        raise UnknownPlace(query)
    return place


def plan_trip(start_query, finish_query, start_fuel_gallons=0.0, stop_cost=None):
    """Plan the trip and return it as a JSON-ready dict."""
    if stop_cost is None:
        stop_cost = settings.DEFAULT_STOP_COST
    range_miles = settings.VEHICLE_RANGE_MILES
    mpg = settings.VEHICLE_MPG

    stations = station_arrays()
    if not len(stations):
        raise StationsNotLoaded()

    start = resolve_place(start_query)
    finish = resolve_place(finish_query)
    leg = fetch_route((start.lat, start.lon), (finish.lat, finish.lon))
    points = decode_polyline(leg.polyline)
    route = Route(points, leg.distance_miles)

    mile, off_route = route.locate(stations.lats, stations.lons, settings.ROUTE_CORRIDOR_MILES)
    near = np.flatnonzero(np.isfinite(off_route))
    candidates = [
        Candidate(mile=float(mile[i]), price=float(stations.prices[i]), ref=int(stations.ids[i]))
        for i in near
    ]
    off_route_by_id = {int(stations.ids[i]): float(off_route[i]) for i in near}

    # Filling up before leaving is priced at whatever the closest station charges.
    from_start = haversine_miles(start.lat, start.lon, stations.lats, stations.lons)
    nearest = int(np.argmin(from_start))
    departure = Candidate(mile=0.0, price=float(stations.prices[nearest]), ref=int(stations.ids[nearest]))

    plan = plan_fuel_stops(
        candidates,
        route.total_miles,
        departure=departure,
        range_miles=range_miles,
        mpg=mpg,
        start_fuel_gallons=start_fuel_gallons,
        stop_cost=stop_cost,
    )

    details = Station.objects.in_bulk([p.candidate.ref for p in plan.purchases], field_name="opis_id")
    stops = []
    for purchase in plan.purchases:
        at_departure = purchase.candidate is departure
        stops.append(
            {
                "kind": "departure" if at_departure else "en_route",
                "mile": round(purchase.candidate.mile, 1),
                "station": details[purchase.candidate.ref].as_dict(),
                "off_route_miles": round(
                    float(from_start[nearest]) if at_departure else off_route_by_id[purchase.candidate.ref], 1
                ),
                "tank_on_arrival_gallons": round(purchase.tank_on_arrival, 2),
                "gallons": round(purchase.gallons, 2),
                "tank_on_departure_gallons": round(purchase.tank_on_departure, 2),
                "cost": round(purchase.cost, 2),
            }
        )

    return {
        "start": describe(start, start_query),
        "finish": describe(finish, finish_query),
        "route": {
            "distance_miles": round(route.total_miles, 1),
            "duration_minutes": round(leg.duration_seconds / 60),
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [round(lon, 5), round(lat, 5)]
                    for lat, lon in simplify(points, RESPONSE_GEOMETRY_TOLERANCE_MILES)
                ],
            },
        },
        "vehicle": {
            "range_miles": range_miles,
            "mpg": mpg,
            "tank_gallons": range_miles / mpg,
            "start_fuel_gallons": start_fuel_gallons,
            "stop_cost": stop_cost,
        },
        "stops": stops,
        "summary": {
            "fuel_consumed_gallons": round(route.total_miles / mpg, 2),
            "fuel_purchased_gallons": round(plan.total_gallons, 2),
            "total_fuel_cost": round(plan.total_cost, 2),
            "average_price_per_gallon": (
                round(plan.total_cost / plan.total_gallons, 3) if plan.total_gallons else None
            ),
            "tank_on_arrival_gallons": round(plan.tank_on_arrival, 2),
            "en_route_stops": sum(1 for stop in stops if stop["kind"] == "en_route"),
        },
    }


def describe(place, query):
    return {"query": query, "label": place.label, "lat": round(place.lat, 5), "lon": round(place.lon, 5)}
