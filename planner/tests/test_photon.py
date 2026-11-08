from unittest import mock

import requests
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

import planner.photon
from planner.photon import GeocodeError, Place, geocode_city, geocode_place

LOCAL_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def feature(lat, lon, **properties):
    return {"type": "Feature", "properties": properties, "geometry": {"type": "Point", "coordinates": [lon, lat]}}


TORONTO = feature(43.65, -79.38, name="Dallas", city="Toronto", state="Ontario", country="Canada", countrycode="CA")
DALLAS = feature(32.78, -96.80, name="Dallas", state="Texas", country="United States", countrycode="US")
DALLAS_GA = feature(33.92, -84.84, name="Dallas", state="Georgia", country="United States", countrycode="US")

BIG_CABIN_KS = feature(
    38.0, -97.0, name="Big Cabin", state="Kansas", country="United States", countrycode="US", osm_value="town"
)
BIG_CABIN_SUBURB = feature(
    36.1, -95.9, name="Big Cabin", city="Tulsa", state="Oklahoma", country="United States",
    countrycode="US", osm_value="suburb",
)
BIG_CABIN_TOWN = feature(
    36.54, -95.22, name="Big Cabin", state="OK", country="United States", countrycode="US", osm_value="town"
)


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.status_code = status_code
        self.body = body

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


def found(*features):
    return FakeResponse({"type": "FeatureCollection", "features": list(features)})


@override_settings(CACHES=LOCAL_CACHE)
class PhotonTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.get = mock.patch.object(planner.photon._session, "get").start()
        self.sleep = mock.patch("planner.photon.time.sleep").start()
        self.addCleanup(mock.patch.stopall)

    def test_geocode_place_returns_the_first_us_hit_and_skips_foreign_ones_before_it(self):
        self.get.return_value = found(TORONTO, DALLAS, DALLAS_GA)
        place = geocode_place("Dallas")
        self.assertEqual(place, Place(lat=32.78, lon=-96.80, label="Dallas, Texas, United States"))

    def test_geocode_place_sends_the_query_with_user_agent_and_timeout(self):
        self.get.return_value = found(DALLAS)
        geocode_place("Dallas")
        args, kwargs = self.get.call_args
        self.assertEqual(args[0], settings.PHOTON_BASE_URL + "/api/")
        self.assertEqual(kwargs["params"]["q"], "Dallas")
        self.assertEqual(kwargs["params"]["bbox"], planner.photon.US_BBOX)
        self.assertEqual(kwargs["headers"]["User-Agent"], settings.USER_AGENT)
        self.assertEqual(kwargs["timeout"], settings.EXTERNAL_TIMEOUT)

    def test_geocode_place_returns_none_without_a_us_hit(self):
        self.get.return_value = found(TORONTO)
        self.assertIsNone(geocode_place("Dallas"))

    def test_label_leaves_out_parts_that_repeat(self):
        self.get.return_value = found(
            feature(40.71, -74.0, name="New York", city="New York", state="New York",
                    country="United States", countrycode="US")
        )
        self.assertEqual(geocode_place("New York").label, "New York, United States")

    def test_geocode_place_caches_hits_regardless_of_case_and_spacing(self):
        self.get.return_value = found(DALLAS)
        first = geocode_place("Dallas, TX")
        second = geocode_place("  dallas,   tx ")
        self.assertEqual(first, second)
        self.assertEqual(self.get.call_count, 1)

    def test_geocode_place_caches_misses(self):
        self.get.return_value = found(TORONTO)
        self.assertIsNone(geocode_place("Nowhere"))
        self.assertIsNone(geocode_place("NOWHERE"))
        self.assertEqual(self.get.call_count, 1)

    def test_busy_answer_is_retried_once_after_a_pause(self):
        self.get.side_effect = [FakeResponse(None, status_code=503), found(DALLAS)]
        self.assertEqual(geocode_place("Dallas").lat, 32.78)
        self.assertEqual(self.get.call_count, 2)
        self.sleep.assert_called_once_with(1)

    def test_failure_after_the_retry_or_a_lost_connection_raises_geocode_error(self):
        self.get.side_effect = [FakeResponse(None, status_code=503), FakeResponse(None, status_code=503)]
        with self.assertRaises(GeocodeError):
            geocode_place("Dallas")
        self.assertEqual(self.get.call_count, 2)

        self.get.side_effect = requests.ConnectionError("connection refused")
        with self.assertRaises(GeocodeError):
            geocode_place("Austin")

    def test_geocode_city_keeps_hits_in_the_state_and_prefers_a_town_to_a_suburb(self):
        self.get.return_value = found(BIG_CABIN_KS, BIG_CABIN_SUBURB, BIG_CABIN_TOWN)
        place = geocode_city("Big Cabin", "OK")
        self.assertEqual((place.lat, place.lon), (36.54, -95.22))
        params = self.get.call_args.kwargs["params"]
        self.assertEqual(params["q"], "Big Cabin, Oklahoma")
        self.assertEqual(params["osm_tag"], "place")

    def test_geocode_city_takes_a_suburb_when_it_is_the_only_hit_in_the_state(self):
        self.get.return_value = found(BIG_CABIN_KS, BIG_CABIN_SUBURB)
        place = geocode_city("Big Cabin", "OK")
        self.assertEqual(place, Place(lat=36.1, lon=-95.9, label="Big Cabin, Tulsa, Oklahoma, United States"))

    def test_geocode_city_returns_none_when_nothing_is_in_the_state(self):
        self.get.return_value = found(BIG_CABIN_KS)
        self.assertIsNone(geocode_city("Big Cabin", "OK"))
