// Public transport travel time map of Zurich (fork of tram.camilleroux.com by Camille Roux).
// The city shown is described by the #city-config JSON block of the page.

const CITY = JSON.parse(document.getElementById("city-config").textContent);
// The page is German (index.html, the default) or English (en/index.html); every text below comes in both.
const LANG = document.documentElement.lang === "de" ? "de" : "en";
const t = (en, de) => (LANG === "de" ? de : en);
const LOCALE = t("en", "de-CH");
// One bundle per timetable year (network history): data/<city>-<year>.json.
const dataUrl = (year) => new URL(`./data/${CITY.slug}-${year}.json?v=${CITY.timetables[year].version}`, import.meta.url);
const GEOCODER_URL = "https://api3.geo.admin.ch/rest/services/api/SearchServer";

const DEFAULT_FROM = CITY.defaultFrom;
const MODE_LABELS = {
  tram: "Tram",
  metro: "Metro",
  sbahn: "S-Bahn",
  train: t("Train", "Zug"),
  funicular: t("Funicular", "Standseilbahn"),
  cable: t("Cable car", "Luftseilbahn"),
  ferry: t("Boat", "Schiff"),
  busway: "Busway",
  bus: "Bus",
};
const DEFAULT_MAX = 45;
const ISOCHRONE_OPTIONS = [15, 30, 45, 60];
const DEFAULT_ISOCHRONES = [15, 30];
const REACH_MINUTES = 30;
// Au doigt, on vise moins précisément et un tap bouge souvent de quelques pixels.
const MARKER_HIT_RADIUS = { mouse: 18, touch: 30 };
const CLICK_SLOP = { mouse: 5, touch: 12 };
const MIN_ZOOM_FACTOR = 0.5;
const MAX_ZOOM_FACTOR = 14;
const STOP_LABEL_SCALE = 0.13; // pixels par mètre au-delà desquels on nomme les arrêts
const RAIL_NAME_RADIUS = 400; // mètres

// Du plus proche (vert) au plus lointain (rouge) ; au-delà du max : gris.
const PALETTE = [
  [0, [47, 150, 18]],
  [0.25, [126, 200, 80]],
  [0.5, [226, 228, 120]],
  [0.75, [244, 182, 112]],
  [1, [226, 120, 120]],
];
// Au-delà du max, la couleur s'efface progressivement jusqu'à laisser voir le fond.
const BEYOND_FADE = 0.15;
const HEAT_ALPHA = 0.78;
const HEAT_UPSAMPLE = 3;
const LUT_SIZE = 512;
const NEIGHBOURS = [[1, 0], [-1, 0], [0, 1], [0, -1]];
const RIVER_BRIDGE_CELLS = 4; // cases de 200 m : de quoi traverser le Rhône ou la Garonne

const COLORS = {
  background: "#eef0f3",
  land: "#e3e5e9",
  water: "#cfdcea",
  park: "rgba(110, 160, 110, 0.16)",
  communeLine: "rgba(255, 255, 255, 0.95)",
  contour: "#0f05a0", // Zürich blue, like the page accents
  from: "#0f05a0",
  to: "#1f2128",
};

const $ = (id) => document.getElementById(id);
const canvas = $("mapCanvas");
const ctx = canvas.getContext("2d");
const stage = $("mapStage");

const app = {
  data: null,
  graph: null,
  paths: null,
  offset: [0, 0],
  view: { cx: 0, cy: 0, scale: 1, fitScale: 1 },
  size: { width: 0, height: 0, dpr: 1 },
  from: null, // { point, label }
  to: null, // { point, label }
  includeBus: false,
  basemap: true, // faint OSM street layer
  mode: "transit", // "transit" or "bike"
  bike: null, // cycling layer (data/<city>-bike.json), loaded on first use
  year: CITY.defaultTimetable,
  maxMinutes: DEFAULT_MAX,
  isochrones: [...DEFAULT_ISOCHRONES],
  heatFrom: "from", // la heatmap part du départ ou de l'arrivée
  solution: null, // plus courts chemins depuis le départ (panneau, itinéraire)
  heatSolution: null, // plus courts chemins depuis le point d'où part la heatmap
  grid: null,
  heatCanvas: document.createElement("canvas"),
  drag: null,
  pointers: new Map(),
  frameRequested: false,
};

// --- Petites fonctions utilitaires ------------------------------------------

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const hypot = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

function formatMinutes(minutes) {
  if (!Number.isFinite(minutes)) return "—";
  if (minutes < 1) return "< 1 min";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes - hours * 60);
  return `${hours} h ${String(rest).padStart(2, "0")} min`;
}

function paletteColor(t) {
  for (let i = 1; i < PALETTE.length; i += 1) {
    const [stop, color] = PALETTE[i];
    if (t <= stop) {
      const [prevStop, prevColor] = PALETTE[i - 1];
      const mix = (t - prevStop) / (stop - prevStop);
      return prevColor.map((channel, c) => Math.round(channel + (color[c] - channel) * mix));
    }
  }
  return PALETTE[PALETTE.length - 1][1];
}

function metersPerDegree() {
  const lat = 111320;
  return { lat, lon: lat * Math.cos((app.data.meta.lat0 * Math.PI) / 180) };
}

function toWorld(lat, lon) {
  const m = metersPerDegree();
  return [lon * m.lon, lat * m.lat];
}

function toLatLon(point) {
  const m = metersPerDegree();
  return { lat: point[1] / m.lat, lon: point[0] / m.lon };
}

