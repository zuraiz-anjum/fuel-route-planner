from django.test import SimpleTestCase

from planner.cities import city_table, find_city, readings, suggest
from planner.geo import haversine_miles
from planner.states import STATE_NAMES, state_code
from planner.stations import read_city_coordinates, read_stations


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

    def test_stations_sit_where_the_city_table_puts_their_town(self):
        # The coordinates file and the city table are built separately from
        # GeoNames; a town placed differently in the two is a matching bug.
        astray = []
        for (city, state), point in read_city_coordinates().items():
            place = find_city(f"{city}, {state}")
            if point and place and haversine_miles(point[0], point[1], place.lat, place.lon) > 10:
                astray.append((city, state))
        self.assertEqual(astray, [])

    def test_unknown_places_and_bare_names_are_left_to_the_geocoder(self):
        for query in ("Nowhere, TX", "Dallas", "Paris, France", "Dallas, Ontario", "100 Main St, Dallas, TX"):
            self.assertIsNone(find_city(query), query)


class SuggestTests(SimpleTestCase):
    """What the map page offers as you type, against the committed table."""

    def labels(self, query, **options):
        return [place.label for place in suggest(query, **options)]

    def test_a_prefix_lists_the_biggest_cities_first_with_an_exact_name_ahead(self):
        offered = self.labels("dal")
        self.assertEqual(offered[0], "Dallas, TX")
        self.assertIn("Daly City, CA", offered)
        self.assertEqual(len(offered), 8)
        # Dallastown and Dallas Center come after every plain Dallas, however small.
        self.assertEqual(self.labels("dallas")[:5], ["Dallas, TX", "Dallas, OR", "Dallas, GA", "Dallas, NC", "Dallas, PA"])

    def test_a_comma_narrows_to_the_states_typed_so_far(self):
        self.assertEqual(self.labels("dallas, t"), ["Dallas, TX"])
        self.assertEqual(self.labels("dallas, tex"), ["Dallas, TX"])
        self.assertEqual(self.labels("dallas, zz"), [])
        self.assertEqual(self.labels("dallas,")[0], "Dallas, TX")
        self.assertIn("Springfield, MA", self.labels("springfield, m"))
        self.assertIn("Springfield, MO", self.labels("springfield, m"))
        self.assertNotIn("Springfield, IL", self.labels("springfield, m"))

    def test_a_whole_state_without_a_comma_narrows_too(self):
        self.assertEqual(self.labels("dallas tx"), ["Dallas, TX"])
        self.assertEqual(self.labels("dallas texas"), ["Dallas, TX"])
        self.assertEqual(self.labels("kansas city kansas"), ["Kansas City, KS"])

    def test_short_and_long_spellings_meet(self):
        self.assertEqual(self.labels("saint lou")[0], "St. Louis, MO")
        self.assertEqual(self.labels("st. l")[0], "St. Louis, MO")
        self.assertEqual(self.labels("ft wor"), ["Fort Worth, TX"])
        self.assertEqual(self.labels("mt vernon, wa"), ["Mount Vernon, WA"])

    def test_one_spot_is_offered_once(self):
        # The table carries "New York City" as a second name for New York.
        offered = self.labels("new york")
        self.assertEqual(offered[0], "New York, NY")
        self.assertNotIn("New York City, NY", offered)

    def test_nothing_for_nothing(self):
        for query in ("", "   ", ",", "xqzv"):
            self.assertEqual(self.labels(query), [], repr(query))

    def test_the_limit_holds_and_every_label_names_a_state(self):
        offered = self.labels("s", limit=3)
        self.assertEqual(len(offered), 3)
        for label in self.labels("d"):
            self.assertIn(label[-2:], STATE_NAMES, label)
            self.assertEqual(label[-4:-2], ", ", label)

    def test_every_city_in_the_table_resolves_to_itself_by_its_label(self):
        # The label is what the box ends up holding, so it has to come back
        # to the same spot the list promised.
        exact, _ = city_table()
        for city in exact.values():
            place = find_city(f"{city.name}, {city.state}")
            self.assertIsNotNone(place, city)
            self.assertEqual((place.lat, place.lon), (city.lat, city.lon), city)
