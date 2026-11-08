# Fuel route planner

An HTTP API that plans a drive between two places in the United States and
works out where to buy fuel on the way. Given a start and a finish it returns
the route, the stops that get the truck there for the least money, and what
the fuel for the trip costs. The vehicle does 10 miles to the gallon and has
a 500-mile range, so long routes need several stops.

Built on Django 6.1. Routing comes from the public OSRM server. A city and
state is looked up in a table bundled with the project; anything else is
geocoded with Photon. Neither service needs an API key, so a fresh clone runs
as is. Fuel prices come from the OPIS price file in `data/`.

## Running it

    python -m venv .venv
    .venv\Scripts\activate          # or: source .venv/bin/activate
    pip install -r requirements.txt
    python manage.py migrate
    python manage.py load_stations
    python manage.py runserver

(`python3` where `python` is still Python 2.)

Then ask for a trip:

    curl "http://127.0.0.1:8000/api/route/?start=Dallas,%20TX&finish=Los%20Angeles,%20CA"

or open <http://127.0.0.1:8000/map/> to see the same answer drawn on a map.
The API URL itself, opened in a browser, shows the browsable API. A few more
to try:

    /api/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA
    /api/route/?start=Chicago,%20IL&finish=Atlanta,%20GA&start_fuel=50
    /api/route/?start=32.7767,-96.7970&finish=29.7604,-95.3698&stop_cost=0

The same requests are in `postman/fuel-route-planner.postman_collection.json`
for Postman, with the server address as a `base_url` variable.

`load_stations` reads `data/fuel-prices.csv` together with
`data/city_coordinates.csv`, the position of every city in the price file,
and takes a few seconds. The coordinates file is committed; "Station
coordinates" below says how it was made and when to remake it.
`python manage.py createsuperuser` and `/admin/` let you browse the stations.
`/healthz/` answers 200 with the station counts once they are loaded and 503
before, without calling anything external.

### In a container

    docker build -t fuel-route-planner .
    docker run --rm -p 8000:8000 fuel-route-planner

The image runs gunicorn with `DJANGO_DEBUG=0`, serves its own static files
through WhiteNoise, and on every start migrates and loads the stations
before listening on port 8000, so there is nothing to keep between runs. It
accepts any `Host` header on the assumption that a proxy in front of it
checks that; set `DJANGO_ALLOWED_HOSTS` to pin it. Without a
`DJANGO_SECRET_KEY` a fresh one is generated at each start, which only
matters for staying logged in to the admin across restarts. The container's
`HEALTHCHECK` polls `/healthz/`.

## The API

`GET /api/route/`

| Parameter    | Required | Meaning |
|--------------|----------|---------|
| `start`      | yes      | A city and state (`Dallas, TX`, `dallas texas`), any other place (`Flagstaff`, an address) or `lat,lon`. Must be in the US. |
| `finish`     | yes      | Same. |
| `start_fuel` | no       | Gallons in the tank at departure, 0 to 50. Default 0, which means the plan starts with a fill-up; see "What a plan means". |
| `stop_cost`  | no       | Dollars the planner charges itself per stop en route, 0 to 1000. Default 10. |

The response for `?start=32.7767,-96.7970&finish=34.0522,-118.2437`
(Dallas to Los Angeles), with two of the four stops and most of the geometry
left out:

```json
{
  "start":  {"query": "32.7767,-96.7970", "label": "32.77670, -96.79700", "lat": 32.7767, "lon": -96.797},
  "finish": {"query": "34.0522,-118.2437", "label": "34.05220, -118.24370", "lat": 34.0522, "lon": -118.2437},
  "route": {
    "distance_miles": 1437.8,
    "duration_minutes": 1486,
    "geometry": {"type": "LineString", "coordinates": [[-96.79675, 32.77594], [-96.79584, 32.77615], "..."]}
  },
  "vehicle": {"range_miles": 500, "mpg": 10, "tank_gallons": 50.0, "start_fuel_gallons": 0.0, "stop_cost": 10.0},
  "stops": [
    {
      "kind": "departure",
      "mile": 0.0,
      "leg_miles": 0.0,
      "station": {"id": 72773, "name": "RaceTrac #2626", "address": "I-20 Exit 472", "city": "Dallas", "state": "TX", "lat": 32.78306, "lon": -96.80667, "price_per_gallon": 2.864},
      "off_route_miles": 0.7,
      "tank_on_arrival_gallons": 0.0,
      "gallons": 23.19,
      "tank_on_departure_gallons": 23.19,
      "cost": 66.42
    },
    {
      "kind": "en_route",
      "mile": 231.0,
      "leg_miles": 231.0,
      "station": {"id": 66689, "name": "ROSCOE TRAVEL PLAZA", "address": "I-20, EXIT 235", "city": "Roscoe", "state": "TX", "lat": 32.44595, "lon": -100.53872, "price_per_gallon": 2.759},
      "off_route_miles": 0.9,
      "tank_on_arrival_gallons": 0.09,
      "gallons": 40.53,
      "tank_on_departure_gallons": 40.44,
      "cost": 111.82
    },
    "..."
  ],
  "summary": {
    "fuel_consumed_gallons": 143.78,
    "fuel_purchased_gallons": 144.22,
    "total_fuel_cost": 402.31,
    "average_price_per_gallon": 2.79,
    "tank_on_arrival_gallons": 0.0,
    "en_route_stops": 3
  },
  "map_url": "http://127.0.0.1:8000/map/?start=32.7767%2C-96.7970&finish=34.0522%2C-118.2437"
}
```

