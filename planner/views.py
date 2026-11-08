from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler

from .models import Station
from .optimizer import NoFeasiblePlan
from .osrm import NoRouteError, RoutingError
from .photon import GeocodeError
from .serializers import TripRequest
from .trip import NoStationNearStart, SamePlace, StationsNotLoaded, UnknownPlace, plan_trip


def error(message, status):
    return Response({"error": message}, status=status)


def api_exception(exc, context):
    """Give the framework's own errors (405, 429, ...) the same shape as ours."""
    response = exception_handler(exc, context)
    if response is not None and "detail" in response.data:
        response.data = {"error": str(response.data["detail"])}
    return response


class RouteView(APIView):
    """GET /api/route/: the route between two places and where to buy fuel on it."""

    def get(self, request):
        params = TripRequest(data=request.query_params)
        if not params.is_valid():
            return Response({"error": "invalid parameters", "details": params.errors}, status=400)
        data = params.validated_data
        try:
            trip = plan_trip(
                data["start"],
                data["finish"],
                start_fuel_gallons=data["start_fuel"],
                stop_cost=data.get("stop_cost"),
            )
        except (UnknownPlace, SamePlace, NoRouteError, NoFeasiblePlan, NoStationNearStart) as exc:
            return error(str(exc), 422)
        except (GeocodeError, RoutingError) as exc:
            return error(str(exc), 502)
        except StationsNotLoaded as exc:
            return error(str(exc), 503)
        # The same request on the map page, carrying only what the API understood.
        shown = {name: request.query_params[name] for name in params.fields if request.query_params.get(name)}
        trip["map_url"] = request.build_absolute_uri(reverse("map")) + "?" + urlencode(shown)
        return Response(trip)


class HealthView(APIView):
    """Whether the service can plan a trip. Nothing external is touched."""

    throttle_classes = []

    def get(self, request):
        located = Station.objects.located().count()
        body = {
            "status": "ok" if located else "no stations loaded",
            "stations": Station.objects.count(),
            "located": located,
        }
        return Response(body, status=200 if located else 503)


@require_GET
def map_page(request):
    return render(request, "planner/map.html")
