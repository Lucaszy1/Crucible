// Arena player: cinematic contest stage rendering plus the animation
// queue. Beat lists come from choreography.js (pure, tested); this
// module only plays them with transforms, opacity, and the Web
// Animations API. No libraries, no audio files, offline safe.
import { HOSTS, eventHost, reduceHostStates } from "./state.js";
import { describeEvent } from "./story.js";
import { MAP_EDGES, MAP_POSITIONS, pathBetween, stateLabel } from "./network.js";
import {
  choreograph, integrityFromEvents, latestByActor, pressureFromEvents, verdictWord,
} from "./choreography.js";
import { reduceAudits } from "./audit.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const NODE_HALF = 26;
const INTEGRITY_W = 56;
const CODE = { web: "WB", app: "AP", db: "DB", workstation: "WS" };

function el(name, attrs, text) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function edgeKey(a, b) {
  return [a, b].sort().join("|");
}

function trimmed(a, b) {
  const p = MAP_POSITIONS[a];
  const q = MAP_POSITIONS[b];
  const dx = q.x - p.x;
  const dy = q.y - p.y;
  const dist = Math.hypot(dx, dy) || 1;
  const trim = NODE_HALF + 6;
  return {
    x1: p.x + (dx / dist) * trim, y1: p.y + (dy / dist) * trim,
    x2: q.x - (dx / dist) * trim, y2: q.y - (dy / dist) * trim,
  };
}

function portFor(target, side) {
  const p = (target && MAP_POSITIONS[target]) || { x: 320, y: 140 };
  return { x: side === "red" ? 8 : 632, y: p.y };
}

// Red travels in through web, then along the real links to the target.
function redPathTo(target) {
  if (!target || !MAP_POSITIONS[target]) return [{ x: 8, y: 140 }, { x: 120, y: 140 }];
  const pts = [portFor(target, "red")];
  const leg = target === "web" ? ["web"] : pathBetween("web", target) || [target];
  for (const hop of leg) pts.push({ ...MAP_POSITIONS[hop] });
  return pts;
}

function filePathFrom(target) {
  return redPathTo(target).slice().reverse();
}

