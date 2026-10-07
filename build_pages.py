#!/usr/bin/env python3
"""Render the map page in German (site/index.html, the default) and English (site/en/index.html), the 404 page,
sitemap.xml, robots.txt and sources/README.md.

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
SITE_NAMES = {"en": "Zurich Isochrones", "de": "Zürich Isochronen"}
LANGS = ("de", "en")  # German first: it is the default page at the site root
LANG = "de"  # the language being rendered, set by main() before each page


def t(en: str, de: str) -> str:
    """The English or German text, whichever page is being rendered."""
    return de if LANG == "de" else en


def page_path(lang: str) -> str:
    """Where each language lives, relative to the site root: German at the root, English under en/."""
    return "" if lang == "de" else f"{lang}/"


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
MONTHS_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
# ZVV's official stop and line timetables; its per-line pages need codes that change each timetable year.
LINE_TIMETABLES_URL = "https://online.fahrplaninfo.zvv.ch/"
MODE_NAMES = {
    "en": {"tram": "Tram", "sbahn": "S-Bahn", "funicular": "Funicular", "cable": "Cable car"},
    "de": {"tram": "Tram", "sbahn": "S-Bahn", "funicular": "Standseilbahn", "cable": "Luftseilbahn"},
}

esc = html.escape


def text_color(background: str) -> str:
    """Black or white text, whichever reads best on a line colour (yellow lines need black)."""
    value = int(background.lstrip("#")[:6] or "888888", 16)
    luminance = 0.299 * (value >> 16) + 0.587 * ((value >> 8) & 255) + 0.114 * (value & 255)
    return "#111" if luminance > 150 else "#fff"


def line_badge(color: str, name: str) -> str:
    return f'<span class="line-badge" style="background:{color};color:{text_color(color)}">{esc(name)}</span>'


def thousands(value: int) -> str:
    """1,234 in English, 1'234 in German (Swiss style, as the browser's de-CH formatting)."""
    return f"{value:,}".replace(",", t(",", "'"))


def people(value: int) -> str:
    if value < 10_000:
        return thousands(value)
    return t(f"{value / 1000:,.0f}k", thousands(round(value, -3)))


def num(value: float) -> str:
    return t(f"{value:g}", f"{value:g}".replace(".", ","))


def short_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.exists() else "0"


def long_date(value: str, weekday: bool = False) -> str:
    day = date.fromisoformat(value[:10])
    if LANG == "de":
        text = f"{day.day}. {MONTHS_DE[day.month - 1]} {day.year}"
        return f"{WEEKDAYS_DE[day.weekday()]}, {text}" if weekday else text
    text = f"{day.day} {day:%B} {day.year}"
    return f"{day:%A} {text}" if weekday else text


def json_ld(data: dict) -> str:
    body = json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
    return '    <script type="application/ld+json">\n    ' + body.replace("\n", "\n    ") + "\n    </script>"


def head(*, title: str, description: str, url: str, base: str, graph: list) -> str:
    alternates = [f'    <link rel="alternate" hreflang="{lang}" href="{SITE_URL}{page_path(lang)}" />' for lang in LANGS]
    alternates.append(f'    <link rel="alternate" hreflang="x-default" href="{SITE_URL}" />')
    return "\n".join(
        [
            '    <meta charset="utf-8" />',
            '    <meta name="viewport" content="width=device-width, initial-scale=1" />',
            f"    <title>{esc(title)}</title>",
            f'    <meta name="description" content="{esc(description)}" />',
            f'    <link rel="canonical" href="{url}" />',
            *alternates,
            '    <meta name="theme-color" content="#0f05a0" />',
            f'    <link rel="icon" href="{base}favicon.svg" type="image/svg+xml" />',
            f'    <link rel="icon" href="{base}favicon-32.png" type="image/png" sizes="32x32" />',
            f'    <link rel="apple-touch-icon" href="{base}apple-touch-icon.png" />',
            '    <meta property="og:type" content="website" />',
            f'    <meta property="og:locale" content="{t("en_GB", "de_CH")}" />',
            f'    <meta property="og:site_name" content="{esc(SITE_NAMES[LANG])}" />',
            f'    <meta property="og:title" content="{esc(title.split(" · ")[0])}" />',
            f'    <meta property="og:description" content="{esc(description)}" />',
            f'    <meta property="og:url" content="{url}" />',
            json_ld({"@context": "https://schema.org", "@graph": graph}),
            '    <link rel="preconnect" href="https://fonts.bunny.net" />',
            '    <link rel="stylesheet" href="https://fonts.bunny.net/css?family=inter:400,500,600,700,800" />',
        ]
    )


def header(base: str, home: str | None = None) -> str:
    """Top bar; `home` is this language's page (the brand link), `base` the site root (shared files)."""
    home = home or base
    other = "en" if LANG == "de" else "de"
    switch = f'{base}{page_path(other)}'
    return f"""    <header class="topbar">
      <a class="brand" href="{home}"><img src="{base}favicon.svg" width="22" height="22" alt="" /> {esc(SITE_NAMES[LANG])}</a>
      <nav class="topbar-links" aria-label="{t("Main navigation", "Hauptnavigation")}">
        <a href="{home}#map-title">{t("Map", "Karte")}</a>
        <a href="{home}#history-title">{t("Over time", "Im Wandel")}</a>
        <a href="{home}#faq-title">{t("Questions", "Fragen")}</a>
        <a class="topbar-pill lang-switch" href="{switch}" hreflang="{other}" lang="{other}" title="{t("Deutsch", "English")}">{other.upper()}</a>
        <a class="topbar-pill github-link" href="{GITHUB_URL}" aria-label="GitHub" title="GitHub">{GITHUB_ICON}</a>
      </nav>
    </header>"""


