#!/usr/bin/env python3
"""Check the model's travel times against the real timetable (transport.opendata.ch, no key needed).

Usage: python3 tools/validate_trips.py <city> [year] [--pairs N] [--seed S]

Random pairs of tram/rail stops on the map. For each pair:
- model: station to station on the year's bundle, tram and train only, the same routing as site/app.js
  (wait = half the headway, changes with walk and wait), without the access minutes at either end;
- real: every connection in two hours of the reference day (08:00 and 12:00), trains and trams only; the expected
  trip for someone arriving at a random minute, min over departures after that minute of (arrival - minute).
Both are expected times including the wait, so they compare directly. API answers are cached in data/<city>/api/.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import random
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://transport.opendata.ch/v1"
WINDOWS = ("08:00", "12:00")  # one hour from each
METERS_PER_DEGREE = 111320


def args():
    parser = argparse.ArgumentParser(description="Model travel times against transport.opendata.ch")
    parser.add_argument("city")
    parser.add_argument("year", nargs="?")
    parser.add_argument("--pairs", type=int, default=30)
    parser.add_argument("--seed", type=int, default=1)
    a = parser.parse_args()
    return a.city, a.year, a.pairs, a.seed


def get(path: str, params: list, cache: Path) -> dict:
    query = urllib.parse.urlencode(params)
    key = cache / (hashlib.sha1(f"{path}?{query}".encode()).hexdigest() + ".json")
    if key.exists():
        return json.loads(key.read_text())
    request = urllib.request.Request(f"{API}/{path}?{query}", headers={"User-Agent": "zurich-temps-transport validate_trips"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read())
            break
        except Exception:  # rate limit or timeout: back off
            time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"API failed: {path}?{query}")
    key.write_text(json.dumps(data))
    time.sleep(0.4)
    return data


def model_times(d: dict, origin: int) -> list:
    """Minutes from stop `origin` to every stop, tram and train only, platform to platform (no access minutes)."""
    st, rs, ri = d["stations"], d["routeStates"], d["routeInfo"]
    rail = lambda k: ri[rs[k]["routeId"]]["rail"]  # noqa: E731
    walk = d["meta"]["walkMetersPerMinute"]
    o = st[origin]["point"]
    dist = [math.inf] * len(rs)
    near = sorted((math.dist(o, s["point"]) / walk, i) for i, s in enumerate(st) if s["rail"])[: d["meta"]["originStationCount"]]
    for w, i in near:
        for k in d["stationStates"][i]:
            if rail(k):
                # walking to a neighbouring stop costs its access; from the origin stop itself only the wait counts
                dist[k] = min(dist[k], w + rs[k]["wait"] + (rs[k]["access"] if i != origin else 0))
    heap = [(t, k) for k, t in enumerate(dist) if t < math.inf]
    heapq.heapify(heap)
    while heap:
        t, u = heapq.heappop(heap)
        if t > dist[u]:
            continue
        for v, w in d["adjacency"][u]:
            if rail(v) and t + w < dist[v]:
                dist[v] = t + w
                heapq.heappush(heap, (t + w, v))
    out = [math.inf] * len(st)
    for k, r in enumerate(rs):
        out[r["stationIndex"]] = min(out[r["stationIndex"]], dist[k])
    return out


def real_expected(connections: list, start: datetime) -> float | None:
    """Expected trip (minutes) for a rider arriving at a uniformly random minute of the hour after `start`."""
    trips = []
    for c in connections:
        dep = datetime.fromisoformat(c["from"]["departure"].replace("+0200", "+02:00").replace("+0100", "+01:00"))
        arr = datetime.fromisoformat(c["to"]["arrival"].replace("+0200", "+02:00").replace("+0100", "+01:00"))
        trips.append(((dep - start).total_seconds() / 60, (arr - start).total_seconds() / 60))
    if not trips or max(dep for dep, _ in trips) < 60:
        return None  # not enough departures to cover the hour
    values = [min(arr for dep, arr in trips if dep >= minute) - minute for minute in range(60)]
    return statistics.mean(values)


def main() -> None:
    slug, year, n_pairs, seed = args()
    city = json.loads((ROOT / "cities" / f"{slug}.json").read_text(encoding="utf-8"))
    year = year or city["defaultTimetable"]
    d = json.loads((ROOT / "site" / "data" / f"{slug}-{year}.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "sources" / f"{slug}-{year}.json").read_text(encoding="utf-8"))
    day = sources["referenceDate"]
    cache = ROOT / "data" / slug / "api"
    cache.mkdir(parents=True, exist_ok=True)
    st = d["stations"]
    lat0 = math.radians(d["meta"]["lat0"])
    to_latlon = lambda p: (p[1] / METERS_PER_DEGREE, p[0] / (METERS_PER_DEGREE * math.cos(lat0)))  # noqa: E731

    # Rail stops inside the view (the clipped feed reaches a little beyond the map), each matched to a real station.
    x0, y0, x1, y1 = d["meta"]["viewBounds"]
    candidates = [i for i, s in enumerate(st) if s["rail"] and x0 <= s["point"][0] <= x1 and y0 <= s["point"][1] <= y1]
    rng = random.Random(seed)
    rows = []
    tried = 0
    while len(rows) < n_pairs and tried < n_pairs * 4:
        tried += 1
        a, b = rng.sample(candidates, 2)
        if math.dist(st[a]["point"], st[b]["point"]) < 1500:
            continue
        ids = []
        for i in (a, b):
            lat, lon = to_latlon(st[i]["point"])
            found = get("locations", [("x", f"{lat:.6f}"), ("y", f"{lon:.6f}"), ("type", "station")], cache)["stations"]
            found = [s for s in found if s.get("id") and s.get("distance") is not None and s["distance"] < 250]
            ids.append(found[0]["id"] if found else None)
        if None in ids:
            continue
        reals = []
        for window in WINDOWS:
            params = [("from", ids[0]), ("to", ids[1]), ("date", day), ("time", window), ("limit", "16"),
                      ("transportations[]", "train"), ("transportations[]", "tram"), ("transportations[]", "cableway")]
            connections = get("connections", params, cache).get("connections", [])
            value = real_expected(connections, datetime.fromisoformat(f"{day}T{window}:00+02:00"))
            if value is not None:
                reals.append(value)
        if not reals:
            continue
        model = model_times(d, a)[b]
        rows.append((st[a]["name"], st[b]["name"], model, statistics.mean(reals)))
        print(f"  {st[a]['name'][:26]:26} → {st[b]['name'][:26]:26} model {model:5.1f}  real {statistics.mean(reals):5.1f}  "
              f"{model - statistics.mean(reals):+5.1f}", flush=True)

    diffs = [m - r for _, _, m, r in rows if m < math.inf]
    ratio = [m / r for _, _, m, r in rows if m < math.inf and r > 0]
    print(f"\n{slug} {year}, reference day {day}: {len(rows)} pairs (seed {seed})")
    print(f"  model − real: mean {statistics.mean(diffs):+.1f} min, median {statistics.median(diffs):+.1f}, "
          f"mean |error| {statistics.mean(abs(x) for x in diffs):.1f}, model/real median {statistics.median(ratio):.2f}")
    print(f"  within ±3 min: {sum(abs(x) <= 3 for x in diffs)}/{len(diffs)}, within ±5: {sum(abs(x) <= 5 for x in diffs)}/{len(diffs)}")
    worst = sorted(rows, key=lambda r: abs(r[2] - r[3]), reverse=True)[:5]
    print("  largest gaps:", "; ".join(f"{a} → {b} {m - r:+.0f}" for a, b, m, r in worst))


if __name__ == "__main__":
    main()
