import random

from django.test import SimpleTestCase

from planner.optimizer import Candidate, NoFeasiblePlan, plan_fuel_stops

RANGE = 500.0
MPG = 10.0
TANK = RANGE / MPG


def greedy_cost(stations, total_miles):
    """Cost of the textbook greedy with continuous amounts, starting empty at mile 0."""
    # The destination is priced at zero so that it counts as a cheaper stop ahead.
    stops = sorted((s.mile, s.price) for s in stations) + [(total_miles, 0.0)]
    i, fuel, cost = 0, 0.0, 0.0
    while stops[i][0] < total_miles:
        mile, price = stops[i]
        reachable = [j for j in range(i + 1, len(stops)) if stops[j][0] - mile <= RANGE]
        cheaper = [j for j in reachable if stops[j][1] < price]
        if cheaper:
            target = cheaper[0]
            need = stops[target][0] - mile
            if need > fuel:
                cost += (need - fuel) * price / MPG
                fuel = need
        else:
            target = min(reachable, key=lambda j: stops[j][1])
            cost += (RANGE - fuel) * price / MPG
            fuel = RANGE
        fuel -= stops[target][0] - mile
        i = target
    return cost


def random_instances(count=200):
    """Seeded random routes with a departure and no gap the vehicle cannot cross."""
    rng = random.Random(0)
    instances = []
    while len(instances) < count:
        total_miles = rng.uniform(200, 2500)
        candidates = [
            Candidate(mile=rng.uniform(0, total_miles), price=rng.uniform(2.5, 5.0), ref=n)
            for n in range(rng.randint(5, 60))
        ]
        miles = sorted([0.0, total_miles] + [c.mile for c in candidates])
        # A few miles of margin, since positions are rounded to whole miles.
        if max(b - a for a, b in zip(miles, miles[1:])) > RANGE - 5:
            continue
        departure = Candidate(mile=0.0, price=rng.uniform(2.5, 5.0), ref="departure")
        instances.append((candidates, total_miles, departure))
    return instances


