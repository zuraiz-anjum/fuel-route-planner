import importlib.util
import os
from pathlib import Path
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

SETTINGS = Path(__file__).resolve().parents[2] / "fuelroute" / "settings.py"
OURS = (
    "DJANGO_DEBUG",
    "DJANGO_SECRET_KEY",
    "DJANGO_ALLOWED_HOSTS",
    "DJANGO_SECURE_SSL_REDIRECT",
    "DJANGO_SECURE_HSTS_SECONDS",
)
A_KEY = "k" * 50


def settings_under(**variables):
    """The settings module evaluated afresh with exactly these of our variables set."""
    environment = {name: value for name, value in os.environ.items() if name not in OURS}
    environment.update(variables)
    with mock.patch.dict(os.environ, environment, clear=True):
        spec = importlib.util.spec_from_file_location("fuelroute.settings_under_test", SETTINGS)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class ProductionDefaultTests(SimpleTestCase):
    def test_nothing_set_is_production_and_wants_a_key(self):
        with self.assertRaises(ImproperlyConfigured):
            settings_under()

    def test_the_development_switch_brings_a_key_of_its_own(self):
        settings = settings_under(DJANGO_DEBUG="1")
        self.assertTrue(settings.DEBUG)
        self.assertEqual(settings.SECRET_KEY, "dev-only-key")

    def test_the_loopback_names_are_always_allowed(self):
        settings = settings_under(DJANGO_DEBUG="0", DJANGO_SECRET_KEY=A_KEY, DJANGO_ALLOWED_HOSTS="example.com")
        self.assertEqual(settings.ALLOWED_HOSTS, ["example.com", "localhost", "127.0.0.1", "[::1]"])
        self.assertFalse(settings.DEBUG)
        again = settings_under(DJANGO_DEBUG="0", DJANGO_SECRET_KEY=A_KEY, DJANGO_ALLOWED_HOSTS="localhost,example.com")
        self.assertEqual(again.ALLOWED_HOSTS, ["localhost", "example.com", "127.0.0.1", "[::1]"])

    def test_the_transport_security_flags_follow_the_seconds(self):
        strict = settings_under(
            DJANGO_DEBUG="0", DJANGO_SECRET_KEY=A_KEY, DJANGO_SECURE_HSTS_SECONDS="31536000", DJANGO_SECURE_SSL_REDIRECT="1"
        )
        self.assertEqual(strict.SECURE_HSTS_SECONDS, 31536000)
        self.assertTrue(strict.SECURE_HSTS_INCLUDE_SUBDOMAINS)
        self.assertTrue(strict.SECURE_HSTS_PRELOAD)
        self.assertTrue(strict.SECURE_SSL_REDIRECT)
        behind_a_proxy = settings_under(DJANGO_DEBUG="0", DJANGO_SECRET_KEY=A_KEY)
        self.assertEqual(behind_a_proxy.SECURE_HSTS_SECONDS, 0)
        self.assertFalse(behind_a_proxy.SECURE_HSTS_PRELOAD)
        self.assertFalse(behind_a_proxy.SECURE_SSL_REDIRECT)
