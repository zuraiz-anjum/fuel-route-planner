"""
Geometry on route polylines.

OSRM returns a route as an encoded polyline plus its length. Everything the
planner needs from that shape lives here: mileage along the route, where a
station sits relative to it, and a lighter copy of the line to send back.

Stations are matched to the nearest point of a dense sampling of the route
rather than projected onto each segment. With a one mile step that is at most
half a mile off along the route, well inside the error of placing a station
at its city centroid, and it turns the search into a matrix product. The
product is taken over unit vectors on the sphere, whose dot product orders
samples exactly as great-circle distance does, so haversine only has to be
evaluated for the winning sample.
"""

import math

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
MILES_PER_DEGREE = EARTH_RADIUS_MILES * math.pi / 180

# Upper bound on the cells of one point x sample matrix in locate(); 500k
# float64 cells is 4 MB.
LOCATE_CHUNK_CELLS = 500_000


def _next_value(encoded, index):
    """Read one signed value starting at index; return it and the next index."""
    result = shift = 0
    while True:
        byte = ord(encoded[index]) - 63
        index += 1
        result |= (byte & 0x1F) << shift
        shift += 5
        if byte < 0x20:
            break
    if result & 1:
        return ~(result >> 1), index
    return result >> 1, index


def decode_polyline(encoded, precision=5):
    """Decode a Google/OSRM encoded polyline into a list of (lat, lon) tuples."""
    factor = 10.0**precision
    points = []
    index = lat = lon = 0
    while index < len(encoded):
        dlat, index = _next_value(encoded, index)
        dlon, index = _next_value(encoded, index)
        lat += dlat
        lon += dlon
        points.append((lat / factor, lon / factor))
    return points


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles. Accepts floats or numpy arrays and broadcasts."""
    lat1, lon1, lat2, lon2 = (np.radians(v) for v in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    # Rounding can push a fraction past 1 for antipodal points.
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.minimum(a, 1.0)))


def _unit_vectors(lats, lons):
    """Points on the unit sphere as an (n, 3) array."""
    lat = np.radians(lats)
    lon = np.radians(lons)
    return np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))


class Route:
    """A route polyline with its cumulative mileage."""

    def __init__(self, points, total_miles=None):
        coords = np.asarray(points, dtype=float)
        self.lats = coords[:, 0]
        self.lons = coords[:, 1]
        legs = haversine_miles(self.lats[:-1], self.lons[:-1], self.lats[1:], self.lons[1:])
        self.miles = np.concatenate(([0.0], np.cumsum(legs)))
        length = self.miles[-1]
        if total_miles is not None and length > 0:
            # Dividing first makes the last vertex land on total_miles exactly.
            self.miles = self.miles / length * total_miles

    @property
    def total_miles(self):
        return float(self.miles[-1])

    def sample(self, step_miles=1.0):
        """Points every step_miles along the route: (lats, lons, miles) arrays, both ends included."""
        total = self.total_miles
        count = math.ceil(total / step_miles)
        miles = np.append(np.arange(count) * step_miles, total)
        lats = np.interp(miles, self.miles, self.lats)
        lons = np.interp(miles, self.miles, self.lons)
        return lats, lons, miles

    def locate(self, lats, lons, within_miles, step_miles=1.0):
        """Project points onto the route: (mile, distance) arrays, distance is inf beyond within_miles."""
        lats = np.asarray(lats, dtype=float)
        lons = np.asarray(lons, dtype=float)
        mile = np.zeros(len(lats))
        distance = np.full(len(lats), np.inf)
        route_lats, route_lons, route_miles = self.sample(step_miles)

        pad_lat = within_miles / MILES_PER_DEGREE
        widest_lat = min(np.abs(route_lats).max() + pad_lat, 89.0)
        pad_lon = within_miles / (MILES_PER_DEGREE * math.cos(math.radians(widest_lat)))
        near = (
            (lats >= route_lats.min() - pad_lat)
            & (lats <= route_lats.max() + pad_lat)
            & (lons >= route_lons.min() - pad_lon)
            & (lons <= route_lons.max() + pad_lon)
        )
        candidates = np.flatnonzero(near)

        route_vectors = _unit_vectors(route_lats, route_lons).T
        chunk = max(1, LOCATE_CHUNK_CELLS // len(route_miles))
        for start in range(0, len(candidates), chunk):
            idx = candidates[start : start + chunk]
            nearest = (_unit_vectors(lats[idx], lons[idx]) @ route_vectors).argmax(axis=1)
            found = haversine_miles(lats[idx], lons[idx], route_lats[nearest], route_lons[nearest])
            mile[idx] = route_miles[nearest]
            distance[idx] = np.where(found <= within_miles, found, np.inf)
        return mile, distance


def _segment_distances(x, y, first, last):
    """Distance from each point strictly between first and last to the segment joining them."""
    px = x[first + 1 : last]
    py = y[first + 1 : last]
    dx = x[last] - x[first]
    dy = y[last] - y[first]
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        t = 0.0
    else:
        t = np.clip(((px - x[first]) * dx + (py - y[first]) * dy) / length_sq, 0.0, 1.0)
    return np.hypot(px - x[first] - t * dx, py - y[first] - t * dy)


def simplify(points, tolerance_miles):
    """Douglas-Peucker: drop points that stay within tolerance_miles of the simplified line."""
    if len(points) < 3:
        return list(points)
    coords = np.asarray(points, dtype=float)
    y = coords[:, 0] * MILES_PER_DEGREE
    x = coords[:, 1] * MILES_PER_DEGREE * math.cos(math.radians(coords[:, 0].mean()))

    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        distances = _segment_distances(x, y, first, last)
        worst = int(distances.argmax())
        if distances[worst] > tolerance_miles:
            split = first + 1 + worst
            keep[split] = True
            stack.append((first, split))
            stack.append((split, last))
    return [p for p, kept in zip(points, keep) if kept]
