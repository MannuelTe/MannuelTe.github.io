# À portée de tram

Cartes interactives des temps de trajet en **tram** (et, en option, en **bus**) dans les grandes villes françaises.

👉 **https://tram.camilleroux.com/** — [Montpellier](https://tram.camilleroux.com/) · [Bordeaux](https://tram.camilleroux.com/bordeaux/) · [Lyon](https://tram.camilleroux.com/lyon/)

Idée originale : le [NYC Transit Time Cartogram](https://castrio.me/nyc/) d'Anthony Castrio, puis sa
[déclinaison parisienne](https://github.com/JulesGrandin/paris-temps-transport) par Jules Grandin.

Fonctionnalités : heatmap et isochrones depuis un départ déplaçable, arrivée au clic avec itinéraire détaillé
(lignes, correspondances, marche), recherche d'adresse (Base Adresse Nationale) ou de station, tram seul ou
tram + bus, déplacement et zoom de la carte, lien de partage.

## Lancer

```bash
python3 fetch_data.py montpellier   # télécharge les sources dans data/montpellier/ (GTFS, communes, OSM)
python3 build_data.py montpellier   # calcule site/data/montpellier.json
python3 build_pages.py              # génère la page de chaque ville, sitemap.xml et robots.txt
python3 -m http.server 8000 --directory site
```

Puis ouvrir [http://localhost:8000](http://localhost:8000).

Les sources brutes (`data/<ville>/` : GTFS, communes, OSM) ne sont pas versionnées : elles restent en local et
`fetch_data.py <ville>` les retélécharge. Seules les données calculées pour le site (`site/data/<ville>.json`) le sont.

## Provenance des données

Chaque calcul écrit `sources/<ville>.json` (versionné) : URL de chaque fichier source, date de téléchargement, taille et
empreinte SHA-256, période couverte par le GTFS, jour de référence retenu, lignes exclues. `fetch_data.py` tient à jour
le détail des téléchargements dans `data/<ville>/manifest.json`.

| Ville | Réseau | GTFS téléchargé le | Validité du GTFS | Jour de référence | Fiche |
|---|---|---|---|---|---|
| Montpellier | TaM | 2026-10-03 | 2026-09-21 → 2026-12-31 | mardi 2026-11-03 | [sources/montpellier.json](sources/montpellier.json) |
| Bordeaux | TBM | 2026-10-04 | 2026-10-03 → 2027-01-01 | jeudi 2026-11-05 | [sources/bordeaux.json](sources/bordeaux.json) |
| Lyon | TCL | 2026-10-04 (à la main, compte data.grandlyon.com) | 2026-10-04 → 2027-02-01 | mardi 2026-10-06 | [sources/lyon.json](sources/lyon.json) |

Le GTFS TCL n'est pas téléchargeable sans compte : le récupérer sur data.grandlyon.com, le poser dans
`data/lyon/gtfs.zip`, puis lancer `fetch_data.py lyon`, qui l'enregistre comme source manuelle.

## Ajouter une ville

1. Créer `cities/<ville>.json` en partant d'une ville existante : URL du GTFS (sur
   [transport.data.gouv.fr](https://transport.data.gouv.fr/)), code SIREN de l'intercommunalité (`epci`), zones OSM,
   départ par défaut, libellés. `railGeometry` vaut `gtfs` si le GTFS contient `shapes.txt`, sinon `osm`.
2. `python3 fetch_data.py <ville>` puis `python3 build_data.py <ville>` et `python3 build_pages.py`.
3. Vérifier des trajets connus et le jour de référence affiché par `build_data.py`.
4. `python3 tools/render_og.py <ville>` génère l'image d'aperçu (Chrome et ImageMagick requis).

## Données

- GTFS théoriques des réseaux : TaM (Montpellier) et TBM (Bordeaux) via [transport.data.gouv.fr](https://transport.data.gouv.fr/), TCL (Lyon) via [data.grandlyon.com](https://data.grandlyon.com/)
- Tracés des lignes (quand le GTFS n'en fournit pas), eau et parcs : © contributeurs OpenStreetMap (ODbL), via Overpass
- Contours des communes de chaque métropole ([geo.api.gouv.fr](https://geo.api.gouv.fr/))
- Recherche d'adresse côté navigateur : [api-adresse.data.gouv.fr](https://adresse.data.gouv.fr/)
- Mesure d'audience : Cloudflare Web Analytics (sans cookie)

## Modèle

Les temps viennent des horaires GTFS d'un mardi ou jeudi de semaine scolaire type, entre 7 h et 20 h :

- jour de référence = programme de service le plus courant parmi les mardis et jeudis à venir bien remplis ;
- durée de chaque inter-station = médiane des durées planifiées ;
- attente = moitié de l'intervalle moyen entre deux passages à l'arrêt (bornée entre 1 et 15 min) ;
- correspondance = 1,5 min de marche + attente de la ligne suivante ; marche possible entre arrêts proches (< 450 m) ;
- marche à pied à 75 m/min (4,5 km/h) à vol d'oiseau, sans pénalité d'accès (arrêts en surface).

Pas de temps réel ni de perturbations. Les trajets à la demande (TaD) sont exclus.