def footer(city: dict) -> str:
    return f"""    <footer class="site-footer">
      <div class="footer-inner">
        <div class="footer-top">
          <p class="footer-author">{t("Made by", "Gemacht von")} <a href="{AUTHOR_URL}">{esc(AUTHOR_NAME)}</a></p>
          <nav class="footer-links" aria-label="Footer">
            <a href="{AUTHOR_URL}">manueltrachsler.ch</a>
            <a href="{GITHUB_URL}" rel="noopener">{t("Source", "Quellcode")}</a>
            <a href="{GITHUB_URL}/issues" rel="noopener">{t("Report an error", "Fehler melden")}</a>
          </nav>
        </div>
        <p class="footer-credits">
          {t("Fork of", "Fork von")} <a href="https://tram.camilleroux.com/">À portée de tram</a> {t("by", "von")} Camille Roux
          (<a href="{UPSTREAM_URL}">code</a>), {t("after", "nach")} <a href="https://castrio.me/nyc/">Anthony Castrio</a> {t("and", "und")}
          <a href="https://julesgrandin.github.io/paris-temps-transport/">Jules Grandin</a>.
          {t("Data", "Daten")}: <a href="{esc(city["gtfsDataset"])}">{t("Swiss GTFS", "Schweizer GTFS")}</a> ·
          <a href="https://www.bfs.admin.ch/bfs/{t("en", "de")}/home/statistics/catalogues-databases.html">BFS STATPOP/STATENT</a> ·
          <a href="https://www.swisstopo.admin.ch/{t("en", "de")}/landscape-model-swissboundaries3d">swisstopo</a> ·
          <a href="https://data.stadt-zuerich.ch/">Stadt Zürich</a> ·
          © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ·
          <a href="https://api3.geo.admin.ch/">geo.admin.ch</a>.
          {t("Timetable on paper, no delays; figures approximate. Data", "Fahrplan auf Papier, ohne Verspätungen; Zahlen gerundet. Daten")} <a href="{ODBL_URL}">ODbL</a>, {t("code", "Code")} MIT.
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
    reference = long_date(sources["referenceDate"], weekday=True)
    return [
        (
            t(f"How long does it take to cross {city['name']} by public transport?",
              f"Wie lange dauert es, {city['name']} mit dem ÖV zu durchqueren?"),
            t(f"From {stats['center']}, {stats['within15']}% of the {stats['railStations']} {city['railStations']} on the map are "
              f"less than 15 minutes away and {stats['within30']}% less than 30 minutes, walking and waiting included. "
              f"The farthest, {stats['farthestStation']}, takes about {stats['farthestMinutes']} minutes.",
              f"Ab {stats['center']} sind {stats['within15']}% der {stats['railStations']} {city['railStations']} auf der Karte "
              f"weniger als 15 Minuten entfernt und {stats['within30']}% weniger als 30 Minuten, Fussweg und Wartezeit inbegriffen. "
              f"Am weitesten entfernt ist {stats['farthestStation']} mit rund {stats['farthestMinutes']} Minuten."),
        ),
        (
            t("Where do the timetables come from?", "Woher stammen die Fahrpläne?"),
            t("From the official Swiss timetable published by opentransportdata.swiss (GTFS), clipped to the Zurich area"
              + (f", downloaded on {long_date(fetched)}" if fetched else "")
              + (f" and valid until {long_date(period[1])}" if period[1] else "")
              + f". Times are those of {reference}, between 7:00 and 20:00.",
              "Aus dem offiziellen Schweizer Fahrplan von opentransportdata.swiss (GTFS), auf den Raum Zürich zugeschnitten"
              + (f", heruntergeladen am {long_date(fetched)}" if fetched else "")
              + (f" und gültig bis {long_date(period[1])}" if period[1] else "")
              + f". Die Zeiten sind jene von {reference}, zwischen 7 und 20 Uhr."),
        ),
        (
            t("Are buses included?", "Sind Busse enthalten?"),
            t(f"Yes, as an option: tick « {city['busLabel']} » under the map. By default only trams, S-Bahn and trains, "
              "funiculars and the Adliswil–Felsenegg cable car are used. Waits at rarely served stops are capped at 15 minutes.",
              f"Ja, als Option: «{city['busLabel']}» unter der Karte ankreuzen. Standardmässig zählen nur Tram, S-Bahn und Züge, "
              "die Standseilbahnen und die Luftseilbahn Adliswil–Felsenegg. Wartezeiten an selten bedienten Haltestellen "
              "sind auf 15 Minuten begrenzt."),
        ),
        (
            t("How are travel times computed?", "Wie werden die Reisezeiten berechnet?"),
            t("Walk to the stop at 4.5 km/h in a straight line, wait half the interval between two departures, ride for the "
              "scheduled time between stops, change with 1.5 minutes of walking, and allow 1.5 minutes to reach a railway "
              "platform. On a section that several lines share (HB to Hardbrücke, tram trunks) the rider takes whichever comes "
              "first and changes where the lines split; a stop served by one line waits for that line. No real-time data or "
              "disruptions: this is the city « on paper ».",
              "Zu Fuss mit 4,5 km/h in Luftlinie zur Haltestelle, halbes Intervall zwischen zwei Abfahrten warten, die "
              "fahrplanmässige Zeit zwischen den Haltestellen fahren, mit 1,5 Minuten Fussweg umsteigen und 1,5 Minuten bis "
              "aufs Perron rechnen. Auf Abschnitten, die mehrere Linien teilen (HB bis Hardbrücke, Tram-Stammstrecken), nimmt "
              "man die erste und steigt dort um, wo sich die Linien trennen; an einer Haltestelle mit nur einer Linie wartet "
              "man auf diese. Keine Echtzeitdaten, keine Störungen: die Stadt «auf Papier»."),
        ),
        (
            t("Do hills and the lake count?", "Zählen Hügel und See?"),
            t("Yes. Walking is slower uphill and a little faster on gentle descents (Tobler's hiking function on swisstopo "
              "elevation), and no walk crosses the Zürichsee, Greifensee or Türlersee. Rivers count as crossable, since "
              "bridges are close together in town.",
              "Ja. Bergauf geht man langsamer, auf leichtem Gefälle etwas schneller (Toblers Wanderfunktion auf den "
              "Höhendaten von swisstopo), und kein Fussweg führt über Zürichsee, Greifensee oder Türlersee. Flüsse gelten "
              "als überquerbar, da die Brücken in der Stadt nahe beieinander liegen."),
        ),
        (
            t("How are bike times computed?", "Wie werden die Velozeiten berechnet?"),
            t("On the streets open to bicycles in OpenStreetMap, one-way streets included where cycling against the "
              "traffic is allowed: 20 km/h on the flat, about 10 km/h on a 5 % climb, 25 km/h on a 3 % descent and 30 km/h "
              "on 5 % or steeper, plus a minute to unlock and a minute to park. The street network comes from the "
              "GIS_playground_ZH cycling project.",
              "Auf den für Velos offenen Strassen in OpenStreetMap, Einbahnstrassen inbegriffen, wo Velos in Gegenrichtung "
              "fahren dürfen: 20 km/h in der Ebene, rund 10 km/h bei 5% Steigung, 25 km/h bei 3% Gefälle und 30 km/h ab "
              "5%, dazu je eine Minute zum Aufschliessen und zum Parkieren. Das Strassennetz stammt aus dem Veloprojekt "
              "GIS_playground_ZH."),
        ),
        (
            t("Where do the resident and job figures come from?", "Woher stammen die Einwohner- und Arbeitsplatzzahlen?"),
            t("From the Federal Statistical Office's hectare grids: STATPOP 2024 (permanent residents) and STATENT 2023 "
              "(employees, all sectors). Each 100 m square is added to the 200 m map cell it falls in; small counts are "
              "rounded by the BFS for privacy, so totals are approximate. The table under the map sums the cells reached "
              "within each isochrone.",
              "Aus den Hektarrastern des Bundesamts für Statistik: STATPOP 2024 (ständige Wohnbevölkerung) und STATENT 2023 "
              "(Beschäftigte, alle Sektoren). Jedes 100-m-Quadrat wird der 200-m-Kartenzelle zugeschlagen, in der es liegt; "
              "kleine Zahlen rundet das BFS aus Datenschutzgründen, die Summen sind also Näherungen. Die Tabelle unter der "
              "Karte summiert die Zellen innerhalb jeder Isochrone."),
        ),
        (
            t("Why do some tram lines look unusual?", "Warum sehen manche Tramlinien ungewohnt aus?"),
            t("The timetable is the published one for the reference day, construction diversions included (lines 50 and 51 "
              "in 2026, for example).",
              "Der Fahrplan ist der veröffentlichte für den Stichtag, Umleitungen wegen Bauarbeiten inbegriffen (zum "
              "Beispiel die Linien 50 und 51 im Jahr 2026)."),
        ),
    ]


def regional_jobs(city: dict) -> dict:
    """Per timetable year, the region's jobs in that year's STATENT, or in the latest one published before it."""
    path = ROOT / "sources" / f"{city['slug']}-jobs.json"
    if not path.exists():
        print(f"  regional jobs skipped: run fetch_data.py {city['slug']} --context-only first")
        return {}
    employees = json.loads(path.read_text(encoding="utf-8"))["employees"]
    out = {}
    for year in city["timetables"]:
        known = [y for y in employees if y <= year]
        if known:
            out[year] = {"year": max(known), "total": employees[max(known)]}
    return out


def regional_jobs_source(city: dict) -> dict | None:
    path = ROOT / "sources" / f"{city['slug']}-jobs.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def region_jobs_cell(entry: dict | None) -> str:
    return f'{people(entry["total"])} <span class="muted">({entry["year"]})</span>' if entry else "–"


def history(city: dict) -> tuple[str, str, dict]:
    """One row per timetable year, from sources/<city>-<year>.json, and the year picker's config."""
    rows, timetables, figures = [], {}, []
    region_jobs = regional_jobs(city)
    for year, timetable in city["timetables"].items():
        path = ROOT / "sources" / f"{city['slug']}-{year}.json"
        if not path.exists():
            print(f"  {year} skipped: run build_data.py {city['slug']} {year} first")
            continue
        sources = json.loads(path.read_text(encoding="utf-8"))
        stats = sources["stats"]
        trams = [line["name"] for line in stats["lines"] if line["mode"] == "tram" and line["name"].isdigit()]
        day = long_date(sources["referenceDate"], weekday=True)
        timetables[year] = {
            "version": short_hash(SITE / "data" / f"{city['slug']}-{year}.json"),
            "draft": bool(timetable.get("draft")),
            "label": (t(f"Draft {year} timetable", f"Fahrplanentwurf {year}") if timetable.get("draft")
                      else t(f"{year} timetable", f"Fahrplan {year}")) + f", {day}",
        }
        figures.append({
            "year": year,
            "draft": bool(timetable.get("draft")),
            "day": day,
            "population": stats["population"],
            "jobs": stats["jobs"],
            "within15": stats["within15"],
            "within30": stats["within30"],
            "railStations": stats["railStations"],
            "tramLines": len(trams),
            "regionJobs": region_jobs.get(year),
        })
        rows.append(
            f"            <tr><td><strong>{year}</strong>{t(' (draft)', ' (Entwurf)') if timetable.get('draft') else ''}</td>"
            f"<td>{long_date(sources['referenceDate'])}</td><td>{len(trams)}</td><td>{stats['railStations']}</td>"
            f"<td>{stats['within30']}%</td><td>{people(stats['population'].get('30', 0))}</td><td>{people(stats['jobs'].get('30', 0))}</td>"
            f"<td>{region_jobs_cell(region_jobs.get(year))}</td></tr>"
        )
    note = t(
        "2022: before the Limmattalbahn's second stage (tram 20 to Killwangen, December 2022). 2026: the SZU lines S4 and "
        "S10 start at Zürich Selnau instead of Zürich HB (works on the HB–Selnau section), which puts the Sihltal and "
        "Uetliberg 10–15 minutes further from HB, and trams run diversions for construction work (temporary lines 50 "
        "and 51); most other stops got slightly faster. 2027: first published version of the timetable, with fewer "
        "trips than the final one. 2022 and 2024 come from the Mobility Database's archive of the Swiss feed.",
        "2022: vor der zweiten Etappe der Limmattalbahn (Tram 20 bis Killwangen, Dezember 2022). 2026: Die SZU-Linien S4 "
        "und S10 beginnen in Zürich Selnau statt in Zürich HB (Bauarbeiten auf dem Abschnitt HB–Selnau); dadurch liegen "
        "Sihltal und Uetliberg 10–15 Minuten weiter vom HB entfernt, und die Trams fahren Umleitungen wegen Bauarbeiten "
        "(provisorische Linien 50 und 51); die meisten anderen Haltestellen wurden etwas schneller. 2027: erste "
        "veröffentlichte Fassung des Fahrplans, mit weniger Kursen als die endgültige. 2022 und 2024 stammen aus dem "
        "Archiv des Schweizer Datensatzes in der Mobility Database.",
    )
    return "\n".join(rows), note, timetables, figures