export function initArena(svg) {
  svg.innerHTML = "";
  const grid = el("g", { class: "arena-grid-layer" });
  for (let x = 40; x < 640; x += 40) {
    grid.append(el("line", { x1: x, y1: 8, x2: x, y2: 352, class: "arena-grid" }));
  }
  for (let y = 40; y < 360; y += 40) {
    grid.append(el("line", { x1: 8, y1: y, x2: 632, y2: y, class: "arena-grid" }));
  }
  const conduitLayer = el("g", { class: "arena-conduits" });
  const nodeLayer = el("g", { class: "arena-nodes" });
  const fxLayer = el("g", { class: "arena-fx" });
  svg.append(grid, conduitLayer, nodeLayer, fxLayer);

  const edgeEls = new Map();
  for (const [a, b] of MAP_EDGES) {
    const pts = trimmed(a, b);
    const casing = el("line", { ...pts, class: "arena-conduit-casing" });
    const core = el("line", { ...pts, class: "arena-conduit-core" });
    const barrier = el("line", { ...pts, class: "arena-conduit-barrier" });
    barrier.setAttribute("visibility", "hidden");
    conduitLayer.append(casing, core, barrier);
    edgeEls.set(edgeKey(a, b), { casing, core, barrier });
  }

  const nodeEls = new Map();
  for (const host of HOSTS) {
    const p = MAP_POSITIONS[host];
    const group = el("g", { class: "arena-node-group", "data-state": "clean" });
    const foothold = el("rect", {
      x: p.x - 32, y: p.y - 32, width: 64, height: 64, rx: 13, class: "arena-foothold",
    });
    foothold.setAttribute("visibility", "hidden");
    const shield = el("rect", {
      x: p.x - 32, y: p.y - 32, width: 64, height: 64, rx: 13, class: "arena-shield-ring",
    });
    shield.setAttribute("visibility", "hidden");
    const rect = el("rect", {
      x: p.x - NODE_HALF, y: p.y - NODE_HALF,
      width: NODE_HALF * 2, height: NODE_HALF * 2, rx: 10, class: "arena-node",
    });
    const code = el("text", { x: p.x, y: p.y + 5, class: "arena-node-name" }, CODE[host] || host);
    const name = el("text", { x: p.x, y: p.y - NODE_HALF - 8, class: "arena-node-name" }, host);
    const state = el("text", { x: p.x, y: p.y + NODE_HALF + 16, class: "arena-node-state" }, "Clean");
    const track = el("rect", {
      x: p.x - INTEGRITY_W / 2, y: p.y + NODE_HALF + 22,
      width: INTEGRITY_W, height: 6, rx: 3, class: "arena-track",
    });
    const fill = el("rect", {
      x: p.x - INTEGRITY_W / 2, y: p.y + NODE_HALF + 22,
      width: INTEGRITY_W, height: 6, rx: 3, class: "arena-fill",
    });
    const crack = el("polyline", {
      points: `${p.x - 16},${p.y - 22} ${p.x - 4},${p.y - 8} ${p.x - 12},${p.y + 2} ${p.x + 4},${p.y + 12} ${p.x - 4},${p.y + 22}`,
      class: "arena-crack",
    });
    crack.setAttribute("visibility", "hidden");
    const flag = el("g", { class: "arena-flag" });
    flag.append(
      el("line", {
        x1: p.x + 30, y1: p.y - 18, x2: p.x + 30, y2: p.y - 44, class: "arena-flag-pole",
      }),
      el("polygon", {
        points: `${p.x + 30},${p.y - 44} ${p.x + 52},${p.y - 44} ${p.x + 52},${p.y - 32} ${p.x + 30},${p.y - 32}`,
        class: "arena-flag-cloth",
      }),
    );
    flag.setAttribute("visibility", "hidden");
    group.append(foothold, shield, rect, code, name, state, track, fill, crack, flag);
    nodeLayer.append(group);
    nodeEls.set(host, { group, state, fill, foothold, shield, crack, flag });
  }
  return { svg, edgeEls, nodeEls, fxLayer };
}

function citedHosts(slice) {
  const hosts = new Set();
  for (const ev of slice) {
    if (!ev || ev.tool !== "report_finding" || (ev.result || {}).ok !== true) continue;
    for (const ref of ev.evidence_refs || []) {
      const host = String(ref).split(":")[0];
      if (host in MAP_POSITIONS) hosts.add(host);
    }
  }
  return hosts;
}

function lastLaneEvent(slice) {
  for (let i = slice.length - 1; i >= 0; i -= 1) {
    if (slice[i] && (slice[i].actor === "red" || slice[i].actor === "blue")) return slice[i];
  }
  return null;
}

const VERDICT_ICON = { justified: "\u2713", caution: "!", flagged: "\u2715" };

function showVerdictIcon(ui, word) {
  ui.verdict.hidden = false;
  ui.verdict.dataset.tone = word;
  ui.verdictIcon.textContent = VERDICT_ICON[word] || "\u2022";
  ui.verdictWord.textContent = word;
  ui.verdictWord.style.visibility = "hidden";
}

function hideVerdict(ui) {
  ui.verdict.hidden = true;
}

