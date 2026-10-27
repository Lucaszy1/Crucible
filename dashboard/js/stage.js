// Cinematic 2.5D stage: the network plays center in an operations
// hall while a red terminal feed types on the left and a blue
// security console answers on the right. This module holds the pure
// core (layout, camera, lane lines, entity levels) plus the canvas
// renderer. Layout never overlaps: lanes, entities, hosts, HUD,
// caption, and the auditor card each own disjoint rects, and the
// camera only pans and zooms inside the slack that keeps every
// host on screen.
import { arenaGeometry, classicLayout, nodeLabels, roleSub } from "./layout.js";
import {
  displayOutcome, eventHost, hostBlocks, pressureValue, redNotches,
  reduceHostStates, scoreEvents,
} from "./state.js";
import { describeEvent } from "./story.js";
import { verdictCard } from "./choreography.js";
import { applyRail } from "./rail.js";

export const STAGE_WORLD = { w: 1600, h: 900 };
// Hosts fit inside this share of their band, leaving slack on every
// side for the camera to dolly without pushing hosts under lanes or
// entities. Share times zoom must stay under 1 or the dolly rests.
const FIT_SHARE = 0.9;
const DOLLY_ZOOM = 1.1;
// Entity presence radius as a share of play height: each entity
// spans roughly 30 to 35 percent of the stage height.
const ENTITY_SHARE = 0.16;
// Reserved rail under the HUD where the clash orb travels. Hosts
// never enter it, so the orb can never cover a host.
const ORB_RAIL = 30;

function worldBounds(nodes) {
  const pts = Object.values(nodes || {});
  if (pts.length === 0) return { x0: 300, x1: 1300, y0: 200, y1: 700 };
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const node of pts) {
    const pad = (node.r || 80) + 34;
    x0 = Math.min(x0, node.x - pad);
    y0 = Math.min(y0, node.y - pad);
    x1 = Math.max(x1, node.x + pad);
    y1 = Math.max(y1, node.y + pad);
  }
  return { x0, y0, x1, y1 };
}

function laneStrip(rect) {
  return {
    ...rect,
    feed: { x: rect.x + 6, y: rect.y + 6, w: rect.w - 12, h: rect.h - 12 },
  };
}

function laneWithEntity(rect, orientation) {
  if (orientation === "side") return laneStrip(rect);
  const zone = Math.min(64, Math.max(40, rect.h - 8));
  return {
    ...rect,
    entity: { x: rect.x + zone / 2 + 4, y: rect.y + rect.h / 2, r: Math.min(26, zone / 2 - 4) },
    feed: { x: rect.x + zone + 12, y: rect.y + 6, w: rect.w - zone - 20, h: rect.h - 12 },
  };
}

// Stage geometry from the viewport, the world node map, and the
// measured DOM chrome. view maps world to screen at rest; cam pans
// and zooms around it. Every rect stays inside the viewport and
// lanes never touch the host band.
export function stageLayout(viewport, geo, chrome) {
  const w = Math.max(200, (viewport && viewport.w) || 800);
  const h = Math.max(160, (viewport && viewport.h) || 450);
  const nodes = (geo && geo.nodes) || {};
  const hudBottom = Math.max(0, (chrome && chrome.hudBottom) || 0);
  const capTop = chrome && chrome.capTop !== undefined && chrome.capTop !== null ? chrome.capTop : h;
  const audBottom = chrome && chrome.audBottom !== undefined && chrome.audBottom !== null
    ? chrome.audBottom
    : null;
  const gap = h < 260 ? 4 : 10;
  const top = hudBottom + gap;
  const bottom = Math.max(top + 60, capTop - gap);
  const mode = w >= 700 ? "side" : "stack";
  let lanes;
  let band;
  let entities;
  let areaTop = top;
  let areaBottom = bottom;
  if (mode === "side") {
    // Slim strips at the edges, big entities inboard, the network
    // between them: strip | Intrusion | hosts | Guardian | strip.
    const laneW = Math.round(Math.min(150, Math.max(96, w * 0.085)));
    const playH = Math.max(60, bottom - top);
    const room = (w - 16 - laneW * 2 - gap * 4 - 200) / 4;
    const entR = Math.min(Math.max(24, Math.min(150, playH * ENTITY_SHARE)), Math.max(24, room));
    const cy = (top + bottom) / 2;
    const intrusionCx = 8 + laneW + gap + entR + 6;
    const guardianCx = w - 8 - laneW - gap - entR - 6;
    const blueTop = audBottom === null ? top : Math.max(top, Math.min(bottom - 40, audBottom + gap));
    lanes = {
      red: laneStrip({ x: 8, y: top, w: laneW, h: Math.max(40, bottom - top) }),
      blue: laneStrip(
        { x: w - 8 - laneW, y: blueTop, w: laneW, h: Math.max(40, bottom - blueTop) }),
    };
    band = { x0: intrusionCx + entR + gap, x1: guardianCx - entR - gap };
    entities = {
      intrusion: { x: intrusionCx, y: cy, r: entR },
      guardian: { x: guardianCx, y: cy, r: entR },
    };
    areaTop = top + ORB_RAIL;
  } else {
    // One strip row, red left and blue right, so narrow stages keep
    // most of their height for the network.
    const avail = Math.max(60, bottom - top);
    const stripH = Math.round(Math.min(56, Math.max(32, avail * 0.28)));
    const halfW = Math.round((w - 16 - gap) / 2);
    const red = { x: 8, y: bottom - stripH, w: halfW, h: stripH };
    const blue = { x: 8 + halfW + gap, y: bottom - stripH, w: w - 16 - halfW - gap, h: stripH };
    areaBottom = Math.max(areaTop + 30, bottom - stripH - gap);
    lanes = {
      red: laneWithEntity(red, "stack"),
      blue: laneWithEntity(blue, "stack"),
    };
    band = { x0: 8, x1: w - 8 };
    entities = {
      intrusion: { ...lanes.red.entity },
      guardian: { ...lanes.blue.entity },
    };
  }
  const wb = worldBounds(nodes);
  const bandW = Math.max(80, band.x1 - band.x0);
  const bandH = Math.max(60, areaBottom - areaTop);
  const scale = Math.min((bandW * FIT_SHARE) / (wb.x1 - wb.x0), (bandH * FIT_SHARE) / (wb.y1 - wb.y0));
  const bandCx = (band.x0 + band.x1) / 2;
  const bandCy = (areaTop + areaBottom) / 2;
  const wbCx = (wb.x0 + wb.x1) / 2;
  const wbCy = (wb.y0 + wb.y1) / 2;
  const view = { scale, ox: bandCx - wbCx * scale, oy: bandCy - wbCy * scale };
  const hosts = {};
  for (const [id, node] of Object.entries(nodes)) {
    hosts[id] = {
      x: node.x * scale + view.ox,
      y: node.y * scale + view.oy,
      r: (node.r || 80) * scale,
      kind: node.kind || "standard",
    };
  }
  return {
    mode, w, h, gap, view, lanes, band, entities,
    area: { top: areaTop, bottom: areaBottom },
    chrome: { hudBottom, capTop, audBottom },
    hosts, bounds: wb,
    edges: (geo && geo.edges) || [],
  };
}

// Screen position of a world point under a camera {dx, dy, z}:
// pan in screen pixels around the band center, then zoom.
export function applyCamera(layout, cam, wx, wy) {
  const bandCx = (layout.band.x0 + layout.band.x1) / 2;
  const bandCy = (layout.area.top + layout.area.bottom) / 2;
  const z = (cam && cam.z) || 1;
  const dx = (cam && cam.dx) || 0;
  const dy = (cam && cam.dy) || 0;
  const sx = wx * layout.view.scale + layout.view.ox;
  const sy = wy * layout.view.scale + layout.view.oy;
  return { x: bandCx + (sx - bandCx) * z + dx, y: bandCy + (sy - bandCy) * z + dy };
}

// Dolly target for one host: pan it toward the band center at the
// dolly zoom, clamped so every host box stays inside the band and
// the play area. Falls back to rest when the host is unknown.
export function dollyTarget(layout, nodes, hostId) {
  const rest = { dx: 0, dy: 0, z: 1 };
  const node = (nodes || {})[hostId];
  if (!node) return rest;
  const z = DOLLY_ZOOM;
  const bandCx = (layout.band.x0 + layout.band.x1) / 2;
  const bandCy = (layout.area.top + layout.area.bottom) / 2;
  const home = layout.hosts[hostId];
  if (!home) return rest;
  let dx = bandCx - (bandCx + (home.x - bandCx) * z);
  let dy = bandCy - (bandCy + (home.y - bandCy) * z);
  const pad = 6;
  let loX = -Infinity;
  let hiX = Infinity;
  let loY = -Infinity;
  let hiY = Infinity;
  for (const [id, host] of Object.entries(layout.hosts)) {
    const r = (nodes[id] && nodes[id].r ? nodes[id].r * layout.view.scale : host.r) * z + pad;
    const zx = bandCx + (host.x - bandCx) * z;
    const zy = bandCy + (host.y - bandCy) * z;
    loX = Math.max(loX, layout.band.x0 + r - zx);
    hiX = Math.min(hiX, layout.band.x1 - r - zx);
    loY = Math.max(loY, layout.area.top + r - zy);
    hiY = Math.min(hiY, layout.area.bottom - r - zy);
  }
  if (loX > hiX || loY > hiY) return rest;
  dx = Math.min(hiX, Math.max(loX, dx));
  dy = Math.min(hiY, Math.max(loY, dy));
  return { dx, dy, z };
}

// One exponential smoothing step toward the target. Converges
// without overshoot, so the camera never jitters; snaps when close.
export function stepCamera(cam, target, dtMs) {
  const current = cam || { dx: 0, dy: 0, z: 1 };
  const goal = target || { dx: 0, dy: 0, z: 1 };
  const k = 1 - Math.exp(-Math.max(0, dtMs || 16) / 1000 * 5.5);
  const snap = (value, want) => (Math.abs(want - value) < 0.05 ? want : value + (want - value) * k);
  return {
    dx: snap(current.dx || 0, goal.dx || 0),
    dy: snap(current.dy || 0, goal.dy || 0),
    z: snap(current.z || 1, goal.z || 1),
  };
}

// ---------- lane feeds ----------
// Terminal lines typed from real events. Red reads as commands,
// blue as console entries plus reasoning. Credentials never render:
// sign-in lines name the host and account only.

function shortPath(path, max) {
  const text = String(path || "");
  if (text.length <= max) return text;
  return `…${text.slice(text.length - max + 1)}`;
}

export function redLine(ev) {
  if (!ev) return "";
  const args = ev.args || {};
  const result = ev.result || {};
  const host = eventHost(ev) || "?";
  const mark = result.ok === false ? " ✗" : "";
  switch (ev.tool) {
    case "scan_host":
      return `> scan ${host}${mark}`;
    case "http_request":
      return `> GET ${host}${shortPath(args.path || "/", 26)} → ${result.status || "…"}` + mark;
    case "try_credential":
      return result.authenticated === true
        ? `> login ${host} as ${args.user || "?"} · open`
        : `> login ${host} as ${args.user || "?"}` + mark;
    case "read_file":
      return `> read ${host}:${shortPath(args.path || result.path || "", 30)}${mark}`;
    case "list_files":
      return `> ls ${host}:${shortPath(args.path || "/", 24)}${mark}`;
    case "report_finding":
      return `> note ${String(args.text || "").slice(0, 40)}`;
    default:
      return `> ${ev.tool || "move"} ${host}${mark}`;
  }
}

export function blueLine(ev) {
  if (!ev) return "";
  const args = ev.args || {};
  const result = ev.result || {};
  const host = eventHost(ev) || "?";
  if (ev.actor === "auditor") {
    const verdict = (result && result.verdict) || "ruling";
    const score = result.score === undefined || result.score === null ? "-" : result.score;
    return `◆ auditor: ${verdict} ${score}/10`;
  }
  switch (ev.tool) {
    case "read_logs":
      return `· review logs on ${host}`;
    case "isolate_host":
      return result.ok === true ? `· isolate ${host} ✓` : `· isolate ${host} ✗`;
    case "patch_service":
      return result.ok === true ? `· patch ${host} ✓` : `· patch ${host} ✗`;
    case "restore_host":
      return result.ok === true ? `· restore ${host} ✓` : `· restore ${host} ✗`;
    case "report_finding":
      return `· report: ${String(args.text || "").slice(0, 48)}`;
    default:
      return `· ${ev.tool || "move"} ${host}`;
  }
}