function pointInRing(point, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > point[1] !== yj > point[1] && point[0] < ((xj - xi) * (point[1] - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function pointInPolygon(point, polygon) {
  return pointInRing(point, polygon[0]) && !polygon.slice(1).some((hole) => pointInRing(point, hole));
}

function isOnLand(point) {
  if (app.data.water.some((polygon) => pointInPolygon(point, polygon))) return false;
  return app.data.boroughs.some((commune) => commune.polygons.some((polygon) => pointInPolygon(point, polygon)));
}

function communeAt(point) {
  return app.data.boroughs.find((commune) => commune.polygons.some((polygon) => pointInPolygon(point, polygon)))?.name;
}

// --- Graphe du réseau ---------------------------------------------------------

class MinHeap {
  constructor() {
    this.keys = [];
    this.values = [];
  }

  get size() {
    return this.keys.length;
  }

  push(key, value) {
    const { keys, values } = this;
    let i = keys.length;
    keys.push(key);
    values.push(value);
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (keys[parent] <= key) break;
      keys[i] = keys[parent];
      values[i] = values[parent];
      i = parent;
    }
    keys[i] = key;
    values[i] = value;
  }

  pop() {
    const { keys, values } = this;
    const top = values[0];
    const lastKey = keys.pop();
    const lastValue = values.pop();
    if (keys.length) {
      let i = 0;
      for (;;) {
        let child = 2 * i + 1;
        if (child >= keys.length) break;
        if (child + 1 < keys.length && keys[child + 1] < keys[child]) child += 1;
        if (keys[child] >= lastKey) break;
        keys[i] = keys[child];
        values[i] = values[child];
        i = child;
      }
      keys[i] = lastKey;
      values[i] = lastValue;
    }
    return top;
  }
}

function prepareGraph(data) {
  const count = data.routeStates.length;
  const offsets = new Int32Array(count + 1);
  data.adjacency.forEach((edges, i) => {
    offsets[i + 1] = offsets[i] + edges.length;
  });
  const targets = new Int32Array(offsets[count]);
  const weights = new Float32Array(offsets[count]);
  data.adjacency.forEach((edges, i) => {
    edges.forEach(([target, weight], k) => {
      targets[offsets[i] + k] = target;
      weights[offsets[i] + k] = weight;
    });
  });
  return {
    count,
    offsets,
    targets,
    weights,
    station: Int32Array.from(data.routeStates, (state) => state.stationIndex),
    wait: Float32Array.from(data.routeStates, (state) => state.wait),
    // Accès au quai (escaliers, couloirs du métro), compté à l'entrée comme à la sortie.
    access: Float32Array.from(data.routeStates, (state) => state.access),
    route: data.routeStates.map((state) => state.routeId),
    isBus: Uint8Array.from(data.routeStates, (state) => (data.routeInfo[state.routeId]?.rail ? 0 : 1)),
  };
}

function walkMinutes(meters) {
  return meters / app.data.meta.walkMetersPerMinute;
}

// --- Terrain: hills (Tobler's hiking function) and lakes -----------------------

/** Elevation and lake grids, built once per bundle from the data (cell elevations, lake bitmap). */
function prepareTerrain(data) {
  const { gridCols: cols, gridRows: rows } = data.meta;
  const z = new Float32Array(cols * rows).fill(NaN);
  for (const cell of data.cells) z[cell.row * cols + cell.col] = cell.z ?? 0;
  const lakes = data.lakes;
  const bits = lakes ? Uint8Array.from(atob(lakes.bits), (c) => c.charCodeAt(0)) : null;
  // Points off land (lake shores, the edge of the map) borrow the elevation of their neighbours.
  return { z: fillGaps(z, cols, rows, 6), bits };
}

function elevationAt(point) {
  const { meta } = app.data;
  const [minX, minY, maxX, maxY] = meta.bounds;
  const cols = meta.gridCols;
  const rows = meta.gridRows;
  const gx = clamp(((point[0] - minX) / (maxX - minX)) * cols - 0.5, 0, cols - 1);
  const gy = clamp(((point[1] - minY) / (maxY - minY)) * rows - 0.5, 0, rows - 1);
  const c0 = Math.floor(gx);
  const r0 = Math.floor(gy);
  const c1 = Math.min(c0 + 1, cols - 1);
  const r1 = Math.min(r0 + 1, rows - 1);
  const tx = gx - c0;
  const ty = gy - r0;
  const z = app.terrain.z;
  const at = (r, c) => (Number.isNaN(z[r * cols + c]) ? 0 : z[r * cols + c]);
  return (at(r0, c0) * (1 - tx) + at(r0, c1) * tx) * (1 - ty) + (at(r1, c0) * (1 - tx) + at(r1, c1) * tx) * ty;
}

function isLake(point) {
  const { lakes, meta } = app.data;
  if (!app.terrain.bits) return false;
  const col = Math.floor((point[0] - meta.bounds[0]) / lakes.cell);
  const row = Math.floor((point[1] - meta.bounds[1]) / lakes.cell);
  if (col < 0 || row < 0 || col >= lakes.cols || row >= lakes.rows) return false;
  const index = row * lakes.cols + col;
  return (app.terrain.bits[index >> 3] >> (index & 7)) & 1;
}

function crossesLake(a, b) {
  if (!app.terrain.bits) return false;
  const steps = Math.max(1, Math.ceil(hypot(a, b) / app.data.lakes.step));
  for (let k = 1; k < steps; k += 1) {
    if (isLake([a[0] + ((b[0] - a[0]) * k) / steps, a[1] + ((b[1] - a[1]) * k) / steps])) return true;
  }
  return false;
}

/** Minutes on foot from a to b on the straight line, slower uphill (same model as build_data.py). Lakes are checked
 * separately with crossesLake, only for the candidates that would win: it is the costly part. */
function hillWalk(a, za, b, zb) {
  const meters = hypot(a, b);
  const { k, offset, minRun } = app.data.meta.tobler ?? { k: 0, offset: 0, minRun: 1 };
  const slope = (zb - za) / Math.max(meters, minRun);
  const factor = Math.exp(-k * Math.abs(slope + offset)) / Math.exp(-k * offset);
  return walkMinutes(meters) / factor;
}

function walkBetween(a, za, b, zb) {
  return crossesLake(a, b) ? Infinity : hillWalk(a, za, b, zb);
}

function stationUsable(index) {
  return app.includeBus || app.data.stations[index].rail;
}

/** Plus courts chemins depuis un point : temps d'arrivée à chaque arrêt + prédécesseurs. */
function solveFrom(point) {
  const { graph, data } = app;
  const dist = new Float64Array(graph.count).fill(Infinity);
  const prev = new Int32Array(graph.count).fill(-1);
  const seedWalk = new Float64Array(graph.count);
  const heap = new MinHeap();

  const z = elevationAt(point);
  const seeds = data.stations
    .map((station, index) => ({ index, meters: hypot(point, station.point) }))
    .filter((seed) => stationUsable(seed.index))
    .sort((a, b) => a.meters - b.meters)
    .slice(0, data.meta.originStationCount * 2)
    .map((seed) => ({ ...seed, walk: walkBetween(point, z, data.stations[seed.index].point, data.stations[seed.index].z ?? z) }))
    .filter((seed) => Number.isFinite(seed.walk))
    .sort((a, b) => a.walk - b.walk)
    .slice(0, data.meta.originStationCount);

  for (const seed of seeds) {
    for (const state of data.stationStates[seed.index]) {
      if (!app.includeBus && graph.isBus[state]) continue;
      const walk = seed.walk + graph.access[state];
      const time = walk + graph.wait[state];
      if (time < dist[state]) {
        dist[state] = time;
        seedWalk[state] = walk;
        heap.push(time, state);
      }
    }
  }

  while (heap.size) {
    const state = heap.pop();
    const base = dist[state];
    for (let e = graph.offsets[state]; e < graph.offsets[state + 1]; e += 1) {
      const next = graph.targets[e];
      if (!app.includeBus && graph.isBus[next]) continue;
      const time = base + graph.weights[e];
      if (time < dist[next]) {
        dist[next] = time;
        prev[next] = state;
        heap.push(time, next);
      }
    }
  }

  const stationTime = new Float64Array(data.stations.length).fill(Infinity);
  const stationBest = new Int32Array(data.stations.length).fill(-1);
  // Temps pour ressortir dans la rue à chaque arrêt (le métro demande de remonter du quai).
  for (let state = 0; state < graph.count; state += 1) {
    const station = graph.station[state];
    const out = dist[state] + graph.access[state];
    if (out < stationTime[station]) {
      stationTime[station] = out;
      stationBest[station] = state;
    }
  }
  return { point, z, dist, prev, seedWalk, stationTime, stationBest };
}

// --- Bike: shortest paths over the cell graph (build_bike.py) ----------------------

const bikeUrl = () => new URL(`./data/${CITY.slug}-bike.json?v=${CITY.bikeVersion}`, import.meta.url);

async function loadBike() {
  if (app.bike) return app.bike;
  const bike = await (await fetch(bikeUrl())).json();
  const count = bike.adjacency.length;
  const offsets = new Int32Array(count + 1);
  bike.adjacency.forEach((edges, i) => {
    offsets[i + 1] = offsets[i] + edges.length;
  });
  const targets = new Int32Array(offsets[count]);
  const weights = new Float32Array(offsets[count]);
  bike.adjacency.forEach((edges, i) => {
    edges.forEach(([target, weight], k) => {
      targets[offsets[i] + k] = target;
      weights[offsets[i] + k] = weight;
    });
  });
  const snap = Float32Array.from(bike.snap, (value) => value ?? Infinity);
  app.bike = { meta: bike.meta, offsets, targets, weights, snap };
  return app.bike;
}

/** Index of the land cell under a point, or of the nearest land cell around it. */
function cellAt(point) {
  const { meta, mask } = app.data;
  const [minX, minY, maxX, maxY] = meta.bounds;
  const col = Math.floor(((point[0] - minX) / (maxX - minX)) * meta.gridCols);
  const row = Math.floor(((point[1] - minY) / (maxY - minY)) * meta.gridRows);
  for (let ring = 0; ring <= 3; ring += 1) {
    for (let dr = -ring; dr <= ring; dr += 1) {
      for (let dc = -ring; dc <= ring; dc += 1) {
        if (Math.max(Math.abs(dr), Math.abs(dc)) !== ring) continue;
        const r = row + dr;
        const c = col + dc;
        if (r < 0 || c < 0 || r >= meta.gridRows || c >= meta.gridCols) continue;
        const index = mask[r * meta.gridCols + c];
        if (index >= 0) return index;
      }
    }
  }
  return -1;
}

/** Bike times from a point: push to the nearest street, unlock, ride over the cell graph. */
function solveBike(point) {
  const bike = app.bike;
  const count = bike.snap.length;
  const time = new Float64Array(count).fill(Infinity);
  const heap = new MinHeap();
  const start = cellAt(point);
  if (start >= 0) {
    time[start] = bike.meta.parkMinutes + bike.snap[start];
    heap.push(time[start], start);
  }
  while (heap.size) {
    const cell = heap.pop();
    const base = time[cell];
    for (let e = bike.offsets[cell]; e < bike.offsets[cell + 1]; e += 1) {
      const next = bike.targets[e];
      const t = base + bike.weights[e];
      if (t < time[next]) {
        time[next] = t;
        heap.push(t, next);
      }
    }
  }
  // Arriving: from the street to the cell centre, and park.
  const arrive = Float64Array.from(time, (t, i) => t + bike.snap[i] + bike.meta.parkMinutes);
  return { bike: true, point, z: elevationAt(point), cellTime: arrive };
}

function solve(point) {
  return app.mode === "bike" ? solveBike(point) : solveFrom(point);
}

function travelBike(solution, point) {
  const cell = cellAt(point);
  const byBike = cell >= 0 ? solution.cellTime[cell] : Infinity;
  const byFoot = walkBetween(solution.point, solution.z, point, elevationAt(point));
  return byFoot <= byBike ? { minutes: byFoot, station: -1, walk: byFoot } : { minutes: byBike, station: -1, bike: true };
}

/** Meilleur temps vers un point quelconque : à pied direct, ou via l'arrêt le plus favorable. */
function travelTo(solution, point) {
  if (solution.bike) return travelBike(solution, point);
  const z = elevationAt(point);
  const direct = walkBetween(solution.point, solution.z, point, z);
  let best = { minutes: direct, station: -1, walk: direct };
  const candidates = [];
  app.data.stations.forEach((station, index) => {
    const arrival = solution.stationTime[index];
    if (!Number.isFinite(arrival)) return;
    const walk = hillWalk(station.point, station.z ?? z, point, z);
    if (arrival + walk < best.minutes) candidates.push({ minutes: arrival + walk, station: index, walk });
  });
  // Best first; the lake check only runs until a candidate passes.
  candidates.sort((a, b) => a.minutes - b.minutes);
  return candidates.find((c) => c.minutes < best.minutes && !crossesLake(app.data.stations[c.station].point, point)) ?? best;
}

function routeLabel(routeId) {
  const info = app.data.routeInfo[routeId];
  const mode = MODE_LABELS[info.mode] ?? t("Line", "Linie");
  // S-Bahn and train names already say what they are (S3, IC5).
  return /^[A-Z]/.test(info.name) ? info.name : `${mode} ${info.name}`;
}

/** Reconstitue l'itinéraire (marche, lignes, correspondances) vers un point. */
function buildItinerary(solution, point) {
  const { graph, data } = app;
  const result = travelTo(solution, point);
  if (result.bike) {
    const km = (hypot(solution.point, point) / 1000).toFixed(1);
    return { minutes: result.minutes, steps: [{ kind: "bike", text: t(`By bike (${km} km as the crow flies)`, `Mit dem Velo (${km.replace(".", ",")} km Luftlinie)`), minutes: result.minutes }] };
  }
  if (result.station === -1) {
    return { minutes: result.minutes, steps: [{ kind: "walk", text: t("Walk all the way", "Ganzer Weg zu Fuss"), minutes: result.minutes }] };
  }

  const chain = [];
  for (let state = solution.stationBest[result.station]; state !== -1; state = solution.prev[state]) chain.push(state);
  chain.reverse();

  const name = (state) => data.stations[graph.station[state]].name;
  // A ride into a terminus arrives in the line's end state (build_data.split_terminating): same line, same leg.
  const line = (state) => data.routeInfo[graph.route[state]]?.endOf ?? graph.route[state];
  const steps = [{ kind: "walk", text: t(`Walk to ${name(chain[0])}`, `Zu Fuss nach ${name(chain[0])}`), minutes: solution.seedWalk[chain[0]] }];
  let legStart = chain[0];
  const closeLeg = (legEnd) => {
    steps.push({
      kind: "ride",
      route: graph.route[legStart],
      text: `${name(legStart)} → ${name(legEnd)}`,
      wait: graph.wait[legStart],
      minutes: solution.dist[legEnd] - solution.dist[legStart],
    });
  };
  for (let i = 1; i < chain.length; i += 1) {
    const from = chain[i - 1];
    const to = chain[i];
    if (line(from) === line(to) && graph.station[from] !== graph.station[to]) continue;
    closeLeg(from);
    if (graph.station[from] !== graph.station[to]) {
      const a = data.stations[graph.station[from]];
      const b = data.stations[graph.station[to]];
      steps.push({ kind: "walk", text: t(`Walk to ${name(to)} to change`, `Zu Fuss nach ${name(to)} zum Umsteigen`), minutes: hillWalk(a.point, a.z ?? 0, b.point, b.z ?? 0) });
    }
    legStart = to;
  }
  closeLeg(chain[chain.length - 1]);
  // La sortie du quai (métro) est comptée avec la marche finale.
  const exit = graph.access[chain[chain.length - 1]];
  steps.push({ kind: "walk", text: t("Walk to the destination", "Zu Fuss zum Ziel"), minutes: result.walk + exit });
  return { minutes: result.minutes, steps };
}

// --- Grille des temps ---------------------------------------------------------

/** Comble les cases sans valeur (eau, hors carte) avec la moyenne de leurs voisines, `passes` fois. */
function fillGaps(values, cols, rows, passes) {
  const filled = Float32Array.from(values);
  for (let pass = 0; pass < passes; pass += 1) {
    const source = Float32Array.from(filled);
    for (let index = 0; index < source.length; index += 1) {
      if (!Number.isNaN(source[index])) continue;
      const row = Math.floor(index / cols);
      const col = index % cols;
      let sum = 0;
      let count = 0;
      for (const [dr, dc] of NEIGHBOURS) {
        const r = row + dr;
        const c = col + dc;
        if (r < 0 || c < 0 || r >= rows || c >= cols) continue;
        const value = source[r * cols + c];
        if (!Number.isNaN(value)) {
          sum += value;
          count += 1;
        }
      }
      if (count) filled[index] = sum / count;
    }
  }
  return filled;
}

function computeGrid(solution) {
  const { cells, meta } = app.data;
  const { gridCols: cols, gridRows: rows } = meta;
  const times = new Float32Array(cols * rows).fill(NaN);
  if (solution.bike) {
    cells.forEach((cell, i) => {
      let best = solution.cellTime[i];
      const direct = hillWalk(solution.point, solution.z, cell.point, cell.z ?? solution.z);
      if (direct < best && !crossesLake(solution.point, cell.point)) best = direct;
      times[cell.row * cols + cell.col] = best;
    });
    const bridged = fillGaps(times, cols, rows, RIVER_BRIDGE_CELLS);
    return { times, smooth: smoothGrid(bridged, cols, rows), cols, rows, contours: {} };
  }
  for (const cell of cells) {
    // cell.access: nearby stops with the walk from the stop to the cell, in minutes (hills and lakes included).
    let best = Infinity;
    for (const [station, minutes] of cell.access) {
      const time = solution.stationTime[station] + minutes;
      if (time < best) best = time;
    }
    const direct = hillWalk(solution.point, solution.z, cell.point, cell.z ?? solution.z);
    if (direct < best && !crossesLake(solution.point, cell.point)) best = direct;
    times[cell.row * cols + cell.col] = best;
  }
  // Les isochrones enjambent les fleuves (comblés avec les valeurs des rives) au lieu d'en faire le tour ;
  // elles sont ensuite découpées sur la terre ferme au dessin.
  const bridged = fillGaps(times, cols, rows, RIVER_BRIDGE_CELLS);
  return { times, smooth: smoothGrid(bridged, cols, rows), cols, rows, contours: {} };
}

/** Moyenne 3×3 limitée à la terre ferme, pour des isochrones moins crénelées. */
function smoothGrid(times, cols, rows) {
  const out = new Float32Array(times.length).fill(NaN);
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const index = row * cols + col;
      if (Number.isNaN(times[index])) continue;
      let sum = 0;
      let weight = 0;
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dx = -1; dx <= 1; dx += 1) {
          const r = row + dy;
          const c = col + dx;
          if (r < 0 || c < 0 || r >= rows || c >= cols) continue;
          const value = times[r * cols + c];
          if (Number.isNaN(value)) continue;
          const w = dx === 0 && dy === 0 ? 2 : 1;
          sum += value * w;
          weight += w;
        }
      }
      out[index] = sum / weight;
    }
  }
  return out;
}

