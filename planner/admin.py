from django.contrib import admin

from .models import Station


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ["opis_id", "name", "city", "state", "price", "price_count", "lat", "lon"]
    list_filter = ["state"]
    search_fields = ["name", "city", "address"]
    ordering = ["opis_id"]
