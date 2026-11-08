import csv
import io
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


def row(name, state, feature_class, code, population, lat, lon, ascii_name=None):
    """A line of the GeoNames dump: id, name, ascii name, alternates, lat, lon,
    class, code, country, cc2, admin1-4, population, elevation, dem, tz, date."""
    return [
        "1", name, ascii_name or name, "", str(lat), str(lon), feature_class, code, "US", "",
        state, "", "", "", str(population), "", "", "America/Chicago", "2025-01-01",
    ]


ROWS = [
    row("Bigtown", "TX", "P", "PPL", 5000, 31.0, -97.0),
    row("Tinyville", "TX", "P", "PPL", 50, 31.1, -97.1),
    row("Big Cabin", "OK", "P", "PPL", 262, 36.5, -95.2),
    row("Downtown Bigtown", "TX", "P", "PPLX", 100000, 31.0, -97.0),
    row("Twin", "TX", "P", "PPL", 300, 30.0, -98.0),
    row("Twin", "TX", "P", "PPL", 3000, 30.5, -98.5),
    row("Bigtown Township", "TX", "A", "ADM3", 9000, 31.0, -97.0),
    row("New York City", "NY", "P", "PPL", 8804190, 40.71427, -74.00597),
    row("Española", "NM", "P", "PPL", 10000, 36.0, -106.0, ascii_name="Espanola"),
    row("Elsewhere", "ZZ", "P", "PPL", 50000, 10.0, 10.0),
    # A hamlet with a bigger census figure than the incorporated city of the same name.
    row("Oak Grove", "TX", "P", "PPL", 8000, 31.0, -99.0),
    row("Oak Grove", "TX", "P", "PPL", 7000, 32.0, -98.0),
    row("City of Oak Grove", "TX", "A", "ADMD", 7000, 32.01, -98.01),
]

STATIONS = [SimpleNamespace(city="Big Cabin", state="OK"), SimpleNamespace(city="Corinth", state="ME")]


class ExtractCitiesTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self.cleanup)
        dump = io.StringIO()
        csv.writer(dump, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE).writerows(ROWS)
        with zipfile.ZipFile(self.tmp / "US.zip", "w") as archive:
            archive.writestr("US.txt", dump.getvalue())
        (self.tmp / "city_coordinates.csv").write_text(
            "city,state,lat,lon,label,source\n"
            "Corinth,ME,44.98048,-69.01075,Town of Corinth,geonames\n"
            "Big Cabin,OK,36.53787,-95.22136,Big Cabin,geonames+photon\n"
        )

    def cleanup(self):
        for path in self.tmp.iterdir():
            path.unlink()
        self.tmp.rmdir()

    def extract(self, **options):
        with override_settings(
            GEONAMES_ZIP=self.tmp / "US.zip",
            US_CITIES_CSV=self.tmp / "us_cities.csv",
            CITY_COORDINATES_CSV=self.tmp / "city_coordinates.csv",
        ), mock.patch("planner.management.commands.extract_cities.read_stations", return_value=STATIONS):
            call_command("extract_cities", stdout=io.StringIO(), **options)
        with (self.tmp / "us_cities.csv").open(newline="") as fh:
            return {(r["name"], r["state"]): r for r in csv.DictReader(fh)}

    def test_keeps_populated_places_and_the_price_files_towns(self):
        self.assertEqual(
            sorted(self.extract()),
            [
                ("Big Cabin", "OK"), ("Bigtown", "TX"), ("Corinth", "ME"), ("Espanola", "NM"),
                ("New York", "NY"), ("New York City", "NY"), ("Oak Grove", "TX"), ("Twin", "TX"),
            ],
        )

    def test_the_most_populous_of_a_name_wins(self):
        twin = self.extract()[("Twin", "TX")]
        self.assertEqual((twin["lat"], twin["lon"], twin["population"]), ("30.50000", "-98.50000", "3000"))

    def test_an_incorporated_town_beats_a_bigger_hamlet_of_the_same_name(self):
        oak_grove = self.extract()[("Oak Grove", "TX")]
        self.assertEqual(
            (oak_grove["lat"], oak_grove["lon"], oak_grove["population"]), ("32.00000", "-98.00000", "7000")
        )

    def test_a_town_with_stations_takes_the_point_the_stations_use(self):
        big_cabin = self.extract()[("Big Cabin", "OK")]
        self.assertEqual(
            (big_cabin["lat"], big_cabin["lon"], big_cabin["population"]), ("36.53787", "-95.22136", "262")
        )

    def test_towns_geonames_files_elsewhere_come_from_the_coordinates_file(self):
        corinth = self.extract()[("Corinth", "ME")]
        self.assertEqual((corinth["lat"], corinth["lon"], corinth["population"]), ("44.98048", "-69.01075", "0"))

    def test_new_york_gets_both_names_at_one_point(self):
        cities = self.extract()
        self.assertEqual(cities[("New York", "NY")]["lat"], cities[("New York City", "NY")]["lat"])

    def test_threshold_is_adjustable(self):
        self.assertIn(("Tinyville", "TX"), self.extract(min_population=10))
