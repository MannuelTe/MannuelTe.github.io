#!/usr/bin/env python3
"""Build the compact JSON bundle of a city for the transit time map.

Usage: python3 build_data.py <city> [year]
  (reads data/<city>/gtfs_<year>.zip, writes site/data/<city>-<year>.json and sources/<city>-<year>.json;
   year: a timetable year of the config, its default timetable when omitted)
"""

from __future__ import annotations

import base64
import csv
import heapq
import io
import json
import math
import statistics
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from cities import load_city

ROOT = Path(__file__).resolve().parent

LAND_PAD_METERS = 1200.0
VIEW_PAD_METERS = 900.0

GRID_CELL_METERS = 200.0
# Walking speed on the flat (4.5 km/h), applied to straight-line distances: tram lines run along straight avenues.
WALK_METERS_PER_MINUTE = 75.0
# Hills: Tobler's hiking function, speed ∝ exp(-3.5·|slope + 0.05|), scaled so that the flat stays at 4.5 km/h
# (fastest on a gentle 5 % descent, half speed around 15 % uphill). Slopes are read on the straight line between
# the two ends, over at least SLOPE_MIN_METERS so that a few metres of noise do not count as a cliff.
TOBLER_K = 3.5
TOBLER_OFFSET = 0.05
SLOPE_MIN_METERS = 60.0
# Lakes cannot be walked across: rasterised at this resolution, and walking legs sampled every BARRIER_STEP metres.
BARRIER_CELL_METERS = 50.0
BARRIER_STEP_METERS = 25.0
# Cells look a little further for stops, since legs across a lake are dropped.
CELL_CANDIDATE_STATIONS = 10
CELL_NEAREST_STATIONS = 5
CELL_NEAREST_RAIL_STATIONS = 3
ORIGIN_NEAREST_STATIONS = 8  # stations reachable on foot from a departure point (also read by site/app.js)
DEFAULT_BOARD_WAIT = 5.0
TRANSFER_WALK = 1.5
INTER_COMPLEX_WALK_RADIUS = 450.0
STOP_GROUP_RADIUS = 350.0
MIN_RIDE_MINUTES = 0.4
MIN_WAIT = 1.0
MAX_WAIT = 15.0
# Daytime window used to measure headways and ride times (weekday, 7h–20h).
SERVICE_WINDOW = (7 * 3600, 20 * 3600)
MIN_RING_DISTANCE = 45.0
MIN_LINE_DISTANCE = 25.0
MIN_PARK_AREA = 20_000.0
MIN_WATER_AREA = 15_000.0
CONTEXT_RING_DISTANCE = 80.0
# Water bodies at least this large (or lagoons) are not land: no heatmap, no pins.
WATER_MASK_AREA = 1_000_000.0
LAKE_MIN_AREA = 100_000.0

# GTFS route_type → mode (basic and extended types).
RAIL_MODES = {"tram", "metro", "sbahn", "train", "funicular", "cable", "busway"}
# Minutes to walk from the street to the platform (and back): stairs, underpasses, long platforms.
MODE_ACCESS_MINUTES = {"metro": 1.0, "sbahn": 1.5, "train": 1.5, "funicular": 1.0, "cable": 1.0}
# Default line colours when neither the GTFS nor OpenStreetMap gives one.
MODE_COLORS = {"tram": "#1d4f91", "sbahn": "#2b74c7", "train": "#c7202f", "funicular": "#6b4c9a", "cable": "#6b4c9a",
               "ferry": "#1f8fbf", "bus": "#888888"}
# Modes whose lines share the wait towards a common next stop.
WAIT_GROUPS = {"sbahn": "rail", "train": "rail", "tram": "tram", "bus": "bus", "busway": "bus"}
# Lines of these modes are listed in the page's table (long-distance trains would swamp it).
TABLE_MODES = ("tram", "sbahn", "funicular", "cable")
# Communes kept when a city config says "communes": "served": enough stops, and not too far from tram/metro.
SERVED_MIN_STOPS = 3
SERVED_MAX_RAIL_DISTANCE = 12_000.0
# Preview image (og): arrival at the rail station closest to this travel time from the centre.
OG_TRIP_MINUTES = 20
REFERENCE_HORIZON_DAYS = 60


def route_mode(route_type: str) -> str:
    value = int(route_type or 3)
    if value == 0 or 900 <= value < 1000:
        return "tram"
    if value == 109:
        return "sbahn"
    if value == 2 or 100 <= value < 200:
        return "train"
    if value == 1 or 400 <= value < 500:
        return "metro"
    if value == 7 or 1400 <= value < 1500:
        return "funicular"
    if value in (5, 6) or 1300 <= value < 1400:
        return "cable"
    if value == 4 or 1000 <= value < 1300:
        return "ferry"
    return "bus"


Point = Tuple[float, float]
Ring = List[Point]
Polygon = List[Ring]
MultiPolygon = List[Polygon]

LAT0 = 0.0  # set from the city config in main()
GTFS_FILE = "gtfs.zip"  # set from the timetable year in main()


def lonlat_to_xy(lon: float, lat: float) -> Point:
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = meters_per_deg_lat * math.cos(math.radians(LAT0))
    return lon * meters_per_deg_lon, lat * meters_per_deg_lat


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def round_point(point: Point) -> List[float]:
    return [round(point[0], 1), round(point[1], 1)]


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def ring_area(ring: Sequence[Point]) -> float:
    area = 0.0
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def polygon_centroid(ring: Sequence[Point]) -> Point:
    area = ring_area(ring) or 1.0
    factor = 1.0 / (6.0 * area)
    cx = cy = 0.0
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return cx * factor, cy * factor


def simplify_polyline(points: Sequence[Point], min_distance: float) -> List[Point]:
    if len(points) <= 2:
        return list(points)
    simplified = [points[0]]
    for point in points[1:-1]:
        if dist(point, simplified[-1]) >= min_distance:
            simplified.append(point)
    if points[-1] != simplified[-1]:
        simplified.append(points[-1])
    return simplified


def simplify_ring(ring: Sequence[Point], min_distance: float) -> Ring:
    if len(ring) <= 4:
        return list(ring)
    core = list(ring[:-1]) if ring[0] == ring[-1] else list(ring)
    simplified = [core[0]]
    for point in core[1:]:
        if dist(point, simplified[-1]) >= min_distance:
            simplified.append(point)
    if len(simplified) < 3:
        simplified = core[:3]
    simplified.append(simplified[0])
    return simplified


