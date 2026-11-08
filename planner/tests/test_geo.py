import numpy as np
from django.test import SimpleTestCase

from planner.geo import Route, haversine_miles, simplify

# North along the 100th meridian from 30N to 40N, then east along 40N to 90W,
# with a vertex every degree.
L_ROUTE = [(lat, -100.0) for lat in range(30, 41)] + [(40.0, lon) for lon in range(-99, -89)]


class HaversineTests(SimpleTestCase):
    def test_new_york_to_los_angeles(self):
        miles = haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)
        self.assertAlmostEqual(miles, 2445, delta=10)

    def test_broadcasts_scalars_and_arrays(self):
        lats = np.array([30.0, 35.0, 40.0, 45.0])
        lons = np.array([-100.0, -95.0, -90.0, -85.0])
        self.assertEqual(haversine_miles(35.0, -95.0, lats, lons).shape, (4,))
        grid = haversine_miles(lats[:3, None], lons[:3, None], lats, lons)
        self.assertEqual(grid.shape, (3, 4))
        self.assertAlmostEqual(grid[1, 1], 0.0)


class RouteTests(SimpleTestCase):
    def test_miles_increase_and_end_at_given_total(self):
        route = Route(L_ROUTE, total_miles=1500.0)
        self.assertEqual(route.miles[0], 0.0)
        self.assertTrue(np.all(np.diff(route.miles) > 0))
        self.assertEqual(route.miles[-1], 1500.0)
        self.assertEqual(route.total_miles, 1500.0)

    def test_without_total_uses_polyline_length(self):
        route = Route([(30.0, -100.0), (35.0, -100.0), (40.0, -100.0)])
        self.assertAlmostEqual(route.total_miles, haversine_miles(30.0, -100.0, 40.0, -100.0), places=6)

    def test_identical_points_give_zero_mileage(self):
        route = Route([(35.0, -100.0)] * 3, total_miles=100.0)
        self.assertEqual(route.miles.tolist(), [0.0, 0.0, 0.0])

    def test_sample_steps_evenly_from_start_to_total(self):
        route = Route(L_ROUTE, total_miles=1234.5)
        lats, lons, miles = route.sample(step_miles=2.0)
        self.assertEqual(miles[0], 0.0)
        self.assertEqual(miles[-1], 1234.5)
        steps = np.diff(miles)
        np.testing.assert_allclose(steps[:-1], 2.0, atol=1e-9)
        self.assertAlmostEqual(steps[-1], 0.5)
        self.assertEqual((lats[0], lons[0]), L_ROUTE[0])
        self.assertEqual((lats[-1], lons[-1]), L_ROUTE[-1])


class LocateTests(SimpleTestCase):
    def setUp(self):
        self.route = Route(L_ROUTE)

    def test_finds_mile_of_point_beside_vertex(self):
        mile, distance = self.route.locate(np.array([33.0]), np.array([-99.999]), within_miles=10)
        self.assertAlmostEqual(mile[0], self.route.miles[3], delta=0.5)
        self.assertLess(distance[0], 0.6)

    def test_point_outside_route_bounds_is_infinitely_far(self):
        mile, distance = self.route.locate(np.array([45.0]), np.array([-80.0]), within_miles=10)
        self.assertEqual(distance[0], np.inf)

    def test_point_inside_bounds_but_off_corridor_is_infinitely_far(self):
        # Inside the L's bounding box, a couple of hundred miles from either leg.
        mile, distance = self.route.locate(np.array([33.0]), np.array([-95.0]), within_miles=10)
        self.assertEqual(distance[0], np.inf)

    def test_returns_float_arrays_matching_input(self):
        lats = np.array([33.0, 45.0, 40.01])
        lons = np.array([-99.999, -80.0, -95.0])
        mile, distance = self.route.locate(lats, lons, within_miles=10)
        self.assertEqual(mile.shape, (3,))
        self.assertEqual(distance.shape, (3,))
        self.assertEqual(mile.dtype, np.float64)
        self.assertEqual(distance.dtype, np.float64)


class SimplifyTests(SimpleTestCase):
    def test_reduces_straight_line_to_its_endpoints(self):
        points = [(35.0, -100.0), (35.0, -99.95), (35.0, -99.9), (35.0, -99.85)]
        self.assertEqual(simplify(points, 0.01), [(35.0, -100.0), (35.0, -99.85)])

    def test_keeps_point_deviating_beyond_tolerance(self):
        # The middle point is about 0.7 miles off the chord.
        points = [(35.0, -100.0), (35.01, -99.95), (35.0, -99.9)]
        self.assertEqual(simplify(points, 0.1), points)

    def test_returns_short_lists_unchanged(self):
        self.assertEqual(simplify([], 0.1), [])
        self.assertEqual(simplify([(35.0, -100.0)], 0.1), [(35.0, -100.0)])
        two = [(35.0, -100.0), (36.0, -100.0)]
        self.assertEqual(simplify(two, 0.1), two)

    def test_accepts_an_array_and_returns_the_original_values(self):
        points = np.array([(35.0, -100.0), (35.0, -99.95), (35.0, -99.9), (35.02, -99.85)])
        self.assertEqual(simplify(points, 0.01), [(35.0, -100.0), (35.0, -99.9), (35.02, -99.85)])
