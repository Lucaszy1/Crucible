// Scenario layout engine: pure geometry from blue-visible range
// structure. Every exercise carries its layout in the first event's
// scenario_layout args (host ids, roles, segments, links, weights,
// crown jewel host); logs that predate the field fall back to the
// classic four-host layout below. No DOM access, so node tests this
// module. Styling derives from roles, never from host names.

export const WORLD = { w: 1600, h: 900 };

export const SEGMENTS = ["dmz", "internal", "data"];

// Fallback for logs without scenario_layout. Mirrors exactly what
// the classic range emits today, so old and new classic runs render
// identically: the service chain, not the inventory mesh.
export const CLASSIC_LAYOUT = {
  scenario_id: "classic",
  hosts: [
    { id: "web", role: "public web front end", segment: "dmz", links: ["app"], weight: 3 },
    { id: "app", role: "internal reporting service", segment: "internal", links: ["web", "db", "workstation"], weight: 3 },
    { id: "db", role: "internal data store", segment: "data", links: ["app"], weight: 5 },
    { id: "workstation", role: "analyst workstation", segment: "internal", links: ["app"], weight: 1 },
  ],
  jewel_host: "db",
};

export function classicLayout() {
  return JSON.parse(JSON.stringify(CLASSIC_LAYOUT));
}

// Clean one raw layout into shape, or null when unusable. Unknown
// segments land in internal; links to unknown hosts drop out;
// weights default to 1; an unknown jewel host means no vault.
export function normalizeLayout(raw) {
  if (!raw || typeof raw !== "object") return null;
  const seen = new Set();
  const hosts = [];
  for (const entry of raw.hosts || []) {
    if (!entry || typeof entry.id !== "string" || !entry.id) continue;
    if (seen.has(entry.id)) continue;
    if (hosts.length >= 10) break;
    seen.add(entry.id);
    hosts.push({ id: entry.id, role: entry.role, segment: entry.segment, links: entry.links, weight: entry.weight });
  }
  if (hosts.length === 0) return null;
  const clean = hosts.map((entry) => ({
    id: entry.id,
    role: typeof entry.role === "string" ? entry.role : "",
    segment: SEGMENTS.includes(entry.segment) ? entry.segment : "internal",
    links: (Array.isArray(entry.links) ? entry.links : [])
      .filter((link) => typeof link === "string" && seen.has(link) && link !== entry.id),
    weight: Number.isFinite(Number(entry.weight)) && Number(entry.weight) > 0 ? Number(entry.weight) : 1,
  }));
  return {
    scenario_id: typeof raw.scenario_id === "string" && raw.scenario_id ? raw.scenario_id : "classic",
    hosts: clean,
    jewel_host: typeof raw.jewel_host === "string" && seen.has(raw.jewel_host) ? raw.jewel_host : "",
  };
}

// First usable layout in an event list (the first event carries it),
// or the classic fallback for logs that predate the field.
export function layoutFromEvents(eventList) {
  for (const ev of eventList || []) {
    const raw = ev && ev.args && ev.args.scenario_layout;
    if (!raw || typeof raw !== "object") continue;
    const clean = normalizeLayout(raw);
    if (clean) return clean;
  }
  return classicLayout();
}

// Scenario identity from the first event's args. Old logs without
// the keys read as classic.
export function scenarioMeta(eventList) {
  for (const ev of eventList || []) {
    const args = (ev && ev.args) || {};
    if (typeof args.scenario_id === "string" && args.scenario_id) {
      return {
        id: args.scenario_id,
        seed: args.scenario_seed ?? null,
        difficulty: args.scenario_difficulty ?? null,
      };
    }
  }
  return { id: "classic", seed: null, difficulty: null };
}

export function scenarioText(meta) {
  if (!meta || meta.id === "classic") return "classic";
  return meta.difficulty ? `${meta.id} · ${meta.difficulty}` : String(meta.id);
}

export function layoutHosts(layout) {
  return ((layout && layout.hosts) || []).map((host) => host.id);
}

