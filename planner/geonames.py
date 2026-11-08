"""
City coordinates from the GeoNames gazetteer.

Looking up 3,800 cities one at a time against a public geocoder is slow and
gets you throttled; both Nominatim and Photon did exactly that. GeoNames
publishes the whole US gazetteer as a single download, so the bulk lookup is
done offline against it, and only the names it does not know go to a geocoder.

Places are matched on a normalised name plus the state. Where a state has two
places of the same name the town beats a neighbourhood and the larger
population wins.
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

# Parts of a town rather than the town: sections, neighbourhoods, farms, ruins.
SECONDARY_CODES = {"PPLX", "PPLL", "PPLQ", "PPLW", "PPLH", "PPLR", "PPLS", "PPLF", "PPLCH"}

# The price file writes "Saint Louis" and "Mount Vernon"; GeoNames prefers the
# short forms. Both sides are folded before comparing.
SHORT_WORDS = {"saint": "st", "sainte": "ste", "mount": "mt", "fort": "ft"}


def normalize_name(name):
    """Case, punctuation and spacing folded away: "Mc Calla" and "McCalla" agree."""
    words = re.sub(r"[^a-z0-9]", " ", name.casefold()).split()
    return "".join(SHORT_WORDS.get(word, word) for word in words)


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


def build_index(zip_path):
    """{(normalised name, state): (lat, lon, name)} for every populated place in the dump."""
    best = {}
    with zipfile.ZipFile(zip_path) as archive, archive.open("US.txt") as raw:
        rows = csv.reader(io.TextIOWrapper(raw, encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE)
        for fields in rows:
            if fields[FEATURE_CLASS] != "P":
                continue
            rank = (fields[FEATURE_CODE] not in SECONDARY_CODES, int(fields[POPULATION] or 0))
            place = (float(fields[LAT]), float(fields[LON]), fields[NAME])
            names = {fields[NAME], fields[ASCII_NAME], *fields[ALTERNATE_NAMES].split(",")}
            for name in names:
                if not name:
                    continue
                key = (normalize_name(name), fields[ADMIN1])
                if key not in best or rank > best[key][0]:
                    best[key] = (rank, place)
    return {key: place for key, (rank, place) in best.items()}
