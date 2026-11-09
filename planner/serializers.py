from django.conf import settings
from rest_framework import serializers


class TripRequest(serializers.Serializer):
    """The query string of GET /api/route/."""

    start = serializers.CharField(max_length=200)
    finish = serializers.CharField(max_length=200)
    start_fuel = serializers.FloatField(min_value=0, default=0.0)
    stop_cost = serializers.FloatField(min_value=0, max_value=1000, required=False)

    def to_internal_value(self, data):
        # A blank box on the map page arrives as "start_fuel=", which means
        # the same as leaving it out. The last value wins if one is repeated.
        return super().to_internal_value({key: value for key, value in data.items() if value != ""})

    def validate_start_fuel(self, value):
        tank = settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG
        if value > tank:
            raise serializers.ValidationError(f"the tank holds {tank:g} gallons")
        return value


class PlacesQuery(serializers.Serializer):
    """The query string of GET /api/places/."""

    q = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)
