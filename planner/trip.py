"""
Putting a trip together: resolve the two places, fetch the route, find the
stations within reach of it and choose where to buy fuel.

Besides geocoding the end points, the route is the only thing fetched per
request, so a trip costs at most three external calls. "City, ST" and
coordinates are resolved locally, so for most requests it is one call, and
none once the route is cached.
"""

import re
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max

from .cities import find_city
from .geo import Route, haversine_miles, simplify
from .models import Station
from .optimizer import Candidate, plan_fuel_stops
from .osrm import fetch_route
from .photon import Place, geocode_place

ROUTE_CACHE_SECONDS = 24 * 3600

COORDINATES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

# Rough boxes around the lower 48, Alaska and Hawaii. Geocoded places are
# already restricted to the US; this is a sanity check on coordinates typed
# in directly, not a border.
US_BOXES = [
    (24.3, 49.5, -125.0, -66.9),
    (51.0, 71.6, -180.0, -129.0),
    (18.8, 22.4, -160.4, -154.7),
]

# About fifty metres: invisible on a map of a whole route, but it takes a
# cross-country polyline from tens of thousands of points down to a few thousand.
RESPONSE_GEOMETRY_TOLERANCE_MILES = 0.03

# "Dallas" and "Dallas, TX" resolve to slightly different points; a trip
# between them is not a trip. Checked after geocoding, since the two inputs
# can be spelt quite differently and still mean the same place.
SAME_PLACE_MILES = 0.5

# With no station within this distance of the start there is nowhere to fill
# up before leaving, and the caller has to say how much fuel is in the tank.
DEPARTURE_REACH_MILES = 100


class UnknownPlace(Exception):
    def __init__(self, query):
        super().__init__(f"could not find '{query}' in the United States")
        self.query = query


class StationsNotLoaded(Exception):
    def __init__(self):
        super().__init__("no stations in the database; run manage.py load_stations")


class SamePlace(Exception):
    def __init__(self):
        super().__init__("start and finish are the same place")


class NoStationNearStart(Exception):
    def __init__(self):
        super().__init__(
            f"no fuel station within {DEPARTURE_REACH_MILES} miles of the start; "
            "pass start_fuel to leave with fuel already in the tank"
        )


@dataclass(frozen=True)
class StationArrays:
    """Every located station packed into arrays for vectorised distance work."""

    ids: np.ndarray
    lats: np.ndarray
    lons: np.ndarray
    prices: np.ndarray

    def __len__(self):
        return len(self.ids)


@dataclass(frozen=True)
class Leg:
    """A fetched route with the derived geometry the planner needs."""

    route: Route
    duration_seconds: float
    geometry: list  # simplified (lat, lon) points for the response


_stations = (None, None)


def station_arrays():
    """The station table as arrays, rebuilt whenever a row changes."""
    global _stations
    version = tuple(Station.objects.aggregate(rows=Count("id"), changed=Max("updated_at")).values())
    if _stations[0] != version:
        rows = list(Station.objects.located().order_by().values_list("opis_id", "lat", "lon", "price"))
        ids, lats, lons, prices = zip(*rows) if rows else ((), (), (), ())
        arrays = StationArrays(
            ids=np.array(ids, dtype=int),
            lats=np.array(lats, dtype=float),
            lons=np.array(lons, dtype=float),
            prices=np.array(prices, dtype=float),
        )
        _stations = (version, arrays)
    return _stations[1]


def in_united_states(lat, lon):
    return any(south <= lat <= north and west <= lon <= east for south, north, west, east in US_BOXES)


def resolve_place(query):
    """Turn user input into a Place: 'lat,lon', 'City, ST' or something to geocode."""
    match = COORDINATES.match(query)
    if match:
        lat, lon = float(match[1]), float(match[2])
        place = Place(lat=lat, lon=lon, label=f"{lat:.5f}, {lon:.5f}")
    else:
        place = find_city(query) or geocode_place(query)
    if place is None or not in_united_states(place.lat, place.lon):
        raise UnknownPlace(query)
    return place