def note_expander(summary: str, paragraphs: list[str]) -> str:
    body = "".join(f"<p>{esc(p)}</p>" for p in paragraphs)
    return f'        <details class="expander data-note"><summary>{esc(summary)}</summary><div class="expander-body">{body}</div></details>'


def reach_notes(city: dict) -> str:
    """What the resident and job counts under the map measure, and what they do not."""
    stats = city["stats"]
    residents, jobs = people(stats["population"]["total"]), people(stats["jobs"]["total"])
    region = f"{city['region'][0].lower()}{city['region'][1:]}"
    return note_expander(t("What these resident and job counts mean", "Was diese Einwohner- und Arbeitsplatzzahlen bedeuten"), [
        t(f"Residents are the BFS STATPOP 2024 grid (permanent residents, by home address); jobs are the STATENT 2023 grid "
          f"(employees of all sectors, counted where they work). The map covers the {region}: "
          f"{residents} residents and {jobs} jobs in all, more than the city alone.",
          f"Die Einwohner stammen aus dem BFS-Raster STATPOP 2024 (ständige Wohnbevölkerung, nach Wohnadresse), die "
          f"Arbeitsplätze aus dem Raster STATENT 2023 (Beschäftigte aller Sektoren, am Arbeitsort gezählt). Die Karte "
          f"umfasst die {city['region']}: insgesamt {residents} Einwohner und {jobs} Arbeitsplätze, mehr als die Stadt allein."),
        t("Jobs are people employed, not full-time equivalents: a part-timer counts as one job, so in a city with much "
          "part-time work the figure overstates the amount of work. Staff are counted at the workplace their employer "
          "registers, which for firms with several sites is not always where they actually sit.",
          "Arbeitsplätze sind Beschäftigte, keine Vollzeitäquivalente: Eine Teilzeitstelle zählt als ein Arbeitsplatz, in "
          "einer Stadt mit viel Teilzeitarbeit überzeichnet die Zahl also die Arbeitsmenge. Gezählt wird am Arbeitsort, "
          "den der Arbeitgeber meldet; bei Firmen mit mehreren Standorten ist das nicht immer der tatsächliche."),
        t("The BFS rounds small hectare counts for privacy, and each 100 m hectare goes to the 200 m map cell its centre "
          "falls in, so figures for a few streets are rough; totals over a whole isochrone hold up much better.",
          "Das BFS rundet kleine Hektarwerte aus Datenschutzgründen, und jede 100-m-Hektare geht an die 200-m-Kartenzelle, "
          "in der ihr Mittelpunkt liegt; Zahlen für wenige Strassen sind also grob, Summen über eine ganze Isochrone "
          "deutlich verlässlicher."),
        t("The grids are the same for every timetable year. Switching years changes only how far the network reaches, "
          "not where people live or work.",
          "Die Raster sind für jedes Fahrplanjahr dieselben. Ein Jahreswechsel ändert nur, wie weit das Netz reicht, "
          "nicht, wo Menschen wohnen oder arbeiten."),
    ])