function blueReason(ev) {
  const raw = String((ev && ev.reasoning_summary) || "").trim().replace(/\s+/g, " ");
  return raw.length > 140 ? `${raw.slice(0, 139)}…` : raw;
}

// Feed lines for one lane, oldest first: { text, tone, id }.
// tone: cmd, dim, ok, bad, verdict. Blue reasoning follows its move
// as a dim line. Auditor verdicts join the blue lane.
export function laneFeed(events, side) {
  const lane = (events || []).filter((ev) => ev && (ev.actor === "red" || ev.actor === "blue" || ev.actor === "auditor"));
  const out = [];
  for (const ev of lane) {
    if (side === "red" && ev.actor !== "red") continue;
    if (side === "blue" && ev.actor !== "blue" && ev.actor !== "auditor") continue;
    if (side === "red") {
      const failed = (ev.result || {}).ok === false;
      const opened = ev.tool === "try_credential" && (ev.result || {}).authenticated === true;
      out.push({ text: redLine(ev), tone: failed ? "bad" : opened ? "ok" : "cmd", id: ev.id });
    } else if (ev.actor === "auditor") {
      out.push({ text: blueLine(ev), tone: "verdict", id: ev.id });
    } else {
      const failed = (ev.result || {}).ok === false;
      out.push({ text: blueLine(ev), tone: failed ? "bad" : "cmd", id: ev.id });
      const reason = blueReason(ev);
      if (reason) out.push({ text: reason, tone: "dim", id: `${ev.id}:why` });
    }
  }
  return out;
}

// ---------- entity levels ----------
// The Intrusion grows and sharpens with red notch progress; the
// Guardian answers blue containment. Both derive from real events.

export function intrusionLevel(fullEvents, sliceEvents) {
  const notches = redNotches(fullEvents || [], sliceEvents || []);
  if (!notches.total) return 0;
  return Math.max(0, Math.min(1, notches.level / notches.total));
}

// Guardian energy: 1 on a fresh blue containment, decaying over
// the moves since, down to a calm idle floor.
export function guardianPulse(events) {
  const lane = (events || []).filter((ev) => ev && (ev.actor === "red" || ev.actor === "blue"));
  for (let back = 0; back < Math.min(lane.length, 8); back += 1) {
    const ev = lane[lane.length - 1 - back];
    if (ev.actor === "blue" && (ev.result || {}).ok === true &&
        (ev.tool === "isolate_host" || ev.tool === "patch_service")) {
      return Math.max(0.15, 1 - back * 0.14);
    }
  }
  return 0.15;
}

// Outcome wash for the stage: calm blue behind contained and held
// ranges, a red edge when the intrusion ends ahead, else neutral.
export function stageWash(events) {
  const key = displayOutcome(events || []).key;
  if (key === "contained" || key === "held") return "calm";
  if (key === "secret-exposed" || key === "uncontained") return "alert";
  return "none";
}

// ---------- renderer ----------
const ART_FILES = {
  backdrop: "backdrop-hall.webp",
  intrusionTex: "intrusion-splash.webp",
  guardianTex: "guardian-splash.webp",
  streak: "fx-streak.webp",
  ring: "fx-ring.webp",
  shard: "fx-shard.webp",
  noise: "fx-noise.png",
  flare: "fx-flare.webp",
};

// Load committed art for canvas use. Browsers resolve against the
// dashboard assets dir; tests inject decoded images instead.
export function loadStageArt(base) {
  const root = base || "assets/art/";
  const out = {};
  const jobs = Object.entries(ART_FILES).map(([key, file]) => new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      out[key] = img;
      resolve();
    };
    img.onerror = () => resolve();
    img.src = root + file;
  }));
  return Promise.all(jobs).then(() => out);
}

function el(id) {
  return (typeof document !== "undefined" && document.getElementById(id)) || null;
}

// Measured DOM chrome the canvas must stay clear of, in wrap px.
export function measureChrome() {
  const wrap = el("arenaWrap");
  const mid = el("arenaMid");
  const cap = el("arenaCap");
  const aud = el("arenaAud");
  if (!wrap || typeof wrap.getBoundingClientRect !== "function") return null;
  const frame = wrap.getBoundingClientRect();
  const midBox = mid ? mid.getBoundingClientRect() : null;
  const capBox = cap ? cap.getBoundingClientRect() : null;
  let audBottom = null;
  if (aud && aud.classList && aud.classList.contains("show") && aud.getBoundingClientRect) {
    audBottom = aud.getBoundingClientRect().bottom - frame.top;
  }
  return {
    w: frame.width,
    h: frame.height,
    hudBottom: midBox ? midBox.bottom - frame.top : 0,
    capTop: capBox ? capBox.top - frame.top : frame.height,
    audBottom,
  };
}

function makeParticles(count, w, h, rand) {
  const randFn = rand || Math.random;
  const parts = [];
  for (let i = 0; i < count; i += 1) {
    parts.push({
      x: randFn() * w,
      y: randFn() * h,
      r: 0.6 + randFn() * 1.8,
      vx: (randFn() - 0.5) * 9,
      vy: -3 - randFn() * 9,
      tw: randFn() * 6.28,
      red: randFn() < 0.3,
    });
  }
  return parts;
}

export function initStage(canvas, opts) {
  const options = opts || {};
  const refs = {
    canvas,
    ctx: canvas ? canvas.getContext("2d") : null,
    art: options.art || {},
    reduced: Boolean(options.reduced),
    geom: { cw: 0, ch: 0, dpr: 1 },
    layout: null,
    geo: null,
    nodes: {},
    hosts: [],
    events: [],
    cursor: 0,
    states: {},
    cam: { dx: 0, dy: 0, z: 1 },
    camTarget: { dx: 0, dy: 0, z: 1 },
    idleUntil: 0,
    trauma: 0,
    hitstopUntil: 0,
    clock: 0,
    lastT: 0,
    frameAvg: 16,
    lowDetail: false,
    frame: null,
    frameCtx: null,
    cache: {},
    sprites: null,
    samples: [],
    fps: 0,
    fpsCount: 0,
    fpsT: 0,
    blankFrames: 0,
    lastError: "",
    debugEl: null,
    debugInit: false,
    debugTick: 0,
    raf: 0,
    active: true,
    staticOnly: false,
    particles: [],
    typed: { red: { at: 0, chars: 0 }, blue: { at: 0, chars: 0 } },
    lanes: { red: [], blue: [] },
    intrusion: 0,
    guardian: 0.15,
    surge: { red: 0, blue: 0 },
    tug: 0,
    fx: [],
    sparks: [],
    lastRedHost: null,
    wash: "none",
    flash: null,
    label: "",
    weights: {},
    finalScore: null,
    display: null,
    capKey: "",
    hud: {
      hudEl: el("arenaHud"),
      exercise: el("arenaExercise"),
      move: el("arenaMove"),
      total: el("arenaTotal"),
      tugR: el("arenaTugR"),
      tugM: el("arenaTugM"),
      score: el("arenaScore"),
      redLine: el("arenaRedLine"),
      blueLine: el("arenaBlueLine"),
      redMeter: el("arenaRedMeter"),
      blueMeter: el("arenaBlueMeter"),
      cap: el("arenaCap"),
      capTag: el("arenaCapTag"),
      capText: el("arenaCapText"),
      capSub: el("arenaCapSub"),
      aud: el("arenaAud"),
      audNum: el("arenaAudNum"),
      audWord: el("arenaAudWord"),
      audCite: el("arenaAudCite"),
      audNote: el("arenaAudNote"),
      readout: el("arenaReadout"),
    },
    audTimer: 0,
  };
  if (typeof ResizeObserver === "function" && canvas) {
    refs.ro = new ResizeObserver(() => fitStage(refs));
    refs.ro.observe(canvas);
  }
  fitStage(refs);
  return refs;
}

export function fitStage(refs) {
  if (!refs || !refs.canvas) return;
  const canvas = refs.canvas;
  const cw = canvas.clientWidth || 800;
  const ch = canvas.clientHeight || 450;
  const dpr = Math.min(2, (typeof window !== "undefined" && window.devicePixelRatio) || 1);
  refs.geom = { cw, ch, dpr };
  canvas.width = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  refs.particles = makeParticles(Math.round(Math.min(110, Math.max(40, (cw * ch) / 16000))), cw, ch);
  relayoutStage(refs);
  ensureFrame(refs);
  buildStaticLayers(refs);
  // Repaint at once: resizing clears the visible canvas, so never
  // leave it blank waiting for the next tick.
  drawStage(refs);
}

// Offscreen layer canvas, or null where the DOM is unavailable.
function makeLayer(w, h) {
  try {
    if (typeof document === "undefined") return null;
    const layer = document.createElement("canvas");
    layer.width = Math.max(1, Math.round(w));
    layer.height = Math.max(1, Math.round(h));
    return layer;
  } catch {
    return null;
  }
}

// Double-buffer surface in device pixels. A missing surface falls
// back to painting the visible canvas directly.
function ensureFrame(refs) {
  const { cw, ch, dpr } = refs.geom;
  const w = Math.max(1, Math.round(cw * dpr));
  const h = Math.max(1, Math.round(ch * dpr));
  if (refs.frame && refs.frameCtx && refs.frame.width === w && refs.frame.height === h) return;
  refs.frame = makeLayer(w, h);
  refs.frameCtx = null;
  try {
    refs.frameCtx = refs.frame ? refs.frame.getContext("2d") : null;
  } catch {
    refs.frameCtx = null;
  }
  if (!refs.frame || !refs.frameCtx) {
    refs.frame = null;
    refs.frameCtx = null;
  }
}

export function setStageLayout(refs, layout) {
  if (!refs) return;
  refs.layout = layout;
  relayoutStage(refs);
}

// Swap art in (async load in the app, injected in tests), rebuild
// the static layers, and repaint at once so the new art shows.
export function setStageArt(refs, art) {
  if (!refs) return;
  refs.art = art || {};
  buildStaticLayers(refs);
  drawStage(refs);
}

// Cached static layers: the far backdrop, the floor grid, and the
// vignette bake once per size (plus band for the grid) and blit per
// frame with small parallax offsets. Keys rebuild them when the
// layout, size, or art changes.
const LAYER_M = 28;

function buildStaticLayers(refs) {
  if (!refs || !refs.stage) return;
  const { cw, ch, dpr } = refs.geom;
  if (!cw || !ch) return;
  const cache = refs.cache || (refs.cache = {});
  const band = refs.stage.band;
  const area = refs.stage.area;
  const size = `${cw}x${ch}@${dpr}`;
  const farKey = `far|${size}|${refs.art && refs.art.backdrop ? "art" : "flat"}`;
  if (cache.farKey !== farKey || !cache.far) {
    cache.far = bakeLayer(refs, LAYER_M, (g) => paintFarInto(refs, g, cw, ch));
    cache.farKey = farKey;
  }
  const gridKey = `grid|${size}|${band.x0},${band.x1},${area.top},${area.bottom}`;
  if (cache.gridKey !== gridKey || !cache.grid) {
    cache.grid = bakeLayer(refs, LAYER_M, (g) => paintGridInto(refs, g, cw, ch));
    cache.gridKey = gridKey;
  }
  const vigKey = `vig|${size}`;
  if (cache.vigKey !== vigKey || !cache.vig) {
    cache.vig = bakeLayer(refs, 0, (g) => paintVigInto(g, cw, ch));
    cache.vigKey = vigKey;
  }
}

function bakeLayer(refs, margin, paint) {
  const { cw, ch, dpr } = refs.geom;
  const layer = makeLayer((cw + margin * 2) * dpr, (ch + margin * 2) * dpr);
  if (!layer) return null;
  let g = null;
  try {
    g = layer.getContext("2d");
  } catch {
    return null;
  }
  if (!g) return null;
  try {
    g.setTransform(dpr, 0, 0, dpr, margin * dpr, margin * dpr);
    paint(g);
  } catch {
    return null;
  }
  return layer;
}

function paintFarInto(refs, g, cw, ch) {
  const M = LAYER_M;
  const art = refs.art && refs.art.backdrop;
  if (art && art.width) {
    const scale = Math.max((cw + M * 2) / art.width, (ch + M * 2) / art.height) * 1.06;
    const dw = art.width * scale;
    const dh = art.height * scale;
    g.save();
    g.globalAlpha = 0.9;
    g.drawImage(art, (cw - dw) / 2, (ch - dh) / 2, dw, dh);
    g.restore();
  } else {
    const sky = g.createLinearGradient(0, 0, 0, ch);
    sky.addColorStop(0, "#05070c");
    sky.addColorStop(0.6, "#0b1020");
    sky.addColorStop(1, "#05070c");
    g.fillStyle = sky;
    g.fillRect(-M, -M, cw + M * 2, ch + M * 2);
  }
  const dim = g.createLinearGradient(0, 0, 0, ch);
  dim.addColorStop(0, "rgba(5,7,12,0.55)");
  dim.addColorStop(0.35, "rgba(5,7,12,0.25)");
  dim.addColorStop(1, "rgba(5,7,12,0.72)");
  g.fillStyle = dim;
  g.fillRect(-M, -M, cw + M * 2, ch + M * 2);
}

