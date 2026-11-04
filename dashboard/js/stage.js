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
  controlBar, controlBarTip, displayOutcome, eventHost, hostBlocks, redNotches,
  reduceHostStates, scoreEvents,
} from "./state.js";
import { describeEvent } from "./story.js";
import { latestAuditorEvent, verdictCard } from "./choreography.js";

export const STAGE_WORLD = { w: 1600, h: 900 };
// Hosts fit inside this share of their band, leaving slack on every
// side for the camera to dolly without pushing hosts under lanes or
// entities. Share times zoom must stay under 1 or the dolly rests.
const FIT_SHARE = 0.9;
const DOLLY_ZOOM = 1.1;
// Entity presence radius as a share of play height: each entity
// spans roughly 30 to 35 percent of the stage height.
const ENTITY_SHARE = 0.16;
// Reserved rail under the HUD where the control bar's tie bursts
// open. Hosts never enter it, so bursts can never cover a host.
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

// Winner beat: after the blast the tail plays on the winner
// instead of the empty spot where the loser stood. A short pan
// toward the winner's side at rest zoom; capped so no host
// leaves the frame. Pure.
export function winnerBeatTarget(stage, winner) {
  const rest = { dx: 0, dy: 0, z: 1 };
  const slots = stage && stage.entities;
  const slot = slots
    ? (winner === "blue" ? slots.guardian : slots.intrusion)
    : null;
  if (!stage || !slot || !stage.band || !stage.area) return rest;
  const bandCx = (stage.band.x0 + stage.band.x1) / 2;
  const bandCy = (stage.area.top + stage.area.bottom) / 2;
  const cap = 70;
  const clamp = (value) => Math.max(-cap, Math.min(cap, value));
  return {
    dx: clamp((bandCx - slot.x) * 0.25),
    dy: clamp((bandCy - slot.y) * 0.25),
    z: 1,
  };
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

// Auditor chrome in wrap px, or null when the card is hidden or
// sits outside the wrap (the right rail): only an overlapping
// card constrains the canvas layout. Pure, for the harness.
export function auditorChrome(frame, audBox, shown) {
  if (!shown || !frame || !audBox) return null;
  const overlaps = audBox.left < frame.right && audBox.right > frame.left &&
    audBox.top < frame.bottom && audBox.bottom > frame.top;
  if (!overlaps) return null;
  return audBox.bottom - frame.top;
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
  const shown = Boolean(aud && aud.classList && aud.classList.contains("show"));
  const audBox = aud && shown && aud.getBoundingClientRect ? aud.getBoundingClientRect() : null;
  return {
    w: frame.width,
    h: frame.height,
    hudBottom: midBox ? midBox.bottom - frame.top : 0,
    capTop: capBox ? capBox.top - frame.top : frame.height,
    audBottom: auditorChrome(frame, audBox, shown),
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
    hitstopFrames: 0,
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
    blankRecoveries: 0,
    recoveryNote: "",
    lastProbe: 0,
    lastRebuild: 0,
    probeTick: 0,
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
    knock: { red: 0, blue: 0 },
    barTarget: 0,
    barShown: 0,
    barArmed: false,
    barRunKey: "",
    barCursor: 0,
    finish: null,
    dead: null,
    desat: 0,
    desatTarget: 0,
    fx: [],
    impacts: [],
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
      tug: el("arenaTug"),
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
    },
  };
  if (typeof ResizeObserver === "function" && canvas) {
    refs.ro = new ResizeObserver(() => fitStage(refs));
    refs.ro.observe(canvas);
  }
  if (canvas && typeof canvas.addEventListener === "function") {
    canvas.addEventListener("contextlost", (ev) => {
      try {
        if (ev && ev.preventDefault) ev.preventDefault();
      } catch {
        // Listener context only.
      }
      refs.contextLost = true;
    });
    canvas.addEventListener("contextrestored", () => {
      refs.contextLost = false;
      rebuildStage(refs, "context restored");
    });
  }
  fitStage(refs);
  return refs;
}

export function fitStage(refs) {
  if (!refs || !refs.canvas) return;
  // The inactive view never touches the shared canvas: resetting its
  // size would wipe the other view's frame. Refit on reactivation.
  if (!refs.active) return;
  const canvas = refs.canvas;
  const cw = canvas.clientWidth || 800;
  const ch = canvas.clientHeight || 450;
  const dpr = Math.min(1.5, (typeof window !== "undefined" && window.devicePixelRatio) || 1);
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
    lance: glowSprite([[0, "rgba(255,64,42,0.95)"], [0.35, "rgba(255,45,30,0.55)"], [0.7, "rgba(178,24,18,0.22)"], [1, "rgba(178,24,18,0)"]]),
    orb: glowSprite([[0, "rgba(150,220,255,0.95)"], [0.35, "rgba(70,180,255,0.55)"], [0.7, "rgba(30,120,255,0.22)"], [1, "rgba(30,120,255,0)"]]),
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

// Entity bodies: each hero splash baked once into a 512px sprite
// with a soft radial mask, so the dark background disappears and
// only the being remains. Drawn additively per frame.
function buildEntityBodies(refs) {
  const art = refs.art || {};
  const key = `${art.intrusionTex ? "i" : ""}${art.guardianTex ? "g" : ""}`;
  if (refs.bodyKey === key) return refs.bodies || {};
  refs.bodyKey = key;
  refs.bodies = {
    intrusion: maskSplash(art.intrusionTex),
    guardian: maskSplash(art.guardianTex),
  };
  return refs.bodies;
}

function maskSplash(tex) {
  if (!tex || !tex.width) return null;
  const S = 512;
  const layer = makeLayer(S, S);
  if (!layer) return null;
  let g = null;
  try {
    g = layer.getContext("2d");
  } catch {
    return null;
  }
  if (!g) return null;
  try {
    const side = Math.min(tex.width, tex.height);
    g.drawImage(tex, (tex.width - side) / 2, (tex.height - side) / 2, side, side, 0, 0, S, S);
    g.globalCompositeOperation = "destination-in";
    const mask = g.createRadialGradient(S / 2, S / 2, S * 0.2, S / 2, S / 2, S * 0.5);
    mask.addColorStop(0, "rgba(255,255,255,1)");
    mask.addColorStop(0.68, "rgba(255,255,255,1)");
    mask.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = mask;
    g.fillRect(0, 0, S, S);
  } catch {
    return null;
  }
  return layer;
}

// Melt sampler: glyph slots from an entity sprite's own pixels.
// data is RGBA bytes of a w-wide square; accent is [r,g,b]. Keeps
// opaque pixels only, sorts outer-first so the peel starts at the
// rim, stride-picks down to max, and tints each slot white-hot
// toward the accent with a trace of its pixel. Pure, for tests.
export function sampleMeltMask(data, w, accent, max) {
  const out = [];
  const n = Math.floor((data ? data.length : 0) / 4);
  const width = Math.max(1, Math.floor(Number(w) || 1));
  const acc = Array.isArray(accent) ? accent : [255, 255, 255];
  const cap = Math.max(1, Math.floor(Number(max) || 1));
  for (let i = 0; i < n; i += 1) {
    const a = data[i * 4 + 3];
    if (a < 48) continue;
    const x = (i % width + 0.5) / width - 0.5;
    const y = (Math.floor(i / width) + 0.5) / width - 0.5;
    out.push({
      x, y,
      rad: Math.hypot(x, y),
      r: data[i * 4], g: data[i * 4 + 1], b: data[i * 4 + 2],
    });
  }
  out.sort((p, q) => q.rad - p.rad);
  const step = Math.max(1, Math.ceil(out.length / cap));
  const picked = [];
  for (let i = 0; i < out.length && picked.length < cap; i += step) {
    const s = out[i];
    // White-hot core tinted toward the accent, keeping a trace of
    // the sampled pixel: bright enough to read on a dark stage.
    let r = 255 * 0.55 + (Number(acc[0]) || 0) * 0.3 + s.r * 0.15;
    let g = 255 * 0.55 + (Number(acc[1]) || 0) * 0.3 + s.g * 0.15;
    let b = 255 * 0.55 + (Number(acc[2]) || 0) * 0.3 + s.b * 0.15;
    // Brightness floor: the splash rim runs near-black, and a dark
    // glyph on a dark stage is invisible. Lift toward white along
    // the same hue so the art's colors survive, legibly.
    const peak = Math.max(r, g, b, 1);
    if (peak < 150) {
      const k = 150 / peak;
      r *= k; g *= k; b *= k;
    }
    picked.push({
      x: s.x, y: s.y,
      fill: `rgb(${Math.min(255, Math.round(r))},${Math.min(255, Math.round(g))},${Math.min(255, Math.round(b))})`,
    });
  }
  return picked;
}

// Melt masks, built once per sprite set: each hero splash is read
// back from its own baked body canvas, so the dissolve carries the
// art's shapes and colors. Cached on refs; the paint path only reads.
function buildMeltMasks(refs) {
  const key = refs.bodyKey || "";
  if (refs.meltKey === key && refs.meltMasks) return refs.meltMasks;
  refs.meltKey = key;
  refs.meltMasks = {
    red: sampleBodyMask(refs, "intrusion", MELT_ACCENTS.red),
    blue: sampleBodyMask(refs, "guardian", MELT_ACCENTS.blue),
  };
  return refs.meltMasks;
}

function sampleBodyMask(refs, which, accent) {
  try {
    const body = (refs.bodies || {})[which];
    if (!body || !body.width) return [];
    const S = 96;
    const probe = makeLayer(S, S);
    const g = probe ? probe.getContext("2d") : null;
    if (!g) return [];
    g.drawImage(body, 0, 0, S, S);
    const px = g.getImageData(0, 0, S, S).data;
    return sampleMeltMask(px, S, accent, MELT_SLOTS);
  } catch {
    return [];
  }
}

function relayoutStage(refs) {
  if (!refs) return;
  const { cw, ch } = refs.geom;
  // Offscreen thumbnails override with a zero-size rect to force
  // the proportional fallback instead of the live wrap boxes.
  const measured = refs.chromeOverride || measureChrome();
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
    const bar = controlBar(list, slice);
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
      states, blocks, hostOrder: hosts, bar,
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
    refs.barTarget = bar.value;
    if (o.keepFx && !instant) {
      // Single-step playback advance: the previous move's tail
      // (impact aftermath, callout) keeps painting under the next
      // windup. Expired entries drop; sparks and the impact ledger
      // age out on their own. Scrub and jump still clear all.
      refs.fx = (refs.fx || []).filter((fx) => refs.clock - (fx.t0 || 0) <= (fx.dur || 0) + 200);
    } else {
      refs.fx = [];
      refs.impacts = [];
      refs.sparks = [];
    }
    refs.finish = null;
    refs.dead = null;
    refs.desat = 0;
    refs.desatTarget = 0;
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
    refs.knock = { red: 0, blue: 0 };
    refs.charge = null;
    refs.cam = { dx: 0, dy: 0, z: 1 };
    refs.camTarget = { dx: 0, dy: 0, z: 1 };
    refs.trauma = 0;
    if (instant || refs.reduced) {
      refs.typed = {
        red: { at: -1, chars: Infinity },
        blue: { at: -1, chars: Infinity },
      };
    }
    // Tie tracking runs after the transient clears so its burst
    // ring and sparks survive the rebuild.
    trackBarTie(refs, bar, at, o.label || "Exercise", list.length, instant);
    updateStageHud(refs);
    syncStageAuditor(refs, list, at);
    drawStage(refs);
    return { states };
  }
  return { states: {} };
}

// Tie and start tracking for the control bar: a fresh run pops
// the marker once at the start, and an exact return to center
// after action on either side pops it again. Scrub rebuilds snap
// the bar without effects.
function trackBarTie(refs, bar, at, label, total, instant) {
  const key = `${label}|${total}`;
  if (key !== refs.barRunKey) {
    refs.barRunKey = key;
    refs.barArmed = false;
    refs.barShown = bar.value;
    refs.barCursor = at;
    if (!instant && !refs.reduced) barTieBurst(refs);
    return;
  }
  if (at === 0 && refs.barCursor > 0) {
    refs.barCursor = 0;
    refs.barArmed = false;
    refs.barShown = bar.value;
    if (!instant && !refs.reduced) barTieBurst(refs);
    return;
  }
  refs.barCursor = at;
  if (instant || refs.reduced) {
    refs.barShown = bar.value;
    return;
  }
  if (bar.raw === 0 && refs.barArmed) {
    refs.barArmed = false;
    barTieBurst(refs);
  } else if (bar.raw !== 0) {
    refs.barArmed = true;
  }
}

// Marker position in canvas pixels, or null when the DOM rects
// cannot be measured (software harness, hidden tab).
function barMarkerCanvas(refs) {
  const hud = refs.hud;
  if (!hud || !hud.tugM || !refs.canvas) return null;
  const getRect = (node) => (node && typeof node.getBoundingClientRect === "function"
    ? node.getBoundingClientRect()
    : null);
  const c = getRect(refs.canvas);
  const m = getRect(hud.tugM);
  if (!c || !m || !(c.width > 0) || !(m.width > 0)) return null;
  const x = m.left + m.width / 2 - c.left;
  const y = m.bottom - c.top;
  if (x < 0 || y < 0 || x > c.width || y > c.height) return null;
  return { x, y };
}

