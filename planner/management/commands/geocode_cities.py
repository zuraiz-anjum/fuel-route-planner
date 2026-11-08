"""
Look up coordinates for every city in the fuel price file.

The price file has no coordinates, only an address, city and state, and the
addresses are highway junctions that no geocoder resolves. City centroids are
close enough to place a stop on a route, so that is what we look up.

Most cities are found offline in the GeoNames gazetteer, which is downloaded
on first use. Whatever GeoNames does not know is asked of Photon, gently, with
long waits if it throttles us. Results are appended to
data/city_coordinates.csv as they arrive and the command skips anything
already there, so it can be stopped and resumed. The finished file is
committed; nobody needs to run this unless the price file changes.
"""

import csv
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from planner.geonames import build_index, ensure_dump, normalize_name
from planner.photon import GeocodeError, geocode_city
from planner.stations import city_key, read_city_coordinates, read_stations

FIELDS = ["city", "state", "lat", "lon", "label", "source"]
# Throttling and outages pass; waiting them out beats restarting by hand.
RETRY_WAITS = [15, 30, 60, 120] + [300] * 5


class Command(BaseCommand):
    help = "Geocode the cities in the fuel price file; results are cached in data/."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retry-missing",
            action="store_true",
            help="Query again for cities that previously returned no result.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=1.0,
            help="Seconds between Photon requests.",
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
        if not todo:
            return

        if not settings.GEONAMES_ZIP.exists():
            self.stdout.write("downloading the GeoNames US gazetteer (about 80 MB)")
        gazetteer = build_index(ensure_dump())

        write_header = not path.exists() or path.stat().st_size == 0
        counts = {"geonames": 0, "photon": 0, "missing": 0}
        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            if write_header:
                writer.writeheader()
            for city, state in todo:
                row = {"city": city, "state": state, "lat": "", "lon": "", "label": "", "source": ""}
                found = gazetteer.get((normalize_name(city), state))
                if found:
                    lat, lon, label = found
                    row.update(lat=f"{lat:.6f}", lon=f"{lon:.6f}", label=label, source="geonames")
                else:
                    try:
                        place = self.ask_photon(city, state, options["interval"])
                    except GeocodeError as exc:
                        self.stderr.write(f"giving up after repeated failures: {exc}")
                        break
                    if place is None:
                        self.stderr.write(f"no result for {city}, {state}")
                    else:
                        row.update(lat=f"{place.lat:.6f}", lon=f"{place.lon:.6f}", label=place.label, source="photon")
                counts[row["source"] or "missing"] += 1
                writer.writerow(row)
                fh.flush()
        self.stdout.write(
            f"{counts['geonames']} from GeoNames, {counts['photon']} from Photon, {counts['missing']} not found"
        )

    def ask_photon(self, city, state, interval):
        """One city, waiting out throttling and outages before giving up."""
        for wait in RETRY_WAITS:
            try:
                return geocode_city(city, state, interval)
            except GeocodeError as exc:
                self.stderr.write(f"{exc}; retrying in {wait}s")
                time.sleep(wait)
        return geocode_city(city, state, interval)
