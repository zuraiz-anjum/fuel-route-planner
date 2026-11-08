"""
City coordinates from the GeoNames gazetteer.

Looking up 3,800 cities one at a time against a public geocoder is slow and
gets you throttled; both Nominatim and Photon did exactly that. GeoNames
publishes the whole US gazetteer as a single download, so the bulk lookup is
done offline against it.

Places are matched on a normalised name plus the state. A populated place
wins over a township or other administrative area, which wins over a
locality or spot feature; among equals the larger population wins. Names
that only differ in word order ("Pueblo of Acoma" against "Acoma Pueblo")
are matched as a last resort.
"""

import csv
import io
import re
import zipfile

import requests
from django.conf import settings

DUMP_URL = "https://download.geonames.org/export/dump/US.zip"

# Column positions in the dump.
NAME, ASCII_NAME, ALTERNATE_NAMES, LAT, LON, FEATURE_CLASS, FEATURE_CODE = 1, 2, 3, 4, 5, 6, 7
ADMIN1, POPULATION = 10, 14

# Feature classes worth considering, best first: populated places, then the
# administrative areas New England files its towns under, then localities
# and spot features such as a marina that a truck stop takes its address from.
CLASS_RANK = {"P": 3, "A": 2, "L": 1, "S": 1}
AREA_CODES = {"ADM3", "ADM4", "ADM5", "ADMD"}

# Parts of a town rather than the town: sections, neighbourhoods, farms, ruins.
SECONDARY_CODES = {"PPLX", "PPLL", "PPLQ", "PPLW", "PPLH", "PPLR", "PPLS", "PPLF", "PPLCH"}

# The price file writes "Saint Louis", "Mount Vernon" and "S Coffeyville";
# GeoNames prefers the short forms of the first two and the long form of the
# third. Both sides are folded before comparing.
SHORT_WORDS = {
    "saint": "st", "sainte": "ste", "mount": "mt", "fort": "ft",
    "north": "n", "south": "s", "east": "e", "west": "w",
}
FILLER_WORDS = {"of", "the"}
AREA_WORDS = {"township", "town", "city", "borough", "village"}


def _words(name):
    words = re.sub(r"[^a-z0-9]", " ", name.casefold()).split()
    return [SHORT_WORDS.get(word, word) for word in words]


def normalize_name(name):
    """Case, punctuation and spacing folded away: "Mc Calla" and "McCalla" agree."""
    return "".join(_words(name))


def loose_name(name):
    """Word order folded away too, for names like "Pueblo of Acoma" and "Acoma Pueblo"."""
    return "".join(sorted(word for word in _words(name) if word not in FILLER_WORDS))


def area_name(name):
    """"Township of Crescent" and "Corinth Town" as just the place name."""
    words = name.casefold().split()
    if len(words) > 2 and words[0] in AREA_WORDS and words[1] == "of":
        words = words[2:]
    elif len(words) > 1 and words[-1] in AREA_WORDS:
        words = words[:-1]
    return " ".join(words)


def ensure_dump(path=None):
    """Download the gazetteer if it is not already on disk; it is about 80 MB."""
    path = path or settings.GEONAMES_ZIP
    if path.exists():
        return path
    partial = path.with_suffix(".part")
    with requests.get(DUMP_URL, stream=True, timeout=settings.EXTERNAL_TIMEOUT) as response:
        response.raise_for_status()
        with partial.open("wb") as fh:
            for chunk in response.iter_content(1 << 20):
                fh.write(chunk)
    partial.replace(path)
    return path


class Gazetteer:
    """Coordinates for a given set of (city, state) pairs, read from the dump in one pass."""

    def __init__(self, zip_path, cities):
        exact_wanted = {(normalize_name(city), state) for city, state in cities}
        loose_wanted = {(loose_name(city), state) for city, state in cities}
        exact, loose = {}, {}
        with zipfile.ZipFile(zip_path) as archive, archive.open("US.txt") as raw:
            rows = csv.reader(io.TextIOWrapper(raw, encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE)
            for fields in rows:
                feature_class, code = fields[FEATURE_CLASS], fields[FEATURE_CODE]
                if feature_class not in CLASS_RANK or (feature_class == "A" and code not in AREA_CODES):
                    continue
                state = fields[ADMIN1]
                rank = (CLASS_RANK[feature_class], code not in SECONDARY_CODES, int(fields[POPULATION] or 0))
                place = (float(fields[LAT]), float(fields[LON]), fields[NAME])
                names = {fields[NAME], fields[ASCII_NAME], *fields[ALTERNATE_NAMES].split(",")}
                if feature_class == "A":
                    names |= {area_name(name) for name in list(names)}
                for name in names:
                    if not name:
                        continue
                    key = (normalize_name(name), state)
                    if key in exact_wanted and (key not in exact or rank > exact[key][0]):
                        exact[key] = (rank, place)
                    key = (loose_name(name), state)
                    if key in loose_wanted and (key not in loose or rank > loose[key][0]):
                        loose[key] = (rank, place)
        self.exact = {key: place for key, (rank, place) in exact.items()}
        self.loose = {key: place for key, (rank, place) in loose.items()}

    def find(self, city, state):
        """(lat, lon, name) for the city, or None."""
        return self.exact.get((normalize_name(city), state)) or self.loose.get((loose_name(city), state))
