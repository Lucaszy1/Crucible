// SVG network map: layered topology, badges, pulses, state styling.
// Geometry comes from the exercise layout (segment columns); without
// one the classic map below applies.
import { HOSTS, eventHost } from "./state.js";
import { classicLayout, layoutHosts, mapGeometry } from "./layout.js";

const SVG_NS = "http://www.w3.org/2000/svg";

export const NODE_R = 26;

// Landscape layout flowing left to right: web, app, db across the
// width, workstation below app. Fits MAP_VIEWBOX with room for the
// name pill above each node and the state label below it.
export const MAP_VIEWBOX = { w: 640, h: 360 };
export const MAP_POSITIONS = {
  web: { x: 110, y: 140 },
  app: { x: 320, y: 140 },
  db: { x: 530, y: 140 },
  workstation: { x: 320, y: 285 },
};

// Only the links that exist in the range: the web/app/db service chain
// plus the workstation reaching the app tier.
export const MAP_EDGES = [
  ["web", "app"],
  ["app", "db"],
  ["workstation", "app"],
];

function buildAdjacency(hosts, edges) {
  const adjacency = new Map(hosts.map((host) => [host, []]));
  for (const [a, b] of edges) {
    if (adjacency.has(a) && adjacency.has(b)) {
      adjacency.get(a).push(b);
      adjacency.get(b).push(a);
    }
  }
  return adjacency;
}

const ADJACENCY = buildAdjacency(HOSTS, MAP_EDGES);

// Resolve one exercise's map graph: explicit layout geometry, or the
// classic positions and edges for old logs.
export function mapGraph(layout) {
  if (!layout) return { positions: MAP_POSITIONS, edges: MAP_EDGES, jewel: "db", radius: NODE_R, crowded: false, hosts: HOSTS };
  const clean = layout.hosts ? layout : classicLayout();
  const geometry = mapGeometry(clean);
  const roles = {};
  for (const host of clean.hosts) roles[host.id] = host.role;
  return {
    positions: geometry.positions,
    edges: geometry.edges,
    jewel: geometry.jewel,
    radius: geometry.radius,
    crowded: geometry.crowded,
    hosts: layoutHosts(clean),
    roles,
  };
}

// Muted pulse colors only. Node state colors live in CSS per theme,
// driven by the data-state attribute updateMap sets below.
const STATE_COLORS = {
  clean: "#758f7f",
  probed: "#c79a3b",
  "signed-in": "#c26a2c",
  compromised: "#b3402e",
  isolated: "#2f6db3",
  restored: "#4d8f83",
};

