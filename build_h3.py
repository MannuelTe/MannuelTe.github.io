#!/usr/bin/env python3
"""Reach from everywhere: for every H3 hexagon on the map, the residents and the land area reachable by public
transport within 15, 30 and 45 minutes, on each timetable year. Written to site/data/<city>-h3.json for the two
hexagon maps on the page (reach in one year, change between two years).

Two networks: rail only (tram, S-Bahn, train, funiculars: the interactive map's default view) and all lines, buses and
boats included (« Bus and boat » ticked). Same model as the interactive map (site/app.js): walk to the nearest stations
(Tobler's hiking function, never across a lake), wait, ride, change, walk from the stop to each 200 m cell; or walk
the whole way. Each hexagon starts from its centre. Reads only the computed bundles in site/data/.

Usage: python3 build_h3.py <city> [--res 9]   (run build_data.py for every timetable year first)
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import time
from pathlib import Path

import h3
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from cities import load_city

ROOT = Path(__file__).resolve().parent
LIMITS = (15, 30, 45)
CELL_KM2 = 0.04  # one 200 m grid cell
BATCH = 400  # origins per Dijkstra call
MAX_DIRECT_WALK = 4_200.0  # metres: 45 min on foot, on the steepest helpful descent


class Bundle:
    """A timetable year's routing graph, cells and terrain, as numpy arrays."""

    def __init__(self, path: Path, include_bus: bool = False):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.meta = meta = data["meta"]
        self.walk_rate = meta["walkMetersPerMinute"]
        self.tobler = meta["tobler"]
        self.seed_count = meta["originStationCount"]
        # Rail only (the browser's default view), or every line as with « Bus and boat » ticked.
        used = {route_id for route_id, info in data["routeInfo"].items() if info["rail"] or include_bus}

        states = data["routeStates"]
        self.n = len(states)
        self.state_station = np.array([s["stationIndex"] for s in states])
        self.wait = np.array([s["wait"] for s in states])
        self.access = np.array([s["access"] for s in states])
        self.state_used = np.array([s["routeId"] in used for s in states])

        # Edges between the lines in use.
        src, dst, w = [], [], []
        for u, edges in enumerate(data["adjacency"]):
            if not self.state_used[u]:
                continue
            for v, weight in edges:
                if self.state_used[v]:
                    src.append(u)
                    dst.append(v)
                    w.append(weight)
        self.edges = dedupe(np.array(src), np.array(dst), np.array(w, dtype=float))

        stations = data["stations"]
        self.station_xy = np.array([s["point"] for s in stations])
        self.station_rail = np.array([s["rail"] for s in stations])
        self.station_states = data["stationStates"]
        self.station_z = np.array([s.get("z") if s.get("z") is not None else np.nan for s in stations], dtype=float)
        self.usable_station_ids = np.flatnonzero(self.station_rail | include_bus)
        self.station_groups = [(np.array(ids), station) for station, ids in enumerate(self.station_states) if ids]

        cells = data["cells"]
        self.cell_xy = np.array([c["point"] for c in cells])
        self.cell_z = np.array([c.get("z") if c.get("z") is not None else np.nan for c in cells], dtype=float)
        self.cell_pop = np.array([c.get("pop", 0) for c in cells], dtype=float)
        access = [(i, station, minutes) for i, c in enumerate(cells) for station, minutes in c["access"]]
        self.access_cell = np.array([a[0] for a in access])
        self.access_station = np.array([a[1] for a in access])
        self.access_minutes = np.array([a[2] for a in access])
        self.access_starts = np.flatnonzero(np.r_[True, np.diff(self.access_cell) != 0])
        self.mask = np.array(data["mask"])

        self.z_grid = fill_gaps(self.cell_grid_z(cells), meta["gridCols"], meta["gridRows"], 6)
        lakes = data.get("lakes")
        self.lakes = lakes
        if lakes:
            raw = np.frombuffer(base64.b64decode(lakes["bits"]), dtype=np.uint8)
            self.lake_bits = np.unpackbits(raw, bitorder="little")
        else:
            self.lake_bits = None
        self.boroughs = data["boroughs"]
        self.water = data["water"]

    def cell_grid_z(self, cells) -> np.ndarray:
        cols, rows = self.meta["gridCols"], self.meta["gridRows"]
        z = np.full(cols * rows, np.nan)
        for c in cells:
            z[c["row"] * cols + c["col"]] = c.get("z") or 0
        return z

    def elevation(self, xy: np.ndarray) -> np.ndarray:
        """Bilinear elevation from the cell grid (elevationAt in app.js)."""
        min_x, min_y, max_x, max_y = self.meta["bounds"]
        cols, rows = self.meta["gridCols"], self.meta["gridRows"]
        gx = np.clip((xy[:, 0] - min_x) / (max_x - min_x) * cols - 0.5, 0, cols - 1)
        gy = np.clip((xy[:, 1] - min_y) / (max_y - min_y) * rows - 0.5, 0, rows - 1)
        c0, r0 = np.floor(gx).astype(int), np.floor(gy).astype(int)
        c1, r1 = np.minimum(c0 + 1, cols - 1), np.minimum(r0 + 1, rows - 1)
        tx, ty = gx - c0, gy - r0
        z = np.nan_to_num(self.z_grid)
        at = lambda r, c: z[r * cols + c]  # noqa: E731
        return (at(r0, c0) * (1 - tx) + at(r0, c1) * tx) * (1 - ty) + (at(r1, c0) * (1 - tx) + at(r1, c1) * tx) * ty

    def is_lake(self, xy: np.ndarray) -> np.ndarray:
        if self.lake_bits is None:
            return np.zeros(len(xy), dtype=bool)
        min_x, min_y = self.meta["bounds"][:2]
        col = np.floor((xy[..., 0] - min_x) / self.lakes["cell"]).astype(int)
        row = np.floor((xy[..., 1] - min_y) / self.lakes["cell"]).astype(int)
        inside = (col >= 0) & (row >= 0) & (col < self.lakes["cols"]) & (row < self.lakes["rows"])
        index = np.where(inside, row * self.lakes["cols"] + col, 0)
        return inside & (self.lake_bits[index] == 1)

    def crosses_lake(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """crossesLake in app.js, for pairs of points a[i] → b[i]."""
        out = np.zeros(len(a), dtype=bool)
        if self.lake_bits is None or not len(a):
            return out
        steps = np.maximum(1, np.ceil(np.hypot(*(b - a).T) / self.lakes["step"])).astype(int)
        for k_steps in np.unique(steps):
            pick = np.flatnonzero(steps == k_steps)
            if k_steps < 2:
                continue
            frac = np.arange(1, k_steps) / k_steps
            points = a[pick, None, :] + (b[pick] - a[pick])[:, None, :] * frac[None, :, None]
            out[pick] = self.is_lake(points).any(axis=1)
        return out

    def hill_walk(self, a: np.ndarray, za, b: np.ndarray, zb) -> np.ndarray:
        """Minutes on foot along the straight line, slower uphill (hillWalk in app.js)."""
        meters = np.hypot(*(b - a).T) if a.ndim == 2 else np.hypot(b[..., 0] - a[0], b[..., 1] - a[1])
        k, offset, min_run = self.tobler["k"], self.tobler["offset"], self.tobler["minRun"]
        slope = (zb - za) / np.maximum(meters, min_run)
        factor = np.exp(-k * np.abs(slope + offset)) / math.exp(-k * offset)
        return meters / self.walk_rate / factor

    def land_cell(self, xy: np.ndarray) -> np.ndarray:
        """Index of the 200 m land cell under each point, or -1."""
        min_x, min_y, max_x, max_y = self.meta["bounds"]
        cols, rows = self.meta["gridCols"], self.meta["gridRows"]
        col = np.floor((xy[:, 0] - min_x) / (max_x - min_x) * cols).astype(int)
        row = np.floor((xy[:, 1] - min_y) / (max_y - min_y) * rows).astype(int)
        inside = (col >= 0) & (row >= 0) & (col < cols) & (row < rows)
        return np.where(inside, self.mask[np.where(inside, row * cols + col, 0)], -1)


def dedupe(src: np.ndarray, dst: np.ndarray, w: np.ndarray):
    """Keep the shortest of parallel edges: a sparse matrix would add them up."""
    order = np.lexsort((w, dst, src))
    src, dst, w = src[order], dst[order], w[order]
    first = np.r_[True, (np.diff(src) != 0) | (np.diff(dst) != 0)]
    return src[first], dst[first], w[first]


def fill_gaps(values: np.ndarray, cols: int, rows: int, passes: int) -> np.ndarray:
    """fillGaps in app.js: empty cells take the mean of their 4 neighbours, `passes` times."""
    grid = values.reshape(rows, cols).copy()
    for _ in range(passes):
        source = grid.copy()
        padded = np.pad(source, 1, constant_values=np.nan)
        neighbours = np.stack([padded[2:, 1:-1], padded[:-2, 1:-1], padded[1:-1, 2:], padded[1:-1, :-2]])
        count = np.sum(~np.isnan(neighbours), axis=0)
        mean = np.nansum(neighbours, axis=0) / np.maximum(count, 1)
        fill = np.isnan(source) & (count > 0)
        grid[fill] = mean[fill]
    return grid.ravel()


def to_world(lat: float, lon: float, lat0: float) -> tuple[float, float]:
    m = 111_320.0
    return lon * m * math.cos(math.radians(lat0)), lat * m


def to_latlon(x: float, y: float, lat0: float) -> tuple[float, float]:
    m = 111_320.0
    return y / m, x / (m * math.cos(math.radians(lat0)))


def origin_hexes(bundle: Bundle, res: int) -> list[str]:
    """Hexagons whose centre lies on a land cell of the map, off the lakes."""
    lat0 = bundle.meta["lat0"]
    candidates = {h3.latlng_to_cell(*to_latlon(x, y, lat0), res) for x, y in bundle.cell_xy}
    hexes = sorted(candidates)
    centres = np.array([to_world(*h3.cell_to_latlng(h), lat0) for h in hexes])
    keep = (bundle.land_cell(centres) >= 0) & ~bundle.is_lake(centres)
    return [h for h, k in zip(hexes, keep) if k]


def travel_times(bundle: Bundle, origins: np.ndarray) -> np.ndarray:
    """Minutes from each origin to each 200 m cell, on the bundle's network."""
    station_z = bundle.station_z
    origin_z = bundle.elevation(origins)
    k_count = len(origins)
    ids = bundle.usable_station_ids

    # Virtual source nodes: one per origin, linked to the platforms of the stations it walks to.
    src, dst, w = [bundle.edges[0]], [bundle.edges[1]], [bundle.edges[2]]
    for k, (origin, z) in enumerate(zip(origins, origin_z)):
        meters = np.hypot(*(bundle.station_xy[ids] - origin).T)
        near = ids[np.argsort(meters, kind="stable")[: bundle.seed_count * 2]]
        walk = bundle.hill_walk(origin, z, bundle.station_xy[near], np.where(np.isnan(station_z[near]), z, station_z[near]))
        walk[bundle.crosses_lake(np.repeat(origin[None], len(near), 0), bundle.station_xy[near])] = np.inf
        finite = np.isfinite(walk)
        near, walk = near[finite], walk[finite]
        order = np.argsort(walk, kind="stable")[: bundle.seed_count]
        for station, minutes in zip(near[order], walk[order]):
            for state in bundle.station_states[station]:
                if bundle.state_used[state]:
                    src.append([bundle.n + k])
                    dst.append([state])
                    w.append([minutes + bundle.access[state] + bundle.wait[state]])
    src, dst, w = dedupe(np.concatenate(src), np.concatenate(dst), np.concatenate(w).astype(float))
    size = bundle.n + k_count
    graph = csr_matrix((w, (src, dst)), shape=(size, size))
    dist = dijkstra(graph, directed=True, indices=np.arange(bundle.n, size))[:, : bundle.n]

    # Out on the street at each station, then on foot to each cell.
    out = dist + bundle.access[None, :]
    station_time = np.full((k_count, len(bundle.station_xy)), np.inf)
    for state_ids, station in bundle.station_groups:
        station_time[:, station] = out[:, state_ids].min(axis=1)
    via_stop = station_time[:, bundle.access_station] + bundle.access_minutes[None, :]
    times = np.minimum.reduceat(via_stop, bundle.access_starts, axis=1)
    # Cells with no stop nearby (none in practice) stay unreachable by transit.
    full = np.full((k_count, len(bundle.cell_xy)), np.inf)
    full[:, bundle.access_cell[bundle.access_starts]] = times

    # Or walk the whole way, where that is quicker and no lake is in between.
    for k, (origin, z) in enumerate(zip(origins, origin_z)):
        near = np.flatnonzero(np.hypot(*(bundle.cell_xy - origin).T) <= MAX_DIRECT_WALK)
        zb = np.where(np.isnan(bundle.cell_z[near]), z, bundle.cell_z[near])
        direct = bundle.hill_walk(origin, z, bundle.cell_xy[near], zb)
        better = (direct < full[k, near]) & (direct <= max(LIMITS))
        cand = near[better]
        blocked = bundle.crosses_lake(np.repeat(origin[None], len(cand), 0), bundle.cell_xy[cand])
        full[k, cand[~blocked]] = direct[better][~blocked]
    return full


def reach(bundle: Bundle, origins: np.ndarray) -> dict:
    pop = {limit: [] for limit in LIMITS}
    area = {limit: [] for limit in LIMITS}
    for start in range(0, len(origins), BATCH):
        times = travel_times(bundle, origins[start : start + BATCH])
        for limit in LIMITS:
            within = times <= limit
            pop[limit].append(within @ bundle.cell_pop)
            area[limit].append(within.sum(axis=1))
    return {
        "pop": {str(limit): np.rint(np.concatenate(pop[limit])).astype(int).tolist() for limit in LIMITS},
        "cells": {str(limit): np.concatenate(area[limit]).astype(int).tolist() for limit in LIMITS},
    }


def point_in_ring(x: float, y: float, ring) -> bool:
    inside = False
    for (xi, yi), (xj, yj) in zip(ring, ring[-1:] + ring[:-1]):
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
    return inside


def commune_of(bundle: Bundle, centres: np.ndarray) -> list[int]:
    """Index of the municipality each hexagon centre lies in (-1 if none)."""
    out = []
    for x, y in centres:
        out.append(next((i for i, commune in enumerate(bundle.boroughs)
                         if any(point_in_ring(x, y, polygon[0]) for polygon in commune["polygons"])), -1))
    return out


def round_ring(ring) -> list:
    return [[round(x), round(y)] for x, y in ring]


def outlines(bundle: Bundle) -> dict:
    """Municipal boundaries and lakes for the hexagon maps' backdrop, coarsened to a few metres."""
    def coarse(ring, step=60.0):
        out = [ring[0]]
        for point in ring[1:]:
            if math.hypot(point[0] - out[-1][0], point[1] - out[-1][1]) >= step:
                out.append(point)
        return round_ring(out)

    return {
        "communes": [coarse(polygon[0]) for commune in bundle.boroughs for polygon in commune["polygons"]],
        "lakes": [coarse(polygon[0]) for polygon in bundle.water],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("city")
    parser.add_argument("--res", type=int, default=9, help="H3 resolution (9 ≈ 0.1 km² per hexagon)")
    parser.add_argument("--check", action="store_true", help="only compare the city centre with the page's figures")
    args = parser.parse_args()
    city = load_city(args.city)
    years = list(city["timetables"])

    networks = {"rail": False, "all": True}  # include buses and boats?
    bundles = {network: {year: Bundle(ROOT / "site" / "data" / f"{city['slug']}-{year}.json", include_bus)
                         for year in years} for network, include_bus in networks.items()}

    first = bundles["rail"][years[0]]
    lat0 = first.meta["lat0"]
    centre = np.array([to_world(city["defaultFrom"]["lat"], city["defaultFrom"]["lon"], lat0)])
    for year in years:
        rail, every = reach(bundles["rail"][year], centre), reach(bundles["all"][year], centre)
        stats = json.loads((ROOT / "sources" / f"{city['slug']}-{year}.json").read_text(encoding="utf-8"))["stats"]
        print(f"  {year} from {city['defaultFrom']['label']}, residents within 15/30/45 min: rail "
              f"{[rail['pop'][str(l)][0] for l in LIMITS]} (page: {[stats['population'].get(str(l)) for l in LIMITS]}), "
              f"with buses {[every['pop'][str(l)][0] for l in LIMITS]}")
    if args.check:
        return

    hexes = origin_hexes(first, args.res)
    centres = np.array([to_world(*h3.cell_to_latlng(h), lat0) for h in hexes])
    print(f"  {len(hexes)} hexagons at resolution {args.res} ({h3.average_hexagon_area(args.res, 'km^2'):.3f} km² each)")
    values = {network: {} for network in networks}
    for network in networks:
        for year in years:
            started = time.time()
            values[network][year] = reach(bundles[network][year], centres)
            print(f"  {network} {year}: {time.time() - started:.0f} s")

    out = {
        "res": args.res,
        "limits": list(LIMITS),
        "cellKm2": CELL_KM2,
        "years": years,
        "networks": list(networks),
        "bounds": first.meta["viewBounds"],
        "totals": {"pop": int(first.cell_pop.sum()), "cells": len(first.cell_xy)},
        "hexes": [round_ring(to_world(lat, lon, lat0) for lat, lon in h3.cell_to_boundary(h)) for h in hexes],
        "ids": hexes,
        "communeNames": [commune["name"] for commune in first.boroughs],
        "commune": commune_of(first, centres),
        "values": values,
        **outlines(first),
    }
    path = ROOT / "site" / "data" / f"{city['slug']}-h3.json"
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
