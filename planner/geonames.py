"""
City coordinates from the GeoNames gazetteer.

Looking up 3,800 cities one at a time against a public geocoder is slow and
gets you throttled; both Nominatim and Photon did exactly that. GeoNames
publishes the whole US gazetteer as a single download, so the bulk lookup is
done offline against it.

Places are matched on a normalised name plus the state. A place's own name
counts for more than a name it is merely also known by: El Paso lists
"Franklin" among its old names, and the Franklin in the price file is the
small town in Robertson County. Then a populated place wins over a township
or other administrative area, which wins over a locality or spot feature,
and among equals the larger population wins. Names that only differ in word
order ("Pueblo of Acoma" against "Acoma Pueblo") are matched as a last
resort.
"""

import csv
import io
import re
import zipfile
from collections import defaultdict, namedtuple

import requests
from django.conf import settings

from .geo import haversine_miles

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

# Capitals and county seats, which GeoNames marks. Together with the places
# that have an incorporated "City of X" or "Town of X" area filed alongside,
# these are the proper towns, as against a hamlet or census tract of the same
# name whose population figure happens to be larger.
SEAT_CODES = {"PPLC", "PPLA", "PPLA2", "PPLA3", "PPLA4"}
TWIN_MILES = 3.0

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


def proper_town(code, lat, lon, areas):
    """A county seat, or a place with an incorporated twin within a few miles."""
    return code in SEAT_CODES or any(
        haversine_miles(lat, lon, area_lat, area_lon) <= TWIN_MILES for area_lat, area_lon in areas
    )


# What the gazetteer knows about one candidate: where it is, what GeoNames
# calls it, and how it ranks. The rank is (own name, feature class, not a part
# of a town, proper town, population); `namesake` says it is a populated place
# in its own right, so that two of them for one name means a real ambiguity.
Match = namedtuple("Match", "lat lon name rank namesake")


class Gazetteer:
    """Candidates for a given set of (city, state) pairs, read from the dump in one pass."""

    def __init__(self, zip_path, cities):
        wanted = {
            normalize_name: {(normalize_name(city), state) for city, state in cities},
            loose_name: {(loose_name(city), state) for city, state in cities},
        }
        # Every row that could be one of the wanted places, with the
        # incorporated areas of the same names, so that a town can be told
        # from a hamlet once the whole dump has been read.
        rows = {fold: defaultdict(list) for fold in wanted}
        areas = {fold: defaultdict(list) for fold in wanted}
        with zipfile.ZipFile(zip_path) as archive, archive.open("US.txt") as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE)
            for fields in reader:
                feature_class, code = fields[FEATURE_CLASS], fields[FEATURE_CODE]
                if feature_class not in CLASS_RANK or (feature_class == "A" and code not in AREA_CODES):
                    continue
                state = fields[ADMIN1]
                lat, lon = float(fields[LAT]), float(fields[LON])
                own = {fields[NAME], fields[ASCII_NAME]}
                if feature_class == "A":
                    own |= {area_name(name) for name in list(own)}
                also_known_as = set(fields[ALTERNATE_NAMES].split(",")) - own
                for is_own, names in ((True, own), (False, also_known_as)):
                    for name in filter(None, names):
                        for fold in wanted:
                            key = (fold(name), state)
                            if key not in wanted[fold]:
                                continue
                            population = int(fields[POPULATION] or 0)
                            rows[fold][key].append((is_own, feature_class, code, population, lat, lon, fields[NAME]))
                            if is_own and feature_class == "A":
                                areas[fold][key].append((lat, lon))
        self.matches = {
            fold: {key: self._ranked(found, areas[fold][key]) for key, found in rows[fold].items()}
            for fold in wanted
        }

    @staticmethod
    def _ranked(rows, areas):
        def rank(row):
            is_own, feature_class, code, population, lat, lon, _ = row
            proper = feature_class == "P" and proper_town(code, lat, lon, areas)
            return (is_own, CLASS_RANK[feature_class], code not in SECONDARY_CODES, proper, population)

        matches, seen = [], set()
        for row in sorted(rows, key=rank, reverse=True):
            is_own, feature_class, code, population, lat, lon, name = row
            if (lat, lon) in seen:
                continue
            seen.add((lat, lon))
            namesake = is_own and feature_class == "P" and code not in SECONDARY_CODES
            matches.append(Match(lat, lon, name, rank(row), namesake))
        return matches

    def candidates(self, city, state):
        """Matches for the city, best first; empty if GeoNames has nothing."""
        return (
            self.matches[normalize_name].get((normalize_name(city), state))
            or self.matches[loose_name].get((loose_name(city), state))
            or []
        )

    def find(self, city, state):
        """(lat, lon, name) for the city, or None."""
        matches = self.candidates(city, state)
        return (matches[0].lat, matches[0].lon, matches[0].name) if matches else None