def load_leg(start, finish):
    """The route between two places, cached so that repeats cost nothing."""
    key = "route:%.5f,%.5f:%.5f,%.5f" % (start.lat, start.lon, finish.lat, finish.lon)
    leg = cache.get(key)
    if leg is None:
        result = fetch_route((start.lat, start.lon), (finish.lat, finish.lon))
        leg = Leg(
            route=Route(result.points, result.distance_miles),
            duration_seconds=result.duration_seconds,
            geometry=simplify(result.points, RESPONSE_GEOMETRY_TOLERANCE_MILES),
        )
        cache.set(key, leg, ROUTE_CACHE_SECONDS)
    return leg


def departure_station(start, stations):
    """Where to fill up before leaving, and how far it is from the start."""
    # The cheapest station close by, which is what a driver would choose;
    # failing that the nearest one within reason, as a price to plan with.
    distance = haversine_miles(start.lat, start.lon, stations.lats, stations.lons)
    close = np.flatnonzero(distance <= settings.DEPARTURE_RADIUS_MILES)
    if len(close):
        i = int(close[np.argmin(stations.prices[close])])
    elif distance.min() <= DEPARTURE_REACH_MILES:
        i = int(np.argmin(distance))
    else:
        return None, None
    return Candidate(mile=0.0, price=float(stations.prices[i]), ref=int(stations.ids[i])), float(distance[i])


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
    if haversine_miles(start.lat, start.lon, finish.lat, finish.lon) < SAME_PLACE_MILES:
        raise SamePlace()
    leg = load_leg(start, finish)
    route = leg.route

    mile, off_route = route.locate(stations.lats, stations.lons, settings.ROUTE_CORRIDOR_MILES)
    candidates = [
        Candidate(
            mile=float(mile[i]),
            price=float(stations.prices[i]),
            ref=int(stations.ids[i]),
            off_route_miles=float(off_route[i]),
        )
        for i in np.flatnonzero(np.isfinite(off_route))
    ]
    departure, departure_distance = departure_station(start, stations)
    if departure is None and not start_fuel_gallons:
        raise NoStationNearStart()

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
    previous_mile = 0.0
    for purchase in plan.purchases:
        candidate = purchase.candidate
        at_departure = candidate is departure
        off_route_miles = departure_distance if at_departure else candidate.off_route_miles
        stops.append(
            {
                "kind": "departure" if at_departure else "en_route",
                "mile": round(candidate.mile, 1),
                "leg_miles": round(candidate.mile - previous_mile, 1),
                "station": details[candidate.ref].as_dict(),
                "off_route_miles": round(off_route_miles, 1),
                "tank_on_arrival_gallons": round(purchase.tank_on_arrival, 2),
                "gallons": round(purchase.gallons, 2),
                "tank_on_departure_gallons": round(purchase.tank_on_departure, 2),
                "cost": round(purchase.cost, 2),
            }
        )
        previous_mile = candidate.mile

    # Totals are sums of the figures shown, so the arithmetic checks to the cent.
    total_gallons = round(sum(stop["gallons"] for stop in stops), 2)
    total_cost = round(sum(stop["cost"] for stop in stops), 2)

    return {
        "start": describe(start, start_query),
        "finish": describe(finish, finish_query),
        "route": {
            "distance_miles": round(route.total_miles, 1),
            "duration_minutes": round(leg.duration_seconds / 60),
            "geometry": {
                "type": "LineString",
                "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in leg.geometry],
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
            "fuel_purchased_gallons": total_gallons,
            "total_fuel_cost": total_cost,
            "average_price_per_gallon": round(total_cost / total_gallons, 3) if total_gallons else None,
            "tank_on_arrival_gallons": round(plan.tank_on_arrival, 2),
            "en_route_stops": sum(1 for stop in stops if stop["kind"] == "en_route"),
        },
    }


def describe(place, query):
    return {"query": query, "label": place.label, "lat": round(place.lat, 5), "lon": round(place.lon, 5)}
