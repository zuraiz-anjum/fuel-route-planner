"""
Look up coordinates for every city in the fuel price file.

The price file has no coordinates, only an address, city and state, and the
addresses are highway junctions that no geocoder resolves. City centroids are
close enough to place a stop on a route, so that is what we look up.

Nearly every city is found offline in the GeoNames gazetteer, which is
downloaded on first use. Those with one match are written straight away.
Where a state has several populated places by the same name, Photon is
asked where the town is: if it agrees with the gazetteer's ranking, good; if
the ranking was only a matter of population, Photon's choice wins; and if
the two disagree about a county seat or an incorporated town, the gazetteer
is kept and the case is printed for a look by hand. Whatever GeoNames does
not know at all is asked of Photon too, gently, with long waits if it
throttles us. A town in data/city_overrides.csv, settled by hand from its
stations' addresses, is taken from there and asked of nobody. Results are
appended to data/city_coordinates.csv as they arrive and the command skips
anything already there, so it can be stopped and resumed. The finished file
is committed; nobody needs to run this unless the price file changes.
"""

import csv
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from planner.geo import haversine_miles
from planner.geonames import Gazetteer, ensure_dump
from planner.photon import GeocodeError, geocode_city
from planner.stations import city_key, read_city_coordinates, read_city_overrides, read_stations

FIELDS = ["city", "state", "lat", "lon", "label", "source"]
# Throttling and outages pass; waiting them out beats restarting by hand.
RETRY_WAITS = [15, 30, 60, 120] + [300] * 5
# How close Photon's answer has to be to a candidate to count as naming it.
NAMESAKE_MILES = 15.0


def write_row(writer, fh, city, state, point=None, source=""):
    row = {"city": city, "state": state, "lat": "", "lon": "", "label": "", "source": source}
    if point:
        lat, lon, label = point
        row.update(lat=f"{lat:.6f}", lon=f"{lon:.6f}", label=label)
    writer.writerow(row)
    fh.flush()


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
        gazetteer = Gazetteer(ensure_dump(), todo)

        write_header = not path.exists() or path.stat().st_size == 0
        overrides = read_city_overrides()
        counts = {"hand": 0, "geonames": 0, "geonames+photon": 0, "photon": 0, "": 0}
        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            if write_header:
                writer.writeheader()

            ambiguous, unknown = [], []
            for city, state in todo:
                if city_key(city, state) in overrides:
                    lat, lon, _ = overrides[city_key(city, state)]
                    write_row(writer, fh, city, state, (lat, lon, city), "hand")
                    counts["hand"] += 1
                    continue
                matches = gazetteer.candidates(city, state)
                namesakes = [m for m in matches if m.namesake]
                if not matches:
                    unknown.append((city, state))
                elif len(namesakes) > 1:
                    ambiguous.append((city, state, namesakes))
                else:
                    write_row(writer, fh, city, state, matches[0][:3], "geonames")
                    counts["geonames"] += 1
            self.stdout.write(
                f"{counts['hand']} settled by hand, {counts['geonames']} found in GeoNames outright; asking Photon "
                f"about {len(ambiguous)} with namesakes in their state and {len(unknown)} it does not know"
            )

            try:
                for city, state, namesakes in ambiguous:
                    place = self.ask_photon(city, state, options["interval"])
                    point, source = self.settle(city, state, namesakes, place)
                    write_row(writer, fh, city, state, point, source)
                    counts[source] += 1
                for city, state in unknown:
                    place = self.ask_photon(city, state, options["interval"])
                    if place is None:
                        self.stderr.write(f"no result for {city}, {state}")
                        write_row(writer, fh, city, state)
                        counts[""] += 1
                    else:
                        write_row(writer, fh, city, state, (place.lat, place.lon, place.label), "photon")
                        counts["photon"] += 1
            except GeocodeError as exc:
                self.stderr.write(f"giving up on Photon for now: {exc}; run again later for the rest")
        self.stdout.write(
            f"{counts['hand']} by hand, {counts['geonames']} from GeoNames, {counts['geonames+photon']} confirmed "
            f"by Photon, {counts['photon']} from Photon, {counts['']} not found"
        )

    def settle(self, city, state, namesakes, place):
        """Which of several same-named places the town is, given Photon's opinion."""
        best = namesakes[0]
        if place is None:
            return best[:3], "geonames"
        nearest = min(namesakes, key=lambda m: haversine_miles(m.lat, m.lon, place.lat, place.lon))
        distance = haversine_miles(nearest.lat, nearest.lon, place.lat, place.lon)
        if distance > NAMESAKE_MILES:
            self.stderr.write(f"{city}, {state}: Photon points {distance:.0f} miles from any candidate; kept GeoNames")
            return best[:3], "geonames"
        if nearest is best:
            return best[:3], "geonames+photon"
        # When everything before population in the rank is equal, a town of
        # thousands still beats a dot on the map whatever Photon says; between
        # two dots Photon's word is taken, though it is wrong often enough
        # about hamlets to be worth a look.
        if nearest.rank[:-1] == best.rank[:-1]:
            best_population, nearest_population = best.rank[-1], nearest.rank[-1]
            if best_population >= 1000 and best_population >= 5 * nearest_population:
                self.stderr.write(
                    f"{city}, {state}: Photon points at a namesake of {nearest_population} people; "
                    f"kept the one of {best_population}"
                )
                return best[:3], "geonames"
            others = ", ".join(f"({m.lat:.3f}, {m.lon:.3f})" for m in namesakes if m is not nearest)
            self.stderr.write(
                f"{city}, {state}: Photon settled a tie in favour of ({nearest.lat:.3f}, {nearest.lon:.3f}) "
                f"over {others}; check against the station addresses"
            )
            return nearest[:3], "photon"
        self.stderr.write(
            f"{city}, {state}: GeoNames ranks {best.name} ({best.lat:.3f}, {best.lon:.3f}) as the town, "
            f"Photon points at {nearest.name} ({nearest.lat:.3f}, {nearest.lon:.3f}); kept GeoNames, check by hand"
        )
        return best[:3], "geonames"

    def ask_photon(self, city, state, interval):
        """One city, waiting out throttling and outages before giving up."""
        for wait in RETRY_WAITS:
            try:
                return geocode_city(city, state, interval)
            except GeocodeError as exc:
                self.stderr.write(f"{exc}; retrying in {wait}s")
                time.sleep(wait)
        return geocode_city(city, state, interval)
