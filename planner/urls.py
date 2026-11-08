from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="map"), name="home"),
    path("map/", views.map_page, name="map"),
    path("api/route/", views.route_api, name="route-api"),
]