function paintGridInto(refs, g, cw, ch) {
  const M = LAYER_M;
  const stage = refs.stage;
  const horizon = stage.area.top + (stage.area.bottom - stage.area.top) * 0.3;
  const vpx = (stage.band.x0 + stage.band.x1) / 2;
  g.save();
  g.globalCompositeOperation = "lighter";
  g.strokeStyle = "rgba(127,212,255,0.22)";
  g.lineWidth = 1;
  g.beginPath();
  for (let i = -12; i <= 12; i += 1) {
    g.moveTo(vpx + i * 26, horizon);
    g.lineTo(vpx + i * 150, ch + 40);
  }
  const rows = 9;
  for (let i = 0; i < rows; i += 1) {
    const f = i / (rows - 1);
    const y = horizon + (ch + 40 - horizon) * f * f;
    g.moveTo(-M, y);
    g.lineTo(cw + M, y);
  }
  g.stroke();
  const sheen = g.createLinearGradient(0, horizon, 0, ch);
  sheen.addColorStop(0, "rgba(47,123,255,0.10)");
  sheen.addColorStop(1, "rgba(47,123,255,0.015)");
  g.fillStyle = sheen;
  g.fillRect(-M, horizon, cw + M * 2, ch - horizon + M);
  g.restore();
}

function paintVigInto(g, cw, ch) {
  const vig = g.createRadialGradient(cw / 2, ch / 2, Math.min(cw, ch) * 0.36, cw / 2, ch / 2, Math.max(cw, ch) * 0.75);
  vig.addColorStop(0, "rgba(0,0,0,0)");
  vig.addColorStop(1, "rgba(0,0,0,0.5)");
  g.fillStyle = vig;
  g.fillRect(0, 0, cw, ch);
}

// Pre-rendered radial glow sprites: entities and halos blit these
// instead of building gradients every frame. No shadowBlur and no
// canvas filter anywhere in the per-frame path.
function buildSprites(refs) {
  if (refs.sprites) return refs.sprites;
  const sprites = {
    red: glowSprite([[0, "rgba(255,59,47,0.55)"], [0.5, "rgba(255,59,47,0.18)"], [1, "rgba(255,59,47,0)"]]),
    blue: glowSprite([[0, "rgba(47,123,255,0.55)"], [0.5, "rgba(47,123,255,0.18)"], [1, "rgba(47,123,255,0)"]]),
    cyan: glowSprite([[0, "rgba(127,212,255,0.5)"], [0.5, "rgba(127,212,255,0.16)"], [1, "rgba(127,212,255,0)"]]),
    white: glowSprite([[0, "rgba(232,236,244,0.5)"], [0.5, "rgba(232,236,244,0.16)"], [1, "rgba(232,236,244,0)"]]),
    gold: glowSprite([[0, "rgba(255,200,61,0.45)"], [0.5, "rgba(255,200,61,0.15)"], [1, "rgba(255,200,61,0)"]]),
    intrusion: glowSprite([[0, "rgba(255,59,47,0.6)"], [0.55, "rgba(160,20,16,0.3)"], [1, "rgba(255,59,47,0)"]]),
    guardian: glowSprite([[0, "rgba(127,212,255,0.56)"], [0.6, "rgba(47,123,255,0.28)"], [1, "rgba(127,212,255,0)"]]),
    guardianCore: glowSprite([[0, "rgba(232,246,255,0.95)"], [0.4, "rgba(127,212,255,0.9)"], [1, "rgba(127,212,255,0)"]]),
  };
  refs.sprites = sprites;
  return sprites;
}

function glowSprite(stops) {
  const layer = makeLayer(128, 128);
  if (!layer) return null;
  let g = null;
  try {
    g = layer.getContext("2d");
  } catch {
    return null;
  }
  if (!g) return null;
  try {
    const grad = g.createRadialGradient(64, 64, 2, 64, 64, 64);
    for (const [at, color] of stops) grad.addColorStop(at, color);
    g.fillStyle = grad;
    g.fillRect(0, 0, 128, 128);
  } catch {
    return null;
  }
  return layer;
}

function drawGlow(ctx, sprite, x, y, r, alpha) {
  if (!sprite || !(r > 0)) return;
  if (alpha === undefined) {
    ctx.drawImage(sprite, x - r, y - r, r * 2, r * 2);
    return;
  }
  ctx.save();
  ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
  ctx.drawImage(sprite, x - r, y - r, r * 2, r * 2);
  ctx.restore();
}

function relayoutStage(refs) {
  if (!refs) return;
  const { cw, ch } = refs.geom;
  const measured = measureChrome();
  const chrome = measured && measured.w > 0
    ? measured
    : {
      hudBottom: Math.min(130, Math.max(60, ch * 0.2)),
      capTop: ch - Math.min(150, Math.max(56, ch * 0.18)),
      audBottom: null,
    };
  const clean = refs.layout && refs.layout.hosts ? refs.layout : classicLayout();
  refs.geo = arenaGeometry(clean);
  refs.stage = stageLayout({ w: cw, h: ch }, refs.geo, chrome);
  refs.hosts = Object.keys(refs.stage.hosts);
  refs.nodes = refs.geo.nodes || {};
}

// ---------- director ----------
// Scrub-safe static rebuild plus the live event queue. Mirrors the
// arena call shapes so the app can drive either view.

function lastLaneEvent(slice) {
  for (let i = slice.length - 1; i >= 0; i -= 1) {
    if (slice[i] && (slice[i].actor === "red" || slice[i].actor === "blue")) return slice[i];
  }
  return null;
}

function latestByActor(slice, actor) {
  for (let i = slice.length - 1; i >= 0; i -= 1) {
    if (slice[i] && slice[i].actor === actor) return slice[i];
  }
  return null;
}

export function renderStageStatic(refs, events, cursor, opts, instant) {
  const list = events || [];
  const at = Math.max(0, Math.min(list.length, cursor || 0));
  const slice = list.slice(0, at);
  const o = opts || {};
  if (refs) {
    if (o.layout) refs.layout = o.layout;
    relayoutStage(refs);
    const clean = refs.layout && refs.layout.hosts ? refs.layout : classicLayout();
    const hosts = Object.keys(refs.stage.hosts);
    const weights = o.weights || {};
    const states = reduceHostStates(slice, hosts);
    const blocks = hostBlocks(slice, hosts);
    const prog = redNotches(list, slice);
    const pressure = pressureValue(slice, clean);
    const scoreInfo = scoreEvents(slice, weights);
    const ended = Boolean(o.finalScore) && at >= list.length && list.length > 0;
    const score = ended && typeof o.finalScore.blue_score === "number"
      ? o.finalScore.blue_score
      : scoreInfo.blue_score;
    const latest = lastLaneEvent(slice);
    const redLast = latestByActor(slice, "red");
    const blueLast = latestByActor(slice, "blue");
    let tag = "EXERCISE";
    let tone = "system";
    const hostWord = hosts.length === 1 ? "host" : "hosts";
    let caption = `${hosts.length} ${hostWord}. One protected file.`;
    let sub = list.length === 0 ? "No exercise loaded." : "Waiting for the first move.";
    if (ended) {
      const display = o.display || null;
      caption = display ? `Exercise over: ${display.label}.` : "Exercise over.";
      sub = display ? display.line : `Final score ${Math.round(score)} after ${at} moves.`;
    } else if (latest) {
      tag = latest.actor === "red" ? "RED" : "BLUE";
      tone = latest.actor === "red" ? "red" : "blue";
      const sentence = describeEvent(latest);
      caption = sentence.length > 110 ? `${sentence.slice(0, 107).trimEnd()}...` : sentence;
      sub = `Move ${at} of ${list.length}.`;
    }
    refs.events = list;
    refs.cursor = at;
    refs.states = states;
    refs.attackMap = o.attackMap || null;
    refs.snap = {
      states, blocks, hostOrder: hosts, pressure,
      level: prog.level, notches: prog.notches, total: prog.total,
      score, moves: at, totalMoves: list.length, label: o.label || "Exercise",
      caption, sub, tag, tagTone: tone,
      redLine: redLast ? describeEvent(redLast) : "Red is waiting.",
      blueLine: blueLast ? describeEvent(blueLast) : "Blue is waiting.",
    };
    refs.lanes = { red: laneFeed(slice, "red"), blue: laneFeed(slice, "blue") };
    let exposed = null;
    for (const ev of slice) {
      if (ev && ev.actor === "red" && ev.tool === "read_file" &&
          (ev.result || {}).ok === true &&
          String((ev.result || {}).path || "").startsWith("/secret/")) {
        exposed = eventHost(ev);
      }
    }
    refs.exposed = exposed;
    refs.intrusion = intrusionLevel(list, slice);
    refs.guardian = guardianPulse(slice);
    refs.tug = pressure;
    refs.fx = [];
    refs.sparks = [];
    let lastRed = null;
    for (const ev of slice) {
      if (ev && ev.actor === "red" && ev.tool === "try_credential" &&
          (ev.result || {}).authenticated === true) {
        lastRed = eventHost(ev);
      }
    }
    refs.lastRedHost = lastRed;
    refs.wash = stageWash(slice);
    refs.labels = nodeLabels(clean);
    refs.roles = {};
    for (const host of clean.hosts) refs.roles[host.id] = host.role;
    refs.jewel = clean.jewel_host;
    refs.flash = null;
    refs.surge = { red: 0, blue: 0 };
    refs.cam = { dx: 0, dy: 0, z: 1 };
    refs.camTarget = { dx: 0, dy: 0, z: 1 };
    refs.trauma = 0;
    if (instant || refs.reduced) {
      refs.typed = {
        red: { at: -1, chars: Infinity },
        blue: { at: -1, chars: Infinity },
      };
    }
    updateStageHud(refs);
    hideStageAuditor(refs, true);
    drawStage(refs);
    return { states };
  }
  return { states: {} };
}

export function stageNotify(refs, ev, states, speed) {
  if (!ev || !refs) return;
  if (states) refs.states = states;
  const side = ev.actor === "red" ? "red" : ev.actor === "blue" || ev.actor === "auditor" ? "blue" : null;
  if (side) {
    refs.lanes[side] = laneFeed(refs.events.slice(0, refs.cursor), side);
    refs.typed[side] = { at: refs.clock, chars: 0 };
  }
  const at = refs.events.slice(0, refs.cursor);
  refs.intrusion = intrusionLevel(refs.events, at);
  refs.guardian = guardianPulse(at);
  refs.wash = stageWash(at);
  refs.tug = pressureValue(at, refs.layout && refs.layout.hosts ? refs.layout : null);
  if (ev.actor === "red") refs.surge.red = refs.clock + 900;
  if (ev.actor === "blue") refs.surge.blue = refs.clock + 900;
  const prevRed = refs.lastRedHost;
  if (ev.actor === "red" && ev.tool === "try_credential" && (ev.result || {}).authenticated === true) {
    refs.lastRedHost = eventHost(ev);
  }
  if (!refs.reduced && (ev.actor === "red" || ev.actor === "blue")) {
    const orb = orbPoint(refs);
    if (orb) {
      refs.fx.push({ kind: "ring", x: orb.x, y: orb.y, t0: refs.clock, dur: 700,
        color: ev.actor === "red" ? "255,90,80" : "127,212,255", r0: 8, r1: 64 });
      spawnSparks(refs, orb.x, orb.y, ev.actor === "red" ? "red" : "blue", 10);
    }
    for (const fx of fxForEvent(ev, { jewel: refs.jewel, lastRedHost: prevRed })) {
      refs.fx.push({ ...fx, t0: refs.clock });
    }
  }
  if (ev.actor === "red" && ev.tool === "read_file" && (ev.result || {}).ok === true &&
      String((ev.result || {}).path || "").startsWith("/secret/")) {
    refs.trauma = Math.min(1, refs.trauma + 0.8);
  }
  const host = eventHost(ev);
  if (host && refs.nodes[host]) {
    refs.camTarget = dollyTarget(refs.stage, refs.nodes, host);
    refs.idleUntil = refs.clock + 2600 / (Number(speed) || 1);
  }
  const r = (ev.result || {}).ok === true;
  if (ev.actor === "blue" && r && (ev.tool === "isolate_host" || ev.tool === "patch_service")) {
    refs.trauma = Math.min(1, refs.trauma + 0.45);
  }
  if (ev.actor === "auditor") showStageAuditor(refs, ev);
  updateStageHud(refs);
  if (refs.reduced) drawStage(refs);
}