// Persistent end-state for a cursor: consoles, caption, pressure,
// nodes, integrity, barriers, rings, flags, verdict icon. Never
// touches the fx layer, so running transient effects survive ticks.
export function renderArenaStatic(refs, ui, events, cursor) {
  const slice = (events || []).slice(0, cursor);
  const states = reduceHostStates(slice);
  const pressure = pressureFromEvents(slice);
  const integrity = integrityFromEvents(slice);

  ui.redBar.style.width = `${pressure.red}%`;
  ui.blueBar.style.width = `${pressure.blue}%`;
  ui.redPct.textContent = `${pressure.red}%`;
  ui.bluePct.textContent = `${pressure.blue}%`;

  const redEv = latestByActor(slice, "red");
  const blueEv = latestByActor(slice, "blue");
  ui.redLine.textContent = redEv ? describeEvent(redEv) : "Red is waiting.";
  ui.blueLine.textContent = blueEv ? describeEvent(blueEv) : "Blue is waiting.";

  const lane = lastLaneEvent(slice);
  if (lane) ui.caption.textContent = describeEvent(lane);
  else if ((events || []).length > 0) ui.caption.textContent = "Waiting for the first move\u2026";
  else ui.caption.textContent = "No exercise loaded.";

  for (const host of HOSTS) {
    const parts = refs.nodeEls.get(host);
    if (!parts) continue;
    const state = states[host] || "clean";
    parts.group.dataset.state = state;
    parts.state.textContent = stateLabel(state);
    const value = integrity[host] ?? 100;
    parts.fill.setAttribute("width", String((INTEGRITY_W * value) / 100));
    parts.fill.classList.toggle("low", value < 100);
    parts.foothold.setAttribute("visibility", state === "signed-in" || state === "compromised" ? "visible" : "hidden");
    parts.shield.setAttribute("visibility", state === "isolated" ? "visible" : "hidden");
    parts.crack.setAttribute("visibility", state === "compromised" ? "visible" : "hidden");
  }

  for (const [a, b] of MAP_EDGES) {
    const parts = refs.edgeEls.get(edgeKey(a, b));
    if (!parts) continue;
    const shut = states[a] === "isolated" || states[b] === "isolated";
    parts.barrier.setAttribute("visibility", shut ? "visible" : "hidden");
  }

  const flagged = citedHosts(slice);
  for (const host of HOSTS) {
    const parts = refs.nodeEls.get(host);
    if (parts) parts.flag.setAttribute("visibility", flagged.has(host) ? "visible" : "hidden");
  }

  const audits = reduceAudits(events || [], cursor);
  const judged = blueEv ? audits.byTarget.get(blueEv.id) : null;
  const word = judged ? verdictWord((judged.result || {}).verdict) : null;
  if (word) showVerdictIcon(ui, word);
  else hideVerdict(ui);

  return { states, pressure, integrity };
}

// ---- Animation queue: one event at a time, never overlapping. ----

const queue = [];
let playing = false;
let runId = 0;

