import csv
import io
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from planner.management.commands.geocode_cities import RETRY_WAITS
from planner.photon import GeocodeError, Place

PRICES_HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"

PRICES = PRICES_HEADER + (
    '20,PILOT TRAVEL CENTER #1243,"I-8, EXIT 119 & SR-85",Gila Bend,AZ,930,3.80\n'
    '7,WOODSHED OF BIG CABIN,"I-44, EXIT 283 & US-69",Big Cabin,OK,307,3.00\n'
    "79,DELAWARE TRUCK PLAZA,US-13 & US-40,New Castle                    ,DE,243,3.249\n"
    "629,FLYING J #850,TCH-16,Edmonton,AB,80,4.39\n"
)

PARTLY_DONE = (
    "city,state,lat,lon,label,source\n"
    "Big Cabin,OK,36.500000,-95.200000,Big Cabin,geonames\n"
    "New Castle,DE,,,,\n"
)

class FakeGazetteer:
    places = {("Big Cabin", "OK"): (36.5, -95.2, "Big Cabin")}

    def find(self, city, state):
        return self.places.get((city, state))

GILA_BEND = Place(lat=32.9478, lon=-112.7168, label="Gila Bend, Arizona, United States")


def photon_answers(city, state, interval):
    return GILA_BEND if city == "Gila Bend" else None


class GeocodeCitiesCommandTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        directory = Path(self.tmp.name)
        self.prices = directory / "prices.csv"
        self.prices.write_text(PRICES, encoding="utf-8")
        self.cities = directory / "cities.csv"
        self.zip = directory / "US.zip"
        self.zip.touch()
        self.addCleanup(mock.patch.stopall)
        mock.patch("planner.management.commands.geocode_cities.ensure_dump", return_value=self.zip).start()
        self.gazetteer = mock.patch(
            "planner.management.commands.geocode_cities.Gazetteer", return_value=FakeGazetteer()
        ).start()
        self.photon = mock.patch(
            "planner.management.commands.geocode_cities.geocode_city", side_effect=photon_answers
        ).start()
        self.sleep = mock.patch("planner.management.commands.geocode_cities.time.sleep").start()

    def run_command(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with override_settings(FUEL_PRICES_CSV=self.prices, CITY_COORDINATES_CSV=self.cities, GEONAMES_ZIP=self.zip):
            call_command("geocode_cities", *args, stdout=stdout, stderr=stderr)
        return stdout.getvalue(), stderr.getvalue()

    def rows(self):
        with self.cities.open(newline="", encoding="utf-8") as fh:
            return {row["city"]: row for row in csv.DictReader(fh)}

    def asked(self):
        return [call.args[:2] for call in self.photon.call_args_list]

    def test_city_in_the_gazetteer_is_written_from_geonames_without_asking_photon(self):
        self.run_command()
        row = self.rows()["Big Cabin"]
        self.assertEqual((row["lat"], row["lon"], row["label"]), ("36.500000", "-95.200000", "Big Cabin"))
        self.assertEqual(row["source"], "geonames")
        self.assertNotIn(("Big Cabin", "OK"), self.asked())

    def test_city_missing_from_the_gazetteer_is_written_from_photon(self):
        self.run_command()
        row = self.rows()["Gila Bend"]
        self.assertEqual((row["lat"], row["lon"]), ("32.947800", "-112.716800"))
        self.assertEqual(row["label"], GILA_BEND.label)
        self.assertEqual(row["source"], "photon")
        self.assertEqual(self.asked(), [("Gila Bend", "AZ"), ("New Castle", "DE")])

    def test_city_nobody_knows_is_written_without_coordinates_and_counted_as_not_found(self):
        stdout, stderr = self.run_command()
        row = self.rows()["New Castle"]
        self.assertEqual((row["lat"], row["lon"], row["source"]), ("", "", ""))
        self.assertIn("1 found in GeoNames; asking Photon about 2", stdout)
        self.assertIn("1 from GeoNames, 1 from Photon, 1 not found", stdout)
        self.assertIn("no result for New Castle, DE", stderr)

    def test_cities_already_in_the_coordinates_file_are_skipped(self):
        self.cities.write_text(PARTLY_DONE, encoding="utf-8")
        stdout, _ = self.run_command()
        self.assertIn("3 cities, 2 cached, 1 to look up", stdout)
        self.assertEqual(self.asked(), [("Gila Bend", "AZ")])

    def test_retry_missing_asks_again_for_cities_without_coordinates(self):
        self.cities.write_text(PARTLY_DONE, encoding="utf-8")
        stdout, _ = self.run_command("--retry-missing")
        self.assertIn("3 cities, 1 cached, 2 to look up", stdout)
        self.assertEqual(self.asked(), [("Gila Bend", "AZ"), ("New Castle", "DE")])

    def test_nothing_left_to_look_up_does_not_load_the_gazetteer(self):
        self.run_command()
        self.gazetteer.reset_mock()
        stdout, _ = self.run_command()
        self.assertIn("3 cities, 3 cached, 0 to look up", stdout)
        self.gazetteer.assert_not_called()

    def test_header_is_written_once_across_runs(self):
        self.run_command()
        with self.prices.open("a", encoding="utf-8") as fh:
            fh.write("88,JOPLIN TRAVEL CENTER,I-44 EXIT 4,Joplin,MO,100,3.10\n")
        self.run_command()
        lines = self.cities.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "city,state,lat,lon,label,source")
        self.assertEqual(sum(line.startswith("city,") for line in lines), 1)
        self.assertEqual(list(self.rows()), ["Big Cabin", "Gila Bend", "New Castle", "Joplin"])

    def test_photon_failure_is_retried_and_then_succeeds(self):
        self.photon.side_effect = [GeocodeError("busy"), GeocodeError("busy"), GILA_BEND, None]
        _, stderr = self.run_command()
        self.assertEqual(self.rows()["Gila Bend"]["source"], "photon")
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], RETRY_WAITS[:2])
        self.assertIn("busy; retrying in 15s", stderr)
        self.assertNotIn("giving up", stderr)

    def test_photon_that_keeps_failing_stops_only_the_photon_pass_and_is_reported(self):
        self.photon.side_effect = GeocodeError("geocoding service unavailable")
        stdout, stderr = self.run_command()
        self.assertEqual(self.photon.call_count, len(RETRY_WAITS) + 1)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], RETRY_WAITS)
        self.assertIn("giving up on Photon for now: geocoding service unavailable", stderr)
        self.assertEqual(set(self.asked()), {("Gila Bend", "AZ")})
        self.assertEqual(list(self.rows()), ["Big Cabin"])
        self.assertIn("1 from GeoNames, 0 from Photon, 0 not found", stdout)

    def test_cities_left_after_giving_up_are_looked_up_on_the_next_run(self):
        self.photon.side_effect = GeocodeError("geocoding service unavailable")
        self.run_command()
        self.photon.reset_mock(side_effect=True)
        self.photon.side_effect = photon_answers
        stdout, _ = self.run_command()
        self.assertIn("3 cities, 1 cached, 2 to look up", stdout)
        self.assertEqual(self.asked(), [("Gila Bend", "AZ"), ("New Castle", "DE")])
        self.assertEqual(list(self.rows()), ["Big Cabin", "Gila Bend", "New Castle"])