/** Peint la grille dans une image (HEAT_UPSAMPLE² pixels par cellule, interpolation bilinéaire). */
function paintHeat(grid, { fast = false } = {}) {
  const { cols, rows, times } = grid;
  const upsample = fast ? 1 : HEAT_UPSAMPLE;
  const heat = app.heatCanvas;
  heat.width = cols * upsample;
  heat.height = rows * upsample;
  const heatCtx = heat.getContext("2d");
  const image = heatCtx.createImageData(heat.width, heat.height);

  // Étend les valeurs d'un cran hors de la terre pour que le lissage ne fonce pas les côtes.
  const filled = fillGaps(times, cols, rows, 2);

  // Table de couleurs précalculée : t ∈ [0, 1 + BEYOND_FADE] découpé en LUT_SIZE pas.
  const lutMax = 1 + BEYOND_FADE;
  const lut = new Uint32Array(LUT_SIZE);
  const lutBytes = new Uint8Array(lut.buffer);
  for (let i = 0; i < LUT_SIZE; i += 1) {
    const t = (i / (LUT_SIZE - 1)) * lutMax;
    const [r, g, b] = paletteColor(Math.min(t, 1));
    const alpha = t <= 1 ? 255 : Math.round(clamp(1 - (t - 1) / BEYOND_FADE, 0, 1) * 255);
    lutBytes.set([r, g, b, alpha], i * 4);
  }
  const pixels = new Uint32Array(image.data.buffer);
  const toLut = (LUT_SIZE - 1) / (app.maxMinutes * lutMax);
  const step = 1 / upsample;
  const width = heat.width;
  for (let y = 0; y < heat.height; y += 1) {
    const gy = (y + 0.5) * step - 0.5;
    const row0 = clamp(Math.floor(gy), 0, rows - 1);
    const row1 = Math.min(row0 + 1, rows - 1);
    const ty = clamp(gy - row0, 0, 1);
    for (let x = 0; x < width; x += 1) {
      const gx = (x + 0.5) * step - 0.5;
      const col0 = clamp(Math.floor(gx), 0, cols - 1);
      const col1 = Math.min(col0 + 1, cols - 1);
      const tx = clamp(gx - col0, 0, 1);
      const v00 = filled[row0 * cols + col0];
      const v01 = filled[row0 * cols + col1];
      const v10 = filled[row1 * cols + col0];
      const v11 = filled[row1 * cols + col1];
      let sum = 0;
      let weight = 0;
      let w = (1 - tx) * (1 - ty);
      if (v00 === v00) { sum += v00 * w; weight += w; }
      w = tx * (1 - ty);
      if (v01 === v01) { sum += v01 * w; weight += w; }
      w = (1 - tx) * ty;
      if (v10 === v10) { sum += v10 * w; weight += w; }
      w = tx * ty;
      if (v11 === v11) { sum += v11 * w; weight += w; }
      if (weight < 0.25) continue;
      const index = Math.round((sum / weight) * toLut);
      if (index < LUT_SIZE) pixels[y * width + x] = lut[index];
    }
  }
  heatCtx.putImageData(image, 0, 0);
}

const CONTOUR_MARGIN = 1; // minutes

/** Marching squares sur les centres de cellules ; renvoie des segments en coordonnées monde. */
function contourSegments(grid, threshold) {
  const { cols, rows, smooth, times } = grid;
  const [minX, minY, maxX, maxY] = app.data.meta.bounds;
  const cellW = (maxX - minX) / cols;
  const cellH = (maxY - minY) / rows;
  // The smoothed value shapes the line, but a cell clearly on one side of the threshold by its own time (a narrow
  // fast strip, such as a bus along a hillside) is not averaged across it. Cells within CONTOUR_MARGIN of the
  // threshold keep the smoothed value, so ordinary edges stay smooth instead of snapping to cell centres.
  const value = (row, col) => {
    const v = smooth[row * cols + col];
    if (Number.isNaN(v)) return Infinity;
    const own = times[row * cols + col];
    if (Number.isNaN(own)) return v;
    if (own <= threshold - CONTOUR_MARGIN) return Math.min(v, threshold - CONTOUR_MARGIN / 2);
    if (own >= threshold + CONTOUR_MARGIN) return Math.max(v, threshold + CONTOUR_MARGIN / 2);
    return v;
  };
  const center = (row, col) => [minX + (col + 0.5) * cellW, minY + (row + 0.5) * cellH];
  const between = (pa, va, pb, vb) => {
    const t = Number.isFinite(va) && Number.isFinite(vb) ? clamp((threshold - va) / (vb - va), 0, 1) : 0.5;
    return [pa[0] + (pb[0] - pa[0]) * t, pa[1] + (pb[1] - pa[1]) * t];
  };

  const segments = [];
  for (let row = 0; row < rows - 1; row += 1) {
    for (let col = 0; col < cols - 1; col += 1) {
      // Coins dans le sens trigonométrique : bas-gauche, bas-droite, haut-droite, haut-gauche.
      const corners = [
        [center(row, col), value(row, col)],
        [center(row, col + 1), value(row, col + 1)],
        [center(row + 1, col + 1), value(row + 1, col + 1)],
        [center(row + 1, col), value(row + 1, col)],
      ];
      const inside = corners.map(([, v]) => v <= threshold);
      const crossings = [];
      for (let k = 0; k < 4; k += 1) {
        const a = corners[k];
        const b = corners[(k + 1) % 4];
        if (inside[k] !== inside[(k + 1) % 4]) crossings.push(between(a[0], a[1], b[0], b[1]));
      }
      if (crossings.length === 2) segments.push(crossings);
      else if (crossings.length === 4) segments.push([crossings[0], crossings[1]], [crossings[2], crossings[3]]);
    }
  }
  return segments;
}

// --- Vue et rendu -------------------------------------------------------------

function buildPaths(data) {
  const [ox, oy] = app.offset;
  const ringPath = (path, ring) => {
    ring.forEach(([x, y], i) => (i ? path.lineTo(x - ox, y - oy) : path.moveTo(x - ox, y - oy)));
    path.closePath();
  };
  const polygonsPath = (polygons) => {
    const path = new Path2D();
    for (const polygon of polygons) for (const ring of polygon) ringPath(path, ring);
    return path;
  };
  // Neighbouring communes overlap slightly along their borders. Under "evenodd" each overlap would be a hole (no
  // land, no heat), so land is filled "nonzero" with outer rings wound one way and holes the other.
  const signedArea = (ring) => ring.reduce((sum, [x, y], i) => {
    const [nx, ny] = ring[(i + 1) % ring.length];
    return sum + x * ny - nx * y;
  }, 0);
  const landPath = (polygons) => {
    const path = new Path2D();
    for (const polygon of polygons) {
      polygon.forEach((ring, k) => {
        const outer = k === 0;
        ringPath(path, (signedArea(ring) > 0) === outer ? ring : [...ring].reverse());
      });
    }
    return path;
  };
  const communeLines = new Path2D();
  for (const commune of data.boroughs) for (const ring of commune.outline) ringPath(communeLines, ring);

  const routes = new Map();
  for (const route of data.routes) {
    if (!routes.has(route.id)) routes.set(route.id, { color: route.color, path: new Path2D() });
    const { path } = routes.get(route.id);
    route.points.forEach(([x, y], i) => (i ? path.lineTo(x - ox, y - oy) : path.moveTo(x - ox, y - oy)));
  }
  // Everything except water (a frame around the map plus the water rings, "evenodd"): isochrones are clipped with it.
  const notWater = new Path2D();
  const [minX, minY, maxX, maxY] = data.meta.bounds;
  ringPath(notWater, [[minX - 1e4, minY - 1e4], [maxX + 1e4, minY - 1e4], [maxX + 1e4, maxY + 1e4], [minX - 1e4, maxY + 1e4]]);
  for (const polygon of data.water) for (const ring of polygon) ringPath(notWater, ring);
  return {
    notWater,
    land: landPath(data.boroughs.flatMap((commune) => commune.polygons)),
    // Terres voisines des villes côtières : ce qui reste découvert autour est la mer.
    context: polygonsPath(data.context ?? []),
    // Un chemin par polygone, rempli en « evenodd » : les îles (trous) restent de la terre ferme,
    // sans que deux plans d'eau qui se chevauchent s'annulent.
    water: data.water.map((polygon) => polygonsPath([polygon])),
    parks: data.parks.map((polygon) => polygonsPath([polygon])),
    communeLines,
    routes: [...routes.values()].reverse(),
  };
}

function project(point) {
  const { cx, cy, scale } = app.view;
  return [app.size.width / 2 + (point[0] - cx) * scale, app.size.height / 2 - (point[1] - cy) * scale];
}

function unproject(x, y) {
  const { cx, cy, scale } = app.view;
  return [cx + (x - app.size.width / 2) / scale, cy - (y - app.size.height / 2) / scale];
}

function fitView() {
  const [minX, minY, maxX, maxY] = app.data.meta.viewBounds;
  const { width, height } = app.size;
  const pad = width < 720 ? 12 : 40;
  const scale = Math.min((width - pad * 2) / (maxX - minX), (height - pad * 2) / (maxY - minY));
  app.view = { cx: (minX + maxX) / 2, cy: (minY + maxY) / 2, scale, fitScale: scale };
}

function zoomAt(factor, screenX, screenY) {
  const before = unproject(screenX, screenY);
  const { fitScale } = app.view;
  app.view.scale = clamp(app.view.scale * factor, fitScale * MIN_ZOOM_FACTOR, fitScale * MAX_ZOOM_FACTOR);
  const after = unproject(screenX, screenY);
  app.view.cx += before[0] - after[0];
  app.view.cy += before[1] - after[1];
  requestRender();
}

