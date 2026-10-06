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
SITE_URL = "https://mannuelte.github.io/"
GITHUB_URL = "https://github.com/MannuelTe/MannuelTe.github.io"
UPSTREAM_URL = "https://github.com/camilleroux/montpellier-temps-transport"
AUTHOR_NAME = "Manuel Trachsler"
AUTHOR_URL = "https://manueltrachsler.ch"
AUTHOR_GITHUB = "https://github.com/MannuelTe"
SITE_NAME = "Zurich Isochrones"
GITHUB_ICON = (
    '<svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8'
    'c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13'
    '-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95'
    ' 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2'
    '-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93'
    '-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>'
)
LICENCES = {
    "opentransportdata": ("opentransportdata.swiss terms of use", "https://opentransportdata.swiss/en/terms-of-use/"),
}
ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
# ZVV's official stop and line timetables; its per-line pages need codes that change each timetable year.
LINE_TIMETABLES_URL = "https://online.fahrplaninfo.zvv.ch/"
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
            '    <meta name="theme-color" content="#0f05a0" />',
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
      <a class="brand" href="{base}"><img src="{base}favicon.svg" width="22" height="22" alt="" /> {esc(SITE_NAME)}</a>
      <nav class="topbar-links" aria-label="Main navigation">
        <a href="#map-title">Map</a>
        <a href="#history-title">Over time</a>
        <a href="#faq-title">Questions</a>
        <a class="topbar-pill github-link" href="{GITHUB_URL}" aria-label="GitHub" title="GitHub">{GITHUB_ICON}</a>
      </nav>
    </header>"""


def footer(city: dict) -> str:
    return f"""    <footer class="site-footer">
      <div class="footer-inner">
        <div class="footer-top">
          <p class="footer-author">Made by <a href="{AUTHOR_URL}">{esc(AUTHOR_NAME)}</a></p>
          <nav class="footer-links" aria-label="Footer">
            <a href="{AUTHOR_URL}">manueltrachsler.ch</a>
            <a class="github-link" href="{AUTHOR_GITHUB}" rel="noopener" aria-label="GitHub" title="GitHub">{GITHUB_ICON}</a>
            <a href="{GITHUB_URL}" rel="noopener">Source</a>
            <a href="{GITHUB_URL}/issues" rel="noopener">Report an error</a>
          </nav>
        </div>
        <p class="footer-credits">
          Fork of <a href="https://tram.camilleroux.com/">À portée de tram</a> by Camille Roux
          (<a href="{UPSTREAM_URL}">code</a>), after <a href="https://castrio.me/nyc/">Anthony Castrio</a> and
          <a href="https://julesgrandin.github.io/paris-temps-transport/">Jules Grandin</a>.
          Data: <a href="{esc(city["gtfsDataset"])}">Swiss GTFS</a> ·
          <a href="https://www.bfs.admin.ch/bfs/en/home/statistics/catalogues-databases.html">BFS STATPOP/STATENT</a> ·
          <a href="https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d">swisstopo</a> ·
          <a href="https://data.stadt-zuerich.ch/">Stadt Zürich</a> ·
          © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ·
          <a href="https://api3.geo.admin.ch/">geo.admin.ch</a>.
          Timetable on paper, no delays; figures approximate. Data <a href="{ODBL_URL}">ODbL</a>, code MIT.
        </p>
      </div>
    </footer>"""


def faq_block(entries: list[tuple]) -> str:
    return "\n".join(f'        <details class="expander faq"><summary>{esc(q)}</summary><div class="expander-body"><p>{esc(a)}</p></div></details>' for q, a in entries)


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
            "platform. On a section that several lines share (HB to Hardbrücke, tram trunks) the rider takes whichever comes first and changes where the lines split; a stop served by one line waits for that line. No real-time data or "
            "disruptions: this is the city « on paper ».",
        ),
        (
            "Do hills and the lake count?",
            "Yes. Walking is slower uphill and a little faster on gentle descents (Tobler's hiking function on swisstopo "
            "elevation), and no walk crosses the Zürichsee, Greifensee or Türlersee. Rivers count as crossable, since "
            "bridges are close together in town.",
        ),
        (
            "How are bike times computed?",
            "On the streets open to bicycles in OpenStreetMap, one-way streets included where cycling against the "
            "traffic is allowed: 20 km/h on the flat, about 10 km/h on a 5 % climb, 25 km/h on a 3 % descent and 30 km/h on 5 % or steeper, plus a "
            "minute to unlock and a minute to park. The street network comes from the GIS_playground_ZH cycling project.",
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
    rows, timetables, figures = [], {}, []
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
        figures.append({
            "year": year,
            "draft": bool(timetable.get("draft")),
            "day": long_date(sources["referenceDate"], weekday=True),
            "population": stats["population"],
            "jobs": stats["jobs"],
            "within15": stats["within15"],
            "within30": stats["within30"],
            "railStations": stats["railStations"],
            "tramLines": len(trams),
        })
        rows.append(
            f"            <tr><td><strong>{year}</strong>{' (draft)' if timetable.get('draft') else ''}</td>"
            f"<td>{long_date(sources['referenceDate'])}</td><td>{len(trams)}</td><td>{stats['railStations']}</td>"
            f"<td>{stats['within30']}%</td><td>{people(stats['population'].get('30', 0))}</td><td>{people(stats['jobs'].get('30', 0))}</td></tr>"
        )
    note = (
        "2022: before the Limmattalbahn's second stage (tram 20 to Killwangen, December 2022). 2026: the SZU lines S4 and "
        "S10 start at Zürich Selnau instead of Zürich HB (works on the HB–Selnau section), which puts the Sihltal and "
        "Uetliberg 10–15 minutes further from HB, and trams run diversions for construction work (temporary lines 50 "
        "and 51); most other stops got slightly faster. 2027: first published version of the timetable, with fewer "
        "trips than the final one. 2022 and 2024 come from the Mobility Database's archive of the Swiss feed."
    )
    return "\n".join(rows), note, timetables, figures


def note_expander(summary: str, paragraphs: list[str]) -> str:
    body = "".join(f"<p>{esc(p)}</p>" for p in paragraphs)
    return f'        <details class="expander data-note"><summary>{esc(summary)}</summary><div class="expander-body">{body}</div></details>'


def reach_notes(city: dict) -> str:
    """What the resident and job counts under the map measure, and what they do not."""
    stats = city["stats"]
    return note_expander("What these resident and job counts mean", [
        f"Residents are the BFS STATPOP 2024 grid (permanent residents, by home address); jobs are the STATENT 2023 grid "
        f"(employees of all sectors, counted where they work). The map covers the {city['region'][0].lower()}{city['region'][1:]}: "
        f"{people(stats['population']['total'])} residents and {people(stats['jobs']['total'])} jobs in all, more than the "
        "city alone.",
        "Jobs are people employed, not full-time equivalents: a part-timer counts as one job, so in a city with much "
        "part-time work the figure overstates the amount of work. Staff are counted at the workplace their employer "
        "registers, which for firms with several sites is not always where they actually sit.",
        "The BFS rounds small hectare counts for privacy, and each 100 m hectare goes to the 200 m map cell its centre "
        "falls in, so figures for a few streets are rough; totals over a whole isochrone hold up much better.",
        "The grids are the same for every timetable year. Switching years changes only how far the network reaches, "
        "not where people live or work.",
    ])


def history_notes(figures: list[dict]) -> str:
    """Why residents within 30 min of the centre move between years, the 2026 dip in particular."""
    by_year = {f["year"]: f for f in figures}
    paragraphs = [
        "Residents and jobs are held at the STATPOP 2024 and STATENT 2023 figures in every year, so a change in the "
        "table is a change in the network: a fall means part of the map takes longer to reach, not that people left.",
    ]
    if "2024" in by_year and "2026" in by_year:
        before, after = by_year["2024"], by_year["2026"]
        paragraphs.append(
            f"2024 → 2026: residents within 30 min go from {before['population']['30']:,} to {after['population']['30']:,} "
            f"({after['population']['30'] - before['population']['30']:+,}), jobs from {before['jobs']['30']:,} to "
            f"{after['jobs']['30']:,}. The SZU lines S4 and S10 no longer call at Zürich HB SZU but start at Selnau (works "
            "on the HB–Selnau section), so the Sihltal and Uetliberg need an extra change from HB and slip past 30 minutes; "
            "trams also run construction diversions (temporary lines 50 and 51). The core got faster at the same time: "
            f"residents within 15 min went from {before['population']['15']:,} to {after['population']['15']:,}."
        )
    paragraphs.append(
        "Each year is one reference weekday of the published timetable, 7:00–20:00, with no delays or disruptions, "
        "so a diversion running on that day shows up in that year's figures."
    )
    return note_expander("Why the counts change from year to year", paragraphs)


def fetched(entry: dict | None) -> str:
    return f", downloaded {long_date(entry['fetchedAt'])}" if entry and entry.get("fetchedAt") else ""


def sources_block(city: dict) -> str:
    """Every input with its link, licence, download date and use: the « Data sources » expander under the map."""
    main = city["sources"]
    years = []
    for year, timetable in city["timetables"].items():
        path = ROOT / "sources" / f"{city['slug']}-{year}.json"
        if path.exists():
            gtfs = json.loads(path.read_text(encoding="utf-8"))["gtfs"]
            period = gtfs.get("servicePeriod") or ["?", "?"]
            years.append(
                f'<li><a href="{esc(timetable["dataset"])}">{esc(timetable.get("note") or year)}</a>, valid '
                f"{esc(period[0])} → {esc(period[1])}{esc(fetched(gtfs))}.</li>"
            )
    osm = main.get("openStreetMap", {})
    items = [
        ("Timetables (GTFS)",
         f'Swiss national timetable, published by <a href="https://opentransportdata.swiss/">opentransportdata.swiss</a> '
         f'(<a href="{LICENCES["opentransportdata"][1]}">terms of use</a>); older years from the '
         f'<a href="https://mobilitydatabase.org/">Mobility Database</a> archive. Clipped to the Zurich area.<ul>{"".join(years)}</ul>'),
        ("Municipal and district boundaries",
         f'<a href="https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d">swissBOUNDARIES3D</a> (swisstopo, '
         f'via api3.geo.admin.ch) and the 12 Stadtkreise from <a href="https://data.stadt-zuerich.ch/">Stadt Zürich Open Data</a>'
         f'{esc(fetched(main.get("boundaries")))}. Open government data.'),
        ("Residents",
         f'<a href="https://www.bfs.admin.ch/bfs/en/home/statistics/catalogues-databases.assetdetail.36171301.html">'
         f'STATPOP 2024</a> hectare grid, Federal Statistical Office (BFS){esc(fetched(main.get("population")))}.'),
        ("Jobs",
         f'<a href="https://www.bfs.admin.ch/bfs/en/home/statistics/catalogues-databases.assetdetail.36073031.html">'
         f'STATENT 2023</a> hectare grid (employees, all sectors), BFS{esc(fetched(main.get("jobs")))}.'),
        ("Elevation",
         f'swisstopo terrain model (swissALTI3D / DHM25) sampled every 100 m through the '
         f'<a href="https://api3.geo.admin.ch/services/sdiservices.html#profile">geo.admin.ch profile service</a>'
         f'{esc(fetched(main.get("elevation")))}.'),
        ("Lines, lakes, parks and cycling streets",
         f'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> (ODbL), via the Overpass API'
         f'{esc(fetched(osm.get("osm_water_parks")))}.'),
        ("Base map",
         'Faint street layer drawn from the same OpenStreetMap network as the bike layer (major roads, streets and '
         'paths; service roads left out), © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap '
         'contributors</a>. Untick « Streets » to hide it.'),
        ("Bus lines",
         'Drawn from OpenStreetMap bus and trolleybus route relations, matched to the timetable by line number (the Swiss '
         'GTFS has no line shapes); shown with « Bus and boat ».'),
        ("Address search",
         'Live queries to the <a href="https://api3.geo.admin.ch/services/sdiservices.html#search">geo.admin.ch search service</a> '
         "(swisstopo) from your browser."),
        ("Method",
         f'Fork of <a href="{UPSTREAM_URL}">À portée de tram</a> by Camille Roux (MIT). Full provenance (URLs, SHA-256, '
         f'dates) in <a href="{GITHUB_URL}/tree/main/sources">sources/</a>.'),
    ]
    rows = "\n".join(f"          <dt>{esc(title)}</dt><dd>{body}</dd>" for title, body in items)
    return f"""        <details class="expander sources">
          <summary>Data sources</summary>
          <div class="expander-body">
            <dl>
{rows}
            </dl>
          </div>
        </details>"""


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
    # Two rows of four: the network, then the people; each column has its own colour.
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
    stat_html = [f'          <div class="stat"><strong>{esc(value)}</strong><span>{esc(label)}</span></div>' for value, label in tiles]
    if stats.get("runnerUpStation"):
        # Easter egg: hovering the farthest stop reveals the runner-up.
        stat_html[3] = (
            '          <div class="stat stat-egg" tabindex="0">'
            f'<div class="stat-face"><strong>{esc(tiles[3][0])}</strong><span>{esc(tiles[3][1])}</span></div>'
            f'<div class="stat-face stat-egg-face"><strong>{stats["runnerUpMinutes"]} min</strong>'
            f'<span>{esc(stats["runnerUpStation"])} is the runner-up. So close.</span></div></div>'
        )
    stat_tiles = "\n".join(stat_html)
    line_rows = "\n".join(
        f'            <tr><td>{line_badge(line["color"], line["name"])} {esc(MODE_NAMES.get(line["mode"], ""))}</td>'
        f'<td>{line["stations"]}</td><td>~{num(line["headway"])} min</td>'
        f'<td><a href="{LINE_TIMETABLES_URL}" rel="noopener" aria-label="Official ZVV timetable for line {esc(line["name"])}">ZVV timetable</a></td></tr>'
        for line in table_lines
    )
    history_rows, history_note, timetables, figures = history(city)
    year_buttons = "\n".join(
        f'              <button type="button" data-year="{year}" aria-pressed="{str(year == city["defaultTimetable"]).lower()}">{year}</button>'
        for year in timetables
    )
    config = {
        "slug": city["slug"],
        "name": city["name"],
        "timetables": timetables,
        "defaultTimetable": city["defaultTimetable"],
        "history": figures,
        "center": stats["center"],
        "bikeVersion": short_hash(SITE / "data" / f"{city['slug']}-bike.json"),
        "streetsVersion": short_hash(SITE / "data" / f"{city['slug']}-streets.json"),
        "busesVersion": short_hash(SITE / "data" / f"{city['slug']}-buses.json"),
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
        "reach_notes": reach_notes(city),
        "history_notes": history_notes(figures),
        "center": esc(stats["center"]),
        "default_year": esc(city["defaultTimetable"]),
        "faq_html": faq_block(faq) + "\n" + sources_block(city),
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
