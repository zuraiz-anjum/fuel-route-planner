import io
import tempfile
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from planner.models import Station
from planner.stations import city_key, normalize_city, read_city_coordinates, read_stations

PRICES_HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"

PRICES = PRICES_HEADER + (
    '7,WOODSHED OF BIG CABIN,"I-44, EXIT 283 & US-69",Big Cabin,OK,307,3.00\n'
    '20,PILOT TRAVEL CENTER #1243,"I-8, EXIT 119 & SR-85",Gila Bend,AZ,930,3.80\n'
    '20,PILOT #1243,"I-8, EXIT 119 & SR-85",Gila Bend,AZ,930,4.00\n'
    '20,PILOT TRAVEL CENTER #1243,"I-8, EXIT 119 & SR-85",Gila Bend,AZ,930,3.90\n'
    "79,DELAWARE TRUCK PLAZA,US-13 & US-40,New Castle                    ,DE,243,3.249\n"
    "629,FLYING J #850,TCH-16,Edmonton,AB,80,4.39\n"
)

COORDINATES = (
    "city,state,lat,lon,label\n"
    "Big Cabin,OK,36.537900,-95.221400,Big Cabin\n"
    "Gila Bend,AZ,32.947800,-112.716800,Gila Bend\n"
    "New Castle,DE,,,\n"
)


def write(directory, name, text):
    path = Path(directory) / name
    path.write_text(text, encoding="utf-8")
    return path


class ReadStationsTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stations = read_stations(write(self.tmp.name, "prices.csv", PRICES))

    def test_collapses_rows_to_one_record_per_station(self):
        self.assertEqual([s.opis_id for s in self.stations], [7, 20, 79])

    def test_drops_canadian_stations(self):
        self.assertNotIn("AB", {s.state for s in self.stations})

    def test_averages_prices_and_counts_them(self):
        pilot = self.stations[1]
        self.assertEqual(pilot.price, Decimal("3.90"))
        self.assertEqual(pilot.price_count, 3)

    def test_keeps_the_most_common_name(self):
        self.assertEqual(self.stations[1].name, "PILOT TRAVEL CENTER #1243")

    def test_strips_padding_from_city(self):
        self.assertEqual(self.stations[2].city, "New Castle")


class CityKeyTests(SimpleTestCase):
    def test_normalises_spacing_case_and_state(self):
        self.assertEqual(city_key("  New   Castle ", "de"), ("new castle", "DE"))
        self.assertEqual(normalize_city("HOMER   GLEN "), "HOMER GLEN")


class ReadCityCoordinatesTests(SimpleTestCase):
    def test_reads_points_and_records_misses_as_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            coordinates = read_city_coordinates(write(tmp, "cities.csv", COORDINATES))
        self.assertEqual(coordinates[("big cabin", "OK")], (36.5379, -95.2214))
        self.assertIsNone(coordinates[("new castle", "DE")])

    def test_missing_file_is_empty(self):
        self.assertEqual(read_city_coordinates(Path("/nowhere/cities.csv")), {})


class LoadStationsCommandTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prices = write(self.tmp.name, "prices.csv", PRICES)
        self.cities = write(self.tmp.name, "cities.csv", COORDINATES)

    def load(self):
        with override_settings(FUEL_PRICES_CSV=self.prices, CITY_COORDINATES_CSV=self.cities):
            call_command("load_stations", stdout=io.StringIO())

    def test_loads_stations_with_coordinates_where_known(self):
        self.load()
        self.assertEqual(Station.objects.count(), 3)
        pilot = Station.objects.get(opis_id=20)
        self.assertEqual((pilot.lat, pilot.lon), (32.9478, -112.7168))
        self.assertEqual(pilot.price, Decimal("3.9000"))
        delaware = Station.objects.get(opis_id=79)
        self.assertIsNone(delaware.lat)
        self.assertEqual(Station.objects.located().count(), 2)

    def test_reloading_updates_rows_in_place(self):
        self.load()
        self.prices.write_text(PRICES.replace("3.00\n", "2.50\n"), encoding="utf-8")
        self.load()
        self.assertEqual(Station.objects.count(), 3)
        self.assertEqual(Station.objects.get(opis_id=7).price, Decimal("2.5000"))