function ensureSegs(meter, count) {
  while (meter.children.length < count) meter.append(document.createElement("i"));
  while (meter.children.length > count) meter.lastChild.remove();
  return [...meter.children];
}

function updateStageHud(refs) {
  const hud = refs.hud;
  const snap = refs.snap;
  if (!hud || !snap) return;
  const pc = `${((0.5 + Math.max(-1, Math.min(1, snap.pressure)) / 2) * 100).toFixed(1)}%`;
  if (hud.tugR) hud.tugR.style.width = pc;
  if (hud.tugM) hud.tugM.style.left = pc;
  if (hud.redMeter && typeof document !== "undefined") {
    const segs = ensureSegs(hud.redMeter, snap.notches.length);
    for (let i = 0; i < segs.length; i += 1) segs[i].className = snap.notches[i] ? "on" : "";
  }
  if (hud.blueMeter && typeof document !== "undefined") {
    const order = snap.hostOrder && snap.hostOrder.length > 0 ? snap.hostOrder : refs.hosts;
    const segs = ensureSegs(hud.blueMeter, order.length);
    for (let i = 0; i < segs.length; i += 1) {
      const block = snap.blocks[order[i]];
      segs[i].className = block === "breached" ? "bad" : block === "isolated" ? "iso" : block === "patched" ? "fixed" : "on";
    }
  }
  if (hud.score) hud.score.textContent = String(Math.round(snap.score));
  if (hud.exercise) hud.exercise.textContent = snap.label || "Exercise";
  if (hud.move) hud.move.textContent = String(snap.moves).padStart(2, "0");
  if (hud.total) hud.total.textContent = String(snap.totalMoves);
  if (hud.redLine) hud.redLine.textContent = snap.redLine;
  if (hud.blueLine) hud.blueLine.textContent = snap.blueLine;
  const capKey = `${snap.tag}|${snap.caption}|${snap.sub}`;
  if (capKey !== refs.capKey) {
    refs.capKey = capKey;
    if (hud.capTag) {
      hud.capTag.textContent = snap.tag;
      hud.capTag.className = snap.tagTone || "system";
      hud.capTag.style.fontSize = snap.tag.length > 5 ? "clamp(14px,2.6cqw,40px)" : "";
    }
    if (hud.capText) hud.capText.textContent = snap.caption;
    if (hud.capSub) hud.capSub.textContent = snap.sub;
  }
  if (hud.readout) {
    const link = snap.pressure > 0.3 ? "CONTESTED" : snap.pressure < -0.3 ? "HELD" : "OPEN";
    hud.readout.textContent =
      `${snap.label || "EX"}  MOVE ${snap.moves}/${snap.totalMoves}\n` +
      `LANES R${refs.lanes.red.length} B${refs.lanes.blue.length}\n` +
      `LINK ${link}`;
  }
}

export function showStageAuditor(refs, ev) {
  const card = verdictCard(ev);
  const hud = refs && refs.hud;
  if (!card || !hud || !hud.aud) return;
  if (hud.audNum) hud.audNum.textContent = `${card.score == null ? "-" : card.score}/10`;
  if (hud.audWord) hud.audWord.textContent = String(card.word || "").toUpperCase();
  if (hud.audCite) hud.audCite.textContent = card.ids.length > 0 ? `cites ${card.ids.join(", ")}` : "no cited lines";
  if (hud.audNote) hud.audNote.textContent = String(card.rationale || "").slice(0, 140);
  hud.aud.className = card.word === "justified" ? "show" : "show bad";
  layOutStageRail(refs);
  relayoutStage(refs);
  if (refs.audTimer) clearTimeout(refs.audTimer);
  refs.audTimer = setTimeout(() => hideStageAuditor(refs), 4200);
}

export function hideStageAuditor(refs, silent) {
  if (!refs) return;
  if (refs.audTimer) {
    clearTimeout(refs.audTimer);
    refs.audTimer = 0;
  }
  if (refs.hud.aud && refs.hud.aud.className !== "") {
    refs.hud.aud.className = "";
    if (!silent) {
      layOutStageRail(refs);
      relayoutStage(refs);
    }
  }
}

function layOutStageRail(refs) {
  if (!refs || !refs.hud) return null;
  const hud = refs.hud;
  return applyRail({
    wrap: el("arenaWrap"),
    mid: el("arenaMid"),
    aud: hud.aud,
    cap: hud.cap,
    red: el("thoughtRed"),
    blue: el("thoughtBlue"),
  });
}

export function flashStageHost(refs, host) {
  if (!refs || !host) return;
  refs.flash = { host, until: refs.clock + 900 };
  if (refs.reduced) drawStage(refs);
}

export function glitchStage(refs) {
  if (!refs) return;
  refs.trauma = 1;
  refs.glitchUntil = refs.clock + 1200;
  if (refs.reduced) drawStage(refs);
}

export function firstAccessStage(refs, host) {
  if (!refs) return;
  refs.flash = { host, until: refs.clock + 1600, first: true };
  refs.trauma = Math.min(1, refs.trauma + 0.5);
  if (refs.reduced) drawStage(refs);
}

export function cancelStage(refs) {
  if (!refs) return;
  refs.active = false;
  if (refs.raf) {
    if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(refs.raf);
    else clearTimeout(refs.raf);
    refs.raf = 0;
  }
  if (refs.audTimer) {
    clearTimeout(refs.audTimer);
    refs.audTimer = 0;
  }
  if (refs.ro) {
    refs.ro.disconnect();
    refs.ro = null;
  }
}

export function setStageActive(refs, on) {
  if (!refs) return;
  refs.active = Boolean(on);
  if (refs.active && !refs.reduced) startStageLoop(refs);
}

// p95 of recent frame times in ms, for the debug overlay.
export function frameP95(samples) {
  const vals = (samples || []).filter((v) => typeof v === "number" && v > 0);
  if (vals.length === 0) return 0;
  const sorted = [...vals].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))];
}

function debugEnabled() {
  try {
    return typeof window !== "undefined" && typeof window.location !== "undefined" &&
      /[?&]debug=1\b/.test(window.location.search || "");
  } catch {
    return false;
  }
}

function ensureStageDebug(refs) {
  if (refs.debugInit) return refs.debugEl;
  refs.debugInit = true;
  if (!debugEnabled()) return null;
  try {
    if (typeof document === "undefined") return null;
    const host = el("arenaWrap") ||
      (refs.canvas && refs.canvas.parentNode) ||
      (typeof document.body !== "undefined" ? document.body : null);
    if (!host || typeof host.appendChild !== "function") return null;
    const box = document.createElement("div");
    box.setAttribute("data-stage-debug", "1");
    box.style.cssText = "position:absolute;left:8px;top:8px;z-index:30;font:10px/1.5 ui-monospace,Menlo,monospace;color:#9fe0b8;background:rgba(4,8,6,0.78);padding:4px 8px;border-radius:6px;pointer-events:none;white-space:pre;";
    box.textContent = "stage …";
    host.appendChild(box);
    refs.debugEl = box;
    return box;
  } catch {
    return null;
  }
}

function updateStageDebug(refs, force) {
  const box = ensureStageDebug(refs);
  if (!box) return;
  refs.debugTick = (refs.debugTick || 0) + 1;
  if (!force && refs.debugTick % 20 !== 0) return;
  const p95 = frameP95(refs.samples || []);
  const avg = refs.frameAvg || 0;
  box.textContent = `stage ${refs.fps || 0}fps ${avg.toFixed(1)}ms p95 ${p95.toFixed(1)} ` +
    `${refs.lowDetail ? "LOW" : "FULL"} blanks ${refs.blankFrames || 0}` +
    (refs.lastError ? `\n! ${refs.lastError}` : "");
}

// Event-to-effects map, pure: each real event names the beams it
// fires. ctx carries { jewel, lastRedHost }. Lateral moves draw a
// tendril from the previous red foothold to the new host.
export function fxForEvent(ev, ctx) {
  if (!ev) return [];
  const c = ctx || {};
  const host = eventHost(ev);
  const r = ev.result || {};
  const failed = r.ok === false;
  if (ev.actor === "red") {
    if (failed && ev.tool !== "try_credential") return host ? [{ kind: "fizzle", host, dur: 700 }] : [];
    switch (ev.tool) {
      case "scan_host":
        return host ? [{ kind: "fan", host, dur: 900 }] : [];
      case "http_request":
      case "list_files":
        return host ? [{ kind: "needle", host, dur: 800 }] : [];
      case "try_credential":
        if (r.authenticated !== true) return host ? [{ kind: "keys", host, dur: 900 }] : [];
        if (!host) return [];
        const out = [{ kind: "pierce", host, dur: 1000 }];
        if (c.lastRedHost && c.lastRedHost !== host) {
          out.push({ kind: "tendril", from: c.lastRedHost, host, dur: 1200 });
        }
        return out;
      case "read_file": {
        if (!host) return [];
        const path = String(r.path || (ev.args || {}).path || "");
        if (r.ok === true && path.startsWith("/secret/")) return [{ kind: "grab", host, dur: 1400 }];
        if (r.ok === true) return [{ kind: "siphon", host, dur: 1100 }];
        return [{ kind: "fizzle", host, dur: 700 }];
      }
      default:
        return [];
    }
  }
  if (ev.actor === "blue") {
    if (failed) return host ? [{ kind: "fizzle", host, dur: 700, color: "blue" }] : [];
    switch (ev.tool) {
      case "read_logs":
        return host ? [{ kind: "scanring", host, dur: 900 }] : [];
      case "report_finding":
        return [{ kind: "flare", dur: 800 }];
      case "isolate_host":
        return host ? [
          { kind: "slam", host, dur: 1000 },
          { kind: "ring", host, t0: 0, dur: 800, color: "127,212,255", r0: 10, r1: 90 },
        ] : [];
      case "patch_service":
        return host ? [{ kind: "sweep", host, dur: 900 }] : [];
      case "restore_host":
        return host ? [{ kind: "reconnect", host, dur: 1000 }] : [];
      default:
        return [];
    }
  }
  return [];
}

// Clash orb position on the rail: red pressure pushes it toward the
// Guardian's side, containment pushes it back toward the Intrusion.
export function clashOrbX(x0, x1, pressure) {
  const p = Math.max(-1, Math.min(1, Number(pressure) || 0));
  return x0 + (x1 - x0) * (0.5 + (p / 2) * 0.76);
}

// Rolling frame-time budget with hysteresis: slow machines drop to a
// lower-detail pass (fewer particles, no grain) and recover cleanly.
export function frameBudget(avg, low, dtMs) {
  const dt = Math.min(100, Math.max(1, Number(dtMs) || 16));
  const next = avg + (dt - avg) * 0.06;
  if (!low && next > 26) return { avg: next, low: true };
  if (low && next < 17) return { avg: next, low: false };
  return { avg: next, low: Boolean(low) };
}