/** Passe le contexte en coordonnées monde (mètres, origine décalée, axe y vers le nord). */
function useWorldTransform() {
  const { cx, cy, scale } = app.view;
  const { width, height, dpr } = app.size;
  const [ox, oy] = app.offset;
  ctx.setTransform(
    dpr * scale,
    0,
    0,
    -dpr * scale,
    dpr * (width / 2 + (ox - cx) * scale),
    dpr * (height / 2 - (oy - cy) * scale),
  );
}

function useScreenTransform() {
  const { dpr } = app.size;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function drawHaloText(text, x, y, { font, color, halo = "rgba(255,255,255,0.92)", width = 3.5 }) {
  ctx.font = font;
  ctx.lineJoin = "round";
  ctx.strokeStyle = halo;
  ctx.lineWidth = width;
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}

function drawIsochrones() {
  if (!app.grid || !app.isochrones.length) return;
  const px = 1 / app.view.scale;
  const [ox, oy] = app.offset;
  const labels = [];
  for (const threshold of [...app.isochrones].sort((a, b) => a - b)) {
    app.grid.contours[threshold] ??= contourSegments(app.grid, threshold);
    const segments = app.grid.contours[threshold];
    if (!segments.length) continue;
    useWorldTransform();
    const path = new Path2D();
    for (const [a, b] of segments) {
      path.moveTo(a[0] - ox, a[1] - oy);
      path.lineTo(b[0] - ox, b[1] - oy);
    }
    ctx.save();
    // Land, then everything but water: the district polygons include part of the lake.
    ctx.clip(app.paths.land, "nonzero");
    ctx.clip(app.paths.notWater, "evenodd");
    ctx.lineCap = "round";
    ctx.strokeStyle = "rgba(255,255,255,0.8)";
    ctx.lineWidth = 4.5 * px;
    ctx.stroke(path);
    ctx.strokeStyle = COLORS.contour;
    ctx.lineWidth = (threshold >= 30 ? 2 : 1.4) * px;
    ctx.stroke(path);
    ctx.restore();

    // Étiquette sur le point le plus au nord de la courbe encore visible, à l'écart des marqueurs
    // et des étiquettes déjà posées.
    const avoid = [app.from, app.to].filter(Boolean).map((place) => project(place.point));
    avoid.push(...labels.map((label) => label.at));
    const candidates = [];
    for (const [a, b] of segments) {
      const world = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      const [x, y] = project(world);
      if (x < 60 || x > app.size.width - 60 || y < 24 || y > app.size.height - 24) continue;
      if (avoid.some(([ax, ay]) => Math.abs(x - ax) < 70 && y - ay > -60 && y - ay < 40)) continue;
      candidates.push({ world, at: [x, y] });
    }
    candidates.sort((p, q) => p.at[1] - q.at[1]);
    const best = candidates.find((candidate) => isOnLand(candidate.world));
    if (best) labels.push({ text: `${threshold} min`, at: best.at });
  }
  useScreenTransform();
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const { text, at } of labels) {
    drawHaloText(text, at[0], at[1], { font: "600 12px Inter, sans-serif", color: COLORS.contour, width: 5 });
  }
}

function drawStops() {
  const { stations } = app.data;
  if (app.includeBus) {
    // Bus stops: small white dots with a grey ring, a step below the rail stops.
    const busRadius = app.view.scale > STOP_LABEL_SCALE ? 2.4 : 1.6;
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(60, 64, 75, 0.75)";
    ctx.fillStyle = "#fff";
    for (const station of stations) {
      if (station.rail) continue;
      const [x, y] = project(station.point);
      if (x < -5 || y < -5 || x > app.size.width + 5 || y > app.size.height + 5) continue;
      ctx.beginPath();
      ctx.arc(x, y, busRadius, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }
  const radius = app.view.scale > STOP_LABEL_SCALE ? 3.2 : 2.2;
  for (const station of stations) {
    if (!station.rail) continue;
    const [x, y] = project(station.point);
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = "#fff";
    ctx.fill();
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = "#333";
    ctx.stroke();
  }
  if (app.view.scale > STOP_LABEL_SCALE) {
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    for (const station of stations) {
      if (!station.rail) continue;
      const [x, y] = project(station.point);
      if (x < -50 || y < -20 || x > app.size.width + 50 || y > app.size.height + 20) continue;
      drawHaloText(station.name, x + 6, y, { font: "500 11px Inter, sans-serif", color: "#333" });
    }
  }
}

function drawCommuneNames() {
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const font = `600 ${app.view.scale > app.view.fitScale * 2 ? 13 : 10.5}px Inter, sans-serif`;
  for (const commune of app.data.boroughs) {
    const [x, y] = project(commune.label);
    if (x < 0 || y < 0 || x > app.size.width || y > app.size.height) continue;
    drawHaloText(commune.name.toUpperCase(), x, y, { font, color: "rgba(40, 40, 40, 0.55)", halo: "rgba(255,255,255,0.6)" });
  }
}

function drawMarker(point, color, label) {
  const [x, y] = project(point);
  ctx.beginPath();
  ctx.arc(x, y, 15, 0, Math.PI * 2);
  ctx.fillStyle = `${color}2e`;
  ctx.fill();
  ctx.beginPath();
  ctx.arc(x, y, 8, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.lineWidth = 3;
  ctx.strokeStyle = "#fff";
  ctx.stroke();
  if (!label) return;
  ctx.font = "700 12px Inter, sans-serif";
  const width = ctx.measureText(label).width + 16;
  const left = clamp(x - width / 2, 6, app.size.width - width - 6);
  const top = y - 42;
  ctx.beginPath();
  ctx.roundRect(left, top, width, 22, 7);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, left + width / 2, top + 11.5);
}

// --- Base map: OpenStreetMap streets, drawn faintly over the heatmap ----------------------------------------

// From the same OSM street network as the bike layer (build_bike.py → data/<city>-streets.json), so no tile
// service or key is needed. Multiplied in soft grey; minor streets and paths fade in as the map is zoomed.
const STREET_STYLE = {
  major: { width: 1.6, alpha: 0.32, from: 0 },
  minor: { width: 0.9, alpha: 0.26, from: 1.8 },
  path: { width: 0.7, alpha: 0.18, from: 3.5, dash: [2, 3] },
};
const STREET_INK = "#5b5f6b";

async function loadStreets() {
  const payload = await (await fetch(new URL(`./data/${CITY.slug}-streets.json?v=${CITY.streetsVersion}`, import.meta.url))).json();
  const [ox0, oy0] = payload.origin;
  const paths = {};
  for (const [kind, lines] of Object.entries(payload.classes)) {
    const path = new Path2D();
    for (const flat of lines) {
      let x = ox0;
      let y = oy0;
      for (let i = 0; i < flat.length; i += 2) {
        x += flat[i];
        y += flat[i + 1];
        // Paths are built in the canvas's offset world frame, like the other layers.
        if (i === 0) path.moveTo(x - app.offset[0], y - app.offset[1]);
        else path.lineTo(x - app.offset[0], y - app.offset[1]);
      }
    }
    paths[kind] = path;
  }
  app.streets = paths;
  requestRender();
}

// Bus and trolleybus lines (OSM route relations, data/<city>-buses.json), drawn thin under the rail lines when
// buses are on; only the lines that run in the selected timetable year.
async function loadBusLines() {
  const payload = await (await fetch(new URL(`./data/${CITY.slug}-buses.json?v=${CITY.busesVersion}`, import.meta.url))).json();
  const [ox0, oy0] = payload.origin;
  app.busLines = Object.entries(payload.lines).map(([ref, line]) => {
    const path = new Path2D();
    for (const flat of line.ways) {
      let x = ox0;
      let y = oy0;
      for (let i = 0; i < flat.length; i += 2) {
        x += flat[i];
        y += flat[i + 1];
        if (i === 0) path.moveTo(x - app.offset[0], y - app.offset[1]);
        else path.lineTo(x - app.offset[0], y - app.offset[1]);
      }
    }
    return { ref, color: line.color, path };
  });
  requestRender();
}

function drawBusLines() {
  if (!app.includeBus || !app.busLines) return;
  const running = new Set(
    Object.values(app.data.routeInfo)
      .filter((info) => !info.rail && !info.trunkOf)
      .map((info) => info.name),
  );
  const px = 1 / app.view.scale;
  const zoom = app.view.scale / app.view.fitScale;
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.globalAlpha = 0.75;
  ctx.lineWidth = (zoom > 3 ? 2 : 1.4) * px;
  for (const line of app.busLines) {
    if (!running.has(line.ref)) continue;
    ctx.strokeStyle = line.color;
    ctx.stroke(line.path);
  }
  ctx.restore();
}

function drawStreets() {
  if (!app.basemap || !app.streets) return;
  const zoom = app.view.scale / app.view.fitScale;
  const px = 1 / app.view.scale;
  ctx.save();
  ctx.globalCompositeOperation = "multiply";
  ctx.strokeStyle = STREET_INK;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const [kind, style] of Object.entries(STREET_STYLE)) {
    const path = app.streets[kind];
    if (!path || zoom < style.from) continue;
    // Fade in over one zoom step past the threshold; widths grow gently with zoom.
    const fade = style.from ? clamp((zoom - style.from) / style.from, 0, 1) : 1;
    ctx.globalAlpha = style.alpha * fade;
    ctx.lineWidth = style.width * Math.min(2, 0.8 + zoom / 8) * px;
    ctx.setLineDash(style.dash ? style.dash.map((d) => d * px) : []);
    ctx.stroke(path);
  }
  ctx.restore();
}

function render() {
  app.frameRequested = false;
  if (!app.data) return;
  const { width, height, dpr } = app.size;
  const px = 1 / app.view.scale;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  // Villes côtières : le fond est la mer, et les terres voisines sont dessinées par-dessus.
  const sea = app.data.meta.sea;
  ctx.fillStyle = sea ? COLORS.water : COLORS.background;
  ctx.fillRect(0, 0, width, height);

  useWorldTransform();
  if (sea) {
    ctx.fillStyle = COLORS.background;
    ctx.fill(app.paths.context);
  }
  ctx.fillStyle = COLORS.land;
  ctx.fill(app.paths.land, "nonzero");

  if (app.grid) {
    const [minX, minY, maxX, maxY] = app.data.meta.bounds;
    const [ox, oy] = app.offset;
    ctx.save();
    ctx.clip(app.paths.land, "nonzero");
    ctx.globalAlpha = HEAT_ALPHA;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    // L'image a sa ligne 0 au sud : avec l'axe y inversé, elle se dessine dans le bon sens.
    ctx.drawImage(app.heatCanvas, minX - ox, minY - oy, maxX - minX, maxY - minY);
    ctx.restore();
  }
  drawStreets();

  ctx.fillStyle = COLORS.park;
  for (const park of app.paths.parks) ctx.fill(park, "evenodd");
  // Boundaries first, water over them: Zurich's districts (Kreise) reach into the lake.
  ctx.strokeStyle = COLORS.communeLine;
  ctx.lineWidth = 1.1 * px;
  ctx.stroke(app.paths.communeLines);
  ctx.fillStyle = COLORS.water;
  for (const water of app.paths.water) ctx.fill(water, "evenodd");

  drawBusLines();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const route of app.paths.routes) {
    ctx.strokeStyle = route.color;
    ctx.lineWidth = 3 * px;
    ctx.stroke(route.path);
  }

  drawIsochrones();
  useScreenTransform();
  drawCommuneNames();
  drawStops();
  if (app.to) {
    const minutes = app.solution ? formatMinutes(travelTo(app.solution, app.to.point).minutes) : null;
    drawMarker(app.to.point, COLORS.to, app.heatFrom === "to" ? `${t("Destination", "Ziel")} · ${minutes}` : minutes);
  }
  if (app.from) drawMarker(app.from.point, COLORS.from, t("Start", "Start"));
}

