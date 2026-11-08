from django.test import SimpleTestCase

from planner.cities import find_city, readings
from planner.states import state_code
from planner.stations import read_stations


class StateCodeTests(SimpleTestCase):
    def test_codes_and_names_in_any_case(self):
        for text in ("TX", "tx", "Texas", "texas", " TEXAS "):
            self.assertEqual(state_code(text), "TX")
        self.assertEqual(state_code("new  york"), "NY")
        self.assertEqual(state_code("District of Columbia"), "DC")

    def test_anything_else_is_not_a_state(self):
        for text in ("USA", "ZZ", "T", "Tex", "United States", ""):
            self.assertIsNone(state_code(text))


class ReadingsTests(SimpleTestCase):
    def test_comma_forms(self):
        self.assertEqual(list(readings("Dallas, TX")), [("Dallas", "TX")])
        self.assertEqual(list(readings("Dallas, Texas, USA")), [("Dallas", "TX")])
        self.assertEqual(list(readings("100 Main St, Dallas, TX")), [("100 Main St", "TX")])

    def test_forms_without_a_comma(self):
        self.assertEqual(list(readings("Dallas TX")), [("Dallas", "TX")])
        # The longer state name is tried first; the other reading is a fallback.
        self.assertEqual(
            list(readings("Charleston West Virginia")), [("Charleston", "WV"), ("Charleston West", "VA")]
        )
        self.assertEqual(list(readings("Kansas City Kansas")), [("Kansas City", "KS")])

    def test_nothing_to_read_without_a_state(self):
        for query in ("Dallas", "New York", "Paris, France", "Dallas, Ontario", ""):
            self.assertEqual(list(readings(query)), [])


class FindCityTests(SimpleTestCase):
    """Against the committed table, which is part of what is being tested."""

    def test_city_and_state_in_their_usual_spellings(self):
        for query in ("Dallas, TX", "dallas, texas", "Dallas TX", "Dallas, TX, USA", "DALLAS,TX"):
            place = find_city(query)
            self.assertEqual(place.label, "Dallas, Texas, United States", query)
            self.assertAlmostEqual(place.lat, 32.783, places=2)
            self.assertAlmostEqual(place.lon, -96.807, places=2)

    def test_new_york_is_new_york(self):
        place = find_city("New York, NY")
        self.assertEqual(place.label, "New York, New York, United States")
        self.assertAlmostEqual(place.lat, 40.714, places=2)

    def test_same_name_different_states(self):
        self.assertNotEqual(find_city("Springfield, IL"), find_city("Springfield, MO"))
        self.assertNotEqual(find_city("Portland, OR"), find_city("Portland, ME"))
        self.assertNotEqual(find_city("Kansas City, MO"), find_city("Kansas City, KS"))

    def test_saint_fort_and_mount_in_either_spelling(self):
        self.assertEqual(find_city("Saint Louis, MO"), find_city("St. Louis, MO"))
        self.assertEqual(find_city("Fort Worth, TX"), find_city("Ft Worth, TX"))
        self.assertEqual(find_city("Mount Vernon, WA"), find_city("Mt Vernon, WA"))

    def test_small_towns_from_the_price_file_are_there(self):
        self.assertEqual(find_city("Big Cabin, OK").label, "Big Cabin, Oklahoma, United States")
        self.assertIsNotNone(find_city("Pueblo of Acoma, NM"))
        self.assertIsNotNone(find_city("Acoma Pueblo, NM"))
        self.assertIsNotNone(find_city("Corinth, ME"))

    def test_every_town_in_the_price_file_resolves(self):
        towns = {(s.city, s.state) for s in read_stations()}
        missing = sorted(town for town in towns if find_city("%s, %s" % town) is None)
        self.assertEqual(missing, [])

    def test_unknown_places_and_bare_names_are_left_to_the_geocoder(self):
        for query in ("Nowhere, TX", "Dallas", "Paris, France", "Dallas, Ontario", "100 Main St, Dallas, TX"):
            self.assertIsNone(find_city(query), query)
