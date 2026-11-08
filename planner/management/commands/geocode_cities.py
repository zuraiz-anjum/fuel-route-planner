"""
Look up coordinates for every city in the fuel price file.

The price file has no coordinates, only an address, city and state, and the
addresses are highway junctions that no geocoder resolves. City centroids are
close enough to place a stop on a route, so that is what we look up.

This talks to Nominatim at its permitted one request per second, so a full
run over the ~3,800 distinct cities takes about an hour. Results are appended
to data/city_coordinates.csv as they arrive and the command skips anything
already there, so it can be stopped and resumed. The finished file is
committed; nobody needs to run this unless the price file changes.
"""

import csv

from django.conf import settings
from django.core.management.base import BaseCommand

from planner.nominatim import GeocodeError, geocode_city
from planner.stations import city_key, read_city_coordinates, read_stations

FIELDS = ["city", "state", "lat", "lon", "label"]


class Command(BaseCommand):
    help = "Geocode the cities in the fuel price file (slow; results are cached in data/)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retry-missing",
            action="store_true",
            help="Query again for cities that previously returned no result.",
        )

    def handle(self, *args, **options):
        path = settings.CITY_COORDINATES_CSV
        known = read_city_coordinates(path)
        if options["retry_missing"]:
            known = {k: v for k, v in known.items() if v is not None}

        wanted = {}
        for station in read_stations():
            wanted.setdefault(city_key(station.city, station.state), (station.city, station.state))
        todo = [v for k, v in wanted.items() if k not in known]
        self.stdout.write(f"{len(wanted)} cities, {len(known)} cached, {len(todo)} to look up")

        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            if write_header:
                writer.writeheader()
            misses = 0
            for n, (city, state) in enumerate(todo, 1):
                try:
                    place = geocode_city(city, state)
                except GeocodeError as exc:
                    self.stderr.write(f"stopping: {exc}")
                    break
                row = {"city": city, "state": state, "lat": "", "lon": "", "label": ""}
                if place is None:
                    misses += 1
                    self.stderr.write(f"no result for {city}, {state}")
                else:
                    row.update(lat=f"{place.lat:.6f}", lon=f"{place.lon:.6f}", label=place.label)
                writer.writerow(row)
                fh.flush()
                if n % 100 == 0:
                    self.stdout.write(f"{n}/{len(todo)} done, {misses} misses")
        self.stdout.write(self.style.SUCCESS(f"finished; {misses} cities had no result"))
