import csv
import io
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from planner.geonames import Match
from planner.management.commands.geocode_cities import RETRY_WAITS
from planner.photon import GeocodeError, Place

PRICES_HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"

PRICES = PRICES_HEADER + (
    '20,PILOT TRAVEL CENTER #1243,"I-8, EXIT 119 & SR-85",Gila Bend,AZ,930,3.80\n'
    '7,WOODSHED OF BIG CABIN,"I-44, EXIT 283 & US-69",Big Cabin,OK,307,3.00\n'
    "79,DELAWARE TRUCK PLAZA,US-13 & US-40,New Castle                    ,DE,243,3.249\n"
    "629,FLYING J #850,TCH-16,Edmonton,AB,80,4.39\n"
    '31,PILOT TRAVEL CENTER #310,"I-24, EXIT 86",Oak Grove,KY,100,2.90\n'
    '32,THORNTONS #607,"I-24, EXIT 62",Antioch,TN,100,2.899\n'
    '33,LOVES #624,"I-10, EXIT 192",Midway,FL,100,2.95\n'
    '34,BLUE RIDGE TRAVEL PLAZA,"I-81, EXIT 155",Mountain Top,PA,100,3.20\n'
    '35,GO MART FOOD STORE #86,"I-79, EXIT 40 & SR-16",Ivydale,WV,100,3.10\n'
)

OVERRIDES = "city,state,lat,lon,note\nIvydale,WV,38.53565,-81.03483,I-79 exit 40 is in Clay County\n"

PARTLY_DONE = (
    "city,state,lat,lon,label,source\n"
    "Big Cabin,OK,36.500000,-95.200000,Big Cabin,geonames\n"
    "New Castle,DE,,,,\n"
)


def match(lat, lon, name, proper=False, population=0):
    return Match(lat, lon, name, (True, 3, True, proper, population), namesake=True)


class FakeGazetteer:
    places = {
        ("Big Cabin", "OK"): [match(36.5, -95.2, "Big Cabin")],
        # An incorporated town and a bigger hamlet: the ranking is decisive.
        ("Oak Grove", "KY"): [
            match(36.6648, -87.4428, "Oak Grove", proper=True, population=7444),
            match(37.0, -85.0, "Oak Grove", population=7989),
        ],
        # Two hamlets nothing tells apart.
        ("Antioch", "TN"): [match(35.5, -86.5, "Antioch"), match(36.06, -86.6722, "Antioch")],
        ("Midway", "FL"): [
            match(30.49, -84.47, "Midway", proper=True, population=3276),
            match(30.6, -87.0, "Midway", population=16115),
        ],
        # A town of thousands and a dot on the map, neither incorporated.
        ("Mountain Top", "PA"): [
            match(41.1695, -75.8774, "Mountain Top", population=10982),
            match(40.1068, -75.9527, "Mountain Top"),
        ],
        ("Ivydale", "WV"): [match(38.53565, -81.03483, "Ivydale"), match(38.3101, -81.70457, "Ivydale")],
    }

    def candidates(self, city, state):
        return self.places.get((city, state), [])


GILA_BEND = Place(lat=32.9478, lon=-112.7168, label="Gila Bend, Arizona, United States")

# Where Photon puts each town: agreeing with the ranking for Oak Grove,
# settling the tie for Antioch, and disagreeing about Midway.
ANSWERS = {
    "Gila Bend": GILA_BEND,
    "Oak Grove": Place(lat=36.66, lon=-87.44, label="Oak Grove, Kentucky, United States"),
    "Antioch": Place(lat=36.06, lon=-86.67, label="Antioch, Tennessee, United States"),
    "Midway": Place(lat=30.6, lon=-87.0, label="Midway, Florida, United States"),
    "Mountain Top": Place(lat=40.1068, lon=-75.9527, label="Mountain Top, Pennsylvania, United States"),
    "Ivydale": Place(lat=38.3101, lon=-81.70457, label="Ivydale, West Virginia, United States"),
}


def photon_answers(city, state, interval):
    return ANSWERS.get(city)


def flaky(failures):
    """Photon that fails the first few times, then answers as usual."""
    calls = iter(range(10**6))

    def answer(city, state, interval):
        if next(calls) < failures:
            raise GeocodeError("busy")
        return photon_answers(city, state, interval)

    return answer


class GeocodeCitiesCommandTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        directory = Path(self.tmp.name)
        self.prices = directory / "prices.csv"
        self.prices.write_text(PRICES, encoding="utf-8")
        self.cities = directory / "cities.csv"
        self.overrides = directory / "overrides.csv"
        self.overrides.write_text(OVERRIDES, encoding="utf-8")
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
        with override_settings(
            FUEL_PRICES_CSV=self.prices,
            CITY_COORDINATES_CSV=self.cities,
            CITY_OVERRIDES_CSV=self.overrides,
            GEONAMES_ZIP=self.zip,
        ):
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

    def test_namesakes_are_asked_of_photon_before_the_unknown_cities(self):
        self.run_command()
        self.assertEqual(
            self.asked(),
            [
                ("Oak Grove", "KY"), ("Antioch", "TN"), ("Midway", "FL"), ("Mountain Top", "PA"),
                ("Gila Bend", "AZ"), ("New Castle", "DE"),
            ],
        )

    def test_a_town_settled_by_hand_is_taken_from_the_overrides_and_asked_of_nobody(self):
        self.run_command()
        row = self.rows()["Ivydale"]
        self.assertEqual(
            (row["lat"], row["lon"], row["label"], row["source"]), ("38.535650", "-81.034830", "Ivydale", "hand")
        )
        self.assertNotIn(("Ivydale", "WV"), self.asked())

    def test_a_town_of_thousands_beats_a_dot_on_the_map_whatever_photon_says(self):
        _, stderr = self.run_command()
        row = self.rows()["Mountain Top"]
        self.assertEqual((row["lat"], row["lon"], row["source"]), ("41.169500", "-75.877400", "geonames"))
        self.assertIn("Mountain Top, PA: Photon points at a namesake of 0 people; kept the one of 10982", stderr)

    def test_a_namesake_photon_agrees_about_is_marked_confirmed(self):
        self.run_command()
        row = self.rows()["Oak Grove"]
        self.assertEqual((row["lat"], row["lon"], row["source"]), ("36.664800", "-87.442800", "geonames+photon"))

    def test_a_tie_between_namesakes_goes_to_photons_choice_and_is_listed_for_a_look(self):
        _, stderr = self.run_command()
        row = self.rows()["Antioch"]
        self.assertEqual((row["lat"], row["lon"], row["source"]), ("36.060000", "-86.672200", "photon"))
        self.assertIn("Antioch, TN: Photon settled a tie in favour of (36.060, -86.672) over (35.500, -86.500)", stderr)

    def test_a_disagreement_over_a_proper_town_keeps_geonames_and_is_reported(self):
        _, stderr = self.run_command()
        row = self.rows()["Midway"]
        self.assertEqual((row["lat"], row["lon"], row["source"]), ("30.490000", "-84.470000", "geonames"))
        self.assertIn("Midway, FL: GeoNames ranks Midway (30.490, -84.470) as the town", stderr)
        self.assertIn("check by hand", stderr)

    def test_city_missing_from_the_gazetteer_is_written_from_photon(self):
        self.run_command()
        row = self.rows()["Gila Bend"]
        self.assertEqual((row["lat"], row["lon"]), ("32.947800", "-112.716800"))
        self.assertEqual(row["label"], GILA_BEND.label)
        self.assertEqual(row["source"], "photon")

    def test_city_nobody_knows_is_written_without_coordinates_and_counted_as_not_found(self):
        stdout, stderr = self.run_command()
        row = self.rows()["New Castle"]
        self.assertEqual((row["lat"], row["lon"], row["source"]), ("", "", ""))
        self.assertIn("1 settled by hand, 1 found in GeoNames outright; asking Photon about 4 with namesakes", stdout)
        self.assertIn("1 by hand, 3 from GeoNames, 1 confirmed by Photon, 2 from Photon, 1 not found", stdout)
        self.assertIn("no result for New Castle, DE", stderr)

    def test_cities_already_in_the_coordinates_file_are_skipped(self):
        self.cities.write_text(PARTLY_DONE, encoding="utf-8")
        stdout, _ = self.run_command()
        self.assertIn("8 cities, 2 cached, 6 to look up", stdout)
        self.assertEqual(
            self.asked(),
            [("Oak Grove", "KY"), ("Antioch", "TN"), ("Midway", "FL"), ("Mountain Top", "PA"), ("Gila Bend", "AZ")],
        )

    def test_retry_missing_asks_again_for_cities_without_coordinates(self):
        self.cities.write_text(PARTLY_DONE, encoding="utf-8")
        stdout, _ = self.run_command("--retry-missing")
        self.assertIn("8 cities, 1 cached, 7 to look up", stdout)
        self.assertIn(("New Castle", "DE"), self.asked())

    def test_nothing_left_to_look_up_does_not_load_the_gazetteer(self):
        self.run_command()
        self.gazetteer.reset_mock()
        stdout, _ = self.run_command()
        self.assertIn("8 cities, 8 cached, 0 to look up", stdout)
        self.gazetteer.assert_not_called()

    def test_header_is_written_once_across_runs(self):
        self.run_command()
        with self.prices.open("a", encoding="utf-8") as fh:
            fh.write("88,JOPLIN TRAVEL CENTER,I-44 EXIT 4,Joplin,MO,100,3.10\n")
        self.run_command()
        lines = self.cities.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "city,state,lat,lon,label,source")
        self.assertEqual(sum(line.startswith("city,") for line in lines), 1)
        self.assertEqual(
            list(self.rows()),
            [
                "Big Cabin", "Ivydale", "Oak Grove", "Antioch", "Midway", "Mountain Top",
                "Gila Bend", "New Castle", "Joplin",
            ],
        )

    def test_photon_failure_is_retried_and_then_succeeds(self):
        self.photon.side_effect = flaky(2)
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
        self.assertEqual(set(self.asked()), {("Oak Grove", "KY")})
        self.assertEqual(list(self.rows()), ["Big Cabin", "Ivydale"])
        self.assertIn("1 by hand, 1 from GeoNames, 0 confirmed by Photon, 0 from Photon, 0 not found", stdout)

    def test_cities_left_after_giving_up_are_looked_up_on_the_next_run(self):
        self.photon.side_effect = GeocodeError("geocoding service unavailable")
        self.run_command()
        self.photon.reset_mock(side_effect=True)
        self.photon.side_effect = photon_answers
        stdout, _ = self.run_command()
        self.assertIn("8 cities, 2 cached, 6 to look up", stdout)
        self.assertEqual(
            list(self.rows()),
            ["Big Cabin", "Ivydale", "Oak Grove", "Antioch", "Midway", "Mountain Top", "Gila Bend", "New Castle"],
        )