def history_notes(figures: list[dict]) -> str:
    """Why residents within 30 min of the centre move between years, the 2026 dip in particular."""
    by_year = {f["year"]: f for f in figures}
    paragraphs = [
        t("Residents and jobs are held at the STATPOP 2024 and STATENT 2023 figures in every year, so a change in the "
          "table is a change in the network: a fall means part of the map takes longer to reach, not that people left.",
          "Einwohner und Arbeitsplätze bleiben in jedem Jahr auf dem Stand von STATPOP 2024 und STATENT 2023; eine "
          "Änderung in der Tabelle ist also eine Änderung im Netz: Ein Rückgang heisst, dass ein Teil der Karte länger "
          "braucht, nicht, dass Menschen weggezogen sind."),
    ]
    region = [f["regionJobs"] for f in figures if f.get("regionJobs")]
    if region:
        first, last = region[0], region[-1]
        gain = thousands(last["total"] - first["total"])
        paragraphs.append(t(
            f"For scale, \"Jobs in the region\" is the actual STATENT count for the map's municipalities in each year, or the "
            f"latest published year where none exists yet (STATENT {last['year']} is the newest): the region went from "
            f"{thousands(first['total'])} jobs in {first['year']} to {thousands(last['total'])} in {last['year']}, +{gain}.",
            f"Zum Vergleich ist «Arbeitsplätze in der Region» die tatsächliche STATENT-Zahl der Gemeinden auf der Karte im "
            f"jeweiligen Jahr, oder das letzte veröffentlichte Jahr, wo es noch keine gibt (STATENT {last['year']} ist die "
            f"neueste): Die Region ging von {thousands(first['total'])} Arbeitsplätzen {first['year']} auf "
            f"{thousands(last['total'])} {last['year']}, +{gain}.",
        ))
    if "2024" in by_year and "2026" in by_year:
        before, after = by_year["2024"], by_year["2026"]
        pop_change = after["population"]["30"] - before["population"]["30"]
        sign = "+" if pop_change >= 0 else "−"
        paragraphs.append(t(
            f"2024 → 2026: residents within 30 min go from {thousands(before['population']['30'])} to "
            f"{thousands(after['population']['30'])} ({sign}{thousands(abs(pop_change))}), jobs from "
            f"{thousands(before['jobs']['30'])} to {thousands(after['jobs']['30'])}. The SZU lines S4 and S10 no longer call "
            "at Zürich HB SZU but start at Selnau (works on the HB–Selnau section), so the Sihltal and Uetliberg need an extra "
            "change from HB and slip past 30 minutes; trams also run construction diversions (temporary lines 50 and 51). "
            f"The core got faster at the same time: residents within 15 min went from {thousands(before['population']['15'])} "
            f"to {thousands(after['population']['15'])}.",
            f"2024 → 2026: Die Einwohner innert 30 Minuten gehen von {thousands(before['population']['30'])} auf "
            f"{thousands(after['population']['30'])} ({sign}{thousands(abs(pop_change))}), die Arbeitsplätze von "
            f"{thousands(before['jobs']['30'])} auf {thousands(after['jobs']['30'])}. Die SZU-Linien S4 und S10 halten nicht "
            "mehr in Zürich HB SZU, sondern beginnen in Selnau (Bauarbeiten auf dem Abschnitt HB–Selnau); Sihltal und "
            "Uetliberg brauchen ab HB also einen zusätzlichen Umstieg und rutschen über 30 Minuten. Dazu fahren die Trams "
            "Umleitungen wegen Bauarbeiten (provisorische Linien 50 und 51). Gleichzeitig wurde das Zentrum schneller: Die "
            f"Einwohner innert 15 Minuten stiegen von {thousands(before['population']['15'])} auf "
            f"{thousands(after['population']['15'])}.",
        ))
    paragraphs.append(t(
        "Each year is one reference weekday of the published timetable, 7:00–20:00, with no delays or disruptions, "
        "so a diversion running on that day shows up in that year's figures.",
        "Jedes Jahr steht für einen Stichtag (Werktag) des veröffentlichten Fahrplans, 7–20 Uhr, ohne Verspätungen oder "
        "Störungen; eine Umleitung an diesem Tag zeigt sich also in den Zahlen dieses Jahres.",
    ))
    return note_expander(t("Why the counts change from year to year", "Warum sich die Zahlen von Jahr zu Jahr ändern"), paragraphs)


