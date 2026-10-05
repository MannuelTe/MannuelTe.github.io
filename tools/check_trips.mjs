import fs from "fs";
// Trip probe: time from the centre to the termini and stations, with door-to-door speed.
// Usage: node tools/check_trips.mjs <city>   (abnormal speeds are flagged with ⚠)
const slug = process.argv[2];
const d = JSON.parse(fs.readFileSync(`site/data/${slug}.json`));
const city = JSON.parse(fs.readFileSync(`cities/${slug}.json`));
const rs = d.routeStates, st = d.stations, ri = d.routeInfo, rail = (r) => ri[r].rail;
const W = d.meta.walkMetersPerMinute, hyp = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const m = 111320, toW = (lat, lon) => [lon * m * Math.cos(d.meta.lat0 * Math.PI / 180), lat * m];
const origin = toW(city.defaultFrom.lat, city.defaultFrom.lon);
// Même modèle que site/app.js : marche vers les stations les plus proches, puis tram/métro seulement.
const dist = new Array(rs.length).fill(Infinity), prev = new Array(rs.length).fill(-1);
const seeds = st.map((s, i) => ({ i, w: hyp(origin, s.point) / W })).filter((x) => st[x.i].rail).sort((a, b) => a.w - b.w).slice(0, d.meta.originStationCount);
for (const s of seeds) for (const k of d.stationStates[s.i]) if (rail(rs[k].routeId)) dist[k] = Math.min(dist[k], s.w + rs[k].access + rs[k].wait);
const done = []; for (;;) { let u = -1, b = Infinity; for (let i = 0; i < rs.length; i++) if (!done[i] && dist[i] < b) { b = dist[i]; u = i; } if (u < 0) break; done[u] = 1;
  for (const [v, w] of d.adjacency[u]) if (rail(rs[v].routeId) && dist[u] + w < dist[v]) { dist[v] = dist[u] + w; prev[v] = u; } }
const out = new Array(st.length).fill(Infinity), best = new Array(st.length).fill(-1);
rs.forEach((r, k) => { const o = dist[k] + r.access; if (o < out[r.stationIndex]) { out[r.stationIndex] = o; best[r.stationIndex] = k; } });
// targets: termini of each rail line (stations of the line farthest apart) + stations named "Gare"
const targets = new Map();
for (const [id, info] of Object.entries(ri)) { if (!info.rail) continue;
  const sts = [...new Set(rs.filter((r) => r.routeId === id).map((r) => r.stationIndex))];
  let a = sts[0], bb = sts[0], md = 0; for (const i of sts) for (const j of sts) { const dd = hyp(st[i].point, st[j].point); if (dd > md) { md = dd; a = i; bb = j; } }
  targets.set(a, `terminus ${info.name}`); targets.set(bb, `terminus ${info.name}`); }
st.forEach((s, i) => { if (s.rail && /^gare\b|gare /i.test(s.name) && !targets.has(i)) targets.set(i, "gare"); });
const rows = [];
for (const [i, why] of targets) { const t = out[i]; const km = hyp(origin, st[i].point) / 1000; const kmh = km / (t / 60);
  const lines = []; for (let k = best[i]; k !== -1; k = prev[k]) { const r = ri[rs[k].routeId].name; if (lines[0] !== r) lines.unshift(r); }
  const flag = !isFinite(t) ? "  ⚠ UNREACHABLE" : (kmh < 8 && km > 2) || kmh > 35 ? "  ⚠ speed" : "";
  rows.push(`  ${st[i].name.slice(0, 30).padEnd(30)} ${why.padEnd(14)} ${km.toFixed(1).padStart(5)} km ${isFinite(t) ? t.toFixed(0).padStart(3) : "  ∞"} min ${isFinite(kmh) ? kmh.toFixed(0).padStart(3) : "  -"} km/h [${lines.join(">")}]${flag}`); }
console.log(`== ${city.name} (from ${city.defaultFrom.label})`); console.log(rows.join("\n"));
