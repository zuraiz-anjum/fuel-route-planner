"""
Write data/us_cities.csv, the table that resolves "City, ST" without a geocoder.

Taken from the GeoNames US dump: every populated place with at least a
thousand people, plus every town in the price file whatever its size, so
that a start or finish typed as a station's own town always resolves
locally. Where a state has several places by one name, a county seat or an
incorporated town beats a hamlet, then the larger population wins. Towns
with stations take the point in data/city_coordinates.csv, which
geocode_cities has already checked against a second source, so the two
tables agree. About seventeen thousand rows; committed, like the
coordinates file, so nobody has to run this to use the API.
"""

import csv
import io
import zipfile
from collections import defaultdict

from django.conf import settings
from django.core.management.base import BaseCommand

from planner.geonames import (
    ADMIN1, AREA_CODES, ASCII_NAME, FEATURE_CLASS, FEATURE_CODE, LAT, LON, NAME, POPULATION, SECONDARY_CODES,
    area_name, ensure_dump, normalize_name, proper_town,
)
from planner.states import STATE_NAMES
from planner.stations import city_key, read_city_coordinates, read_stations

FIELDS = ["name", "state", "lat", "lon", "population"]

# GeoNames calls the city "New York City"; everybody else calls it New York.
ALSO_KNOWN_AS = {("New York City", "NY"): "New York"}


class Command(BaseCommand):
    help = "Extract the US city table from the GeoNames dump into data/us_cities.csv."

    def add_arguments(self, parser):
        parser.add_argument("--min-population", type=int, default=1000)

    def handle(self, *args, **options):
        towns = {(normalize_name(s.city), s.state): s.city for s in read_stations()}
        if not settings.GEONAMES_ZIP.exists():
            self.stdout.write("downloading the GeoNames US gazetteer (about 80 MB)")
        candidates = defaultdict(list)
        areas = defaultdict(list)
        with zipfile.ZipFile(ensure_dump()) as archive, archive.open("US.txt") as raw:
            rows = csv.reader(io.TextIOWrapper(raw, encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE)
            for fields in rows:
                feature_class, code, state = fields[FEATURE_CLASS], fields[FEATURE_CODE], fields[ADMIN1]
                if state not in STATE_NAMES:
                    continue
                lat, lon = float(fields[LAT]), float(fields[LON])
                if feature_class == "A" and code in AREA_CODES:
                    areas[normalize_name(area_name(fields[ASCII_NAME])), state].append((lat, lon))
                if feature_class != "P" or code in SECONDARY_CODES:
                    continue
                population = int(fields[POPULATION] or 0)
                names = [fields[ASCII_NAME]]
                if (fields[NAME], state) in ALSO_KNOWN_AS:
                    names.append(ALSO_KNOWN_AS[fields[NAME], state])
                for name in names:
                    key = (normalize_name(name), state)
                    if population >= options["min_population"] or key in towns:
                        candidates[key].append((code, population, name, lat, lon))

        best = {}
        for key, rows in candidates.items():
            code, population, name, lat, lon = max(
                rows, key=lambda row: (proper_town(row[0], row[3], row[4], areas[key]), row[1])
            )
            best[key] = (population, name, lat, lon)

        coordinates = read_city_coordinates()
        for key, town in towns.items():
            point = coordinates.get(city_key(town, key[1]))
            if point:
                population = best[key][0] if key in best else 0
                best[key] = (population, best[key][1] if key in best else town, *point)

        with settings.US_CITIES_CSV.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(FIELDS)
            by_state_and_name = sorted(best.items(), key=lambda item: (item[0][1], item[1][1]))
            for (_, state), (population, name, lat, lon) in by_state_and_name:
                writer.writerow([name, state, f"{lat:.5f}", f"{lon:.5f}", population])
        self.stdout.write(self.style.SUCCESS(f"{len(best)} cities written to {settings.US_CITIES_CSV.name}"))