export function layoutWeights(layout) {
  const weights = {};
  for (const host of (layout && layout.hosts) || []) weights[host.id] = host.weight;
  return weights;
}

// Host ids per segment column, in layout order.
export function columnsOf(layout) {
  const columns = SEGMENTS.map(() => []);
  for (const host of (layout && layout.hosts) || []) {
    const at = SEGMENTS.indexOf(host.segment);
    columns[at < 0 ? 1 : at].push(host.id);
  }
  return columns;
}

// Undirected link pairs in first-seen order, for conduits.
export function edgesOf(layout) {
  const ids = new Set(layoutHosts(layout));
  const seen = new Set();
  const edges = [];
  for (const host of (layout && layout.hosts) || []) {
    for (const link of host.links || []) {
      if (!ids.has(link)) continue;
      const key = [host.id, link].sort().join("|");
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push([host.id, link]);
    }
  }
  return edges;
}

// Entry host: first dmz host, else the first host.
export function entryOf(layout) {
  const hosts = (layout && layout.hosts) || [];
  const dmz = hosts.find((host) => host.segment === "dmz");
  return (dmz || hosts[0] || {}).id || "";
}

// Block style from role: the crown jewel gets the vault look,
// workstations the small block, everything else the standard block.
export function roleKind(role, isJewel) {
  if (isJewel) return "vault";
  if (/workstation/i.test(role || "")) return "small";
  return "standard";
}

const BASE_RADIUS = { vault: 100, standard: 88, small: 72 };

// Short block label: workstation roles read WS, anything longer
// than a few characters truncates to fit the block.
export function nodeLabel(id, role) {
  if (/workstation/i.test(role || "")) return "WS";
  return String(id || "").toUpperCase().slice(0, 7);
}

// One label per host, unique across the range. Ranges with two
// workstations (or two names sharing a prefix) would otherwise
// draw the same letters twice; collisions gain a middle dot plus
// enough of the host id to tell them apart.
export function nodeLabels(layout) {
  const hosts = (layout && layout.hosts) || [];
  const taken = new Set();
  const labels = {};
  for (const host of hosts) {
    const base = nodeLabel(host.id, host.role);
    if (!taken.has(base)) {
      taken.add(base);
      labels[host.id] = base;
      continue;
    }
    const stem = String(host.id || "").toUpperCase().replace(/[^A-Z0-9]/g, "") || "X";
    let label = base;
    for (let len = 2; len <= stem.length + 1; len += 1) {
      label = `${base}·${stem.slice(0, len)}`;
      if (!taken.has(label)) break;
    }
    taken.add(label);
    labels[host.id] = label;
  }
  return labels;
}

// One-line role caption under the label. The classic sentence
// roles map to the same captions as before; unknown roles show
// themselves; the vault names its host id below instead.
export function roleSub(role, isJewel) {
  if (isJewel) return "";
  const map = {
    "public web front end": "public site",
    "web portal": "public site",
    "api": "public site",
    "internal reporting service": "reports server",
    "app": "reports server",
    "analyst workstation": "staff desktop",
    "workstation": "staff desktop",
    "internal data store": "",
    "database": "",
    "backup": "",
    "file share": "file share",
    "jump host": "jump host",
  };
  if (role in map) return map[role];
  return String(role || "");
}

// Drawn block extents in units of r: the weight badge reaches
// 1.35r above center; the status tag reaches 1.6r below (2.1r for
// vaults, which stack their name and tag under the dial).
const SLOT_TOP = 1.35;
const SLOT_BOTTOM = { vault: 2.1, standard: 1.6, small: 1.6 };

