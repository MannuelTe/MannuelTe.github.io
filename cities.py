"""City configuration (cities/<slug>.json), completed with defaults shared by every script.

A config only needs what cannot be deduced: name, network, sources (GTFS, municipalities) and the centre of the map.
Everything else (titles, labels, OSM areas…) has a default below and can be overridden in the JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CITIES_DIR = ROOT / "cities"

# Half-sizes (degrees of latitude, longitude) of the OSM areas around the centre.
OSM_HALF_SIZE = (0.14, 0.20)
PARKS_HALF_SIZE = (0.08, 0.11)


def with_defaults(raw: dict) -> dict:
    city = dict(raw)
    lat, lon = city["defaultFrom"]["lat"], city["defaultFrom"]["lon"]
    city.setdefault("path", "")
    city.setdefault("railNoun", "tram and train")
    city.setdefault("railLabel", "Tram, S-Bahn and train")
    city.setdefault("railStations", "tram and rail stops")
    city.setdefault("busNoun", "bus and boat")
    city.setdefault("busLabel", "Bus and boat")
    city.setdefault("title", f"How far is it in {city['name']}?")
    city.setdefault("titleSuffix", f"Travel times by public transport in {city['name']}")
    city.setdefault("railGeometry", "osm")
    city.setdefault("lat0", round(lat, 2))
    city.setdefault("osmBbox", [round(lat - OSM_HALF_SIZE[0], 2), round(lon - OSM_HALF_SIZE[1], 2),
                                round(lat + OSM_HALF_SIZE[0], 2), round(lon + OSM_HALF_SIZE[1], 2)])
    city.setdefault("parksBbox", [round(lat - PARKS_HALF_SIZE[0], 2), round(lon - PARKS_HALF_SIZE[1], 2),
                                  round(lat + PARKS_HALF_SIZE[0], 2), round(lon + PARKS_HALF_SIZE[1], 2)])
    city.setdefault("osmRailBbox", city["osmBbox"])
    city.setdefault(
        "ogAlt",
        f"Map of {city['name']} coloured by public transport travel time from {city['defaultFrom']['label']}, "
        "with the 15 and 30 minute isochrones.",
    )
    return city


def load_city(slug: str) -> dict:
    return with_defaults(json.loads((CITIES_DIR / f"{slug}.json").read_text(encoding="utf-8")))


def load_cities() -> list[dict]:
    return [with_defaults(json.loads(path.read_text(encoding="utf-8"))) for path in sorted(CITIES_DIR.glob("*.json"))]