// Tie burst: the marker pops (CSS) and a small cold explosion
// opens under the bar (canvas ring plus sparks).
function barTieBurst(refs) {
  if (!refs || refs.reduced) return;
  const hud = refs.hud;
  if (hud && hud.tug && hud.tug.classList) {
    hud.tug.classList.remove("tie");
    void hud.tug.offsetWidth;
    hud.tug.classList.add("tie");
  }
  const at = barMarkerCanvas(refs);
  if (at) {
    refs.fx.push({
      kind: "ring", x: at.x, y: at.y, t0: refs.clock, dur: 600,
      color: "255,255,255", r0: 6, r1: 70,
    });
    spawnSparks(refs, at.x, at.y, "white", 14);
  }
}

// Every landed move thumps the bar marker (CSS pulse only; the
// projectile it launched carries the canvas motion).
function pulseBar(refs) {
  const hud = refs && refs.hud;
  if (!hud || !hud.tug || !hud.tug.classList || (refs && refs.reduced)) return;
  hud.tug.classList.remove("hit");
  void hud.tug.offsetWidth;
  hud.tug.classList.add("hit");
}

// One static arena frame for lobby thumbnails: the episode's real
// stage at the poster event, painted once into the given canvas.
// Headless by design: no HUD writes, no loop, no effects, always
// the reduced still path. Returns the refs for harness inspection.
export function renderStageThumb(canvas, lane, cursor, opts) {
  const o = opts || {};
  const refs = initStage(canvas, { art: o.art || {}, reduced: true });
  refs.hud = {};
  // 800x450 keeps the wide side composition (big entities facing
  // off across the network); 480 wide would fall into the stacked
  // narrow layout where the clash reads as specks.
  refs.geom = { cw: 800, ch: 450, dpr: 1 };
  canvas.width = 800;
  canvas.height = 450;
  refs.chromeOverride = { w: 0, h: 0 };
  setStageLayout(refs, o.layout || classicLayout());
  const list = lane || [];
  const at = Math.max(0, Math.min(list.length, cursor || 0));
  // The static build always clears staged fx and paints once, so
  // the context hides during the build: layout and states compute,
  // the paint waits for the staged clash below. One paint total.
  const ctx = refs.ctx;
  refs.ctx = null;
  try {
    renderStageStatic(refs, list, at, {
      weights: o.weights || {},
      label: o.label || "thumbnail",
      display: null,
      layout: refs.layout,
      attackMap: o.attackMap,
    }, true);
  } finally {
    refs.ctx = ctx;
  }
  // Poster staging: the poster event's own effects freeze
  // mid-flight, so the still shows the clash, not the calm after.
  // No impact ledger: sparks and hit-stop stay live-only.
  const poster = list[at - 1];
  if (poster && (poster.actor === "red" || poster.actor === "blue")) {
    let lastRed = null;
    for (const ev of list.slice(0, Math.max(0, at - 1))) {
      if (ev && ev.actor === "red" && ev.tool === "try_credential" &&
          (ev.result || {}).authenticated === true) {
        lastRed = eventHost(ev);
      }
    }
    const jewel = refs.layout ? refs.layout.jewel_host : null;
    for (const fx of fxForEvent(poster, { jewel, lastRedHost: lastRed })) {
      const span = Number(fx.flight) || Number(fx.dur) || 800;
      refs.fx.push({ ...fx, t0: refs.clock - span * 0.45 });
    }
  }
  drawStage(refs);
  const slots = refs.stage ? refs.stage.entities : null;
  refs.thumbCrop = slots
    ? clashCrop(slots.intrusion, slots.guardian, 800, 450)
    : { sx: 0, sy: 0, sw: 800, sh: 450 };
  return refs;
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
  if (ev.actor === "red") refs.surge.red = refs.clock + 900;
  if (ev.actor === "blue") refs.surge.blue = refs.clock + 900;
  const prevRed = refs.lastRedHost;
  if (ev.actor === "red" && ev.tool === "try_credential" && (ev.result || {}).authenticated === true) {
    refs.lastRedHost = eventHost(ev);
  }
  if (!refs.reduced && (ev.actor === "red" || ev.actor === "blue")) {
    pulseBar(refs);
    for (const fx of fxForEvent(ev, { jewel: refs.jewel, lastRedHost: prevRed })) {
      const entry = { ...fx, t0: refs.clock };
      refs.fx.push(entry);
      scheduleImpacts(refs, entry);
    }
    const callout = calloutForEvent(ev, refs.attackMap);
    if (callout) {
      refs.fx.push({ kind: "callout", ...callout, host: eventHost(ev), t0: refs.clock, dur: 2000 });
    }
  }
  if (ev.actor === "red" && ev.tool === "read_file" && (ev.result || {}).ok === true &&
      String((ev.result || {}).path || "").startsWith("/secret/")) {
    refs.trauma = Math.min(1, refs.trauma + 0.8);
    refs.knock.blue = refs.clock + 600;
  }
  const host = eventHost(ev);
  if (host && refs.nodes[host]) {
    refs.camTarget = dollyTarget(refs.stage, refs.nodes, host);
    refs.idleUntil = refs.clock + 2600 / (Number(speed) || 1);
  }
  const r = (ev.result || {}).ok === true;
  if (ev.actor === "blue" && r && (ev.tool === "isolate_host" || ev.tool === "patch_service")) {
    refs.trauma = Math.min(1, refs.trauma + 0.45);
    refs.knock.red = refs.clock + 450;
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
  paintBarDom(refs);
  if (hud.tug && snap.bar) {
    const tip = controlBarTip(snap.bar);
    hud.tug.setAttribute("title", tip);
    hud.tug.setAttribute("aria-label", `Control bar. ${tip}`);
  }
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
      hud.capTag.style.fontSize = capTagFontSize(snap.tag);
    }
    if (hud.capText) hud.capText.textContent = snap.caption;
    if (hud.capSub) hud.capSub.textContent = snap.sub;
    // The big caption yields the stage after 2s: it shrinks to a
    // slim strip until the next move replaces it.
    if (hud.cap) {
      hud.cap.classList.remove("mini");
      if (refs.capTimer) clearTimeout(refs.capTimer);
      const cap = hud.cap;
      refs.capTimer = setTimeout(() => {
        cap.classList.add("mini");
        if (refs) refs.capTimer = 0;
      }, 2000);
    }
  }
}

// Control bar DOM paint from the eased value: red fill grows from
// the left, the marker rides the boundary.
function paintBarDom(refs) {
  const hud = refs.hud;
  if (!hud) return;
  const pc = `${((0.5 + Math.max(-1, Math.min(1, Number(refs.barShown) || 0)) / 2) * 100).toFixed(1)}%`;
  if (hud.tugR) hud.tugR.style.width = pc;
  if (hud.tugM) hud.tugM.style.left = pc;
}

