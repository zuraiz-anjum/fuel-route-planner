from django import forms
from django.conf import settings


class TripForm(forms.Form):
    start = forms.CharField(max_length=200)
    finish = forms.CharField(max_length=200)
    start_fuel = forms.FloatField(required=False, min_value=0)
    stop_cost = forms.FloatField(required=False, min_value=0, max_value=1000)

    def clean_start_fuel(self):
        value = self.cleaned_data["start_fuel"]
        if value is None:
            return 0.0
        tank = settings.VEHICLE_RANGE_MILES / settings.VEHICLE_MPG
        if value > tank:
            raise forms.ValidationError(f"the tank holds {tank:g} gallons")
        return value

    def clean_stop_cost(self):
        value = self.cleaned_data["stop_cost"]
        return settings.DEFAULT_STOP_COST if value is None else value
