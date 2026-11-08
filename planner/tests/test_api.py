import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.throttling import AnonRateThrottle

from planner.geo import haversine_miles
from planner.models import Station
from planner.osrm import NoRouteError, RouteResult, RoutingError
from planner.photon import GeocodeError, Place
from planner.trip import load_leg

# A straight route due east along the 35th parallel, a little over 1,100 miles.
START = Place(lat=35.0, lon=-100.0, label="Start, Texas")
FINISH = Place(lat=35.0, lon=-80.0, label="Finish, Georgia")
PLACES = {
    "Start, TX": START,
    "Finish, GA": FINISH,
    # Where the real geocoder puts Dallas: nearly a mile from the city table's point.
    "Dallas": Place(lat=32.77627, lon=-96.79686, label="Dallas, Texas, United States"),
}

LOCAL_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def straight_route(points=None, distance_miles=None):
    points = np.array(points or [(35.0, lon / 10) for lon in range(-1000, -799)])
    if distance_miles is None:
        distance_miles = haversine_miles(points[:-1, 0], points[:-1, 1], points[1:, 0], points[1:, 1]).sum()
    return RouteResult(distance_miles=distance_miles, duration_seconds=distance_miles * 60, points=points)


def fake_geocode(query):
    return PLACES.get(query)


def station(opis_id, lat, lon, price, name=None):
    return Station.objects.create(
        opis_id=opis_id,
        name=name or f"Station {opis_id}",
        address="I-40, EXIT 1",
        city="Somewhere",
        state="TX",
        rack_id=1,
        price=price,
        price_count=1,
        lat=lat,
        lon=lon,
    )


