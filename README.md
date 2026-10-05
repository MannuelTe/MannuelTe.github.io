# How far is it in Zurich?

Interactive travel time map of Zurich by **tram, S-Bahn and train** (optionally **bus and boat**) and by **bike**.
Pick a starting point and every place in the City of Zurich and 20 neighbouring municipalities is coloured by how
long it takes to get there, with 15/30/45/60-minute isochrones and the residents and jobs inside each of them.

A Zurich fork of [À portée de tram](https://github.com/camilleroux/montpellier-temps-transport) by Camille Roux,
itself based on Anthony Castrio's [NYC Transit Time Cartogram](https://castrio.me/nyc/) and Jules Grandin's
[Paris version](https://github.com/JulesGrandin/paris-temps-transport).

Features: heatmap and isochrones from a draggable start, destination on click with a detailed route, address search
(geo.admin.ch), public transport or bike, **timetable years 2022 / 2024 / 2026 / 2027** to see the network change,
residents and jobs within each isochrone, share link.

## Run

```bash
python3 build.py --fetch            # download sources, build every timetable year, the bike layer and the page
python3 -m http.server 8000 --directory site
```

Separate steps: `fetch_data.py zurich [year …]`, `build_data.py zurich <year>`, `build_bike.py zurich`,
`build_pages.py`. `node tools/check_trips.mjs zurich` probes trips from the centre to the termini and flags odd speeds.

Raw sources (`data/zurich/`: national GTFS per year, boundaries, OSM, BFS grids, elevation) are not committed;
`fetch_data.py` downloads them again. Only the computed bundles (`site/data/*.json`) and their provenance
(`sources/*.json`: URLs, download dates, SHA-256, GTFS validity, reference day) are versioned.

## Data

| What | Source |
|---|---|
| Timetables | Swiss national GTFS, [opentransportdata.swiss](https://opentransportdata.swiss/) (2026, 2027); 2022 and 2024 from the [Mobility Database](https://mobilitydatabase.org/) archive (mdb-1092, mdb-2144). Clipped to the Zurich area at fetch time (3.7 GB of stop times → 300 MB). |
| Boundaries | [swissBOUNDARIES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d) via api3.geo.admin.ch; the 12 Stadtkreise from [Stadt Zürich Open Data](https://data.stadt-zuerich.ch/) |
| Residents / jobs | BFS hectare grids [STATPOP 2024](https://www.bfs.admin.ch/bfs/en/home/statistics/catalogues-databases.assetdetail.36171301.html) and [STATENT 2023](https://www.bfs.admin.ch/bfs/en/home/statistics/catalogues-databases.assetdetail.36073031.html) |
| Elevation | swisstopo terrain model sampled every 100 m via the geo.admin.ch profile service |
| Lines, lakes, parks, cycling streets | © OpenStreetMap contributors (ODbL), via Overpass; line colours from the OSM route relations (the Swiss GTFS has none) |
| Address search | geo.admin.ch search service, live in the browser |

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
  branches look 15 minutes closer: 524k residents within 30 min of HB against 415k now);
- changes: 1.5 min walk + wait; walking links between stops less than 450 m apart; 1.5 min to reach a railway
  platform, 1 min for funiculars and the cable car;
- excluded: replacement buses (EV), extra trains (EXT), night services, on-demand buses (Rufbus), taxis.

Walking (to and from stops, and direct): straight line at 4.5 km/h on the flat, **slower uphill and slightly faster
on gentle descents** (Tobler's hiking function on the swisstopo elevation), and **never across a lake** (Zürichsee,
Greifensee, Türlersee). Rivers are treated as crossable: the Limmat and Sihl have bridges every few hundred metres in
town.

Bike (`build_bike.py`, same model as the GIS_playground_ZH cycling project): OSM streets open to bicycles,
contraflow where `oneway:bicycle=no`, 16 km/h on the flat, slower uphill (≈10 km/h on 5 %, never below walking pace),
up to 22 km/h downhill, 1 min to unlock and to park. The street network is the one computed in GIS_playground_ZH
(its cached osmnx graph of the city + 6 km, copied to `data/zurich/bike_graph.graphml`), with OSM patches from Overpass
for Küsnacht, Zumikon, Stallikon and the edges of Kloten and Dietikon. It is folded onto the 200 m grid (each cell →
the 28 cells within 600 m, times from Dijkstra on the real network, both directions), so the browser only runs
Dijkstra on ~6,000 cells; times stay within about 5 % of a full street-network search.

Residents and jobs: each BFS hectare is added to the 200 m cell its centre falls in; the BFS rounds small counts
for privacy, so totals are approximate. In the history table, residents and jobs are held at today's numbers so
that only the network changes.

## Notes and known gaps

- **Local buses** (e.g. 912/916/918/919 in Zollikon and Küsnacht, 161 to Wollishofen, PostAuto lines) are in the
  national GTFS and in the map: tick **Bus and boat**. By default the map shows tram, S-Bahn and train only, as
  upstream does. What is missing: on-demand buses (Rufbus, excluded on purpose: no fixed times), replacement buses
  during works (excluded), and lines starting more than ~5 km outside the map are cut at the clipping box. Nothing
  beyond that needs another source; if a specific line looks absent, check its `route_desc` category in
  `routes.txt` and `excludeRouteCategories` in `cities/zurich.json`.
- **2026 dip:** the 2026 timetable runs the SZU lines S4 and S10 from Zürich Selnau instead of Zürich HB
  (HB–Selnau works) and diverts trams for construction (temporary lines 50 and 51), so reach from HB is lower than in
  2024 although most stops got slightly faster.
- **2027** is the first published version of that timetable, with far fewer trips than the final one.
- Straight-line walking is optimistic in hilly and fenced areas; rivers are crossed anywhere.

## Licences

- Code: MIT (see [LICENSE](LICENSE)), © Camille Roux for the original, fork changes under the same licence.
- Computed data (`site/data/*.json`, `sources/*.json`): derived databases under
  [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), as OpenStreetMap requires. Timetables under the
  [opentransportdata.swiss terms of use](https://opentransportdata.swiss/en/terms-of-use/); swisstopo, BFS and
  Stadt Zürich data are open government data (attribution required).
