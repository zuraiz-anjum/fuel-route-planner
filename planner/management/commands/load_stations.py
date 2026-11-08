"""
Load the fuel price file into the database.

Each station is attached to the coordinates of its city from the geocoding
cache. Stations whose city is not in the cache are still loaded, without
coordinates, so they show up in the admin; the planner ignores them.
Running the command again updates rows in place and removes stations that
have dropped out of the file.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from planner.models import Station
from planner.stations import city_key, read_city_coordinates, read_stations

TENTH_OF_A_CENT = Decimal("0.001")


class Command(BaseCommand):
    help = "Load stations from data/fuel-prices.csv with coordinates from data/city_coordinates.csv."

    def handle(self, *args, **options):
        coordinates = read_city_coordinates()
        stations = []
        unlocated = 0
        for record in read_stations():
            point = coordinates.get(city_key(record.city, record.state))
            if point is None:
                unlocated += 1
            stations.append(
                Station(
                    opis_id=record.opis_id,
                    name=record.name,
                    address=record.address,
                    city=record.city,
                    state=record.state,
                    rack_id=record.rack_id,
                    price=record.price.quantize(TENTH_OF_A_CENT),
                    price_count=record.price_count,
                    lat=point[0] if point else None,
                    lon=point[1] if point else None,
                )
            )
        Station.objects.bulk_create(
            stations,
            update_conflicts=True,
            unique_fields=["opis_id"],
            update_fields=["name", "address", "city", "state", "rack_id", "price", "price_count", "lat", "lon", "updated_at"],
        )
        gone, _ = Station.objects.exclude(opis_id__in=[s.opis_id for s in stations]).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(stations)} stations loaded, {unlocated} without coordinates, {gone} removed"
            )
        )