// Plain state label for the map: "Signed in", not "signed-in".
export function stateLabel(state) {
  const words = String(state || "clean").split("-").join(" ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function el(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

function edgeKey(a, b) {
  return [a, b].sort().join("|");
}

// Shortest route between two hosts over the real links only.
// The optional graph ({ positions, edges }) routes generated ranges;
// without it the classic map applies.
export function pathBetween(from, to, graph) {
  const positions = (graph && graph.positions) || MAP_POSITIONS;
  const adjacency = graph && graph.edges ? buildAdjacency(Object.keys(positions), graph.edges) : ADJACENCY;
  if (!(from in positions) || !(to in positions)) return null;
  if (from === to) return [from];
  const prev = new Map([[from, null]]);
  const queue = [from];
  while (queue.length > 0) {
    const current = queue.shift();
    for (const next of adjacency.get(current) || []) {
      if (prev.has(next)) continue;
      prev.set(next, current);
      if (next === to) {
        const path = [to];
        let node = current;
        while (node) {
          path.unshift(node);
          node = prev.get(node);
        }
        return path;
      }
      queue.push(next);
    }
  }
  return null;
}

// The edge a blue response travels to reach a host: the first real link
// touching it, directed toward the host.
export function blueLinkFor(host, edges) {
  for (const [a, b] of edges || MAP_EDGES) {
    if (a === host) return [b, a];
    if (b === host) return [a, b];
  }
  return null;
}

// Line endpoints trimmed to the ring edges so links stop at each circle.
function edgePoints(positions, radius, from, to) {
  const a = positions[from];
  const b = positions[to];
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const dist = Math.hypot(dx, dy) || 1;
  const trim = radius + 3;
  return {
    x1: a.x + (dx / dist) * trim,
    y1: a.y + (dy / dist) * trim,
    x2: b.x - (dx / dist) * trim,
    y2: b.y - (dy / dist) * trim,
  };
}

// Small inline glyph per role, drawn inside the node ring: globe for
// public web roles, gear for services, database cylinder for data
// stores, monitor for workstations. Stroked with the node state color.
export function iconKind(role) {
  const text = String(role || "").toLowerCase();
  if (text.includes("workstation")) return "monitor";
  if (text.includes("data") || text.includes("database") || text.includes("backup")) return "cylinder";
  if (text.includes("web") || text.includes("portal") || text.includes("api") || text.includes("public")) return "globe";
  return "gear";
}

function hostIcon(role, pos) {
  const group = el("g", {
    class: "node-icon",
    transform: `translate(${pos.x} ${pos.y})`,
  });
  const stroke = (name, attrs) => group.append(el(name, { class: "icon-stroke", ...attrs }));
  const kind = iconKind(role);
  if (kind === "globe") {
    stroke("circle", { cx: 0, cy: 0, r: 9 });
    stroke("ellipse", { cx: 0, cy: 0, rx: 4.2, ry: 9 });
    stroke("line", { x1: -9, y1: 0, x2: 9, y2: 0 });
  } else if (kind === "gear") {
    stroke("circle", { cx: 0, cy: 0, r: 5 });
    stroke("circle", { cx: 0, cy: 0, r: 2.4 });
    for (let i = 0; i < 8; i += 1) {
      const angle = (i / 8) * Math.PI * 2;
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      stroke("line", {
        x1: (cos * 5).toFixed(2), y1: (sin * 5).toFixed(2),
        x2: (cos * 8.4).toFixed(2), y2: (sin * 8.4).toFixed(2),
      });
    }
  } else if (kind === "cylinder") {
    stroke("path", { d: "M -8 -5.5 V 5.5 A 8 3.2 0 0 0 8 5.5 V -5.5" });
    stroke("ellipse", { cx: 0, cy: -5.5, rx: 8, ry: 3.2 });
  } else {
    stroke("rect", { x: -8.5, y: -6.5, width: 17, height: 11, rx: 1.5 });
    stroke("line", { x1: 0, y1: 4.5, x2: 0, y2: 7.5 });
    stroke("line", { x1: -5, y1: 7.5, x2: 5, y2: 7.5 });
  }
  return group;
}

function lockIcon(pos) {
  const group = el("g", {
    class: "node-lock",
    transform: `translate(${pos.x + 30} ${pos.y - 26})`,
  });
  const title = el("title", {});
  title.textContent = "Protected secret";
  const shackle = el("path", {
    d: "M -4.5 1 v -3 a 4.5 4.5 0 0 1 9 0 V 1",
    class: "lock-shackle",
  });
  const body = el("rect", {
    x: -7, y: 1, width: 14, height: 10, rx: 2, class: "lock-body",
  });
  group.append(title, shackle, body);
  return group;
}

export function initMap(svg, weights, layout) {
  svg.innerHTML = "";
  const graph = mapGraph(layout || null);
  const edgeLayer = el("g", { class: "edges" });
  const nodeLayer = el("g", { class: "nodes" });
  const fxLayer = el("g", { class: "fx" });
  svg.append(edgeLayer, nodeLayer, fxLayer);

  const edgeEls = new Map();
  for (const [a, b] of graph.edges) {
    const pts = edgePoints(graph.positions, graph.radius, a, b);
    const line = el("line", {
      x1: pts.x1, y1: pts.y1,
      x2: pts.x2, y2: pts.y2,
      class: "edge",
    });
    edgeLayer.append(line);
    edgeEls.set(edgeKey(a, b), line);
  }

  const nodeEls = new Map();
  for (const host of graph.hosts) {
    const pos = graph.positions[host];
    const radius = graph.radius;
    const group = el("g", { class: "node-group", "data-state": "clean" });
    const halo = el("circle", {
      cx: pos.x, cy: pos.y, r: radius + 10, class: "halo",
    });
    const isoRing = el("circle", {
      cx: pos.x, cy: pos.y, r: radius + 7, class: "iso-ring",
    });
    const ring = el("circle", { cx: pos.x, cy: pos.y, r: radius, class: "node-ring" });
    group.append(halo, isoRing, ring, hostIcon((graph.roles || {})[host], pos));
    if (host === graph.jewel) group.append(lockIcon(pos));
    // Name plus a small weight pill above the circle, one state label
    // below it, so no text ever overlaps the ring. Crowded columns
    // shorten long names; the full name stays on hover.
    const shown = graph.crowded && host.length > 8 ? `${host.slice(0, 8)}…` : host;
    const nameY = pos.y - radius - 12;
    const label = el("text", { x: pos.x, y: nameY, class: "node-label" });
    label.textContent = shown;
    if (shown !== host) {
      const full = el("title", {});
      full.textContent = host;
      label.append(full);
    }
    const nameHalf = (shown.length * 6.7) / 2;
    const pillCx = pos.x + nameHalf + 6 + 14;
    const pill = el("rect", {
      x: pillCx - 14, y: nameY - 13, width: 28, height: 17, rx: 8.5, class: "node-badge",
    });
    const pillTitle = el("title", {});
    pillTitle.textContent = `Business weight ${weights[host] ?? "?"}`;
    pill.append(pillTitle);
    const pillText = el("text", { x: pillCx, y: nameY - 1, class: "node-badge-text" });
    pillText.textContent = `${weights[host] ?? "?"}`;
    const state = el("text", { x: pos.x, y: pos.y + radius + 18, class: "node-state" });
    group.append(label, pill, pillText, state);
    nodeLayer.append(group);
    nodeEls.set(host, { group, state });
  }
  return { edgeEls, nodeEls, fxLayer, graph };
}

// Latest red hosts in order, used to route the pulse path.
function redTrail(slice, positions) {
  const trail = [];
  for (const ev of slice) {
    if (ev.actor !== "red") continue;
    const host = eventHost(ev);
    if (host && host in positions && trail[trail.length - 1] !== host) trail.push(host);
  }
  return trail;
}

function firePulse(fxLayer, graph, from, to, kind, delay) {
  if (!graph.positions[from] || !graph.positions[to]) return;
  const pts = edgePoints(graph.positions, graph.radius, from, to);
  const dot = el("circle", { r: kind === "blue" ? 4 : 5.5, class: `pulse pulse-${kind}` });
  const motion = el("animateMotion", {
    dur: kind === "blue" ? "0.9s" : "0.7s",
    begin: `${delay || 0}s`,
    repeatCount: "1",
    path: `M ${pts.x1} ${pts.y1} L ${pts.x2} ${pts.y2}`,
  });
  dot.append(motion);
  fxLayer.append(dot);
  motion.addEventListener("endEvent", () => dot.remove());
  setTimeout(() => dot.remove(), (delay || 0) * 1000 + 1200);
}

function pingNode(fxLayer, graph, host, kind) {
  const pos = graph.positions[host];
  if (!pos) return;
  const radius = graph.radius;
  const color = kind === "blue" ? STATE_COLORS.isolated : STATE_COLORS.compromised;
  const ring = el("circle", {
    cx: pos.x, cy: pos.y, r: radius, fill: "none",
    stroke: color, "stroke-width": 2,
  });
  const grow = el("animate", { attributeName: "r", from: String(radius), to: String(radius + 13), dur: "0.6s", repeatCount: "1" });
  const fade = el("animate", { attributeName: "opacity", from: "1", to: "0", dur: "0.6s", repeatCount: "1" });
  ring.append(grow, fade);
  fxLayer.append(ring);
  setTimeout(() => ring.remove(), 700);
}

export function updateMap(refs, states, slice, animate) {
  const graph = (refs && refs.graph) || mapGraph(null);
  for (const host of graph.hosts) {
    const parts = refs.nodeEls.get(host);
    if (!parts) continue;
    const state = states[host] || "clean";
    parts.group.dataset.state = state;
    parts.state.textContent = stateLabel(state);
  }

  for (const line of refs.edgeEls.values()) line.setAttribute("class", "edge");

  const visible = slice || [];
  const latest = visible[visible.length - 1];
  if (!latest) return;
  const host = eventHost(latest);
  if (!host || !(host in graph.positions)) return;

  if (latest.actor === "red") {
    const trail = redTrail(visible, graph.positions);
    const prev = trail.length > 1 ? trail[trail.length - 2] : null;
    const path = prev ? pathBetween(prev, host, graph) : null;
    if (path && path.length > 1) {
      const line = refs.edgeEls.get(edgeKey(path[path.length - 2], path[path.length - 1]));
      if (line) line.setAttribute("class", "edge lit-red");
      if (animate) {
        path.slice(1).forEach((node, i) => firePulse(refs.fxLayer, graph, path[i], node, "red", i * 0.45));
      }
    } else if (animate) {
      pingNode(refs.fxLayer, graph, host, "red");
    }
  } else if (latest.actor === "blue") {
    const link = blueLinkFor(host, graph.edges);
    if (link) {
      const line = refs.edgeEls.get(edgeKey(link[0], link[1]));
      if (line) line.setAttribute("class", "edge lit-blue");
      if (animate) firePulse(refs.fxLayer, graph, link[0], link[1], "blue", 0);
    } else if (animate) {
      pingNode(refs.fxLayer, graph, host, "blue");
    }
  }
}
