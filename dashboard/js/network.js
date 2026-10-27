// SVG network map: layered topology, badges, pulses, state styling.
import { HOSTS, eventHost } from "./state.js";

const SVG_NS = "http://www.w3.org/2000/svg";

export const NODE_R = 34;

// Believable range layout: public web tier on top, app in the middle,
// db holding the protected secret at the bottom, workstation aside.
export const MAP_POSITIONS = {
  web: { x: 190, y: 78 },
  app: { x: 190, y: 232 },
  workstation: { x: 56, y: 232 },
  db: { x: 190, y: 386 },
};

// Only the links that exist in the range: the web/app/db service chain
// plus the workstation reaching the app tier.
export const MAP_EDGES = [
  ["web", "app"],
  ["app", "db"],
  ["workstation", "app"],
];

const ADJACENCY = new Map(HOSTS.map((host) => [host, []]));
for (const [a, b] of MAP_EDGES) {
  ADJACENCY.get(a).push(b);
  ADJACENCY.get(b).push(a);
}

const STATE_COLORS = {
  clean: "#34d399",
  probed: "#fbbf24",
  "signed-in": "#fb923c",
  compromised: "#f87171",
  isolated: "#22d3ee",
  restored: "#2dd4bf",
};

function el(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

function edgeKey(a, b) {
  return [a, b].sort().join("|");
}

// Shortest route between two hosts over the real links only.
export function pathBetween(from, to) {
  if (!(from in MAP_POSITIONS) || !(to in MAP_POSITIONS)) return null;
  if (from === to) return [from];
  const prev = new Map([[from, null]]);
  const queue = [from];
  while (queue.length > 0) {
    const current = queue.shift();
    for (const next of ADJACENCY.get(current) || []) {
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
export function blueLinkFor(host) {
  for (const [a, b] of MAP_EDGES) {
    if (a === host) return [b, a];
    if (b === host) return [a, b];
  }
  return null;
}

// Line endpoints trimmed to the ring edges so links stop at each circle.
function edgePoints(from, to) {
  const a = MAP_POSITIONS[from];
  const b = MAP_POSITIONS[to];
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const dist = Math.hypot(dx, dy) || 1;
  const trim = NODE_R + 3;
  return {
    x1: a.x + (dx / dist) * trim,
    y1: a.y + (dy / dist) * trim,
    x2: b.x - (dx / dist) * trim,
    y2: b.y - (dy / dist) * trim,
  };
}

function lockIcon(pos) {
  const group = el("g", {
    class: "node-lock",
    transform: `translate(${pos.x + 25} ${pos.y - 31})`,
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

export function initMap(svg, weights) {
  svg.innerHTML = "";
  const edgeLayer = el("g", { class: "edges" });
  const nodeLayer = el("g", { class: "nodes" });
  const fxLayer = el("g", { class: "fx" });
  svg.append(edgeLayer, nodeLayer, fxLayer);

  const edgeEls = new Map();
  for (const [a, b] of MAP_EDGES) {
    const pts = edgePoints(a, b);
    const line = el("line", {
      x1: pts.x1, y1: pts.y1,
      x2: pts.x2, y2: pts.y2,
      class: "edge",
    });
    edgeLayer.append(line);
    edgeEls.set(edgeKey(a, b), line);
  }

  const nodeEls = new Map();
  for (const host of HOSTS) {
    const pos = MAP_POSITIONS[host];
    const group = el("g", { class: "node-group", "data-state": "clean" });
    const halo = el("circle", {
      cx: pos.x, cy: pos.y, r: NODE_R + 10, class: "halo",
    });
    const isoRing = el("circle", {
      cx: pos.x, cy: pos.y, r: NODE_R + 7, class: "iso-ring",
    });
    const ring = el("circle", { cx: pos.x, cy: pos.y, r: NODE_R, class: "node-ring" });
    group.append(halo, isoRing, ring);
    if (host === "db") group.append(lockIcon(pos));
    // Name, weight badge, and state all sit beneath the circle so no
    // text ever overlaps the ring.
    const label = el("text", { x: pos.x, y: pos.y + NODE_R + 20, class: "node-label" });
    label.textContent = host;
    const badgeCy = pos.y + NODE_R + 37;
    const badge = el("rect", {
      x: pos.x - 21, y: badgeCy - 8, width: 42, height: 16, rx: 8, class: "node-badge",
    });
    const badgeTitle = el("title", {});
    badgeTitle.textContent = `Business weight ${weights[host] ?? "?"}`;
    badge.append(badgeTitle);
    const badgeText = el("text", { x: pos.x, y: badgeCy + 4, class: "node-badge-text" });
    badgeText.textContent = `w${weights[host] ?? "?"}`;
    const state = el("text", { x: pos.x, y: pos.y + NODE_R + 62, class: "node-state" });
    group.append(label, badge, badgeText, state);
    nodeLayer.append(group);
    nodeEls.set(host, { group, state });
  }
  return { edgeEls, nodeEls, fxLayer };
}

// Latest red hosts in order, used to route the pulse path.
function redTrail(slice) {
  const trail = [];
  for (const ev of slice) {
    if (ev.actor !== "red") continue;
    const host = eventHost(ev);
    if (host && host in MAP_POSITIONS && trail[trail.length - 1] !== host) trail.push(host);
  }
  return trail;
}

function firePulse(fxLayer, from, to, kind, delay) {
  if (!MAP_POSITIONS[from] || !MAP_POSITIONS[to]) return;
  const pts = edgePoints(from, to);
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

function pingNode(fxLayer, host, kind) {
  const pos = MAP_POSITIONS[host];
  if (!pos) return;
  const color = kind === "blue" ? STATE_COLORS.isolated : STATE_COLORS.compromised;
  const ring = el("circle", {
    cx: pos.x, cy: pos.y, r: NODE_R, fill: "none",
    stroke: color, "stroke-width": 2,
  });
  const grow = el("animate", { attributeName: "r", from: String(NODE_R), to: String(NODE_R + 13), dur: "0.6s", repeatCount: "1" });
  const fade = el("animate", { attributeName: "opacity", from: "1", to: "0", dur: "0.6s", repeatCount: "1" });
  ring.append(grow, fade);
  fxLayer.append(ring);
  setTimeout(() => ring.remove(), 700);
}

export function updateMap(refs, states, slice, animate) {
  for (const host of HOSTS) {
    const parts = refs.nodeEls.get(host);
    if (!parts) continue;
    const state = states[host] || "clean";
    const color = STATE_COLORS[state] || STATE_COLORS.clean;
    parts.group.style.setProperty("--node-color", color);
    parts.group.dataset.state = state;
    parts.state.textContent = state;
  }

  for (const line of refs.edgeEls.values()) line.setAttribute("class", "edge");

  const visible = slice || [];
  const latest = visible[visible.length - 1];
  if (!latest) return;
  const host = eventHost(latest);
  if (!host || !(host in MAP_POSITIONS)) return;

  if (latest.actor === "red") {
    const trail = redTrail(visible);
    const prev = trail.length > 1 ? trail[trail.length - 2] : null;
    const path = prev ? pathBetween(prev, host) : null;
    if (path && path.length > 1) {
      const line = refs.edgeEls.get(edgeKey(path[path.length - 2], path[path.length - 1]));
      if (line) line.setAttribute("class", "edge lit-red");
      if (animate) {
        path.slice(1).forEach((node, i) => firePulse(refs.fxLayer, path[i], node, "red", i * 0.45));
      }
    } else if (animate) {
      pingNode(refs.fxLayer, host, "red");
    }
  } else if (latest.actor === "blue") {
    const link = blueLinkFor(host);
    if (link) {
      const line = refs.edgeEls.get(edgeKey(link[0], link[1]));
      if (line) line.setAttribute("class", "edge lit-blue");
      if (animate) firePulse(refs.fxLayer, link[0], link[1], "blue", 0);
    } else if (animate) {
      pingNode(refs.fxLayer, host, "blue");
    }
  }
}