// One eased step of the shown bar toward its target. A lead change
// mid-slide crosses center and pops the marker like an exact tie.
function stepBarShown(refs, dtMs) {
  const target = Math.max(-1, Math.min(1, Number(refs.barTarget) || 0));
  if (refs.reduced) {
    refs.barShown = target;
    paintBarDom(refs);
    return;
  }
  const prev = Number(refs.barShown) || 0;
  const k = 1 - Math.exp(-Math.max(0, dtMs || 16) / 1000 * 6);
  let next = prev + (target - prev) * k;
  if (Math.abs(target - next) < 0.002) next = target;
  refs.barShown = next;
  paintBarDom(refs);
  // A sign flip mid-slide is a lead change through center; an
  // exact landing on zero belongs to the tie tracker instead.
  if (prev !== 0 && next !== 0 && Math.sign(prev) !== Math.sign(next)) {
    barTieBurst(refs);
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
  relayoutStage(refs);
}

export function hideStageAuditor(refs, silent) {
  if (!refs) return;
  if (refs.hud && refs.hud.aud && refs.hud.aud.className !== "") {
    refs.hud.aud.className = "";
    if (!silent) {
      relayoutStage(refs);
    }
  }
}

// Verdict card from the cursor: the most recent auditor event at or
// before the cursor stays visible across pause and scrub, and clears
// only when the cursor moves before it.
function syncStageAuditor(refs, events, cursor) {
  const found = latestAuditorEvent(events, cursor);
  if (found) showStageAuditor(refs, found);
  else hideStageAuditor(refs, true);
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
  // Scrub and replay clear transient effects only. The loop and the
  // resize observer stay up: killing them here used to freeze idle
  // animation and strand the canvas black after any resize cleared it.
  if (!refs) return;
  refs.fx = [];
  refs.impacts = [];
  refs.sparks = [];
  refs.flash = null;
  refs.finish = null;
  refs.dead = null;
  refs.desat = 0;
  refs.desatTarget = 0;
  refs.surge = { red: 0, blue: 0 };
  refs.knock = { red: 0, blue: 0 };
  refs.charge = null;
  if (refs.capTimer) {
    clearTimeout(refs.capTimer);
    refs.capTimer = 0;
  }
}

export function setStageActive(refs, on) {
  if (!refs) return;
  refs.active = Boolean(on);
  if (refs.active) {
    // Refit first: resizes while deselected never touched the canvas.
    fitStage(refs);
    if (refs.reduced) startStageSlowLoop(refs);
    else startStageLoop(refs);
  } else {
    if (refs.raf) {
      if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(refs.raf);
      else clearTimeout(refs.raf);
      refs.raf = 0;
    }
    if (refs.slowTimer) {
      clearTimeout(refs.slowTimer);
      refs.slowTimer = 0;
    }
  }
}

// Reduced motion keeps a slow repaint beat (about 4fps) instead of
// no loop at all: static means no animated effects, not no
// repainting, and the blank-guard probe still runs so the stage
// can never sit black. The clock stays frozen, so every paint is
// the same still, rebuilt.
export function setStageReduced(refs, reduced) {
  if (!refs) return;
  refs.reduced = Boolean(reduced);
  if (refs.active) {
    if (refs.raf) {
      if (typeof cancelAnimationFrame === "function") cancelAnimationFrame(refs.raf);
      else clearTimeout(refs.raf);
      refs.raf = 0;
    }
    if (refs.slowTimer) {
      clearTimeout(refs.slowTimer);
      refs.slowTimer = 0;
    }
    if (refs.reduced) startStageSlowLoop(refs);
    else startStageLoop(refs);
  }
}

function startStageSlowLoop(refs) {
  if (!refs || refs.slowTimer || !refs.reduced || !refs.active) return;
  const beat = () => {
    refs.slowTimer = 0;
    if (!refs.active || !refs.reduced) return;
    drawStage(refs);
    sampleFps(refs, Date.now());
    refs.slowTimer = setTimeout(beat, 250);
  };
  refs.slowTimer = setTimeout(beat, 250);
}

// Rolling frames-per-second from repaint timestamps, shared by the
// full loop and the reduced-motion beat.
function sampleFps(refs, t) {
  refs.fpsCount += 1;
  if (!refs.fpsT) refs.fpsT = t;
  if (t - refs.fpsT >= 500) {
    refs.fps = Math.round((refs.fpsCount * 1000) / (t - refs.fpsT));
    refs.fpsCount = 0;
    refs.fpsT = t;
  }
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
    `${refs.lowDetail ? "LOW" : "FULL"} blanks ${refs.blankFrames || 0} rec ${refs.blankRecoveries || 0}` +
    (refs.lastError ? `\n! ${refs.lastError}` : "") +
    (refs.recoveryNote ? `\n! recovered: ${refs.recoveryNote}` : "");
}

// Event-to-effects map, pure: each real event names the marks it
// leaves on the target host itself. ctx carries { jewel,
// lastRedHost }. Lateral moves draw a tendril along the link from
// the previous red foothold to the new host.
export function fxForEvent(ev, ctx) {
  if (!ev) return [];
  const c = ctx || {};
  const host = eventHost(ev);
  const r = ev.result || {};
  const failed = r.ok === false;
  if (ev.actor === "red") {
    // Every red move also flies a crimson shard at the Guardian:
    // scans volley three small ones, sign-ins and the secret read
    // throw heavy lances, failed moves shatter on the shield.
    const fail = projectileEntry("lance", "red", "standard", true);
    if (failed && ev.tool !== "try_credential") {
      return host ? [{ kind: "fizzle", host, dur: 700 }, fail] : [fail];
    }
    switch (ev.tool) {
      case "scan_host":
        return host
          ? [{ kind: "fan", host, dur: 900 }, projectileEntry("volley", "red", "small", false)]
          : [projectileEntry("volley", "red", "small", false)];
      case "http_request":
      case "list_files":
        return host
          ? [{ kind: "needle", host, dur: 800 }, projectileEntry("lance", "red", "standard", false)]
          : [projectileEntry("lance", "red", "standard", false)];
      case "try_credential":
        if (r.authenticated !== true) {
          return host ? [{ kind: "keys", host, dur: 900 }, fail] : [fail];
        }
        if (!host) return [projectileEntry("lance", "red", "heavy", false)];
        const out = [
          { kind: "pierce", host, dur: 1000 },
          projectileEntry("lance", "red", "heavy", false),
        ];
        if (c.lastRedHost && c.lastRedHost !== host) {
          out.push({ kind: "tendril", from: c.lastRedHost, host, dur: 1200 });
        }
        return out;
      case "read_file": {
        const heavy = projectileEntry("lance", "red", "heavy", false);
        const standard = projectileEntry("lance", "red", "standard", false);
        if (!host) return r.ok === true ? [standard] : [fail];
        const path = String(r.path || (ev.args || {}).path || "");
        if (r.ok === true && path.startsWith("/secret/")) return [{ kind: "grab", host, dur: 1400 }, heavy];
        if (r.ok === true) return [{ kind: "siphon", host, dur: 1100 }, standard];
        return [{ kind: "fizzle", host, dur: 700 }, fail];
      }
      default:
        return [];
    }
  }
  if (ev.actor === "blue") {
    // Every blue move answers with a light orb at the Intrusion:
    // reviews and reports send small ones, isolation and patching
    // send large ones, failed moves fizzle mid-flight.
    const fail = projectileEntry("orb", "blue", "standard", true);
    if (failed) return host ? [{ kind: "fizzle", host, dur: 700, color: "blue" }, fail] : [fail];
    switch (ev.tool) {
      case "read_logs":
        return host
          ? [{ kind: "scanring", host, dur: 900 }, projectileEntry("orb", "blue", "small", false)]
          : [projectileEntry("orb", "blue", "small", false)];
      case "report_finding":
        return [{ kind: "flare", dur: 800 }, projectileEntry("orb", "blue", "small", false)];
      case "isolate_host":
        return host ? [
          { kind: "slam", host, dur: 1000 },
          { kind: "ring", host, t0: 0, dur: 800, color: "127,212,255", r0: 10, r1: 90 },
          projectileEntry("orb", "blue", "large", false),
        ] : [projectileEntry("orb", "blue", "large", false)];
      case "patch_service":
        return host
          ? [{ kind: "sweep", host, dur: 900 }, projectileEntry("orb", "blue", "large", false)]
          : [projectileEntry("orb", "blue", "large", false)];
      case "restore_host":
        return host
          ? [{ kind: "reconnect", host, dur: 1000 }, projectileEntry("orb", "blue", "standard", false)]
          : [projectileEntry("orb", "blue", "standard", false)];
      default:
        return [];
    }
  }
  return [];
}

// Kinetic callout content for one move: the technique name from the
// data over the plain-English line. Null for non-move events.
// Caption tag size: short tags (RED, BLUE) use the chip's full
// size; longer tags (EXERCISE) step down so the whole word fits
// the chip at any canvas width instead of clipping. Pure.
export function capTagFontSize(tag) {
  return String(tag || "").length > 5 ? "clamp(12px,2.2cqw,34px)" : "";
}

export function calloutForEvent(ev, attackMap) {
  if (!ev || (ev.actor !== "red" && ev.actor !== "blue")) return null;
  const map = attackMap || {};
  let tech = ev.tool || "move";
  if (ev.actor === "red") {
    const entry = map.red && map.red[ev.tool];
    if (entry && entry.technique) tech = `${entry.technique} · ${entry.name || ev.tool}`;
  } else {
    const entry = map.blue && map.blue[ev.tool];
    if (entry && entry.label) tech = entry.label;
  }
  return { tech, line: describeEvent(ev), side: ev.actor, failed: (ev.result || {}).ok === false };
}

// Projectile flight lane between the entities: a quadratic arc that
// lifts over the network so the exchange reads above the hosts.
// Pure: endpoints in, lane out; lanePoint samples it.
export function flightLane(from, to) {
  const a = from || { x: 0, y: 0 };
  const b = to || { x: 0, y: 0 };
  const dist = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const lift = Math.max(80, Math.min(200, dist * 0.3));
  return {
    x0: a.x, y0: a.y,
    cx: (a.x + b.x) / 2, cy: Math.min(a.y, b.y) - lift,
    x1: b.x, y1: b.y,
  };
}

export function lanePoint(lane, t) {
  const u = Math.max(0, Math.min(1, Number(t) || 0));
  const a = (1 - u) * (1 - u);
  const b = 2 * (1 - u) * u;
  const c = u * u;
  return {
    x: a * lane.x0 + b * lane.cx + c * lane.x1,
    y: a * lane.y0 + b * lane.cy + c * lane.y1,
  };
}

// Bounding box of the projectile lane between two entity slots,
// padded for bodies and trails. Pure, for the overlap harness:
// the lane must clear the HUD (and the bar inside it), the
// caption, and the auditor card at every size. The pad defaults
// to body size; pass a small pad for the curve-only box.
export function flightLaneRect(intrusion, guardian, pad) {
  const a = intrusion || { x: 0, y: 0, r: 0 };
  const b = guardian || { x: 0, y: 0, r: 0 };
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const from = { x: a.x + (dx / len) * a.r * 0.7, y: a.y + (dy / len) * a.r * 0.7 };
  const to = { x: b.x - (dx / len) * b.r * 0.55, y: b.y - (dy / len) * b.r * 0.55 };
  const lane = flightLane(from, to);
  // Quadratic extrema where the derivative crosses zero.
  const extrema = (p0, pc, p1) => {
    const denom = p0 - 2 * pc + p1;
    if (denom === 0) return [];
    const t = (p0 - pc) / denom;
    return t > 0 && t < 1 ? [t] : [];
  };
  const xs = [lane.x0, lane.x1];
  const ys = [lane.y0, lane.y1];
  for (const t of [...extrema(lane.x0, lane.cx, lane.x1), ...extrema(lane.y0, lane.cy, lane.y1)]) {
    const pt = lanePoint(lane, t);
    xs.push(pt.x);
    ys.push(pt.y);
  }
  const base = pad === undefined ? 44 : Math.max(0, Number(pad) || 0);
  const margin = pad === undefined ? base * projStageScale(a.r, b.r) : base;
  const x0 = Math.min(...xs) - margin;
  const y0 = Math.min(...ys) - margin;
  return { x: x0, y: y0, w: Math.max(...xs) - x0 + margin, h: Math.max(...ys) - y0 + margin };
}

// Clash crop for thumbnails: the tightest 16:9 box holding both
// entities and the full projectile lane between them, so a card
// reads as red versus blue with the hosts behind. Clamped to the
// frame. Pure, for tests.
export function clashCrop(red, blue, W, H) {
  const w = Math.max(48, Number(W) || 480);
  const h = Math.max(27, Number(H) || 270);
  const r = red || { x: w * 0.22, y: h * 0.55, r: 40 };
  const b = blue || { x: w * 0.78, y: h * 0.55, r: 40 };
  const lane = flightLaneRect(r, b, 0);
  const m = Math.max(20, ((Number(r.r) || 40) + (Number(b.r) || 40)) / 2 * 0.9);
  const x0 = Math.min(lane.x, r.x - r.r, b.x - b.r) - m;
  const x1 = Math.max(lane.x + lane.w, r.x + r.r, b.x + b.r) + m;
  const y0 = Math.min(lane.y, r.y - r.r, b.y - b.r) - m;
  const y1 = Math.max(lane.y + lane.h, r.y + r.r, b.y + b.r) + m;
  let cw = x1 - x0;
  let ch = (cw * 9) / 16;
  if (ch < y1 - y0) {
    ch = y1 - y0;
    cw = (ch * 16) / 9;
  }
  if (cw > w) {
    cw = w;
    ch = (cw * 9) / 16;
  }
  if (ch > h) {
    ch = h;
    cw = (ch * 16) / 9;
  }
  cw = Math.round(cw);
  ch = Math.round(ch);
  const sx = Math.round(Math.min(Math.max(0, (x0 + x1) / 2 - cw / 2), Math.max(0, w - cw)));
  const sy = Math.round(Math.min(Math.max(0, (y0 + y1) / 2 - ch / 2), Math.max(0, h - ch)));
  return { sx, sy, sw: cw, sh: ch };
}

// Flight times in ms by projectile size: travel runs about half a
// second for light moves to four-fifths for heavy ones, then a short
// impact window for the shockwave and flash.
export const FLIGHT_MS = { small: 500, standard: 650, heavy: 800, large: 800 };
export const IMPACT_MS = 350;
export const AFTERMATH_MS = 800;
export const VOLLEY_STAGGER_MS = 120;

// Full on-screen life of one move's projectile, flight plus
// impact plus the fading aftermath ring. Every size runs past
// the 1600ms replay beat so the next windup starts while the
// previous impact is still fading. Pure, for tests.
export function moveSpanMs(size) {
  return (FLIGHT_MS[size] || FLIGHT_MS.standard) + IMPACT_MS + AFTERMATH_MS;
}

// Finish pace scales every beat of the ending: a short charge,
// one giant projectile, then three beams two seconds apart at
// standard pace; the card lands past the middle of the long
// dissolve, and the trimmed seal hold keeps the whole finish
// near eight seconds (quick near five, slow near fourteen).
export const FINISH_PACES = { quick: 0.65, standard: 1, slow: 1.8 };

export function finishPlan(pace) {
  const m = FINISH_PACES[pace] || FINISH_PACES.standard;
  const charge = Math.round(600 * m);
  const titanFlight = Math.round(900 * m);
  const gap = Math.round(2000 * m);
  const beamDur = Math.round(1400 * m);
  const postBeam = Math.round(500 * m);
  const explodeDur = Math.round(1800 * m);
  const cardDelay = Math.round(400 * m);
  return {
    mult: m, charge, titanFlight, gap, beamDur, postBeam, explodeDur, cardDelay,
    total: charge + titanFlight + 2 * gap + postBeam + explodeDur + cardDelay,
  };
}

function projectileEntry(kind, side, size, fail) {
  const flight = FLIGHT_MS[size] || FLIGHT_MS.standard;
  const stagger = kind === "volley" ? VOLLEY_STAGGER_MS * 2 : 0;
  return {
    kind, side, size, fail: Boolean(fail), flight,
    dur: flight + stagger + IMPACT_MS + AFTERMATH_MS,
  };
}

// Hit-stop freeze in frames by move size: small volleys land
// without freezing, standard hits hold 3 frames, heavy ones 5,
// and the finish's titan projectile holds 6. Pure.
export function hitstopFor(size) {
  if (size === "titan") return 6;
  if (size === "heavy" || size === "large") return 5;
  if (size === "small") return 0;
  return 3;
}

// Frames of sustained slowness before the stage drops detail.
export const LOW_STREAK = 8;

// Rolling frame-time budget with hysteresis: LOW detail engages
// only after sustained slowness (a streak of slow frames, never
// one spike) and recovers when the average settles. Pure; the
// streak counts consecutive frames over 26ms.
export function frameBudget(avg, low, dtMs, streak) {
  const dt = Math.min(100, Math.max(1, Number(dtMs) || 16));
  const next = avg + (dt - avg) * 0.06;
  const slow = dt > 26 ? (Number(streak) || 0) + 1 : 0;
  if (!low && slow >= LOW_STREAK) return { avg: next, low: true, slow };
  if (low && next < 17) return { avg: next, low: false, slow };
  return { avg: next, low: Boolean(low), slow };
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
    const budget = frameBudget(refs.frameAvg || 16, refs.lowDetail, dt, refs.slowStreak);
    refs.frameAvg = budget.avg;
    refs.lowDetail = budget.low;
    refs.slowStreak = budget.slow;
    refs.samples.push(dt);
    if (refs.samples.length > 120) refs.samples.shift();
    sampleFps(refs, t);
    if (refs.hitstopFrames > 0) {
      // Hit-stop: hold the frozen frame for a beat so heavy
      // impacts land. Counted in frames, immune to clock drift.
      refs.hitstopFrames -= 1;
      drawStage(refs);
      return;
    }
    refs.clock += dt;
    refs.cam = stepCamera(refs.cam, refs.camTarget, dt);
    stepBarShown(refs, dt);
    if (refs.finish && !refs.finish.fired && refs.clock >= refs.finish.doneAt) {
      // The ending ran its course: hand the card to the caller once.
      refs.finish.fired = true;
      const done = refs.finish.onDone;
      refs.finish = null;
      if (typeof done === "function") done();
    }
    if (refs.desat !== refs.desatTarget) {
      const dk = 1 - Math.exp(-Math.max(0, dt) / 1000 * 3);
      refs.desat += (refs.desatTarget - refs.desat) * dk;
      if (Math.abs(refs.desatTarget - refs.desat) < 0.01) refs.desat = refs.desatTarget;
    }
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
    maybeProbeBlank(refs);
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
const CODE_GLYPHS = "01<>/\\{}=+*#$";
// Melt tuning: the flare and shockwave peak and fade for 400ms
// before the peel starts, glyphs peel across 3200ms, and the last
// of the sprite fades over 800ms after. The whole dissolve runs
// about twice as long as the first cut, with a softer falloff so
// glyphs stay readable for most of it. Desktop samples 450
// slots; LOW strides the same mask down to about 180.
export const MELT_DELAY_MS = 400;
export const MELT_SPAN_MS = 3200;
export const MELT_FADE_MS = 800;
export const MELT_SLOTS = 450;
export const MELT_SLOTS_LOW = 180;
const MELT_GLYPHS = "01";
// Loser accents for the melt: white-hot cores tinted toward these.
const MELT_ACCENTS = { red: [255, 110, 90], blue: [140, 210, 255] };

// Sprite alpha across the melt: full while the flare peaks, at
// least 70% while glyphs peel, gone 400ms after the peel ends.
export function meltSpriteAlpha(age) {
  const a = Math.max(0, Number(age) || 0);
  if (a < MELT_DELAY_MS) return 1;
  if (a < MELT_DELAY_MS + MELT_SPAN_MS) {
    return 1 - ((a - MELT_DELAY_MS) / MELT_SPAN_MS) * 0.3;
  }
  if (a < MELT_DELAY_MS + MELT_SPAN_MS + MELT_FADE_MS) {
    return 0.7 * (1 - (a - MELT_DELAY_MS - MELT_SPAN_MS) / MELT_FADE_MS);
  }
  return 0;
}

// Melt digit: 0/1 flipping about every 120ms, phased per glyph so
// the cloud flickers instead of blinking in sync.
export function meltChar(j, clockMs) {
  const n = Math.floor(Math.max(0, Number(j) || 0));
  const t = Math.floor(Math.max(0, Number(clockMs) || 0) / 120);
  return (n + t) % 2 ? "1" : "0";
}

// LOW strides one mask down to its cap; full detail takes all slots.
export function meltStride(count, low) {
  if (!low) return 1;
  return Math.max(1, Math.ceil(Math.max(0, Number(count) || 0) / MELT_SLOTS_LOW));
}

// Glyph pixels from the entity's rendered radius: 16 to 26.
export function meltGlyphPx(rr) {
  return Math.min(26, Math.max(16, Math.round(Math.max(8, Number(rr) || 8) * 0.16)));
}

// Per-glyph alphas over life lp (0 birth, 1 death). Soft falloff:
// late glyphs stay readable instead of winking out while the peel
// still has room to run.
export function meltGlyphAlpha(lp) {
  const t = Math.max(0, Math.min(1, Number(lp) || 0));
  return Math.min(1, t * 4) * Math.pow(1 - t, 0.35) * 0.95;
}

export function meltHaloAlpha(lp) {
  const t = Math.max(0, Math.min(1, Number(lp) || 0));
  // Dark enough to ring each glyph even over the white-hot core.
  return Math.pow(1 - t, 0.35) * 0.8;
}

// Melt drift at life lp: mostly radial with a mild upward bias
// plus a deterministic sideways shuffle, so the late cloud
// spreads around the sprite instead of stacking above it.
export function meltDrift(j, lp, rr, sx, sy) {
  const len = Math.hypot(sx, sy) || 1;
  const d = lp * lp;
  const r = Math.max(8, Number(rr) || 8);
  return {
    dx: (sx / len) * d * r * 1.4 + (fhash(j * 2 + 1) - 0.5) * d * r * 1.2,
    dy: (sy / len) * d * r * 1.0 - d * r * 0.35,
  };
}

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

// Projectile endpoints at the entities' rims, facing each other.
function projEnds(refs, side) {
  const from = entityCore(refs, side);
  const to = entityCore(refs, side === "blue" ? "red" : "blue");
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.hypot(dx, dy) || 1;
  return {
    from: { x: from.x + (dx / len) * from.r * 0.7, y: from.y + (dy / len) * from.r * 0.7 },
    to: { x: to.x - (dx / len) * to.r * 0.55, y: to.y - (dy / len) * to.r * 0.55 },
  };
}

// Finish sequence: when a decisive exercise ends, the winner
// charges, fires one giant projectile, and three beams burst
// outward from the loser before it explodes. Blue wins seal the
// Intrusion first and it dissolves; red wins shatter the Guardian
// and desaturate the stage. The titan and beams reuse the fx and
// impact ledgers with future stamps; completion fires from the
// tick so stills and live play agree.
export function stageFinish(refs, side, opts) {
  const o = opts || {};
  const done = typeof o.onDone === "function" ? o.onDone : () => {};
  if (!refs || refs.finish) return false;
  if (refs.reduced) {
    done();
    return true;
  }
  const winner = side === "red" ? "red" : "blue";
  const loser = winner === "red" ? "blue" : "red";
  const plan = finishPlan(o.pace);
  const t0 = refs.clock;
  if (!refs.impacts) refs.impacts = [];
  // Charge: the winner swells and brightens while the camera
  // pushes in slow, then the titan crosses the full stage.
  refs.charge = { side: winner, from: t0, until: t0 + plan.charge };
  refs.camTarget = { dx: 0, dy: 0, z: 1.18 };
  const kind = winner === "red" ? "lance" : "orb";
  const tat = t0 + plan.charge;
  const titan = {
    kind, side: winner, size: "titan", fail: false,
    flight: plan.titanFlight, t0: tat,
    dur: plan.titanFlight + IMPACT_MS + AFTERMATH_MS,
  };
  refs.fx.push(titan);
  scheduleImpacts(refs, titan);
  // Three beams burst outward from the loser, two seconds apart,
  // each wider and brighter; the first lands with the impact.
  const impactAt = tat + plan.titanFlight;
  for (let i = 0; i < 3; i += 1) {
    const at = impactAt + i * plan.gap;
    refs.fx.push({ kind: "beam", side: loser, power: i + 1, t0: at, dur: plan.beamDur });
    refs.impacts.push({ at, kind: "beamburst", side: loser, power: i + 1 });
  }
  const thirdAt = impactAt + 2 * plan.gap;
  if (winner === "blue") {
    refs.fx.push({ kind: "seal", t0: thirdAt, dur: plan.postBeam });
  }
  const explodeAt = thirdAt + plan.postBeam;
  refs.impacts.push({ at: explodeAt, kind: "explode", side: loser });
  refs.finish = { doneAt: explodeAt + plan.explodeDur + plan.cardDelay, onDone: done, fired: false };
  return true;
}

// Impact ledger: one record per projectile strike, shatter,
// mid-flight fizzle, beam burst, or final explosion. Fired from
// drawStage so stills and live play agree, and cleared on scrub
// like every transient effect.
function scheduleImpacts(refs, fx) {
  if (!refs || !fx) return;
  if (fx.kind !== "lance" && fx.kind !== "volley" && fx.kind !== "orb") return;
  if (!refs.impacts) refs.impacts = [];
  const struck = fx.side === "blue" ? "red" : "blue";
  const flight = Number(fx.flight) || FLIGHT_MS.standard;
  const t0 = Number(fx.t0) || refs.clock;
  if (fx.kind === "volley") {
    for (let i = 0; i < 3; i += 1) {
      refs.impacts.push({ at: t0 + flight + i * VOLLEY_STAGGER_MS, kind: "strike", side: struck, size: "small" });
    }
    return;
  }
  if (fx.fail && fx.kind === "orb") {
    // A failed orb dies halfway: fix its burst point now, from the
    // same lane the painter flies, so paint and sparks agree.
    const ends = projEnds(refs, fx.side);
    const mid = lanePoint(flightLane(ends.from, ends.to), 0.5);
    refs.impacts.push({ at: t0 + flight / 2, kind: "fizzlepuff", side: struck, x: mid.x, y: mid.y });
    return;
  }
  refs.impacts.push({
    at: t0 + flight,
    kind: fx.fail ? "shatter" : "strike",
    side: struck,
    size: fx.size || "standard",
  });
}

// Knock window per side, matching the recoil denominators in
// paintEntities: red reads 450ms, blue 600ms.
const KNOCK_MS = { red: 450, blue: 600 };

function fireImpacts(refs) {
  const due = refs.impacts;
  if (!due || due.length === 0) return;
  let dirty = false;
  for (const hit of due) {
    if (hit.fired || refs.clock < hit.at) continue;
    hit.fired = true;
    dirty = true;
    if (hit.kind === "fizzlepuff") {
      spawnSparks(refs, hit.x, hit.y, "gray", 8);
      continue;
    }
    const ends = projEnds(refs, hit.side === "red" ? "blue" : "red");
    if (hit.kind === "shatter") {
      // A failed lance breaks on the shield: sparks only, no
      // hit-stop, no recoil, and the bar does not move.
      spawnSparks(refs, ends.to.x, ends.to.y, "red", 7);
      spawnSparks(refs, ends.to.x, ends.to.y, "gray", 7);
      continue;
    }
    if (hit.kind === "beamburst") {
      // Finish beams burst outward from the loser with escalating
      // shake and sparks. The first arrives with the titan impact
      // and hands the camera back to the wide shot.
      const power = Math.max(1, Math.min(3, Number(hit.power) || 1));
      spawnSparks(refs, ends.to.x, ends.to.y, hit.side === "red" ? "red" : "blue", 8 + power * 12);
      spawnSparks(refs, ends.to.x, ends.to.y, "white", power * 6);
      refs.trauma = Math.min(1, refs.trauma + 0.15 + power * 0.15);
      refs.knock[hit.side] = refs.clock + (KNOCK_MS[hit.side] || 500);
      if (power === 1) refs.camTarget = { dx: 0, dy: 0, z: 1 };
      continue;
    }
    if (hit.kind === "explode") {
      // The loser explodes into particles. Red wins also drain
      // the color from the stage; the vault already cracked when
      // the secret was read.
      spawnSparks(refs, ends.to.x, ends.to.y, hit.side === "red" ? "red" : "blue", 34);
      spawnSparks(refs, ends.to.x, ends.to.y, "white", 26);
      refs.trauma = 1;
      refs.knock[hit.side] = refs.clock + (KNOCK_MS[hit.side] || 500);
      refs.hitstopFrames = Math.max(refs.hitstopFrames || 0, 12);
      refs.fx.push({
        kind: "blast", x: ends.to.x, y: ends.to.y, t0: refs.clock, dur: 900,
        color: hit.side === "red" ? "255,110,100" : "140,210,255",
      });
      refs.dead = {
        side: hit.side,
        at: refs.clock,
        mode: hit.side === "red" ? "dissolve" : "shatter",
      };
      if (hit.side === "blue") refs.desatTarget = 0.85;
      refs.camTarget = winnerBeatTarget(
        refs.stage, hit.side === "red" ? "blue" : "red");
      continue;
    }
    const size = hit.size || "standard";
    const heavy = size === "heavy" || size === "large";
    const titan = size === "titan";
    if (titan) spawnSparks(refs, ends.to.x, ends.to.y, "white", 16);
    spawnSparks(refs, ends.to.x, ends.to.y, hit.side === "red" ? "blue" : "red", titan ? 40 : heavy ? 26 : size === "small" ? 10 : 16);
    refs.trauma = Math.min(1, refs.trauma + (titan ? 0.65 : heavy ? 0.5 : size === "small" ? 0.15 : 0.3));
    refs.knock[hit.side] = refs.clock + (KNOCK_MS[hit.side] || 500);
    refs.hitstopFrames = Math.max(refs.hitstopFrames || 0, hitstopFor(size));
  }
  if (dirty) refs.impacts = due.filter((hit) => !hit.fired || refs.clock - hit.at < 500);
}

function drawStage(refs) {
  const ctx = refs.ctx;
  if (!ctx || !refs.stage) return;
  fireImpacts(refs);
  ensureFrame(refs);
  buildStaticLayers(refs);
  buildSprites(refs);
  buildEntityBodies(refs);
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

// Full rebuild after a blank probe or a lost context: drop every
// cached surface and repaint from scratch.
function rebuildStage(refs, why) {
  if (!refs || !refs.stage) return;
  refs.cache = {};
  refs.frame = null;
  refs.frameCtx = null;
  refs.lastRebuild = refs.clock;
  refs.blankRecoveries = (refs.blankRecoveries || 0) + 1;
  refs.recoveryNote = String(why || "rebuild").slice(0, 80);
  drawStage(refs);
  updateStageDebug(refs, true);
}

// Expected-content probe: sample pixels where bright content must be
// (entity cores, lane accents). True when every sample reads as the
// empty background, meaning the presented frame went blank.
export function probeStageContent(refs) {
  const ctx = refs && refs.ctx;
  const stage = refs && refs.stage;
  if (!ctx || !stage || stage.mode !== "side") return false;
  const { cw, ch, dpr } = refs.geom;
  if (!cw || !ch) return false;
  const pts = [
    [stage.entities.intrusion.x, stage.entities.intrusion.y],
    [stage.entities.guardian.x, stage.entities.guardian.y],
    [stage.lanes.red.x + 2, stage.lanes.red.y + stage.lanes.red.h / 2],
    [stage.lanes.blue.x + stage.lanes.blue.w - 2, stage.lanes.blue.y + stage.lanes.blue.h / 2],
  ];
  try {
    for (const [x, y] of pts) {
      const px = Math.max(0, Math.min(cw * dpr - 1, Math.round(x * dpr)));
      const py = Math.max(0, Math.min(ch * dpr - 1, Math.round(y * dpr)));
      const cell = ctx.getImageData(px, py, 1, 1).data;
      // Empty background is INK (10, 12, 16); any lit pixel passes.
      if (cell[0] > 28 || cell[1] > 28 || cell[2] > 34) return false;
    }
    return true;
  } catch {
    return false;
  }
}

function maybeProbeBlank(refs) {
  refs.probeTick = (refs.probeTick || 0) + 1;
  if (refs.probeTick % 20 !== 0) return;
  if (refs.clock - (refs.lastRebuild || 0) < 2000) return;
  if (probeStageContent(refs)) rebuildStage(refs, "blank probe");
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
  paintFx(refs, ctx);
  paintLanes(refs, ctx);
  paintParticles(refs, ctx);
  ctx.translate(-sx, -sy);
  paintOverlays(refs, ctx);
  if (refs.desat > 0.01) {
    // Red-win aftermath: drain the color. A gray wash through the
    // saturation blend desaturates in one fill, no filter passes.
    ctx.save();
    ctx.globalCompositeOperation = "saturation";
    ctx.fillStyle = `rgba(128,128,128,${(Math.min(1, refs.desat) * 0.9).toFixed(2)})`;
    ctx.fillRect(0, 0, cw, ch);
    ctx.restore();
  }
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
  // One cached vertical fade for all three beams: the stops never
  // change, so rebuilding it per beam per frame is pure waste.
  // The gradient lives in user space, so it survives the shake
  // translate; it only rebuilds when the height changes.
  if (!refs.beamGrad || refs.beamGradH !== ch) {
    const grad = ctx.createLinearGradient(0, 0, 0, ch);
    grad.addColorStop(0, "rgba(127,212,255,0.10)");
    grad.addColorStop(1, "rgba(127,212,255,0)");
    refs.beamGrad = grad;
    refs.beamGradH = ch;
  }
  for (let i = 0; i < 3; i += 1) {
    const fx = stage.band.x0 + bandW * (0.22 + i * 0.28);
    const sway = Math.sin(t * 0.24 + i * 2.1) * 24;
    const topW = 40 + i * 14;
    ctx.fillStyle = refs.beamGrad;
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
  const sprites = refs.sprites || {};
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
    // State-tinted floor reflection from a cached glow sprite,
    // squashed flat: no per-host radial gradient per frame.
    const mirrorSprite = state === "compromised" ? sprites.red
      : state === "isolated" ? sprites.blue
      : state === "probed" ? sprites.white
      : state === "signed-in" ? sprites.gold : sprites.cyan;
    if (mirrorSprite) {
      ctx.save();
      ctx.translate(at.x, at.y + r * 1.5);
      ctx.scale(1, 0.31);
      ctx.globalAlpha = 0.55;
      const mr = r * 1.6;
      ctx.drawImage(mirrorSprite, -mr, -mr, mr * 2, mr * 2);
      ctx.restore();
    }
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

// Name-tag placement for one host block, pure so node tests it.
// Every host keeps its tag at all times: blocks too small to hold
// letters draw the name under the node instead of hiding it.
export function hostLabelLayout(r) {
  if (r < 14) return { place: "below", fs: 11, dy: Math.round(r + 14) };
  return {
    place: "inside",
    fs: Math.round(Math.min(24, Math.max(9, r * 0.5))),
    dy: 1,
  };
}

function paintHostLabel(refs, ctx, id, x, y, r, jewel) {
  const label = (refs.labels && refs.labels[id]) || String(id).toUpperCase().slice(0, 7);
  const role = (refs.roles && refs.roles[id]) || "";
  const lay = hostLabelLayout(r);
  const fs = lay.fs;
  ctx.save();
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `900 ${fs}px ${DISP}`;
  ctx.lineWidth = Math.max(2, fs * 0.16);
  ctx.strokeStyle = "rgba(5,7,12,0.9)";
  ctx.strokeText(label, x, y + lay.dy);
  ctx.fillStyle = BONE;
  ctx.fillText(label, x, y + lay.dy);
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

// Deterministic hash for debris scatter: no RNG in the paint path.
function fhash(n) {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

function paintEntities(refs, ctx) {
  const slots = refs.stage.entities;
  const t = refs.clock / 1000;
  const red = slots.intrusion;
  const blue = slots.guardian;
  const redHot = refs.clock < refs.surge.red ? (refs.surge.red - refs.clock) / 900 : 0;
  const blueHot = refs.clock < refs.surge.blue ? (refs.surge.blue - refs.clock) / 900 : 0;
  const redKnock = refs.clock < refs.knock.red ? (refs.knock.red - refs.clock) / 450 : 0;
  const blueKnock = refs.clock < refs.knock.blue ? (refs.knock.blue - refs.clock) / 600 : 0;
  // Finish charge: the winner swells and brightens before firing.
  const ch = refs.charge;
  const chargeK = ch && refs.clock >= ch.from && refs.clock < ch.until
    ? (refs.clock - ch.from) / Math.max(1, ch.until - ch.from) : 0;
  const chargeRed = chargeK > 0 && ch.side === "red";
  const chargeBlue = chargeK > 0 && ch.side === "blue";
  const redFlare = chargeRed ? Math.max(redHot, chargeK) : redHot;
  const blueFlare = chargeBlue ? Math.max(blueHot, chargeK) : blueHot;
  const level = Math.min(1, refs.intrusion + redHot * 0.25);
  const energy = Math.min(1.15, refs.guardian + blueHot * 0.35);
  const sway = refs.cam.dx * 0.12;
  const redRN = red.r * (0.68 + 0.42 * refs.intrusion) * (chargeRed ? 1 + 0.35 * chargeK : 1);
  const blueRN = blue.r * (chargeBlue ? 1 + 0.35 * chargeK : 1);
  let redAlpha = 1;
  let blueAlpha = 1;
  let redY = red.y;
  const dead = refs.dead;
  if (dead) {
    // The exit holds its size and only fades: freeze the radius at
    // death so the sprite never thins into a blotch mid-melt.
    if (dead.r == null) dead.r = dead.side === "red" ? redRN : blueRN;
    const a = meltSpriteAlpha(refs.clock - dead.at);
    if (dead.side === "red") {
      redAlpha = a;
      redY = red.y - (1 - a) * 36;
    } else {
      blueAlpha = a;
    }
  }
  const redR = dead && dead.side === "red" && dead.r != null ? dead.r : redRN;
  const blueR = dead && dead.side === "blue" && dead.r != null ? dead.r : blueRN;
  if (redAlpha > 0) {
    ctx.save();
    ctx.globalAlpha = redAlpha;
    paintIntrusion(ctx, {
      sprites: refs.sprites || {}, bodies: refs.bodies || {},
      swayX: sway, flare: redFlare, recoil: redKnock, burst: redFlare,
    }, red.x, redY, redR, level, t);
    ctx.restore();
  }
  if (blueAlpha > 0) {
    ctx.save();
    ctx.globalAlpha = blueAlpha;
    paintGuardian(ctx, {
      sprites: refs.sprites || {}, bodies: refs.bodies || {},
      swayX: sway, flare: blueFlare, recoil: blueKnock, burst: 0,
    }, blue.x, blue.y, blueR, energy, t);
    ctx.restore();
  }
  if (dead && refs.clock > dead.at) {
    // The loser turns into code: its own sprite pixels peel off as
    // glyphs while the body fades beneath. Masks build once.
    buildMeltMasks(refs);
    const age = refs.clock - dead.at;
    if (dead.side === "red") {
      paintMeltGlyphs(refs, ctx, "red", red.x, redY, redR, age);
    } else {
      paintMeltGlyphs(refs, ctx, "blue", blue.x, blue.y, blueR, age);
      // Shattered: opaque plate debris tumbles while the body fades
      // and keeps going after it is gone.
      paintShatterDebris(ctx, blue.x, blue.y, blue.r, age);
    }
  }
}

// Guardian wreckage: pale plates tumbling outward, fading over
// about two seconds. Deterministic scatter, opaque fills.
function paintShatterDebris(ctx, x, y, r, age) {
  const k = Math.max(0, Math.min(1, age / (MELT_DELAY_MS + MELT_SPAN_MS)));
  if (k >= 1) return;
  ctx.save();
  for (let i = 0; i < 12; i += 1) {
    const a = fhash(i + 1) * 6.283;
    const dist = (30 + fhash(i + 21) * 170) * (1 - (1 - k) * (1 - k)) * Math.max(0.4, r / 82);
    const px = x + Math.cos(a) * dist;
    const py = y + Math.sin(a) * dist * 0.7 + k * k * 60;
    const w = (8 + fhash(i + 41) * 14) * Math.max(0.4, r / 82);
    ctx.save();
    ctx.translate(px, py);
    ctx.rotate(k * (2 + fhash(i + 61) * 4) + fhash(i + 81) * 6.283);
    ctx.globalAlpha = 0.95 * (1 - k);
    ctx.fillStyle = i % 3 === 0 ? "#e8f4ff" : i % 3 === 1 ? "#9fd0f5" : "#5d7fa6";
    ctx.beginPath();
    ctx.moveTo(w / 2, 0);
    ctx.lineTo(0, -w * 0.3);
    ctx.lineTo(-w / 2, 0);
    ctx.lineTo(0, w * 0.3);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
  ctx.restore();
}

// Loser melt: the dead sprite's own sampled pixels peel off as 0/1
// glyphs, outer pixels first, after the flare peaks. Slots sit
// where the body drew (bs matches the splash draw size), rise and
// spread for most of the span, and flip digits every 120ms. Two
// passes: faint dark halos in source-over (additive would swallow
// black), then white-hot fills with "lighter". LOW strides the
// same cached mask down to its cap.
function paintMeltGlyphs(refs, ctx, side, x, y, r, age) {
  const pts = (refs.meltMasks || {})[side] || [];
  if (pts.length === 0 || age < MELT_DELAY_MS) return;
  const p = Math.max(0, Math.min(1, (age - MELT_DELAY_MS) / MELT_SPAN_MS));
  if (p <= 0) return;
  const stride = meltStride(pts.length, refs.lowDetail);
  const count = Math.ceil(pts.length / stride);
  // Long overlapping lives: starts stagger outer-first across the
  // first stretch while each glyph lives most of the span, so the
  // peel reads rim-to-core and the cloud stays dense to the end.
  const LIFE = 0.85;
  const rr = Math.max(8, r);
  const bs = rr * 2.5;
  const live = [];
  for (let j = 0; j < count; j += 1) {
    const lp = (p - (j / count) * (1 - LIFE)) / LIFE;
    if (lp <= 0 || lp >= 1) continue;
    const s = pts[j * stride];
    const drift = meltDrift(j, lp, rr, s.x, s.y);
    live.push({
      s,
      lp,
      ch: meltChar(j, refs.clock),
      gx: x + s.x * bs + drift.dx,
      gy: y + s.y * bs + drift.dy,
    });
  }
  if (live.length === 0) return;
  const size = meltGlyphPx(rr);
  ctx.save();
  ctx.font = `${size}px ${MONO}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = Math.max(2, Math.round(size * 0.25));
  ctx.lineJoin = "round";
  ctx.strokeStyle = "rgba(2,4,8,0.8)";
  for (const g of live) {
    ctx.globalAlpha = meltHaloAlpha(g.lp);
    ctx.strokeText(g.ch, g.gx, g.gy);
  }
  ctx.globalCompositeOperation = "lighter";
  for (const g of live) {
    ctx.globalAlpha = meltGlyphAlpha(g.lp);
    ctx.fillStyle = g.s.fill;
    ctx.fillText(g.ch, g.gx, g.gy);
  }
  ctx.restore();
}

// The Intrusion: the hero splash as its body, drawn large and
// additive with a soft mask, with rotating shard rings, orbiting
// code glyphs, and glitch slices on top. Idle breathing, flare and
// recoil on events. Abstract light and code only.
export function paintIntrusion(ctx, env, x, y, r, level, t) {
  const rr = Math.max(8, r);
  const lvl = Math.max(0, Math.min(1, level));
  const mood = env || {};
  const sprites = mood.sprites || {};
  const body = (mood.bodies || {}).intrusion;
  const violence = 0.25 + lvl * 1.6;
  const step = Math.floor(t * 24);
  const rnd = (n) => {
    const s = Math.sin(step * 12.9898 + n * 78.233) * 43758.5453;
    return s - Math.floor(s);
  };
  const recoil = Math.max(0, Math.min(1, mood.recoil || 0));
  const burst = Math.max(0, Math.min(1, mood.burst || 0));
  const breath = 1 + Math.sin(t * 0.9) * 0.025;
  const jx = (rnd(1) - 0.5) * rr * 0.08 * violence - recoil * rr * 0.35;
  const jy = (rnd(2) - 0.5) * rr * 0.08 * violence;
  const sway = (mood.swayX || 0) + Math.sin(t * 0.5) * rr * 0.04;
  ctx.save();
  ctx.translate(x + jx + sway, y + Math.sin(t * 1.1) * rr * 0.04 + jy);
  ctx.scale(breath, breath);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  drawGlow(ctx, sprites.intrusion, 0, 0, rr * 2.4, 0.45 + lvl * 0.55);
  ctx.restore();
  // Splash body, large and additive; the baked mask drops the dark
  // ground so only the being remains.
  if (body) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = 0.95;
    const bs = rr * 2.5;
    ctx.drawImage(body, -bs / 2, -bs / 2, bs, bs);
    ctx.restore();
  } else {
    // Procedural fallback when the art is missing.
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
  }
  // Layered shard rings over the body, each turning at its own speed.
  const rings = [
    { n: 7, rad: 1.1, size: 0.17, speed: 0.55, wob: 0.1 },
    { n: 5, rad: 0.76, size: 0.15, speed: -0.38, wob: 0.14 },
    { n: 4, rad: 0.48, size: 0.13, speed: 0.9, wob: 0.08 },
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
  // White-hot heart with violent flicker.
  const flick = 1 + (rnd(3) - 0.5) * 0.5 * violence + Math.sin(t * 47) * 0.06 * violence;
  const coreR = Math.max(2, rr * 0.2 * flick);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  drawGlow(ctx, sprites.white, 0, 0, coreR * 3.2, 0.5 + lvl * 0.4);
  ctx.fillStyle = "#fff6f2";
  ctx.beginPath();
  ctx.arc(0, 0, coreR, 0, 6.283);
  ctx.fill();
  ctx.restore();
  // Horizontal glitch slices across the body, bursting after events.
  const slices = 2 + Math.round(lvl * 3) + Math.round(burst * 3);
  for (let i = 0; i < slices; i += 1) {
    const gy = -rr * 1.2 + ((i + rnd(10 + i) * 0.6) / slices) * rr * 2.4;
    const gh = Math.max(1, rr * (0.02 + rnd(20 + i) * 0.03));
    const off = (rnd(30 + i) - 0.5) * rr * 0.5 * (0.3 + lvl + burst * 0.8);
    ctx.fillStyle = `rgba(255,70,60,${(0.25 + lvl * 0.4 + burst * 0.25).toFixed(2)})`;
    ctx.fillRect(-rr * 1.5 + off, gy, rr * 3, gh);
    ctx.fillStyle = "rgba(127,212,255,0.18)";
    ctx.fillRect(-rr * 1.5 + off * 0.5, gy + gh, rr * 3, 1);
  }
  // Orbiting code glyphs.
  const glyphs = CODE_GLYPHS;
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
  // Event flare: a white flash over the body.
  const flare = Math.max(0, Math.min(1, mood.flare || 0));
  if (flare > 0.02) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    drawGlow(ctx, sprites.white, 0, 0, rr * 1.7, flare * 0.55);
    ctx.restore();
  }
  ctx.restore();
}

// The Guardian: the hero splash as its body, drawn large and
// additive with a soft mask, with segmented counter-turning rings,
// orbiting shield plates, and a calm core pulse. Idle breathing,
// flare and recoil on events. Abstract light and code only.
export function paintGuardian(ctx, env, x, y, r, energy, t) {
  const rr = Math.max(8, r);
  const e = Math.max(0.15, Math.min(1.2, energy));
  const mood = env || {};
  const sp = mood.sprites || {};
  const body = (mood.bodies || {}).guardian;
  const recoil = Math.max(0, Math.min(1, mood.recoil || 0));
  const breathe = 1 + Math.sin(t * 1.1) * 0.025;
  const sway = (mood.swayX || 0) + Math.sin(t * 0.45 + 1.2) * rr * 0.04;
  ctx.save();
  ctx.translate(x + sway + recoil * rr * 0.35, y + Math.sin(t * 0.9) * rr * 0.03);
  ctx.scale(breathe, breathe);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  drawGlow(ctx, sp.guardian, 0, 0, rr * 2.4, 0.4 + e * 0.55);
  ctx.restore();
  // Splash body, large and additive; the baked mask drops the dark
  // ground so only the sentinel remains.
  if (body) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = 0.95;
    const bs = rr * 2.5;
    ctx.drawImage(body, -bs / 2, -bs / 2, bs, bs);
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
  // Event flare: a white flash over the body.
  const flare = Math.max(0, Math.min(1, mood.flare || 0));
  if (flare > 0.02) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    drawGlow(ctx, sp.white, 0, 0, rr * 1.7, flare * 0.55);
    ctx.restore();
  }
  ctx.restore();
}

// Transient event effects: rings, sparks, projectiles, callouts. Entries
// resolve host positions at paint time so the camera carries them.
// Flights paint above everything else in the effects layer:
// the kinetic callout plate sits on the lane apex and used to
// cover single lances mid-flight, leaving only their glow and
// embers visible. Host marks, plates, and seals paint first,
// then projectiles and finish beams over them, each pass keeping
// its push order.
const TOP_FX = new Set(["lance", "volley", "orb", "beam"]);

function paintFx(refs, ctx) {
  paintFxPass(refs, ctx, false);
  paintFxPass(refs, ctx, true);
  paintFxSparks(refs, ctx);
}

function paintFxPass(refs, ctx, top) {
  for (const fx of refs.fx) {
    if (TOP_FX.has(fx.kind) !== top) continue;
    const age = refs.clock - fx.t0;
    if (age < 0 || age > fx.dur + 200) continue;
    const p = Math.max(0, Math.min(1, age / fx.dur));
    switch (fx.kind) {
      case "ring":
        paintFxRing(refs, ctx, fx, age, fxPoint(refs, fx));
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
      case "scanring":
        paintFxScanring(refs, ctx, fx, p);
        break;
      case "flare":
        paintFxFlare(refs, ctx, fx, p);
        break;
      case "slam":
        paintFxSlam(refs, ctx, fx, p);
        break;
      case "sweep":
        paintFxSweep(refs, ctx, fx, p);
        break;
      case "reconnect":
        paintFxReconnect(refs, ctx, fx, p);
        break;
      case "callout":
        paintFxCallout(refs, ctx, fx, p);
        break;
      case "lance":
      case "volley":
      case "orb":
        paintProjectile(refs, ctx, fx, age);
        break;
      case "beam":
        paintBeam(refs, ctx, fx, age);
        break;
      case "seal":
        paintSeal(refs, ctx, fx, age);
        break;
      case "blast":
        paintBlast(refs, ctx, fx, age);
        break;
      default:
        break;
    }
  }
}

function paintFxSparks(refs, ctx) {
  if (refs.sparks.length > 0) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.lineCap = "round";
    for (const s of refs.sparks) {
      const a = Math.max(0, Math.min(1, s.life / 500));
      ctx.strokeStyle = s.color === "red" ? `rgba(255,120,100,${a.toFixed(2)})`
        : s.color === "blue" ? `rgba(140,200,255,${a.toFixed(2)})`
          : s.color === "gray" ? `rgba(150,155,168,${a.toFixed(2)})`
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

function fxTex(refs, key) {
  const tex = refs.art && refs.art[key];
  return tex && tex.width ? tex : null;
}

function paintFxRing(refs, ctx, fx, age, pt) {
  if (!pt) return;
  const p = Math.max(0, Math.min(1, age / fx.dur));
  const r = (fx.r0 || 8) + ((fx.r1 || 64) - (fx.r0 || 8)) * (1 - (1 - p) * (1 - p));
  const tex = refs ? fxTex(refs, "ring") : null;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  if (tex) {
    ctx.globalAlpha = 0.8 * (1 - p);
    const s = r * 2.6;
    ctx.drawImage(tex, pt.x - s / 2, pt.y - s / 2, s, s);
    ctx.globalAlpha = 1;
  }
  ctx.strokeStyle = `rgba(${fx.color || "255,255,255"},${(0.85 * (1 - p)).toFixed(2)})`;
  ctx.lineWidth = 3 * (1 - p) + 1;
  ctx.beginPath();
  ctx.arc(pt.x, pt.y, r, 0, 6.283);
  ctx.stroke();
  ctx.restore();
}

// Scan: a sweep ring crosses the target host itself. Nothing is
// drawn from the entities to the hosts; the exchange between the
// entities travels as projectiles instead.
function paintFxFan(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  paintFxRing(refs, ctx, { ...fx, r0: at.r + 4, r1: at.r + 34, color: "255,90,80" },
    p * fx.dur, at);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.strokeStyle = `rgba(255,150,130,${(0.8 * (1 - p)).toFixed(2)})`;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(at.x, at.y, at.r + 12, p * 10, p * 10 + 2.1);
  ctx.stroke();
  ctx.restore();
}

// Probe: a flickering touch flares on the target host itself.
function paintFxNeedle(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  const flick = 0.6 + 0.4 * Math.sin(p * 40);
  if (p > 0.2) {
    const q = (p - 0.2) / 0.8;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.fillStyle = `rgba(255,160,140,${(q * 1.2 * flick).toFixed(2)})`;
    ctx.beginPath();
    ctx.arc(at.x, at.y, 4 + q * 10, 0, 6.283);
    ctx.fill();
    ctx.restore();
  }
}

// Credential guess: the attempt breaks on the host itself.
function paintFxKeys(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  const tex = fxTex(refs, "shard");
  // Shard chips scatter off the host's edge and fall away.
  for (let i = 0; i < 3; i += 1) {
    const a = -Math.PI / 2 + (i - 1) * 0.7;
    const d = at.r * 0.6 + p * at.r * 1.1;
    const x = at.x + Math.cos(a) * d;
    const y = at.y + Math.sin(a) * d + p * p * 26;
    const s = 6 * (1 - p * 0.5);
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(p * 9 + i);
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = 0.9 * (1 - p);
    if (tex) {
      ctx.drawImage(tex, -s * 1.6, -s * 1.6, s * 3.2, s * 3.2);
    } else {
      ctx.fillStyle = "rgba(255,150,130,0.9)";
      ctx.beginPath();
      ctx.moveTo(0, -s);
      ctx.lineTo(s * 0.7, s * 0.6);
      ctx.lineTo(-s * 0.7, s * 0.6);
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();
  }
  if (p > 0.15 && p < 0.85) {
    ctx.save();
    ctx.globalAlpha = (0.9 * (1 - (p - 0.15) / 0.7)).toFixed(2);
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

// Sign-in: the host itself flares where the session lands. Grab:
// the jewel snatch, brighter and wider, still on the host alone.
function paintFxPierce(refs, ctx, fx, p, grab) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  if (p > 0.15) {
    const q = (p - 0.15) / 0.85;
    const sprites = refs.sprites || {};
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    drawGlow(ctx, grab ? sprites.white : sprites.red, at.x, at.y,
      at.r * (grab ? 2.4 : 1.8) * (0.5 + q), grab ? 0.9 : 0.75 * (1 - q * 0.5));
    ctx.restore();
    paintFxRing(refs, ctx, { ...fx, r0: at.r, r1: at.r + (grab ? 90 : 50), color: "255,110,100" },
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

// File read: data motes lift off the host and dissolve in place.
function paintFxSiphon(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (let i = 0; i < 6; i += 1) {
    const q = ((p * 1.4 - i / 6) % 1 + 1) % 1;
    const x = at.x + Math.sin(i * 2.4) * at.r * 0.7 * q;
    const y = at.y - q * at.r * 1.6;
    ctx.fillStyle = `rgba(255,150,130,${(0.85 * Math.sin(q * Math.PI)).toFixed(2)})`;
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, 6.283);
    ctx.fill();
  }
  ctx.restore();
  paintFxRing(refs, ctx, { ...fx, r0: at.r + 2, r1: at.r + 30, color: "255,150,130" }, p * fx.dur, at);
}

// Log review: scanning rings sweep the host itself.
function paintFxScanring(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  paintFxRing(refs, ctx, { ...fx, r0: at.r + 4, r1: at.r + 70, color: "127,212,255" }, p * fx.dur, at);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.strokeStyle = `rgba(160,230,255,${(0.8 * (1 - p)).toFixed(2)})`;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(at.x, at.y, at.r + 16, p * 12, p * 12 + 1.8);
  ctx.stroke();
  ctx.restore();
}

// Report: the Guardian flares bright.
function paintFxFlare(refs, ctx, fx, p) {
  const at = entityCore(refs, "blue");
  const sprites = refs.sprites || {};
  const tex = fxTex(refs, "flare");
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  drawGlow(ctx, sprites.cyan, at.x, at.y, at.r * 2.2 * (0.6 + p), 0.85 * (1 - p));
  if (tex) {
    ctx.globalAlpha = 0.9 * (1 - p);
    const s = at.r * 2.6 * (0.7 + p * 0.8);
    ctx.drawImage(tex, at.x - s / 2, at.y - s / 2, s, s);
    ctx.globalAlpha = 1;
  } else {
    drawGlow(ctx, sprites.white, at.x, at.y, at.r * 0.9, 0.9 * (1 - p));
  }
  ctx.restore();
}

// Isolate: a barrier ring slams down onto the host.
function paintFxSlam(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  const r = at.r + 8 + (1 - Math.min(1, p * 1.4)) * 70;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.strokeStyle = `rgba(120,190,255,${(0.9 * (1 - p * 0.4)).toFixed(2)})`;
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.arc(at.x, at.y, Math.max(4, r), 0, 6.283);
  ctx.stroke();
  if (p > 0.6) {
    const q = (p - 0.6) / 0.4;
    ctx.strokeStyle = `rgba(230,245,255,${(0.8 * (1 - q)).toFixed(2)})`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(at.x, at.y, at.r + 8 + q * 30, 0, 6.283);
    ctx.stroke();
  }
  // Contact flash from the flare texture.
  const flare = fxTex(refs, "flare");
  if (flare && p > 0.55) {
    const q = Math.min(1, (p - 0.55) / 0.45);
    ctx.globalAlpha = 0.8 * (1 - q);
    const s = at.r * 2.4;
    ctx.drawImage(flare, at.x - s / 2, at.y - s / 2, s, s);
    ctx.globalAlpha = 1;
  }
  ctx.restore();
}

// Patch: a green repair sweep crosses the host.
function paintFxSweep(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  const x = at.x - at.r * 1.4 + p * at.r * 2.8;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  const band = ctx.createLinearGradient(x - at.r * 0.5, 0, x + at.r * 0.5, 0);
  band.addColorStop(0, "rgba(120,220,150,0)");
  band.addColorStop(0.5, `rgba(140,235,170,${(0.55 * Math.sin(p * Math.PI)).toFixed(2)})`);
  band.addColorStop(1, "rgba(120,220,150,0)");
  ctx.fillStyle = band;
  ctx.fillRect(x - at.r * 0.5, at.y - at.r * 1.4, at.r, at.r * 2.8);
  ctx.strokeStyle = `rgba(150,240,180,${(0.7 * (1 - p)).toFixed(2)})`;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x, at.y - at.r * 1.3);
  ctx.lineTo(x, at.y + at.r * 1.3);
  ctx.stroke();
  ctx.restore();
}

// Restore: reconnect pulses ripple out of the host.
function paintFxReconnect(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  paintFxRing(refs, ctx, { ...fx, r0: at.r, r1: at.r + 60, color: "127,212,255" }, p * fx.dur, at);
  if (p > 0.35) {
    paintFxRing(refs, ctx, { ...fx, r0: at.r, r1: at.r + 44, color: "200,235,255" },
      (p - 0.35) * fx.dur, at);
  }
  const sprites = refs.sprites || {};
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  drawGlow(ctx, sprites.cyan, at.x, at.y, at.r * 1.6, 0.5 * Math.sin(p * Math.PI));
  ctx.restore();
}

// Kinetic callout: technique name over the plain-English line,
// floating above the target with a fade in and out.
function paintFxCallout(refs, ctx, fx, p) {
  const stage = refs.stage;
  const at = fx.host ? hostScreen(refs, fx.host) : null;
  const anchor = at || entityCore(refs, fx.side === "blue" ? "blue" : "red");
  const ar = at ? at.r : 40;
  const x = Math.max(stage.band.x0 + 120, Math.min(stage.band.x1 - 120, anchor.x));
  const y = Math.max(stage.area.top + 34, Math.min(stage.area.bottom - 76, anchor.y - ar - 60)) - p * 8;
  const tech = String(fx.tech || "").toUpperCase();
  let line = String(fx.line || "");
  ctx.save();
  ctx.font = `900 15px ${DISP}`;
  const techW = ctx.measureText(tech).width;
  ctx.font = `400 11px ${MONO}`;
  while (line.length > 8 && ctx.measureText(line).width > 300) line = `${line.slice(0, -5)}…`;
  const lineW = ctx.measureText(line).width;
  const w = Math.min(stage.band.x1 - stage.band.x0 - 40, Math.max(techW, lineW) + 28);
  const h = 46;
  const fade = p < 0.15 ? p / 0.15 : p > 0.7 ? Math.max(0, 1 - (p - 0.7) / 0.3) : 1;
  ctx.globalAlpha = fade;
  ctx.fillStyle = "rgba(6,8,12,0.85)";
  roundRectPath(ctx, x - w / 2, y - h / 2, w, h, 8);
  ctx.fill();
  const accent = fx.failed ? "#8a8f9a" : fx.side === "blue" ? "#8fb8ff" : "#ff7a5c";
  ctx.fillStyle = accent;
  ctx.fillRect(x - w / 2, y - h / 2 + 8, 3, h - 16);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `900 15px ${DISP}`;
  ctx.fillStyle = accent;
  ctx.fillText(tech, x + 4, y - 10, w - 20);
  ctx.font = `400 11px ${MONO}`;
  ctx.fillStyle = BONE;
  ctx.fillText(line, x + 4, y + 11, w - 20);
  ctx.restore();
}

// Body scale by move significance: even the small volley shards
// read at full size, heavy lances and large orbs fly much bigger.
export const PROJ_SCALE = { small: 1, standard: 1.3, heavy: 1.8, large: 1.8, titan: 5.5 };
export const LANCE_LEN = 46;
export const LANCE_WID = 13;
export const ORB_R = 15;

// Body size in stage px for a size class at a stage scale: the
// lance length, the lance width, and the orb diameter. Pure, for
// tests and the lane-pixel harness. Small stays above 28px at
// full stage scale so every move reads at normal size.
export function projBody(size, stage) {
  const s = (PROJ_SCALE[size] || 1) * (Number(stage) || 1);
  return { lanceLen: LANCE_LEN * s, lanceWid: LANCE_WID * s, orbD: ORB_R * 2 * s };
}

// Trail reach as a fraction of the lane: at least three body
// lengths of streak behind the head, clamped to the lane. Pure.
export function trailSpan(bodyLen, laneLen) {
  const dist = Math.max(1, Number(laneLen) || 1);
  const want = (3.2 * (Number(bodyLen) || 0)) / dist;
  return Math.min(0.6, Math.max(0.15, want));
}

// Impact flash hold over the strike window: near-full brightness
// for the first 250ms of the 350ms window, then a quick decay.
// Pure; iq runs 0 at the strike to 1 at the window end.
export function flashHold(iq) {
  const q = Math.min(1, Math.max(0, Number(iq) || 0));
  if (q <= 0.72) return 1;
  return Math.max(0, 1 - (q - 0.72) / 0.28);
}

// Finish beam: shafts of light burst outward from the loser in
// all directions. Each of the three is wider and brighter than
// the last, over a white-hot core flash and an expanding ring.
function paintBeam(refs, ctx, fx, age) {
  const red = fx.side !== "blue";
  const slots = refs.stage.entities;
  const core = red ? slots.intrusion : slots.guardian;
  const stage = projStageScale(slots.intrusion.r, slots.guardian.r);
  const power = Math.max(1, Math.min(3, Number(fx.power) || 1));
  const p = Math.max(0, Math.min(1, age / Math.max(1, fx.dur)));
  const fade = 1 - p;
  const s = stage * (0.9 + power * 0.45);
  const tint = red ? "255,110,90" : "140,210,255";
  const sprites = refs.sprites || {};
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  drawGlow(ctx, sprites.white, core.x, core.y, 90 * s * (1 - p * 0.5), 0.9 * fade);
  drawGlow(ctx, red ? sprites.red : sprites.cyan, core.x, core.y, 130 * s, 0.6 * fade);
  const shafts = 5 + power * 2;
  const len = core.r * (2.6 + power * 0.7);
  const spin = age / 900;
  const w = (3 + power * 2.5) * stage * fade + 1;
  for (let i = 0; i < shafts; i += 1) {
    const a = spin + (i / shafts) * 6.283;
    const x0 = core.x + Math.cos(a) * core.r * 0.5;
    const y0 = core.y + Math.sin(a) * core.r * 0.5;
    const x1 = core.x + Math.cos(a) * len;
    const y1 = core.y + Math.sin(a) * len;
    ctx.strokeStyle = `rgba(${tint},${(0.7 * fade).toFixed(2)})`;
    ctx.lineWidth = w * 2.2;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
    ctx.strokeStyle = `rgba(255,255,255,${(0.8 * fade).toFixed(2)})`;
    ctx.lineWidth = w;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
  }
  paintFxRing(refs, ctx, { r0: 10 * s, r1: (60 + power * 30) * s, color: tint, dur: fx.dur }, age, core);
  ctx.restore();
}

// Blue-win seal: pale rings cinch onto the Intrusion while three
// shafts converge on it from the Guardian's side, then a white
// flash as the dissolve begins.
function paintSeal(refs, ctx, fx, age) {
  const slots = refs.stage.entities;
  const red = { x: slots.intrusion.x, y: slots.intrusion.y, r: slots.intrusion.r };
  const blue = { x: slots.guardian.x, y: slots.guardian.y, r: slots.guardian.r };
  const p = Math.max(0, Math.min(1, age / Math.max(1, fx.dur)));
  const sprites = refs.sprites || {};
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  for (let i = 0; i < 2; i += 1) {
    const rr = red.r * (1.7 - p * 0.8) + i * 14;
    ctx.strokeStyle = `rgba(230,245,255,${(0.75 * (1 - p * 0.4)).toFixed(2)})`;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(red.x, red.y, Math.max(4, rr), 0, 6.283);
    ctx.stroke();
  }
  for (let i = 0; i < 3; i += 1) {
    const a = Math.PI + (i - 1) * 0.38;
    const sx = blue.x + Math.cos(a) * blue.r * 0.6;
    const sy = blue.y + Math.sin(a) * blue.r * 0.6;
    ctx.strokeStyle = `rgba(190,235,255,${(0.35 + p * 0.5).toFixed(2)})`;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.lineTo(red.x, red.y);
    ctx.stroke();
  }
  ctx.strokeStyle = `rgba(255,255,255,${(0.5 + p * 0.4).toFixed(2)})`;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(blue.x - blue.r * 0.6, blue.y);
  ctx.lineTo(red.x, red.y);
  ctx.stroke();
  if (p > 0.75) {
    const q = (p - 0.75) / 0.25;
    drawGlow(ctx, sprites.white, red.x, red.y, red.r * (1 + q), 0.85 * q);
  }
  ctx.restore();
}

// Explosion flash: a white-hot core with two shockwaves racing
// out. The spark burst and the loser's breakup carry the rest.
function paintBlast(refs, ctx, fx, age) {
  const pt = fxPoint(refs, fx);
  if (!pt) return;
  const dur = Math.max(1, fx.dur);
  const p = Math.max(0, Math.min(1, age / dur));
  const color = fx.color || "255,255,255";
  paintFxRing(refs, ctx, { r0: 12, r1: 190, color, dur }, age, pt);
  if (p > 0.12) {
    paintFxRing(refs, ctx, { r0: 8, r1: 130, color: "255,255,255", dur: dur * 0.8 },
      (p - 0.12) * dur, pt);
  }
  const sprites = refs.sprites || {};
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  drawGlow(ctx, sprites.white, pt.x, pt.y, 150 * (1 - p * 0.7), 0.95 * (1 - p));
  ctx.restore();
}

// Body scale follows the entities: full size on the big stage,
// shrinking with docked glyphs so narrow stages keep projectiles
// proportionate to the lane they fly.
function projStageScale(aR, bR) {
  const r = Math.min(Number(aR) || 0, Number(bR) || 0);
  return Math.max(0.45, Math.min(1, r / 56));
}

function paintProjectile(refs, ctx, fx, age) {
  const side = fx.side === "blue" ? "blue" : "red";
  const slots = refs.stage.entities;
  const stage = projStageScale(slots.intrusion.r, slots.guardian.r);
  const ends = projEnds(refs, side);
  const lane = flightLane(ends.from, ends.to);
  const flight = Number(fx.flight) || FLIGHT_MS.standard;
  const count = fx.kind === "volley" ? 3 : 1;
  const stagger = fx.kind === "volley" ? VOLLEY_STAGGER_MS : 0;
  for (let i = 0; i < count; i += 1) {
    const local = age - i * stagger;
    if (local < 0) continue;
    paintProjOne(refs, ctx, fx, side, lane, ends, local, flight, i, stage);
  }
}

function laneTangent(lane, q) {
  const p1 = lanePoint(lane, q);
  const p0 = lanePoint(lane, Math.max(0, q - 0.03));
  return Math.atan2(p1.y - p0.y, p1.x - p0.x);
}

function paintProjOne(refs, ctx, fx, side, lane, ends, local, flight, index, stage) {
  const s = (PROJ_SCALE[fx.size] || 1) * (Number(stage) || 1);
  const q = local / flight;
  const red = side !== "blue";
  if (fx.fail && !red) {
    paintOrbFail(refs, ctx, lane, ends, q, s);
    return;
  }
  if (q < 1) {
    if (red) paintLance(refs, ctx, lane, q, s, index);
    else paintOrb(refs, ctx, lane, q, s);
    return;
  }
  const it = local - flight;
  if (it < IMPACT_MS) {
    const iq = Math.min(1, it / IMPACT_MS);
    if (fx.fail) paintShatter(refs, ctx, ends.to, iq, s);
    else paintStrike(refs, ctx, ends.to, iq, s, red);
    return;
  }
  // Failed moves end with the debris; landed strikes leave a
  // fading aftermath ring the next windup overlaps.
  if (!fx.fail) {
    paintAftermath(refs, ctx, ends.to, (it - IMPACT_MS) / AFTERMATH_MS, s, red);
  }
}

// Aftermath: the strike's echo, a widening faint ring and a dim
// glow settling on the struck rim. aq runs 0 to 1. Painted by
// hand, not through the textured ring, so the handoff from the
// strike never pops back to full brightness.
function paintAftermath(refs, ctx, at, aq, s, red) {
  const q = Math.min(1, Math.max(0, aq));
  if (q >= 1) return;
  const color = red ? "255,80,60" : "90,195,255";
  const ease = 1 - (1 - q) * (1 - q);
  const r = 96 * s + (165 * s - 96 * s) * ease;
  const sprites = refs.sprites || {};
  const hot = red ? (sprites.lance || sprites.red) : (sprites.orb || sprites.cyan);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.strokeStyle = `rgba(${color},${(0.4 * (1 - q)).toFixed(2)})`;
  ctx.lineWidth = 3 * (1 - q) + 1;
  ctx.beginPath();
  ctx.arc(at.x, at.y, r, 0, 6.283);
  ctx.stroke();
  drawGlow(ctx, hot, at.x, at.y, 60 * s * (1 - q * 0.5), 0.3 * (1 - q));
  ctx.restore();
}

// Tapered ribbon trail for giant bodies: at titan scale the
// five trail dots read as separate beads, so the tail becomes
// one ribbon narrowing to the tail with a hot inner thread.
function paintTrailRibbon(ctx, lane, q, span, halfWid, color, inner) {
  const passes = [
    { mul: 1, color },
    { mul: 0.45, color: inner },
  ];
  for (const pass of passes) {
    const left = [];
    const right = [];
    const N = 16;
    for (let j = 0; j < N; j += 1) {
      const qq = Math.max(0, q - (span * j) / (N - 1));
      const p = lanePoint(lane, qq);
      const a = laneTangent(lane, qq) + Math.PI / 2;
      const w = halfWid * pass.mul * Math.pow(1 - j / (N - 1), 0.7);
      left.push([p.x + Math.cos(a) * w, p.y + Math.sin(a) * w]);
      right.push([p.x - Math.cos(a) * w, p.y - Math.sin(a) * w]);
    }
    ctx.fillStyle = pass.color;
    ctx.beginPath();
    ctx.moveTo(left[0][0], left[0][1]);
    for (const pt of left) ctx.lineTo(pt[0], pt[1]);
    for (let j = N - 1; j >= 0; j -= 1) ctx.lineTo(right[j][0], right[j][1]);
    ctx.closePath();
    ctx.fill();
  }
}

// Crimson code-lance: a shard body along the flight tangent with
// a streak trail. Abstract splinter, no handle anywhere.
function paintLance(refs, ctx, lane, q, s, index) {
  const head = lanePoint(lane, q);
  const ang = laneTangent(lane, q);
  const off = (index - 1) * 9 * s;
  const px = head.x + Math.cos(ang + Math.PI / 2) * off;
  const py = head.y + Math.sin(ang + Math.PI / 2) * off;
  const sprites = refs.sprites || {};
  const len = LANCE_LEN * s;
  const wid = LANCE_WID * s;
  const laneLen = Math.max(1, Math.hypot(lane.x1 - lane.x0, lane.y1 - lane.y0));
  const span = trailSpan(len, laneLen);
  // Additive halo first: streak and glow are light.
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  const tail = lanePoint(lane, Math.max(0, q - span));
  const streak = fxTex(refs, "streak");
  if (streak) {
    const tlen = Math.hypot(px - tail.x, py - tail.y);
    const tang = Math.atan2(py - tail.y, px - tail.x);
    ctx.save();
    ctx.globalAlpha = 0.6;
    ctx.translate((px + tail.x) / 2, (py + tail.y) / 2);
    ctx.rotate(tang);
    ctx.drawImage(streak, -tlen / 2, -wid, tlen, wid * 2);
    ctx.restore();
  }
  drawGlow(ctx, sprites.lance || sprites.red, px, py, len * 1.2, 1);
  ctx.restore();
  // Opaque body over the light: additive red over the bright
  // backdrop can never go crimson (the backdrop's green stays),
  // so the silhouette, embers, and core cover it for real.
  ctx.save();
  ctx.translate(px, py);
  ctx.rotate(ang);
  ctx.fillStyle = "#f52d1e";
  ctx.beginPath();
  ctx.moveTo(len / 2, 0);
  ctx.lineTo(-len * 0.3, -wid / 2);
  ctx.lineTo(-len * 0.15, 0);
  ctx.lineTo(-len * 0.3, wid / 2);
  ctx.closePath();
  ctx.fill();
  // No shard texture on the opaque body: its dark surround used
  // to vanish under additive blending, but covered the crimson
  // with near-black once the body went opaque. A dark spine and
  // code ticks keep the flat fill from reading blank.
  ctx.strokeStyle = "#7d0f0a";
  ctx.lineWidth = Math.max(1, 1.6 * s);
  ctx.beginPath();
  ctx.moveTo(len * 0.42, 0);
  ctx.lineTo(-len * 0.26, 0);
  ctx.stroke();
  ctx.lineWidth = Math.max(1, 1.2 * s);
  for (let i = 0; i < 3; i += 1) {
    const tx = len * (0.28 - i * 0.16);
    ctx.beginPath();
    ctx.moveTo(tx, -wid * 0.22);
    ctx.lineTo(tx, wid * 0.22);
    ctx.stroke();
  }
  ctx.fillStyle = "#fff6f0";
  ctx.beginPath();
  ctx.arc(len * 0.22, 0, 4.5 * s, 0, 6.283);
  ctx.fill();
  ctx.restore();
  ctx.save();
  if (s >= 3) {
    paintTrailRibbon(ctx, lane, q, span, wid * 0.9, "#f52d1e", "#ffb3a0");
  } else {
    ctx.fillStyle = "#f52d1e";
    for (let i = 1; i <= 5; i += 1) {
      const dot = lanePoint(lane, Math.max(0, q - (i * span) / 5));
      ctx.beginPath();
      ctx.arc(dot.x, dot.y, Math.max(1.2, (6 - i) * s), 0, 6.283);
      ctx.fill();
    }
  }
  ctx.restore();
}

// Cyan light orb: a glowing sphere with a long comet tail.
function paintOrb(refs, ctx, lane, q, s) {
  const head = lanePoint(lane, q);
  const sprites = refs.sprites || {};
  const r = ORB_R * s;
  const laneLen = Math.max(1, Math.hypot(lane.x1 - lane.x0, lane.y1 - lane.y0));
  const span = trailSpan(r * 2, laneLen);
  // Additive halo first, then the opaque body over it, mirroring
  // the lance: light glows, the body covers.
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  drawGlow(ctx, sprites.orb || sprites.cyan, head.x, head.y, r * 3.6, 1);
  const flare = fxTex(refs, "flare");
  if (flare) {
    ctx.save();
    ctx.globalAlpha = 0.7;
    ctx.drawImage(flare, head.x - r * 1.8, head.y - r * 1.8, r * 3.6, r * 3.6);
    ctx.restore();
  }
  ctx.restore();
  ctx.save();
  ctx.fillStyle = "#5ac8ff";
  ctx.beginPath();
  ctx.arc(head.x, head.y, r * 0.85, 0, 6.283);
  ctx.fill();
  ctx.fillStyle = "#f2faff";
  ctx.beginPath();
  ctx.arc(head.x, head.y, r * 0.5, 0, 6.283);
  ctx.fill();
  if (s >= 3) {
    paintTrailRibbon(ctx, lane, q, span, r * 0.8, "#5ac8ff", "#d8f1ff");
  } else {
    ctx.fillStyle = "#5ac8ff";
    for (let i = 1; i <= 5; i += 1) {
      const dot = lanePoint(lane, Math.max(0, q - (i * span) / 5));
      ctx.beginPath();
      ctx.arc(dot.x, dot.y, Math.max(1, r * (1 - i * 0.14)), 0, 6.283);
      ctx.fill();
    }
  }
  ctx.restore();
}

// A landed strike: shockwave ring plus a hot flash on the struck
// entity's rim, held near-full for the first 250ms. Sparks,
// hit-stop, and recoil fire from the ledger.
function paintStrike(refs, ctx, at, iq, s, red) {
  const color = red ? "255,80,60" : "90,195,255";
  paintFxRing(refs, ctx, { r0: 12 * s, r1: 96 * s, color, dur: IMPACT_MS }, iq * IMPACT_MS, at);
  const hold = flashHold(iq);
  if (hold <= 0) return;
  const sprites = refs.sprites || {};
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  const hot = red ? (sprites.lance || sprites.red) : (sprites.orb || sprites.cyan);
  drawGlow(ctx, hot, at.x, at.y, 78 * s * (1 - iq * 0.4), hold);
  ctx.fillStyle = red ? "rgba(255,240,235,1)" : "rgba(242,250,255,1)";
  ctx.beginPath();
  ctx.arc(at.x, at.y, 9 * s * hold + 1, 0, 6.283);
  ctx.fill();
  ctx.restore();
}

// A failed lance bursts against the shield: a white-hot contact
// flash, a ripple racing over the shield, and dark debris
// silhouettes rebounding clear of the glow. Debris paints opaque
// dark crimson so it reads against the bright shield. No ring
// marches on; the bar does not move.
function paintShatter(refs, ctx, at, iq, s) {
  const sprites = refs.sprites || {};
  paintFxRing(refs, ctx, { r0: 8 * s, r1: 70 * s, color: "255,255,255", dur: IMPACT_MS * 0.7 },
    Math.min(1, iq * 1.6) * IMPACT_MS * 0.7, at);
  if (iq < 0.55) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    drawGlow(ctx, sprites.white, at.x, at.y, 54 * s * (1 - iq), 0.9 * (1 - iq / 0.55));
    ctx.restore();
  }
  ctx.save();
  for (let i = 0; i < 5; i += 1) {
    const a = Math.PI + (i - 2) * 0.42;
    const d = iq * 150 * s;
    const x = at.x + Math.cos(a) * d;
    const y = at.y + Math.sin(a) * d * 0.6 + iq * iq * 48;
    const w = 15 * s * (1 - iq * 0.45);
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(iq * 8 + i * 1.7);
    ctx.globalAlpha = 0.95 * (1 - iq);
    ctx.fillStyle = "#6e130d";
    ctx.beginPath();
    ctx.moveTo(w / 2, 0);
    ctx.lineTo(-w * 0.3, -w * 0.28);
    ctx.lineTo(-w * 0.1, 0);
    ctx.lineTo(-w * 0.3, w * 0.28);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
  ctx.restore();
}

// A failed orb gutters out halfway across: it shrinks and grays,
// leaving a small cold ring where it died.
function paintOrbFail(refs, ctx, lane, ends, q, s) {
  const mid = lanePoint(lane, 0.5);
  if (q < 0.5) {
    const fade = q > 0.32 ? 1 - (q - 0.32) / 0.18 * 0.4 : 1;
    ctx.save();
    ctx.globalAlpha = fade;
    paintOrb(refs, ctx, lane, q, s);
    ctx.restore();
    return;
  }
  const iq = Math.min(1, (q - 0.5) * 4);
  paintFxRing(refs, ctx, { r0: 6, r1: 34, color: "150,155,168", dur: IMPACT_MS }, iq * IMPACT_MS, mid);
  void ends;
}

// Failed actions sputter and die on the target host itself.
function paintFxFizzle(refs, ctx, fx, p) {
  const at = fxPoint(refs, fx);
  if (!at) return;
  const red = fx.color !== "blue";
  const color = red ? "255,120,100" : "140,200,255";
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  // Short gray sparks spit off the host's rim and fall back.
  for (let i = 0; i < 5; i += 1) {
    if (Math.sin(p * 47 + i * 9) > 0.35) continue;
    const a = (i / 5) * Math.PI * 2 + 0.6;
    const r0 = at.r * (0.7 + p * 0.5);
    const r1 = r0 + 10 + (i % 3) * 5;
    ctx.strokeStyle = `rgba(${color},${(0.8 * (1 - p)).toFixed(2)})`;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(at.x + Math.cos(a) * r0, at.y + Math.sin(a) * r0);
    ctx.lineTo(at.x + Math.cos(a) * r1, at.y + Math.sin(a) * r1 + p * 10);
    ctx.stroke();
  }
  ctx.fillStyle = `rgba(150,150,160,${(p * (1 - p) * 4 * 0.45).toFixed(2)})`;
  for (let i = 0; i < 3; i += 1) {
    ctx.beginPath();
    ctx.arc(at.x + (i - 1) * 8, at.y - at.r - p * 20, 4 + p * 8, 0, 6.283);
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

// Truncated feed lines, cached: truncation depends only on the
// texts and the feed width, so remeasuring every frame wastes the
// text pass. The cache keys on the full texts plus width and font.
function fittedFeed(refs, ctx, side, shown, maxW) {
  const key = `${ctx.font}|${Math.round(maxW)}|${shown.map((line) => line.text).join("\n")}`;
  const cache = (refs.feedCache = refs.feedCache || {});
  const hit = cache[side];
  if (hit && hit.key === key) return hit.lines;
  const lines = shown.map((line) => {
    let text = line.text;
    while (text.length > 4 && ctx.measureText(text).width > maxW) {
      text = `${text.slice(0, -5)}…`;
    }
    return { text, tone: line.tone };
  });
  cache[side] = { key, lines };
  return lines;
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
  const fitted = fittedFeed(refs, ctx, side, shown, feed.w);
  const typed = refs.typed[side] || { chars: Infinity };
  fitted.forEach((line, i) => {
    const y = feed.y + 14 + i * lineH;
    let text = line.text;
    if (i === fitted.length - 1 && typed.chars !== Infinity) {
      text = text.slice(0, Math.max(0, Math.floor(typed.chars)));
    }
    ctx.fillStyle = tones[line.tone] || tones.cmd;
    const fresh = (i + 1) / shown.length;
    ctx.globalAlpha = slim ? 0.2 + 0.8 * fresh * fresh : 0.45 + 0.55 * fresh;
    ctx.fillText(text, feed.x, y);
  });
  ctx.globalAlpha = 1;
  if (typed.chars !== Infinity && fitted.length > 0) {
    const y = feed.y + 14 + (fitted.length - 1) * lineH;
    const last = fitted[fitted.length - 1].text.slice(0, Math.max(0, Math.floor(typed.chars)));
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
    // Cached like the beam fade: same stops every frame.
    if (!refs.washGrad || refs.washGradH !== ch) {
      const grad = ctx.createLinearGradient(0, 0, 0, ch);
      grad.addColorStop(0, "rgba(47,123,255,0.10)");
      grad.addColorStop(1, "rgba(47,123,255,0.02)");
      refs.washGrad = grad;
      refs.washGradH = ch;
    }
    ctx.fillStyle = refs.washGrad;
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
