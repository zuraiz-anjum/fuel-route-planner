"""
The cache is a convenience. If its directory is unwritable or its server is
down, a request still goes through, slower, and the trouble is logged rather
than turned into a 500.
"""

import logging

from django.core.cache import cache

log = logging.getLogger(__name__)


def cache_get(key, default=None):
    try:
        return cache.get(key, default)
    except Exception:
        log.warning("cache unavailable; %s computed afresh", key, exc_info=True)
        return default


def cache_set(key, value, timeout):
    try:
        cache.set(key, value, timeout)
    except Exception:
        log.warning("cache unavailable; %s not stored", key, exc_info=True)