def fetched(entry: dict | None) -> str:
    return t(", downloaded ", ", heruntergeladen am ") + long_date(entry["fetchedAt"]) if entry and entry.get("fetchedAt") else ""


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
                f'<li><a href="{esc(timetable["dataset"])}">{esc(timetable.get("note") or year)}</a>, {t("valid", "gültig")} '
                f"{esc(period[0])} → {esc(period[1])}{esc(fetched(gtfs))}.</li>"
            )
    osm = main.get("openStreetMap", {})
    osm_link = f'<a href="https://www.openstreetmap.org/copyright">{t("OpenStreetMap contributors", "OpenStreetMap-Mitwirkende")}</a>'
    items = [
        (t("Timetables (GTFS)", "Fahrpläne (GTFS)"),
         t(f'Swiss national timetable, published by <a href="https://opentransportdata.swiss/">opentransportdata.swiss</a> '
           f'(<a href="{LICENCES["opentransportdata"][1]}">terms of use</a>); older years from the '
           f'<a href="https://mobilitydatabase.org/">Mobility Database</a> archive. Clipped to the Zurich area.',
           f'Schweizer Landesfahrplan, veröffentlicht von <a href="https://opentransportdata.swiss/">opentransportdata.swiss</a> '
           f'(<a href="{LICENCES["opentransportdata"][1]}">Nutzungsbedingungen</a>); ältere Jahre aus dem Archiv der '
           f'<a href="https://mobilitydatabase.org/">Mobility Database</a>. Auf den Raum Zürich zugeschnitten.')
         + f'<ul>{"".join(years)}</ul>'),
        (t("Municipal and district boundaries", "Gemeinde- und Kreisgrenzen"),
         f'<a href="https://www.swisstopo.admin.ch/{t("en", "de")}/landscape-model-swissboundaries3d">swissBOUNDARIES3D</a> (swisstopo, '
         + t('via api3.geo.admin.ch) and the 12 Stadtkreise from', 'über api3.geo.admin.ch) und die 12 Stadtkreise von')
         + f' <a href="https://data.stadt-zuerich.ch/">Stadt Zürich Open Data</a>'
         f'{esc(fetched(main.get("boundaries")))}. ' + t("Open government data.", "Offene Behördendaten.")),
        (t("Residents", "Einwohner"),
         f'<a href="https://www.bfs.admin.ch/bfs/{t("en", "de")}/home/statistics/catalogues-databases.assetdetail.36171301.html">'
         f'STATPOP 2024</a> ' + t("hectare grid, Federal Statistical Office (BFS)", "Hektarraster, Bundesamt für Statistik (BFS)")
         + f'{esc(fetched(main.get("population")))}.'),
        (t("Jobs", "Arbeitsplätze"),
         f'<a href="https://www.bfs.admin.ch/bfs/{t("en", "de")}/home/statistics/catalogues-databases.assetdetail.36073031.html">'
         f'STATENT 2023</a> ' + t("hectare grid (employees, all sectors), BFS", "Hektarraster (Beschäftigte, alle Sektoren), BFS")
         + f'{esc(fetched(main.get("jobs")))}.'),
        (t("Jobs in the region", "Arbeitsplätze in der Region"),
         t("BFS STAT-TAB table ", "BFS-STAT-TAB-Tabelle ")
         + '<a href="https://www.pxweb.bfs.admin.ch/pxweb/de/px-x-0602010000_102/px-x-0602010000_102/px-x-0602010000_102.px/">'
         'px-x-0602010000_102</a> '
         + t("(STATENT employees by municipality and year), summed over the map's municipalities",
             "(STATENT-Beschäftigte nach Gemeinde und Jahr), summiert über die Gemeinden auf der Karte")
         + f'{esc(fetched(regional_jobs_source(city)))}.'),
        (t("Elevation", "Höhe"),
         t("swisstopo terrain model (swissALTI3D / DHM25) sampled every 100 m through the ",
           "Terrainmodell von swisstopo (swissALTI3D / DHM25), alle 100 m abgefragt über den ")
         + f'<a href="https://api3.geo.admin.ch/services/sdiservices.html#profile">{t("geo.admin.ch profile service", "Profildienst von geo.admin.ch")}</a>'
         f'{esc(fetched(main.get("elevation")))}.'),
        (t("Lines, lakes, parks and cycling streets", "Linien, Seen, Pärke und Velostrassen"),
         f'© {osm_link} (ODbL), {t("via the Overpass API", "über die Overpass-API")}{esc(fetched(osm.get("osm_water_parks")))}.'),
        (t("Base map", "Grundkarte"),
         t(f'Faint street layer drawn from the same OpenStreetMap network as the bike layer (major roads, streets and '
           f'paths; service roads left out), © {osm_link}. Untick « Streets » to hide it.',
           f'Blasse Strassenebene aus demselben OpenStreetMap-Netz wie die Veloebene (Hauptstrassen, Strassen und Wege; '
           f'Erschliessungswege weggelassen), © {osm_link}. «Strassen» abwählen, um sie auszublenden.')),
        (t("Bus lines", "Buslinien"),
         t("Drawn from OpenStreetMap bus and trolleybus route relations, matched to the timetable by line number (the Swiss "
           "GTFS has no line shapes); shown with « Bus and boat ».",
           "Aus den Bus- und Trolleybus-Routenrelationen von OpenStreetMap, über die Liniennummer dem Fahrplan zugeordnet "
           "(das Schweizer GTFS enthält keine Linienverläufe); sichtbar mit «Bus und Schiff».")),
        (t("Address search", "Adresssuche"),
         t("Live queries to the ", "Live-Abfragen an den ")
         + f'<a href="https://api3.geo.admin.ch/services/sdiservices.html#search">{t("geo.admin.ch search service", "Suchdienst von geo.admin.ch")}</a> '
         + t("(swisstopo) from your browser.", "(swisstopo) aus deinem Browser.")),
        (t("Method", "Methode"),
         t(f'Fork of <a href="{UPSTREAM_URL}">À portée de tram</a> by Camille Roux (MIT). Full provenance (URLs, SHA-256, '
           f'dates) in <a href="{GITHUB_URL}/tree/main/sources">sources/</a>.',
           f'Fork von <a href="{UPSTREAM_URL}">À portée de tram</a> von Camille Roux (MIT). Vollständige Herkunft (URLs, '
           f'SHA-256, Daten) in <a href="{GITHUB_URL}/tree/main/sources">sources/</a>.')),
    ]
    rows = "\n".join(f"          <dt>{esc(title)}</dt><dd>{body}</dd>" for title, body in items)
    return f"""        <details class="expander sources">
          <summary>{t("Data sources", "Datenquellen")}</summary>
          <div class="expander-body">
            <dl>
{rows}
            </dl>
          </div>
        </details>"""


