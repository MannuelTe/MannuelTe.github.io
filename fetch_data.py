#!/usr/bin/env python3
"""Download the raw sources of a city into data/<city>/.

Usage: python3 fetch_data.py <city> [--gtfs-only]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
USER_AGENT = "tram.camilleroux.com/0.2 (build script)"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def load_city(slug: str) -> dict:
    return json.loads((ROOT / "cities" / f"{slug}.json").read_text(encoding="utf-8"))


def bbox(values) -> str:
    return ",".join(str(v) for v in values)


def download(url: str, data: bytes | None = None) -> bytes:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


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


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    city = load_city(sys.argv[1])
    out = ROOT / "data" / city["slug"]
    out.mkdir(parents=True, exist_ok=True)

    print(f"GTFS {city['network']}…")
    (out / "gtfs.zip").write_bytes(download(city["gtfsUrl"]))
    if "--gtfs-only" in sys.argv:
        return

    print(f"Communes de {city['metropole']}…")
    communes_url = f"https://geo.api.gouv.fr/epcis/{city['epci']}/communes?fields=nom,code&format=geojson&geometry=contour"
    (out / "communes.geojson").write_bytes(download(communes_url))

    if city.get("railGeometry") == "osm":
        print("Tracés des lignes (OSM)…")
        query = f'[out:json][timeout:110];relation["route"~"^(tram|subway|light_rail)$"]({bbox(city["osmRailBbox"])});out geom;'
        (out / "osm_rail.json").write_bytes(overpass(query))

    print("Eau et parcs (OSM)…")
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


if __name__ == "__main__":
    main()
