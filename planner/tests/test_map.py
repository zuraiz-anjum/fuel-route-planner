from django.test import SimpleTestCase
from django.urls import reverse


class MapPageTests(SimpleTestCase):
    def test_home_redirects_to_the_map(self):
        self.assertRedirects(self.client.get("/"), reverse("map"), fetch_redirect_response=False)

    def test_map_page_calls_the_api(self):
        response = self.client.get(reverse("map"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("route-api"))

    def test_the_place_boxes_offer_cities_from_the_api(self):
        response = self.client.get(reverse("map"))
        self.assertContains(response, reverse("places-api"))
        self.assertContains(response, 'role="listbox"', count=2)
        self.assertContains(response, 'role="combobox"', count=2)