# Fixed page text in English and German, substituted into templates/index.html as ${ui_<key>}.
UI = {
    "start_address": ("Starting address", "Startadresse"),
    "start_here": ("Start here", "Hier starten"),
    "map": ("Map", "Karte"),
    "noscript": ("This interactive map needs JavaScript to compute travel times.",
                 "Diese interaktive Karte braucht JavaScript, um die Reisezeiten zu berechnen."),
    "start": ("Start", "Start"),
    "loading_network": ("Loading the network…", "Netz wird geladen…"),
    "destination": ("Destination", "Ziel"),
    "remove": ("Remove", "Entfernen"),
    "map_from": ("Map from", "Karte ab"),
    "map_from_label": ("Where the map starts from", "Wovon die Karte ausgeht"),
    "click_hint": ("Click the map to set a destination.", "Klicke auf die Karte, um ein Ziel zu setzen."),
    "zoom_in": ("Zoom in", "Hineinzoomen"),
    "zoom_out": ("Zoom out", "Herauszoomen"),
    "recentre": ("Recentre the map", "Karte zentrieren"),
    "fullscreen": ("Full screen", "Vollbild"),
    "mode": ("Mode", "Verkehrsmittel"),
    "mode_label": ("Mode of travel", "Verkehrsmittel"),
    "transit": ("Public transport", "ÖV"),
    "bike": ("Bike", "Velo"),
    "timetable": ("Timetable", "Fahrplan"),
    "timetable_year": ("Timetable year", "Fahrplanjahr"),
    "network": ("Network", "Netz"),
    "base_map": ("Base map", "Grundkarte"),
    "streets": ("Streets", "Strassen"),
    "isochrones": ("Isochrones", "Isochronen"),
    "scale": ("Scale", "Skala"),
    "my_location": ("My location", "Mein Standort"),
    "swap": ("Swap", "Tauschen"),
    "share": ("Share", "Teilen"),
    "within_reach": ("Within reach", "In Reichweite"),
    "within_reach_desc": ("Residents and jobs inside the isochrones ticked above.",
                          "Einwohner und Arbeitsplätze innerhalb der oben gewählten Isochronen."),
    "travel_time": ("Travel time", "Reisezeit"),
    "residents": ("Residents", "Einwohner"),
    "jobs": ("Jobs", "Arbeitsplätze"),
    "across_years": ("Across the years", "Über die Jahre"),
    "across_years_desc": ("This start (and destination) on each timetable year, 2022 → 2027.",
                          "Dieser Start (und dieses Ziel) in jedem Fahrplanjahr, 2022 → 2027."),
    "lines_summary": ("Tram, S-Bahn and funicular lines", "Tram-, S-Bahn- und Seilbahnlinien"),
    "line": ("Line", "Linie"),
    "stops": ("Stops", "Haltestellen"),
    "every": ("Every", "Takt"),
    "official_data": ("Official data", "Offizielle Daten"),
    "over_time": ("The network over time", "Das Netz im Wandel"),
    "over_time_desc": ("The same model on each year's official timetable; residents and jobs held at today's numbers.",
                       "Dasselbe Modell auf dem offiziellen Fahrplan jedes Jahres; Einwohner und Arbeitsplätze auf heutigem Stand."),
    "insights": ("Insights", "Einblicke"),
    "prev_insight": ("Previous insight", "Vorheriger Einblick"),
    "next_insight": ("Next insight", "Nächster Einblick"),
    "measure": ("Measure", "Kennzahl"),
    "show_table": ("Show as table", "Als Tabelle anzeigen"),
    "day": ("Day", "Tag"),
    "tram_lines": ("Tram lines", "Tramlinien"),
    "rail_stops": ("Rail stops", "Haltestellen"),
    "stops_30": ("Stops within 30 min", "Haltestellen innert 30 min"),
    "residents_30": ("Residents within 30 min", "Einwohner innert 30 min"),
    "jobs_30": ("Jobs within 30 min", "Arbeitsplätze innert 30 min"),
    "jobs_region": ("Jobs in the region", "Arbeitsplätze in der Region"),
    "questions": ("Questions", "Fragen"),
    "questions_desc": ("How the times are computed, and where the data comes from.",
                       "Wie die Zeiten berechnet werden und woher die Daten stammen."),
}


