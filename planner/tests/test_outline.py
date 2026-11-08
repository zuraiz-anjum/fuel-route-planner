from django.test import SimpleTestCase

from planner.trip import in_united_states

INSIDE = {
    "Key West": (24.5551, -81.7800),
    "Miami Beach": (25.7907, -80.1300),
    "Montauk": (41.0359, -71.9545),
    "Eastport, ME": (44.9062, -66.9900),
    "Cape Hatteras": (35.2510, -75.5280),
    "Galveston": (29.3013, -94.7977),
    "Brownsville": (25.9017, -97.4975),
    "San Diego": (32.7157, -117.1611),
    "Seattle": (47.6062, -122.3321),
    "International Falls": (48.6023, -93.4108),
    "Detroit": (42.3314, -83.0458),
    "Honolulu": (21.3069, -157.8583),
    "Hilo": (19.7074, -155.0885),
    "Anchorage": (61.2181, -149.9003),
    "Nome": (64.5011, -165.4064),
    "Juneau": (58.3005, -134.4197),
}

OUTSIDE = {
    "Toronto": (43.6532, -79.3832),
    "Montreal": (45.5017, -73.5673),
    "Vancouver": (49.2827, -123.1207),
    "Winnipeg": (49.8951, -97.1384),
    "Monterrey": (25.6866, -100.3161),
    "Havana": (23.1136, -82.3666),
    "Nassau": (25.0443, -77.3504),
    "London": (51.5074, -0.1278),
}


class OutlineTests(SimpleTestCase):
    """Against the committed outline, which is part of what is being tested."""

    def test_coastal_border_and_island_places_are_inside(self):
        for name, (lat, lon) in INSIDE.items():
            self.assertTrue(in_united_states(lat, lon), name)

    def test_neighbouring_cities_are_outside(self):
        for name, (lat, lon) in OUTSIDE.items():
            self.assertFalse(in_united_states(lat, lon), name)