function requestRender() {
  if (app.frameRequested) return;
  app.frameRequested = true;
  requestAnimationFrame(render);
}

function resize() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const first = !app.size.width;
  const ratio = app.size.width ? rect.width / app.size.width : 1;
  app.size = { width: rect.width, height: rect.height, dpr };
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  if (!app.data) return;
  if (first) {
    fitView();
  } else {
    app.view.scale *= ratio;
    app.view.fitScale *= ratio;
  }
  requestRender();
}

// --- État, panneau et URL -------------------------------------------------------

/** Nom de lieu : la station de tram/métro proche si elle existe (plus parlante qu'un arrêt de bus), sinon l'arrêt le plus proche. */
function nearestStopName(point) {
  let best = null;
  let bestDistance = Infinity;
  let rail = null;
  let railDistance = Infinity;
  for (const station of app.data.stations) {
    const d = hypot(point, station.point);
    if (d < bestDistance) {
      bestDistance = d;
      best = station.name;
    }
    if (station.rail && d < railDistance) {
      railDistance = d;
      rail = station.name;
    }
  }
  return railDistance <= RAIL_NAME_RADIUS ? rail : best;
}

function describePlace(point) {
  const stop = nearestStopName(point);
  const commune = communeAt(point);
  const near = t("Near", "Bei");
  return commune ? `${near} ${stop} (${commune})` : `${near} ${stop}`;
}

function heatSource() {
  return app.heatFrom === "to" && app.to ? app.to : app.from;
}

function recompute({ fast = false } = {}) {
  if (!app.from) return;
  app.solution = solve(app.from.point);
  app.heatSolution = heatSource() === app.from ? app.solution : solve(app.to.point);
  app.grid = computeGrid(app.heatSolution);
  paintHeat(app.grid, { fast });
  updatePanel();
  if (!fast) {
    updateCounts();
    scheduleYears();
  }
  requestRender();
}

function setFrom(point, label = null, { quiet = false, fast = false } = {}) {
  if (!isOnLand(point)) return false;
  app.from = { point, label: label || describePlace(point) };
  recompute({ fast });
  if (!quiet) syncUrl();
  return true;
}

function setTo(point, label = null, { quiet = false, fast = false } = {}) {
  if (!isOnLand(point)) return false;
  app.to = { point, label: label || describePlace(point) };
  if (app.heatFrom === "to") {
    recompute({ fast });
  } else {
    updatePanel();
    if (!fast) scheduleYears();
    requestRender();
  }
  if (!quiet) syncUrl();
  return true;
}

function removeTo() {
  app.to = null;
  scheduleYears();
  setHeatFrom("from");
  syncUrl();
}

function setHeatFrom(source) {
  app.heatFrom = source === "to" && app.to ? "to" : "from";
  for (const button of $("heatFrom").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.source === app.heatFrom));
  }
  recompute();
}

function updatePanel() {
  $("tripFrom").textContent = app.from?.label ?? "—";
  const result = $("tripResult");
  if (!app.to || !app.solution) {
    result.hidden = true;
    $("tripHint").hidden = false;
  } else {
    const itinerary = buildItinerary(app.solution, app.to.point);
    result.hidden = false;
    $("tripHint").hidden = true;
    $("tripTo").textContent = app.to.label;
    $("tripDuration").textContent = formatMinutes(itinerary.minutes);
    $("tripSteps").replaceChildren(
      ...itinerary.steps
        .filter((step) => step.kind === "ride" || step.minutes >= 0.5)
        .map((step) => {
          const item = document.createElement("li");
          const badge = document.createElement("span");
          badge.className = "badge";
          if (step.kind === "ride") {
            const info = app.data.routeInfo[step.route];
            badge.textContent = info.name;
            badge.style.background = info.color;
            badge.style.color = contrastText(info.color);
            badge.title = routeLabel(step.route);
          } else {
            badge.classList.add("walk");
            badge.textContent = step.kind === "bike" ? "🚲" : "🚶";
          }
          const text = document.createElement("span");
          text.textContent = step.kind === "ride" ? `${step.text} · ${t("wait", "warten")} ~${Math.round(step.wait)} min` : step.text;
          const minutes = document.createElement("span");
          minutes.className = "minutes";
          minutes.textContent = formatMinutes(step.minutes);
          item.append(badge, text, minutes);
          return item;
        }),
    );
  }

  if (app.heatSolution) {
    const source = heatSource();
    const tram = app.data.stations.map((station, index) => ({ station, index })).filter(({ station }) => station.rail);
    const where = source === app.from ? t("this start", "diesem Start") : t("this destination", "diesem Ziel");
    if (app.heatSolution.bike) {
      const reachable = tram.filter(({ station }) => travelBike(app.heatSolution, station.point).minutes <= REACH_MINUTES).length;
      const percent = Math.round((reachable / tram.length) * 100);
      setReach(percent, t(
        ` of ${CITY.railStations} are less than ${REACH_MINUTES} minutes by bike from ${where}.`,
        ` der ${CITY.railStations} sind mit dem Velo weniger als ${REACH_MINUTES} Minuten von ${where} entfernt.`,
      ));
    } else {
      const reachable = tram.filter(({ station, index }) => {
        const byFoot = walkBetween(source.point, elevationAt(source.point), station.point, station.z ?? 0);
        return Math.min(byFoot, app.heatSolution.stationTime[index]) <= REACH_MINUTES;
      }).length;
      const percent = Math.round((reachable / tram.length) * 100);
      const bus = app.includeBus ? t(` (with ${CITY.busNoun})`, ` (mit ${CITY.busNoun})`) : "";
      setReach(percent, t(
        ` of ${CITY.railStations} are less than ${REACH_MINUTES} minutes from ${where}${bus}.`,
        ` der ${CITY.railStations} sind weniger als ${REACH_MINUTES} Minuten von ${where} entfernt${bus}.`,
      ));
    }
  }
}

/** "82% of the stops…", the share in the accent colour. */
function setReach(percent, rest) {
  const share = document.createElement("strong");
  share.className = "reach-share";
  share.textContent = `${percent}%`;
  $("reach").replaceChildren(share, rest);
}

// German has no short form for thousands (410'200, not 410k): compact still shortens millions (1,2 Mio.).
const compact = new Intl.NumberFormat(LOCALE, { notation: "compact", maximumFractionDigits: 1 });

/** Residents and jobs in the cells reached within each isochrone (BFS hectare grids, summed per cell). */
function reachCounts(grid, limits) {
  const { cells } = app.data;
  const totals = { pop: 0, jobs: 0 };
  const within = limits.map(() => ({ pop: 0, jobs: 0 }));
  for (const cell of cells) {
    const time = grid.times[cell.row * grid.cols + cell.col];
    totals.pop += cell.pop ?? 0;
    totals.jobs += cell.jobs ?? 0;
    limits.forEach((limit, k) => {
      if (time <= limit) {
        within[k].pop += cell.pop ?? 0;
        within[k].jobs += cell.jobs ?? 0;
      }
    });
  }
  return { totals, within };
}

// --- Across the years: this trip on every timetable year (section 03) --------------

const BIKE_YEARS_NOTE = t(
  "The bike network is today's; switch to public transport to compare timetable years.",
  "Das Velonetz ist das heutige; wechsle zum ÖV, um Fahrplanjahre zu vergleichen.",
);
const LOADING_TIMETABLES = t("Loading the timetables…", "Fahrpläne werden geladen…");
const ON_FOOT = t("on foot", "zu Fuss");

let yearsTimer = null;
let yearsVisible = false; // the other years' bundles load only once the section scrolls into view

function scheduleYears() {
  clearTimeout(yearsTimer);
  yearsTimer = setTimeout(() => updateYears().catch((error) => console.error(error)), 250);
}

/** Runs `fn` with another year's network in place of the current one (the routing functions read `app`). */
function withYear(prepared, fn) {
  const saved = [app.data, app.graph, app.terrain];
  [app.data, app.graph, app.terrain] = [prepared.data, prepared.graph, prepared.terrain];
  try {
    return fn();
  } finally {
    [app.data, app.graph, app.terrain] = saved;
  }
}

/** The current start (and destination) on every timetable year: trip time, lines, residents and jobs within 30 min. */
async function tripAcrossYears() {
  const years = Object.keys(CITY.timetables);
  const prepared = await Promise.all(years.map((year) => bundle(year)));
  return years.map((year, k) =>
    withYear(prepared[k], () => {
      const solution = solveFrom(app.from.point);
      const itinerary = app.to ? buildItinerary(solution, app.to.point) : null;
      const counts = reachCounts(computeGrid(solution), [30]).within[0];
      const lines = itinerary
        ? itinerary.steps.filter((step) => step.kind === "ride").map((step) => app.data.routeInfo[step.route])
        : [];
      return { year, minutes: itinerary?.minutes, lines, pop: counts.pop, jobs: counts.jobs };
    }),
  );
}

async function updateYears() {
  if (app.from && insights.page?.id === "trip") renderInsight().catch((error) => console.error(error));
  if (!yearsVisible || !app.from) return;
  const body = $("yearsBody");
  if (app.mode === "bike") {
    body.textContent = BIKE_YEARS_NOTE;
    return;
  }
  body.textContent = LOADING_TIMETABLES;
  const rows = await tripAcrossYears();
  const longest = Math.max(...rows.map((row) => row.minutes ?? 0), 1);
  const table = document.createElement("table");
  table.className = "data-table years-table";
  table.innerHTML = `<thead><tr><th scope="col">${t("Timetable", "Fahrplan")}</th>${
    app.to ? `<th scope="col">${t("This trip", "Diese Fahrt")}</th><th scope="col">${t("Lines", "Linien")}</th>` : ""
  }
    <th scope="col">${t("Residents within 30 min", "Einwohner innert 30 min")}</th><th scope="col">${t("Jobs within 30 min", "Arbeitsplätze innert 30 min")}</th></tr></thead>`;
  const tbody = document.createElement("tbody");
  rows.forEach((row, k) => {
    const tr = document.createElement("tr");
    if (row.year === app.year) tr.className = "current";
    const cell = (content) => {
      const td = document.createElement("td");
      if (typeof content === "string") td.textContent = content;
      else td.append(...content);
      tr.append(td);
    };
    cell(CITY.timetables[row.year].draft ? `${row.year} (${t("draft", "Entwurf")})` : row.year);
    if (app.to) {
      const bar = document.createElement("span");
      bar.className = "year-bar";
      bar.style.width = `${Math.round((row.minutes / longest) * 160)}px`; // longest trip = 160 px
      const previous = rows[k - 1]?.minutes;
      const change = Math.round(row.minutes) - Math.round(previous ?? row.minutes);
      const delta = previous == null ? "" : change === 0 ? t(" (same)", " (gleich)") : ` (${change > 0 ? "+" : "−"}${formatMinutes(Math.abs(change))})`;
      const label = document.createElement("span");
      label.textContent = formatMinutes(row.minutes) + delta;
      cell([label, bar]);
      cell(
        row.lines.length
          ? row.lines.map((info) => {
              const badge = document.createElement("span");
              badge.className = "badge";
              badge.textContent = info.name;
              badge.style.background = info.color;
              badge.style.color = contrastText(info.color);
              return badge;
            })
          : ON_FOOT,
      );
    }
    const previous = rows[k - 1];
    const change = (value, before) => (before == null ? "" : ` (${value - before >= 0 ? "+" : "−"}${compact.format(Math.abs(value - before))})`);
    cell(compact.format(row.pop) + change(row.pop, previous?.pop));
    cell(compact.format(row.jobs) + change(row.jobs, previous?.jobs));
    tbody.append(tr);
  });
  table.append(tbody);
  const note = document.createElement("p");
  note.className = "table-note";
  const start = app.from.label.replace(new RegExp(`^${t("Near", "Bei")} `), "");
  note.textContent = t(
    `Counted from the start on the map (${start})${app.to ? " with the same destination" : ""} on each year's timetable${
      app.includeBus ? ", buses and boats included" : ", tram and train only"
    }, so they match "The network over time" below only for a start at ${CITY.center} without buses. Residents and jobs held at today's numbers; changes are against the previous row.${
      app.to ? "" : " Click the map to add a destination."
    }`,
    `Gezählt ab dem Start auf der Karte (${start})${app.to ? " mit demselben Ziel" : ""} auf dem Fahrplan jedes Jahres${
      app.includeBus ? ", Busse und Schiffe inbegriffen" : ", nur Tram und Zug"
    }; sie stimmen mit «Das Netz im Wandel» unten also nur für einen Start ab ${CITY.center} ohne Busse überein. Einwohner und Arbeitsplätze auf heutigem Stand; Änderungen gegenüber der Zeile davor.${
      app.to ? "" : " Klicke auf die Karte, um ein Ziel hinzuzufügen."
    }`,
  );
  body.replaceChildren(table, note);
}

