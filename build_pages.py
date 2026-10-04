#!/usr/bin/env python3
"""Render the home page, one page per city (cities/*.json), the 404 page, sitemap.xml and robots.txt.

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
SITE_URL = "https://tram.camilleroux.com/"
GITHUB_URL = "https://github.com/camilleroux/montpellier-temps-transport"
AUTHOR_URL = "https://www.camilleroux.com/"
SITE_NAME = "À portée de tram"
ANALYTICS = (
    '    <!-- Cloudflare Web Analytics (sans cookie). "spa": false : les mises à jour de l\'URL ne comptent pas comme des pages vues. -->\n'
    '    <script type="module" src="https://static.cloudflareinsights.com/beacon.min.js" '
    "data-cf-beacon='{\"token\": \"1904c17ed0624c0cab4d69ea1bacc5e7\", \"spa\": false}'></script>"
)
LICENCES = {
    "lo": ("Licence Ouverte 2.0", "https://www.etalab.gouv.fr/licence-ouverte-open-licence/"),
    "odbl": ("ODbL", "https://opendatacommons.org/licenses/odbl/1-0/"),
    "mobilites": ("Licence Mobilités", "https://wiki.lafabriquedesmobilites.fr/wiki/Licence_Mobilit%C3%A9s"),
}
ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
MODE_LABEL_SHORT = {"tram": "Tram", "metro": "Métro", "metro+tram": "Métro et tram"}
MODE_NAMES = {"metro": "Métro", "tram": "Tram", "funicular": "Funiculaire", "cable": "Téléphérique", "busway": "Busway"}
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

esc = html.escape


def num(value: float) -> str:
    """French decimal comma: 4.4 → « 4,4 »."""
    return f"{value:g}".replace(".", ",")


def short_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.exists() else "0"


def french_date(value: str, weekday: bool = False) -> str:
    day = date.fromisoformat(value[:10])
    text = f"{day.day} {MONTHS[day.month - 1]} {day.year}"
    return f"{WEEKDAYS[day.weekday()]} {text}" if weekday else text


def load_built_cities() -> list[dict]:
    """Cities whose data has been built, with their figures (sources/<city>.json)."""
    cities = []
    for city in load_cities():
        sources = ROOT / "sources" / f"{city['slug']}.json"
        if not sources.exists() or not (SITE / "data" / f"{city['slug']}.json").exists():
            print(f"  {city['slug']} ignorée : lancer d'abord build_data.py {city['slug']}")
            continue
        city["sources"] = json.loads(sources.read_text(encoding="utf-8"))
        city["stats"] = city["sources"]["stats"]
        cities.append(city)
    return cities


def json_ld(data: dict) -> str:
    body = json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
    return '    <script type="application/ld+json">\n    ' + body.replace("\n", "\n    ") + "\n    </script>"


def head(*, title: str, description: str, url: str, base: str, image: str, image_alt: str, published: str, graph: list) -> str:
    """<head> content shared by every page: SEO, social previews, structured data."""
    redirect = (
        "    <script>\n"
        "      // Anciens liens github.io : on renvoie vers le domaine du site en gardant départ et arrivée.\n"
        f'      if (location.hostname.endsWith("github.io")) location.replace("{url}" + location.search);\n'
        "    </script>"
    )
    title_text = esc(title.split(" · ")[0])
    return "\n".join(
        [
            '    <meta charset="utf-8" />',
            '    <meta name="viewport" content="width=device-width, initial-scale=1" />',
            f"    <title>{esc(title)}</title>",
            f'    <meta name="description" content="{esc(description)}" />',
            redirect,
            f'    <link rel="canonical" href="{url}" />',
            '    <meta name="theme-color" content="#3aa70b" />',
            f'    <link rel="icon" href="{base}favicon.svg" type="image/svg+xml" />',
            f'    <link rel="icon" href="{base}favicon-32.png" type="image/png" sizes="32x32" />',
            f'    <link rel="apple-touch-icon" href="{base}apple-touch-icon.png" />',
            '    <meta name="author" content="Camille Roux" />',
            f'    <link rel="author" href="{AUTHOR_URL}" />',
            '    <meta property="og:type" content="website" />',
            '    <meta property="og:locale" content="fr_FR" />',
            f'    <meta property="og:site_name" content="{SITE_NAME}" />',
            f'    <meta property="og:title" content="{title_text}" />',
            f'    <meta property="og:description" content="{esc(description)}" />',
            f'    <meta property="og:url" content="{url}" />',
            f'    <meta property="og:image" content="{image}" />',
            '    <meta property="og:image:width" content="1200" />',
            '    <meta property="og:image:height" content="630" />',
            f'    <meta property="og:image:alt" content="{esc(image_alt)}" />',
            f'    <meta property="article:author" content="{AUTHOR_URL}" />',
            f'    <meta property="article:published_time" content="{published}T08:00:00+02:00" />',
            '    <meta name="twitter:card" content="summary_large_image" />',
            f'    <meta name="twitter:title" content="{title_text}" />',
            f'    <meta name="twitter:description" content="{esc(description)}" />',
            f'    <meta name="twitter:image" content="{image}" />',
            json_ld({"@context": "https://schema.org", "@graph": graph}),
            '    <link rel="preconnect" href="https://fonts.bunny.net" />',
            '    <link rel="stylesheet" href="https://fonts.bunny.net/css?family=inter:400,500,600,700,800" />',
        ]
    )


GITHUB_ICON = (
    '<svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 '
    "3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15"
    "-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59"
    ".82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1"
    ".16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55"
    '.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>'
)


def header(base: str) -> str:
    return f"""    <header class="topbar">
      <nav class="topbar-inner" aria-label="Navigation principale">
        <a class="brand" href="{base}"><img src="{base}favicon.svg" width="22" height="22" alt="" /> {SITE_NAME}</a>
        <div class="topbar-links">
          <a class="topbar-link icon-link" href="{GITHUB_URL}" rel="noopener" aria-label="Code source sur GitHub">{GITHUB_ICON}<span>GitHub</span></a>
          <a class="topbar-link" href="{AUTHOR_URL}" rel="author">camilleroux.com</a>
        </div>
      </nav>
    </header>"""


def footer(cities: list[dict], base: str, data_credit: str) -> str:
    links = " · ".join(f'<a href="{base}{city["path"]}">{esc(city["name"])}</a>' for city in sorted(cities, key=lambda c: c["name"]))
    return f"""    <footer class="site-footer">
      <div class="footer-inner">
        <p class="footer-author">
          Un projet de <a href="{AUTHOR_URL}" rel="author">Camille Roux</a>, développeur et co-fondateur de Human Coders,
          à Montpellier, d'après l'idée d'Anthony Castrio et de Jules Grandin. Découvrez
          <a href="{AUTHOR_URL}realisations/">ses autres réalisations</a> et sa
          <a href="{AUTHOR_URL}veille/">veille tech hebdomadaire</a>.
        </p>
        <p class="footer-links">Villes&nbsp;: {links}</p>
        <p class="footer-links">
          <a href="{GITHUB_URL}" rel="noopener">Code source sur GitHub</a> ·
          <a href="{GITHUB_URL}/issues" rel="noopener">Proposer une ville ou signaler une erreur</a> ·
          <a href="{base}mentions-legales/">Mentions légales et licences</a>
        </p>
        <p class="footer-credits">
          Idée originale&nbsp;: le <a href="https://castrio.me/nyc/">NYC Transit Time Cartogram</a> d'Anthony Castrio,
          adapté ensuite à Paris par Jules Grandin
          (<a href="https://julesgrandin.github.io/paris-temps-transport/">C'est encore loin&nbsp;?</a>).
          {data_credit} Fond de carte © <a href="https://www.openstreetmap.org/copyright">contributeurs OpenStreetMap</a>,
          <a href="https://geo.api.gouv.fr/">contours communaux</a>, recherche d'adresse via la
          <a href="https://adresse.data.gouv.fr/">Base Adresse Nationale</a>.
          Données calculées publiées sous licence <a href="{ODBL_URL}">ODbL</a>, code sous licence MIT.
        </p>
      </div>
    </footer>"""


def faq_block(entries: list[tuple]) -> str:
    """Entries are (question, answer) or (question, answer, answer_html) when the visible answer carries links."""
    return "\n".join(
        f'        <details class="faq"><summary>{esc(entry[0])}</summary><p>{entry[2] if len(entry) > 2 else esc(entry[1])}</p></details>'
        for entry in entries
    )


def faq_schema(entries: list[tuple]) -> dict:
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": entry[0], "acceptedAnswer": {"@type": "Answer", "text": entry[1]}} for entry in entries
        ],
    }


def credits_entry(question: str) -> tuple:
    """« Who made this? »: the original authors first, then the author of these maps."""
    text = (
        "L'idée vient du NYC Transit Time Cartogram d'Anthony Castrio, adapté ensuite à Paris par Jules Grandin "
        "(« C'est encore loin ? »). Ces cartes sont réalisées par Camille Roux, développeur et co-fondateur de Human "
        "Coders à Montpellier, qui présente ses autres réalisations sur camilleroux.com. Le code est ouvert sur GitHub."
    )
    html_text = (
        'L\'idée vient du <a href="https://castrio.me/nyc/">NYC Transit Time Cartogram</a> d\'Anthony Castrio, adapté '
        'ensuite à Paris par Jules Grandin (<a href="https://julesgrandin.github.io/paris-temps-transport/">C\'est encore '
        f'loin&nbsp;?</a>). Ces cartes sont réalisées par <a href="{AUTHOR_URL}" rel="author">Camille Roux</a>, développeur '
        f'et co-fondateur de Human Coders à Montpellier&nbsp;: découvrez <a href="{AUTHOR_URL}realisations/">ses autres '
        f'réalisations</a>. Le code est ouvert sur <a href="{GITHUB_URL}">GitHub</a>.'
    )
    return (question, text, html_text)


def author_schema() -> dict:
    return {
        "@type": "Person",
        "@id": AUTHOR_URL + "#me",
        "name": "Camille Roux",
        "url": AUTHOR_URL,
        "image": AUTHOR_URL + "content/images/size/w256h256/format/jpeg/2025/05/camillecouleur---lowres-2.jpg",
        "jobTitle": "Développeur, co-fondateur de Human Coders",
        "worksFor": {"@type": "Organization", "name": "Human Coders", "url": "https://www.humancoders.com/"},
        "address": {"@type": "PostalAddress", "addressLocality": "Montpellier", "addressCountry": "FR"},
        "sameAs": [
            "https://www.linkedin.com/in/camilleroux",
            "https://x.com/CamilleRoux",
            "https://bsky.app/profile/camilleroux.com",
            "https://mastodon.social/@camilleroux",
            "https://github.com/camilleroux",
        ],
    }


def city_card(city: dict, base: str, heading: str = "h3") -> str:
    stats = city["stats"]
    return f"""          <a class="city-card" href="{base}{city['path']}">
            <img src="{base}og/thumb-{city['slug']}.jpg" width="600" height="315" alt="" loading="lazy" />
            <span class="city-card-body">
              <{heading}>{esc(city['title'])}</{heading}>
              <span>{stats['within30']}&nbsp;% des {esc(city['railStations'])} à moins de 30&nbsp;min du centre ({esc(stats['center'])}) · réseau {esc(city['network'])}</span>
            </span>
          </a>"""


def city_faq(city: dict) -> list[tuple]:
    stats, sources = city["stats"], city["sources"]
    name, rail = city["name"], city["railNoun"]
    lines = stats["lines"]
    headways = ", ".join(f"{MODE_NAMES.get(line['mode'], 'ligne').lower()} {line['name']} : {num(line['headway'])} min" for line in lines)
    fastest = min(lines, key=lambda line: line["headway"])
    period = sources["gtfs"].get("servicePeriod") or [None, None]
    fetched = sources["gtfs"].get("fetchedAt")
    return [
        (
            f"Combien de temps faut-il pour traverser {name} en {rail} ?",
            f"Depuis le centre ({stats['center']}), {stats['within15']} % des {city['railStations']} sont à moins de 15 minutes et "
            f"{stats['within30']} % à moins de 30 minutes, marche et attente comprises. La plus éloignée, {stats['farthestStation']}, "
            f"est à environ {stats['farthestMinutes']} minutes.",
        ),
        (
            f"Quelle est la fréquence des lignes de {rail} à {name} ?",
            f"En journée de semaine, l'intervalle moyen entre deux passages est de {headways}. La ligne la plus fréquente "
            f"est la {fastest['name']}, avec un passage toutes les {num(fastest['headway'])} minutes environ.",
        ),
        (
            "D'où viennent les horaires utilisés ?",
            f"Des horaires théoriques publiés par le réseau {city['network']} (format GTFS, {LICENCES[city['gtfsLicence']][0]})"
            + (f", téléchargés le {french_date(fetched)}" if fetched else "")
            + (f" et valables jusqu'au {french_date(period[1])}" if period[1] else "")
            + f". Les temps correspondent au {french_date(sources['referenceDate'], weekday=True)}, entre 7 h et 20 h.",
        ),
        (
            f"Les bus sont-ils pris en compte à {name} ?",
            f"Oui, en option : cochez « {city['busLabel']} » sous la carte. Par défaut, seuls les {rail} sont affichés. "
            "L'attente aux arrêts de bus peu fréquentés est plafonnée à 15 minutes.",
        ),
        (
            "Comment les temps de trajet sont-ils calculés ?",
            "Pour chaque trajet : marche jusqu'à l'arrêt à 4,5 km/h, attente égale à la moitié de l'intervalle entre deux "
            "passages, durée prévue entre les arrêts, correspondances avec 1,5 minute de marche"
            + (", et 2 minutes pour rejoindre le quai du métro" if any(line["mode"] == "metro" for line in lines) else "")
            + ". Pas de temps réel ni de perturbations : c'est la ville « sur le papier ».",
        ),
        credits_entry(f"Qui a réalisé cette carte de {name} ?"),
    ]


def render_city(template: Template, cities: list[dict], city: dict) -> str:
    url = SITE_URL + city["path"]
    base = "../"
    stats = city["stats"]
    rail_noun = city["railNoun"]
    description = (
        f"Carte des temps de trajet en {rail_noun} à {city['name']} : choisissez un départ, toute la ville se colore "
        f"selon le temps qu'il faut pour y aller (réseau {city['network']})."
    )
    faq = city_faq(city)
    graph = [
        {
            "@type": "WebApplication",
            "name": city["title"],
            "url": url,
            "description": description,
            "inLanguage": "fr",
            "applicationCategory": "TravelApplication",
            "operatingSystem": "Web",
            "isAccessibleForFree": True,
            "image": f"{SITE_URL}og/{city['slug']}.jpg",
            "author": {"@id": AUTHOR_URL + "#me"},
            "spatialCoverage": {"@type": "Place", "name": city["metropole"]},
            "isBasedOn": ["https://castrio.me/nyc/", "https://julesgrandin.github.io/paris-temps-transport/"],
            "datePublished": city["published"],
            "dateModified": city["sources"]["builtAt"][:10],
        },
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": SITE_URL},
                {"@type": "ListItem", "position": 2, "name": city["name"], "item": url},
            ],
        },
        faq_schema(faq),
        author_schema(),
    ]
    fastest = min(stats["lines"], key=lambda line: line["headway"])
    tiles = [
        (f"{stats['within30']} %", f"des {city['railStations']} à moins de 30 min du centre ({stats['center']})"),
        (str(stats["railStations"]), city["railStations"]),
        (f"{num(fastest['headway'])} min", f"entre deux passages sur la ligne {fastest['name']}, la plus fréquente"),
        (f"{stats['farthestMinutes']} min", f"depuis le centre pour rejoindre {stats['farthestStation']}, la station la plus éloignée"),
    ]
    stat_tiles = "\n".join(f'          <div class="stat"><strong>{esc(value)}</strong><span>{esc(label)}</span></div>' for value, label in tiles)
    line_rows = "\n".join(
        f'            <tr><td><span class="line-badge" style="background:{line["color"]}">{esc(line["name"])}</span> '
        f'{esc(MODE_NAMES.get(line["mode"], ""))}</td><td>{line["stations"]}</td><td>~{num(line["headway"])} min</td></tr>'
        for line in stats["lines"]
    )
    # City switcher: the city name in the title opens a panel of real links (site/app.js), crawlable as well.
    items = "\n".join(
        f'            <a class="city-item{" current" if other["slug"] == city["slug"] else ""}" href="{base}{other["path"]}"'
        f'{" aria-current=\"page\"" if other["slug"] == city["slug"] else ""} data-name="{esc(other["name"].lower())}">'
        f'<img src="{base}og/thumb-{other["slug"]}.jpg" width="120" height="63" alt="" loading="lazy" />'
        f'<span><strong>{esc(other["name"])}</strong><small>{esc(MODE_LABEL_SHORT[other["kind"]])} · {esc(other["network"])}</small></span></a>'
        for other in sorted(cities, key=lambda item: item["name"])
    )
    rest = esc(city["title"][len(city["name"]):])
    headline = (
        f'<button id="cityTrigger" type="button" class="city-trigger" aria-haspopup="dialog" aria-expanded="false" '
        f'title="Changer de ville">{esc(city["name"])}<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 6l5 5 5-5"/></svg></button>'
        + "&nbsp;".join(rest.rsplit(" ", 1))
    )
    config = {
        "slug": city["slug"],
        "name": city["name"],
        "dataVersion": short_hash(SITE / "data" / f"{city['slug']}.json"),
        "defaultFrom": city["defaultFrom"],
        "railNoun": rail_noun,
        "railStations": city["railStations"],
        "busNoun": city["busNoun"],
    }
    data_credit = (
        f'Horaires&nbsp;: <a href="{esc(city["gtfsDataset"])}">GTFS {esc(city["network"])}</a> ({esc(city["metropole"])}).'
    )
    values = {
        "head": head(
            title=f"{city['title']} · {city['titleSuffix']}",
            description=description,
            url=url,
            base=base,
            image=f"{SITE_URL}og/{city['slug']}.jpg?v={short_hash(SITE / 'og' / (city['slug'] + '.jpg'))}",
            image_alt=city["ogAlt"],
            published=city["published"],
            graph=graph,
        ),
        "header": header(base),
        "footer": footer(cities, base, data_credit),
        "analytics": ANALYTICS,
        "base": base,
        "city_config": json.dumps(config, ensure_ascii=False).replace("</", "<\\/"),
        "city_items": items,
        "headline": headline,
        "name": esc(city["name"]),
        "area": esc(city.get("area", "de la Métropole")),
        "rail_noun": esc(rail_noun),
        "rail_label": esc(city["railLabel"]),
        "bus_label": esc(city["busLabel"]),
        "search_example": esc(city["searchExample"]),
        "network": esc(city["network"]),
        "stat_tiles": stat_tiles,
        "line_rows": line_rows,
        "faq_html": faq_block(faq),
        "other_cities": "\n".join(city_card(other, base) for other in cities if other["slug"] != city["slug"]),
        "styles_version": short_hash(SITE / "styles.css"),
        "app_version": short_hash(SITE / "app.js"),
    }
    return template.substitute(values)


def render_home(template: Template, cities: list[dict]) -> str:
    names = ", ".join(city["name"] for city in cities[:-1]) + f" et {cities[-1]['name']}"
    networks = ", ".join(f"{city['network']} ({city['name']})" for city in cities)
    description = f"Cartes des temps de trajet en tram et métro à {names} : la ville se colore selon le temps pour y aller."
    faq = [
        (
            "D'où viennent les temps de trajet ?",
            f"Des horaires théoriques officiels de chaque réseau ({networks}), publiés en open data au format GTFS. "
            "Ils correspondent à un jour de semaine ordinaire, entre 7 h et 20 h.",
        ),
        (
            "Les temps affichés sont-ils fiables ?",
            "Ce sont des moyennes « sur le papier » : marche jusqu'à l'arrêt, attente égale à la moitié de l'intervalle entre "
            "deux passages, durée prévue entre les arrêts et correspondances. Pas de temps réel ni de perturbations.",
        ),
        (
            "Le bus est-il pris en compte ?",
            "Oui, en option sur chaque carte. Par défaut, seuls le tram, le métro et les transports guidés sont affichés, "
            "pour montrer l'ossature du réseau.",
        ),
        (
            "Ma ville n'y est pas, pourquoi ?",
            "Il faut un réseau de tram ou de métro et des horaires publiés en open data. Les prochaines villes sont ajoutées "
            "au fur et à mesure : vous pouvez en proposer une sur GitHub.",
        ),
        credits_entry("Qui a réalisé ce site ?"),
    ]
    graph = [
        {
            "@type": "WebSite",
            "@id": SITE_URL + "#site",
            "name": SITE_NAME,
            "url": SITE_URL,
            "description": description,
            "inLanguage": "fr",
            "author": {"@id": AUTHOR_URL + "#me"},
        },
        {
            "@type": "ItemList",
            "name": "Cartes des temps de trajet par ville",
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "name": city["title"], "url": SITE_URL + city["path"]}
                for i, city in enumerate(cities)
            ],
        },
        faq_schema(faq),
        author_schema(),
    ]
    published = min(city["published"] for city in cities)
    values = {
        "head": head(
            title=f"{SITE_NAME} · Temps de trajet en tram et métro par ville",
            description=description,
            url=SITE_URL,
            base="./",
            image=SITE_URL + "og/home.jpg?v=" + short_hash(SITE / "og" / "home.jpg"),
            image_alt=f"Cartes des temps de trajet en tram et métro à {names}.",
            published=published,
            graph=graph,
        ),
        "header": header("./"),
        "footer": footer(cities, "./", "Horaires&nbsp;: GTFS des réseaux de chaque ville (détail dans les mentions légales)."),
        "analytics": ANALYTICS,
        "city_count": str(len(cities)),
        "city_cards": "\n".join(city_card(city, "./", "h2") for city in cities),
        "faq_html": faq_block(faq),
        "styles_version": short_hash(SITE / "styles.css"),
    }
    return template.substitute(values)


def render_legal(cities: list[dict]) -> str:
    """Mentions légales (LCEN) and the licence of every source."""
    rows = "\n".join(
        f'          <tr><td>{esc(city["name"])}</td><td><a href="{esc(city["gtfsDataset"])}">GTFS {esc(city["network"])}</a></td>'
        f'<td><a href="{LICENCES[city["gtfsLicence"]][1]}">{LICENCES[city["gtfsLicence"]][0]}</a></td>'
        f'<td>{french_date(city["sources"]["gtfs"]["fetchedAt"]) if city["sources"]["gtfs"].get("fetchedAt") else "—"}</td></tr>'
        for city in sorted(cities, key=lambda item: item["name"])
    )
    graph = [author_schema()]
    return f"""<!doctype html>
<html lang="fr">
  <head>
{head(title=f"Mentions légales et licences · {SITE_NAME}", description="Éditeur, hébergeur, mesure d'audience et licences des données utilisées par À portée de tram.", url=SITE_URL + "mentions-legales/", base="../", image=SITE_URL + "og/home.jpg", image_alt="À portée de tram", published="2026-10-05", graph=graph)}
    <link rel="stylesheet" href="../styles.css?v={short_hash(SITE / 'styles.css')}" />
  </head>
  <body>
{header('../')}
    <main class="page">
      <nav class="breadcrumb" aria-label="Fil d'Ariane">
        <a href="../">{SITE_NAME}</a> <span aria-hidden="true">›</span> <span aria-current="page">Mentions légales</span>
      </nav>
      <section class="section">
        <h1 class="page-title">Mentions légales et licences</h1>
        <h2>Éditeur</h2>
        <p>Ce site est édité à titre personnel par <a href="{AUTHOR_URL}" rel="author">Camille Roux</a>. Contact&nbsp;: via
        <a href="{AUTHOR_URL}contact/">la page contact de camilleroux.com</a> ou les <a href="{GITHUB_URL}/issues">issues GitHub</a> du projet.</p>
        <h2>Hébergement</h2>
        <p>GitHub, Inc. (GitHub Pages), 88 Colin P. Kelly Jr. Street, San Francisco, CA 94107, États-Unis.
        Nom de domaine géré par Cloudflare, Inc., 101 Townsend Street, San Francisco, CA 94107, États-Unis.</p>
        <h2>Mesure d'audience et données personnelles</h2>
        <p>La fréquentation est mesurée avec Cloudflare Web Analytics, sans cookie ni identifiant personnel. Les trajets
        sont calculés dans votre navigateur&nbsp;: aucune position ni adresse n'est enregistrée. La recherche d'adresse
        interroge l'API de la Base Adresse Nationale (adresse.data.gouv.fr).</p>
        <h2>Licences</h2>
        <p>Le code est publié sous licence MIT sur <a href="{GITHUB_URL}">GitHub</a>. Les données calculées
        (<code>data/*.json</code>) sont des bases de données dérivées, publiées sous licence <a href="{ODBL_URL}">ODbL</a>.
        Fond de carte et tracés&nbsp;: © <a href="https://www.openstreetmap.org/copyright">contributeurs OpenStreetMap</a>
        (ODbL). Contours communaux&nbsp;: <a href="https://geo.api.gouv.fr/">geo.api.gouv.fr</a> (Licence Ouverte).</p>
        <table class="lines-table">
          <caption>Horaires utilisés pour chaque ville</caption>
          <thead><tr><th scope="col">Ville</th><th scope="col">Source</th><th scope="col">Licence</th><th scope="col">Téléchargé le</th></tr></thead>
          <tbody>
{rows}
          </tbody>
        </table>
      </section>
    </main>
{footer(cities, "../", "")}
{ANALYTICS}
  </body>
</html>
"""


def write_sources_readme(cities: list[dict]) -> None:
    lines = [
        "# Provenance des données",
        "",
        "Généré par `build_pages.py` à partir des fiches `sources/<ville>.json`.",
        "",
        "| Ville | Réseau | Licence | GTFS téléchargé le | Validité du GTFS | Jour de référence |",
        "|---|---|---|---|---|---|",
    ]
    for city in sorted(cities, key=lambda item: item["name"]):
        gtfs = city["sources"]["gtfs"]
        period = gtfs.get("servicePeriod") or ["?", "?"]
        how = " (à la main)" if gtfs.get("how") == "manual" else ""
        lines.append(
            f"| [{city['name']}]({city['slug']}.json) | {city['network']} | {LICENCES[city['gtfsLicence']][0]} | "
            f"{gtfs.get('fetchedAt', '?')[:10]}{how} | {period[0]} → {period[1]} | {city['sources']['referenceDate']} |"
        )
    (ROOT / "sources" / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_404(cities: list[dict]) -> str:
    links = "\n".join(f'          <a class="chip" href="/{city["path"]}">{esc(city["name"])}</a>' for city in cities)
    return f"""<!doctype html>
<html lang="fr">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Page introuvable · {SITE_NAME}</title>
    <meta name="robots" content="noindex" />
    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
    <link rel="stylesheet" href="https://fonts.bunny.net/css?family=inter:400,500,600,700,800" />
    <link rel="stylesheet" href="/styles.css?v={short_hash(SITE / 'styles.css')}" />
  </head>
  <body>
{header('/')}
    <main class="page">
      <section class="hero">
        <h1>Terminus&nbsp;!</h1>
        <p class="lede">Cette page n'existe pas. Choisissez une ville pour reprendre votre trajet.</p>
        <nav class="city-switch" aria-label="Villes">
{links}
        </nav>
      </section>
    </main>
  </body>
</html>
"""


def main() -> None:
    cities = load_built_cities()
    city_template = Template((ROOT / "templates" / "city.html").read_text(encoding="utf-8"))
    for city in cities:
        page = SITE / city["path"] / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(render_city(city_template, cities, city), encoding="utf-8")
        print(f"Wrote {page.relative_to(ROOT)}")

    home_template = Template((ROOT / "templates" / "home.html").read_text(encoding="utf-8"))
    (SITE / "index.html").write_text(render_home(home_template, cities), encoding="utf-8")
    (SITE / "404.html").write_text(render_404(cities), encoding="utf-8")
    (SITE / "mentions-legales").mkdir(exist_ok=True)
    (SITE / "mentions-legales" / "index.html").write_text(render_legal(cities), encoding="utf-8")
    write_sources_readme(cities)
    print("Wrote site/index.html, site/404.html")

    today = date.today().isoformat()
    urls = [f"  <url><loc>{SITE_URL}</loc><lastmod>{today}</lastmod></url>"] + [
        f"  <url><loc>{SITE_URL}{city['path']}</loc><lastmod>{city['sources']['builtAt'][:10]}</lastmod></url>" for city in cities
    ]
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n",
        encoding="utf-8",
    )
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n", encoding="utf-8")
    print("Wrote site/sitemap.xml, site/robots.txt")


if __name__ == "__main__":
    main()