@override_settings(CACHES=LOCAL_CACHE)
class RouteApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.geocode = mock.patch("planner.trip.geocode_place", side_effect=fake_geocode).start()
        self.fetch_route = mock.patch("planner.trip.fetch_route", return_value=straight_route()).start()
        self.addCleanup(mock.patch.stopall)

    def seed_stations(self):
        station(1, 35.0, -100.05, "3.400", name="Closest to start")
        station(2, 35.0, -99.0, "3.000")
        station(3, 35.0, -92.0, "2.500", name="Cheapest on the route")
        station(4, 35.0, -85.0, "3.200")
        station(5, 36.0, -92.0, "1.000", name="Cheap but 69 miles off the route")

    def plan(self, **params):
        params.setdefault("start", "Start, TX")
        params.setdefault("finish", "Finish, GA")
        return self.client.get(reverse("route-api"), params)

    def test_plans_a_trip_from_an_empty_tank(self):
        self.seed_stations()
        response = self.plan(stop_cost=0)
        self.assertEqual(response.status_code, 200)
        trip = response.json()

        self.assertEqual([s["station"]["id"] for s in trip["stops"]], [1, 2, 3, 4])
        self.assertEqual([s["kind"] for s in trip["stops"]], ["departure", "en_route", "en_route", "en_route"])
        self.assertEqual(trip["summary"]["en_route_stops"], 3)
        self.assertAlmostEqual(trip["route"]["distance_miles"], 1132.0, delta=0.5)
        self.assertAlmostEqual(
            trip["summary"]["fuel_purchased_gallons"], trip["summary"]["fuel_consumed_gallons"], delta=0.2
        )
        # 5.7 gal at 3.40, 39.6 at 3.00, a full 50 at 2.50, then 18 at 3.20.
        self.assertAlmostEqual(trip["summary"]["total_fuel_cost"], 320.8, delta=1.5)
        self.assertEqual(trip["route"]["geometry"]["type"], "LineString")
        self.assertEqual(trip["route"]["geometry"]["coordinates"][0], [-100.0, 35.0])
        self.assertIn("start=Start%2C+TX", trip["map_url"])

    def test_each_stop_reports_the_leg_the_tank_and_the_cost(self):
        self.seed_stations()
        stop = self.plan(stop_cost=0).json()["stops"][2]
        self.assertEqual(stop["station"]["name"], "Cheapest on the route")
        self.assertAlmostEqual(stop["mile"], 453, delta=1)
        self.assertAlmostEqual(stop["leg_miles"], 396, delta=1)
        self.assertAlmostEqual(stop["tank_on_arrival_gallons"], 0, delta=0.15)
        self.assertAlmostEqual(stop["gallons"], 50, delta=0.15)
        self.assertAlmostEqual(stop["tank_on_departure_gallons"], 50, delta=0.15)
        self.assertAlmostEqual(stop["cost"], 125, delta=0.5)
        self.assertLess(stop["off_route_miles"], 1)

    def test_full_tank_at_start_skips_the_early_stops(self):
        self.seed_stations()
        trip = self.plan(start_fuel=50, stop_cost=0).json()
        self.assertEqual([s["station"]["id"] for s in trip["stops"]], [3, 4])
        self.assertEqual(trip["vehicle"]["start_fuel_gallons"], 50)
        self.assertAlmostEqual(trip["summary"]["fuel_purchased_gallons"], 63.3, delta=0.3)

    def test_departure_is_the_cheapest_station_near_the_start(self):
        self.seed_stations()
        station(6, 35.05, -100.1, "3.100", name="Across town but cheaper")
        trip = self.plan(stop_cost=0).json()
        self.assertEqual(trip["stops"][0]["station"]["name"], "Across town but cheaper")
        self.assertAlmostEqual(trip["stops"][0]["off_route_miles"], 6.6, delta=0.2)

    def test_without_a_station_near_the_start_the_tank_must_be_given(self):
        station(3, 35.0, -92.0, "2.500")
        response = self.plan()
        self.assertEqual(response.status_code, 422)
        self.assertIn("start_fuel", response.json()["error"])
        self.fetch_route.return_value = straight_route([(35.0, -100.0), (35.0, -92.9)], distance_miles=400)
        self.assertEqual(self.plan(start_fuel=50, finish="35,-92.9").status_code, 200)

    def test_stop_cost_discourages_marginal_stops(self):
        station(1, 35.0, -100.05, "3.000")
        station(2, 35.0, -99.0, "2.900")
        self.fetch_route.return_value = straight_route([(35.0, -100.0), (35.0, -96.47)], distance_miles=200)
        # Fourteen gallons at ten cents less saves $1.43: worth a stop at no
        # charge, not worth one at $10.
        self.assertEqual(len(self.plan(stop_cost=0).json()["stops"]), 2)
        self.assertEqual(len(self.plan().json()["stops"]), 1)

    def test_coordinates_bypass_the_geocoder(self):
        self.seed_stations()
        response = self.plan(start="35.0,-100.0", finish="35, -80")
        self.assertEqual(response.status_code, 200)
        self.geocode.assert_not_called()
        self.assertEqual(response.json()["start"]["label"], "35.00000, -100.00000")

    def test_a_city_and_state_are_resolved_without_the_geocoder(self):
        self.seed_stations()
        response = self.plan(start="Amarillo, TX", finish="Charlotte, NC", start_fuel=50)
        self.assertEqual(response.status_code, 200)
        self.geocode.assert_not_called()
        self.assertEqual(response.json()["start"]["label"], "Amarillo, Texas, United States")

    def test_start_and_finish_in_the_same_place_are_rejected(self):
        self.seed_stations()
        for finish in ("dallas texas", "Dallas"):
            response = self.plan(start="Dallas, TX", finish=finish)
            self.assertEqual(response.status_code, 422, finish)
            self.assertEqual(response.json()["error"], "start and finish are within 2 miles of each other")
        self.fetch_route.assert_not_called()

    def test_totals_are_the_sums_of_the_stops_shown(self):
        self.seed_stations()
        trip = self.plan(stop_cost=0).json()
        self.assertEqual(trip["summary"]["total_fuel_cost"], round(sum(s["cost"] for s in trip["stops"]), 2))
        self.assertEqual(
            trip["summary"]["fuel_purchased_gallons"], round(sum(s["gallons"] for s in trip["stops"]), 2)
        )

    def test_an_unusable_cache_does_not_take_the_api_down(self):
        self.seed_stations()
        with tempfile.TemporaryDirectory() as tmp:
            not_a_directory = Path(tmp) / "cache"
            not_a_directory.write_text("")
            broken = {"default": {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache", "LOCATION": str(not_a_directory)}}
            with override_settings(CACHES=broken), self.assertLogs("planner", "WARNING") as logs:
                self.assertEqual(self.plan().status_code, 200)
                self.assertEqual(self.plan().status_code, 200)
        self.assertEqual(self.fetch_route.call_count, 2)
        self.assertTrue(any("cache unavailable" in line for line in logs.output))

    def test_coordinates_outside_the_us_are_rejected(self):
        self.seed_stations()
        response = self.plan(start="51.5,-0.1")
        self.assertEqual(response.status_code, 422)
        self.assertIn("51.5,-0.1", response.json()["error"])

    def test_missing_parameters_are_a_bad_request(self):
        self.seed_stations()
        response = self.client.get(reverse("route-api"), {"start": "Start, TX"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("finish", response.json()["details"])

    def test_out_of_range_numbers_are_a_bad_request(self):
        self.seed_stations()
        self.assertEqual(self.plan(start_fuel=60).status_code, 400)
        self.assertEqual(self.plan(stop_cost=-1).status_code, 400)
        self.assertEqual(self.plan(stop_cost=1e9).status_code, 400)

    def test_unknown_place(self):
        self.seed_stations()
        response = self.plan(finish="Atlantis")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "could not find 'Atlantis' in the United States")

    def test_no_road_between_the_points(self):
        self.seed_stations()
        self.fetch_route.side_effect = NoRouteError("no road route between those points")
        self.assertEqual(self.plan().status_code, 422)

    def test_no_station_within_range(self):
        station(1, 35.0, -100.05, "3.400")
        response = self.plan()
        self.assertEqual(response.status_code, 422)
        self.assertIn("mile", response.json()["error"])

    def test_upstream_failures_are_a_bad_gateway(self):
        self.seed_stations()
        self.fetch_route.side_effect = RoutingError("routing service unavailable")
        self.assertEqual(self.plan().status_code, 502)
        self.fetch_route.side_effect = None
        self.geocode.side_effect = GeocodeError("geocoding service unavailable")
        self.assertEqual(self.plan().status_code, 502)

    def test_without_station_data_the_service_is_unavailable(self):
        self.assertEqual(self.plan().status_code, 503)

    def test_stations_loaded_later_are_picked_up(self):
        self.assertEqual(self.plan().status_code, 503)
        self.seed_stations()
        self.assertEqual(self.plan().status_code, 200)

    def test_only_get_is_allowed(self):
        response = self.client.post(reverse("route-api"), {})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json(), {"error": 'Method "POST" not allowed.'})

    def test_a_client_is_throttled_past_the_rate_limit(self):
        self.seed_stations()
        # The rates are read once at import, so overriding the setting is not enough.
        with mock.patch.object(AnonRateThrottle, "THROTTLE_RATES", {"anon": "2/min"}):
            self.assertEqual(self.plan().status_code, 200)
            self.assertEqual(self.plan().status_code, 200)
            response = self.plan()
        self.assertEqual(response.status_code, 429)
        self.assertIn("throttled", response.json()["error"])
        self.assertEqual(self.client.get(reverse("health")).status_code, 200)

    def test_blank_optional_parameters_are_the_same_as_absent_ones(self):
        self.seed_stations()
        response = self.plan(start_fuel="", stop_cost="")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["vehicle"]["stop_cost"], 10.0)

    def test_route_is_fetched_once_and_then_cached(self):
        self.seed_stations()
        self.plan()
        self.plan(stop_cost=0)
        self.assertEqual(self.fetch_route.call_count, 1)


@override_settings(CACHES=LOCAL_CACHE)
class RouteFetchTests(TestCase):
    def test_identical_requests_arriving_together_fetch_the_route_once(self):
        cache.clear()

        def slow_route(*args):
            time.sleep(0.2)
            return straight_route()

        with mock.patch("planner.trip.fetch_route", side_effect=slow_route) as fetch:
            threads = [threading.Thread(target=load_leg, args=(START, FINISH)) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(fetch.call_count, 1)
        self.assertIsNotNone(cache.get("route:35.00000,-100.00000:35.00000,-80.00000"))


class HealthTests(TestCase):
    def test_unavailable_until_stations_are_loaded(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "no stations loaded")

    def test_reports_how_many_stations_are_loaded_and_placed(self):
        station(1, 35.0, -100.0, "3.000")
        Station.objects.create(
            opis_id=2, name="Unplaced", address="", city="Nowhere", state="TX", rack_id=1, price="3.000", price_count=1
        )
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "stations": 2, "located": 1})
