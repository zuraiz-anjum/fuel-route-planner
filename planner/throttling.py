import logging

from rest_framework.throttling import AnonRateThrottle

log = logging.getLogger(__name__)


class AnonThrottle(AnonRateThrottle):
    """The stock throttle, except that a broken cache lets requests through."""

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            log.warning("cache unavailable; not throttling", exc_info=True)
            return True
