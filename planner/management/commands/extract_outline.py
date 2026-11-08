"""
Write data/us_outline.json, the shape typed-in coordinates are checked against.

Taken from Natural Earth's 1:50m countries (public domain): the United
States, pushed out by about ten miles so that a harbour, a beach or the
far side of a border town still counts, then simplified to a few thousand
points. Committed, like the other data files; this only needs to run again
if the buffer or the source changes.
"""

import json

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from shapely.geometry import mapping, shape

COUNTRIES_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
    "ne_50m_admin_0_countries.geojson"
)

# Degrees. A tenth of a degree is about seven miles of latitude.
BUFFER = 0.15
TOLERANCE = 0.02


class Command(BaseCommand):
    help = "Extract a buffered outline of the United States from Natural Earth into data/us_outline.json."

    def handle(self, *args, **options):
        source = settings.NATURAL_EARTH_COUNTRIES
        if not source.exists():
            self.stdout.write("downloading Natural Earth countries (about 3 MB)")
            response = requests.get(COUNTRIES_URL, timeout=(3, 60))
            response.raise_for_status()
            source.write_bytes(response.content)
        with source.open(encoding="utf-8") as fh:
            countries = json.load(fh)["features"]
        united_states = next(f for f in countries if f["properties"]["ADM0_A3"] == "USA")

        outline = shape(united_states["geometry"]).buffer(BUFFER).simplify(TOLERANCE)
        geometry = mapping(outline)
        geometry["coordinates"] = _rounded(geometry["coordinates"])
        with settings.US_OUTLINE_JSON.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(geometry, fh, separators=(",", ":"))
            fh.write("\n")
        points = sum(len(ring) for polygon in geometry["coordinates"] for ring in polygon)
        self.stdout.write(self.style.SUCCESS(
            f"{len(geometry['coordinates'])} polygons, {points} points written to {settings.US_OUTLINE_JSON.name}"
        ))


def _rounded(coordinates):
    if isinstance(coordinates[0], float):
        return [round(value, 3) for value in coordinates]
    return [_rounded(part) for part in coordinates]