function updateCounts() {
  const table = $("reachCounts");
  if (!table || !app.grid) return;
  const limits = app.isochrones.length ? [...app.isochrones].sort((a, b) => a - b) : [30];
  const { totals, within } = reachCounts(app.grid, limits);
  const share = (value, total) => (total ? ` (${Math.round((value / total) * 100)}%)` : "");
  const row = (label, counts) => {
    const tr = document.createElement("tr");
    for (const text of [label, compact.format(counts.pop) + share(counts.pop, totals.pop), compact.format(counts.jobs) + share(counts.jobs, totals.jobs)]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.append(td);
    }
    return tr;
  };
  table.querySelector("tbody").replaceChildren(
    ...limits.map((limit, k) => row(t(`Within ${limit} min`, `Innert ${limit} min`), within[k])),
    row(t("Whole map", "Ganze Karte"), totals),
  );
}

function contrastText(hex) {
  const value = parseInt(hex.slice(1), 16);
  const luminance = 0.299 * (value >> 16) + 0.587 * ((value >> 8) & 255) + 0.114 * (value & 255);
  return luminance > 150 ? "#111" : "#fff";
}

function updateLegend() {
  const stops = PALETTE.map(([t, [r, g, b]]) => `rgb(${r}, ${g}, ${b}) ${Math.round(t * 100)}%`);
  $("legendBar").style.background = `linear-gradient(90deg, ${stops.join(", ")})`;
  $("legendMid").textContent = `${Math.round(app.maxMinutes / 2)} min`;
  $("legendMax").textContent = `${app.maxMinutes} min`;
  $("maxValue").textContent = `${app.maxMinutes} min`;
}

function formatPair(point) {
  const { lat, lon } = toLatLon(point);
  return `${lat.toFixed(5)},${lon.toFixed(5)}`;
}

function parsePair(value) {
  const match = /^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/.exec(value || "");
  return match ? toWorld(Number(match[1]), Number(match[2])) : null;
}

function syncUrl() {
  const params = new URLSearchParams();
  if (app.from) params.set("from", formatPair(app.from.point));
  if (app.to) params.set("to", formatPair(app.to.point));
  if (app.to && app.heatFrom === "to") params.set("map", "to");
  if (app.mode === "bike") params.set("mode", "bike");
  if (app.year !== CITY.defaultTimetable) params.set("year", app.year);
  if (app.includeBus) params.set("bus", "1");
  if (!app.basemap) params.set("streets", "0");
  if (app.maxMinutes !== DEFAULT_MAX) params.set("max", String(app.maxMinutes));
  const iso = [...app.isochrones].sort((a, b) => a - b).join(",");
  if (iso !== DEFAULT_ISOCHRONES.join(",")) params.set("iso", iso || "0");
  const query = params.toString().replaceAll("%2C", ",");
  history.replaceState(null, "", query ? `?${query}` : location.pathname);
}

// The language switch keeps the same start, destination and settings (the query string syncUrl maintains).
document.querySelector(".lang-switch")?.addEventListener("click", (event) => {
  event.currentTarget.search = location.search;
});

function restoreFromUrl() {
  const params = new URLSearchParams(location.search);
  app.includeBus = params.get("bus") === "1";
  app.basemap = params.get("streets") !== "0";
  $("basemapToggle").checked = app.basemap;
  $("busToggle").checked = app.includeBus;
  const max = Number(params.get("max"));
  if (max >= 20 && max <= 90) app.maxMinutes = max;
  $("maxRange").value = String(app.maxMinutes);
  if (params.has("iso")) {
    app.isochrones = params
      .get("iso")
      .split(",")
      .map(Number)
      .filter((value) => ISOCHRONE_OPTIONS.includes(value));
  }
  for (const input of $("isoToggles").querySelectorAll("input")) input.checked = app.isochrones.includes(Number(input.value));
  updateLegend();

  const from = parsePair(params.get("from"));
  if (!from || !setFrom(from, null, { quiet: true })) {
    setFrom(toWorld(DEFAULT_FROM.lat, DEFAULT_FROM.lon), DEFAULT_FROM.label, { quiet: true });
  }
  const to = parsePair(params.get("to"));
  if (to && setTo(to, null, { quiet: true }) && params.get("map") === "to") setHeatFrom("to");
}

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    element.hidden = true;
  }, 2200);
}

// --- Interactions sur la carte ----------------------------------------------

function eventPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return [event.clientX - rect.left, event.clientY - rect.top];
}

function pointerKind(event) {
  return event.pointerType === "mouse" ? "mouse" : "touch";
}

function markerAt(screen, kind = "mouse") {
  for (const key of ["to", "from"]) {
    if (app[key] && hypot(screen, project(app[key].point)) <= MARKER_HIT_RADIUS[kind]) return key;
  }
  return null;
}

canvas.addEventListener("pointerdown", (event) => {
  const screen = eventPoint(event);
  app.pointers.set(event.pointerId, screen);
  canvas.setPointerCapture(event.pointerId);
  if (app.pointers.size === 2) {
    const [a, b] = [...app.pointers.values()];
    app.drag = { kind: "pinch", distance: hypot(a, b) };
    return;
  }
  const pointer = pointerKind(event);
  const marker = markerAt(screen, pointer);
  app.drag = marker
    ? { kind: "marker", marker, start: screen }
    : { kind: "pan", start: screen, last: screen, moved: false, slop: CLICK_SLOP[pointer] };
  // Saisir un marqueur recentre la heatmap sur lui, comme sur la version parisienne.
  if (marker && marker !== app.heatFrom) setHeatFrom(marker);
});

canvas.addEventListener("pointermove", (event) => {
  const screen = eventPoint(event);
  if (app.pointers.has(event.pointerId)) app.pointers.set(event.pointerId, screen);
  const drag = app.drag;

  if (!drag) {
    canvas.classList.toggle("over-marker", Boolean(markerAt(screen)));
    return;
  }
  if (drag.kind === "pinch" && app.pointers.size === 2) {
    const [a, b] = [...app.pointers.values()];
    const distance = hypot(a, b);
    zoomAt(distance / drag.distance, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
    drag.distance = distance;
  } else if (drag.kind === "marker") {
    const world = unproject(...screen);
    if (drag.marker === "from") setFrom(world, null, { quiet: true, fast: true });
    else setTo(world, null, { quiet: true, fast: true });
  } else if (drag.kind === "pan") {
    if (!drag.moved && hypot(screen, drag.start) < drag.slop) return;
    drag.moved = true;
    canvas.classList.add("panning");
    app.view.cx -= (screen[0] - drag.last[0]) / app.view.scale;
    app.view.cy += (screen[1] - drag.last[1]) / app.view.scale;
    drag.last = screen;
    requestRender();
  }
});

function endPointer(event) {
  app.pointers.delete(event.pointerId);
  const drag = app.drag;
  if (!drag) return;
  if (drag.kind === "pinch") {
    if (!app.pointers.size) app.drag = null;
    return;
  }
  app.drag = null;
  canvas.classList.remove("panning");
  if (event.type === "pointercancel") return;
  if (drag.kind === "pan" && !drag.moved) {
    if (!setTo(unproject(...eventPoint(event)))) toast(t("This point is outside the map or on water.", "Dieser Punkt liegt ausserhalb der Karte oder auf dem Wasser."));
  } else if (drag.kind === "marker") {
    recompute();
    syncUrl();
  }
}

canvas.addEventListener("dblclick", (event) => {
  if (markerAt(eventPoint(event)) === "to") removeTo();
});

canvas.addEventListener("pointerup", endPointer);
canvas.addEventListener("pointercancel", endPointer);
canvas.addEventListener(
  "wheel",
  (event) => {
    event.preventDefault();
    const [x, y] = eventPoint(event);
    zoomAt(Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0018)), x, y);
  },
  { passive: false },
);

// --- Commandes ----------------------------------------------------------------

$("zoomIn").addEventListener("click", () => zoomAt(1.4, app.size.width / 2, app.size.height / 2));
$("zoomOut").addEventListener("click", () => zoomAt(1 / 1.4, app.size.width / 2, app.size.height / 2));
$("recenter").addEventListener("click", () => {
  fitView();
  requestRender();
});
// L'iPhone ne sait pas passer un élément de page en plein écran : on masque le bouton.
$("fullscreen").hidden = !document.fullscreenEnabled;
$("fullscreen").addEventListener("click", () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else stage.requestFullscreen?.();
});

$("basemapToggle").addEventListener("change", (event) => {
  app.basemap = event.target.checked;
  requestRender();
  syncUrl();
});

$("busToggle").addEventListener("change", (event) => {
  app.includeBus = event.target.checked;
  recompute();
  syncUrl();
});

$("isoToggles").addEventListener("change", () => {
  app.isochrones = [...$("isoToggles").querySelectorAll("input:checked")].map((input) => Number(input.value));
  updateCounts();
  requestRender();
  syncUrl();
});

$("maxRange").addEventListener("input", (event) => {
  app.maxMinutes = Number(event.target.value);
  updateLegend();
  if (app.grid) paintHeat(app.grid);
  requestRender();
  syncUrl();
});

$("swap").addEventListener("click", () => {
  if (!app.to) {
    toast(t("Click the map to set a destination first.", "Klicke zuerst auf die Karte, um ein Ziel zu setzen."));
    return;
  }
  [app.from, app.to] = [app.to, app.from];
  setHeatFrom("from");
  syncUrl();
});

$("removeTo").addEventListener("click", removeTo);
$("heatFrom").addEventListener("click", (event) => {
  const source = event.target.closest("button")?.dataset.source;
  if (source && source !== app.heatFrom) {
    setHeatFrom(source);
    syncUrl();
  }
});

$("locate").addEventListener("click", () => {
  if (!navigator.geolocation) {
    toast(t("Geolocation is not available.", "Standortbestimmung ist nicht verfügbar."));
    return;
  }
  navigator.geolocation.getCurrentPosition(
    ({ coords }) => {
      if (!setFrom(toWorld(coords.latitude, coords.longitude), t("My location", "Mein Standort"))) toast(t("You are outside the map.", "Du bist ausserhalb der Karte."));
    },
    () => toast(t("Could not get your location.", "Dein Standort konnte nicht bestimmt werden.")),
  );
});

