"""
Client for the OSRM routing service.

One request per trip: the full route geometry comes back as an encoded
polyline together with the driven distance, which is all the planner needs.
Routes are cached for a while because the same demo pair tends to be asked
for repeatedly, and the public server asks not to be hammered.
"""

from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

ROUTE_CACHE_SECONDS = 3600
METRES_PER_MILE = 1609.344


class RoutingError(Exception):
    """OSRM could not be reached or returned something unusable."""


class NoRouteError(RoutingError):
    """OSRM found no road connecting the two points."""


@dataclass(frozen=True)
class RouteResult:
    distance_miles: float
    duration_seconds: float
    polyline: str


_session = requests.Session()


def fetch_route(start, finish):
    """Drive from start to finish, both (lat, lon); returns a RouteResult."""
    key = "route:%.5f,%.5f:%.5f,%.5f" % (*start, *finish)
    result = cache.get(key)
    if result is not None:
        return result

    url = "%s/route/v1/driving/%f,%f;%f,%f" % (
        settings.OSRM_BASE_URL, start[1], start[0], finish[1], finish[0],
    )
    try:
        response = _session.get(
            url,
            params={"overview": "full", "geometries": "polyline", "steps": "false", "alternatives": "false"},
            timeout=settings.EXTERNAL_TIMEOUT_SECONDS,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"routing request failed: {exc}") from exc

    # OSRM answers 400 with a code for unroutable pairs, which is an answer,
    # not an outage.
    code = body.get("code")
    if code in ("NoRoute", "NoSegment"):
        raise NoRouteError("no road route between those points")
    if code != "Ok":
        raise RoutingError(f"routing service returned {code or response.status_code}")

    route = body["routes"][0]
    result = RouteResult(
        distance_miles=route["distance"] / METRES_PER_MILE,
        duration_seconds=route["duration"],
        polyline=route["geometry"],
    )
    cache.set(key, result, ROUTE_CACHE_SECONDS)
    return result
