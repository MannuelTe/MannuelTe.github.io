#!/usr/bin/env python3
"""Render the map page (site/index.html), the 404 page, sitemap.xml, robots.txt and sources/README.md.

Usage: python3 build_pages.py   (run build_data.py <city> first: figures come from sources/<city>.json)
"""

from __future__ import annotations

import hashlib
import html
import json
from datetime import date
from pathlib import Path
from string import Template

from cities import load_cities

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
SITE_URL = "https://mannuelte.github.io/zurich-temps-transport/"
GITHUB_URL = "https://github.com/MannuelTe/zurich-temps-transport"
UPSTREAM_URL = "https://github.com/camilleroux/montpellier-temps-transport"
SITE_NAME = "How far is it in Zurich?"
LICENCES = {
    "opentransportdata": ("opentransportdata.swiss terms of use", "https://opentransportdata.swiss/en/terms-of-use/"),
}
ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
MODE_NAMES = {"tram": "Tram", "sbahn": "S-Bahn", "funicular": "Funicular", "cable": "Cable car"}

esc = html.escape


def text_color(background: str) -> str:
    """Black or white text, whichever reads best on a line colour (yellow lines need black)."""
    value = int(background.lstrip("#")[:6] or "888888", 16)
    luminance = 0.299 * (value >> 16) + 0.587 * ((value >> 8) & 255) + 0.114 * (value & 255)
    return "#111" if luminance > 150 else "#fff"


def line_badge(color: str, name: str) -> str:
    return f'<span class="line-badge" style="background:{color};color:{text_color(color)}">{esc(name)}</span>'


def people(value: int) -> str:
    return f"{value / 1000:,.0f}k" if value >= 10_000 else f"{value:,}"


def num(value: float) -> str:
    return f"{value:g}"


def short_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.exists() else "0"


def long_date(value: str, weekday: bool = False) -> str:
    day = date.fromisoformat(value[:10])
    text = f"{day.day} {day:%B} {day.year}"
    return f"{day:%A} {text}" if weekday else text


def json_ld(data: dict) -> str:
    body = json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
    return '    <script type="application/ld+json">\n    ' + body.replace("\n", "\n    ") + "\n    </script>"


def head(*, title: str, description: str, url: str, base: str, graph: list) -> str:
    return "\n".join(
        [
            '    <meta charset="utf-8" />',
            '    <meta name="viewport" content="width=device-width, initial-scale=1" />',
            f"    <title>{esc(title)}</title>",
            f'    <meta name="description" content="{esc(description)}" />',
            f'    <link rel="canonical" href="{url}" />',
            '    <meta name="theme-color" content="#3aa70b" />',
            f'    <link rel="icon" href="{base}favicon.svg" type="image/svg+xml" />',
            f'    <link rel="icon" href="{base}favicon-32.png" type="image/png" sizes="32x32" />',
            f'    <link rel="apple-touch-icon" href="{base}apple-touch-icon.png" />',
            '    <meta property="og:type" content="website" />',
            '    <meta property="og:locale" content="en_GB" />',
            f'    <meta property="og:site_name" content="{esc(SITE_NAME)}" />',
            f'    <meta property="og:title" content="{esc(title.split(" · ")[0])}" />',
            f'    <meta property="og:description" content="{esc(description)}" />',
            f'    <meta property="og:url" content="{url}" />',
            json_ld({"@context": "https://schema.org", "@graph": graph}),
            '    <link rel="preconnect" href="https://fonts.bunny.net" />',
            '    <link rel="stylesheet" href="https://fonts.bunny.net/css?family=inter:400,500,600,700,800" />',
        ]
    )


def header(base: str) -> str:
    return f"""    <header class="topbar">
      <nav class="topbar-inner" aria-label="Main navigation">
        <a class="brand" href="{base}"><img src="{base}favicon.svg" width="22" height="22" alt="" /> {esc(SITE_NAME)}</a>
        <div class="topbar-links">
          <a class="topbar-link" href="{GITHUB_URL}">Source on GitHub</a>
        </div>
      </nav>
    </header>"""


