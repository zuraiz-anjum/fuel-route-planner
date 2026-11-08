from django.db import models


class StationQuerySet(models.QuerySet):
    def located(self):
        """Stations whose city could be placed on the map."""
        return self.filter(lat__isnull=False, lon__isnull=False)


class Station(models.Model):
    """A truck stop from the price file, positioned at the centre of its city."""

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=2)
    rack_id = models.PositiveIntegerField()
    # Mean of every price row the file has for this station, to a tenth of a cent.
    price = models.DecimalField(max_digits=6, decimal_places=3)
    price_count = models.PositiveSmallIntegerField()
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    # The planner keeps the table in memory and reloads it when this moves.
    updated_at = models.DateTimeField(auto_now=True)

    objects = StationQuerySet.as_manager()

    class Meta:
        ordering = ["state", "city", "name"]

    def __str__(self):
        return f"{self.name}, {self.city} {self.state}"

    def as_dict(self):
        return {
            "id": self.opis_id,
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "lat": self.lat,
            "lon": self.lon,
            "price_per_gallon": float(self.price),
        }
