"""
The cache is a convenience. If its directory is unwritable or its server is
down, a request still goes through, slower, and the trouble is logged rather
than turned into a 500.
"""

import logging

from django.core.cache import cache

log = logging.getLogger(__name__)

# Part of every cache key. Cached files outlive a restart, so a change to
# what is stored, a pickled route, a plan dict or a geocoded place, would
# otherwise be served for as long as the entry lives; raise this with it.
CACHE_VERSION = 1

_complained = False


def complain():
    """One warning with the traceback per process; a broken cache should not flood the log."""
    global _complained
    if not _complained:
        _complained = True
        log.warning("cache unavailable; reads miss, writes are dropped and the throttle is off", exc_info=True)


def cache_get(key, default=None):
    try:
        return cache.get(key, default)
    except Exception:
        complain()
        return default


def cache_set(key, value, timeout):
    try:
        cache.set(key, value, timeout)
    except Exception:
        complain()
