"""
Choosing where to buy fuel along a route, and how much.

The route arrives here already reduced to a line: candidate stations at mile
markers, each with a price per gallon. The search is a dynamic programme over
the range left in the tank, counted in whole miles rather than gallons, so the
state at each stop is a small array holding the cheapest way to be there with
0..500 miles in the tank. Whole miles make the answer exact up to a one-mile
grid, which is much finer than the city centroids the stations are placed by,
and they let each stop be handled with a few numpy operations over the whole
array instead of a loop over fuel levels.

Every purchase away from the origin is charged a fixed stop cost in the
objective. Without it the cheapest plan will pull off the highway to save
fifty cents. The stop cost only steers the choice of stops; it is never part
of the money reported as spent.
"""

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Candidate:
    """Somewhere fuel can be bought, positioned along the route."""

    mile: float  # distance from the origin along the route
    price: float  # dollars per gallon
    ref: object = None  # opaque handle for the caller; never inspected here


@dataclass(frozen=True)
class Purchase:
    candidate: Candidate
    gallons: float
    tank_on_arrival: float  # gallons
    tank_on_departure: float  # gallons
    cost: float  # gallons * price, without the stop cost


@dataclass(frozen=True)
class Plan:
    purchases: list  # in route order, only where something was bought
    total_gallons: float
    total_cost: float  # without the stop cost
    tank_on_arrival: float  # gallons left at the destination


class NoFeasiblePlan(Exception):
    """A stretch of the route is longer than the fuel available to cross it."""

    def __init__(self, from_mile, to_mile):
        self.from_mile = from_mile
        self.to_mile = to_mile
        super().__init__(
            f"no fuel stop within range between mile {from_mile:.0f} and mile {to_mile:.0f}"
        )


def plan_fuel_stops(candidates, total_miles, *, departure=None, range_miles=500.0, mpg=10.0,
                    start_fuel_gallons=0.0, stop_cost=0.0):
    """Choose purchases that get the vehicle to total_miles for the least money."""
    full = round(range_miles)
    levels = np.arange(full + 1)
    cost = np.full(full + 1, np.inf)
    # The allowance keeps a full 50 gallons that arrives as 49.9999999 from
    # losing a mile; otherwise rounding down is the safe direction.
    cost[min(full, math.floor(start_fuel_gallons * mpg + 1e-9))] = 0.0

    visits = []
    if departure is not None:
        visits.append((0, 0.0, departure, 0.0))
    for candidate in sorted((c for c in candidates if 0 <= c.mile <= total_miles), key=lambda c: c.mile):
        visits.append((round(candidate.mile), candidate.mile, candidate, stop_cost))
    visits.append((math.ceil(total_miles), total_miles, None, 0.0))

    steps = []
    here, here_mile = 0, 0.0
    for position, mile, candidate, penalty in visits:
        gap = position - here
        if gap > full:
            raise NoFeasiblePlan(here_mile, mile)
        if gap:
            cost[:-gap] = cost[gap:]
            cost[-gap:] = np.inf
            if cost.min() == np.inf:
                raise NoFeasiblePlan(here_mile, mile)
        here, here_mile = position, mile
        if candidate is None:
            break

        # Leaving with L after arriving with a <= L costs cost[a] + (L - a) * price per
        # mile, so the best a for every L is a running minimum of cost[a] - a * price.
        price_of_levels = levels * (candidate.price / mpg)
        base = cost - price_of_levels
        running = np.minimum.accumulate(base)
        source = np.maximum.accumulate(np.where(base <= running, levels, 0))
        bought = running + price_of_levels + penalty
        # The margin makes float noise count as a tie, and a tie means not stopping.
        better = bought < cost - 1e-9
        cost = np.where(better, bought, cost)
        steps.append((position, candidate, np.where(better, source, -1)))

    arrival = int(np.argmin(cost))
    purchases = _backtrack(steps, here, arrival, mpg)
    return Plan(
        purchases=purchases,
        total_gallons=math.fsum(p.gallons for p in purchases),
        total_cost=math.fsum(p.cost for p in purchases),
        tank_on_arrival=arrival / mpg,
    )


def _backtrack(steps, destination, arrival, mpg):
    """Walk back from the destination and return the purchases in route order."""
    purchases = []
    level, ahead = arrival, destination
    for position, candidate, bought_from in reversed(steps):
        leaving = level + ahead - position
        level = int(bought_from[leaving]) if bought_from[leaving] >= 0 else leaving
        if leaving > level:
            gallons = (leaving - level) / mpg
            purchases.append(Purchase(
                candidate=candidate,
                gallons=gallons,
                tank_on_arrival=level / mpg,
                tank_on_departure=leaving / mpg,
                cost=gallons * candidate.price,
            ))
        ahead = position
    purchases.reverse()
    return purchases