function startStageLoop(refs) {
  if (!refs || refs.raf || refs.reduced || !refs.active) return;
  const frames = typeof requestAnimationFrame === "function"
    ? requestAnimationFrame
    : (fn) => setTimeout(fn, 16);
  const tick = (now) => {
    if (!refs.active) {
      refs.raf = 0;
      return;
    }
    refs.raf = frames(tick);
    const t = typeof now === "number" ? now : refs.clock + 16;
    const dt = Math.max(1, Math.min(100, t - (refs.lastT || t - 16)));
    refs.lastT = t;
    const budget = frameBudget(refs.frameAvg || 16, refs.lowDetail, dt);
    refs.frameAvg = budget.avg;
    refs.lowDetail = budget.low;
    refs.samples.push(dt);
    if (refs.samples.length > 120) refs.samples.shift();
    refs.fpsCount += 1;
    if (!refs.fpsT) refs.fpsT = t;
    if (t - refs.fpsT >= 500) {
      refs.fps = Math.round((refs.fpsCount * 1000) / (t - refs.fpsT));
      refs.fpsCount = 0;
      refs.fpsT = t;
    }
    if (t < refs.hitstopUntil) {
      drawStage(refs);
      return;
    }
    refs.clock += dt;
    refs.cam = stepCamera(refs.cam, refs.camTarget, dt);
    if (refs.idleUntil && refs.clock > refs.idleUntil) {
      refs.idleUntil = 0;
      refs.camTarget = { dx: 0, dy: 0, z: 1 };
    }
    refs.trauma = Math.max(0, refs.trauma - (dt / 1000) * 1.4);
    for (const part of refs.particles) {
      part.x += part.vx * (dt / 1000);
      part.y += part.vy * (dt / 1000);
      part.tw += dt / 1000;
      const { cw, ch } = refs.geom;
      if (part.y < -4) {
        part.y = ch + 4;
        part.x = Math.random() * cw;
      }
      if (part.x < -4) part.x = cw + 4;
      if (part.x > cw + 4) part.x = -4;
    }
    for (const side of ["red", "blue"]) {
      const typed = refs.typed[side];
      if (typed && typed.chars !== Infinity) typed.chars += dt / 1000 * 90;
    }
    for (const spark of refs.sparks) {
      spark.x += spark.vx * (dt / 1000);
      spark.y += spark.vy * (dt / 1000);
      spark.vy += 260 * (dt / 1000);
      spark.life -= dt;
    }
    if (refs.sparks.length > 0) refs.sparks = refs.sparks.filter((s) => s.life > 0);
    if (refs.fx.length > 0) refs.fx = refs.fx.filter((f) => refs.clock - f.t0 < f.dur + 200);
    drawStage(refs);
  };
  refs.raf = frames(tick);
}

// ---------- painters ----------
const INK = "#0a0c10";
const BONE = "#f2efe8";
const RED = "#ff3b2f";
const BLUE = "#2f7bff";
const CYAN = "#7fd4ff";
const GOLD = "#ffc83d";
const MONO = "'JetBrains Mono',ui-monospace,Menlo,monospace";
const DISP = "'Big Shoulders Display',Impact,'Arial Narrow',sans-serif";

function roundRectPath(ctx, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

function hostScreen(refs, id) {
  const node = refs.nodes[id];
  if (!node) return null;
  const at = applyCamera(refs.stage, refs.cam, node.x, node.y);
  return { x: at.x, y: at.y, r: node.r * refs.stage.view.scale * refs.cam.z, kind: node.kind || "standard" };
}

// Clash orb anchor on its rail, or null on narrow stages.
function orbPoint(refs) {
  const stage = refs.stage;
  if (!stage || stage.mode !== "side") return null;
  const top = stage.chrome.hudBottom + stage.gap;
  return {
    x: clashOrbX(stage.band.x0, stage.band.x1, refs.tug),
    y: top + ORB_RAIL / 2,
  };
}

function spawnSparks(refs, x, y, color, count) {
  for (let i = 0; i < count; i += 1) {
    const a = Math.random() * 6.283;
    const speed = 60 + Math.random() * 220;
    refs.sparks.push({
      x, y,
      vx: Math.cos(a) * speed,
      vy: Math.sin(a) * speed - 40,
      life: 420 + Math.random() * 380,
      color,
    });
  }
  if (refs.sparks.length > 220) refs.sparks.splice(0, refs.sparks.length - 220);
}

function drawStage(refs) {
  const ctx = refs.ctx;
  if (!ctx || !refs.stage) return;
  ensureFrame(refs);
  buildStaticLayers(refs);
  buildSprites(refs);
  const target = refs.frameCtx || ctx;
  const direct = target === ctx;
  try {
    paintFrame(refs, target);
  } catch (err) {
    // Blank-frame guard: a failed paint never reaches the screen,
    // so the last good frame stays up instead of a cleared canvas.
    notePaintError(refs, err);
    return;
  }
  if (!direct) {
    try {
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.drawImage(refs.frame, 0, 0);
      ctx.restore();
    } catch (err) {
      notePaintError(refs, err);
      return;
    }
  }
  updateStageDebug(refs, false);
}

function notePaintError(refs, err) {
  refs.blankFrames = (refs.blankFrames || 0) + 1;
  const msg = err && err.message ? err.message : String(err);
  refs.lastError = String(msg).slice(0, 160);
  updateStageDebug(refs, true);
}

function paintFrame(refs, ctx) {
  const { cw, ch, dpr } = refs.geom;
  ctx.save();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cw, ch);
  ctx.fillStyle = INK;
  ctx.fillRect(0, 0, cw, ch);
  const shake = refs.trauma * refs.trauma;
  const t = refs.clock / 1000;
  const sx = shake * 12 * (Math.sin(t * 91) * 0.6 + Math.sin(t * 47) * 0.4);
  const sy = shake * 10 * (Math.cos(t * 83) * 0.6 + Math.cos(t * 59) * 0.4);
  ctx.translate(sx, sy);
  paintBackdrop(refs, ctx);
  paintBeams(refs, ctx);
  paintFloor(refs, ctx);
  paintLinks(refs, ctx);
  paintHosts(refs, ctx);
  paintEntities(refs, ctx);
  paintClash(refs, ctx);
  paintFx(refs, ctx);
  paintLanes(refs, ctx);
  paintParticles(refs, ctx);
  ctx.translate(-sx, -sy);
  paintOverlays(refs, ctx);
  ctx.restore();
}

function paintBackdrop(refs, ctx) {
  const { cw, ch } = refs.geom;
  const far = refs.cache && refs.cache.far;
  if (!far) {
    paintFarInto(refs, ctx, cw, ch);
    return;
  }
  const drift = refs.cam.dx * 0.05 + Math.sin(refs.clock / 9000) * 6;
  const ox = Math.max(-LAYER_M, Math.min(LAYER_M, drift));
  ctx.drawImage(far, -LAYER_M + ox, -LAYER_M, cw + LAYER_M * 2, ch + LAYER_M * 2);
}

function paintBeams(refs, ctx) {
  const stage = refs.stage;
  const { cw, ch } = refs.geom;
  const t = refs.clock / 1000;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  const bandW = stage.band.x1 - stage.band.x0;
  for (let i = 0; i < 3; i += 1) {
    const fx = stage.band.x0 + bandW * (0.22 + i * 0.28);
    const sway = Math.sin(t * 0.24 + i * 2.1) * 24;
    const topW = 40 + i * 14;
    const grad = ctx.createLinearGradient(0, 0, 0, ch);
    grad.addColorStop(0, "rgba(127,212,255,0.10)");
    grad.addColorStop(1, "rgba(127,212,255,0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.moveTo(fx - topW + sway, -20);
    ctx.lineTo(fx + topW + sway, -20);
    ctx.lineTo(fx + topW * 3.2 + sway * 2, ch + 20);
    ctx.lineTo(fx - topW * 3.2 + sway * 2, ch + 20);
    ctx.closePath();
    ctx.fill();
  }
  void cw;
  ctx.restore();
}

function paintFloor(refs, ctx) {
  const { cw, ch } = refs.geom;
  const grid = refs.cache && refs.cache.grid;
  if (!grid) {
    paintGridInto(refs, ctx, cw, ch);
    return;
  }
  const ox = Math.max(-LAYER_M, Math.min(LAYER_M, refs.cam.dx * 0.3));
  ctx.drawImage(grid, -LAYER_M + ox, -LAYER_M, cw + LAYER_M * 2, ch + LAYER_M * 2);
}

function paintLinks(refs, ctx) {
  const edges = refs.stage.edges || [];
  if (edges.length === 0) return;
  const t = refs.clock / 1000;
  ctx.save();
  ctx.lineCap = "round";
  for (const [a, b] of edges) {
    const p = hostScreen(refs, a);
    const q = hostScreen(refs, b);
    if (!p || !q) continue;
    const sa = refs.states[a] || "clean";
    const sb = refs.states[b] || "clean";
    const severed = sa === "isolated" || sb === "isolated";
    const hot = sa === "compromised" || sb === "compromised" ||
      sa === "signed-in" || sb === "signed-in";
    const mx = (p.x + q.x) / 2;
    const my = (p.y + q.y) / 2 - Math.abs(q.x - p.x) * 0.08;
    ctx.strokeStyle = severed ? "rgba(91,102,120,0.5)" : hot ? "rgba(255,90,80,0.55)" : "rgba(120,150,200,0.4)";
    ctx.lineWidth = severed ? 2 : 3;
    ctx.setLineDash(severed ? [6, 7] : []);
    ctx.beginPath();
    if (severed) {
      // Break the conduit short of the sealed host.
      const cut = sa === "isolated" ? 0.32 : 0.68;
      ctx.moveTo(p.x, p.y);
      ctx.quadraticCurveTo(mx, my, p.x + (q.x - p.x) * cut, p.y + (q.y - p.y) * cut);
    } else {
      ctx.moveTo(p.x, p.y);
      ctx.quadraticCurveTo(mx, my, q.x, q.y);
    }
    ctx.stroke();
    if (!severed) {
      const flow = (t * 0.35) % 1;
      const fx = (1 - flow) * (1 - flow) * p.x + 2 * (1 - flow) * flow * mx + flow * flow * q.x;
      const fy = (1 - flow) * (1 - flow) * p.y + 2 * (1 - flow) * flow * my + flow * flow * q.y;
      ctx.fillStyle = hot ? "rgba(255,110,100,0.9)" : "rgba(150,190,255,0.75)";
      ctx.beginPath();
      ctx.arc(fx, fy, 2.2, 0, 6.283);
      ctx.fill();
    }
  }
  ctx.restore();
}

function paintHosts(refs, ctx) {
  const t = refs.clock / 1000;
  for (const id of refs.hosts) {
    const node = refs.nodes[id];
    const at = hostScreen(refs, id);
    if (!node || !at) continue;
    const state = refs.states[id] || "clean";
    const jewel = id === refs.jewel;
    const exposed = id === refs.exposed;
    const r = Math.max(3, at.r);
    // Grounding shadow plus state-tinted floor reflection.
    ctx.fillStyle = "rgba(2,4,8,0.55)";
    ctx.beginPath();
    ctx.ellipse(at.x, at.y + r * 1.35, r * 1.25, r * 0.34, 0, 0, 6.283);
    ctx.fill();
    const glowColor = state === "compromised" ? "255,59,47"
      : state === "isolated" ? "47,123,255"
      : state === "probed" ? "232,236,244"
      : state === "signed-in" ? "199,154,59" : "127,212,255";
    const mirror = ctx.createRadialGradient(at.x, at.y + r * 1.5, 2, at.x, at.y + r * 1.5, r * 1.6);
    mirror.addColorStop(0, `rgba(${glowColor},0.30)`);
    mirror.addColorStop(1, `rgba(${glowColor},0)`);
    ctx.fillStyle = mirror;
    ctx.beginPath();
    ctx.ellipse(at.x, at.y + r * 1.5, r * 1.6, r * 0.5, 0, 0, 6.283);
    ctx.fill();
    if (node.kind === "vault") {
      paintVault(ctx, refs.sprites || {}, at.x, at.y, r, t, state, exposed);
    } else {
      paintBlock(ctx, refs.sprites || {}, at.x, at.y, r, t, state, node.kind === "small");
    }
    if (state === "isolated") {
      ctx.save();
      ctx.strokeStyle = BLUE;
      ctx.lineWidth = Math.max(2, r * 0.1);
      ctx.globalAlpha = 0.9;
      ctx.beginPath();
      ctx.arc(at.x, at.y, r + 7, 0, 6.283);
      ctx.stroke();
      ctx.globalAlpha = 0.35;
      ctx.beginPath();
      ctx.arc(at.x, at.y, r + 12 + Math.sin(t * 2.4) * 2, 0, 6.283);
      ctx.stroke();
      ctx.restore();
    }
    if (state === "restored") {
      const phase = (t * 0.7) % 1;
      ctx.save();
      ctx.strokeStyle = CYAN;
      ctx.globalAlpha = 0.7 * (1 - phase);
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(at.x, at.y, r + 6 + phase * 22, 0, 6.283);
      ctx.stroke();
      ctx.restore();
    }
    if (refs.flash && refs.flash.host === id && refs.clock < refs.flash.until) {
      const left = (refs.flash.until - refs.clock) / (refs.flash.first ? 1600 : 900);
      ctx.save();
      ctx.strokeStyle = refs.flash.first ? RED : "#ffffff";
      ctx.globalAlpha = Math.min(1, left + 0.2);
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(at.x, at.y, r + 10 + (1 - left) * 26, 0, 6.283);
      ctx.stroke();
      if (refs.flash.first) {
        ctx.font = `900 ${Math.round(Math.min(30, Math.max(14, r * 0.5)))}px ${DISP}`;
        ctx.textAlign = "center";
        ctx.fillStyle = RED;
        ctx.fillText("FIRST ACCESS", at.x, at.y - r - 18);
      }
      ctx.restore();
    }
    paintHostLabel(refs, ctx, id, at.x, at.y, r, jewel);
  }
}

function paintBlock(ctx, sprites, x, y, r, t, state, small) {
  const s = r * 2;
  const breached = state === "compromised" || state === "signed-in";
  ctx.save();
  if (breached) {
    drawGlow(ctx, sprites.red, x, y, r * 2, 0.65);
  }
  ctx.fillStyle = state === "compromised" ? "#2a0f0c" : state === "isolated" ? "#111722" : "#161c28";
  roundRectPath(ctx, x - r, y - r, s, s, small ? 5 : 9);
  ctx.fill();
  // First touch reads white-hot; gold stays reserved for the crown jewel.
  const edge = state === "compromised" ? RED
    : state === "signed-in" ? "#ff7a5c"
      : state === "probed" ? BONE
        : state === "isolated" ? "#4a5872" : "#8fa3c4";
  ctx.lineWidth = Math.max(1.5, r * 0.09);
  if (state === "compromised") {
    // Corrupted edges: the outline stutters in segments.
    ctx.strokeStyle = edge;
    const j = (seed) => Math.sin(t * 13 + seed * 2.4) * Math.max(1, r * 0.06);
    ctx.beginPath();
    const c = [[-1, -1], [1, -1], [1, 1], [-1, 1]];
    for (let i = 0; i < 4; i += 1) {
      const [x0, y0] = c[i];
      const [x1, y1] = c[(i + 1) % 4];
      ctx.moveTo(x + x0 * r + j(i), y + y0 * r + j(i + 9));
      ctx.lineTo(x + x1 * r + j(i + 4), y + y1 * r + j(i + 13));
    }
    ctx.stroke();
    ctx.strokeStyle = "rgba(255,59,47,0.35)";
    ctx.lineWidth = 1;
    roundRectPath(ctx, x - r - 4, y - r - 4, s + 8, s + 8, 10);
    ctx.stroke();
  } else {
    ctx.strokeStyle = edge;
    roundRectPath(ctx, x - r, y - r, s, s, small ? 5 : 9);
    ctx.stroke();
  }
  if (state === "isolated") {
    ctx.strokeStyle = "rgba(120,140,175,0.5)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x - r * 0.55, y - r * 0.55);
    ctx.lineTo(x + r * 0.55, y + r * 0.55);
    ctx.moveTo(x + r * 0.55, y - r * 0.55);
    ctx.lineTo(x - r * 0.55, y + r * 0.55);
    ctx.stroke();
  }
  ctx.restore();
}

function paintVault(ctx, sprites, x, y, r, t, state, exposed) {
  const breached = state === "compromised" || state === "signed-in" || exposed;
  ctx.save();
  if (exposed || state === "compromised") {
    drawGlow(ctx, sprites.red, x, y, r * 2.1, 0.73);
  } else {
    drawGlow(ctx, sprites.gold, x, y, r * 2.1, 0.62);
  }
  ctx.fillStyle = "#141824";
  ctx.beginPath();
  ctx.arc(x, y, r, 0, 6.283);
  ctx.fill();
  ctx.lineWidth = Math.max(2, r * 0.12);
  ctx.strokeStyle = exposed ? RED : breached ? "#ff7a5c" : GOLD;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, 6.283);
  ctx.stroke();
  ctx.strokeStyle = "rgba(160,190,235,0.55)";
  ctx.lineWidth = Math.max(1.5, r * 0.07);
  for (let k = 0; k < 3; k += 1) {
    const a0 = t * (k % 2 === 0 ? 0.5 : -0.35) + (k * 2.1);
    ctx.beginPath();
    ctx.arc(x, y, r * (0.68 - k * 0.14), a0, a0 + 4.2);
    ctx.stroke();
  }
  ctx.fillStyle = exposed ? RED : GOLD;
  ctx.beginPath();
  ctx.arc(x, y, Math.max(2, r * 0.1), 0, 6.283);
  ctx.fill();
  if (exposed) {
    ctx.strokeStyle = "#05070c";
    ctx.lineWidth = Math.max(2, r * 0.08);
    ctx.beginPath();
    ctx.moveTo(x - r * 0.5, y - r * 0.75);
    ctx.lineTo(x + r * 0.15, y - r * 0.1);
    ctx.lineTo(x - r * 0.25, y + r * 0.65);
    ctx.stroke();
  }
  ctx.restore();
}

