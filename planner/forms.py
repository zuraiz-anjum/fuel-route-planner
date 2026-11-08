from django import forms
from django.conf import settings

TANK_GALLONS = settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG


class TripForm(forms.Form):
    start = forms.CharField(max_length=200)
    finish = forms.CharField(max_length=200)
    start_fuel = forms.FloatField(required=False, min_value=0, max_value=TANK_GALLONS)
    stop_cost = forms.FloatField(required=False, min_value=0)

    def clean_start_fuel(self):
        value = self.cleaned_data["start_fuel"]
        return 0.0 if value is None else value

    def clean_stop_cost(self):
        value = self.cleaned_data["stop_cost"]
        return settings.DEFAULT_STOP_COST if value is None else value
