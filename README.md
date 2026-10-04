# À portée de tram

Cartes interactives des temps de trajet en **tram** (et, en option, en **bus**) dans les grandes villes françaises.

👉 **https://tram.camilleroux.com/** — [Montpellier](https://tram.camilleroux.com/) · [Bordeaux](https://tram.camilleroux.com/bordeaux/)

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

Le GTFS brut n'est pas versionné (`data/*/gtfs.zip`, jusqu'à plusieurs dizaines de Mo) : `fetch_data.py <ville> --gtfs-only`
le retélécharge. Les autres sources (communes, OSM) sont versionnées pour que le calcul reste reproductible malgré les
caprices d'Overpass.

## Ajouter une ville

1. Créer `cities/<ville>.json` en partant d'une ville existante : URL du GTFS (sur
   [transport.data.gouv.fr](https://transport.data.gouv.fr/)), code SIREN de l'intercommunalité (`epci`), zones OSM,
   départ par défaut, libellés. `railGeometry` vaut `gtfs` si le GTFS contient `shapes.txt`, sinon `osm`.
2. `python3 fetch_data.py <ville>` puis `python3 build_data.py <ville>` et `python3 build_pages.py`.
3. Vérifier des trajets connus et le jour de référence affiché par `build_data.py`.
4. `python3 tools/render_og.py <ville>` génère l'image d'aperçu (Chrome et ImageMagick requis).

## Données

- GTFS théoriques des réseaux ([transport.data.gouv.fr](https://transport.data.gouv.fr/)) : TaM (Montpellier), TBM (Bordeaux)
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