// Vertical slots for one column of blocks, pitched by true drawn
// extents (badge above, tag below) so neighbors never overlap.
// Single blocks sit at mid world; pairs hang the second block a
// full pitch below; bigger columns shrink blocks to fit a 700-tall
// band centered on the world middle.
function columnSlots(items) {
  const n = items.length;
  const top = (item) => SLOT_TOP * item.r;
  const bottom = (item) => (SLOT_BOTTOM[item.kind] || SLOT_BOTTOM.standard) * item.r;
  if (n <= 0) return [];
  if (n === 1) return [{ y: 450, r: items[0].r }];
  if (n === 2) {
    const pitch = bottom(items[0]) + top(items[1]) + 16;
    return [{ y: 450, r: items[0].r }, { y: Math.round(450 + pitch), r: items[1].r }];
  }
  const gap = 16;
  const band = 700;
  const height = (item) => (SLOT_TOP + (SLOT_BOTTOM[item.kind] || SLOT_BOTTOM.standard)) * item.r;
  const total = items.reduce((sum, item) => sum + height(item), 0) + gap * (n - 1);
  const scale = Math.min(1, band / total);
  const scaled = items.map((item) => ({ kind: item.kind, r: Math.max(34, item.r * scale) }));
  const used = scaled.reduce((sum, item) => sum + top(item) + bottom(item), 0) + gap * (n - 1);
  let y = 450 - used / 2;
  return scaled.map((item) => {
    y += top(item);
    const slot = { y, r: item.r };
    y += bottom(item) + gap;
    return slot;
  });
}

// Arena world geometry: segment columns left to right, hosts
// spread vertically, red entering left of dmz and blue settled
// bottom right. Columns past six hosts split into two sub-columns
// so dense ranges stay readable.
export function arenaGeometry(layout) {
  const clean = layout || classicLayout();
  const kinds = {};
  for (const host of clean.hosts) kinds[host.id] = roleKind(host.role, host.id === clean.jewel_host);
  const columns = columnsOf(clean);
  const colX = { dmz: 330, internal: 790, data: 1250 };
  const nodes = {};
  SEGMENTS.forEach((segment, index) => {
    const ids = columns[index];
    const groups = ids.length > 6
      ? [ids.slice(0, Math.ceil(ids.length / 2)), ids.slice(Math.ceil(ids.length / 2))]
      : [ids];
    groups.forEach((group, gi) => {
      const x = colX[segment] + (groups.length > 1 ? (gi === 0 ? -95 : 95) : 0);
      const items = group.map((id) => ({ r: BASE_RADIUS[kinds[id]], kind: kinds[id] }));
      const slots = columnSlots(items);
      group.forEach((id, i) => {
        nodes[id] = { x, y: slots[i].y, r: slots[i].r, kind: kinds[id] };
      });
    });
  });
  const entry = entryOf(clean);
  const edges = edgesOf(clean);
  if (entry) edges.push(["red", entry]);
  const xs = Object.values(nodes).map((node) => node.x);
  const lo = xs.length > 0 ? Math.min(...xs) : 330;
  const hi = xs.length > 0 ? Math.max(...xs) : 1250;
  return {
    nodes,
    edges,
    entry,
    jewel: clean.jewel_host,
    red: { x: Math.max(60, lo - 240), y: 450 },
    blue: { x: Math.min(1540, hi + 240), y: 790 },
  };
}

// Small-map geometry for the Story view SVG: same segment columns,
// tighter rows. Classic coordinates reproduce exactly.
export const MAP_VIEW = { w: 640, h: 360 };

export function mapGeometry(layout) {
  const clean = layout || classicLayout();
  const columns = columnsOf(clean);
  const colX = { dmz: 110, internal: 320, data: 530 };
  const positions = {};
  let crowded = false;
  SEGMENTS.forEach((segment, index) => {
    const ids = columns[index];
    const groups = ids.length > 5
      ? [ids.slice(0, Math.ceil(ids.length / 2)), ids.slice(Math.ceil(ids.length / 2))]
      : [ids];
    if (groups.length > 1) crowded = true;
    groups.forEach((group, gi) => {
      const x = colX[segment] + (groups.length > 1 ? (gi === 0 ? -38 : 38) : 0);
      group.forEach((id, i) => {
        let y = 140;
        if (group.length === 2) y = i === 0 ? 140 : 285;
        else if (group.length > 2) y = 85 + (i * 195) / (group.length - 1);
        positions[id] = { x, y };
      });
    });
  });
  return { positions, edges: edgesOf(clean), jewel: clean.jewel_host, radius: crowded ? 20 : 26, crowded };
}
