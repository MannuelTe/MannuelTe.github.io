#!/usr/bin/env python3
"""Download the raw sources of a city into data/<city>/.

Usage: python3 fetch_data.py <city> [year …] [--gtfs-only | --context-only] [--refresh]
  (years: timetable years of the config, all by default; --refresh: download the national GTFS again)

The Swiss national GTFS covers the whole country: about 300 MB zipped, up to 3.7 GB of stop times. Each timetable
year is kept as data/<city>/gtfs_ch_<year>.zip and clipped to the city's bounding box into data/<city>/gtfs_<year>.zip,
which is what build_data.py reads.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cities import STATENT_GRID_URL, load_city, statent_year

ROOT = Path(__file__).resolve().parent
USER_AGENT = "zurich-temps-transport/0.1 (build script; github.com/MannuelTe/MannuelTe.github.io)"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
# Municipal boundaries (swissBOUNDARIES3D, swisstopo) and the 12 districts (Stadtkreise) of the City of Zurich.
MUNICIPALITIES_URL = (
    "https://api3.geo.admin.ch/rest/services/api/MapServer/identify?"
    "layers=all:ch.swisstopo.swissboundaries3d-gemeinde-flaeche.fill&timeInstant={year}&geometry={bbox}"
    "&geometryType=esriGeometryEnvelope&returnGeometry=true&geometryFormat=geojson&sr=4326&tolerance=0&limit=200"
)
KREISE_URL = (
    "https://www.ogd.stadt-zuerich.ch/wfs/geoportal/Stadtkreise?service=WFS&version=1.1.0&request=GetFeature"
    "&outputFormat=GeoJSON&typename=adm_stadtkreise_a&srsName=EPSG:4326"
)


# Hectare grid of residents (STATPOP) from the Federal Statistical Office; the jobs grids (STATENT, one per year)
# are listed in cities.py.
BFS_ASSETS = {
    "statpop.zip": "https://dam-api.bfs.admin.ch/hub/api/dam/assets/36171301/master",  # STATPOP 2024
}
# Employees per municipality and year (STATENT 2011 onwards), BFS STAT-TAB: the region's job total in each year.
STATENT_MUNICIPAL_URL = "https://www.pxweb.bfs.admin.ch/api/v1/de/px-x-0602010000_102/px-x-0602010000_102.px"


def bbox(values) -> str:
    return ",".join(str(v) for v in values)


def download(url: str, data: bytes | None = None) -> bytes:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=600) as response:
        return response.read()


def download_to(url: str, path: Path) -> None:
    """Stream a large file to disk (the national GTFS does not fit comfortably in memory)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    partial = path.with_suffix(".part")
    with urllib.request.urlopen(request, timeout=600) as response, partial.open("wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    partial.replace(path)


def overpass(query: str) -> bytes:
    payload = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(6):
        for url in OVERPASS_URLS:
            try:
                body = download(url, payload)
                json.loads(body)
                return body
            except Exception as error:  # noqa: BLE001 - Overpass is often busy, just retry
                print(f"  {url} failed ({error}), retrying…")
        time.sleep(10 * (attempt + 1))
    raise RuntimeError("Overpass unavailable")


def record(out: Path, name: str, source: str, how: str = "download") -> None:
    """Note in data/<city>/manifest.json where each raw file comes from and when it was fetched."""
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    path = out / name
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    fetched = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if how != "download" else datetime.now(timezone.utc)
    manifest[name] = {
        "source": source,
        "how": how,
        "fetchedAt": fetched.isoformat(timespec="seconds"),
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --- GTFS -------------------------------------------------------------------


def read_rows(archive: zipfile.ZipFile, name: str):
    with archive.open(name) as handle:
        reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8-sig", newline=""))
        header = next(reader)
        yield header
        yield from reader


def clip_gtfs(source: Path, target: Path, clip: list[float]) -> None:
    """Keep the stops inside `clip` (south, west, north, east) and everything that serves them.

    Trips are cut to their stops inside the box: a train from Zurich to Bern keeps its Zurich stops only.
    """
    south, west, north, east = clip
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as out:

        def write_table(name: str, rows) -> int:
            count = -1
            with out.open(name, "w", force_zip64=True) as handle:
                text = io.TextIOWrapper(handle, encoding="utf-8", newline="")
                writer = csv.writer(text, lineterminator="\n")
                for row in rows:
                    writer.writerow(row)
                    count += 1
                text.flush()
                text.detach()
            return count

        rows = read_rows(archive, "stops.txt")
        header = next(rows)
        lat_col, lon_col, id_col, parent_col = (header.index(c) for c in ("stop_lat", "stop_lon", "stop_id", "parent_station"))
        stops = [row for row in rows if row[lat_col] and south <= float(row[lat_col]) <= north and west <= float(row[lon_col]) <= east]
        stop_ids = {row[id_col] for row in stops}
        parents = {row[parent_col] for row in stops if row[parent_col]}

        # stop_times.txt is 3.7 GB: split each line by hand rather than through the csv module.
        print("  clipping stop_times.txt (a few minutes)…")
        trip_ids = set()
        kept = 0
        with archive.open("stop_times.txt") as handle, out.open("stop_times.txt", "w", force_zip64=True) as dst:
            header_line = handle.readline()
            columns = header_line.decode("utf-8-sig").strip().split(",")
            trip_col, stop_col = columns.index("trip_id"), columns.index("stop_id")
            dst.write(header_line.decode("utf-8-sig").encode())
            needed = max(trip_col, stop_col) + 1
            for line in handle:
                parts = line.split(b",", needed)
                stop_id = parts[stop_col].strip(b'"').decode()
                if stop_id in stop_ids:
                    dst.write(line)
                    trip_ids.add(parts[trip_col].strip(b'"').decode())
                    kept += 1
        print(f"  {len(stop_ids)} stops, {len(trip_ids)} trips, {kept} stop times")

        trips_rows = read_rows(archive, "trips.txt")
        trips_header = next(trips_rows)
        t_trip, t_route, t_service = (trips_header.index(c) for c in ("trip_id", "route_id", "service_id"))
        trips = [row for row in trips_rows if row[t_trip] in trip_ids]
        route_ids = {row[t_route] for row in trips}
        service_ids = {row[t_service] for row in trips}
        write_table("trips.txt", [trips_header, *trips])

        def filtered(name: str, column: str, keep: set):
            rows = read_rows(archive, name)
            header = next(rows)
            index = header.index(column)
            yield header
            yield from (row for row in rows if row[index] in keep)

        all_stops = read_rows(archive, "stops.txt")
        write_table("stops.txt", (row for i, row in enumerate(all_stops) if i == 0 or row[id_col] in stop_ids or row[id_col] in parents))
        write_table("routes.txt", filtered("routes.txt", "route_id", route_ids))
        write_table("calendar.txt", filtered("calendar.txt", "service_id", service_ids))
        write_table("calendar_dates.txt", filtered("calendar_dates.txt", "service_id", service_ids))
        for name in ("agency.txt", "feed_info.txt"):
            out.writestr(name, archive.read(name))


def fetch_gtfs(city: dict, out: Path, year: str, refresh: bool) -> None:
    timetable = city["timetables"][year]
    national = out / f"gtfs_ch_{year}.zip"
    if refresh or not national.exists():
        print(f"GTFS {year} (national feed, ~300 MB)…")
        download_to(timetable["url"], national)
        record(out, national.name, timetable["url"])
    else:
        print(f"GTFS {year}: keeping {national.relative_to(ROOT)} (--refresh to download it again)")
    print(f"Clipping the {year} GTFS to {city['gtfsClipBbox']}…")
    clip_gtfs(national, out / f"gtfs_{year}.zip", city["gtfsClipBbox"])
    record(out, f"gtfs_{year}.zip", f"{national.name} clipped to {city['gtfsClipBbox']}", how="derived")


# --- Boundaries, water, parks -----------------------------------------------


def fetch_boundaries(city: dict, out: Path) -> None:
    """Municipalities of the map, the City of Zurich split into its 12 districts (Kreise)."""
    south, west, north, east = city["gtfsClipBbox"]
    url = MUNICIPALITIES_URL.format(year=datetime.now().year, bbox=f"{west},{south},{east},{north}")
    print("Municipal boundaries (swisstopo)…")
    results = json.loads(download(url))["results"]
    wanted = set(city["municipalities"])
    features = []
    for result in results:
        name = result["properties"]["gemname"]
        if name in wanted and name != "Zürich":
            features.append({"type": "Feature", "properties": {"name": name}, "geometry": result["geometry"]})
    missing = wanted - {feature["properties"]["name"] for feature in features} - {"Zürich"}
    if missing:
        sys.exit(f"Municipalities not found in swissBOUNDARIES3D: {sorted(missing)}")
    if "Zürich" in wanted:
        print("Districts of the City of Zurich (Stadt Zürich open data)…")
        for feature in json.loads(download(KREISE_URL))["features"]:
            features.append({"type": "Feature", "properties": {"name": feature["properties"]["bezeichnung"]}, "geometry": feature["geometry"]})
    (out / "communes.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    record(out, "communes.geojson", f"{url} + {KREISE_URL}")


def fetch_osm_rail(city: dict, out: Path) -> None:
    print("Rail lines (OSM)…")
    rail = bbox(city["osmRailBbox"])
    # Numbered trains are the rack railways (Dolderbahn 25); aerialways the Adliswil–Felsenegg cable car.
    query = (
        "[out:json][timeout:180];("
        f'relation["route"~"^(tram|light_rail|funicular|subway|aerialway)$"]({rail});'
        f'relation["route"="train"]["ref"~"^S[0-9]+$"]({rail});'
        f'relation["route"="train"]["network"="{city["network"]}"]["ref"~"^[0-9]+$"]({rail});'
        ");out geom;"
    )
    (out / "osm_rail.json").write_bytes(overpass(query))
    record(out, "osm_rail.json", f"Overpass API: {query}")


def fetch_osm(city: dict, out: Path) -> None:
    if city.get("railGeometry") == "osm":
        fetch_osm_rail(city, out)

    print("Water and parks (OSM)…")
    area, parks = bbox(city["osmBbox"]), bbox(city["parksBbox"])
    query = (
        "[out:json][timeout:180];("
        f'relation["natural"="water"]({area});'
        f'way["natural"="water"]({area});'
        f'relation["leisure"="park"]({parks});'
        f'way["leisure"="park"]({parks});'
        ");out geom;"
    )
    (out / "osm_water_parks.json").write_bytes(overpass(query))
    record(out, "osm_water_parks.json", f"Overpass API: {query}")


# Elevation: swisstopo's profile service samples its terrain model (swissALTI3D / DHM25) along a line, so one call
# per row of a 100 m LV95 grid is enough for the whole map (instead of hundreds of 1 km swissALTI3D tiles).
PROFILE_URL = "https://api3.geo.admin.ch/rest/services/profile.json"
ELEVATION_STEP = 100


def wgs84_to_lv95(lat: float, lon: float) -> tuple[float, float]:
    """swisstopo's approximate formulas (about 1 m accurate): (lat, lon) → LV95 (east, north)."""
    phi = (lat * 3600 - 169028.66) / 10000
    lam = (lon * 3600 - 26782.5) / 10000
    east = 2600072.37 + 211455.93 * lam - 10938.51 * lam * phi - 0.36 * lam * phi ** 2 - 44.54 * lam ** 3
    north = 1200147.07 + 308807.95 * phi + 3745.25 * lam ** 2 + 76.63 * phi ** 2 - 194.56 * lam ** 2 * phi + 119.79 * phi ** 3
    return east, north


def fetch_elevation(city: dict, out: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    south, west, north, east = city["gtfsClipBbox"]
    corners = [wgs84_to_lv95(lat, lon) for lat in (south, north) for lon in (west, east)]
    step = ELEVATION_STEP
    min_e = int(min(e for e, _ in corners) // step * step)
    max_e = int(-(-max(e for e, _ in corners) // step) * step)
    min_n = int(min(n for _, n in corners) // step * step)
    max_n = int(-(-max(n for _, n in corners) // step) * step)
    cols = (max_e - min_e) // step + 1
    northings = list(range(min_n, max_n + 1, step))
    print(f"Elevation (swisstopo profile service, {cols}×{len(northings)} points)…")

    def row(northing: int) -> list[float]:
        query = urllib.parse.urlencode({
            "geom": json.dumps({"type": "LineString", "coordinates": [[min_e, northing], [max_e, northing]]}),
            "sr": "2056", "nb_points": str(cols), "distinct_points": "true",
        })
        for attempt in range(5):
            try:
                points = json.loads(download(f"{PROFILE_URL}?{query}"))
                return [round(point["alts"]["COMB"], 1) for point in points]
            except Exception as error:  # noqa: BLE001 - the public service throttles bursts
                print(f"  row {northing} failed ({error}), retrying…")
                time.sleep(2 * (attempt + 1))
        raise RuntimeError("profile service unavailable")

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(row, northings))
    if any(len(values) != cols for values in rows):
        sys.exit("Elevation rows have unexpected lengths")
    grid = {"crs": "EPSG:2056", "minE": min_e, "minN": min_n, "step": step, "cols": cols, "rows": len(rows), "z": rows}
    (out / "elevation.json").write_text(json.dumps(grid, separators=(",", ":")), encoding="utf-8")
    record(out, "elevation.json", f"{PROFILE_URL} (COMB terrain model), {step} m LV95 grid")


# Streets cyclists may use, as in the GIS_playground_ZH cycling model (osmnx "bike" filter minus footways, plus
# footways and paths where cycling is explicitly allowed).
BIKE_FILTERS = [
    '["highway"]["area"!~"yes"]["highway"!~"abandoned|bus_guideway|construction|corridor|elevator|escalator|footway|'
    'motor|no|planned|platform|proposed|raceway|razed|steps|pedestrian|bridleway"]'
    '["bicycle"!~"no"]["service"!~"private"]["access"!~"private|no"]',
    '["highway"~"footway|pedestrian|bridleway|path"]["bicycle"~"yes|designated|permissive"]',
]


def fetch_bike_network(city: dict, out: Path) -> None:
    """Streets for the bike layer. The main network is the zh-cycling graph (data/<city>/bike_graph.graphml, City of
    Zurich + 6 km); Overpass fills the parts of the map beyond it (`bikePatchBboxes`), or the whole map without it.
    A single query for the whole map is too heavy for the public Overpass servers."""
    print("Cycling network (OSM)…")
    has_graph = (out / "bike_graph.graphml").exists()
    boxes = city.get("bikePatchBboxes", []) if has_graph else [city["gtfsClipBbox"]]
    if not has_graph:
        print("  bike_graph.graphml missing (copy it from GIS_playground_ZH/data/cache/): downloading the whole map")
    query = "[out:json][timeout:300];(" + "".join(f"way{f}({bbox(b)});" for b in boxes for f in BIKE_FILTERS) + ");out body;>;out skel qt;"
    (out / "osm_bike.json").write_bytes(overpass(query))
    record(out, "osm_bike.json", f"Overpass API: {query}")


def fetch_bfs(city: dict, out: Path) -> None:
    for name, url in BFS_ASSETS.items():
        print(f"BFS hectare grid {name}…")
        download_to(url, out / name)
        record(out, name, url)
    for year in sorted({statent_year(y) for y in city["timetables"]}):
        name, url = f"statent_{year}.csv", STATENT_GRID_URL.format(year=year)
        print(f"BFS hectare grid {name}…")
        download_to(url, out / name)
        record(out, name, url)


def fetch_regional_jobs(city: dict) -> None:
    """Employees in the map's municipalities for every STATENT year, into sources/<city>-jobs.json (committed)."""
    print("BFS STATENT employees by municipality…")
    meta = json.loads(download(STATENT_MUNICIPAL_URL))
    places = next(v for v in meta["variables"] if v["code"] == "Gemeinde")
    names = set(city["municipalities"])
    codes = [code for code, label in zip(places["values"], places["valueTexts"]) if label.partition(" ")[2] in names]
    if len(codes) != len(names):
        raise RuntimeError(f"STATENT: found {len(codes)} of {len(names)} municipalities")
    query = {
        "query": [
            {"code": "Gemeinde", "selection": {"filter": "item", "values": codes}},
            {"code": "Wirtschaftssektor", "selection": {"filter": "item", "values": ["999"]}},  # all sectors
            {"code": "Beobachtungseinheit", "selection": {"filter": "item", "values": ["2"]}},  # employees
        ],
        "response": {"format": "json-stat2"},
    }
    data = json.loads(download(STATENT_MUNICIPAL_URL, json.dumps(query).encode()))
    years = list(data["dimension"]["Jahr"]["category"]["index"])
    width = len(codes)  # values run year by year, municipality fastest
    totals = {year: sum(v or 0 for v in data["value"][i * width:(i + 1) * width]) for i, year in enumerate(years)}
    out = ROOT / "sources" / f"{city['slug']}-jobs.json"
    out.write_text(json.dumps({
        "source": STATENT_MUNICIPAL_URL,
        "table": "px-x-0602010000_102",
        "measure": "employees, all sectors, summed over the map's municipalities",
        "municipalities": len(codes),
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "employees": totals,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    city = load_city(sys.argv[1])
    out = ROOT / "data" / city["slug"]
    out.mkdir(parents=True, exist_ok=True)
    years = [arg for arg in sys.argv[2:] if not arg.startswith("--")] or list(city["timetables"])
    if "--context-only" not in sys.argv:
        for year in years:
            fetch_gtfs(city, out, year, refresh="--refresh" in sys.argv)
    if "--gtfs-only" in sys.argv:
        return
    fetch_boundaries(city, out)
    fetch_osm(city, out)
    fetch_bfs(city, out)
    fetch_regional_jobs(city)
    fetch_elevation(city, out)
    fetch_bike_network(city, out)


if __name__ == "__main__":
    main()