$("share").addEventListener("click", async () => {
  const url = location.href;
  if (navigator.share) {
    try {
      await navigator.share({ title: document.title, url });
      return;
    } catch {
      /* partage annulé : on retombe sur la copie */
    }
  }
  try {
    await navigator.clipboard.writeText(url);
    toast(t("Link copied!", "Link kopiert!"));
  } catch {
    toast(url);
  }
});

// --- Address search (geo.admin.ch, swisstopo) --------------------------------

const searchInput = $("searchInput");
const searchResults = $("searchResults");
let searchTimer = null;
let searchController = null;

function normalize(text) {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** Arrêts de tram dont le nom contient tous les mots tapés. */
function searchStops(query) {
  const words = normalize(query).split(" ");
  return app.data.stations
    .filter((station) => station.rail && words.every((word) => normalize(station.name).includes(word)))
    .slice(0, 3)
    .map((station) => ({
      label: station.name,
      context: `Stop · ${station.routes
        .filter((id) => app.data.routeInfo[id]?.rail)
        .map((id) => routeLabel(id))
        .join(", ")}`,
      point: station.point,
    }));
}

async function searchAddress(query) {
  const stops = searchStops(query);
  searchController?.abort();
  searchController = new AbortController();
  // The search box only filters with a bounding box in Swiss coordinates (LV95); results still carry lat/lon.
  const params = new URLSearchParams({
    searchText: query,
    type: "locations",
    origins: "address,gazetteer,zipcode",
    limit: "8",
    sr: "2056",
    bbox: CITY.searchBbox.join(","),
  });
  let payload = { results: [] };
  try {
    const response = await fetch(`${GEOCODER_URL}?${params}`, { signal: searchController.signal });
    payload = await response.json();
  } catch (error) {
    if (error.name === "AbortError" || !stops.length) throw error;
  }
  // Labels come as HTML (« Bahnhofstrasse 1 <b>8001 Zürich</b> »): street first, the bold part as context.
  const plain = (html) => new DOMParser().parseFromString(html, "text/html").body.textContent.trim();
  const addresses = (payload.results ?? [])
    .map(({ attrs }) => {
      const [street, place] = attrs.label.split("<b>");
      const title = plain(street) || plain(attrs.label);
      return { label: title, context: place ? plain(`<b>${place}`) : attrs.origin, point: toWorld(attrs.lat, attrs.lon) };
    })
    .filter((result) => isOnLand(result.point));
  return [...stops, ...addresses].slice(0, 7);
}

function showResults(results) {
  searchResults.replaceChildren(
    ...results.map((result) => {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = result.label;
      const context = document.createElement("small");
      context.textContent = result.context;
      button.append(context);
      button.addEventListener("click", () => chooseResult(result));
      item.append(button);
      return item;
    }),
  );
  searchResults.hidden = !results.length;
}

function chooseResult(result) {
  searchResults.hidden = true;
  searchInput.value = result.label;
  setFrom(result.point, result.label);
  const [sx, sy] = project(result.point);
  if (sx < 0 || sy < 0 || sx > app.size.width || sy > app.size.height) {
    [app.view.cx, app.view.cy] = result.point;
    requestRender();
  }
}

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const query = searchInput.value.trim();
  if (query.length < 3) {
    searchResults.hidden = true;
    return;
  }
  searchTimer = setTimeout(async () => {
    try {
      showResults(await searchAddress(query));
    } catch (error) {
      if (error.name !== "AbortError") searchResults.hidden = true;
    }
  }, 250);
});

$("searchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = searchInput.value.trim();
  if (query.length < 3) return;
  try {
    const results = await searchAddress(query);
    if (results.length) chooseResult(results[0]);
    else toast(t("No address found on the map.", "Keine Adresse auf der Karte gefunden."));
  } catch (error) {
    if (error.name !== "AbortError") toast(t("The address search is not responding.", "Die Adresssuche antwortet nicht."));
  }
});

document.addEventListener("click", (event) => {
  if (!$("searchForm").contains(event.target)) searchResults.hidden = true;
});

// --- Démarrage ----------------------------------------------------------------

const bundles = new Map();

/** A timetable year's data with its routing graph and terrain, fetched and prepared once. */
function bundle(year) {
  if (!bundles.has(year)) {
    bundles.set(
      year,
      fetch(dataUrl(year))
        .then((response) => response.json())
        .then((data) => ({ data, graph: prepareGraph(data), terrain: prepareTerrain(data) })),
    );
  }
  return bundles.get(year);
}

async function loadYear(year) {
  const { data, graph, terrain } = await bundle(year);
  app.year = year;
  app.data = data;
  app.offset = [data.meta.bounds[0], data.meta.bounds[1]];
  app.graph = graph;
  app.terrain = terrain;
  app.paths = buildPaths(data);
  for (const button of $("yearPicker").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.year === year));
  }
  $("yearNote").textContent = CITY.timetables[year].label;
}

/** Switch timetable year, keeping the start, destination and settings: the map shows how the network changed. */
async function switchYear(year) {
  if (year === app.year || !CITY.timetables[year]) return;
  await loadYear(year);
  if (app.from) app.from.label = describePlace(app.from.point);
  if (app.to) app.to.label = describePlace(app.to.point);
  recompute();
  syncUrl();
  if (insights.page && insights.page.id !== "trip") renderInsight();
}

// --- Insights: the network over time as a line chart (after beautifului.dev's insight card) -------------

// One series per chart. The mark is a lighter step of Zürich blue: #0F05A0 itself is too dark for a data mark
// (OKLab L 0.33, below the 0.43–0.77 band); #3b32d4 passes lightness, chroma and contrast on the chart surface.
const SERIES_COLOR = "#3b32d4";
const CHART_SURFACE = "#f6f7f9";
const SVG_NS = "http://www.w3.org/2000/svg";

const HISTORY = CITY.history ?? [];
const metricOf = (key, limit) => (row) =>
  key === "stops" ? row[`within${limit}`] : (row[key === "pop" ? "population" : "jobs"][limit] ?? 0);
const METRICS = {
  pop: { label: t("Residents", "Einwohner"), noun: t("residents", "Einwohner"), format: (v) => compact.format(v) },
  jobs: { label: t("Jobs", "Arbeitsplätze"), noun: t("jobs", "Arbeitsplätze"), format: (v) => compact.format(v) },
  stops: { label: t("Rail stops", "Haltestellen"), noun: t("points of rail stops", "Prozentpunkte der Haltestellen"), format: (v) => `${v}%` },
};
// German nouns keep their capital in the middle of a sentence.
const lowerNoun = (noun) => (LANG === "de" ? noun : noun.toLowerCase());

const INSIGHT_PAGES = [
  { id: "reach30", limit: "30", metrics: ["pop", "jobs", "stops"], title: (m) => t(`${METRICS[m].label} within 30 min of ${CITY.center}`, `${METRICS[m].label} innert 30 min ab ${CITY.center}`) },
  { id: "reach15", limit: "15", metrics: ["pop", "jobs", "stops"], title: (m) => t(`${METRICS[m].label} within 15 min of ${CITY.center}`, `${METRICS[m].label} innert 15 min ab ${CITY.center}`) },
  {
    id: "network",
    metrics: ["railStations", "tramLines"],
    labels: { railStations: t("Rail stops", "Haltestellen"), tramLines: t("Tram lines", "Tramlinien") },
    title: (m) => (m === "railStations" ? t("Tram and rail stops on the map", "Tram- und Bahnhaltestellen auf der Karte") : t("Tram lines in service", "Tramlinien in Betrieb")),
  },
  { id: "trip", metrics: ["minutes"], labels: { minutes: t("Trip time", "Reisezeit") }, title: () => t("This trip, year by year", "Diese Fahrt, Jahr für Jahr") },
];

const insights = { index: 0, metric: {}, page: null, trip: null };

function signed(value, format) {
  if (Math.round(value * 10) === 0) return "±0";
  return `${value > 0 ? "+" : "−"}${format(Math.abs(value))}`;
}

/** Monotone cubic segments through the points (no overshoot between years), one "C…" command per interval.
 * Tangents come from all the points, so a path split into solid and dashed parts stays smooth at the join. */
function smoothSegments(points) {
  const n = points.length;
  if (n < 2) return [];
  const dx = [];
  const slope = [];
  for (let i = 0; i < n - 1; i += 1) {
    dx.push(points[i + 1][0] - points[i][0]);
    slope.push((points[i + 1][1] - points[i][1]) / dx[i]);
  }
  const tangent = [slope[0]];
  for (let i = 1; i < n - 1; i += 1) tangent.push(slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2);
  tangent.push(slope[n - 2]);
  return points.slice(0, -1).map(([x0, y0], i) => {
    const [x1, y1] = points[i + 1];
    const h = dx[i] / 3;
    return `C${x0 + h},${y0 + tangent[i] * h} ${x1 - h},${y1 - tangent[i + 1] * h} ${x1},${y1}`;
  });
}

/** Path from point `from` to point `to` along the smooth segments. */
function segmentPath(points, segments, from, to) {
  return `M${points[from][0]},${points[from][1]} ${segments.slice(from, to).join(" ")}`;
}

/** About `count` round tick values (1, 2, 2.5 or 5 × 10^k apart) covering lo…hi. */
function niceTicks(lo, hi, count = 4) {
  const raw = (hi - lo) / count || 1;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw);
  const ticks = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 1e-9; v += step) ticks.push(Number(v.toPrecision(12)));
  if (ticks.at(-1) < hi) ticks.push(Number((ticks.at(-1) + step).toPrecision(12)));
  return ticks;
}

function svg(tag, attributes = {}, parent = null) {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  parent?.append(element);
  return element;
}

/** Rows {year, draft, value, note} → a line chart: hairline at the first year's value, ringed points, a dashed
 * segment into the draft year, a crosshair tooltip that snaps to the nearest year, arrow keys on focus. */
