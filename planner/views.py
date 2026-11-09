from urllib.parse import urlencode

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views import defaults
from django.views.decorators.http import require_safe
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler

from .cities import suggest
from .models import Station
from .optimizer import NoFeasiblePlan
from .osrm import NoRouteError, RoutingError
from .photon import GeocodeError
from .serializers import PlacesQuery, TripRequest
from .throttling import PlacesThrottle
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


class PlacesView(APIView):
    """GET /api/places/?q=dal: US cities to offer for a partly typed place.

    Answered from the city table, so nothing external is touched and the
    list is American by construction.
    """

    throttle_classes = [PlacesThrottle]

    def get(self, request):
        params = PlacesQuery(data=request.query_params)
        if not params.is_valid():
            return Response({"error": "invalid parameters", "details": params.errors}, status=400)
        places = suggest(params.validated_data["q"])
        return Response({"places": [{"label": place.label, "lat": place.lat, "lon": place.lon} for place in places]})


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


@require_safe
def map_page(request):
    return render(request, "planner/map.html")


def api_or_page(message, status, page):
    """Django's own error pages are HTML; under /api/ the answer keeps the API's shape."""

    def handler(request, exception=None):
        if request.path_info.startswith("/api/"):
            return JsonResponse({"error": message}, status=status)
        # Django hands the 400 and 404 handlers the exception; the 500 handler gets none.
        return page(request) if exception is None else page(request, exception)

    return handler


bad_request = api_or_page("bad request", 400, defaults.bad_request)
not_found = api_or_page("not found", 404, defaults.page_not_found)
server_error = api_or_page("internal server error", 500, defaults.server_error)
