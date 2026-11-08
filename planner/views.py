from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from .forms import TripForm
from .models import Station
from .optimizer import NoFeasiblePlan
from .osrm import NoRouteError, RoutingError
from .photon import GeocodeError
from .trip import NoStationNearStart, SamePlace, StationsNotLoaded, UnknownPlace, plan_trip


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
    except (UnknownPlace, SamePlace, NoRouteError, NoFeasiblePlan, NoStationNearStart) as exc:
        return error(str(exc), 422)
    except (GeocodeError, RoutingError) as exc:
        return error(str(exc), 502)
    except StationsNotLoaded as exc:
        return error(str(exc), 503)
    trip["map_url"] = request.build_absolute_uri(reverse("map") + "?" + request.GET.urlencode())
    return JsonResponse(trip)


@require_GET
def map_page(request):
    return render(request, "planner/map.html")


@require_GET
def health(request):
    """Whether the service can plan a trip. Nothing external is touched."""
    located = Station.objects.located().count()
    body = {"status": "ok" if located else "no stations loaded", "stations": Station.objects.count(), "located": located}
    return JsonResponse(body, status=200 if located else 503)