With place names, `start` and `finish` carry a label ("Dallas, Texas,
United States") instead of the bare coordinates.

`route.geometry` is GeoJSON, simplified to about fifty metres so that a
cross-country route is a few thousand points rather than tens of thousands.
`stops` are in driving order; `leg_miles` is the distance since the previous
stop, `off_route_miles` how far the station's city centre is from the route
(for the departure stop, from the start point), and the two tank figures are
measured on the route, before turning off and after coming back. The half
gallon by which fuel bought exceeds fuel burned above is the detours.

Errors come back as `{"error": "..."}`:

| Status | When |
|--------|------|
| 400 | Missing or invalid parameters; `details` says which. |
| 422 | A place could not be found in the US, start and finish are within two miles of each other, no road connects the two, no station is within 100 miles of the start to fill up at, or some stretch is longer than the range with no station in it. |
| 429 | More than 60 requests in a minute from one address. |
| 502 | OSRM or Photon failed or timed out. |
| 503 | No stations loaded yet; run `load_stations`. |

## What a plan means

The truck burns a gallon every ten miles, so a 1,400-mile trip needs 140
gallons from somewhere, and the plan accounts for all of them. By default the
tank is treated as empty at the start and the first entry in `stops` is a
fill-up before leaving, priced at the cheapest station within 10 miles of the
start (or the nearest one within 100 miles, if there is nothing closer).
Every stop after that is at a station from the price file that sits within
10 miles of the route. `total_fuel_cost` is what all that fuel costs at the
pump. Pass `start_fuel=50` to plan a trip that leaves on a full tank; then
only the fuel bought on the way is counted, and a trip shorter than the
range needs no stop at all.

A small departure purchase is not a mistake. "Buy 4 gallons before leaving"
means: do not fill up here, there is a cheaper station 40 miles down the
road, buy just enough to reach it.

Stops are chosen to minimise cost, not to minimise stops. Left to itself the
cheapest plan will pull off the highway to save fifty cents, so when the
planner weighs options it charges each stop en route `stop_cost` (default
$10, roughly what a stop costs in time). The penalty only shapes the choice;
it is never added to the reported cost. Set `stop_cost=0` for the pure
price-optimal plan. On the price file as it stands, New York to Los Angeles
makes 6 stops at the default and 8 stops at zero, to save $3.72; Chicago to
Atlanta makes 1 stop instead of 3, giving up $3.56.

Turning off to a station costs fuel too. Each station's distance from the
route is driven there and back in the model, so a station eight miles off
the highway has to be cheaper by more than the gallon and a half it costs to
reach before the plan prefers it, and the truck always has the fuel to get
to the pump.

The optimiser is exact for this model to a tenth of a mile: it is a dynamic
programme over how much range is left in the tank at each station
(`planner/optimizer.py` explains it). The tests check it against the textbook
greedy algorithm on random routes.

## How a request is served

1. The two places are resolved. Coordinates are taken as they are; a city
   and state (`Dallas, TX`, `Dallas, Texas`, `Dallas TX`) is looked up in
   `data/us_cities.csv` without leaving the process; anything else is
   geocoded with Photon, and those answers are cached for a month, since
   places do not move.
2. One request to OSRM returns the route geometry and distance. The route is
   prepared once (mileage along it, a simplified copy for the response) and
   cached for a day.
3. Every located station is matched against a one-mile sampling of the route
   with numpy; those within 10 miles become candidates, each at its mile
   marker and with its distance from the road.
4. The optimiser picks the stops.

A cold request makes one external call for cities and coordinates, at most
three otherwise, and takes under two seconds, nearly all of it waiting for
OSRM. Repeating it takes
30 ms for a short trip and under 300 ms coast to coast, which is the
corridor match over 6,600 stations plus the optimiser. The cache is a
directory of files under `.cache/`, so it survives the development server
restarting.

## Station coordinates

The price file has an address, city and state for each station but no
coordinates, and the addresses are highway junctions ("I-44, EXIT 283 &
US-69") that geocoders do not resolve. Stations are therefore placed at the
centre of their city, which is normally within a few miles of the truck stop
and good enough to tell whether it is on the route; the 10-mile corridor and
the detour accounting allow for the error.

`python manage.py geocode_cities` produced `data/city_coordinates.csv` from
the 3,808 distinct city/state pairs in the file. It looks each one up offline
in the GeoNames US gazetteer, an 80 MB download fetched on first use and not
committed, and would ask Photon only about names GeoNames does not know. It
knew all 3,808 once the file's spellings were folded in: "S Coffeyville" for
South Coffeyville, "Pueblo Of Acoma" for Acoma Pueblo, and New England towns
that GeoNames files as administrative areas ("Town of Corinth"). A place's
own name is matched before names other places are merely also known by,
which matters more than it sounds: El Paso lists "Franklin" among its old
names, and the first version of the matcher put the Franklin, TX truck stop
592 miles from home, with forty other towns astray by twenty miles or more.
A test now checks every station against the city table below. The command
is resumable and skips what is already cached, so it only needs to run again
if the price file gains new cities; anything neither source knows is loaded
without coordinates and ignored by the planner.

The 7,531 US rows in the price file collapse to 6,626 stations; where a
station appears more than once its prices are averaged. Canadian stations are
dropped.

## Place names

`data/us_cities.csv` is what lets `Dallas, TX` resolve without a geocoder:
every populated place in the GeoNames US dump with at least a thousand
people, plus every town in the price file whatever its size, 16,781 rows in
all. `python manage.py extract_cities` rebuilds it from the same dump. Names
are folded the way the gazetteer folds them, so `St. Louis` and `Saint
Louis`, `Ft Worth` and `Fort Worth` all land, and where a state has several
places of one name the most populous answers, which is what someone typing
it means. A bare name (`Flagstaff`), an address, or a hamlet below the
threshold that sells no fuel goes to Photon as before. Both tables are
derived from GeoNames data, published under CC BY 4.0.

## Design notes

OSRM and Photon were chosen because they need no account, so the project
runs from a clean clone. Both are public servers with fair-use policies;
point `OSRM_BASE_URL` and `PHOTON_BASE_URL` at your own instances for
anything beyond a demo. Nominatim was the first choice for geocoding and was
dropped when its public server throttled the one-off bulk job and its
one-request-per-second limit turned out to be the slowest part of a cold
request. The bulk job then moved offline altogether, which is where it should
have started. Typed-in places went straight to Photon at first too; the city
table came when the public server's bad afternoons turned out to be the only
thing that could make an ordinary request fail.

There is no GeoDjango or PostGIS. The spatial work is one corridor match per
request over a few thousand points, which numpy does in milliseconds in
memory; a spatial database would add a GDAL install for no gain here.
The HTTP layer is Django REST Framework: a serializer validates the query
string, the browsable API shows a request and its answer in a browser, and
anonymous clients are held to 60 requests a minute so that one script cannot
spend the public OSRM server's goodwill. The response stays a plain dict,
built in one place, with nothing in it a serializer would check.

The in-memory station table is rebuilt whenever a row in the database
changes, so `load_stations` can be rerun under a running server. The cache
is per machine; use Redis or memcached when running several workers. The
check that typed-in coordinates lie in the US is a bounding box around the
lower 48, Alaska and Hawaii, a sanity check rather than a border.

## Configuration

Everything has a development default. Override with environment variables:

| Variable | Default |
|----------|---------|
| `DJANGO_DEBUG` | `1`; set `0` in production, which then requires a secret key |
| `DJANGO_SECRET_KEY` | a fixed development key while `DJANGO_DEBUG=1` |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1`; `*` in the image |
| `OSRM_BASE_URL` | `https://router.project-osrm.org` |
| `PHOTON_BASE_URL` | `https://photon.komoot.io` |
| `USER_AGENT` | `fuelroute/1.0`, sent to both services |

Vehicle range, fuel economy, corridor width, the departure radius and the
default stop cost live at the bottom of `fuelroute/settings.py`.

## Tests

    python manage.py test

A hundred and twenty-eight tests cover the price file reader and loader, the
GeoNames matching, the city table and its extraction, route geometry, the
optimiser (including the comparison with the greedy algorithm on random
routes), both external clients with canned responses, the geocoding command,
and the API with the external services mocked. They run in a few seconds,
need no network, and run on GitHub Actions for every push, which also builds
the image and waits for its health check.

## Layout

    Dockerfile, docker-entrypoint.sh
    data/                     price file, station city coordinates, US city table
    fuelroute/                settings and the URL root
    planner/
      stations.py             reading the data files
      geonames.py             the offline gazetteer
      cities.py, states.py    "City, ST" without a geocoder
      photon.py, osrm.py      the two external services
      geo.py                  mileage along the route, corridor matching, simplification
      optimizer.py            choosing the stops
      trip.py                 puts a request together
      serializers.py, views.py  the HTTP layer
      management/commands/    extract_cities, geocode_cities, load_stations
      templates/planner/      the map page
      tests/
    postman/                  a collection of the requests above
