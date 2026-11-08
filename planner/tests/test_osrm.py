from unittest import mock

import numpy as np
import requests
from django.conf import settings
from django.test import SimpleTestCase, override_settings

import planner.osrm
from planner.osrm import NoRouteError, RouteResult, RoutingError, fetch_route

BIG_CABIN = (36.5379, -95.2214)
GILA_BEND = (32.9478, -112.7168)

OK_BODY = {
    "code": "Ok",
    "routes": [
        {
            "distance": 160934.4,
            "duration": 5400.0,
            "geometry": {
                "type": "LineString",
                "coordinates": [[-95.2214, 36.5379], [-100.0, 35.0], [-112.7168, 32.9478]],
            },
        }
    ],
}


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.status_code = status_code
        self.body = body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


@override_settings(OSRM_BASE_URL="http://osrm.test")
class FetchRouteTests(SimpleTestCase):
    def answer(self, body, status_code=200):
        get = mock.patch.object(planner.osrm._session, "get", return_value=FakeResponse(body, status_code)).start()
        self.addCleanup(mock.patch.stopall)
        return get

    def test_successful_route_is_converted_to_miles_seconds_and_lat_lon_points(self):
        self.answer(OK_BODY)
        route = fetch_route(BIG_CABIN, GILA_BEND)
        self.assertIsInstance(route, RouteResult)
        self.assertAlmostEqual(route.distance_miles, 100.0)
        self.assertEqual(route.duration_seconds, 5400.0)
        self.assertEqual(route.points.dtype, np.float64)
        self.assertEqual(route.points.shape, (3, 2))
        self.assertTrue(route.points.flags["C_CONTIGUOUS"])
        self.assertEqual(route.points.tolist(), [[36.5379, -95.2214], [35.0, -100.0], [32.9478, -112.7168]])

    def test_request_goes_to_the_driving_route_with_lon_lat_pairs(self):
        get = self.answer(OK_BODY)
        fetch_route(BIG_CABIN, GILA_BEND)
        get.assert_called_once()
        args, kwargs = get.call_args
        self.assertEqual(args[0], "http://osrm.test/route/v1/driving/-95.221400,36.537900;-112.716800,32.947800")
        self.assertEqual(kwargs["params"]["overview"], "full")
        self.assertEqual(kwargs["params"]["geometries"], "geojson")
        self.assertEqual(kwargs["headers"]["User-Agent"], settings.USER_AGENT)
        self.assertEqual(kwargs["timeout"], settings.EXTERNAL_TIMEOUT)

    def test_no_route_code_raises_no_route_error(self):
        self.answer({"code": "NoRoute", "message": "Impossible route between points"}, status_code=400)
        with self.assertRaises(NoRouteError):
            fetch_route(BIG_CABIN, GILA_BEND)

    def test_no_segment_code_raises_no_route_error(self):
        self.answer({"code": "NoSegment", "message": "Could not find a matching segment"}, status_code=400)
        with self.assertRaises(NoRouteError):
            fetch_route(BIG_CABIN, GILA_BEND)

    def test_any_other_code_raises_a_routing_error_that_names_it(self):
        self.answer({"code": "InvalidQuery"}, status_code=400)
        with self.assertRaises(RoutingError) as caught:
            fetch_route(BIG_CABIN, GILA_BEND)
        self.assertNotIsInstance(caught.exception, NoRouteError)
        self.assertIn("InvalidQuery", str(caught.exception))

    def test_body_that_is_not_an_object_raises_a_routing_error(self):
        self.answer([OK_BODY])
        with self.assertRaises(RoutingError) as caught:
            fetch_route(BIG_CABIN, GILA_BEND)
        self.assertNotIsInstance(caught.exception, NoRouteError)

    def test_connection_failure_raises_a_routing_error(self):
        mock.patch.object(
            planner.osrm._session, "get", side_effect=requests.ConnectionError("connection refused")
        ).start()
        self.addCleanup(mock.patch.stopall)
        with self.assertRaises(RoutingError) as caught:
            fetch_route(BIG_CABIN, GILA_BEND)
        self.assertIsInstance(caught.exception.__cause__, requests.ConnectionError)

    def test_body_that_is_not_json_raises_a_routing_error(self):
        self.answer(ValueError("Expecting value"), status_code=502)
        with self.assertRaises(RoutingError):
            fetch_route(BIG_CABIN, GILA_BEND)

    def test_ok_body_without_a_usable_route_raises_a_routing_error(self):
        broken_bodies = [
            {"code": "Ok"},
            {"code": "Ok", "routes": []},
            {"code": "Ok", "routes": [{"distance": 1.0, "duration": 1.0}]},
        ]
        for body in broken_bodies:
            with self.subTest(body=body):
                self.answer(body)
                with self.assertRaises(RoutingError):
                    fetch_route(BIG_CABIN, GILA_BEND)
