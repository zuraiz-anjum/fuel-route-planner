from unittest import mock

from django.test import TestCase
from django.urls import reverse

from planner.geo import haversine_miles
from planner.models import Station
from planner.osrm import NoRouteError, RouteResult, RoutingError
from planner.photon import GeocodeError, Place
from planner.trip import station_arrays

# A straight route due east along the 35th parallel, a little over 1,100 miles.
START = Place(lat=35.0, lon=-100.0, label="Start, Texas")
FINISH = Place(lat=35.0, lon=-80.0, label="Finish, Georgia")
PLACES = {"Start, TX": START, "Finish, GA": FINISH}


def encode_polyline(points):
    out = []
    prev_lat = prev_lon = 0
    for lat, lon in points:
        lat, lon = round(lat * 1e5), round(lon * 1e5)
        for delta in (lat - prev_lat, lon - prev_lon):
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 0x20:
                out.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            out.append(chr(delta + 63))
        prev_lat, prev_lon = lat, lon
    return "".join(out)


def straight_route():
    points = [(35.0, lon / 10) for lon in range(-1000, -799)]
    miles = sum(haversine_miles(a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:]))
    return RouteResult(distance_miles=miles, duration_seconds=miles / 60 * 3600, polyline=encode_polyline(points))


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


class RouteApiTests(TestCase):
    def setUp(self):
        station_arrays.cache_clear()
        self.addCleanup(station_arrays.cache_clear)
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
        self.assertIn("map_url", trip)
        self.assertIn("start=Start%2C+TX", trip["map_url"])

    def test_each_stop_reports_the_tank_and_the_cost(self):
        self.seed_stations()
        stop = self.plan(stop_cost=0).json()["stops"][2]
        self.assertEqual(stop["station"]["name"], "Cheapest on the route")
        self.assertAlmostEqual(stop["mile"], 453, delta=1)
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

    def test_stop_cost_discourages_marginal_stops(self):
        station(1, 35.0, -100.05, "3.000")
        station(2, 35.0, -99.0, "2.990")
        self.fetch_route.return_value = RouteResult(
            distance_miles=200, duration_seconds=12000, polyline=encode_polyline([(35.0, -100.0), (35.0, -96.47)])
        )
        self.assertEqual(len(self.plan(stop_cost=0).json()["stops"]), 2)
        self.assertEqual(len(self.plan().json()["stops"]), 1)

    def test_coordinates_bypass_the_geocoder(self):
        self.seed_stations()
        response = self.plan(start="35.0,-100.0", finish="35, -80")
        self.assertEqual(response.status_code, 200)
        self.geocode.assert_not_called()
        self.assertEqual(response.json()["start"]["label"], "35.00000, -100.00000")

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

    def test_start_fuel_beyond_the_tank_is_a_bad_request(self):
        self.seed_stations()
        self.assertEqual(self.plan(start_fuel=60).status_code, 400)

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
        self.fetch_route.side_effect = RoutingError("routing request failed")
        self.assertEqual(self.plan().status_code, 502)
        self.fetch_route.side_effect = None
        self.geocode.side_effect = GeocodeError("geocoder request failed")
        self.assertEqual(self.plan().status_code, 502)

    def test_without_station_data_the_service_is_unavailable(self):
        self.assertEqual(self.plan().status_code, 503)

    def test_only_get_is_allowed(self):
        self.assertEqual(self.client.post(reverse("route-api"), {}).status_code, 405)

    def test_route_is_fetched_once_per_request(self):
        self.seed_stations()
        self.plan()
        self.assertEqual(self.fetch_route.call_count, 1)
