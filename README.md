# Zurich Isochrones

Interactive travel time map of Zurich by **tram, S-Bahn and train** (optionally **bus and boat**) and by **bike**.
Pick a starting point and every place in the City of Zurich and 20 neighbouring municipalities is coloured by how
long it takes to get there, with 15/30/45/60-minute isochrones and the residents and full-time jobs inside each of them.

A Zurich fork of [À portée de tram](https://github.com/camilleroux/montpellier-temps-transport) by Camille Roux,
itself based on Anthony Castrio's [NYC Transit Time Cartogram](https://castrio.me/nyc/) and Jules Grandin's
[Paris version](https://github.com/JulesGrandin/paris-temps-transport).

In German at the site root (the default) and in English under `/en/`: `build_pages.py` renders both from one template,
and `app.js` picks its texts from the page's `lang`.

Features: heatmap and isochrones from a draggable start, destination on click with a detailed route, public transport
or bike, **timetable years 2022 / 2024 / 2026 / 2027** to see the network change, residents and full-time jobs within each
isochrone, share link, and **reach from everywhere**: two H3 hexagon maps of the residents (or land area) reachable
within 15/30/45 min from every point on the map, in one year and as the change between two years.

## Run

```bash
python3 build.py --fetch            # download sources, build every timetable year, the bike layer and the page
python3 -m http.server 8000 --directory site
```

Separate steps: `fetch_data.py zurich [year …]`, `build_data.py zurich <year>`, `build_bike.py zurich`,
`build_h3.py zurich`, `build_pages.py`. `build_h3.py` needs `numpy`, `scipy` and `h3` (`pip install "h3>=4"`); it reads
only the bundles in `site/data/` and takes about a minute. `node tools/check_trips.mjs zurich` probes trips from the centre to the termini and flags odd speeds.
`python3 tools/validate_trips.py zurich [year] [--pairs N]` compares random stop-to-stop times with the real
timetable (transport.opendata.ch), waits included on both sides.

Raw sources (`data/zurich/`: national GTFS per year, boundaries, OSM, BFS grids, elevation) are not committed;
`fetch_data.py` downloads them again. Only the computed bundles (`site/data/*.json`) and their provenance
(`sources/*.json`: URLs, download dates, SHA-256, GTFS validity, reference day) are versioned.

## Data

| What | Source |
|---|---|
| Timetables | Swiss national GTFS, [opentransportdata.swiss](https://opentransportdata.swiss/) (2026, 2027); 2022 and 2024 from the [Mobility Database](https://mobilitydatabase.org/) archive (mdb-1092, mdb-2144). Clipped to the Zurich area at fetch time (3.7 GB of stop times → 300 MB). |
| Boundaries | [swissBOUNDARIES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d) via api3.geo.admin.ch; the 12 Stadtkreise from [Stadt Zürich Open Data](https://data.stadt-zuerich.ch/) |
| Residents / full-time jobs | BFS hectare grids, each timetable year with its own: STATPOP 2022, 2024 and 2025 ([data.geo.admin.ch](https://data.geo.admin.ch/browser/index.html#/collections/ch.bfs.statistik-bevoelkerung_haushalte); 2022 for the 2022 timetable, 2024 for 2024, 2025 for 2026 and 2027) and STATENT full-time equivalents ([data.geo.admin.ch](https://data.geo.admin.ch/browser/index.html#/collections/ch.bfs.betriebszaehlungen), column `B08VZAT`; 2022 for 2022, 2024 for 2024–2027, the newest published) |
| Full-time jobs in the region, by year | BFS STAT-TAB [px-x-0602010000_102](https://www.pxweb.bfs.admin.ch/pxweb/de/px-x-0602010000_102/px-x-0602010000_102/px-x-0602010000_102.px/) (STATENT full-time equivalents per municipality, 2011 onwards), summed over the map's municipalities into `sources/zurich-jobs.json` |
| Elevation | swisstopo terrain model sampled every 100 m via the geo.admin.ch profile service |
| Lines, lakes, parks, cycling streets | © OpenStreetMap contributors (ODbL), via Overpass; line colours from the OSM route relations (the Swiss GTFS has none) |

The page has a **Data sources** expander with the exact feeds, validity periods and download dates.

## Model

Public transport (a weekday in late autumn, 7:00–20:00, per timetable year):

- ride time between stops = median of the scheduled times; route variants and operators of the Swiss feed are merged
  into one line per category and name (S10 runs under several route_ids);
- wait = half the line's own headway, between 1 and 15 min;
- **common lines:** where several lines run the same consecutive stops (S-Bahn HB → Hardbrücke → Altstetten, tram
  trunks), a trunk route "any of S3, S5, …" runs over that shared section only, with half the combined headway as its
  wait; where the lines split, the rider changes to the specific line and waits for it. Routing takes the better of
  waiting for one's own line from the start or taking the first train and changing at the split, so shared corridors
  are fast and branch stops are not (an earlier version gave every line the pooled wait, which made single-line
  branches look 15 minutes closer: at the time, 524k residents within 30 min of HB against 415k with the fix);
- **pooled rides:** lines that reach the same later stop by different routes (S5 via Hardbrücke and S14 non-stop
  from HB to Hedingen; IC, S2, S16 and S24 from HB to the airport) also get a ride "any of them to that stop", with
  half the combined headway as wait, where that beats waiting for the best single line by 2 min or more;
- **termini:** where most trips on a segment end at the next stop (the 912/916 inbound at Bellevue), the ride arrives
  in an arrive-only state; going on means changing vehicle, with the change time and the outbound wait, instead of
  riding through the terminus;
- wait shown in the route panel = half the headway of the line (or of the shared section) at that stop, from its
  departures 7:00–20:00 on the reference day (912/916 at Opernhaus: 76 departures → 10.3 min headway → ~5 min);
- changes: 1.5 min walk + wait; walking links between stops less than 450 m apart; 1.5 min to reach a railway
  platform, 1 min for funiculars and the cable car;
- excluded: replacement buses (EV), extra trains (EXT), night services, on-demand buses (Rufbus), taxis.

Walking (to and from stops, and direct): straight line at 4.5 km/h on the flat, **slower uphill and slightly faster
on gentle descents** (Tobler's hiking function on the swisstopo elevation), and **never across a lake** (Zürichsee,
Greifensee, Türlersee). Rivers are treated as crossable: the Limmat and Sihl have bridges every few hundred metres in
town.

Bike (`build_bike.py`, same model as the GIS_playground_ZH cycling project): OSM streets open to bicycles,
contraflow where `oneway:bicycle=no`, 20 km/h on the flat, slower uphill (≈10 km/h on 5 %, never below 5 km/h),
25 km/h on a 3 % descent and 30 km/h from 5 %, 1 min to unlock and to park. The street network is the one computed in GIS_playground_ZH
(its cached osmnx graph of the city + 6 km, copied to `data/zurich/bike_graph.graphml`), with OSM patches from Overpass
for Küsnacht, Zumikon, Stallikon and the edges of Kloten and Dietikon. It is folded onto the 200 m grid (each cell →
the 28 cells within 600 m, times from Dijkstra on the real network, both directions), so the browser only runs
Dijkstra on ~6,000 cells; times stay within about 5 % of a full street-network search.

Reach from everywhere (`build_h3.py`): the map's land is cut into H3 hexagons (resolution 9, ≈0.1 km², about 2,460
of them). From each hexagon's centre the same model as the browser (seeding, waits, Tobler walking, lakes) gives the
time to every 200 m cell, for each timetable year and two networks: all lines with buses and boats, and rail only
(the map's default). Dijkstra runs in batches with one virtual source node per origin (scipy). The result,
`site/data/<city>-h3.json`, holds per hexagon the residents and the number of land cells within 15/30/45 min. Checks:
from Zürich HB it reproduces the page's figures (rail only) and the browser with « Bus and boat » ticked.

Residents and full-time jobs: each BFS hectare is added to the 200 m cell its centre falls in; the BFS rounds small
counts for privacy, so totals are approximate. Both move with the timetable year (STATPOP of that year, or the newest
published; STATENT full-time equivalents likewise), so reach within a time combines the network with where people
live and work. Where two timetables share a grid (full-time jobs from 2024, residents from 2026) the difference is the
network alone. The history table also shows the region's STATENT full-time equivalents per year
(`sources/zurich-jobs.json`) for scale.

## Notes and known gaps

- **Local buses** (e.g. 912/916/918/919 in Zollikon and Küsnacht, 161 to Wollishofen, PostAuto lines) are in the
  national GTFS and in the map: tick **Bus and boat**. By default the map shows tram, S-Bahn and train only, as
  upstream does. What is missing: on-demand buses (Rufbus, excluded on purpose: no fixed times), replacement buses
  during works (excluded), and lines starting more than ~5 km outside the map are cut at the clipping box. Nothing
  beyond that needs another source; if a specific line looks absent, check its `route_desc` category in
  `routes.txt` and `excludeRouteCategories` in `cities/zurich.json`.
- **2026 dip:** the 2026 timetable runs the SZU lines S4 and S10 from Zürich Selnau instead of Zürich HB
  (HB–Selnau works) and diverts trams for construction (temporary lines 50 and 51), so reach from HB is lower than in
  2024: the Sihltal stops are about 10 min further from HB, the S10 stops in town 11–12 min (Uetliberg itself barely
  changes), while 215 of the 314 tram and rail stops change by a minute or less.
- **2027** is the first published version of that timetable, with far fewer trips than the final one.
- Straight-line walking is optimistic in hilly and fenced areas; rivers are crossed anywhere.

## Licences

- Code: MIT (see [LICENSE](LICENSE)), © Camille Roux for the original, fork changes under the same licence.
- Computed data (`site/data/*.json`, `sources/*.json`): derived databases under
  [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), as OpenStreetMap requires. Timetables under the
  [opentransportdata.swiss terms of use](https://opentransportdata.swiss/en/terms-of-use/); swisstopo, BFS and
  Stadt Zürich data are open government data (attribution required).