function reducedMotion() {
  try {
    return typeof window !== "undefined" && Boolean(window.matchMedia) &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function animate(node, keyframes, opts) {
  if (!node || typeof node.animate !== "function") return Promise.resolve();
  try {
    const anim = node.animate(keyframes, opts || {});
    return Promise.resolve(anim.finished).catch(() => {});
  } catch {
    return Promise.resolve();
  }
}

function wait(ms) {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

function centerBox(node) {
  node.style.transformBox = "fill-box";
  node.style.transformOrigin = "center";
}

function alive(myRun) {
  return myRun === runId;
}

// Enqueue one event's transient effects. Static end-state is already
// applied by renderArenaStatic, so dropping queued transient under
// pressure is always safe.
export function arenaNotify(refs, ui, ev, states, speed) {
  if (!ev || !refs || !ui) return;
  const spd = Number(speed) || 1;
  let steps = choreograph(ev, { states });
  if (spd >= 4) steps = steps.filter((step) => !step.secondary);
  if (steps.length === 0) return;
  queue.push({ ev, steps, speed: spd });
  if (queue.length > 6) queue.splice(0, queue.length - 6);
  if (!playing) void drain(refs, ui);
}

async function drain(refs, ui) {
  playing = true;
  const myRun = runId;
  while (queue.length > 0 && alive(myRun)) {
    const job = queue.shift();
    for (const step of job.steps) {
      if (!alive(myRun)) break;
      playSound(step.effect);
      await playStep(refs, ui, step, job.ev, job.speed, myRun);
    }
  }
  if (alive(myRun)) playing = false;
}

// Scrub or view switch: drop everything transient immediately.
export function cancelArena(refs, ui) {
  runId += 1;
  queue.length = 0;
  playing = false;
  if (refs && refs.fxLayer) refs.fxLayer.innerHTML = "";
  if (ui) {
    if (ui.banner) ui.banner.hidden = true;
    if (ui.vignette) {
      ui.vignette.hidden = true;
      ui.vignette.style.opacity = "0";
    }
  }
}

function targetPos(step) {
  const t = step.target;
  if (t && MAP_POSITIONS[t]) return { ...MAP_POSITIONS[t] };
  return { x: 320, y: 140 };
}

async function travel(node, points, ms) {
  if (points.length < 2) return;
  const [start] = points;
  const frames = points.map((p, i) => ({
    transform: `translate(${p.x - start.x}px, ${p.y - start.y}px)`,
    offset: points.length === 1 ? 1 : i / (points.length - 1),
  }));
  await animate(node, frames, { duration: Math.max(60, ms), easing: "linear", fill: "forwards" });
}

async function fadeInOut(node, ms, holdMs) {
  centerBox(node);
  await animate(node, [{ opacity: "0" }, { opacity: "1" }], { duration: Math.max(40, ms / 3), fill: "forwards" });
  await wait(Math.max(0, holdMs || 0));
  await animate(node, [{ opacity: "1" }, { opacity: "0" }], { duration: Math.max(40, ms / 3), fill: "forwards" });
}

async function pop(node, ms) {
  centerBox(node);
  await animate(node, [
    { opacity: "0", transform: "scale(0.4)" },
    { opacity: "1", transform: "scale(1)" },
  ], { duration: Math.max(60, ms), easing: "ease-out", fill: "forwards" });
}

function markText(fxLayer, pos, text, cls, dy) {
  const mark = el("text", {
    x: pos.x, y: pos.y + (dy === undefined ? -54 : dy), class: `arena-fx-mark ${cls || "red"}`,
  }, text);
  fxLayer.append(mark);
  return mark;
}

async function playStep(refs, ui, step, ev, speed, myRun) {
  void ev;
  const fx = refs.fxLayer;
  const scale = 1 / (Number(speed) || 1);
  const base = {
    "scan-ring": 650, "target-ping": 350, packet: 600, "packet-fail": 400,
    "key-try": 500, "key-bounce": 350, denied: 800, "key-through": 350,
    "flash-red": 300, foothold: 300, "file-stream": 600, "file-fail": 300,
    crack: 350, "vignette-red": 700, banner: 1400, "log-beam": 600,
    "beam-fail": 300, "alert-pulse": 500, flag: 300, "isolate-snap": 250,
    barrier: 200, shield: 300, "isolate-fail": 300, wrench: 650,
    "ring-blue": 350, check: 800, "green-sweep": 500, reconnect: 300,
    "restore-fail": 300, "patch-fail": 300, "block-pulse": 400, "block-fail": 300,
    "idle-tick": 300, "bounce-blocked": 700, "scan-fail": 250, verdict: 1400,
  }[step.effect] || 300;
  const ms = Math.max(60, Math.round(base * scale));

  if (reducedMotion()) {
    await reducedStep(refs, ui, step, ms, myRun);
    return;
  }

  const pos = targetPos(step);
  switch (step.effect) {
    case "scan-ring": {
      const from = portFor(step.target, "red");
      const ring = el("circle", { cx: from.x, cy: from.y, r: 8, class: "arena-fx-ring" });
      fx.append(ring);
      centerBox(ring);
      await animate(ring, [
        { transform: "translate(0px, 0px) scale(0.5)", opacity: "1" },
        { transform: `translate(${pos.x - from.x}px, ${pos.y - from.y}px) scale(2.6)`, opacity: "0.9" },
      ], { duration: ms, easing: "ease-out", fill: "forwards" });
      ring.remove();
      break;
    }
    case "target-ping": {
      const ping = el("circle", { cx: pos.x, cy: pos.y, r: NODE_HALF, class: "arena-fx-pulse" });
      fx.append(ping);
      centerBox(ping);
      await animate(ping, [
        { transform: "scale(1)", opacity: "1" },
        { transform: "scale(1.5)", opacity: "0" },
      ], { duration: ms, easing: "ease-out", fill: "forwards" });
      ping.remove();
      break;
    }
    case "packet":
    case "key-through": {
      const pts = redPathTo(step.target);
      const dot = step.effect === "packet"
        ? el("circle", { cx: pts[0].x, cy: pts[0].y, r: 5.5, class: "arena-fx-packet" })
        : keyShape(pts[0]);
      fx.append(dot);
      await travel(dot, pts, ms);
      dot.remove();
      break;
    }
    case "packet-fail": {
      const pts = redPathTo(step.target);
      const mid = pts[Math.max(0, Math.ceil(pts.length / 2) - 1)];
      const dot = el("circle", { cx: pts[0].x, cy: pts[0].y, r: 5.5, class: "arena-fx-packet" });
      fx.append(dot);
      await travel(dot, [pts[0], mid], ms);
      await fadeInOut(dot, 160, 0);
      dot.remove();
      break;
    }
    case "key-try": {
      const pts = redPathTo(step.target);
      const key = keyShape(pts[0]);
      fx.append(key);
      await travel(key, pts, ms);
      key.remove();
      break;
    }
    case "key-bounce": {
      const start = { ...pos };
      const key = keyShape(start);
      fx.append(key);
      await travel(key, [start, { x: start.x - 70, y: start.y - 10 }], ms);
      await animate(key, [{ opacity: "1" }, { opacity: "0" }], { duration: 120, fill: "forwards" });
      key.remove();
      break;
    }
    case "denied": {
      const mark = markText(fx, pos, step.mark || "Denied", "red");
      await fadeInOut(mark, 300, ms);
      mark.remove();
      break;
    }
    case "flash-red": {
      const flash = el("rect", {
        x: pos.x - NODE_HALF, y: pos.y - NODE_HALF,
        width: NODE_HALF * 2, height: NODE_HALF * 2, rx: 10, class: "arena-fx-packet",
      });
      fx.append(flash);
      await fadeInOut(flash, ms, 60);
      flash.remove();
      break;
    }
    case "foothold":
    case "shield":
    case "flag":
    case "crack":
    case "barrier": {
      // Persistent pieces are already visible from static render; pop
      // a brief highlight so the beat reads on screen.
      const ping = el("circle", {
        cx: pos.x, cy: pos.y, r: NODE_HALF + 8,
        class: step.effect === "foothold" || step.effect === "crack" ? "arena-fx-pulse red" : "arena-fx-pulse",
      });
      fx.append(ping);
      centerBox(ping);
      await animate(ping, [
        { transform: "scale(0.7)", opacity: "1" },
        { transform: "scale(1.15)", opacity: "0" },
      ], { duration: ms, easing: "ease-out", fill: "forwards" });
      ping.remove();
      break;
    }
    case "file-stream": {
      const pts = filePathFrom(step.from && MAP_POSITIONS[step.from] ? step.from : step.target);
      const file = el("g", { class: "arena-file" });
      file.append(
        el("rect", { x: pts[0].x - 7, y: pts[0].y - 9, width: 14, height: 18, class: "arena-fx-file" }),
        el("line", { x1: pts[0].x - 4, y1: pts[0].y - 3, x2: pts[0].x + 4, y2: pts[0].y - 3, class: "arena-fx-file" }),
        el("line", { x1: pts[0].x - 4, y1: pts[0].y + 1, x2: pts[0].x + 4, y2: pts[0].y + 1, class: "arena-fx-file" }),
      );
      fx.append(file);
      await travel(file, pts, ms);
      file.remove();
      break;
    }
    case "file-fail":
    case "isolate-fail":
    case "patch-fail":
    case "restore-fail":
    case "block-fail": {
      const parts = step.target && refs.nodeEls.get(step.target);
      if (parts) {
        centerBox(parts.group);
        await animate(parts.group, [
          { transform: "translateX(0px)" }, { transform: "translateX(-4px)" },
          { transform: "translateX(4px)" }, { transform: "translateX(-2px)" },
          { transform: "translateX(0px)" },
        ], { duration: ms, easing: "ease-in-out" });
        parts.group.style.transform = "";
      } else {
        await wait(ms);
      }
      break;
    }
    case "vignette-red": {
      ui.vignette.hidden = false;
      await animate(ui.vignette, [{ opacity: "0" }, { opacity: "1" }, { opacity: "0" }],
        { duration: ms, easing: "ease-in-out", fill: "forwards" });
      ui.vignette.hidden = true;
      break;
    }
    case "banner": {
      ui.banner.textContent = step.text || "Secret exposed";
      ui.banner.hidden = false;
      await animate(ui.banner, [{ opacity: "0" }, { opacity: "1" }], { duration: 180, fill: "forwards" });
      if (alive(myRun)) await wait(ms);
      if (alive(myRun)) await animate(ui.banner, [{ opacity: "1" }, { opacity: "0" }], { duration: 220, fill: "forwards" });
      ui.banner.hidden = true;
      break;
    }
    case "log-beam":
    case "beam-fail": {
      const from = portFor(step.target, "blue");
      const width = Math.max(20, from.x - pos.x);
      const beam = el("rect", {
        x: pos.x, y: pos.y - 7, width, height: 14, class: "arena-fx-beam",
      });
      fx.append(beam);
      beam.style.transformBox = "fill-box";
      beam.style.transformOrigin = "right center";
      await animate(beam, [
        { transform: "scaleX(0)", opacity: step.effect === "beam-fail" ? "0.4" : "1" },
        { transform: "scaleX(1)", opacity: step.effect === "beam-fail" ? "0.4" : "1" },
      ], { duration: Math.round(ms * 0.6), easing: "ease-out", fill: "forwards" });
      await animate(beam, [{ opacity: "1" }, { opacity: "0" }], { duration: Math.round(ms * 0.4), fill: "forwards" });
      beam.remove();
      break;
    }
    case "alert-pulse":
    case "block-pulse": {
      const at = step.effect === "block-pulse" ? { x: 560, y: 140 } : pos;
      const ping = el("circle", { cx: at.x, cy: at.y, r: 12, class: "arena-fx-pulse" });
      fx.append(ping);
      centerBox(ping);
      await animate(ping, [
        { transform: "scale(0.5)", opacity: "1" },
        { transform: "scale(2.2)", opacity: "0" },
      ], { duration: ms, easing: "ease-out", fill: "forwards" });
      ping.remove();
      break;
    }
    case "isolate-snap": {
      const ping = el("circle", { cx: pos.x, cy: pos.y, r: NODE_HALF + 6, class: "arena-fx-pulse" });
      fx.append(ping);
      await fadeInOut(ping, ms, 40);
      ping.remove();
      break;
    }
    case "wrench": {
      const wrench = el("g", { class: "arena-wrench" });
      wrench.append(
        el("circle", { cx: pos.x - 8, cy: pos.y - 8, r: 6, class: "arena-fx-wrench" }),
        el("rect", { x: pos.x - 5, y: pos.y - 5, width: 18, height: 7, class: "arena-fx-wrench" }),
      );
      fx.append(wrench);
      centerBox(wrench);
      await animate(wrench, [{ transform: "rotate(0deg)" }, { transform: "rotate(360deg)" }],
        { duration: ms, easing: "ease-in-out", fill: "forwards" });
      await animate(wrench, [{ opacity: "1" }, { opacity: "0" }], { duration: 140, fill: "forwards" });
      wrench.remove();
      break;
    }
    case "ring-blue": {
      const ping = el("circle", { cx: pos.x, cy: pos.y, r: NODE_HALF, class: "arena-fx-pulse" });
      fx.append(ping);
      centerBox(ping);
      await animate(ping, [
        { transform: "scale(1)", opacity: "1" },
        { transform: "scale(1.5)", opacity: "0" },
      ], { duration: ms, easing: "ease-out", fill: "forwards" });
      ping.remove();
      break;
    }
    case "check": {
      const tick = el("polyline", {
        points: `${pos.x - 10},${pos.y} ${pos.x - 2},${pos.y + 8} ${pos.x + 12},${pos.y - 10}`,
        class: "arena-check",
      });
      fx.append(tick);
      await pop(tick, 260);
      const mark = markText(fx, pos, step.mark || "Patched", "ink");
      await fadeInOut(mark, 300, Math.max(200, ms - 300));
      tick.remove();
      mark.remove();
      break;
    }
    case "green-sweep": {
      const sweep = el("rect", {
        x: pos.x - NODE_HALF - 8, y: pos.y - NODE_HALF - 8,
        width: 16, height: (NODE_HALF + 8) * 2, class: "arena-fx-sweep",
      });
      fx.append(sweep);
      await travel(sweep, [
        { x: pos.x - NODE_HALF - 8, y: 0 }, { x: pos.x + NODE_HALF + 8, y: 0 },
      ], ms);
      sweep.remove();
      break;
    }
    case "reconnect": {
      const ping = el("circle", { cx: pos.x, cy: pos.y, r: NODE_HALF, class: "arena-fx-pulse" });
      fx.append(ping);
      await fadeInOut(ping, ms, 60);
      ping.remove();
      break;
    }
    case "idle-tick": {
      await animate(ui.caption, [{ opacity: "1" }, { opacity: "0.55" }, { opacity: "1" }],
        { duration: ms, easing: "ease-in-out" });
      break;
    }
    case "bounce-blocked": {
      const side = (ev && ev.actor === "blue") ? "blue" : "red";
      const from = portFor(step.target && MAP_POSITIONS[step.target] ? step.target : null, side);
      const bounceAt = step.target && MAP_POSITIONS[step.target]
        ? { x: (from.x + pos.x) / 2, y: pos.y }
        : { x: side === "red" ? 150 : 490, y: 140 };
      const dot = el("circle", {
        cx: from.x, cy: from.y, r: 5.5,
        class: side === "blue" ? "arena-fx-packet blue" : "arena-fx-packet",
      });
      fx.append(dot);
      await animate(dot, [
        { transform: "translate(0px, 0px)" },
        { transform: `translate(${bounceAt.x - from.x}px, ${bounceAt.y - from.y}px)`, offset: 0.45 },
        { transform: `translate(${(bounceAt.x - from.x) * 0.55}px, ${(bounceAt.y - from.y) * 0.55}px)`, offset: 0.7 },
        { transform: `translate(${(bounceAt.x - from.x) * 0.7}px, ${(bounceAt.y - from.y) * 0.7}px)`, opacity: "0" },
      ], { duration: ms, easing: "ease-out", fill: "forwards" });
      dot.remove();
      const mark = markText(fx, bounceAt, step.mark || "Blocked", "ink");
      await fadeInOut(mark, 300, 500);
      mark.remove();
      break;
    }
    case "scan-fail": {
      const from = portFor(step.target, "red");
      const ring = el("circle", { cx: from.x, cy: from.y, r: 8, class: "arena-fx-ring" });
      fx.append(ring);
      await fadeInOut(ring, ms, 0);
      ring.remove();
      break;
    }
    case "verdict": {
      const word = step.word || verdictWord((ev.result || {}).verdict);
      if (!word) break;
      ui.verdict.hidden = false;
      ui.verdict.dataset.tone = word;
      ui.verdictIcon.textContent = VERDICT_ICON[word] || "\u2022";
      ui.verdictWord.textContent = word;
      ui.verdictWord.style.visibility = "visible";
      await pop(ui.verdict, 240);
      if (alive(myRun)) await wait(Math.min(ms, 1200));
      if (alive(myRun)) {
        await animate(ui.verdictWord, [{ opacity: "1" }, { opacity: "0" }], { duration: 220, fill: "forwards" });
        ui.verdictWord.style.visibility = "hidden";
        ui.verdictWord.style.opacity = "";
      }
      break;
    }
    default:
      await wait(Math.min(ms, 200));
  }
}

function keyShape(at) {
  const key = el("g", { class: "arena-key" });
  key.append(
    el("circle", { cx: at.x, cy: at.y, r: 5, class: "arena-fx-key" }),
    el("rect", { x: at.x + 3, y: at.y - 2, width: 14, height: 4, class: "arena-fx-key" }),
    el("rect", { x: at.x + 12, y: at.y + 1, width: 3, height: 5, class: "arena-fx-key" }),
  );
  return key;
}

// Reduced motion: persistent end-state is already on screen from the
// static render, so transient beats collapse to short fades.
async function reducedStep(refs, ui, step, ms, myRun) {
  const fx = refs.fxLayer;
  const pos = targetPos(step);
  const quick = Math.min(220, ms);
  if (step.effect === "banner") {
    ui.banner.textContent = step.text || "Secret exposed";
    ui.banner.hidden = false;
    if (myRun === runId) await wait(Math.min(900, ms));
    ui.banner.hidden = true;
    return;
  }
  if (step.effect === "verdict") {
    const word = step.word;
    if (word) {
      showVerdictIcon(ui, word);
      ui.verdictWord.style.visibility = "visible";
      if (myRun === runId) await wait(Math.min(900, ms));
      ui.verdictWord.style.visibility = "hidden";
    }
    return;
  }
  if (step.effect === "vignette-red" || step.effect === "idle-tick") {
    await wait(80);
    return;
  }
  if (step.mark || step.text) {
    const mark = markText(fx, pos, step.mark || step.text, step.effect === "denied" ? "red" : "ink");
    await fadeInOut(mark, quick, 200);
    mark.remove();
    return;
  }
  const soft = el("circle", { cx: pos.x, cy: pos.y, r: NODE_HALF, class: "arena-fx-pulse" });
  fx.append(soft);
  await fadeInOut(soft, quick, 0);
  soft.remove();
}

// ---- Synthesized effects: one subtle voice per beat, off by default. ----

let soundOn = false;
let audioCtx = null;

export function isArenaSoundOn() {
  return soundOn;
}

export function toggleArenaSound() {
  soundOn = !soundOn;
  if (soundOn) ensureAudio();
  return soundOn;
}

function ensureAudio() {
  try {
    if (typeof window === "undefined") return null;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return null;
    if (!audioCtx) audioCtx = new Ctor();
    if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
    return audioCtx;
  } catch {
    return null;
  }
}

// [fromHz, toHz, seconds, wave]
const VOICES = {
  "scan-ring": [320, 520, 0.22, "sine"],
  packet: [660, 660, 0.07, "triangle"],
  "packet-fail": [300, 180, 0.12, "triangle"],
  "key-try": [520, 520, 0.08, "triangle"],
  "key-bounce": [400, 240, 0.12, "triangle"],
  denied: [180, 140, 0.16, "sine"],
  "key-through": [240, 480, 0.12, "triangle"],
  "flash-red": [120, 90, 0.18, "sine"],
  foothold: [140, 110, 0.2, "sine"],
  "file-stream": [740, 560, 0.14, "triangle"],
  "file-fail": [260, 200, 0.1, "triangle"],
  crack: [110, 70, 0.3, "sawtooth"],
  "vignette-red": [196, 196, 0.35, "sine"],
  banner: [294, 294, 0.4, "sine"],
  "log-beam": [520, 780, 0.2, "sine"],
  "beam-fail": [330, 260, 0.1, "sine"],
  "alert-pulse": [880, 880, 0.09, "triangle"],
  flag: [740, 740, 0.1, "triangle"],
  "isolate-snap": [150, 120, 0.1, "square"],
  barrier: [200, 160, 0.1, "square"],
  shield: [440, 440, 0.12, "triangle"],
  wrench: [700, 900, 0.16, "triangle"],
  "ring-blue": [620, 620, 0.1, "sine"],
  check: [780, 780, 0.12, "sine"],
  "green-sweep": [440, 660, 0.22, "sine"],
  reconnect: [520, 660, 0.16, "sine"],
  "block-pulse": [500, 500, 0.1, "triangle"],
  "bounce-blocked": [170, 120, 0.16, "sine"],
  verdict: [590, 590, 0.12, "sine"],
  "idle-tick": [330, 330, 0.05, "sine"],
};

function playSound(effect) {
  if (!soundOn) return;
  const ctx = ensureAudio();
  const voice = VOICES[effect];
  if (!ctx || !voice) return;
  try {
    const [from, to, secs, wave] = voice;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = wave;
    osc.frequency.setValueAtTime(from, ctx.currentTime);
    if (to !== from) osc.frequency.exponentialRampToValueAtTime(Math.max(40, to), ctx.currentTime + secs);
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.045, ctx.currentTime + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + secs);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + secs + 0.02);
  } catch {
    // Sound is decorative; a blocked context must never break playback.
  }
}
