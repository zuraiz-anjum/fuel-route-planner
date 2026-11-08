"""
Client for the OSRM routing service.

One request per trip. The geometry is asked for as GeoJSON rather than an
encoded polyline: the json module parses thirty thousand coordinate pairs in
C in a few milliseconds, where decoding a polyline of that length in Python
took a noticeable fraction of a second.
"""

from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings

METRES_PER_MILE = 1609.344


class RoutingError(Exception):
    """OSRM could not be reached or returned something unusable."""


class NoRouteError(RoutingError):
    """OSRM found no road connecting the two points."""


@dataclass(frozen=True)
class RouteResult:
    distance_miles: float
    duration_seconds: float
    points: np.ndarray  # (n, 2) array of lat, lon along the road


_session = requests.Session()


def fetch_route(start, finish):
    """Drive from start to finish, both (lat, lon)."""
    url = "%s/route/v1/driving/%f,%f;%f,%f" % (
        settings.OSRM_BASE_URL, start[1], start[0], finish[1], finish[0],
    )
    try:
        response = _session.get(
            url,
            params={"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "false"},
            headers={"User-Agent": settings.USER_AGENT},
            timeout=settings.EXTERNAL_TIMEOUT,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError("routing service unavailable") from exc

    # OSRM answers 400 with a code for unroutable pairs, which is an answer,
    # not an outage.
    code = body.get("code") if isinstance(body, dict) else None
    if code in ("NoRoute", "NoSegment"):
        raise NoRouteError("no road route between those points")
    if code != "Ok":
        raise RoutingError(f"routing service returned {code or response.status_code}")
    try:
        route = body["routes"][0]
        points = np.asarray(route["geometry"]["coordinates"], dtype=float).reshape(-1, 2)[:, ::-1]
        return RouteResult(
            distance_miles=float(route["distance"]) / METRES_PER_MILE,
            duration_seconds=float(route["duration"]),
            points=np.ascontiguousarray(points),
        )
    except (LookupError, TypeError, ValueError) as exc:
        raise RoutingError("routing service returned an unexpected response") from exc
