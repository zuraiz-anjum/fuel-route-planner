from rest_framework.throttling import AnonRateThrottle

from .caching import complain


class AnonThrottle(AnonRateThrottle):
    """The stock throttle, except that a broken cache lets requests through."""

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            complain()
            return True


class PlacesThrottle(AnonThrottle):
    """Typing in the map page's boxes asks several times a second, so its own, higher rate."""

    scope = "places"
