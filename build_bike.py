#!/usr/bin/env python3
"""Build the cycling layer: bike travel times between neighbouring map cells, on the OSM street network.

Usage: python3 build_bike.py <city>   (run build_data.py <city> first: it reuses the cells of the default timetable;
       reads data/<city>/bike_graph.graphml, or osm_bike.json, and elevation.json; writes site/data/<city>-bike.json)

The street network is the one computed in the GIS_playground_ZH cycling project (zh-cycling): its cached osmnx graph
of the City of Zurich plus 6 km (data/cache/osm_bike_osm-current-v1_canton6.graphml), copied to
data/<city>/bike_graph.graphml. Without it, fetch_data.py downloads the same streets from Overpass (osm_bike.json).

Model, as in the GIS_playground_ZH cycling project: streets cyclists may use (fetch_data.BIKE_FILTERS), one-way
streets open both ways when OSM says cycling is allowed against the traffic, 20 km/h on the flat. Hills come from
the same swisstopo elevation as walking: slower uphill (about 10 km/h on 5 %, 6.7 km/h on 10 %, never below 5 km/h),
faster downhill (25 km/h on 3 %, 30 km/h from 5 %). One minute is added at each end to unlock and park the bike.

The browser cannot hold the whole street graph, so the network is folded onto the map grid: each cell is snapped to
a junction near its centre, and a short Dijkstra from there on the real network gives the time to the nodes of the
28 cells within 600 m. Shortest paths over this cell graph stay within about 5 % of a full street-network Dijkstra
keep one-way streets and slopes (each direction is computed separately), and lakes or rivers without a bridge
simply have no edge across them.
"""

from __future__ import annotations

import heapq
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import build_data as bd
from cities import load_city

ROOT = Path(__file__).resolve().parent

BIKE_KMH = 20.0
BIKE_METERS_PER_MINUTE = BIKE_KMH * 1000 / 60
UPHILL_K = 20.0  # speed / (1 + k·slope) uphill: 10 km/h on 5 %
# Downhill speed factor against the descent, interpolated linearly and flat beyond the last point.
DOWNHILL_FACTORS = [(0.0, 1.0), (0.03, 25 / 20), (0.05, 30 / 20)]
MIN_KMH = 5.0  # steeper than that: pushing
SLOPE_MIN_METERS = 20.0
PARK_MINUTES = 1.0  # unlock at the start, park at the end
SNAP_MAX_METERS = 400.0
SEARCH_LIMIT_MINUTES = 6.0
# Cells linked to every cell within this many cells (3: 600 m): longer hops pass through fewer snapped nodes, which
# keeps cell-graph times within a few percent of a full street-network Dijkstra.
NEIGHBOUR_RADIUS = 3
NEIGHBOURS = [(dr, dc) for dr in range(-NEIGHBOUR_RADIUS, NEIGHBOUR_RADIUS + 1) for dc in range(-NEIGHBOUR_RADIUS, NEIGHBOUR_RADIUS + 1)
              if (dr, dc) != (0, 0) and math.hypot(dr, dc) <= NEIGHBOUR_RADIUS + 0.01]
# Snap to a junction (3+ streets) near the cell centre rather than to the nearest node, often a dead end or a path.
SNAP_JUNCTION_METERS = 120.0


def downhill_factor(descent: float) -> float:
    for (d0, f0), (d1, f1) in zip(DOWNHILL_FACTORS, DOWNHILL_FACTORS[1:]):
        if descent <= d1:
            return f0 + (f1 - f0) * (descent - d0) / (d1 - d0)
    return DOWNHILL_FACTORS[-1][1]


def bike_factor(rise: float, run: float) -> float:
    slope = rise / max(run, SLOPE_MIN_METERS)
    if slope > 0:
        factor = 1 / (1 + UPHILL_K * slope)
    else:
        factor = downhill_factor(-slope)
    return max(factor, MIN_KMH / BIKE_KMH)