def localized(city: dict) -> dict:
    """The city config with its German wording (cities/<slug>.json « de ») on the German page."""
    return {**city, **city.get("de", {})} if LANG == "de" else city


def render_page(template: Template, city: dict) -> str:
    url = SITE_URL + page_path(LANG)
    base = "./" if LANG == "de" else "../"  # the site root, where app.js, styles.css and data/ live
    stats = city["stats"]
    center = stats["center"]
    description = t(
        f"Travel time map of {city['name']} by tram, S-Bahn and bus: pick a starting point and the whole city is coloured "
        "by how long it takes to get there.",
        f"Reisezeitkarte von {city['name']} mit Tram, S-Bahn und Bus: Wähle einen Startpunkt, und die ganze Stadt wird "
        "danach eingefärbt, wie lange es bis dorthin dauert.",
    )
    faq = city_faq(city)
    graph = [
        {
            "@type": "WebApplication",
            "name": city["title"],
            "url": url,
            "description": description,
            "inLanguage": LANG,
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
    # One row of four about the network; the people counts are in « Within reach » above.
    tiles = [
        (f"{stats['within30']}%", t(f"of the {city['railStations']} within 30 min of {center}",
                                            f"der {city['railStations']} innert 30 min ab {center}")),
        (str(stats["railStations"]), t(f"{city['railStations']} on the map", f"{city['railStations']} auf der Karte")),
        (f"{num(fastest['headway'])} min", t(f"between two trams on line {fastest['name']}, the most frequent",
                                             f"zwischen zwei Trams der Linie {fastest['name']}, der dichteste Takt")),
        (f"{stats['farthestMinutes']} min", t(f"from {center} to {stats['farthestStation']}, the farthest stop",
                                              f"von {center} nach {stats['farthestStation']}, die entfernteste Haltestelle")),
    ]
    stat_html = [f'          <div class="stat"><strong>{esc(value)}</strong><span>{esc(label)}</span></div>' for value, label in tiles]
    if stats.get("runnerUpStation"):
        # Easter egg: hovering the farthest stop reveals the runner-up.
        runner_up = esc(stats["runnerUpStation"])
        stat_html[3] = (
            '          <div class="stat stat-egg" tabindex="0">'
            f'<div class="stat-face"><strong>{esc(tiles[3][0])}</strong><span>{esc(tiles[3][1])}</span></div>'
            f'<div class="stat-face stat-egg-face"><strong>{stats["runnerUpMinutes"]} min</strong>'
            f'<span>{t(f"{runner_up} is the runner-up. So close.", f"{runner_up} ist Zweite. Knapp daneben.")}</span></div></div>'
        )
    stat_tiles = "\n".join(stat_html)
    line_rows = "\n".join(
        f'            <tr><td>{line_badge(line["color"], line["name"])} {esc(MODE_NAMES[LANG].get(line["mode"], ""))}</td>'
        f'<td>{line["stations"]}</td><td>~{num(line["headway"])} min</td>'
        f'<td><a href="{LINE_TIMETABLES_URL}" rel="noopener" aria-label="{t("Official ZVV timetable for line", "Offizieller ZVV-Fahrplan der Linie")} {esc(line["name"])}">'
        f'{t("ZVV timetable", "ZVV-Fahrplan")}</a></td></tr>'
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
        "center": center,
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
        "lang": LANG,
        "head": head(title=f"{city['title']} · {city['titleSuffix']}", description=description, url=url, base=base, graph=graph),
        "header": header(base, home="./"),
        "footer": footer(city),
        "base": base,
        "city_config": json.dumps(config, ensure_ascii=False).replace("</", "<\\/"),
        "headline": esc(city["title"]),
        "ui_lede": esc(t(
            "Choose any start and destination on the map below and it is coloured by the time it takes to get there or "
            "to come back from there. Public transport can include buses and boats in the periphery, or be compared with biking.",
            "Wähle auf der Karte unten einen beliebigen Start und ein Ziel: Die Karte färbt sich nach der Zeit, die es "
            "braucht, um dorthin oder von dort zurück zu kommen. Der ÖV kann in der Agglomeration auch Busse und Schiffe "
            "einbeziehen oder mit dem Velo verglichen werden.",
        )),
        "ui_search_placeholder": esc(t(f"Start from an address… (e.g. {city['searchExample']})",
                                       f"Von einer Adresse starten… (z. B. {city['searchExample']})")),
        "ui_in_numbers": esc(t(f"{city['name']} in numbers", f"{city['name']} in Zahlen")),
        "ui_in_numbers_desc": esc(t(f"From {center}, on a weekday of the {city['defaultTimetable']} timetable.",
                                    f"Ab {center}, an einem Werktag des Fahrplans {city['defaultTimetable']}.")),
        **{f"ui_{key}": esc(t(en, de)) for key, (en, de) in UI.items()},
        "rail_label": esc(city["railLabel"]),
        "bus_label": esc(city["busLabel"]),
        "network": esc(city["network"]),
        "stat_tiles": stat_tiles,
        "line_rows": line_rows,
        "year_buttons": year_buttons,
        "history_rows": history_rows,
        "history_note": esc(history_note),
        "reach_notes": reach_notes(city),
        "history_notes": history_notes(figures),
        "faq_html": faq_block(faq) + "\n" + sources_block(city),
        "styles_version": short_hash(SITE / "styles.css"),
        "app_version": short_hash(SITE / "app.js"),
    }
    return template.substitute(values)


def render_404() -> str:
    return f"""<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Seite nicht gefunden · {esc(SITE_NAMES["de"])}</title>
    <meta name="robots" content="noindex" />
    <link rel="stylesheet" href="https://fonts.bunny.net/css?family=inter:400,500,600,700,800" />
    <link rel="stylesheet" href="{SITE_URL}styles.css?v={short_hash(SITE / 'styles.css')}" />
  </head>
  <body>
{header(SITE_URL)}
    <main class="page">
      <section class="hero">
        <h1>Endstation!</h1>
        <p class="lede">Diese Seite gibt es nicht. <a href="{SITE_URL}">Zurück zur Karte</a> ·
          <span lang="en">This page does not exist. <a href="{SITE_URL}en/">Back to the map</a>.</span></p>
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
    global LANG
    template = Template((ROOT / "templates" / "index.html").read_text(encoding="utf-8"))
    (city,) = load_cities()  # a single-city fork
    city["sources"] = json.loads((ROOT / "sources" / f"{city['slug']}-{city['defaultTimetable']}.json").read_text(encoding="utf-8"))
    city["stats"] = city["sources"]["stats"]
    for LANG in LANGS:
        out = SITE / page_path(LANG) / "index.html"
        out.parent.mkdir(exist_ok=True)
        out.write_text(render_page(template, localized(city)), encoding="utf-8")
    LANG = "de"
    (SITE / "404.html").write_text(render_404(), encoding="utf-8")
    write_sources_readme(city)
    lastmod = city["sources"]["builtAt"][:10]
    urls = "".join(f"  <url><loc>{SITE_URL}{page_path(lang)}</loc><lastmod>{lastmod}</lastmod></url>\n" for lang in LANGS)
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n",
        encoding="utf-8",
    )
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n", encoding="utf-8")
    print("Wrote site/index.html (de), site/en/index.html, site/404.html, site/sitemap.xml, site/robots.txt, sources/README.md")


if __name__ == "__main__":
    main()