function paintHostLabel(refs, ctx, id, x, y, r, jewel) {
  // Tiny stages keep state-colored blocks and skip letters that
  // would collide; the lanes still name every host in text.
  if (r < 14) return;
  const label = (refs.labels && refs.labels[id]) || String(id).toUpperCase().slice(0, 7);
  const role = (refs.roles && refs.roles[id]) || "";
  const fs = Math.round(Math.min(24, Math.max(9, r * 0.5)));
  ctx.save();
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `900 ${fs}px ${DISP}`;
  ctx.lineWidth = Math.max(2, fs * 0.16);
  ctx.strokeStyle = "rgba(5,7,12,0.9)";
  ctx.strokeText(label, x, y + 1);
  ctx.fillStyle = BONE;
  ctx.fillText(label, x, y + 1);
  if (r >= 22) {
    const sub = jewel ? "PROTECTED" : roleSub(role, false).toUpperCase();
    ctx.font = `700 ${Math.round(Math.min(12, Math.max(8, fs * 0.42)))}px ${MONO}`;
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(5,7,12,0.9)";
    ctx.strokeText(sub, x, y + r + 15);
    ctx.fillStyle = jewel ? GOLD : "#a8b4c8";
    ctx.fillText(sub, x, y + r + 15);
  }
  ctx.restore();
}

function paintEntities(refs, ctx) {
  const slots = refs.stage.entities;
  const t = refs.clock / 1000;
  const sprites = refs.sprites || {};
  const art = refs.art || {};
  const red = slots.intrusion;
  const blue = slots.guardian;
  const redHot = refs.clock < refs.surge.red ? (refs.surge.red - refs.clock) / 900 : 0;
  const blueHot = refs.clock < refs.surge.blue ? (refs.surge.blue - refs.clock) / 900 : 0;
  const level = Math.min(1, refs.intrusion + redHot * 0.25);
  const energy = Math.min(1.15, refs.guardian + blueHot * 0.35);
  paintIntrusion(ctx, sprites, art, red.x, red.y,
    red.r * (0.68 + 0.42 * refs.intrusion), level, t);
  paintGuardian(ctx, sprites, art, blue.x, blue.y, blue.r, energy, t);
}