def oneway(tags: dict) -> int:
    """1: only along the way, -1: only against it, 0: both ways (for bicycles)."""
    if tags.get("oneway:bicycle") == "no" or any(tags.get(k, "").startswith("opposite") for k in ("cycleway", "cycleway:left", "cycleway:right")):
        return 0
    value = tags.get("oneway", "")
    if value in ("yes", "true", "1") or tags.get("junction") in ("roundabout", "circular"):
        return 1
    if value == "-1":
        return -1
    return 0


def load_network(path: Path, terrain: bd.Terrain):
    """Raw OSM ways from Overpass (fetch_data.py): nodes {osm id: xy} and edges {(id, id): minutes}."""
    payload = bd.load_json(path)
    coords = {e["id"]: bd.lonlat_to_xy(e["lon"], e["lat"]) for e in payload["elements"] if e["type"] == "node"}
    heights = {}
    z = lambda osm_id: heights.setdefault(osm_id, terrain.z(coords[osm_id]))  # noqa: E731
    edges: dict = {}
    for way in (e for e in payload["elements"] if e["type"] == "way"):
        refs = [ref for ref in way["nodes"] if ref in coords]
        direction = oneway(way.get("tags", {}))
        for a, b in zip(refs, refs[1:]):
            meters = bd.dist(coords[a], coords[b])
            if meters == 0:
                continue
            for u, v, keep in ((a, b, direction >= 0), (b, a, direction <= 0)):
                if keep:
                    minutes = meters / (BIKE_METERS_PER_MINUTE * bike_factor(z(v) - z(u), meters))
                    edges[(u, v)] = min(minutes, edges.get((u, v), math.inf))
    return coords, edges


def lv95_to_xy(east: float, north: float):
    lat, lon = bd.lv95_to_wgs84(east, north)
    return bd.lonlat_to_xy(lon, lat)


def z_lv95(terrain: bd.Terrain, east: float, north: float) -> float:
    lat, lon = bd.lv95_to_wgs84(east, north)
    return terrain.z(bd.lonlat_to_xy(lon, lat))


def load_graphml(path: Path, terrain: bd.Terrain):
    """The zh-cycling osmnx graph (LV95, directed: one-way streets and contraflow already resolved). Each edge's
    time is summed over the vertices of its geometry, so a climb in the middle of a long edge counts."""
    ns = "{http://graphml.graphdrawing.org/xmlns}"
    keys, coords, edges = {}, {}, []
    for _, element in ET.iterparse(path, events=("end",)):
        tag = element.tag.removeprefix(ns)
        if tag == "key":
            keys[element.get("id")] = element.get("attr.name")
        elif tag == "node":
            data = {keys.get(d.get("key")): d.text for d in element.findall(f"{ns}data")}
            coords[element.get("id")] = (float(data["x"]), float(data["y"]))
            element.clear()
        elif tag == "edge":
            data = {keys.get(d.get("key")): d.text for d in element.findall(f"{ns}data")}
            edges.append((element.get("source"), element.get("target"), data.get("geometry")))
            element.clear()
    best: dict = {}
    for source, target, geometry in edges:
        if geometry and geometry.startswith("LINESTRING"):
            line = [tuple(map(float, pair.split())) for pair in geometry[geometry.index("(") + 1:-1].split(",")]
        else:
            line = [coords[source], coords[target]]
        minutes = 0.0
        heights = [z_lv95(terrain, e, n) for e, n in line]
        for (e1, n1), (e2, n2), z1, z2 in zip(line, line[1:], heights, heights[1:]):
            meters = math.hypot(e2 - e1, n2 - n1)
            minutes += meters / (BIKE_METERS_PER_MINUTE * bike_factor(z2 - z1, meters))
        key = (int(source), int(target))  # osmnx node ids are OSM node ids
        best[key] = min(minutes, best.get(key, math.inf))
    return {int(node_id): lv95_to_xy(*xy) for node_id, xy in coords.items()}, best