def point_in_ring(point: Point, ring: Sequence[Point]) -> bool:
    x, y = point
    inside = False
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        intersects = (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
        if intersects:
            inside = not inside
    return inside


def ring_bounds(ring: Sequence[Point]) -> Tuple[float, float, float, float]:
    xs = [x for x, _ in ring]
    ys = [y for _, y in ring]
    return min(xs), min(ys), max(xs), max(ys)


class PolygonSet:
    """Point-in-polygon tests with a bounding-box pre-check (big rivers have thousands of vertices)."""

    def __init__(self, polygons: MultiPolygon):
        self.items = [(ring_bounds(polygon[0]), polygon) for polygon in polygons if polygon and len(polygon[0]) >= 4]

    def contains(self, point: Point) -> bool:
        x, y = point
        for (min_x, min_y, max_x, max_y), polygon in self.items:
            if x < min_x or x > max_x or y < min_y or y > max_y:
                continue
            if point_in_ring(point, polygon[0]) and not any(point_in_ring(point, hole) for hole in polygon[1:]):
                return True
        return False


def multipolygon_bounds(multipolygon: MultiPolygon, pad: float) -> Tuple[float, float, float, float]:
    xs = [x for polygon in multipolygon for ring in polygon for x, _ in ring]
    ys = [y for polygon in multipolygon for ring in polygon for _, y in ring]
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def coords_to_polygons(geometry: dict, min_distance: float = MIN_RING_DISTANCE) -> MultiPolygon:
    geom_type = geometry["type"]
    coords = geometry["coordinates"]
    if geom_type == "Polygon":
        polygons = [coords]
    elif geom_type == "MultiPolygon":
        polygons = coords
    else:
        return []
    converted: MultiPolygon = []
    for rings in polygons:
        polygon: Polygon = []
        for ring in rings:
            points = [lonlat_to_xy(lon, lat) for lon, lat in ring]
            if len(points) < 4:
                continue
            if points[0] != points[-1]:
                points.append(points[0])
            polygon.append(simplify_ring(points, min_distance))
        if polygon:
            converted.append(polygon)
    return converted


def serialize_polygon(polygon: Polygon) -> List[List[List[float]]]:
    return [[round_point(point) for point in ring] for ring in polygon]


class StationIndex:
    """Bucket grid for nearest-station queries (cities have thousands of stops)."""

    def __init__(self, points: Sequence[Point], indexes: Sequence[int], size: float = 800.0):
        self.size = size
        self.points = points
        self.buckets: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for index in indexes:
            x, y = points[index]
            self.buckets[(int(x // size), int(y // size))].append(index)
        self.empty = not indexes

    def nearest(self, point: Point, count: int, max_rings: int = 40) -> List[Tuple[float, int]]:
        if self.empty:
            return []
        cx, cy = int(point[0] // self.size), int(point[1] // self.size)
        found: List[Tuple[float, int]] = []
        for ring in range(max_rings + 1):
            for gx in range(cx - ring, cx + ring + 1):
                for gy in range(cy - ring, cy + ring + 1):
                    if max(abs(gx - cx), abs(gy - cy)) != ring:
                        continue
                    for index in self.buckets.get((gx, gy), ()):
                        found.append((dist(point, self.points[index]), index))
            found.sort()
            # Every station closer than `ring * size` is already found.
            if len(found) >= count and found[count - 1][0] <= ring * self.size:
                break
        return found[:count]

    def within(self, point: Point, radius: float) -> List[int]:
        reach = int(radius // self.size) + 1
        cx, cy = int(point[0] // self.size), int(point[1] // self.size)
        return [
            index
            for gx in range(cx - reach, cx + reach + 1)
            for gy in range(cy - reach, cy + reach + 1)
            for index in self.buckets.get((gx, gy), ())
            if dist(point, self.points[index]) <= radius
        ]


# --- Terrain: elevation and lakes -------------------------------------------


def xy_to_lonlat(point: Point) -> Tuple[float, float]:
    meters_per_deg_lat = 111_320.0
    return point[0] / (meters_per_deg_lat * math.cos(math.radians(LAT0))), point[1] / meters_per_deg_lat


def wgs84_to_lv95(lat: float, lon: float) -> Tuple[float, float]:
    """swisstopo's approximate formulas (about 1 m accurate): (lat, lon) → LV95 (east, north)."""
    phi = (lat * 3600 - 169028.66) / 10000
    lam = (lon * 3600 - 26782.5) / 10000
    east = 2600072.37 + 211455.93 * lam - 10938.51 * lam * phi - 0.36 * lam * phi ** 2 - 44.54 * lam ** 3
    north = 1200147.07 + 308807.95 * phi + 3745.25 * lam ** 2 + 76.63 * phi ** 2 - 194.56 * lam ** 2 * phi + 119.79 * phi ** 3
    return east, north


def tobler_factor(rise: float, run: float) -> float:
    """Walking speed relative to the flat for a climb of `rise` metres over `run` metres (negative: downhill)."""
    slope = rise / max(run, SLOPE_MIN_METERS)
    return math.exp(-TOBLER_K * abs(slope + TOBLER_OFFSET)) / math.exp(-TOBLER_K * TOBLER_OFFSET)


class Terrain:
    """Elevation (swisstopo, data/<city>/elevation.json) and lakes, for walking times. Without the elevation file the
    city is flat; without lakes nothing blocks."""

    def __init__(self, data_dir: Path, bounds, lakes: MultiPolygon):
        path = data_dir / "elevation.json"
        self.grid = load_json(path) if path.exists() else None
        if not self.grid:
            print("  elevation.json missing: walking on the flat (python3 fetch_data.py <city> --context-only)")
        min_x, min_y, max_x, max_y = bounds
        self.origin = (min_x, min_y)
        self.cols = math.ceil((max_x - min_x) / BARRIER_CELL_METERS)
        self.rows = math.ceil((max_y - min_y) / BARRIER_CELL_METERS)
        self.bits = bytearray((self.cols * self.rows + 7) // 8)
        lake_set = PolygonSet(lakes)
        for polygon in lakes:
            px0, py0, px1, py1 = ring_bounds(polygon[0])
            c0, c1 = max(0, int((px0 - min_x) // BARRIER_CELL_METERS)), min(self.cols - 1, int((px1 - min_x) // BARRIER_CELL_METERS))
            r0, r1 = max(0, int((py0 - min_y) // BARRIER_CELL_METERS)), min(self.rows - 1, int((py1 - min_y) // BARRIER_CELL_METERS))
            for row in range(r0, r1 + 1):
                for col in range(c0, c1 + 1):
                    centre = (min_x + (col + 0.5) * BARRIER_CELL_METERS, min_y + (row + 0.5) * BARRIER_CELL_METERS)
                    if lake_set.contains(centre):
                        index = row * self.cols + col
                        self.bits[index >> 3] |= 1 << (index & 7)

    def z(self, point: Point) -> float:
        if not self.grid:
            return 0.0
        lon, lat = xy_to_lonlat(point)
        east, north = wgs84_to_lv95(lat, lon)
        g = self.grid
        gx = clamp((east - g["minE"]) / g["step"], 0, g["cols"] - 1)
        gy = clamp((north - g["minN"]) / g["step"], 0, g["rows"] - 1)
        c0, r0 = int(gx), int(gy)
        c1, r1 = min(c0 + 1, g["cols"] - 1), min(r0 + 1, g["rows"] - 1)
        tx, ty = gx - c0, gy - r0
        z = g["z"]
        return (z[r0][c0] * (1 - tx) + z[r0][c1] * tx) * (1 - ty) + (z[r1][c0] * (1 - tx) + z[r1][c1] * tx) * ty

    def is_lake(self, point: Point) -> bool:
        col = int((point[0] - self.origin[0]) // BARRIER_CELL_METERS)
        row = int((point[1] - self.origin[1]) // BARRIER_CELL_METERS)
        if not (0 <= col < self.cols and 0 <= row < self.rows):
            return False
        index = row * self.cols + col
        return bool(self.bits[index >> 3] >> (index & 7) & 1)

    def crosses_lake(self, a: Point, b: Point) -> bool:
        steps = max(1, math.ceil(dist(a, b) / BARRIER_STEP_METERS))
        return any(self.is_lake((a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps)) for k in range(1, steps))

    def walk(self, a: Point, za: float, b: Point, zb: float) -> float:
        """Minutes on foot from a to b, uphill slower and gently downhill faster; infinite across a lake."""
        meters = dist(a, b)
        if self.crosses_lake(a, b):
            return math.inf
        return meters / (WALK_METERS_PER_MINUTE * tobler_factor(zb - za, meters))

    def serialize(self) -> dict:
        return {"cell": BARRIER_CELL_METERS, "step": BARRIER_STEP_METERS, "cols": self.cols, "rows": self.rows,
                "bits": base64.b64encode(bytes(self.bits)).decode()}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --- Land, water, parks -----------------------------------------------------


def served_communes(payload: dict, stations: Sequence[dict]) -> set:
    """Communes with a few stops of the network and a tram/metro station within reach: big intercommunalities
    (Grand Reims, Nice Côte d'Azur…) reach far beyond their urban network."""
    rail_points = [station["point"] for station in stations if station["rail"]]
    names = set()
    for feature in payload["features"]:
        polygons = PolygonSet(coords_to_polygons(feature["geometry"]))
        inside = [station["point"] for station in stations if polygons.contains(station["point"])]
        if len(inside) < SERVED_MIN_STOPS:
            continue
        if min(dist(point, rail) for point in inside for rail in rail_points) <= SERVED_MAX_RAIL_DISTANCE:
            names.add(feature["properties"]["name"])
    return names


def extract_communes(data_dir: Path, city: dict, stations: Sequence[dict]) -> Tuple[List[dict], MultiPolygon]:
    payload = load_json(data_dir / "communes.geojson")
    # Some metropolises are far larger than their urban network (Aix-Marseille-Provence): keep only the listed
    # communes, or the ones actually served.
    wanted = served_communes(payload, stations) if city.get("communes") == "served" else set(city.get("communes", []))
    communes = []
    all_polygons: MultiPolygon = []
    for feature in sorted(payload["features"], key=lambda f: f["properties"]["name"]):
        if wanted and feature["properties"]["name"] not in wanted:
            continue
        polygons = coords_to_polygons(feature["geometry"])
        if not polygons:
            continue
        largest = max((polygon[0] for polygon in polygons), key=lambda ring: abs(ring_area(ring)))
        communes.append(
            {
                "name": feature["properties"]["name"],
                "polygons": [serialize_polygon(polygon) for polygon in polygons],
                "outline": [[round_point(point) for point in polygon[0]] for polygon in polygons],
                "label": round_point(polygon_centroid(largest)),
            }
        )
        all_polygons.extend(polygons)
    return communes, all_polygons


def way_points(geometry: Sequence[dict]) -> List[Point]:
    return [lonlat_to_xy(node["lon"], node["lat"]) for node in geometry if node]


def assemble_rings(ways: List[List[Point]]) -> List[Ring]:
    """Join open ways end to end into closed rings (OSM multipolygon members)."""
    rings: List[Ring] = []
    pending = [list(way) for way in ways if len(way) >= 2]
    while pending:
        ring = pending.pop()
        while ring[0] != ring[-1]:
            for i, way in enumerate(pending):
                if way[0] == ring[-1]:
                    ring.extend(way[1:])
                elif way[-1] == ring[-1]:
                    ring.extend(reversed(way[:-1]))
                elif way[-1] == ring[0]:
                    ring[:0] = way[:-1]
                elif way[0] == ring[0]:
                    ring[:0] = list(reversed(way[1:]))
                else:
                    continue
                pending.pop(i)
                break
            else:
                break  # cut by the query bounding box: drop it
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings


def osm_polygons(element: dict) -> MultiPolygon:
    if element["type"] == "way":
        points = way_points(element.get("geometry") or [])
        return [[points]] if len(points) >= 4 and points[0] == points[-1] else []
    members = [m for m in element.get("members", []) if m["type"] == "way" and m.get("geometry")]
    outers = assemble_rings([way_points(m["geometry"]) for m in members if m.get("role") != "inner"])
    inners = assemble_rings([way_points(m["geometry"]) for m in members if m.get("role") == "inner"])
    polygons: MultiPolygon = []
    for outer in outers:
        holes = [inner for inner in inners if point_in_ring(inner[0], outer)]
        polygons.append([outer, *holes])
    return polygons


def extract_water_and_parks(data_dir: Path, bounds) -> Tuple[MultiPolygon, MultiPolygon, MultiPolygon, MultiPolygon]:
    """Return (water that is not land, other water shown on the map, parks, lakes that block walking)."""
    payload = load_json(data_dir / "osm_water_parks.json")
    min_x, min_y, max_x, max_y = bounds
    masked: MultiPolygon = []
    water: MultiPolygon = []
    parks: MultiPolygon = []
    lakes: MultiPolygon = []
    for element in payload["elements"]:
        tags = element.get("tags", {})
        for polygon in osm_polygons(element):
            ring_min_x, ring_min_y, ring_max_x, ring_max_y = ring_bounds(polygon[0])
            if ring_max_x < min_x or ring_min_x > max_x or ring_max_y < min_y or ring_min_y > max_y:
                continue
            area = abs(ring_area(polygon[0]))
            if tags.get("natural") == "water":
                if area < MIN_WATER_AREA:
                    continue
                tolerance = MIN_RING_DISTANCE if area > 1e6 else 12.0
                simplified = [simplify_ring(ring, tolerance) for ring in polygon]
                target = masked if tags.get("water") == "lagoon" or area >= WATER_MASK_AREA else water
                target.append(simplified)
                # Rivers have bridges every few hundred metres in town: only lakes stop a walk.
                if tags.get("water") in ("lake", "reservoir", "lagoon") and area >= LAKE_MIN_AREA:
                    lakes.append(simplified)
            elif tags.get("leisure") == "park" and area >= MIN_PARK_AREA:
                parks.append([simplify_ring(ring, 15.0) for ring in polygon])
    return masked, water, parks, lakes


def extract_context(data_dir: Path, city: dict) -> MultiPolygon:
    """Land around a coastal metropolis (neighbouring communes, foreign territories such as Monaco), within the OSM
    area of the city: the map draws it as land so that what remains uncovered reads as sea."""
    south, west, north, east = city["osmBbox"]
    min_x, min_y = lonlat_to_xy(west, south)
    max_x, max_y = lonlat_to_xy(east, north)
    polygons: MultiPolygon = []
    if (data_dir / "context.geojson").exists():
        for feature in load_json(data_dir / "context.geojson")["features"]:
            polygons += coords_to_polygons(feature["geometry"], CONTEXT_RING_DISTANCE)
    if (data_dir / "context_osm.json").exists():
        for element in load_json(data_dir / "context_osm.json")["elements"]:
            polygons += [[simplify_ring(ring, CONTEXT_RING_DISTANCE) for ring in polygon] for polygon in osm_polygons(element)]
    kept = []
    for polygon in polygons:
        ring_min_x, ring_min_y, ring_max_x, ring_max_y = ring_bounds(polygon[0])
        if ring_max_x >= min_x and ring_min_x <= max_x and ring_max_y >= min_y and ring_min_y <= max_y:
            kept.append(polygon)
    return kept


# --- GTFS -------------------------------------------------------------------


def read_gtfs_table(archive: zipfile.ZipFile, name: str) -> Iterable[dict]:
    if name not in archive.namelist():
        return
    with archive.open(name) as handle:
        yield from csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig"))


def parse_time(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.strip().split(":"))
    return hours * 3600 + minutes * 60 + seconds


def parse_date(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def services_by_date(calendar: Sequence[dict], calendar_dates: Sequence[dict]) -> Dict[date, frozenset]:
    """Active services for every day of the feed (calendar.txt rules + calendar_dates.txt exceptions)."""
    active: Dict[date, set] = defaultdict(set)
    for row in calendar:
        day, end = parse_date(row["start_date"]), parse_date(row["end_date"])
        while day <= end:
            if row[WEEKDAYS[day.weekday()]] == "1":
                active[day].add(row["service_id"])
            day += timedelta(days=1)
    for row in calendar_dates:
        day = parse_date(row["date"])
        if row["exception_type"] == "1":
            active[day].add(row["service_id"])
        else:
            active[day].discard(row["service_id"])
    return {day: frozenset(services) for day, services in active.items()}


def pick_reference_date(services: Dict[date, frozenset], trips_per_service: Counter, not_before: date | None = None) -> date:
    """A plain school-term Tuesday or Thursday: the most common set of services among those days.

    Picking the busiest day instead would favour holidays with works and substitution buses. Days
    with a thinner timetable are left out: holidays, and feeds that run months ahead with only a few lines filled in.
    """
    weekdays = sorted(day for day, active in services.items() if day.weekday() in (1, 3) and active)
    # A config date wins over today: archived timetables lie in the past.
    start = not_before or date.today()
    upcoming = [day for day in weekdays if day >= start] or weekdays
    # Stay close to today when the feed allows it: a date months ahead reads oddly on the page.
    soon = [day for day in upcoming if day <= upcoming[0] + timedelta(days=REFERENCE_HORIZON_DAYS)]
    upcoming = soon if len(soon) >= 4 else upcoming
    volume = {day: sum(trips_per_service[service] for service in services[day]) for day in upcoming}
    busiest = max(volume.values())
    # School holidays typically run 10–20 % fewer trips: only near-busiest days are plain term days.
    candidates = [day for day in upcoming if volume[day] >= 0.92 * busiest]
    signatures = Counter(services[day] for day in candidates)
    typical = signatures.most_common(1)[0][0]
    return next(day for day in candidates if services[day] == typical)


SMALL_WORDS = {"de", "du", "des", "la", "le", "les", "et", "en", "sur", "sous", "aux", "au", "à"}
ACRONYMS = {"TGV", "SNCF", "CHU", "CHR", "IUT", "ZI", "ZA", "ZAC", "RN", "RD", "TER", "UFR", "INSA", "EDF", "CPAM", "IME", "MEETT"}


def display_name(name: str) -> str:
    """Some feeds write stop names in capitals (« LYCEE S. WEIL »): turn them into title case for display."""
    letters = [c for c in name if c.isalpha()]
    if len(letters) < 4 or not all(c.isupper() for c in letters):
        return name
    words = []
    for i, word in enumerate(name.lower().split(" ")):
        if word.upper().strip(".,") in ACRONYMS:
            words.append(word.upper())
        elif i and word in SMALL_WORDS:
            words.append(word)
        elif word[:2] in ("d'", "l'") and len(word) > 2:
            words.append(word[:2] + word[2:3].upper() + word[3:])
        else:
            words.append("-".join(part[:1].upper() + part[1:] for part in word.split("-")))
    return " ".join(words)


def strip_city_prefix(name: str, city: dict) -> str:
    """Swiss stop names start with their municipality (« Zürich, Bellevue »): drop it inside the main city."""
    prefix = f"{city.get('stopPrefix', '')}, "
    return name[len(prefix):] if prefix != ", " and name.startswith(prefix) else name


def normalize_name(name: str) -> str:
    return " ".join(name.lower().replace("-", " ").replace("’", "'").split())


def group_stops(stops: Dict[str, dict], used_stop_ids: set, city: dict) -> Tuple[List[dict], Dict[str, int]]:
    """Merge stops sharing a name and lying close together into one complex."""
    by_name: Dict[str, List[str]] = defaultdict(list)
    for stop_id in used_stop_ids:
        by_name[normalize_name(stops[stop_id]["stop_name"])].append(stop_id)

    complexes: List[dict] = []
    complex_of: Dict[str, int] = {}
    for _, stop_ids in sorted(by_name.items()):
        points = {stop_id: lonlat_to_xy(float(stops[stop_id]["stop_lon"]), float(stops[stop_id]["stop_lat"])) for stop_id in stop_ids}
        clusters: List[List[str]] = []
        for stop_id in sorted(stop_ids):
            merged = None
            for cluster in clusters:
                if any(dist(points[stop_id], points[other]) <= STOP_GROUP_RADIUS for other in cluster):
                    if merged is None:
                        cluster.append(stop_id)
                        merged = cluster
                    else:
                        merged.extend(cluster)
                        cluster.clear()
            clusters = [cluster for cluster in clusters if cluster]
            if merged is None:
                clusters.append([stop_id])
        for cluster in clusters:
            xs = [points[stop_id][0] for stop_id in cluster]
            ys = [points[stop_id][1] for stop_id in cluster]
            index = len(complexes)
            names = Counter(stops[stop_id]["stop_name"] for stop_id in cluster)
            complexes.append(
                {
                    "id": min(cluster),
                    "name": strip_city_prefix(display_name(names.most_common(1)[0][0]), city),
                    "point": (sum(xs) / len(xs), sum(ys) / len(ys)),
                    "routes": set(),
                }
            )
            for stop_id in cluster:
                complex_of[stop_id] = index
    return complexes, complex_of


def read_stop_times(archive: zipfile.ZipFile, trips: Dict[str, dict]) -> Dict[str, List[Tuple[int, str, int, int]]]:
    """Stream stop_times.txt (hundreds of MB for big networks), keeping only the reference day's trips."""
    stop_times: Dict[str, List[Tuple[int, str, int, int]]] = defaultdict(list)
    with archive.open("stop_times.txt") as handle:
        reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8-sig"))
        header = next(reader)
        trip_col, seq_col, stop_col = header.index("trip_id"), header.index("stop_sequence"), header.index("stop_id")
        arr_col, dep_col = header.index("arrival_time"), header.index("departure_time")
        for row in reader:
            trip_id = row[trip_col]
            if trip_id not in trips or not row[arr_col]:
                continue
            stop_times[trip_id].append((int(row[seq_col]), row[stop_col], parse_time(row[arr_col]), parse_time(row[dep_col])))
    return stop_times


def route_excluded(row: dict, city: dict) -> bool:
    """School buses (extended types 712/713) are not open to the public; some cities exclude special lines.
    Regional aggregated feeds (Caen in the Normandy one) are narrowed to the city's operator."""
    agencies = city.get("agencies")
    return (
        row.get("route_type") in ("712", "713")
        or row.get("route_desc") in city.get("excludeRouteCategories", [])
        or row.get("route_type") in city.get("excludeRouteTypes", [])
        or row.get("route_short_name") in city.get("excludeRouteNames", [])
        or row["route_id"] in city.get("excludeRoutes", [])
        or bool(agencies and row.get("agency_id") not in agencies)
    )


def line_key(row: dict) -> str:
    name = row.get("route_short_name") or row.get("route_long_name") or row["route_id"]
    return f"{row.get('route_desc') or row.get('route_type')}:{name}"


def extract_network(data_dir: Path, city: dict):
    # Some feeds mislabel their lines (Reims declares its tram as a metro); configs fix them by short name.
    mode_overrides = city.get("routeModes", {})
    with zipfile.ZipFile(data_dir / GTFS_FILE) as archive:
        raw_routes = {row["route_id"]: row for row in read_gtfs_table(archive, "routes.txt")}
        excluded = {route_id for route_id, row in raw_routes.items() if route_excluded(row, city)}
        # The Swiss feed has one route_id per operator and timetable variant (S10 runs as several): a line is
        # its category and name, so that headways count every vehicle of the line.
        line_of = {route_id: line_key(row) for route_id, row in raw_routes.items()}
        routes = {line_of[route_id]: row for route_id, row in sorted(raw_routes.items())}
        stops = {row["stop_id"]: row for row in read_gtfs_table(archive, "stops.txt")}
        services = services_by_date(list(read_gtfs_table(archive, "calendar.txt")), list(read_gtfs_table(archive, "calendar_dates.txt")))
        # Demand-responsive trips (TaM flags them in a "TAD" column) cannot be modelled with fixed times.
        all_trips = [
            row
            for row in read_gtfs_table(archive, "trips.txt")
            if not (row.get("TAD") or "").strip() and row["route_id"] not in excluded
        ]
        # Configs can skip school holidays that the trip volume does not reveal (Zurich keeps its timetable).
        not_before = date.fromisoformat(city["referenceNotBefore"]) if city.get("referenceNotBefore") else None
        reference_date = pick_reference_date(services, Counter(row["service_id"] for row in all_trips), not_before)
        active_services = services[reference_date]
        trips = {row["trip_id"]: {**row, "route_id": line_of[row["route_id"]]} for row in all_trips if row["service_id"] in active_services}
        stop_times = read_stop_times(archive, trips)

    used_stop_ids = {stop_id for sequence in stop_times.values() for _, stop_id, _, _ in sequence}
    complexes, complex_of = group_stops(stops, used_stop_ids, city)

    ride_samples: Dict[Tuple[int, int, str], List[float]] = defaultdict(list)
    departures: Dict[Tuple[int, str], Counter] = defaultdict(Counter)
    # Departures towards each next stop, all lines of a mode group together (see waits below).
    toward: Counter = Counter()
    group_of = {route_id: WAIT_GROUPS.get(route_mode(row.get("route_type", "3")), "bus") for route_id, row in routes.items()}
    window_start, window_end = SERVICE_WINDOW
    for trip_id, sequence in stop_times.items():
        trip = trips[trip_id]
        route_id = trip["route_id"]
        sequence.sort()
        for (_, stop_a, _, dep_a), (_, stop_b, arr_b, _) in zip(sequence, sequence[1:]):
            a, b = complex_of[stop_a], complex_of[stop_b]
            complexes[a]["routes"].add(route_id)
            complexes[b]["routes"].add(route_id)
            if a == b or not window_start <= dep_a < window_end:
                continue
            ride_samples[(a, b, route_id)].append(max(0, arr_b - dep_a) / 60.0)
            departures[(a, route_id)][trip.get("direction_id") or "0"] += 1
            toward[(a, b, group_of[route_id])] += 1

    edges = {key: max(MIN_RIDE_MINUTES, statistics.median(samples)) for key, samples in ride_samples.items()}
    window_minutes = (window_end - window_start) / 60.0
    # Common lines: where several lines run to the same next stop (S-Bahn trunk HB → Oerlikon, tram trunks), a
    # rider takes the first one, so the wait is half the headway of all of them. Lines that diverge further on make
    # this slightly optimistic; counting each line alone would make it 15 min on a corridor served every 5 min.
    next_stops: Dict[Tuple[int, str], set] = defaultdict(set)
    for a, b, route_id in ride_samples:
        next_stops[(a, route_id)].add(b)
    waits: Dict[Tuple[int, str], float] = {}
    own_waits: Dict[Tuple[int, str], float] = {}  # the line alone, for the page's table
    for key, per_direction in departures.items():
        mean_departures = sum(per_direction.values()) / len(per_direction)
        a, route_id = key
        pooled = max((toward[(a, b, group_of[route_id])] for b in next_stops[key]), default=0)
        headway = window_minutes / max(mean_departures, pooled)
        waits[key] = round(min(MAX_WAIT, max(MIN_WAIT, headway / 2.0)), 2)
        own_waits[key] = round(min(MAX_WAIT, max(MIN_WAIT, window_minutes / mean_departures / 2.0)), 2)

    served = {route_id for station in complexes for route_id in station["routes"]}
    route_info = {}
    for route_id in sorted(served):  # sorted: identical output from one build to the next
        row = routes[route_id]
        mode = mode_overrides.get(row.get("route_short_name", ""), route_mode(row.get("route_type", "3")))
        route_info[route_id] = {
            "mode": mode,
            "rail": mode in RAIL_MODES,
            "color": f"#{row['route_color'].strip().lstrip('#')}" if (row.get("route_color") or "").strip() else MODE_COLORS.get(mode, "#888888"),
            "name": row.get("route_short_name") or row.get("route_long_name") or route_id,
        }
    rail_shape_ids = {
        trip["shape_id"] for trip in trips.values() if route_info.get(trip["route_id"], {}).get("rail") and trip.get("shape_id")
    }
    shape_routes = {trip["shape_id"]: trip["route_id"] for trip in trips.values() if trip.get("shape_id") in rail_shape_ids}
    return reference_date, complexes, edges, waits, own_waits, route_info, shape_routes


def build_graph(complexes: Sequence[dict], edges, waits, route_info: Dict[str, dict], access_minutes: Dict[str, float], terrain: "Terrain"):
    route_states: List[dict] = []
    station_states: List[List[int]] = [[] for _ in complexes]
    lookup: Dict[Tuple[int, str], int] = {}
    for station_index, station in enumerate(complexes):
        for route_id in sorted(station["routes"]):
            state_index = len(route_states)
            route_states.append(
                {
                    "stationIndex": station_index,
                    "routeId": route_id,
                    "wait": waits.get((station_index, route_id), DEFAULT_BOARD_WAIT),
                    "access": access_minutes.get(route_info[route_id]["mode"], 0.0),
                }
            )
            station_states[station_index].append(state_index)
            lookup[(station_index, route_id)] = state_index

    adjacency: List[List[List[float]]] = [[] for _ in route_states]

    def add_edge(src: int, dst: int, weight: float) -> None:
        adjacency[src].append([dst, round(weight, 2)])

    # Ride edges are directed: one-way loops and branches stay correct.
    for (a, b, route_id), minutes in edges.items():
        add_edge(lookup[(a, route_id)], lookup[(b, route_id)], minutes)

    def transfer(src: int, dst: int, walk: float) -> float:
        # Leaving one platform and reaching the other: half of each access time, plus the wait.
        access = (route_states[src]["access"] + route_states[dst]["access"]) / 2.0
        return walk + access + route_states[dst]["wait"]

    # Changing line inside a stop: short walk plus waiting for the next vehicle.
    for states in station_states:
        for src in states:
            for dst in states:
                if src != dst:
                    add_edge(src, dst, transfer(src, dst, TRANSFER_WALK))

    # Walking to a nearby stop with another name.
    points = [station["point"] for station in complexes]
    index = StationIndex(points, range(len(points)))
    for i, a in enumerate(complexes):
        for j in index.within(a["point"], INTER_COMPLEX_WALK_RADIUS):
            if i == j:
                continue
            b = complexes[j]
            walk = terrain.walk(a["point"], a["z"], b["point"], b["z"]) + TRANSFER_WALK
            if not math.isfinite(walk):
                continue
            for src in station_states[i]:
                for dst in station_states[j]:
                    if route_states[src]["routeId"] != route_states[dst]["routeId"]:
                        add_edge(src, dst, transfer(src, dst, walk))
    return route_states, station_states, adjacency


# --- Rail geometry ----------------------------------------------------------


def rail_routes_from_gtfs(data_dir: Path, shape_routes: Dict[str, str], route_info: Dict[str, dict]) -> List[dict]:
    points: Dict[str, List[Tuple[int, Point]]] = defaultdict(list)
    with zipfile.ZipFile(data_dir / GTFS_FILE) as archive:
        for row in read_gtfs_table(archive, "shapes.txt"):
            if row["shape_id"] in shape_routes:
                points[row["shape_id"]].append(
                    (int(row["shape_pt_sequence"]), lonlat_to_xy(float(row["shape_pt_lon"]), float(row["shape_pt_lat"])))
                )
    shapes, seen = [], set()
    for shape_id, sequence in sorted(points.items()):
        line = simplify_polyline([point for _, point in sorted(sequence)], MIN_LINE_DISTANCE)
        key = (shape_routes[shape_id], tuple(sorted({tuple(round_point(p)) for p in line[:: max(1, len(line) // 20)]})))
        if len(line) < 2 or key in seen:
            continue
        seen.add(key)
        route_id = shape_routes[shape_id]
        shapes.append({"id": route_id, "color": route_info[route_id]["color"], "points": [round_point(p) for p in line]})
    return shapes


def rail_routes_from_osm(data_dir: Path, city: dict, route_info: Dict[str, dict], bounds) -> List[dict]:
    """Line geometry from OSM route relations, and their `colour` tag when the GTFS has no colours (Swiss feed).
    S-Bahn relations run far beyond the map: only the ways inside `bounds` are kept."""
    payload = load_json(data_dir / "osm_rail.json")
    by_name = {info["name"]: route_id for route_id, info in route_info.items() if info["rail"]}
    aliases = city.get("osmRefAliases", {})
    min_x, min_y, max_x, max_y = bounds
    colours: Dict[str, Counter] = defaultdict(Counter)
    for relation in payload["elements"]:
        tags = relation.get("tags", {})
        route_id = by_name.get(aliases.get(tags.get("ref", ""), tags.get("ref", "")))
        colour = tags.get("colour", "")
        if route_id and colour.startswith("#") and len(colour) == 7 and colour.upper() != "#FFFFFF":
            # Count the ways inside the map: a namesake line elsewhere in Switzerland (S24) weighs little.
            inside = sum(1 for m in relation.get("members", []) for p in m.get("geometry", [])[:1]
                         if p and min_x <= lonlat_to_xy(p["lon"], p["lat"])[0] <= max_x and min_y <= lonlat_to_xy(p["lon"], p["lat"])[1] <= max_y)
            colours[route_id][colour.upper()] += inside
    for route_id, counter in colours.items():
        colour, weight = counter.most_common(1)[0]
        if weight:
            route_info[route_id]["color"] = colour
    seen: Dict[str, set] = defaultdict(set)
    shapes = []
    for relation in sorted(payload["elements"], key=lambda item: item["id"]):
        ref = relation.get("tags", {}).get("ref", "")
        route_id = by_name.get(aliases.get(ref, ref))
        if not route_id:
            continue
        for member in relation.get("members", []):
            if member["type"] != "way" or member.get("role") not in ("", None) or member["ref"] in seen[route_id]:
                continue
            seen[route_id].add(member["ref"])
            runs: List[List[Point]] = [[]]
            for x, y in way_points(member.get("geometry", [])):
                if min_x <= x <= max_x and min_y <= y <= max_y:
                    runs[-1].append((x, y))
                elif runs[-1]:
                    runs.append([])
            for points in runs:
                if len(points) >= 2:
                    shapes.append(
                        {
                            "id": route_id,
                            "color": route_info[route_id]["color"],
                            "points": [round_point(p) for p in simplify_polyline(points, MIN_LINE_DISTANCE)],
                        }
                    )
    return shapes


# --- Population and jobs ------------------------------------------------------

# Hectare files of the Federal Statistical Office: (zip, csv inside, column with the count).
HECTARE_SOURCES = {
    "pop": ("statpop.zip", "STATPOP", "BBTOT"),  # permanent residents
    "jobs": ("statent.zip", "STATENT_", "B08EMPT"),  # employees (jobs), all sectors
}


def lv95_to_wgs84(east: float, north: float) -> Tuple[float, float]:
    """swisstopo's approximate formulas (about 1 m accurate): LV95 → (lat, lon)."""
    y = (east - 2_600_000) / 1e6
    x = (north - 1_200_000) / 1e6
    lon = 2.6779094 + 4.728982 * y + 0.791484 * y * x + 0.1306 * y * x * x - 0.0436 * y ** 3
    lat = 16.9023892 + 3.238272 * x - 0.270978 * y * y - 0.002528 * x * x - 0.0447 * y * y * x - 0.0140 * x ** 3
    return lat * 100 / 36, lon * 100 / 36


def hectare_counts(data_dir: Path, bounds, cols: int, rows: int, mask: Sequence[int]) -> Dict[str, Dict[int, int]]:
    """Residents and jobs per map cell: each hectare (100 m, LV95 south-west corner) goes to the cell of its centre.
    Hectares on water or outside the map are left out, so totals are those of the map."""
    min_x, min_y, max_x, max_y = bounds
    cell_w, cell_h = (max_x - min_x) / cols, (max_y - min_y) / rows
    counts: Dict[str, Dict[int, int]] = {}
    for key, (zip_name, prefix, column) in HECTARE_SOURCES.items():
        path = data_dir / zip_name
        per_cell: Dict[int, int] = defaultdict(int)
        if not path.exists():
            print(f"  {zip_name} missing: no {key} figures (python3 fetch_data.py <city> --context-only)")
            counts[key] = per_cell
            continue
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist() if n.startswith(prefix) and n.endswith(".csv") and "_GMDE" not in n and "_NOLOC" not in n)
            with archive.open(name) as handle:
                reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8-sig"), delimiter=";")
                header = next(reader)
                e_col, n_col, v_col = header.index("E_KOORD"), header.index("N_KOORD"), header.index(column)
                for row in reader:
                    lat, lon = lv95_to_wgs84(float(row[e_col]) + 50, float(row[n_col]) + 50)
                    x, y = lonlat_to_xy(lon, lat)
                    if not (min_x <= x < max_x and min_y <= y < max_y):
                        continue
                    index = mask[int((y - min_y) // cell_h) * cols + int((x - min_x) // cell_w)]
                    if index >= 0 and row[v_col]:
                        per_cell[index] += round(float(row[v_col]))
        counts[key] = per_cell
    return counts


# --- Grid -------------------------------------------------------------------


def build_grid(land: MultiPolygon, masked_water: MultiPolygon, stations: Sequence[dict], bounds, cols: int, rows: int, terrain: "Terrain"):
    """Land cells and, for each, the nearby stops with the walk from the stop to the cell (minutes, hills included;
    stops across a lake are left out)."""
    min_x, min_y, max_x, max_y = bounds
    cell_w = (max_x - min_x) / cols
    cell_h = (max_y - min_y) / rows
    land_set, water_set = PolygonSet(land), PolygonSet(masked_water)
    points = [station["point"] for station in stations]
    all_index = StationIndex(points, range(len(points)))
    rail_index = StationIndex(points, [i for i, station in enumerate(stations) if station["rail"]])
    cells = []
    mask = [-1] * (cols * rows)
    for row in range(rows):
        for col in range(cols):
            point = (min_x + (col + 0.5) * cell_w, min_y + (row + 0.5) * cell_h)
            if not land_set.contains(point) or water_set.contains(point):
                continue
            z = terrain.z(point)

            def egress(candidates, keep):
                found = []
                for _, index in candidates:
                    station = stations[index]
                    minutes = terrain.walk(station["point"], station["z"], point, z)
                    if math.isfinite(minutes):
                        found.append((index, minutes))
                return found[:keep]

            nearest = dict(egress(all_index.nearest(point, CELL_CANDIDATE_STATIONS), CELL_NEAREST_STATIONS))
            nearest.update(egress(rail_index.nearest(point, CELL_CANDIDATE_STATIONS), CELL_NEAREST_RAIL_STATIONS))
            mask[row * cols + col] = len(cells)
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "point": round_point(point),
                    "z": round(z),
                    "access": [[index, round(minutes, 2)] for index, minutes in sorted(nearest.items(), key=lambda item: item[1])],
                }
            )
    return cells, mask


def network_stats(city: dict, route_info, stations, route_states, station_states, adjacency, on_map, own_waits, cells, terrain) -> dict:
    """Figures shown on the page (and its FAQ): lines, headways, share of rail stations within 30 min of the centre."""
    rail_states = [i for i, state in enumerate(route_states) if route_info[state["routeId"]]["rail"]]
    lines = []
    mode_order = {"metro": 0, "tram": 1, "sbahn": 2, "funicular": 3, "cable": 4}
    for route_id, info in sorted(route_info.items(), key=lambda item: (mode_order.get(item[1]["mode"], 9), len(item[1]["name"]), item[1]["name"])):
        if info["mode"] not in TABLE_MODES:
            continue
        waits = sorted(own_waits.get((route_states[i]["stationIndex"], route_id), MAX_WAIT)
                       for i in rail_states if route_states[i]["routeId"] == route_id)
        # Diversions add a few sparsely served stops to a line: the headway is read on its regular section.
        regular = [wait for wait in waits if wait < MAX_WAIT] or waits
        lines.append(
            {
                "name": info["name"],
                "mode": info["mode"],
                "color": info["color"],
                "stations": len(waits),
                "headway": round(statistics.median(regular) * 2, 1),
            }
        )

    # Same model as the browser: walk to the nearest rail stations, then rail only.
    origin = lonlat_to_xy(city["defaultFrom"]["lon"], city["defaultFrom"]["lat"])
    rail_station_ids = [i for i, station in enumerate(stations) if station["rail"]]
    # Figures cover the stations on the map; the clipped feed reaches a little beyond it.
    shown_ids = [i for i in rail_station_ids if on_map(stations[i]["point"])]
    origin_z = terrain.z(origin)
    seeds = sorted(rail_station_ids, key=lambda i: dist(origin, stations[i]["point"]))[:ORIGIN_NEAREST_STATIONS]
    best = [math.inf] * len(route_states)
    heap: List[Tuple[float, int]] = []
    for station_index in seeds:
        walk = terrain.walk(origin, origin_z, stations[station_index]["point"], stations[station_index]["z"])
        for state in station_states[station_index]:
            if route_info[route_states[state]["routeId"]]["rail"]:
                time = walk + route_states[state]["access"] + route_states[state]["wait"]
                if time < best[state]:
                    best[state] = time
                    heapq.heappush(heap, (time, state))
    while heap:
        time, state = heapq.heappop(heap)
        if time > best[state]:
            continue
        for target, weight in adjacency[state]:
            target = int(target)
            if route_info[route_states[target]["routeId"]]["rail"] and time + weight < best[target]:
                best[target] = time + weight
                heapq.heappush(heap, (time + weight, target))
    arrival = {}
    for state, time in enumerate(best):
        index = route_states[state]["stationIndex"]
        out = time + route_states[state]["access"]
        walk = terrain.walk(origin, origin_z, stations[index]["point"], stations[index]["z"])
        arrival[index] = min(arrival.get(index, math.inf), out, walk)
    rail_times = [arrival.get(i, math.inf) for i in shown_ids]
    # Residents and jobs reachable from the centre (rail only, as on the page's default map).
    reach = {"pop": Counter(), "jobs": Counter()}
    for cell in cells:
        time = terrain.walk(origin, origin_z, cell["point"], cell["z"])
        for index, minutes in cell["access"]:
            time = min(time, arrival.get(index, math.inf) + minutes)
        for key in reach:
            reach[key]["total"] += cell.get(key, 0)
            for limit in (15, 30, 45):
                if time <= limit:
                    reach[key][str(limit)] += cell.get(key, 0)
    reachable = [i for i in shown_ids if math.isfinite(arrival.get(i, math.inf))]
    farthest = max(reachable, key=lambda i: arrival[i])
    og_station = min(reachable, key=lambda i: abs(arrival[i] - OG_TRIP_MINUTES))
    meters_per_deg_lat = 111_320.0
    og_x, og_y = stations[og_station]["point"]
    return {
        "lines": lines,
        "railStations": len(shown_ids),
        "busLines": sum(1 for info in route_info.values() if not info["rail"]),
        "center": city["defaultFrom"]["label"],
        "within15": round(100 * sum(t <= 15 for t in rail_times) / len(rail_times)),
        "within30": round(100 * sum(t <= 30 for t in rail_times) / len(rail_times)),
        "population": dict(reach["pop"]),
        "jobs": dict(reach["jobs"]),
        "farthestStation": stations[farthest]["name"],
        "farthestMinutes": round(arrival[farthest]),
        "ogTrip": {
            "name": stations[og_station]["name"],
            "lat": round(og_y / meters_per_deg_lat, 5),
            "lon": round(og_x / (meters_per_deg_lat * math.cos(math.radians(LAT0))), 5),
        },
    }


def write_provenance(city: dict, data_dir: Path, reference_date: date, route_info: Dict[str, dict], stations: Sequence[dict], stats: dict) -> Path:
    """Record in sources/<city>.json (versioned) which raw files were used, when they were fetched and what they cover."""
    manifest_path = data_dir / "manifest.json"
    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    with zipfile.ZipFile(data_dir / GTFS_FILE) as archive:
        feed_info = next(iter(read_gtfs_table(archive, "feed_info.txt")), None)
        services = services_by_date(list(read_gtfs_table(archive, "calendar.txt")), list(read_gtfs_table(archive, "calendar_dates.txt")))
    days = sorted(day for day, active in services.items() if active)
    window_start, window_end = SERVICE_WINDOW
    provenance = {
        "city": city["name"],
        "builtAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "referenceDate": reference_date.isoformat(),
        "serviceWindow": f"{window_start // 3600}h–{window_end // 3600}h",
        "gtfs": {
            "network": city["network"],
            "dataset": city["gtfsDataset"],
            **manifest.get(GTFS_FILE.replace("gtfs_", "gtfs_ch_"), {}),
            "timetableYear": city["year"],
            "note": city["timetables"][city["year"]].get("note"),
            "feedInfo": feed_info,
            "servicePeriod": [days[0].isoformat(), days[-1].isoformat()] if days else None,
        },
        "boundaries": {"municipalities": city["municipalities"], **manifest.get("communes.geojson", {})},
        "openStreetMap": {
            "licence": "ODbL, © OpenStreetMap contributors",
            **{name.removesuffix(".json"): manifest[name] for name in ("osm_rail.json", "osm_water_parks.json") if name in manifest},
        },
        "railGeometry": "OpenStreetMap" if city.get("railGeometry") == "osm" else "GTFS shapes.txt",
        "excludedRoutes": city.get("excludeRoutes", []),
        "network": {
            "lines": dict(Counter(info["mode"] for info in route_info.values())),
            "stops": len(stations),
            "railStations": sum(1 for station in stations if station["rail"]),
        },
        "stats": stats,
    }
    path = ROOT / "sources" / f"{city['slug']}-{city['year']}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    global LAT0, GTFS_FILE
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    city = load_city(sys.argv[1])
    year = sys.argv[2] if len(sys.argv) > 2 else city["defaultTimetable"]
    timetable = city["timetables"][year]
    city.update(year=year, gtfsDataset=timetable["dataset"], referenceNotBefore=timetable.get("referenceNotBefore"))
    GTFS_FILE = f"gtfs_{year}.zip"
    LAT0 = city["lat0"]
    data_dir = ROOT / "data" / city["slug"]
    output_path = ROOT / "site" / "data" / f"{city['slug']}-{year}.json"

    reference_date, complexes, edges, waits, own_waits, route_info, shape_routes = extract_network(data_dir, city)
    for station in complexes:
        station["rail"] = any(route_info[route_id]["rail"] for route_id in station["routes"])
    communes, land = extract_communes(data_dir, city, complexes)
    bounds = multipolygon_bounds(land, LAND_PAD_METERS)
    cols = round((bounds[2] - bounds[0]) / GRID_CELL_METERS)
    rows = round((bounds[3] - bounds[1]) / GRID_CELL_METERS)
    masked_water, water, parks, lakes = extract_water_and_parks(data_dir, bounds)
    terrain = Terrain(data_dir, bounds, lakes)
    for station in complexes:
        station["z"] = terrain.z(station["point"])
    context = extract_context(data_dir, city)

    access_minutes = {**MODE_ACCESS_MINUTES, **city.get("modeAccess", {})}
    route_states, station_states, adjacency = build_graph(complexes, edges, waits, route_info, access_minutes, terrain)
    if city.get("railGeometry") == "osm":
        routes = rail_routes_from_osm(data_dir, city, route_info, bounds)
    else:
        routes = rail_routes_from_gtfs(data_dir, shape_routes, route_info)

    stations = [
        {
            "id": station["id"],
            "name": station["name"],
            "point": station["point"],
            "routes": sorted(station["routes"], key=lambda r: (len(route_info[r]["name"]), route_info[r]["name"])),
            "rail": any(route_info[route_id]["rail"] for route_id in station["routes"]),
            "z": round(station["z"], 1),
        }
        for station in complexes
    ]
    # Fit the view to the stations on the map (the clipped feed reaches a little beyond it).
    land_set, water_set = PolygonSet(land), PolygonSet(masked_water)
    on_map = lambda point: land_set.contains(point) and not water_set.contains(point)  # noqa: E731
    rail_points = [station["point"] for station in stations if station["rail"] and on_map(station["point"])]
    view_bounds = (
        min(x for x, _ in rail_points) - VIEW_PAD_METERS,
        min(y for _, y in rail_points) - VIEW_PAD_METERS,
        max(x for x, _ in rail_points) + VIEW_PAD_METERS,
        max(y for _, y in rail_points) + VIEW_PAD_METERS,
    )
    cells, mask = build_grid(land, masked_water, stations, bounds, cols, rows, terrain)
    counts = hectare_counts(data_dir, bounds, cols, rows, mask)
    for key, per_cell in counts.items():
        for index, value in per_cell.items():
            cells[index][key] = value

    output = {
        "meta": {
            "lat0": LAT0,
            "referenceDate": reference_date.strftime("%Y%m%d"),
            "bounds": [round(v, 1) for v in bounds],
            "viewBounds": [round(v, 1) for v in view_bounds],
            "gridCols": cols,
            "gridRows": rows,
            "walkMetersPerMinute": WALK_METERS_PER_MINUTE,
            "tobler": {"k": TOBLER_K, "offset": TOBLER_OFFSET, "minRun": SLOPE_MIN_METERS},
            "originStationCount": ORIGIN_NEAREST_STATIONS,
            "sea": bool(context),
        },
        "context": [serialize_polygon(polygon) for polygon in context],
        "boroughs": communes,
        "water": [serialize_polygon(polygon) for polygon in masked_water + water],
        "parks": [serialize_polygon(polygon) for polygon in parks],
        "routes": routes,
        "routeInfo": route_info,
        "stations": [{**station, "point": round_point(station["point"])} for station in stations],
        "routeStates": route_states,
        "stationStates": station_states,
        "adjacency": adjacency,
        "cells": cells,
        "lakes": terrain.serialize(),
        "mask": mask,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    stats = network_stats(city, route_info, stations, route_states, station_states, adjacency, on_map, own_waits, cells, terrain)
    provenance_path = write_provenance(city, data_dir, reference_date, route_info, stations, stats)
    print(f"Wrote {provenance_path.relative_to(ROOT)}")
    rail_count = sum(1 for station in stations if station["rail"])
    modes = Counter(info["mode"] for info in route_info.values())
    print(
        f"Wrote {output_path.relative_to(ROOT)} "
        f"({output_path.stat().st_size / 1_000_000:.2f} MB, GTFS du {reference_date}, lignes {dict(modes)}, "
        f"{len(stations)} arrêts dont {rail_count} tram/métro, {len(route_states)} states, "
        f"{sum(len(a) for a in adjacency)} edges, {len(cells)} cells ({cols}×{rows}), {len(routes)} tracés)"
    )


if __name__ == "__main__":
    main()