// The Intrusion: a hot white core wrapped in layered jagged crimson
// shards turning at different speeds, glitch slices, and orbiting
// code glyphs. Flicker turns violent as red progresses. Abstract
// light and code only; the splash art lights the inside additively.
export function paintIntrusion(ctx, sprites, art, x, y, r, level, t) {
  const rr = Math.max(8, r);
  const lvl = Math.max(0, Math.min(1, level));
  const violence = 0.25 + lvl * 1.6;
  const step = Math.floor(t * 24);
  const rnd = (n) => {
    const s = Math.sin(step * 12.9898 + n * 78.233) * 43758.5453;
    return s - Math.floor(s);
  };
  const jx = (rnd(1) - 0.5) * rr * 0.08 * violence;
  const jy = (rnd(2) - 0.5) * rr * 0.08 * violence;
  ctx.save();
  ctx.translate(x + jx, y + Math.sin(t * 1.1) * rr * 0.04 + jy);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  drawGlow(ctx, (sprites || {}).intrusion, 0, 0, rr * 2.4, 0.45 + lvl * 0.55);
  ctx.restore();
  const tex = art && art.intrusionTex;
  if (tex && tex.width) {
    ctx.save();
    ctx.beginPath();
    ctx.arc(0, 0, rr * 1.3, 0, 6.283);
    ctx.clip();
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = 0.4 + lvl * 0.3;
    ctx.rotate(Math.sin(t * 0.18) * 0.2 + t * 0.03);
    const ts = rr * 2.6;
    ctx.drawImage(tex, -ts / 2, -ts / 2, ts, ts);
    ctx.restore();
  }
  // Dark jagged body: the silhouette anchor under the shards.
  ctx.save();
  ctx.rotate(t * 0.12);
  ctx.fillStyle = "rgba(24,6,7,0.95)";
  ctx.strokeStyle = lvl > 0.66 ? "#ff7a5c" : "#e03228";
  ctx.lineWidth = Math.max(1.5, rr * 0.035);
  ctx.beginPath();
  const teeth = 9;
  for (let i = 0; i < teeth; i += 1) {
    const a = (i * 6.283) / teeth;
    const rad = rr * (i % 2 === 0 ? 0.98 : 0.72);
    const px = Math.cos(a) * rad;
    const py = Math.sin(a) * rad;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.restore();
  // Radiating spikes: thin teeth that sell the silhouette.
  const spikes = 6 + Math.round(lvl * 3);
  for (let i = 0; i < spikes; i += 1) {
    const a = t * 0.35 + (i * 6.283) / spikes;
    const len = rr * (0.35 + 0.3 * Math.abs(Math.sin(t * 3.1 + i * 2.4)) + lvl * 0.25);
    const base = rr * 0.85;
    const tip = base + len;
    const wdt = rr * 0.07;
    const dx = Math.cos(a);
    const dy = Math.sin(a);
    const nx = -dy;
    const ny = dx;
    ctx.fillStyle = "rgba(255,59,47,0.85)";
    ctx.beginPath();
    ctx.moveTo(dx * base + nx * wdt, dy * base + ny * wdt);
    ctx.lineTo(dx * tip, dy * tip);
    ctx.lineTo(dx * base - nx * wdt, dy * base - ny * wdt);
    ctx.closePath();
    ctx.fill();
  }
  // Layered shard rings, each turning at its own speed.
  const rings = [
    { n: 8, rad: 1.12, size: 0.2, speed: 0.55, wob: 0.1 },
    { n: 6, rad: 0.78, size: 0.18, speed: -0.38, wob: 0.14 },
    { n: 5, rad: 0.5, size: 0.16, speed: 0.9, wob: 0.08 },
  ];
  const extra = Math.round(lvl * 3);
  rings.forEach((ring, ri) => {
    const n = ring.n + (ri === 0 ? extra : 0);
    for (let i = 0; i < n; i += 1) {
      const a = t * ring.speed + (i * 6.283) / n + ri * 1.3;
      const dist = rr * ring.rad * (1 + Math.sin(t * 2.1 + i * 1.7 + ri) * ring.wob);
      const s = rr * ring.size * (0.8 + lvl * 0.5) * (0.7 + 0.6 * Math.abs(Math.sin(i * 2.39 + ri * 1.1)));
      ctx.save();
      ctx.translate(Math.cos(a) * dist, Math.sin(a) * dist * 0.92);
      ctx.rotate(a * 2 + i * 0.7 + t * (0.6 + ri * 0.3));
      ctx.fillStyle = "rgba(46,10,10,0.92)";
      ctx.strokeStyle = lvl > 0.66 ? "#ff7a5c" : "#ff3b2f";
      ctx.lineWidth = Math.max(1, rr * 0.02);
      ctx.beginPath();
      ctx.moveTo(0, -s * 2.1);
      ctx.lineTo(s * 0.5, s * 0.1);
      ctx.lineTo(0, s * 0.9);
      ctx.lineTo(-s * 0.5, s * 0.1);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.restore();
    }
  });
  // White-hot core with violent flicker.
  const flick = 1 + (rnd(3) - 0.5) * 0.5 * violence + Math.sin(t * 47) * 0.06 * violence;
  const coreR = Math.max(2, rr * 0.3 * flick);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  drawGlow(ctx, (sprites || {}).white, 0, 0, coreR * 3.2, 0.5 + lvl * 0.4);
  ctx.fillStyle = "#fff6f2";
  ctx.beginPath();
  ctx.arc(0, 0, coreR, 0, 6.283);
  ctx.fill();
  ctx.restore();
  // Horizontal glitch-slice displacement across the body.
  const slices = 2 + Math.round(lvl * 3);
  for (let i = 0; i < slices; i += 1) {
    const gy = -rr * 1.2 + ((i + rnd(10 + i) * 0.6) / slices) * rr * 2.4;
    const gh = Math.max(1, rr * (0.02 + rnd(20 + i) * 0.03));
    const off = (rnd(30 + i) - 0.5) * rr * 0.5 * (0.3 + lvl);
    ctx.fillStyle = `rgba(255,70,60,${(0.25 + lvl * 0.4).toFixed(2)})`;
    ctx.fillRect(-rr * 1.5 + off, gy, rr * 3, gh);
    ctx.fillStyle = "rgba(127,212,255,0.18)";
    ctx.fillRect(-rr * 1.5 + off * 0.5, gy + gh, rr * 3, 1);
  }
  // Orbiting code glyphs.
  const glyphs = "01<>/\\{}=+*#$";
  const count = 5 + Math.round(lvl * 4);
  ctx.save();
  ctx.font = `${Math.max(8, Math.round(rr * 0.2))}px ${MONO}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (let i = 0; i < count; i += 1) {
    const a = t * (0.5 + lvl * 0.9) + (i * 6.283) / count;
    const dist = rr * (1.35 + 0.25 * Math.sin(t * 1.3 + i * 2.2));
    ctx.fillStyle = `rgba(255,110,100,${(0.35 + 0.45 * Math.abs(Math.sin(t * 3 + i))).toFixed(2)})`;
    ctx.fillText(glyphs[(i * 3 + step) % glyphs.length], Math.cos(a) * dist, Math.sin(a) * dist);
  }
  ctx.restore();
  ctx.restore();
}

// The Guardian: a bright cyan-white core inside segmented rings
// turning in opposite directions, with orbiting shield plates and a
// calm steady pulse that brightens on blue actions. Abstract light
// and code only; the splash art lights the inside additively.
export function paintGuardian(ctx, sprites, art, x, y, r, energy, t) {
  const rr = Math.max(8, r);
  const e = Math.max(0.15, Math.min(1.2, energy));
  const breathe = 1 + Math.sin(t * 1.4) * 0.02;
  const sp = sprites || {};
  ctx.save();
  ctx.translate(x, y + Math.sin(t * 0.9) * rr * 0.03);
  ctx.scale(breathe, breathe);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  drawGlow(ctx, sp.guardian, 0, 0, rr * 2.4, 0.4 + e * 0.55);
  ctx.restore();
  const tex = art && art.guardianTex;
  if (tex && tex.width) {
    ctx.save();
    ctx.beginPath();
    ctx.arc(0, 0, rr * 1.3, 0, 6.283);
    ctx.clip();
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = 0.22 + e * 0.2;
    ctx.rotate(t * 0.05);
    const ts = rr * 2.4;
    ctx.drawImage(tex, -ts / 2, -ts / 2, ts, ts);
    ctx.restore();
  }
  // Segmented rings turning in opposite directions.
  const rings = [
    { rad: 1.0, segs: 4, gap: 0.32, speed: 0.3, w: 0.055, a: 0.75 },
    { rad: 0.78, segs: 6, gap: 0.2, speed: -0.22, w: 0.04, a: 0.6 },
    { rad: 0.56, segs: 3, gap: 0.5, speed: 0.16, w: 0.03, a: 0.5 },
  ];
  for (const ring of rings) {
    ctx.save();
    ctx.rotate(t * ring.speed);
    ctx.strokeStyle = `rgba(160,220,255,${Math.min(1, ring.a + e * 0.3).toFixed(2)})`;
    ctx.lineWidth = Math.max(1.5, rr * ring.w);
    const full = 6.283 / ring.segs;
    for (let s = 0; s < ring.segs; s += 1) {
      ctx.beginPath();
      ctx.arc(0, 0, rr * ring.rad, s * full + ring.gap / 2, (s + 1) * full - ring.gap / 2);
      ctx.stroke();
    }
    ctx.restore();
  }
  // Hex shield plates orbit the core.
  for (let i = 0; i < 6; i += 1) {
    const a = -t * 0.4 + (i * 6.283) / 6;
    const px = Math.cos(a) * rr * 0.78;
    const py = Math.sin(a) * rr * 0.78;
    ctx.save();
    ctx.translate(px, py);
    ctx.rotate(a);
    ctx.fillStyle = "rgba(30,60,110,0.9)";
    ctx.strokeStyle = `rgba(160,220,255,${(0.4 + e * 0.5).toFixed(2)})`;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let k = 0; k < 6; k += 1) {
      const ha = (k * 6.283) / 6;
      const hx = Math.cos(ha) * rr * 0.15;
      const hy = Math.sin(ha) * rr * 0.15;
      if (k === 0) ctx.moveTo(hx, hy);
      else ctx.lineTo(hx, hy);
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }
  // Bright core: calm steady pulse, brighter on blue energy.
  const pulse = 0.5 + 0.5 * Math.sin(t * 1.4);
  const coreR = Math.max(2, rr * (0.24 + e * 0.1 + pulse * 0.02));
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  drawGlow(ctx, sp.guardianCore, 0, 0, rr * 0.55, 0.5 + e * 0.5);
  ctx.fillStyle = "#f2fbff";
  ctx.beginPath();
  ctx.arc(0, 0, coreR, 0, 6.283);
  ctx.fill();
  ctx.restore();
  if (e > 0.4) {
    const phase = (t * 0.9) % 1;
    ctx.save();
    ctx.strokeStyle = CYAN;
    ctx.globalAlpha = (1 - phase) * Math.min(1, e);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(0, 0, rr * (0.4 + phase * 1.1), 0, 6.283);
    ctx.stroke();
    ctx.restore();
  }
  ctx.restore();
}

// The clash orb rides its rail between the entities: red pressure
// pushes it toward the Guardian, containment pushes it back. A beam
// strand from each entity feeds it; sparks and rings fire on events.
function paintClash(refs, ctx) {
  const orb = orbPoint(refs);
  if (!orb) return;
  const slots = refs.stage.entities;
  const t = refs.clock / 1000;
  const tug = Math.max(-1, Math.min(1, refs.tug || 0));
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  const strands = [
    { from: { x: slots.intrusion.x + slots.intrusion.r * 0.9, y: slots.intrusion.y }, color: "255,70,60" },
    { from: { x: slots.guardian.x - slots.guardian.r * 0.9, y: slots.guardian.y }, color: "90,170,255" },
  ];
  for (const [i, s] of strands.entries()) {
    const wob = Math.sin(t * 6 + i * 2.4) * 10;
    const mx = (s.from.x + orb.x) / 2;
    const my = (s.from.y + orb.y) / 2 + wob - 30;
    ctx.strokeStyle = `rgba(${s.color},0.5)`;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(s.from.x, s.from.y);
    ctx.quadraticCurveTo(mx, my, orb.x, orb.y);
    ctx.stroke();
    ctx.strokeStyle = `rgba(${s.color},0.2)`;
    ctx.lineWidth = 6;
    ctx.stroke();
  }
  ctx.restore();
  const hot = tug > 0.15 ? "red" : tug < -0.15 ? "blue" : null;
  const sprites = refs.sprites || {};
  drawGlow(ctx, sprites.white, orb.x, orb.y, 26, 0.7);
  if (hot) drawGlow(ctx, hot === "red" ? sprites.red : sprites.blue, orb.x, orb.y, 20, 0.8);
  ctx.save();
  ctx.fillStyle = hot === "red" ? "#ffd9d4" : hot === "blue" ? "#d9e9ff" : "#f2f4fa";
  ctx.beginPath();
  ctx.arc(orb.x, orb.y, 8 + Math.sin(t * 9) * 1.5, 0, 6.283);
  ctx.fill();
  // Crackle arcs around the orb.
  ctx.strokeStyle = hot === "blue" ? "rgba(127,212,255,0.8)" : "rgba(255,110,100,0.8)";
  ctx.lineWidth = 1.5;
  for (let i = 0; i < 3; i += 1) {
    const a0 = t * (2 + i) + i * 2.1;
    ctx.beginPath();
    ctx.arc(orb.x, orb.y, 12 + ((i * 5 + t * 7) % 8), a0, a0 + 1.4);
    ctx.stroke();
  }
  ctx.restore();
}

// Transient event effects: rings, sparks, beams, callouts. Entries
// resolve host positions at paint time so the camera carries them.
function paintFx(refs, ctx) {
  for (const fx of refs.fx) {
    const age = refs.clock - fx.t0;
    if (age < 0 || age > fx.dur + 200) continue;
    const p = Math.max(0, Math.min(1, age / fx.dur));
    switch (fx.kind) {
      case "ring":
        paintFxRing(ctx, fx, age, fxPoint(refs, fx));
        break;
      case "fan":
        paintFxFan(refs, ctx, fx, p);
        break;
      case "needle":
        paintFxNeedle(refs, ctx, fx, p);
        break;
      case "keys":
        paintFxKeys(refs, ctx, fx, p);
        break;
      case "pierce":
        paintFxPierce(refs, ctx, fx, p, false);
        break;
      case "grab":
        paintFxPierce(refs, ctx, fx, p, true);
        break;
      case "tendril":
        paintFxTendril(refs, ctx, fx, p);
        break;
      case "siphon":
        paintFxSiphon(refs, ctx, fx, p);
        break;
      case "fizzle":
        paintFxFizzle(refs, ctx, fx, p);
        break;
      default:
        break;
    }
  }
  if (refs.sparks.length > 0) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.lineCap = "round";
    for (const s of refs.sparks) {
      const a = Math.max(0, Math.min(1, s.life / 500));
      ctx.strokeStyle = s.color === "red" ? `rgba(255,120,100,${a.toFixed(2)})`
        : s.color === "blue" ? `rgba(140,200,255,${a.toFixed(2)})`
          : `rgba(240,240,245,${a.toFixed(2)})`;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(s.x - s.vx * 0.03, s.y - s.vy * 0.03);
      ctx.stroke();
    }
    ctx.restore();
  }
}

function fxPoint(refs, fx) {
  if (typeof fx.x === "number" && typeof fx.y === "number") return { x: fx.x, y: fx.y, r: 0 };
  if (fx.host) {
    const at = hostScreen(refs, fx.host);
    if (at) return at;
  }
  return null;
}

function entityCore(refs, side) {
  const slots = refs.stage.entities;
  const slot = side === "blue" ? slots.guardian : slots.intrusion;
  return { x: slot.x, y: slot.y, r: slot.r };
}

function beamStroke(ctx, x0, y0, x1, y1, color, width, alpha) {
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  ctx.strokeStyle = `rgba(${color},${(alpha * 0.35).toFixed(2)})`;
  ctx.lineWidth = width * 3;
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.stroke();
  ctx.strokeStyle = `rgba(${color},${alpha.toFixed(2)})`;
  ctx.lineWidth = width;
  ctx.stroke();
  ctx.restore();
}

function paintFxRing(ctx, fx, age, pt) {
  if (!pt) return;
  const p = Math.max(0, Math.min(1, age / fx.dur));
  const r = (fx.r0 || 8) + ((fx.r1 || 64) - (fx.r0 || 8)) * (1 - (1 - p) * (1 - p));
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.strokeStyle = `rgba(${fx.color || "255,255,255"},${(0.85 * (1 - p)).toFixed(2)})`;
  ctx.lineWidth = 3 * (1 - p) + 1;
  ctx.beginPath();
  ctx.arc(pt.x, pt.y, r, 0, 6.283);
  ctx.stroke();
  ctx.restore();
}

// Scan: a fan of rays sweeps from the Intrusion across the target.
function paintFxFan(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  const from = entityCore(refs, "red");
  const base = Math.atan2(at.y - from.y, at.x - from.x);
  const dist = Math.hypot(at.x - from.x, at.y - from.y);
  const sweep = Math.sin(p * Math.PI) * 0.5;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (let i = -2; i <= 2; i += 1) {
    const a = base + i * 0.12 + sweep * 0.4;
    const len = dist * Math.min(1, p * 1.6);
    ctx.strokeStyle = `rgba(255,90,80,${(0.5 * (1 - p) * (1 - Math.abs(i) / 3)).toFixed(2)})`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(from.x + Math.cos(a) * len, from.y + Math.sin(a) * len);
    ctx.stroke();
  }
  ctx.restore();
  if (p > 0.65) {
    paintFxRing(ctx, { ...fx, r0: at.r + 4, r1: at.r + 34, color: "255,90,80" },
      (p - 0.65) * fx.dur, at);
  }
}

// Probe: thin flickering needle beams.
function paintFxNeedle(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  const from = entityCore(refs, "red");
  const flick = 0.6 + 0.4 * Math.sin(p * 40);
  for (const off of [-5, 0, 5]) {
    const dx = at.x - from.x;
    const dy = at.y - from.y;
    const len = Math.hypot(dx, dy) || 1;
    beamStroke(ctx, from.x - (dy / len) * off, from.y + (dx / len) * off,
      from.x + dx * Math.min(1, p * 1.4) - (dy / len) * off,
      from.y + dy * Math.min(1, p * 1.4) + (dx / len) * off,
      "255,120,100", 1.5, flick * (1 - p * 0.5));
  }
  if (p > 0.5) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.fillStyle = `rgba(255,160,140,${((p - 0.5) * 1.6).toFixed(2)})`;
    ctx.beginPath();
    ctx.arc(at.x, at.y, 4 + (p - 0.5) * 10, 0, 6.283);
    ctx.fill();
    ctx.restore();
  }
}

// Credential guess: key shards fly out and bounce off.
function paintFxKeys(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  const from = entityCore(refs, "red");
  const leg = p < 0.5 ? p * 2 : (1 - p) * 2;
  for (let i = 0; i < 3; i += 1) {
    const lag = i * 0.12;
    const q = Math.max(0, Math.min(1, leg - lag));
    const x = from.x + (at.x - from.x) * q;
    const y = from.y + (at.y - from.y) * q - Math.sin(q * Math.PI) * 26;
    const s = 7;
    const a = q * 9 + i;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(a);
    ctx.fillStyle = `rgba(255,150,130,${(0.9 * (1 - p * 0.4)).toFixed(2)})`;
    ctx.beginPath();
    ctx.moveTo(0, -s);
    ctx.lineTo(s * 0.7, s * 0.6);
    ctx.lineTo(-s * 0.7, s * 0.6);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
  if (p > 0.35 && p < 0.8) {
    ctx.save();
    ctx.globalAlpha = (0.9 * (1 - (p - 0.35) / 0.45)).toFixed(2);
    ctx.strokeStyle = "#ff5a4a";
    ctx.lineWidth = 3;
    const r = at.r * 0.4;
    ctx.beginPath();
    ctx.moveTo(at.x - r, at.y - r);
    ctx.lineTo(at.x + r, at.y + r);
    ctx.moveTo(at.x + r, at.y - r);
    ctx.lineTo(at.x - r, at.y + r);
    ctx.stroke();
    ctx.restore();
  }
}

// Sign-in: pierce the host with a flare. Grab: the jewel snatch,
// brighter and wider.
function paintFxPierce(refs, ctx, fx, p, grab) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  const from = entityCore(refs, "red");
  const grow = Math.min(1, p * 3);
  beamStroke(ctx, from.x, from.y,
    from.x + (at.x - from.x) * grow, from.y + (at.y - from.y) * grow,
    grab ? "255,220,200" : "255,90,80", grab ? 7 : 4, 0.9 * (1 - p * 0.6));
  if (p > 0.3) {
    const q = (p - 0.3) / 0.7;
    const sprites = refs.sprites || {};
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    drawGlow(ctx, grab ? sprites.white : sprites.red, at.x, at.y,
      at.r * (grab ? 2.4 : 1.8) * (0.5 + q), grab ? 0.9 : 0.75 * (1 - q * 0.5));
    ctx.restore();
    paintFxRing(ctx, { ...fx, r0: at.r, r1: at.r + (grab ? 90 : 50), color: "255,110,100" },
      q * fx.dur, at);
  }
}

// Lateral move: a tendril crawls the link from the old foothold.
function paintFxTendril(refs, ctx, fx, p) {
  const a = hostScreen(refs, fx.from);
  const b = hostScreen(refs, fx.host);
  if (!a || !b) return;
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2 - Math.abs(b.x - a.x) * 0.12;
  const head = Math.min(1, p * 1.2);
  const steps = 24;
  const upto = Math.max(2, Math.floor(steps * head));
  const quad = (t0) => ({
    x: (1 - t0) * (1 - t0) * a.x + 2 * (1 - t0) * t0 * mx + t0 * t0 * b.x,
    y: (1 - t0) * (1 - t0) * a.y + 2 * (1 - t0) * t0 * my + t0 * t0 * b.y,
  });
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  ctx.strokeStyle = `rgba(255,70,60,${(0.85 * (1 - p * 0.5)).toFixed(2)})`;
  ctx.lineWidth = 3.5;
  ctx.beginPath();
  for (let i = 0; i <= upto; i += 1) {
    const pt = quad(i / steps);
    if (i === 0) ctx.moveTo(pt.x, pt.y);
    else ctx.lineTo(pt.x, pt.y);
  }
  ctx.stroke();
  const tip = quad(head);
  ctx.fillStyle = "rgba(255,200,190,0.95)";
  ctx.beginPath();
  ctx.arc(tip.x, tip.y, 5, 0, 6.283);
  ctx.fill();
  ctx.restore();
}

// File read: a stream of data pulls back toward the Intrusion.
function paintFxSiphon(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  const from = entityCore(refs, "red");
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (let i = 0; i < 6; i += 1) {
    const q = ((p * 1.4 - i / 6) % 1 + 1) % 1;
    const x = at.x + (from.x - at.x) * q;
    const y = at.y + (from.y - at.y) * q;
    ctx.fillStyle = `rgba(255,150,130,${(0.85 * Math.sin(q * Math.PI)).toFixed(2)})`;
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, 6.283);
    ctx.fill();
  }
  ctx.restore();
}

// Failed actions sputter and die short of the target.
function paintFxFizzle(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  const red = fx.color !== "blue";
  const from = entityCore(refs, red ? "red" : "blue");
  const color = red ? "255,120,100" : "140,200,255";
  const reach = 0.35 + 0.1 * Math.sin(p * 30);
  const hx = from.x + (at.x - from.x) * reach;
  const hy = from.y + (at.y - from.y) * reach;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  for (let i = 0; i < 4; i += 1) {
    const t0 = i / 4;
    const t1 = t0 + 0.14;
    if (Math.sin(p * 47 + i * 9) > -0.2) continue;
    ctx.strokeStyle = `rgba(${color},${(0.7 * (1 - p)).toFixed(2)})`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(from.x + (hx - from.x) * t0, from.y + (hy - from.y) * t0);
    ctx.lineTo(from.x + (hx - from.x) * t1, from.y + (hy - from.y) * t1);
    ctx.stroke();
  }
  ctx.fillStyle = `rgba(150,150,160,${(0.5 * p * (1 - p) * 4 * 0.5).toFixed(2)})`;
  for (let i = 0; i < 3; i += 1) {
    ctx.beginPath();
    ctx.arc(hx + (i - 1) * 8, hy - p * 20, 4 + p * 8, 0, 6.283);
    ctx.fill();
  }
  ctx.restore();
}

const LANE_TONES = {
  red: { cmd: "#f6d9d4", dim: "#a08a86", ok: "#ffb3a6", bad: "#ff5a4a", verdict: GOLD },
  blue: { cmd: "#d6e5fa", dim: "#7f8fb0", ok: "#9fe0b8", bad: "#ff8a7a", verdict: GOLD },
};

function paintLanes(refs, ctx) {
  const lanes = refs.stage.lanes;
  paintLane(refs, ctx, "red", lanes.red);
  paintLane(refs, ctx, "blue", lanes.blue);
}

function paintLane(refs, ctx, side, lane) {
  const accent = side === "red" ? RED : BLUE;
  const slim = refs.stage.mode === "side";
  ctx.save();
  ctx.fillStyle = slim ? "rgba(8,10,15,0.62)" : "rgba(8,10,15,0.86)";
  roundRectPath(ctx, lane.x, lane.y, lane.w, lane.h, 8);
  ctx.fill();
  ctx.strokeStyle = "rgba(120,150,200,0.25)";
  ctx.lineWidth = 1;
  roundRectPath(ctx, lane.x, lane.y, lane.w, lane.h, 8);
  ctx.stroke();
  ctx.fillStyle = accent;
  ctx.globalAlpha = 0.85;
  ctx.fillRect(side === "red" ? lane.x : lane.x + lane.w - 3, lane.y + 8, 3, lane.h - 16);
  ctx.globalAlpha = 1;
  ctx.font = `700 ${slim ? 9 : 10}px ${MONO}`;
  ctx.textAlign = "left";
  ctx.fillStyle = side === "red" ? "#c98a82" : "#7f9cc9";
  const feed = lane.feed;
  ctx.fillText(side === "red" ? "INTRUSION" : "OPS CONSOLE", feed.x, feed.y + 2);
  const tones = LANE_TONES[side];
  const lines = refs.lanes[side] || [];
  const lineH = slim ? 12 : 13;
  const maxLines = Math.max(1, Math.floor((feed.h - 14) / lineH));
  const shown = lines.slice(-maxLines);
  ctx.font = `400 ${slim ? 10 : 11}px ${MONO}`;
  const typed = refs.typed[side] || { chars: Infinity };
  shown.forEach((line, i) => {
    const y = feed.y + 14 + i * lineH;
    let text = line.text;
    if (i === shown.length - 1 && typed.chars !== Infinity) {
      text = text.slice(0, Math.max(0, Math.floor(typed.chars)));
    }
    const maxW = feed.w;
    while (text.length > 4 && ctx.measureText(text).width > maxW) {
      text = `${text.slice(0, -5)}…`;
    }
    ctx.fillStyle = tones[line.tone] || tones.cmd;
    const fresh = (i + 1) / shown.length;
    ctx.globalAlpha = slim ? 0.2 + 0.8 * fresh * fresh : 0.45 + 0.55 * fresh;
    ctx.fillText(text, feed.x, y);
  });
  ctx.globalAlpha = 1;
  if (typed.chars !== Infinity && shown.length > 0) {
    const y = feed.y + 14 + (shown.length - 1) * lineH;
    const last = shown[shown.length - 1].text.slice(0, Math.max(0, Math.floor(typed.chars)));
    const cx = feed.x + ctx.measureText(last).width + 2;
    if (Math.floor(refs.clock / 400) % 2 === 0) {
      ctx.fillStyle = tones.cmd;
      ctx.fillRect(cx, y - 9, 6, 10);
    }
  }
  ctx.restore();
}

function paintParticles(refs, ctx) {
  const t = refs.clock / 1000;
  const step = refs.lowDetail ? 2 : 1;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (let i = 0; i < refs.particles.length; i += step) {
    const part = refs.particles[i];
    const a = 0.25 + 0.35 * Math.abs(Math.sin(part.tw + t));
    ctx.strokeStyle = part.red ? `rgba(255,90,80,${a.toFixed(2)})` : `rgba(150,200,255,${a.toFixed(2)})`;
    ctx.lineWidth = part.r;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(part.x, part.y);
    ctx.lineTo(part.x - part.vx * 0.09, part.y - part.vy * 0.09);
    ctx.stroke();
  }
  ctx.restore();
}

function paintOverlays(refs, ctx) {
  const { cw, ch, dpr } = refs.geom;
  void dpr;
  if (refs.wash === "calm") {
    const wash = ctx.createLinearGradient(0, 0, 0, ch);
    wash.addColorStop(0, "rgba(47,123,255,0.10)");
    wash.addColorStop(1, "rgba(47,123,255,0.02)");
    ctx.fillStyle = wash;
    ctx.fillRect(0, 0, cw, ch);
  } else if (refs.wash === "alert") {
    ctx.save();
    ctx.strokeStyle = "rgba(255,59,47,0.35)";
    ctx.lineWidth = 10;
    ctx.strokeRect(5, 5, cw - 10, ch - 10);
    ctx.restore();
  }
  if (refs.glitchUntil && refs.clock < refs.glitchUntil) {
    // Chromatic slices: offset strips plus red/cyan ghosts.
    const left = (refs.glitchUntil - refs.clock) / 1200;
    ctx.save();
    for (let i = 0; i < 8; i += 1) {
      const gy = Math.abs(Math.sin(refs.clock * 0.02 + i * 3.7)) * ch;
      const gh = 6 + (i % 3) * 8;
      const off = Math.sin(refs.clock * 0.05 + i * 9.1) * 26 * left;
      try {
        ctx.drawImage(refs.canvas, 0, gy * refs.geom.dpr, refs.canvas.width, gh * refs.geom.dpr,
          off, gy, cw, gh);
      } catch {
        break;
      }
      ctx.fillStyle = i % 2 === 0 ? `rgba(255,59,47,${0.08 * left})` : `rgba(127,212,255,${0.08 * left})`;
      ctx.fillRect(0, gy, cw, gh);
    }
    ctx.restore();
  }
  const cached = refs.cache && refs.cache.vig;
  if (cached) {
    ctx.drawImage(cached, 0, 0, cw, ch);
  } else {
    paintVigInto(ctx, cw, ch);
  }
  const noise = refs.art && refs.art.noise;
  if (noise && noise.width && !refs.lowDetail) {
    ctx.save();
    ctx.globalAlpha = 0.05;
    ctx.drawImage(noise, 0, 0, cw, ch);
    ctx.restore();
  }
}
