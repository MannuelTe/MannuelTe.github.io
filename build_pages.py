#!/usr/bin/env python3
"""Render one HTML page per city (cities/*.json) from templates/city.html, plus sitemap.xml and robots.txt.

Usage: python3 build_pages.py
"""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
SITE_URL = "https://tram.camilleroux.com/"


def short_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.exists() else "0"


def load_cities() -> list[dict]:
    cities = [json.loads(path.read_text(encoding="utf-8")) for path in (ROOT / "cities").glob("*.json")]
    return sorted(cities, key=lambda city: city["order"])


def city_links(cities: list[dict], current: dict) -> str:
    """Chips linking every city page; links are relative so they work locally and online."""
    depth = "../" if current["path"] else "./"
    links = []
    for city in cities:
        label = html.escape(city["name"])
        if city["slug"] == current["slug"]:
            links.append(f'          <span class="chip active" aria-current="page">📍 {label}</span>')
        else:
            links.append(f'          <a class="chip" href="{depth}{city["path"]}">{label}</a>')
    return "\n".join(links)


def render(template: Template, cities: list[dict], city: dict) -> str:
    url = SITE_URL + city["path"]
    base = "../" if city["path"] else "./"
    rail_noun = city["railNoun"]
    description = (
        f"Carte des temps de trajet en {rail_noun} à {city['name']} : choisissez un départ, toute la Métropole se colore "
        f"selon le temps qu'il faut pour y aller (réseau {city['network']})."
    )
    og_description = (
        f"{city['name']} redessiné par le temps de trajet en {rail_noun} : choisissez un départ, la carte se colore "
        "selon le temps qu'il faut pour aller partout ailleurs."
    )
    og_image = SITE_URL + city["ogImage"]
    json_ld = {
        "@context": "https://schema.org",
        "@type": "WebApplication",
        "name": city["title"],
        "url": url,
        "description": description,
        "inLanguage": "fr",
        "applicationCategory": "TravelApplication",
        "operatingSystem": "Web",
        "isAccessibleForFree": True,
        "image": og_image,
        "author": {"@type": "Person", "name": "Camille Roux", "url": "https://www.camilleroux.com/"},
        "spatialCoverage": {"@type": "Place", "name": city["metropole"]},
        "isBasedOn": ["https://castrio.me/nyc/", "https://julesgrandin.github.io/paris-temps-transport/"],
        "datePublished": city["published"],
    }
    config = {
        "slug": city["slug"],
        "name": city["name"],
        "dataVersion": short_hash(SITE / "data" / f"{city['slug']}.json"),
        "defaultFrom": city["defaultFrom"],
        "railNoun": rail_noun,
        "railStations": city["railStations"],
        "busNoun": city["busNoun"],
    }
    osm_credit = "tracés des lignes, eau et parcs" if city.get("railGeometry") == "osm" else "eau et parcs"
    values = {
        "title": city["title"],
        "title_suffix": city["titleSuffix"],
        "description": description,
        "og_description": og_description,
        "og_image": og_image,
        "og_alt": city["ogAlt"],
        "url": url,
        "base": base,
        "published": city["published"],
        "json_ld": "    " + json.dumps(json_ld, ensure_ascii=False, indent=2).replace("\n", "\n    "),
        "city_config": json.dumps(config, ensure_ascii=False),
        "city_links": city_links(cities, city),
        "name": city["name"],
        # Pas de mot seul en fin de ligne : espace insécable avant le dernier mot du titre.
        "headline": "&nbsp;".join(html.escape(city["title"]).rsplit(" ", 1)),
        "rail_noun": rail_noun,
        "rail_label": city["railLabel"],
        "bus_label": city["busLabel"],
        "search_example": city["searchExample"],
        "network": city["network"],
        "metropole": city["metropole"],
        "gtfs_dataset": city["gtfsDataset"],
        "osm_credit": osm_credit,
        "styles_version": short_hash(SITE / "styles.css"),
        "app_version": short_hash(SITE / "app.js"),
    }
    escaped = {key: value if key in ("json_ld", "city_config", "city_links", "headline") else html.escape(value, quote=True) for key, value in values.items()}
    return template.substitute(escaped)


def main() -> None:
    template = Template((ROOT / "templates" / "city.html").read_text(encoding="utf-8"))
    cities = load_cities()
    for city in cities:
        page = SITE / city["path"] / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(render(template, cities, city), encoding="utf-8")
        print(f"Wrote {page.relative_to(ROOT)}")

    urls = "\n".join(f"  <url><loc>{SITE_URL}{city['path']}</loc></url>" for city in cities)
    (SITE / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>\n',
        encoding="utf-8",
    )
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n", encoding="utf-8")
    print("Wrote site/sitemap.xml, site/robots.txt")


if __name__ == "__main__":
    main()
