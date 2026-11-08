"""
Choosing where to buy fuel along a route, and how much.

The route arrives here reduced to a line: candidate stations at mile markers,
each with a price and how far it sits off the road. The search is a dynamic
programme over the range left in the tank, counted in tenths of a mile, so
the state at each stop is one array holding the cheapest way to be there with
any amount of fuel. Each stop is then a few vectorised operations, and the
answer is exact to a tenth of a mile.

Every purchase away from the origin is charged a fixed stop cost in the
objective so the plan does not pull off the highway to save fifty cents. The
stop cost only steers the choice; it is never part of the money reported.
"""

import math
from dataclasses import dataclass

import numpy as np

STEPS_PER_MILE = 10


@dataclass(frozen=True)
class Candidate:
    """Somewhere fuel can be bought, positioned along the route."""

    mile: float  # distance from the origin along the route
    price: float  # dollars per gallon
    ref: object = None  # opaque handle for the caller; never inspected here
    off_route_miles: float = 0.0  # one way; stopping means driving it there and back


@dataclass(frozen=True)
class Purchase:
    candidate: Candidate
    gallons: float
    tank_on_arrival: float  # gallons, on the route before turning off
    tank_on_departure: float  # gallons, back on the route afterwards
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
        super().__init__(f"no fuel stop within range between mile {from_mile:.1f} and mile {to_mile:.1f}")


def plan_fuel_stops(candidates, total_miles, *, departure=None, range_miles=500.0, mpg=10.0,
                    start_fuel_gallons=0.0, stop_cost=0.0):
    """Choose purchases that get the vehicle to total_miles for the least money."""
    full = round(range_miles * STEPS_PER_MILE)
    levels = np.arange(full + 1)
    cost = np.full(full + 1, np.inf)
    # The allowance keeps a full tank that arrives as 49.9999999 gallons from
    # losing a step; otherwise rounding down is the safe direction.
    cost[min(full, math.floor(start_fuel_gallons * mpg * STEPS_PER_MILE + 1e-6))] = 0.0

    visits = []
    if departure is not None:
        visits.append((0, 0.0, departure, 0, 0.0))
    finish = math.ceil(total_miles * STEPS_PER_MILE)
    # A station in the last tenth of a mile is at the point the trip ends, and
    # the only thing to buy there is a few drops; finishing in a station's own
    # town puts one there every time. A trip whose final leg is longer than
    # the range save for such a station would be refused, which no stretch of
    # the real station table comes near.
    last_useful_mile = total_miles - 1 / STEPS_PER_MILE
    for candidate in sorted((c for c in candidates if 0 <= c.mile <= last_useful_mile), key=lambda c: c.mile):
        position = round(candidate.mile * STEPS_PER_MILE)
        detour = round(candidate.off_route_miles * STEPS_PER_MILE)
        visits.append((position, candidate.mile, candidate, detour, stop_cost))
    visits.append((finish, total_miles, None, 0, 0.0))

    steps = []
    here, here_mile = 0, 0.0
    for position, mile, candidate, detour, penalty in visits:
        gap = position - here
        if gap > full:
            raise NoFeasiblePlan(here_mile, mile)
        cost = drive(cost, gap)
        if cost.min() == np.inf:
            raise NoFeasiblePlan(here_mile, mile)
        here, here_mile = position, mile
        if candidate is None:
            break
        # Stopping means driving out to the station and back, so the purchase
        # is weighed on what is left after the way out, and the way back comes
        # off again. Passing by costs nothing.
        bought, source = buy(drive(cost, detour), levels, candidate.price / mpg / STEPS_PER_MILE, penalty)
        back = drive(bought, detour)
        from_level = np.full(full + 1, -1)
        from_level[: full + 1 - detour] = source[detour:]
        # Only a stop that actually buys fuel counts; turning off and coming
        # back empty-handed is never a plan.
        better = (back < cost - 1e-6) & (from_level < levels + detour)
        steps.append((position, candidate, detour, np.where(better, from_level, -1)))
        cost = np.where(better, back, cost)

    arrival = int(np.argmin(cost))
    purchases = _backtrack(steps, here, arrival, mpg)
    return Plan(
        purchases=purchases,
        total_gallons=math.fsum(p.gallons for p in purchases),
        total_cost=math.fsum(p.cost for p in purchases),
        tank_on_arrival=arrival / STEPS_PER_MILE / mpg,
    )


def drive(cost, steps):
    """Range left after driving steps forward; levels that run dry become impossible."""
    if steps == 0:
        return cost
    driven = np.full_like(cost, np.inf)
    driven[:-steps] = cost[steps:]
    return driven


def buy(cost, levels, price_per_step, penalty):
    """Cheapest way to leave at each level after buying here, and the level each came from."""
    # Leaving with L after arriving with a <= L costs cost[a] + (L - a) * price,
    # so the best a for every L is a running minimum of cost[a] - a * price.
    base = cost - levels * price_per_step
    running = np.minimum.accumulate(base)
    source = np.maximum.accumulate(np.where(base <= running, levels, 0))
    return running + levels * price_per_step + penalty, source


def _backtrack(steps, destination, arrival, mpg):
    """Walk back from the destination and return the purchases in route order."""
    gallons_per_step = 1 / STEPS_PER_MILE / mpg
    purchases = []
    level, ahead = arrival, destination
    for position, candidate, detour, from_level in reversed(steps):
        leaving = level + ahead - position
        pumped_from = int(from_level[leaving])
        if pumped_from >= 0:
            level = pumped_from + detour
            purchases.append(
                Purchase(
                    candidate=candidate,
                    gallons=(leaving + detour - pumped_from) * gallons_per_step,
                    tank_on_arrival=level * gallons_per_step,
                    tank_on_departure=leaving * gallons_per_step,
                    cost=(leaving + detour - pumped_from) * gallons_per_step * candidate.price,
                )
            )
        else:
            level = leaving
        ahead = position
    purchases.reverse()
    return purchases