def footer(city: dict) -> str:
    return f"""    <footer class="site-footer">
      <div class="footer-inner">
        <p class="footer-author">
          A Zurich fork of <a href="https://tram.camilleroux.com/">À portée de tram</a> by
          <a href="https://www.camilleroux.com/">Camille Roux</a>
          (<a href="{UPSTREAM_URL}">code</a>), itself based on Anthony Castrio's
          <a href="https://castrio.me/nyc/">NYC Transit Time Cartogram</a> and Jules Grandin's
          <a href="https://julesgrandin.github.io/paris-temps-transport/">Paris version</a>.
        </p>
        <p class="footer-links">
          <a href="{GITHUB_URL}" rel="noopener">Source code on GitHub</a> ·
          <a href="{GITHUB_URL}/issues" rel="noopener">Report an error</a>
        </p>
        <p class="footer-credits">
          Timetables: <a href="{esc(city["gtfsDataset"])}">Swiss national GTFS</a> (opentransportdata.swiss).
          Municipal boundaries: <a href="https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d">swissBOUNDARIES3D</a>
          (swisstopo); districts: <a href="https://data.stadt-zuerich.ch/">Stadt Zürich Open Data</a>.
          Lines, water and parks © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>.
          Address search: <a href="https://api3.geo.admin.ch/">geo.admin.ch</a>.
          Derived data under <a href="{ODBL_URL}">ODbL</a>, code under the MIT licence.
        </p>
      </div>
    </footer>"""


def faq_block(entries: list[tuple]) -> str:
    return "\n".join(f'        <details class="faq"><summary>{esc(q)}</summary><p>{esc(a)}</p></details>' for q, a in entries)


def faq_schema(entries: list[tuple]) -> dict:
    return {
        "@type": "FAQPage",
        "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in entries],
    }


def city_faq(city: dict) -> list[tuple]:
    stats, sources = city["stats"], city["sources"]
    period = sources["gtfs"].get("servicePeriod") or [None, None]
    fetched = sources["gtfs"].get("fetchedAt")
    return [
        (
            f"How long does it take to cross {city['name']} by public transport?",
            f"From {stats['center']}, {stats['within15']}% of the {stats['railStations']} {city['railStations']} on the map are "
            f"less than 15 minutes away and {stats['within30']}% less than 30 minutes, walking and waiting included. "
            f"The farthest, {stats['farthestStation']}, takes about {stats['farthestMinutes']} minutes.",
        ),
        (
            "Where do the timetables come from?",
            "From the official Swiss timetable published by opentransportdata.swiss (GTFS), clipped to the Zurich area"
            + (f", downloaded on {long_date(fetched)}" if fetched else "")
            + (f" and valid until {long_date(period[1])}" if period[1] else "")
            + f". Times are those of {long_date(sources['referenceDate'], weekday=True)}, between 7:00 and 20:00.",
        ),
        (
            "Are buses included?",
            f"Yes, as an option: tick « {city['busLabel']} » under the map. By default only trams, S-Bahn and trains, "
            "funiculars and the Adliswil–Felsenegg cable car are used. Waits at rarely served stops are capped at 15 minutes.",
        ),
        (
            "How are travel times computed?",
            "Walk to the stop at 4.5 km/h in a straight line, wait half the interval between two departures, ride for the "
            "scheduled time between stops, change with 1.5 minutes of walking, and allow 1.5 minutes to reach a railway "
            "platform. No real-time data or disruptions: this is the city « on paper ».",
        ),
        (
            "Where do the resident and job figures come from?",
            "From the Federal Statistical Office's hectare grids: STATPOP 2024 (permanent residents) and STATENT 2023 "
            "(employees, all sectors). Each 100 m square is added to the 200 m map cell it falls in; small counts are "
            "rounded by the BFS for privacy, so totals are approximate. The table under the map sums the cells reached "
            "within each isochrone.",
        ),
        (
            "Why do some tram lines look unusual?",
            "The timetable is the published one for the reference day, construction diversions included (lines 50 and 51 "
            "in 2026, for example).",
        ),
    ]


