import tempfile
import zipfile
from pathlib import Path

from django.test import SimpleTestCase

from planner.geonames import Gazetteer, area_name, loose_name, normalize_name


def dump_line(geonameid, name, alternates, lat, lon, code, state, population, feature_class="P"):
    fields = [""] * 19
    fields[0] = str(geonameid)
    fields[1] = fields[2] = name
    fields[3] = alternates
    fields[4], fields[5] = str(lat), str(lon)
    fields[6], fields[7] = feature_class, code
    fields[8], fields[10] = "US", state
    fields[14] = str(population)
    return "\t".join(fields)


DUMP = "\n".join(
    [
        dump_line(1, "McCalla", "Mc Calla", 33.3487, -87.0142, "PPL", "AL", 0),
        dump_line(2, "St. Louis", "Saint Louis,Сент-Луис", 38.6270, -90.1994, "PPLA2", "MO", 300000),
        dump_line(3, "Springfield", "", 39.7817, -89.6501, "PPLA", "IL", 114000),
        dump_line(4, "Springfield", "", 39.9, -89.7, "PPLX", "IL", 999999),
        dump_line(5, "Springfield", "", 37.2153, -93.2982, "PPLA2", "MO", 170000),
        dump_line(6, "Mount Pleasant", "", 43.5978, -84.7675, "PPL", "MI", 26000),
        dump_line(7, "A Mountain", "", 32.2, -110.9, "MT", "AZ", 0, feature_class="T"),
        dump_line(8, "South Coffeyville", "", 36.9945, -95.6203, "PPL", "OK", 772),
        dump_line(9, "Township of Crescent", "", 40.5563, -80.2286, "ADM3", "PA", 0, feature_class="A"),
        dump_line(10, "Willow Beach", "", 35.8703, -114.6594, "", "AZ", 0, feature_class="S"),
        dump_line(11, "Acoma Pueblo", "", 34.7839, -107.6677, "ADMD", "NM", 0, feature_class="A"),
        dump_line(12, "Acoma Pueblo", "", 34.8964, -107.5817, "PPL", "NM", 0),
        dump_line(13, "Corinth", "", 44.9, -69.0, "PPL", "ME", 0),
        dump_line(14, "Corinth", "", 44.95, -69.05, "", "ME", 0, feature_class="S"),
        dump_line(15, "Dallas", "", 32.7767, -96.7970, "PPLA2", "TX", 1300000),
    ]
)

CITIES = [
    ("Mc Calla", "AL"), ("Saint Louis", "MO"), ("Springfield", "IL"), ("Springfield", "MO"),
    ("Mount Pleasant", "MI"), ("A Mountain", "AZ"), ("S Coffeyville", "OK"), ("Crescent", "PA"),
    ("Willow Beach", "AZ"), ("Pueblo Of Acoma", "NM"), ("Corinth", "ME"),
]


class NameFoldingTests(SimpleTestCase):
    def test_folds_case_punctuation_and_spacing(self):
        self.assertEqual(normalize_name("Mc Calla"), normalize_name("McCalla"))
        self.assertEqual(normalize_name("Bois D Arc"), normalize_name("Bois D'Arc"))
        self.assertEqual(normalize_name("Winston Salem"), normalize_name("Winston-Salem"))
        self.assertEqual(normalize_name("HOMER GLEN "), "homerglen")

    def test_folds_abbreviated_words(self):
        self.assertEqual(normalize_name("Saint Louis"), normalize_name("St. Louis"))
        self.assertEqual(normalize_name("Sault Sainte Marie"), normalize_name("Sault Ste. Marie"))
        self.assertEqual(normalize_name("Mount Vernon"), normalize_name("Mt Vernon"))
        self.assertEqual(normalize_name("Fort Wayne"), normalize_name("Ft. Wayne"))
        self.assertEqual(normalize_name("S Coffeyville"), normalize_name("South Coffeyville"))
        self.assertEqual(normalize_name("N Las Vegas"), normalize_name("North Las Vegas"))

    def test_loose_name_ignores_word_order_and_filler(self):
        self.assertEqual(loose_name("Pueblo of Acoma"), loose_name("Acoma Pueblo"))
        self.assertNotEqual(loose_name("Lake Forest"), loose_name("Forest Lake Village"))

    def test_area_name_strips_the_administrative_wording(self):
        self.assertEqual(area_name("Township of Crescent"), "crescent")
        self.assertEqual(area_name("Town of Corinth"), "corinth")
        self.assertEqual(area_name("Corinth Town"), "corinth")
        self.assertEqual(area_name("Township"), "township")


class GazetteerTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "US.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("US.txt", DUMP)
        self.gazetteer = Gazetteer(path, CITIES)

    def test_matches_alternate_names(self):
        self.assertEqual(self.gazetteer.find("Mc Calla", "AL"), (33.3487, -87.0142, "McCalla"))
        self.assertEqual(self.gazetteer.find("Saint Louis", "MO")[2], "St. Louis")

    def test_town_beats_section_of_town_regardless_of_population(self):
        self.assertEqual(self.gazetteer.find("Springfield", "IL")[:2], (39.7817, -89.6501))

    def test_same_name_in_another_state_is_a_different_place(self):
        self.assertEqual(self.gazetteer.find("Springfield", "MO")[:2], (37.2153, -93.2982))

    def test_abbreviated_direction_matches_the_full_name(self):
        self.assertEqual(self.gazetteer.find("S Coffeyville", "OK")[2], "South Coffeyville")

    def test_township_matches_without_its_wording(self):
        self.assertEqual(self.gazetteer.find("Crescent", "PA")[2], "Township of Crescent")

    def test_spot_feature_is_a_last_resort(self):
        self.assertEqual(self.gazetteer.find("Willow Beach", "AZ")[2], "Willow Beach")
        self.assertEqual(self.gazetteer.find("Corinth", "ME")[:2], (44.9, -69.0))

    def test_reversed_word_order_matches_and_prefers_the_populated_place(self):
        self.assertEqual(self.gazetteer.find("Pueblo Of Acoma", "NM")[:2], (34.8964, -107.5817))

    def test_skips_features_that_are_not_places(self):
        self.assertIsNone(self.gazetteer.find("A Mountain", "AZ"))

    def test_only_indexes_the_cities_asked_for(self):
        self.assertIsNone(self.gazetteer.find("Dallas", "TX"))
        self.assertNotIn(("dallas", "TX"), self.gazetteer.exact)
