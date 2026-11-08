from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from .forms import TripForm
from .nominatim import GeocodeError
from .optimizer import NoFeasiblePlan
from .osrm import NoRouteError, RoutingError
from .trip import StationsNotLoaded, UnknownPlace, plan_trip


def error(message, status):
    return JsonResponse({"error": message}, status=status)


@require_GET
def route_api(request):
    form = TripForm(request.GET)
    if not form.is_valid():
        details = {field: list(messages) for field, messages in form.errors.items()}
        return JsonResponse({"error": "invalid parameters", "details": details}, status=400)
    data = form.cleaned_data
    try:
        trip = plan_trip(
            data["start"],
            data["finish"],
            start_fuel_gallons=data["start_fuel"],
            stop_cost=data["stop_cost"],
        )
    except (UnknownPlace, NoRouteError, NoFeasiblePlan) as exc:
        return error(str(exc), 422)
    except (GeocodeError, RoutingError) as exc:
        return error(str(exc), 502)
    except StationsNotLoaded as exc:
        return error(str(exc), 503)
    trip["map_url"] = request.build_absolute_uri(reverse("map") + "?" + request.GET.urlencode())
    return JsonResponse(trip)


def map_page(request):
    return render(request, "planner/map.html")