def history(city: dict) -> tuple[str, str, dict]:
    """One row per timetable year, from sources/<city>-<year>.json, and the year picker's config."""
    rows, timetables = [], {}
    for year, timetable in city["timetables"].items():
        path = ROOT / "sources" / f"{city['slug']}-{year}.json"
        if not path.exists():
            print(f"  {year} skipped: run build_data.py {city['slug']} {year} first")
            continue
        sources = json.loads(path.read_text(encoding="utf-8"))
        stats = sources["stats"]
        trams = [line["name"] for line in stats["lines"] if line["mode"] == "tram" and line["name"].isdigit()]
        timetables[year] = {
            "version": short_hash(SITE / "data" / f"{city['slug']}-{year}.json"),
            "label": f"{'Draft ' if timetable.get('draft') else ''}{year} timetable, {long_date(sources['referenceDate'], weekday=True)}",
        }
        rows.append(
            f"            <tr><td><strong>{year}</strong>{' (draft)' if timetable.get('draft') else ''}</td>"
            f"<td>{long_date(sources['referenceDate'])}</td><td>{len(trams)}</td><td>{stats['railStations']}</td>"
            f"<td>{stats['within30']}%</td><td>{people(stats['population'].get('30', 0))}</td><td>{people(stats['jobs'].get('30', 0))}</td></tr>"
        )
    note = (
        "2022: before the Limmattalbahn's second stage (tram 20 to Killwangen, December 2022). 2026: tram diversions "
        "for construction work (temporary lines 50 and 51). 2027: first published version of the timetable, with fewer "
        "trips than the final one. 2022 and 2024 come from the Mobility Database's archive of the Swiss feed."
    )
    return "\n".join(rows), note, timetables


def render_page(template: Template, city: dict) -> str:
    url = SITE_URL
    base = "./"
    stats = city["stats"]
    description = (
        f"Travel time map of {city['name']} by tram, S-Bahn and bus: pick a starting point and the whole city is coloured "
        "by how long it takes to get there."
    )
    faq = city_faq(city)
    graph = [
        {
            "@type": "WebApplication",
            "name": city["title"],
            "url": url,
            "description": description,
            "inLanguage": "en",
            "applicationCategory": "TravelApplication",
            "operatingSystem": "Web",
            "isAccessibleForFree": True,
            "spatialCoverage": {"@type": "Place", "name": city["region"]},
            "isBasedOn": ["https://tram.camilleroux.com/", "https://castrio.me/nyc/"],
            "datePublished": city["published"],
            "dateModified": city["sources"]["builtAt"][:10],
        },
        faq_schema(faq),
    ]
    table_lines = [line for line in stats["lines"] if any(c.isdigit() for c in line["name"])]
    trams = [line for line in table_lines if line["mode"] == "tram"]
    fastest = min(trams or table_lines, key=lambda line: line["headway"])
    tiles = [
        (f"{stats['within30']}%", f"of the {city['railStations']} within 30 min of {stats['center']}"),
        (str(stats["railStations"]), f"{city['railStations']} on the map"),
        (f"{num(fastest['headway'])} min", f"between two trams on line {fastest['name']}, the most frequent"),
        (f"{stats['farthestMinutes']} min", f"from {stats['center']} to {stats['farthestStation']}, the farthest stop"),
        (people(stats["population"]["total"]), "residents on the map (STATPOP 2024)"),
        (people(stats["jobs"]["total"]), "jobs on the map (STATENT 2023)"),
        (people(stats["population"].get("30", 0)), f"residents within 30 min of {stats['center']} by tram and train"),
        (people(stats["jobs"].get("30", 0)), f"jobs within 30 min of {stats['center']} by tram and train"),
    ]
    stat_tiles = "\n".join(f'          <div class="stat"><strong>{esc(value)}</strong><span>{esc(label)}</span></div>' for value, label in tiles)
    line_rows = "\n".join(
        f'            <tr><td>{line_badge(line["color"], line["name"])} {esc(MODE_NAMES.get(line["mode"], ""))}</td>'
        f'<td>{line["stations"]}</td><td>~{num(line["headway"])} min</td></tr>'
        for line in table_lines
    )
    history_rows, history_note, timetables = history(city)
    year_buttons = "\n".join(
        f'              <button type="button" data-year="{year}" aria-pressed="{str(year == city["defaultTimetable"]).lower()}">{year}</button>'
        for year in timetables
    )
    config = {
        "slug": city["slug"],
        "name": city["name"],
        "timetables": timetables,
        "defaultTimetable": city["defaultTimetable"],
        "defaultFrom": city["defaultFrom"],
        "railNoun": city["railNoun"],
        "railStations": city["railStations"],
        "busNoun": city["busNoun"],
        "searchBbox": city["searchBboxLV95"],
    }
    values = {
        "head": head(title=f"{city['title']} · {city['titleSuffix']}", description=description, url=url, base=base, graph=graph),
        "header": header(base),
        "footer": footer(city),
        "base": base,
        "city_config": json.dumps(config, ensure_ascii=False).replace("</", "<\\/"),
        "headline": esc(city["title"]),
        "name": esc(city["name"]),
        "region_lower": esc("the " + city["region"]),
        "rail_label": esc(city["railLabel"]),
        "bus_label": esc(city["busLabel"]),
        "search_example": esc(city["searchExample"]),
        "network": esc(city["network"]),
        "stat_tiles": stat_tiles,
        "line_rows": line_rows,
        "year_buttons": year_buttons,
        "history_rows": history_rows,
        "history_note": esc(history_note),
        "center": esc(stats["center"]),
        "faq_html": faq_block(faq),
        "styles_version": short_hash(SITE / "styles.css"),
        "app_version": short_hash(SITE / "app.js"),
    }
    return template.substitute(values)