def merge(*networks):
    """Join networks on their OSM node ids (the zh-cycling graph and the Overpass patches around it)."""
    coords, edges = {}, {}
    for nodes, links in networks:
        coords.update(nodes)
        for key, minutes in links.items():
            edges[key] = min(minutes, edges.get(key, math.inf))
    index = {node_id: i for i, node_id in enumerate(coords)}
    points = list(coords.values())
    graph = [[] for _ in points]
    for (u, v), minutes in edges.items():
        if u in index and v in index:
            graph[index[u]].append((index[v], minutes))
    return points, graph


# --- Street layer (faint base map) -------------------------------------------------------------------------

STREET_CLASSES = {
    "major": {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link"},
    "minor": {"tertiary", "tertiary_link", "unclassified", "residential", "living_street", "road"},
    "path": {"path", "track", "cycleway", "footway", "pedestrian", "bridleway"},
}
STREET_TOLERANCE_METERS = 3.0


def street_class(highway: str | None) -> str | None:
    first = (highway or "").strip("[]'\" ").split("'")[0]
    return next((name for name, kinds in STREET_CLASSES.items() if first in kinds), None)


def douglas_peucker(points, tolerance):
    if len(points) <= 2:
        return points
    (ax, ay), (bx, by) = points[0], points[-1]
    dx, dy = bx - ax, by - ay
    norm = math.hypot(dx, dy) or 1e-9
    index, distance = 0, -1.0
    for i in range(1, len(points) - 1):
        d = abs(dy * (points[i][0] - ax) - dx * (points[i][1] - ay)) / norm
        if d > distance:
            index, distance = i, d
    if distance <= tolerance:
        return [points[0], points[-1]]
    return douglas_peucker(points[: index + 1], tolerance)[:-1] + douglas_peucker(points[index:], tolerance)


def street_lines(data_dir: Path):
    """Undirected street polylines in local metres, by class, from the zh-cycling graph and the Overpass patches."""
    seen, lines = set(), defaultdict(list)
    graphml = data_dir / "bike_graph.graphml"
    if graphml.exists():
        ns = "{http://graphml.graphdrawing.org/xmlns}"
        keys, coords = {}, {}
        for _, element in ET.iterparse(graphml, events=("end",)):
            tag = element.tag.removeprefix(ns)
            if tag == "key":
                keys[element.get("id")] = element.get("attr.name")
            elif tag == "node":
                data = {keys.get(d.get("key")): d.text for d in element.findall(f"{ns}data")}
                coords[element.get("id")] = (float(data["x"]), float(data["y"]))
                element.clear()
            elif tag == "edge":
                data = {keys.get(d.get("key")): d.text for d in element.findall(f"{ns}data")}
                u, v = element.get("source"), element.get("target")
                element.clear()
                kind = street_class(data.get("highway"))
                geometry = data.get("geometry")
                key = (min(u, v), max(u, v), len(geometry or ""))
                if not kind or key in seen:
                    continue
                seen.add(key)
                if geometry and geometry.startswith("LINESTRING"):
                    line = [tuple(map(float, pair.split())) for pair in geometry[geometry.index("(") + 1:-1].split(",")]
                else:
                    line = [coords[u], coords[v]]
                lines[kind].append([lv95_to_xy(e, n) for e, n in line])
    patches = data_dir / "osm_bike.json"
    if patches.exists():
        payload = bd.load_json(patches)
        nodes = {e["id"]: bd.lonlat_to_xy(e["lon"], e["lat"]) for e in payload["elements"] if e["type"] == "node"}
        for way in (e for e in payload["elements"] if e["type"] == "way"):
            kind = street_class(way.get("tags", {}).get("highway"))
            refs = [ref for ref in way["nodes"] if ref in nodes]
            if kind and len(refs) >= 2 and ("w", way["id"]) not in seen:
                seen.add(("w", way["id"]))
                lines[kind].append([nodes[ref] for ref in refs])
    return lines


def write_streets(city: dict, meta: dict) -> None:
    min_x, min_y, max_x, max_y = meta["bounds"]
    out = {"origin": [round(min_x), round(min_y)], "classes": {}}
    total = 0
    for kind, lines in street_lines(ROOT / "data" / city["slug"]).items():
        encoded = []
        for line in lines:
            if not any(min_x <= x <= max_x and min_y <= y <= max_y for x, y in line):
                continue
            simple = douglas_peucker(line, STREET_TOLERANCE_METERS)
            flat, px, py = [], round(min_x), round(min_y)
            for x, y in simple:
                ix, iy = round(x), round(y)
                flat += [ix - px, iy - py]
                px, py = ix, iy
            if len(flat) >= 4:
                encoded.append(flat)
                total += len(flat) // 2
        out["classes"][kind] = encoded
    path = ROOT / "site" / "data" / f"{city['slug']}-streets.json"
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    counts = {kind: len(lines) for kind, lines in out["classes"].items()}
    print(f"Wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.2f} MB, {counts}, {total} points)")


BUS_TOLERANCE_METERS = 5.0
BUS_FALLBACK_COLOR = "#8a8d96"


def too_light(colour: str) -> bool:
    """White or near-white route colours (some OSM bus relations) vanish on the map: use the fallback grey."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.299 * r + 0.587 * g + 0.114 * b > 215


def write_bus_lines(city: dict, meta: dict) -> None:
    """Bus and trolleybus line geometry from OSM route relations (data/<city>/osm_bus.json), keyed by line number:
    the Swiss GTFS has no shapes. Year-independent; the page draws the lines that run in the selected timetable."""
    source = ROOT / "data" / city["slug"] / "osm_bus.json"
    if not source.exists():
        print("  osm_bus.json missing: no bus lines on the map")
        return
    min_x, min_y, max_x, max_y = meta["bounds"]
    ox, oy = round(min_x), round(min_y)
    by_ref: dict = {}
    seen = defaultdict(set)
    colours = defaultdict(lambda: defaultdict(int))
    for relation in bd.load_json(source)["elements"]:
        tags = relation.get("tags", {})
        ref = tags.get("ref", "").strip()
        if relation["type"] != "relation" or not ref:
            continue
        colour = tags.get("colour", "")
        for member in relation.get("members", []):
            if member["type"] != "way" or member.get("role") not in ("", None, "forward", "backward") or member["ref"] in seen[ref]:
                continue
            seen[ref].add(member["ref"])
            runs = [[]]
            for node in member.get("geometry", []):
                x, y = bd.lonlat_to_xy(node["lon"], node["lat"])
                if min_x <= x <= max_x and min_y <= y <= max_y:
                    runs[-1].append((x, y))
                elif runs[-1]:
                    runs.append([])
            for run in runs:
                if len(run) < 2:
                    continue
                flat, px, py = [], ox, oy
                for x, y in douglas_peucker(run, BUS_TOLERANCE_METERS):
                    ix, iy = round(x), round(y)
                    flat += [ix - px, iy - py]
                    px, py = ix, iy
                by_ref.setdefault(ref, []).append(flat)
                if colour.startswith("#") and len(colour) == 7 and not too_light(colour):
                    colours[ref][colour.upper()] += 1
    lines = {
        ref: {"color": max(colours[ref].items(), key=lambda item: item[1])[0] if colours[ref] else BUS_FALLBACK_COLOR, "ways": ways}
        for ref, ways in sorted(by_ref.items())
    }
    path = ROOT / "site" / "data" / f"{city['slug']}-buses.json"
    path.write_text(json.dumps({"origin": [ox, oy], "lines": lines}, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.2f} MB, {len(lines)} lines)")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    city = load_city(sys.argv[1])
    bd.LAT0 = city["lat0"]
    data_dir = ROOT / "data" / city["slug"]
    transit = bd.load_json(ROOT / "site" / "data" / f"{city['slug']}-{city['defaultTimetable']}.json")
    meta, cells = transit["meta"], transit["cells"]
    terrain = bd.Terrain(data_dir, meta["bounds"], [])

    sources = [(data_dir / "bike_graph.graphml", load_graphml), (data_dir / "osm_bike.json", load_network)]
    sources = [(path, loader) for path, loader in sources if path.exists()]
    print(f"Cycling network ({', '.join(path.name for path, _ in sources)})…")
    points, graph = merge(*(loader(path, terrain) for path, loader in sources))
    print(f"  {len(points)} nodes, {sum(len(e) for e in graph)} directed edges")
    connected = [i for i, edges in enumerate(graph) if edges]
    nodes_index = bd.StationIndex(points, connected, size=200.0)
    degree = defaultdict(set)
    for u, edges in enumerate(graph):
        for v, _ in edges:
            degree[u].add(v)
            degree[v].add(u)
    junctions = [i for i in connected if len(degree[i]) >= 3]
    junction_index = bd.StationIndex(points, junctions, size=200.0)

    # Snap: the street node nearest to each cell centre, walked (pushed) at walking pace.
    snap_node, snap_minutes = [], []
    for cell in cells:
        found = junction_index.nearest(tuple(cell["point"]), 1, max_rings=1)
        if not found or found[0][0] > SNAP_JUNCTION_METERS:
            found = nodes_index.nearest(tuple(cell["point"]), 1, max_rings=3)
        if found and found[0][0] <= SNAP_MAX_METERS:
            snap_node.append(found[0][1])
            snap_minutes.append(found[0][0] / bd.WALK_METERS_PER_MINUTE)
        else:
            snap_node.append(-1)
            snap_minutes.append(math.inf)

    by_position = {(cell["row"], cell["col"]): i for i, cell in enumerate(cells)}
    adjacency = []
    for i, cell in enumerate(cells):
        source = snap_node[i]
        targets = {}
        for dr, dc in NEIGHBOURS:
            j = by_position.get((cell["row"] + dr, cell["col"] + dc))
            if j is not None and snap_node[j] >= 0:
                targets.setdefault(snap_node[j], []).append(j)
        edges = []
        if source >= 0 and targets:
            best = {source: 0.0}
            heap = [(0.0, source)]
            remaining = set(targets)
            while heap and remaining:
                time, u = heapq.heappop(heap)
                if time > best.get(u, math.inf):
                    continue
                if u in remaining:
                    remaining.discard(u)
                    edges += [[j, round(time, 2)] for j in targets[u]]
                for v, weight in graph[u]:
                    t = time + weight
                    if t <= SEARCH_LIMIT_MINUTES and t < best.get(v, math.inf):
                        best[v] = t
                        heapq.heappush(heap, (t, v))
        adjacency.append(sorted(edges))

    output = {
        "meta": {
            "speedKmh": BIKE_KMH,
            "parkMinutes": PARK_MINUTES,
            "model": "OSM streets open to bicycles, contraflow where allowed, 20 km/h on the flat, slopes from swisstopo",
        },
        "snap": [round(m, 2) if math.isfinite(m) else None for m in snap_minutes],
        "adjacency": adjacency,
    }
    write_streets(city, meta)
    write_bus_lines(city, meta)
    path = ROOT / "site" / "data" / f"{city['slug']}-bike.json"
    path.write_text(json.dumps(output, separators=(",", ":")), encoding="utf-8")
    edge_count = sum(len(e) for e in adjacency)
    unsnapped = sum(1 for n in snap_node if n < 0)
    print(f"Wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.2f} MB, {edge_count} cell edges, {unsnapped} cells without a street)")


if __name__ == "__main__":
    main()
