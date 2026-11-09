from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("planner.urls")),
]

handler400 = "planner.views.bad_request"
handler404 = "planner.views.not_found"
handler500 = "planner.views.server_error"
