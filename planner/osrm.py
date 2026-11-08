"""
Client for the OSRM routing service.

One request per trip. The geometry is asked for as an encoded polyline, which
puts a third of the bytes of GeoJSON on the wire for a coast-to-coast route.
Decoded one character at a time in Python that took a noticeable fraction of
a second for thirty thousand points; done over the whole string at once in
numpy it takes ten milliseconds.
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


def decode_polyline(text, precision=5):
    """Google's polyline encoding to an (n, 2) array of lat, lon, the whole string at once.

    Each number is a run of 5-bit chunks, low bits first, with bit 6 set on
    every chunk but the last; the numbers are zigzag-encoded deltas, lat and
    lon alternating.
    """
    codes = np.frombuffer(text.encode("ascii"), dtype=np.uint8).astype(np.int64) - 63
    if not len(codes):
        return np.empty((0, 2))
    ends = (codes & 0x20) == 0
    number = np.concatenate(([0], np.cumsum(ends)[:-1]))
    first = np.concatenate(([0], np.flatnonzero(ends)[:-1] + 1))
    shift = 5 * (np.arange(len(codes)) - first[number])
    values = np.zeros(int(ends.sum()), dtype=np.int64)
    np.add.at(values, number, (codes & 0x1F) << shift)
    if len(values) % 2:
        raise ValueError("polyline holds an odd number of values")
    deltas = np.where(values & 1, ~(values >> 1), values >> 1)
    scale = 10.0**precision
    return np.column_stack((np.cumsum(deltas[0::2]) / scale, np.cumsum(deltas[1::2]) / scale))


def fetch_route(start, finish):
    """Drive from start to finish, both (lat, lon)."""
    url = "%s/route/v1/driving/%f,%f;%f,%f" % (
        settings.OSRM_BASE_URL, start[1], start[0], finish[1], finish[0],
    )
    try:
        response = _session.get(
            url,
            params={"overview": "full", "geometries": "polyline", "steps": "false", "alternatives": "false"},
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
        return RouteResult(
            distance_miles=float(route["distance"]) / METRES_PER_MILE,
            duration_seconds=float(route["duration"]),
            points=decode_polyline(route["geometry"]),
        )
    except (LookupError, TypeError, ValueError, AttributeError) as exc:
        raise RoutingError("routing service returned an unexpected response") from exc