class PlanFuelStopsTests(SimpleTestCase):
    def test_buys_just_enough_dear_fuel_to_reach_cheaper_station(self):
        departure = Candidate(0.0, 3.50, ref="origin")
        cheap = Candidate(100.0, 3.00, ref="cheap")
        plan = plan_fuel_stops([cheap], 300.0, departure=departure)
        self.assertEqual([p.candidate.ref for p in plan.purchases], ["origin", "cheap"])
        self.assertAlmostEqual(plan.purchases[0].gallons, 10.0)
        self.assertAlmostEqual(plan.purchases[1].gallons, 20.0)
        self.assertAlmostEqual(plan.purchases[1].tank_on_arrival, 0.0)
        self.assertAlmostEqual(plan.total_cost, 95.0)
        self.assertAlmostEqual(plan.tank_on_arrival, 0.0)

    def test_full_tank_covers_short_trip_without_buying(self):
        plan = plan_fuel_stops([], 400.0, start_fuel_gallons=TANK)
        self.assertEqual(plan.purchases, [])
        self.assertEqual(plan.total_cost, 0.0)
        self.assertAlmostEqual(plan.tank_on_arrival, 10.0)

    def test_fuel_in_tank_is_used_before_buying(self):
        departure = Candidate(0.0, 3.50)
        cheap = Candidate(100.0, 3.00)
        plan = plan_fuel_stops([cheap], 300.0, departure=departure, start_fuel_gallons=5.0)
        self.assertAlmostEqual(plan.purchases[0].gallons, 5.0)
        self.assertAlmostEqual(plan.purchases[0].tank_on_departure, 10.0)
        self.assertAlmostEqual(plan.purchases[1].gallons, 20.0)

    def test_empty_tank_without_departure_purchase_is_infeasible(self):
        with self.assertRaises(NoFeasiblePlan) as caught:
            plan_fuel_stops([Candidate(120.0, 3.00)], 300.0)
        self.assertEqual(caught.exception.from_mile, 0)
        self.assertEqual(caught.exception.to_mile, 120.0)

    def test_gap_longer_than_range_is_reported(self):
        stations = [Candidate(100.0, 3.00), Candidate(800.0, 3.00)]
        with self.assertRaises(NoFeasiblePlan) as caught:
            plan_fuel_stops(stations, 900.0, departure=Candidate(0.0, 3.00))
        self.assertEqual(caught.exception.from_mile, 100.0)
        self.assertEqual(caught.exception.to_mile, 800.0)
        self.assertIn("800", str(caught.exception))

    def test_stop_cost_suppresses_penny_saving_stop(self):
        departure = Candidate(0.0, 3.00, ref="origin")
        stations = [Candidate(50.0, 2.99, ref="cheaper")]

        plan = plan_fuel_stops(stations, 200.0, departure=departure, stop_cost=10.0)
        self.assertEqual([p.candidate.ref for p in plan.purchases], ["origin"])
        self.assertAlmostEqual(plan.total_cost, 60.0)

        plan = plan_fuel_stops(stations, 200.0, departure=departure, stop_cost=0.0)
        self.assertEqual([p.candidate.ref for p in plan.purchases], ["origin", "cheaper"])
        self.assertLess(plan.total_cost, 60.0)

    def test_stop_cost_is_left_out_of_reported_cost(self):
        departure = Candidate(0.0, 3.00)
        stations = [Candidate(400.0, 3.00, ref="midway")]
        plan = plan_fuel_stops(stations, 700.0, departure=departure, stop_cost=25.0)
        self.assertEqual(len(plan.purchases), 2)
        self.assertAlmostEqual(plan.total_gallons, 70.0)
        self.assertAlmostEqual(plan.total_cost, 210.0)

    def test_departure_purchase_carries_no_stop_cost(self):
        departure = Candidate(0.0, 4.00, ref="origin")
        stations = [Candidate(100.0, 2.00, ref="cheap")]
        plan = plan_fuel_stops(stations, 300.0, departure=departure, stop_cost=1000.0)
        self.assertEqual([p.candidate.ref for p in plan.purchases], ["origin"])
        self.assertAlmostEqual(plan.total_gallons, 30.0)

    def test_candidates_off_the_route_are_ignored(self):
        departure = Candidate(0.0, 3.50, ref="origin")
        stations = [Candidate(-5.0, 1.00, ref="behind"), Candidate(350.0, 1.00, ref="beyond")]
        plan = plan_fuel_stops(stations, 300.0, departure=departure)
        self.assertEqual([p.candidate.ref for p in plan.purchases], ["origin"])
        self.assertAlmostEqual(plan.total_cost, 105.0)

    def test_cheaper_of_two_stations_at_same_mile_is_used(self):
        departure = Candidate(0.0, 3.50)
        stations = [Candidate(100.0, 3.20, ref="dear"), Candidate(100.0, 2.80, ref="cheap")]
        plan = plan_fuel_stops(stations, 300.0, departure=departure)
        self.assertEqual(plan.purchases[-1].candidate.ref, "cheap")
        self.assertAlmostEqual(plan.purchases[-1].gallons, 20.0)
        self.assertNotIn("dear", [p.candidate.ref for p in plan.purchases])

    def test_candidate_order_does_not_matter(self):
        departure = Candidate(0.0, 3.80)
        stations = [Candidate(m, 3.0 + (m % 7) / 10, ref=m) for m in range(50, 1500, 90)]
        forward = plan_fuel_stops(stations, 1500.0, departure=departure, stop_cost=5.0)
        backward = plan_fuel_stops(stations[::-1], 1500.0, departure=departure, stop_cost=5.0)
        self.assertEqual(forward, backward)

    def test_matches_greedy_on_random_routes(self):
        for n, (candidates, total_miles, departure) in enumerate(random_instances()):
            plan = plan_fuel_stops(candidates, total_miles, departure=departure)
            expected = greedy_cost(candidates + [departure], total_miles)
            self.assertAlmostEqual(plan.total_cost, expected, delta=1.0, msg=f"instance {n}")

    def test_plan_invariants_hold_on_random_routes(self):
        for n, (candidates, total_miles, departure) in enumerate(random_instances()):
            plan = plan_fuel_stops(candidates, total_miles, departure=departure)
            miles = [p.candidate.mile for p in plan.purchases]
            self.assertEqual(miles, sorted(miles), msg=f"instance {n}")
            for purchase in plan.purchases:
                self.assertGreater(purchase.gallons, 0)
                self.assertGreaterEqual(purchase.tank_on_arrival, 0)
                self.assertLessEqual(purchase.tank_on_departure, TANK + 1e-9)
                self.assertAlmostEqual(purchase.cost, purchase.gallons * purchase.candidate.price)
            self.assertAlmostEqual(plan.total_gallons, sum(p.gallons for p in plan.purchases))
            self.assertAlmostEqual(plan.total_cost, sum(p.cost for p in plan.purchases))
            self.assertGreaterEqual(plan.total_gallons * MPG, total_miles - 1e-9, msg=f"instance {n}")
