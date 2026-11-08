import tempfile
import zipfile
from pathlib import Path

from django.test import SimpleTestCase

from planner.geonames import build_index, normalize_name


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
    ]
)


class NormalizeNameTests(SimpleTestCase):
    def test_folds_case_punctuation_and_spacing(self):
        self.assertEqual(normalize_name("Mc Calla"), normalize_name("McCalla"))
        self.assertEqual(normalize_name("Bois D Arc"), normalize_name("Bois D'Arc"))
        self.assertEqual(normalize_name("Winston Salem"), normalize_name("Winston-Salem"))
        self.assertEqual(normalize_name("HOMER GLEN "), "homerglen")

    def test_folds_saint_mount_and_fort(self):
        self.assertEqual(normalize_name("Saint Louis"), normalize_name("St. Louis"))
        self.assertEqual(normalize_name("Sault Sainte Marie"), normalize_name("Sault Ste. Marie"))
        self.assertEqual(normalize_name("Mount Vernon"), normalize_name("Mt Vernon"))
        self.assertEqual(normalize_name("Fort Wayne"), normalize_name("Ft. Wayne"))


class BuildIndexTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "US.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("US.txt", DUMP)
        self.index = build_index(path)

    def test_matches_alternate_names(self):
        self.assertEqual(self.index[("mccalla", "AL")], (33.3487, -87.0142, "McCalla"))
        self.assertEqual(self.index[(normalize_name("Saint Louis"), "MO")][2], "St. Louis")

    def test_town_beats_section_of_town_regardless_of_population(self):
        self.assertEqual(self.index[("springfield", "IL")][:2], (39.7817, -89.6501))

    def test_same_name_in_another_state_is_a_different_key(self):
        self.assertEqual(self.index[("springfield", "MO")][:2], (37.2153, -93.2982))

    def test_skips_features_that_are_not_populated_places(self):
        self.assertNotIn(("amountain", "AZ"), self.index)
        self.assertIn(("mtpleasant", "MI"), self.index)
