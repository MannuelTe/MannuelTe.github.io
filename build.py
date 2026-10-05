#!/usr/bin/env python3
"""Build the whole site: data of the city, the page, then a control table.

Usage: python3 build.py [city …] [--fetch]
  (no city: all of them; --fetch: download the sources first)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cities import load_cities, load_city

ROOT = Path(__file__).resolve().parent


def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def control_row(slug: str, year: str) -> str:
    """One line per timetable year to spot anomalies at a glance (reference day, size, reach, farthest station)."""
    sources = json.loads((ROOT / "sources" / f"{slug}-{year}.json").read_text(encoding="utf-8"))
    stats = sources["stats"]
    size = (ROOT / "site" / "data" / f"{slug}-{year}.json").stat().st_size / 1e6
    lines = " ".join(f"{line['name']}:{line['headway']:g}" for line in stats["lines"] if line["mode"] == "tram")
    return (
        f"{slug} {year} {sources['referenceDate']} | {size:4.1f} MB | {stats['railStations']:3d} stations | "
        f"30 min: {stats['within30']:3d}% | farthest: {stats['farthestStation'][:24]} {stats['farthestMinutes']} min | {lines}"
    )


def main() -> None:
    flags = {arg for arg in sys.argv[1:] if arg.startswith("--")}
    slugs = [arg for arg in sys.argv[1:] if not arg.startswith("--")] or [city["slug"] for city in load_cities()]
    for slug in slugs:
        if "--fetch" in flags:
            run("fetch_data.py", slug)
        for year in load_city(slug)["timetables"]:
            run("build_data.py", slug, year)
        run("build_bike.py", slug)
    run("build_pages.py")
    print()
    for slug in slugs:
        for year in load_city(slug)["timetables"]:
            print(control_row(slug, year))


if __name__ == "__main__":
    main()