def render_404() -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Page not found · {esc(SITE_NAME)}</title>
    <meta name="robots" content="noindex" />
    <link rel="stylesheet" href="https://fonts.bunny.net/css?family=inter:400,500,600,700,800" />
    <link rel="stylesheet" href="{SITE_URL}styles.css?v={short_hash(SITE / 'styles.css')}" />
  </head>
  <body>
{header(SITE_URL)}
    <main class="page">
      <section class="hero">
        <h1>End of the line!</h1>
        <p class="lede">This page does not exist. <a href="{SITE_URL}">Back to the map</a>.</p>
      </section>
    </main>
  </body>
</html>
"""


def write_sources_readme(city: dict) -> None:
    lines = [
        "# Data provenance",
        "",
        "Generated by `build_pages.py` from `sources/<city>.json`.",
        "",
        "| Timetable | Network | Licence | GTFS downloaded | GTFS validity | Reference day |",
        "|---|---|---|---|---|---|",
    ]
    for year in city["timetables"]:
        path = ROOT / "sources" / f"{city['slug']}-{year}.json"
        if not path.exists():
            continue
        sources = json.loads(path.read_text(encoding="utf-8"))
        gtfs = sources["gtfs"]
        period = gtfs.get("servicePeriod") or ["?", "?"]
        lines.append(
            f"| [{city['name']} {year}]({path.name}) | {city['network']} | {LICENCES[city['gtfsLicence']][0]} | "
            f"{gtfs.get('fetchedAt', '?')[:10]} | {period[0]} → {period[1]} | {sources['referenceDate']} |"
        )
    lines.append("")
    (ROOT / "sources" / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    template = Template((ROOT / "templates" / "index.html").read_text(encoding="utf-8"))
    (city,) = load_cities()  # a single-city fork
    city["sources"] = json.loads((ROOT / "sources" / f"{city['slug']}-{city['defaultTimetable']}.json").read_text(encoding="utf-8"))
    city["stats"] = city["sources"]["stats"]
    (SITE / "index.html").write_text(render_page(template, city), encoding="utf-8")
    (SITE / "404.html").write_text(render_404(), encoding="utf-8")
    write_sources_readme(city)
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{SITE_URL}</loc><lastmod>{city['sources']['builtAt'][:10]}</lastmod></url>\n</urlset>\n",
        encoding="utf-8",
    )
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n", encoding="utf-8")
    print("Wrote site/index.html, site/404.html, site/sitemap.xml, site/robots.txt, sources/README.md")


if __name__ == "__main__":
    main()
