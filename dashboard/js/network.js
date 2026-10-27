// SVG network map: node states, lit edges, red pulse animation.
import { HOSTS, eventHost } from "./state.js";

const SVG_NS = "http://www.w3.org/2000/svg";

const POSITIONS = {
  web: { x: 62, y: 88 },
  app: { x: 180, y: 58 },
  db: { x: 298, y: 88 },
  workstation: { x: 180, y: 218 },
};

const EDGES = [
  ["web", "app"],
  ["app", "db"],
  ["web", "workstation"],
  ["app", "workstation"],
  ["db", "workstation"],
];

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

export function initMap(svg, weights) {
  svg.innerHTML = "";
  const edgeLayer = el("g", { class: "edges" });
  const nodeLayer = el("g", { class: "nodes" });
  const fxLayer = el("g", { class: "fx" });
  svg.append(edgeLayer, nodeLayer, fxLayer);

  const edgeEls = new Map();
  for (const [a, b] of EDGES) {
    const line = el("line", {
      x1: POSITIONS[a].x, y1: POSITIONS[a].y,
      x2: POSITIONS[b].x, y2: POSITIONS[b].y,
      class: "edge",
    });
    edgeLayer.append(line);
    edgeEls.set(edgeKey(a, b), line);
  }

  const nodeEls = new Map();
  for (const host of HOSTS) {
    const pos = POSITIONS[host];
    const group = el("g", {});
    const ring = el("circle", { cx: pos.x, cy: pos.y, r: 27, class: "node-ring" });
    const label = el("text", { x: pos.x, y: pos.y - 2, class: "node-label" });
    label.textContent = host;
    const sub = el("text", { x: pos.x, y: pos.y + 12, class: "node-sub" });
    sub.textContent = `weight ${weights[host] ?? "?"}`;
    const state = el("text", { x: pos.x, y: pos.y + 44, class: "node-state" });
    group.append(ring, label, sub, state);
    nodeLayer.append(group);
    nodeEls.set(host, { ring, state });
  }
  return { edgeEls, nodeEls, fxLayer };
}

// Latest red hosts in order, used to draw the pulse path.
function redTrail(slice) {
  const trail = [];
  for (const ev of slice) {
    if (ev.actor !== "red") continue;
    const host = eventHost(ev);
    if (host && host in POSITIONS && trail[trail.length - 1] !== host) trail.push(host);
  }
  return trail;
}

function firePulse(fxLayer, from, to) {
  const a = POSITIONS[from];
  const b = POSITIONS[to];
  if (!a || !b) return;
  const dot = el("circle", { r: 5, class: "pulse" });
  const motion = el("animateMotion", { dur: "0.8s", repeatCount: "1", path: `M ${a.x} ${a.y} L ${b.x} ${b.y}` });
  dot.append(motion);
  fxLayer.append(dot);
  motion.addEventListener("endEvent", () => dot.remove());
  setTimeout(() => dot.remove(), 1000);
}

function pingNode(fxLayer, host) {
  const pos = POSITIONS[host];
  if (!pos) return;
  const ring = el("circle", {
    cx: pos.x, cy: pos.y, r: 27, fill: "none",
    stroke: STATE_COLORS.compromised, "stroke-width": 2,
  });
  const grow = el("animate", { attributeName: "r", from: "27", to: "40", dur: "0.6s", repeatCount: "1" });
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
    parts.ring.setAttribute("stroke", color);
    parts.ring.style.filter = state === "clean" ? "none" : `drop-shadow(0 0 8px ${color})`;
    parts.state.textContent = state;
    parts.state.setAttribute("fill", color);
  }

  for (const line of refs.edgeEls.values()) line.setAttribute("class", "edge");

  const visible = slice || [];
  const latest = visible[visible.length - 1];
  let litKey = null;
  if (latest) {
    const host = eventHost(latest);
    if (host && host in POSITIONS) {
      if (latest.actor === "red") {
        const trail = redTrail(visible);
        const prev = trail.length > 1 ? trail[trail.length - 2] : null;
        if (prev && refs.edgeEls.has(edgeKey(prev, host))) {
          litKey = edgeKey(prev, host);
        } else {
          for (const [a, b] of EDGES) {
            if (a === host || b === host) refs.edgeEls.get(edgeKey(a, b)).setAttribute("class", "edge lit-red");
          }
        }
        if (animate) {
          if (prev && prev !== host) firePulse(refs.fxLayer, prev, host);
          else pingNode(refs.fxLayer, host);
        }
      } else if (latest.actor === "blue" && ["isolate_host", "patch_service", "restore_host"].includes(latest.tool)) {
        for (const [a, b] of EDGES) {
          if (a === host || b === host) refs.edgeEls.get(edgeKey(a, b)).setAttribute("class", "edge lit-blue");
        }
      }
    }
  }
  if (litKey) refs.edgeEls.get(litKey).setAttribute("class", "edge lit-red");
}