function drawLineChart(container, rows, format, reference) {
  container.replaceChildren();
  const width = Math.max(260, container.clientWidth);
  const height = 190;
  const values = rows.map((row) => row.value);
  const span = Math.max(...values) - Math.min(...values) || Math.abs(values[0]) * 0.1 || 1;
  const ticks = niceTicks(Math.min(...values) - span * 0.2, Math.max(...values) + span * 0.2);
  const lo = ticks[0];
  const hi = ticks.at(-1);
  const tickLabels = ticks.map((v) => format(v));
  // Gutter for the y labels: 6.6 px per character of the 11 px mono font, plus the gap to the plot.
  const pad = { top: 18, right: 22, bottom: 26, left: Math.max(...tickLabels.map((t) => t.length)) * 6.6 + 14 };
  const inset = 14; // first and last year sit off the axis lines
  const x = (i) => pad.left + inset + (i / (rows.length - 1)) * (width - pad.left - pad.right - inset);
  const y = (v) => pad.top + (1 - (v - lo) / (hi - lo)) * (height - pad.top - pad.bottom);
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, class: "line-chart" }, container);
  ticks.forEach((v, k) => {
    svg("line", { x1: pad.left, x2: width - pad.right + inset, y1: y(v), y2: y(v), class: k === 0 ? "chart-axis" : "chart-grid" }, root);
    const label = svg("text", { x: pad.left - 8, y: y(v) + 3.5, class: "chart-tick", "text-anchor": "end" }, root);
    label.textContent = tickLabels[k];
  });
  svg("line", { x1: pad.left, x2: pad.left, y1: pad.top, y2: height - pad.bottom, class: "chart-axis" }, root);
  svg("line", { x1: pad.left, x2: width - pad.right + inset, y1: y(reference), y2: y(reference), class: "chart-ref" }, root);
  const points = rows.map((row, i) => [x(i), y(row.value)]);
  const segments = smoothSegments(points);
  const draft = rows.findIndex((row) => row.draft);
  const solidEnd = draft === -1 ? points.length - 1 : draft - 1;
  if (solidEnd > 0) svg("path", { d: segmentPath(points, segments, 0, solidEnd), class: "chart-line", stroke: SERIES_COLOR }, root);
  if (draft > 0) svg("path", { d: segmentPath(points, segments, draft - 1, points.length - 1), class: "chart-line draft", stroke: SERIES_COLOR }, root);
  rows.forEach((row, i) => {
    const last = i === rows.length - 1;
    const label = svg("text", { x: last ? width - 4 : x(i), y: height - 8, class: "chart-tick", "text-anchor": last ? "end" : "middle" }, root);
    label.textContent = row.draft ? `${row.year} ${t("draft", "Entwurf")}` : row.year;
  });
  const crosshair = svg("line", { y1: pad.top - 10, y2: height - pad.bottom, class: "chart-crosshair", visibility: "hidden" }, root);
  const dots = rows.map((row, i) =>
    svg("circle", {
      cx: points[i][0],
      cy: points[i][1],
      r: row.current ? 5.5 : 4,
      class: "chart-dot",
      fill: row.draft ? CHART_SURFACE : SERIES_COLOR,
      stroke: row.draft ? SERIES_COLOR : CHART_SURFACE,
    }, root),
  );
  const tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  tooltip.hidden = true;
  container.append(tooltip);
  let active = -1;
  const show = (i) => {
    active = i;
    crosshair.setAttribute("x1", points[i][0]);
    crosshair.setAttribute("x2", points[i][0]);
    crosshair.setAttribute("visibility", "visible");
    dots.forEach((dot, k) => dot.classList.toggle("active", k === i));
    const value = document.createElement("strong");
    value.textContent = format(rows[i].value);
    const line = document.createElement("span");
    line.className = "tooltip-row";
    const key = document.createElement("span");
    key.className = "line-key";
    key.style.background = SERIES_COLOR;
    const caption = document.createElement("span");
    caption.textContent = `${rows[i].year}${rows[i].draft ? ` (${t("draft", "Entwurf")})` : ""}${rows[i].note ? ` · ${rows[i].note}` : ""}`;
    line.append(key, caption);
    tooltip.replaceChildren(value, line);
    tooltip.hidden = false;
    const left = clamp(points[i][0] - tooltip.offsetWidth / 2, 0, width - tooltip.offsetWidth);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${Math.max(0, points[i][1] - tooltip.offsetHeight - 14)}px`;
  };
  const hide = () => {
    active = -1;
    crosshair.setAttribute("visibility", "hidden");
    dots.forEach((dot) => dot.classList.remove("active"));
    tooltip.hidden = true;
  };
  const hit = svg("rect", { x: 0, y: 0, width, height, fill: "transparent" }, root);
  hit.addEventListener("pointermove", (event) => {
    const bounds = root.getBoundingClientRect();
    const px = ((event.clientX - bounds.left) / bounds.width) * width;
    let nearest = 0;
    points.forEach(([pointX], i) => {
      if (Math.abs(pointX - px) < Math.abs(points[nearest][0] - px)) nearest = i;
    });
    show(nearest);
  });
  hit.addEventListener("pointerleave", hide);
  container.tabIndex = 0;
  container.onkeydown = (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    show(clamp((active === -1 ? rows.length - 1 : active) + (event.key === "ArrowRight" ? 1 : -1), 0, rows.length - 1));
  };
  container.onblur = hide;
}

function pageRows(page, metric) {
  if (page.id === "trip") {
    return (insights.trip ?? [])
      .filter((row) => Number.isFinite(row.minutes))
      .map((row) => ({
        year: row.year,
        draft: Boolean(HISTORY.find((h) => h.year === row.year)?.draft),
        value: Math.round(row.minutes),
        note: row.lines.map((info) => info.name).join(" → ") || ON_FOOT,
      }));
  }
  if (page.id === "network") return HISTORY.map((row) => ({ year: row.year, draft: row.draft, value: row[metric], note: row.day }));
  return HISTORY.map((row) => ({ year: row.year, draft: row.draft, value: metricOf(metric, page.limit)(row), note: row.day }));
}

function showInsightMessage(text) {
  $("insLede").textContent = text;
  $("insChart").replaceChildren();
  for (const id of ["insRef", "insValue", "insDelta", "insVs", "insArrow"]) $(id).textContent = "";
}

async function renderInsight() {
  const page = INSIGHT_PAGES[insights.index];
  insights.page = page;
  const metric = insights.metric[page.id] ?? page.metrics[0];
  $("insCount").textContent = `${insights.index + 1}/${INSIGHT_PAGES.length}`;
  $("insTitle").textContent = page.title(metric);
  $("insTag").textContent = `${HISTORY[0]?.year} → ${HISTORY[HISTORY.length - 1]?.year}`;
  const picker = $("insMetric");
  picker.replaceChildren(
    ...page.metrics.map((key) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = page.labels?.[key] ?? METRICS[key].label;
      button.setAttribute("aria-pressed", String(key === metric));
      button.addEventListener("click", () => {
        insights.metric[page.id] = key;
        renderInsight();
      });
      return button;
    }),
  );
  picker.hidden = page.metrics.length < 2;
  if (page.id === "trip") {
    if (!app.to || !app.from) {
      showInsightMessage(t(
        "Click the map to set a destination: this card then follows your trip across the timetable years.",
        "Klicke auf die Karte, um ein Ziel zu setzen: Diese Karte folgt dann deiner Fahrt durch die Fahrplanjahre.",
      ));
      return;
    }
    if (app.mode === "bike") {
      showInsightMessage(BIKE_YEARS_NOTE);
      return;
    }
    $("insLede").textContent = LOADING_TIMETABLES;
    insights.trip = await tripAcrossYears();
    if (insights.page !== page) return;
  }
  const rows = pageRows(page, metric);
  if (rows.length < 2) {
    showInsightMessage(t("Not enough timetable years for a chart.", "Zu wenige Fahrplanjahre für ein Diagramm."));
    return;
  }
  const format = page.id === "trip" ? (v) => formatMinutes(v) : page.id === "network" ? (v) => String(v) : METRICS[metric].format;
  // The headline compares the selected (or latest final) timetable with the first year; the draft is plotted but
  // never headlined. Lower is better for trip times, higher for everything else.
  const final = rows.filter((row) => !row.draft);
  const current = final.find((row) => row.year === app.year) ?? final[final.length - 1];
  current.current = true;
  const first = rows[0];
  const delta = current.value - first.value;
  const good = (change) => (page.id === "trip" ? change < 0 : change > 0);
  const tone = (change) => (Math.round(change) === 0 ? "" : good(change) ? "good" : "bad");
  let step = null;
  for (let i = 1; i < final.length; i += 1) {
    const change = final[i].value - final[i - 1].value;
    if (!step || Math.abs(change) > Math.abs(step.change)) step = { change, from: final[i - 1], to: final[i] };
  }
  const noun = page.id === "trip" ? "" : ` ${lowerNoun(page.labels?.[metric] ?? METRICS[metric].noun)}`;
  const strong = document.createElement("strong");
  strong.textContent = step.to.year;
  const amount = document.createElement("span");
  amount.className = `mono ${tone(step.change)}`;
  amount.textContent = signed(step.change, format) + noun;
  $("insLede").replaceChildren(t("Biggest change in ", "Grösste Änderung "), strong, " — ", amount, t(` against ${step.from.year}.`, ` gegenüber ${step.from.year}.`));
  $("insArrow").textContent = Math.round(delta) === 0 ? "→" : delta > 0 ? "↑" : "↓";
  $("insArrow").className = `insight-arrow ${tone(delta)}`;
  $("insRef").textContent = t(`${format(first.value)} in ${first.year}`, `${format(first.value)} im Jahr ${first.year}`);
  $("insValue").textContent = format(current.value);
  $("insDelta").textContent = signed(delta, format);
  $("insDelta").className = `mono ${tone(delta)}`;
  $("insVs").textContent = t(`${current.year} vs ${first.year}`, `${current.year} gegenüber ${first.year}`);
  $("insChart").setAttribute("aria-label", `${page.title(metric)}: ${rows.map((row) => `${row.year} ${format(row.value)}`).join(", ")}`);
  drawLineChart($("insChart"), rows, format, first.value);
}

function setupInsights() {
  if (!$("insights") || HISTORY.length < 2) return;
  const go = (step) => {
    insights.index = (insights.index + step + INSIGHT_PAGES.length) % INSIGHT_PAGES.length;
    renderInsight().catch((error) => console.error(error));
  };
  $("insPrev").addEventListener("click", () => go(-1));
  $("insNext").addEventListener("click", () => go(1));
  let lastWidth = 0;
  new ResizeObserver(([entry]) => {
    const width = Math.round(entry.contentRect.width);
    if (width !== lastWidth && insights.page) {
      lastWidth = width;
      renderInsight().catch(() => {});
    }
  }).observe($("insChart"));
  renderInsight().catch((error) => console.error(error));
}

/** Public transport or bike: the bike layer ignores the timetable year and the bus toggle. */
async function setMode(mode) {
  if (mode === "bike") await loadBike();
  app.mode = mode === "bike" ? "bike" : "transit";
  for (const button of $("modePicker").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.mode === app.mode));
  }
  document.body.classList.toggle("bike-mode", app.mode === "bike");
}

async function init() {
  resize();
  const requested = new URLSearchParams(location.search).get("year");
  await loadYear(CITY.timetables[requested] ? requested : CITY.defaultTimetable);
  app.size.width = 0;
  resize();
  if (new URLSearchParams(location.search).get("mode") === "bike") await setMode("bike").catch(() => toast(t("Could not load the cycling layer.", "Die Veloebene konnte nicht geladen werden.")));
  restoreFromUrl();
  loadStreets().catch((error) => console.error("streets", error));
  loadBusLines().catch((error) => console.error("bus lines", error));
  new ResizeObserver(resize).observe(canvas);
  $("modePicker").addEventListener("click", (event) => {
    const mode = event.target.closest("button")?.dataset.mode;
    if (!mode || mode === app.mode) return;
    setMode(mode)
      .then(() => {
        recompute();
        syncUrl();
      })
      .catch(() => toast(t("Could not load the cycling layer.", "Die Veloebene konnte nicht geladen werden.")));
  });
  new IntersectionObserver(([entry]) => {
    if (entry.isIntersecting && !yearsVisible) {
      yearsVisible = true;
      scheduleYears();
    }
  }, { rootMargin: "200px" }).observe($("yearsPanel"));
  setupInsights();
  $("yearPicker").addEventListener("click", (event) => {
    const year = event.target.closest("button")?.dataset.year;
    if (year) switchYear(year).catch(() => toast(t("Could not load that timetable.", "Dieser Fahrplan konnte nicht geladen werden.")));
  });
}

init().catch((error) => {
  console.error(error);
  $("tripFrom").textContent = t("Could not load the network.", "Das Netz konnte nicht geladen werden.");
});
