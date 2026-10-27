// Arena: cinematic contest stage driven by real exercise events.
// A full-bleed canvas renders the scene (hosts, conduits, packets,
// bursts); a DOM HUD carries banners, meters, tug bar, score, caption
// and the auditor card. Plain canvas plus DOM, no libraries, offline.
// Transient effects spawn only while stepping forward in real time;
// scrubbing rebuilds the static snapshot with no leftovers.
import {
  DEFAULT_WEIGHTS, hostBlocks, motionReduced, pressureValue,
  redNotches, reduceHostStates, scoreEvents, secretRead, shortResult,
} from "./state.js";
import { describeEvent } from "./story.js";
import { applyRail } from "./rail.js";
import { pathBetween } from "./network.js";
import {
  choreograph, effectHost, httpLeakTokens,
  latestByActor, logLineIds, verdictCard,
} from "./choreography.js";
import {
  arenaGeometry, classicLayout, layoutHosts, nodeLabels, roleSub,
} from "./layout.js";

const RED = "#ff3b2f";
const RED_DEEP = "#7d1913";
const BLUE = "#2f7bff";
const BLUE_LIGHT = "#8fb8ff";
const BLUE_DEEP = "#14336f";
const GREEN = "#2fd27a";
const GOLD = "#ffc83d";
const BONE = "#f2efe8";
const INK = "#090b10";
const STEEL = "#8791a3";
const PANEL = "#141923";
const DISP = "'Big Shoulders Display',Impact,'Arial Narrow',sans-serif";
const MONO = "'JetBrains Mono',ui-monospace,Menlo,monospace";

const MAX_PARTICLES = 600;
const MAX_TEXTS = 48;
const MAX_TRANSIENTS = 96;
const MAX_BURSTS = 6;
const SPEED_LINES = 30;

const BASE_MS = {
  "scan-ring": 700, "target-ping": 400, packet: 650, "packet-fail": 450,
  "key-try": 550, "key-bounce": 400, denied: 900, "key-through": 400,
  "flash-red": 350, foothold: 400, "file-stream": 650, "file-fail": 350,
  crack: 500, "vignette-red": 800, banner: 1500, "log-beam": 700,
  "beam-fail": 350, "alert-pulse": 550, flag: 400, "isolate-snap": 350,
  barrier: 250, shield: 400, "isolate-fail": 350, wrench: 700,
  "ring-blue": 400, check: 900, "green-sweep": 550, reconnect: 400,
  "restore-fail": 300, "patch-fail": 300, "block-pulse": 450, "block-fail": 300,
  "idle-tick": 350, "bounce-blocked": 800, "scan-fail": 300, verdict: 2200,
};

// World stage in fixed coordinates; the canvas scales to fit.
// Host geometry lives on refs (see setArenaLayout): segment columns
// from the exercise layout, or the classic four when unknown.
const WORLD = { w: 1600, h: 900 };
const CAM_HOME = { x: 800, y: 450, z: 1 };

// Single motion switch shared with the app shell, so ?motion=full
// previews the full show even where the OS asks for reduced motion.
const reducedMotion = motionReduced;

function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

function easeOut(t) {
  return 1 - Math.pow(1 - t, 3);
}

function now() {
  return (typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now();
}

function nodePos(refs, key) {
  if (key === "red") return refs.src.red;
  if (key === "blue") return refs.src.blue;
  const n = refs.nodes[key];
  return n ? { x: n.x, y: n.y } : { x: WORLD.w / 2, y: WORLD.h / 2 };
}

// Red travels in through the entry host, then along the real links.
function redPathTo(refs, target) {
  const src = refs.src.red;
  const nodes = refs.nodes;
  if (!target || !nodes[target]) return [{ ...src }, { x: src.x + 220, y: src.y }];
  const pts = [{ ...src }];
  const entry = refs.entry;
  const leg = target === entry ? [entry] : pathBetween(entry, target, refs.paths) || [target];
  for (const hop of leg) {
    if (nodes[hop]) pts.push({ x: nodes[hop].x, y: nodes[hop].y });
  }
  return pts;
}

function pointAlong(pts, t) {
  if (pts.length === 0) return { x: 0, y: 0 };
  if (pts.length === 1) return { ...pts[0] };
  const segs = [];
  let total = 0;
  for (let i = 1; i < pts.length; i += 1) {
    const d = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    segs.push(d);
    total += d;
  }
  if (total <= 0) return { ...pts[0], ang: 0 };
  let want = clamp(t, 0, 1) * total;
  for (let i = 1; i < pts.length; i += 1) {
    if (want <= segs[i - 1]) {
      const f = segs[i - 1] > 0 ? want / segs[i - 1] : 0;
      return {
        x: pts[i - 1].x + (pts[i].x - pts[i - 1].x) * f,
        y: pts[i - 1].y + (pts[i].y - pts[i - 1].y) * f,
        ang: Math.atan2(pts[i].y - pts[i - 1].y, pts[i].x - pts[i - 1].x),
      };
    }
    want -= segs[i - 1];
  }
  const last = pts[pts.length - 1];
  const prev = pts[pts.length - 2];
  return { ...last, ang: Math.atan2(last.y - prev.y, last.x - prev.x) };
}

function makeNode(key, base) {
  return {
    key, x: base.x, y: base.y, r: base.r,
    kind: base.kind || "standard", label: base.label || key.toUpperCase(), sub: base.sub || "",
    tint: "ok", tag: "CLEAN", flood: 0, floodTarget: 0,
    open: 0, openTarget: 0, gold: 0, goldTarget: 0,
    wheel: Math.random() * 6,
    scanT: 0, scanMax: 1, flicker: 0, pop: 0,
    sh: 0, shTarget: 0, shScale: 2.4, flag: false,
  };
}

// Rebuild host nodes, conduits, and team positions for one exercise
// layout. Called when an exercise opens and whenever the layout
// changes; scrubbing within one exercise never rebuilds.
export function setArenaLayout(refs, layout) {
  if (!refs) return;
  const clean = layout && layout.hosts ? layout : classicLayout();
  const geo = arenaGeometry(clean);
  const roles = {};
  for (const host of clean.hosts) roles[host.id] = host.role;
  refs.layout = clean;
  refs.hosts = layoutHosts(clean);
  refs.edges = geo.edges;
  refs.src = { red: geo.red, blue: geo.blue };
  refs.entry = geo.entry;
  refs.jewel = geo.jewel;
  refs.paths = {
    positions: Object.fromEntries(
      Object.entries(geo.nodes).map(([id, node]) => [id, { x: node.x, y: node.y }])),
    edges: geo.edges.filter(([a]) => a !== "red" && a !== "blue"),
  };
  refs.nodes = {};
  const labels = nodeLabels(clean);
  for (const [id, node] of Object.entries(geo.nodes)) {
    const role = roles[id] || "";
    const jewel = id === clean.jewel_host;
    refs.nodes[id] = makeNode(id, {
      x: node.x, y: node.y, r: node.r, kind: node.kind,
      label: labels[id] || id.toUpperCase(), sub: roleSub(role, jewel),
    });
  }
}

function ensureLayout(refs, layout) {
  const clean = layout && layout.hosts ? layout : classicLayout();
  const key = `${clean.scenario_id}|${layoutHosts(clean).join(",")}`;
  if (!refs || refs.layoutKey === key) return;
  refs.layoutKey = key;
  setArenaLayout(refs, clean);
}

function el(id) {
  return (typeof document !== "undefined" && document.getElementById(id)) || null;
}

export function initArena(canvas) {
  const refs = {
    canvas, ctx: canvas.getContext("2d"),
    hud: {
      hudEl: el("arenaHud"),
      wrapEl: el("arenaWrap"),
      midEl: el("arenaMid"),
      thoughtRed: el("thoughtRed"),
      thoughtBlue: el("thoughtBlue"),
      redLine: el("arenaRedLine"), blueLine: el("arenaBlueLine"),
      redMeter: el("arenaRedMeter"), blueMeter: el("arenaBlueMeter"),
      exercise: el("arenaExercise"), move: el("arenaMove"), total: el("arenaTotal"),
      tug: el("arenaTug"), tugR: el("arenaTugR"), tugM: el("arenaTugM"),
      score: el("arenaScore"), cap: el("arenaCap"), capTag: el("arenaCapTag"),
      capText: el("arenaCapText"), capSub: el("arenaCapSub"),
      aud: el("arenaAud"), audNum: el("arenaAudNum"), audWord: el("arenaAudWord"),
      audCite: el("arenaAudCite"), audNote: el("arenaAudNote"),
      readout: el("arenaReadout"),
    },
    geom: { cw: 0, ch: 0, dpr: 1, sc: 1, cx: 0, cy: 0 },
    dots: null,
    nodes: {}, hosts: [], edges: [], src: { red: { x: 90, y: 450 }, blue: { x: 1490, y: 790 } },
    entry: "", jewel: "", paths: { positions: {}, edges: [] }, layout: null, layoutKey: "",
    snap: null, shown: null,
    particles: [], texts: [], transients: [], bursts: [], speedLines: [],
    pending: [], queue: [], playing: false, runId: 0,
    speed: 1, flash: null, audTimer: 0, capKey: "",
    cam: { ...CAM_HOME }, camTarget: { ...CAM_HOME }, focusUntil: 0,
    trauma: 0, hitstopUntil: 0,
    latencyEma: 16, clock: 0, floorDrift: 0,
    active: true, raf: 0, lastT: 0, firstFrame: true,
    ambient: { dots: [], next: 0 }, idle: true,
  };
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => fitCanvas(refs)) : null;
  if (ro && canvas) {
    // The caption grows with long sentences, so the scene re-centers too.
    ro.observe(canvas);
    if (refs.hud.hudEl) ro.observe(refs.hud.hudEl);
    if (refs.hud.cap) ro.observe(refs.hud.cap);
  }
  refs.ro = ro;
  fitCanvas(refs);
  setArenaLayout(refs, classicLayout());
  blankSnapshot(refs);
  refs.lastT = now();
  const loop = () => {
    refs.raf = 0;
    if (refs.active) {
      try {
        frame(refs);
      } catch {
        // One bad frame never freezes the scene; the next tick retries.
      }
    }
    if (refs.active && typeof requestAnimationFrame === "function") refs.raf = requestAnimationFrame(loop);
  };
  if (typeof requestAnimationFrame === "function") refs.raf = requestAnimationFrame(loop);
  return refs;
}

export function setArenaActive(refs, on) {
  if (!refs) return;
  const was = refs.active;
  refs.active = Boolean(on);
  if (refs.active && !was) {
    refs.lastT = now();
    refs.firstFrame = true;
    if (typeof requestAnimationFrame === "function" && !refs.raf) {
      const loop = () => {
        refs.raf = 0;
        if (refs.active) {
          try {
            frame(refs);
          } catch {
            // One bad frame never freezes the scene; the next tick retries.
          }
        }
        if (refs.active && typeof requestAnimationFrame === "function") refs.raf = requestAnimationFrame(loop);
      };
      refs.raf = requestAnimationFrame(loop);
    }
  }
  if (!refs.active && refs.raf && typeof cancelAnimationFrame === "function") {
    cancelAnimationFrame(refs.raf);
    refs.raf = 0;
  }
}

function fitCanvas(refs) {
  const { canvas } = refs;
  if (!canvas) return;
  const cw = Math.max(1, canvas.clientWidth || 640);
  const ch = Math.max(1, canvas.clientHeight || 360);
  const dpr = Math.min(2, (typeof window !== "undefined" && window.devicePixelRatio) || 1);
  // The scene lives between the HUD and the caption, like the target.
  const topPad = (refs.hud.hudEl ? refs.hud.hudEl.offsetHeight : 100) + 10;
  const botPad = refs.hud.cap ? refs.hud.cap.offsetHeight : 150;
  const uh = Math.max(120, ch - topPad - botPad);
  refs.geom = {
    cw, ch, dpr,
    sc: Math.min(cw / WORLD.w, uh / 700),
    cx: cw / 2, cy: topPad + uh / 2,
  };
  canvas.width = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  buildDots(refs);
  layOutRail(refs);
}

function blankSnapshot(refs) {
  refs.snap = {
    states: {}, blocks: {}, hostOrder: [...refs.hosts],
    pressure: 0, level: 0, notches: [false, false, false],
    score: 100, outcome: null, moves: 0, total: 0, label: "Exercise",
    weights: { ...DEFAULT_WEIGHTS }, secret: false,
    caption: "Hosts guard one secret.", sub: "", tag: "EXERCISE", tagTone: "system",
    redLine: "Red is waiting.", blueLine: "Blue is waiting.",
  };
  refs.shown = { pressure: 0, score: 100 };
}

// Prerendered halftone squares plus giant side words. The red/blue
// tint washes vary with pressure and draw live each frame.
function buildDots(refs) {
  if (typeof document === "undefined") return;
  const layer = document.createElement("canvas");
  layer.width = Math.max(1, Math.round(refs.geom.cw));
  layer.height = Math.max(1, Math.round(refs.geom.ch));
  const g = layer.getContext("2d");
  const { cw, ch } = refs.geom;
  g.fillStyle = "rgba(242,239,232,0.07)";
  const st = 26;
  for (let y = st / 2; y < ch; y += st) {
    const dx = cw * 0.58 - (y / ch) * cw * 0.16;
    const off = (Math.floor(y / st) % 2) ? st / 2 : 0;
    for (let x = off; x < cw; x += st) {
      const s = 3.6 - (Math.abs(x - dx) / cw) * 10;
      if (s > 0.5) g.fillRect(x, y, s, s);
    }
  }
  const fs = Math.min(ch * 0.62, cw * 0.3);
  g.font = `900 ${fs}px ${DISP}`;
  g.textBaseline = "middle";
  g.lineWidth = 2;
  g.strokeStyle = "rgba(255,59,47,0.14)";
  g.textAlign = "left";
  g.strokeText("RED", cw * 0.015, ch * 0.52);
  g.strokeStyle = "rgba(47,123,255,0.14)";
  g.textAlign = "right";
  g.strokeText("BLUE", cw * 0.985, ch * 0.52);
  const v = g.createRadialGradient(cw / 2, ch / 2, Math.min(cw, ch) * 0.35, cw / 2, ch / 2, Math.max(cw, ch) * 0.75);
  v.addColorStop(0, "rgba(0,0,0,0)");
  v.addColorStop(1, "rgba(0,0,0,0.6)");
  g.fillStyle = v;
  g.fillRect(0, 0, cw, ch);
  refs.dots = layer;
}

function frame(refs) {
  const t = now();
  let dt = refs.firstFrame ? 16 : t - refs.lastT;
  refs.firstFrame = false;
  refs.lastT = t;
  if (!Number.isFinite(dt) || dt < 0) dt = 16;
  dt = Math.min(dt, 100);
  refs.latencyEma += (dt - refs.latencyEma) * 0.06;
  update(refs, dt / 1000);
  draw(refs);
}

function update(refs, dt) {
  const reduced = reducedMotion();
  if (!reduced && now() < refs.hitstopUntil) dt *= 0.06;
  if (!reduced) {
    refs.clock += dt;
    refs.floorDrift += dt * 0.3;
  }
  // Idle means no event is animating: the queue, the delayed beats
  // and every transient list are empty. Ambient motion only breathes
  // in that gap, and freezes entirely under reduced motion.
  const idle = !refs.playing && refs.queue.length === 0 &&
    refs.pending.length === 0 && refs.transients.length === 0 &&
    refs.bursts.length === 0;
  refs.idle = idle;
  const t = now();
  if (t > refs.focusUntil) {
    if (idle && !reduced) {
      refs.camTarget = {
        x: CAM_HOME.x + Math.sin(refs.clock * 0.13) * 34,
        y: CAM_HOME.y + Math.cos(refs.clock * 0.1) * 22,
        z: 1 + Math.sin(refs.clock * 0.07) * 0.018,
      };
    } else {
      refs.camTarget = { ...CAM_HOME };
    }
  }
  if (!reduced) stepAmbient(refs, dt, idle);
  const k = 1 - Math.exp(-dt * 2.6);
  refs.cam.x += (refs.camTarget.x - refs.cam.x) * k;
  refs.cam.y += (refs.camTarget.y - refs.cam.y) * k;
  refs.cam.z += (refs.camTarget.z - refs.cam.z) * k;

  for (const key of refs.hosts) {
    const n = refs.nodes[key];
    if (!n) continue;
    n.wheel += dt * (n.open > 0 && n.open < 1 ? 9 : 0.35);
    if (n.scanT > 0) n.scanT -= dt;
    if (n.flicker > 0) n.flicker -= dt;
    n.pop *= Math.exp(-dt * 5);
    n.flood += (n.floodTarget - n.flood) * Math.min(1, dt * 2.2);
    n.open += (n.openTarget - n.open) * Math.min(1, dt * 2.4);
    n.gold += (n.goldTarget - n.gold) * Math.min(1, dt * 2.0);
    n.sh += (n.shTarget - n.sh) * Math.min(1, dt * 6);
    n.shScale += (1 - n.shScale) * Math.min(1, dt * 5);
  }

  for (let i = refs.pending.length - 1; i >= 0; i -= 1) {
    if (refs.pending[i].at <= t) {
      const job = refs.pending.splice(i, 1)[0];
      try {
        job.fn();
      } catch {
        // A missed beat never stops the scene.
      }
    }
  }

  const stepList = (list, isPacket) => {
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const item = list[i];
      if (item.delay > 0) {
        item.delay -= dt * 1000;
        continue;
      }
      item.t += dt * 1000;
      if (item.t >= item.ttl) {
        list.splice(i, 1);
        continue;
      }
      if (isPacket && item.pts) {
        const at = pointAlong(item.pts, easeOut(item.t / item.ttl));
        item.x = at.x;
        item.y = at.y;
        item.ang = at.ang || 0;
        item.trail.push({ x: at.x, y: at.y });
        if (item.trail.length > 20) item.trail.shift();
      } else if (list === refs.particles) {
        item.x += item.vx * dt;
        item.y += item.vy * dt;
        item.vx *= 0.96;
        item.vy *= 0.96;
      }
    }
  };
  stepList(refs.transients, true);
  stepList(refs.particles, false);
  stepList(refs.texts, false);
  stepList(refs.bursts, false);
  stepList(refs.speedLines, false);

  refs.trauma *= Math.exp(-dt * 5);
  if (refs.trauma < 0.2) refs.trauma = 0;
  if (refs.flash) {
    refs.flash.a -= dt * 0.7;
    if (refs.flash.a <= 0) refs.flash = null;
  }
  const shown = refs.shown;
  const snap = refs.snap;
  shown.pressure += (snap.pressure - shown.pressure) * (1 - Math.exp(-dt * 3));
  shown.score += (snap.score - shown.score) * (1 - Math.exp(-dt * 4));
  if (Math.abs(snap.score - shown.score) < 0.4) shown.score = snap.score;
  updateHud(refs);
}

function progressOf(item) {
  if (item.delay > 0 || item.ttl <= 0) return 0;
  return clamp(item.t / item.ttl, 0, 1);
}

// Ambient drift: small dim gray dots coasting along open conduits
// while nothing animates. Capped at four, slow, and visually far
// from the bright red and blue event packets.
const AMBIENT_COL = "#6b7690";
const AMBIENT_MAX = 4;

function stepAmbient(refs, dt, idle) {
  const amb = refs.ambient;
  for (let i = amb.dots.length - 1; i >= 0; i -= 1) {
    const dot = amb.dots[i];
    dot.t += dt;
    if (dot.t >= dot.ttl) amb.dots.splice(i, 1);
  }
  if (!idle) return;
  amb.next -= dt;
  if (amb.next > 0 || amb.dots.length >= AMBIENT_MAX) return;
  amb.next = 1.1 + Math.random() * 1.2;
  const open = refs.edges.filter(([a, b]) => !conduitCut(refs, a, b));
  if (open.length === 0) return;
  const [a, b] = open[Math.floor(Math.random() * open.length)];
  const A = nodePos(refs, a);
  const B = nodePos(refs, b);
  const flip = Math.random() < 0.5;
  amb.dots.push({
    x0: flip ? B.x : A.x, y0: flip ? B.y : A.y,
    x1: flip ? A.x : B.x, y1: flip ? A.y : B.y,
    t: 0, ttl: 2.6 + Math.random() * 1.6,
  });
}

function drawAmbient(refs) {
  const { ctx } = refs;
  ctx.save();
  for (const dot of refs.ambient.dots) {
    const p = clamp(dot.t / dot.ttl, 0, 1);
    const fade = Math.sin(p * Math.PI);
    ctx.globalAlpha = 0.3 * fade;
    ctx.fillStyle = AMBIENT_COL;
    const x = dot.x0 + (dot.x1 - dot.x0) * p;
    const y = dot.y0 + (dot.y1 - dot.y0) * p;
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, 6.283);
    ctx.fill();
    ctx.globalAlpha = 0.14 * fade;
    ctx.beginPath();
    ctx.arc(x - (dot.x1 - dot.x0) * 0.03, y - (dot.y1 - dot.y0) * 0.03, 3, 0, 6.283);
    ctx.fill();
  }
  ctx.restore();
}

// Two-thump heartbeat in [0, 1]: a strong beat plus its echo.
function heartbeat(phase) {
  const beat = Math.max(0, Math.sin(phase));
  const echo = Math.max(0, Math.sin(phase - 0.9));
  return beat * beat * 0.7 + echo * echo * 0.3;
}

function draw(refs) {
  const { ctx, geom } = refs;
  if (!ctx) return;
  const { cw, ch, dpr, sc, cx, cy } = geom;
  const reduced = reducedMotion();
  // Backdrop in screen space.
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = INK;
  ctx.fillRect(0, 0, cw, ch);
  const pv = refs.shown.pressure;
  const rp = 0.06 + 0.13 * Math.max(0, pv);
  const bp = 0.06 + 0.13 * Math.max(0, -pv);
  ctx.fillStyle = `rgba(255,59,47,${rp.toFixed(3)})`;
  ctx.beginPath();
  ctx.moveTo(0, 0); ctx.lineTo(cw * 0.58, 0); ctx.lineTo(cw * 0.42, ch); ctx.lineTo(0, ch);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = `rgba(47,123,255,${bp.toFixed(3)})`;
  ctx.beginPath();
  ctx.moveTo(cw * 0.58, 0); ctx.lineTo(cw, 0); ctx.lineTo(cw, ch); ctx.lineTo(cw * 0.42, ch);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = "rgba(242,239,232,0.1)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(cw * 0.58, 0);
  ctx.lineTo(cw * 0.42, ch);
  ctx.stroke();
  if (refs.dots) ctx.drawImage(refs.dots, 0, 0, cw, ch);
  // Perspective floor grid, scrolling gently.
  const hy = ch * 0.3;
  const vx = cw * 0.5;
  ctx.strokeStyle = "rgba(242,239,232,0.07)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = -16; i <= 16; i += 1) {
    ctx.moveTo(vx + i * 12, hy);
    ctx.lineTo(vx + i * cw * 0.14, ch);
  }
  ctx.stroke();
  ctx.beginPath();
  for (let kk = 0; kk < 16; kk += 1) {
    const f = (kk + (refs.floorDrift % 1)) / 16;
    const y = hy + (ch - hy) * f * f;
    ctx.moveTo(0, y);
    ctx.lineTo(cw, y);
  }
  ctx.stroke();

  // World space with camera and shake.
  const z = refs.cam.z * sc;
  const sx = reduced ? 0 : (Math.random() - 0.5) * refs.trauma;
  const sy = reduced ? 0 : (Math.random() - 0.5) * refs.trauma;
  ctx.setTransform(dpr * z, 0, 0, dpr * z, dpr * (cx - refs.cam.x * z + sx), dpr * (cy - refs.cam.y * z + sy));
  drawConduits(refs);
  drawAmbient(refs);
  drawEmblems(refs);
  // Vaults draw last so the crown jewel sits on top.
  const ordered = [...refs.hosts].sort((a, b) => {
    const ka = refs.nodes[a] && refs.nodes[a].kind === "vault" ? 1 : 0;
    const kb = refs.nodes[b] && refs.nodes[b].kind === "vault" ? 1 : 0;
    return ka - kb;
  });
  for (const key of ordered) {
    if (refs.nodes[key]) drawNode(refs, refs.nodes[key]);
  }
  for (const key of refs.hosts) {
    if (refs.nodes[key]) drawShield(refs, refs.nodes[key]);
  }
  drawBeams(refs);
  drawScans(refs);
  drawPackets(refs);
  drawFx(refs);
  drawRays(refs);
  drawLabels(refs);

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (refs.flash) {
    ctx.globalAlpha = Math.max(0, refs.flash.a);
    ctx.fillStyle = refs.flash.c;
    ctx.fillRect(0, 0, cw, ch);
    ctx.globalAlpha = 1;
  }
}

function nodeTintColor(tint) {
  if (tint === "probed") return BONE;
  if (tint === "signed" || tint === "comp") return RED;
  if (tint === "iso") return BLUE;
  if (tint === "patched") return BLUE_LIGHT;
  if (tint === "restored") return GREEN;
  return STEEL;
}

function conduitCut(refs, a, b) {
  const s = refs.snap.states;
  return (s[a] === "isolated") || (s[b] === "isolated");
}

function drawConduits(refs) {
  const { ctx } = refs;
  for (const [a, b] of refs.edges) {
    const A = nodePos(refs, a);
    const B = nodePos(refs, b);
    const cut = conduitCut(refs, a, b);
    const s = refs.snap.states;
    const hot = s[a] === "compromised" || s[b] === "compromised" || s[a] === "signed-in" || s[b] === "signed-in";
    ctx.lineCap = "round";
    ctx.strokeStyle = cut ? "#161b25" : "#1d2330";
    ctx.lineWidth = 16;
    ctx.beginPath();
    ctx.moveTo(A.x, A.y);
    ctx.lineTo(B.x, B.y);
    ctx.stroke();
    if (!cut) {
      ctx.setLineDash([22, 26]);
      ctx.lineDashOffset = -refs.clock * (hot ? 180 : 60);
      ctx.strokeStyle = hot ? RED : "#39445a";
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.moveTo(A.x, A.y);
      ctx.lineTo(B.x, B.y);
      ctx.stroke();
      ctx.setLineDash([]);
    } else {
      const m = s[a] === "isolated" ? A : B;
      const o = m === A ? B : A;
      const dx = m.x - o.x;
      const dy = m.y - o.y;
      const l = Math.hypot(dx, dy) || 1;
      const gap = 130;
      const px = m.x - (dx / l) * gap;
      const py = m.y - (dy / l) * gap;
      ctx.strokeStyle = BLUE;
      ctx.lineWidth = 7;
      ctx.beginPath();
      ctx.moveTo(px - 16, py - 16); ctx.lineTo(px + 16, py + 16);
      ctx.moveTo(px + 16, py - 16); ctx.lineTo(px - 16, py + 16);
      ctx.stroke();
    }
  }
}

// Shrink a font size until the text fits maxWidth. Dense ranges
// shrink blocks, so labels and captions measure to fit instead of
// spilling past the plate edge.
function fitFont(ctx, s, font, weight, base, maxWidth, floor) {
  ctx.font = `${weight} ${base}px ${font}`;
  const w = ctx.measureText(s).width;
  if (w > maxWidth && w > 0) {
    return Math.max(floor || 12, Math.floor((base * maxWidth) / w));
  }
  return base;
}

function textOut(ctx, s, x, y, font, size, fill, weight, align, stroke) {
  ctx.font = `${weight || 900} ${size}px ${font}`;
  ctx.textAlign = align || "center";
  ctx.textBaseline = "middle";
  if (stroke !== false) {
    ctx.lineWidth = Math.max(4, size * 0.14);
    ctx.strokeStyle = INK;
    ctx.lineJoin = "round";
    ctx.strokeText(s, x, y);
  }
  ctx.fillStyle = fill;
  ctx.fillText(s, x, y);
}

function drawEmblems(refs) {
  const { ctx } = refs;
  const r = refs.src.red;
  const b = refs.src.blue;
  ctx.fillStyle = RED;
  ctx.beginPath();
  ctx.moveTo(r.x - 46, r.y - 58);
  ctx.lineTo(r.x + 34, r.y);
  ctx.lineTo(r.x - 46, r.y + 58);
  ctx.lineTo(r.x - 22, r.y);
  ctx.closePath();
  ctx.fill();
  textOut(ctx, "RED", r.x - 6, r.y + 92, DISP, 44, RED, 900, "center", false);
  ctx.save();
  ctx.translate(b.x, b.y);
  ctx.fillStyle = BLUE;
  ctx.beginPath();
  ctx.moveTo(0, -50); ctx.lineTo(44, -30); ctx.lineTo(38, 22);
  ctx.lineTo(0, 52); ctx.lineTo(-38, 22); ctx.lineTo(-44, -30);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = INK;
  ctx.beginPath();
  ctx.moveTo(0, -26); ctx.lineTo(22, -16); ctx.lineTo(19, 10);
  ctx.lineTo(0, 27); ctx.lineTo(-19, 10); ctx.lineTo(-22, -16);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
  textOut(ctx, "BLUE", b.x, b.y - 84, DISP, 44, BLUE, 900, "center", false);
}

function roundRectPath(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function floodFill(refs, x, y, w, h, lv) {
  const { ctx } = refs;
  const top = y + h * (1 - lv);
  const amp = lv < 1 ? 10 : 0;
  ctx.fillStyle = RED;
  ctx.beginPath();
  ctx.moveTo(x, y + h);
  ctx.lineTo(x, top);
  for (let i = 0; i <= 24; i += 1) {
    const px = x + (w * i) / 24;
    ctx.lineTo(px, top + Math.sin(i * 0.9 + refs.clock * 5) * amp);
  }
  ctx.lineTo(x + w, y + h);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "rgba(0,0,0,0.18)";
  ctx.fillRect(x, Math.max(top + 30, y), w, Math.max(0, y + h - top - 30));
}

function drawNode(refs, n) {
  const { ctx } = refs;
  const col = nodeTintColor(n.tint);
  const r = n.r;
  let jx = 0;
  let jy = 0;
  if (n.flicker > 0 && !reducedMotion()) {
    jx = (Math.random() - 0.5) * 10;
    jy = (Math.random() - 0.5) * 10;
  }
  ctx.save();
  ctx.translate(n.x + jx, n.y + jy);
  // Resting hosts breathe with a faint heartbeat; event pops ride on top.
  const heart = refs.idle && !reducedMotion()
    ? heartbeat(refs.clock * 2.2 + n.x * 0.01 + n.y * 0.013) * 0.022
    : 0;
  const bob = 1 + 0.012 * Math.sin(refs.clock * 2 + n.x) + 0.13 * n.pop + heart;
  ctx.scale(bob, bob);
  // Rotating platform rings.
  ctx.save();
  ctx.translate(0, r * 0.95);
  ctx.scale(1, 0.3);
  ctx.strokeStyle = col;
  ctx.lineWidth = 7;
  ctx.globalAlpha = 0.85;
  ctx.setLineDash([30, 20]);
  ctx.lineDashOffset = -refs.clock * 70;
  ctx.beginPath();
  ctx.arc(0, 0, r * 1.55, 0, 6.283);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.lineWidth = 3;
  ctx.globalAlpha = 0.4;
  ctx.beginPath();
  ctx.arc(0, 0, r * 1.9, 0, 6.283);
  ctx.stroke();
  ctx.restore();

  const isVault = n.kind === "vault";
  if (isVault) {
    for (let i = 26; i > 0; i -= 2) {
      const c = 10 + (26 - i);
      ctx.fillStyle = `rgb(${c},${c + 3},${c + 10})`;
      ctx.beginPath();
      ctx.arc(i * 0.55, i, r * 1.12, 0, 6.283);
      ctx.fill();
    }
    ctx.fillStyle = PANEL;
    ctx.beginPath();
    ctx.arc(0, 0, r * 1.12, 0, 6.283);
    ctx.fill();
    if (n.flood > 0.01) {
      ctx.save();
      ctx.beginPath();
      ctx.arc(0, 0, r * 1.12, 0, 6.283);
      ctx.clip();
      floodFill(refs, -r * 1.12, -r * 1.12, r * 2.24, r * 2.24, n.flood);
      ctx.restore();
    }
    ctx.lineWidth = 12;
    ctx.strokeStyle = col;
    ctx.beginPath();
    ctx.arc(0, 0, r * 1.12, 0, 6.283);
    ctx.stroke();
    // Vault dial arcs that part as the vault opens.
    const gap = n.open * 1.15;
    ctx.lineWidth = 9;
    ctx.strokeStyle = n.flood > 0.5 ? BONE : "#2b3345";
    for (let i = 0; i < 3; i += 1) {
      const a0 = n.wheel + i * 2.094 + gap;
      const a1 = n.wheel + (i + 1) * 2.094 - gap - 0.2;
      ctx.beginPath();
      ctx.arc(0, 0, r * 0.76, a0, a1);
      ctx.stroke();
    }
    ctx.lineWidth = 7;
    ctx.strokeStyle = n.flood > 0.5 ? BONE : "#39445a";
    for (let i = 0; i < 4; i += 1) {
      const a = n.wheel * 1.4 + i * 1.5708;
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * r * 0.18, Math.sin(a) * r * 0.18);
      ctx.lineTo(Math.cos(a) * r * 0.58, Math.sin(a) * r * 0.58);
      ctx.stroke();
    }
    ctx.fillStyle = n.gold > 0 ? GOLD : (n.flood > 0.5 ? BONE : "#2f394d");
    ctx.beginPath();
    ctx.arc(0, 0, r * 0.2, 0, 6.283);
    ctx.fill();
    for (let i = 0; i < 8; i += 1) {
      const a = i * 0.7854;
      ctx.fillStyle = col;
      ctx.beginPath();
      ctx.arc(Math.cos(a) * r, Math.sin(a) * r, 5, 0, 6.283);
      ctx.fill();
    }
    if (n.gold > 0) {
      const h = 170 * n.gold;
      ctx.fillStyle = GOLD;
      ctx.fillRect(-34, -r * 0.2 - h, 68, h);
      if (n.gold > 0.7) {
        ctx.fillStyle = INK;
        ctx.font = `900 20px ${DISP}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("SECRET", 0, -r * 0.2 - h / 2);
      }
    }
    const tagFs = n.label.length <= 3 ? 54 : Math.max(28, Math.round((54 * 3.2) / n.label.length));
    const vaultFs = fitFont(ctx, n.label, DISP, 900, tagFs, r * 2.1);
    textOut(ctx, n.label, 0, r * 1.12 + 34, DISP, vaultFs, BONE, 900, "center");
  } else {
    const s = r * 2;
    const x0 = -r;
    const y0 = -r;
    for (let i = 24; i > 0; i -= 2) {
      const c = 9 + (24 - i);
      ctx.fillStyle = `rgb(${c},${c + 3},${c + 10})`;
      roundRectPath(ctx, x0 + i * 0.55, y0 + i, s, s, 18);
      ctx.fill();
    }
    ctx.fillStyle = PANEL;
    roundRectPath(ctx, x0, y0, s, s, 18);
    ctx.fill();
    if (n.flood > 0.01) {
      ctx.save();
      roundRectPath(ctx, x0, y0, s, s, 18);
      ctx.clip();
      floodFill(refs, x0, y0, s, s, n.flood);
      ctx.restore();
    }
    ctx.lineWidth = 12;
    ctx.strokeStyle = col;
    roundRectPath(ctx, x0, y0, s, s, 18);
    ctx.stroke();
    // Label and caption come from the layout (role-based); long names
    // shrink to fit the block. Small blocks keep the compact size.
    const fs = n.kind === "small" ? 62
      : n.label.length <= 3 ? 78 : Math.max(34, Math.round((78 * 3.4) / n.label.length));
    const labelFs = fitFont(ctx, n.label, DISP, 900, fs, s * 0.92);
    textOut(ctx, n.label, 0, -4, DISP, labelFs, n.flood > 0.5 ? INK : BONE, 900, "center", false);
    const sub = String(n.sub || "").toUpperCase();
    const subFs = sub ? fitFont(ctx, sub, MONO, 700, 14, s * 0.88, 10) : 14;
    ctx.font = `700 ${subFs}px ${MONO}`;
    ctx.fillStyle = n.flood > 0.5 ? INK : "#5b6678";
    ctx.textAlign = "center";
    ctx.fillText(sub, 0, r * 0.58);
  }
  // Business-weight badge, from the server config when loaded.
  const weights = refs.snap.weights || DEFAULT_WEIGHTS;
  const w = weights[n.key];
  if (typeof w === "number") {
    ctx.fillStyle = BONE;
    ctx.fillRect(-r - 6, -r - 30, 64, 30);
    ctx.fillStyle = INK;
    ctx.font = `900 22px ${DISP}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`×${w}`, -r + 26, -r - 14);
  }
  // Status tag plate.
  const ty = (isVault ? r * 1.12 + 78 : r + 34);
  ctx.font = `900 24px ${DISP}`;
  const tw = ctx.measureText(n.tag).width + 28;
  ctx.fillStyle = col;
  ctx.fillRect(-tw / 2, ty - 16, tw, 32);
  ctx.fillStyle = n.tint === "ok" ? BONE : INK;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(n.tag, 0, ty + 1);
  // Finding pennant.
  if (n.flag) {
    ctx.fillStyle = BLUE;
    ctx.fillRect(r + 6, -r - 34, 6, 34);
    ctx.beginPath();
    ctx.moveTo(r + 12, -r - 34);
    ctx.lineTo(r + 44, -r - 25);
    ctx.lineTo(r + 12, -r - 16);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
}

function drawShield(refs, n) {
  if (n.sh <= 0.01) return;
  const { ctx } = refs;
  const a = clamp(n.sh, 0, 1);
  const R = n.r * 1.62 * n.shScale;
  ctx.save();
  ctx.translate(n.x, n.y);
  ctx.globalAlpha = a;
  ctx.beginPath();
  for (let i = 0; i < 6; i += 1) {
    const ang = -Math.PI / 2 + (i * Math.PI) / 3;
    const px = Math.cos(ang) * R;
    const py = Math.sin(ang) * R;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
  ctx.fillStyle = "rgba(47,123,255,0.2)";
  ctx.fill();
  ctx.lineWidth = 14;
  ctx.strokeStyle = BLUE;
  ctx.lineJoin = "round";
  ctx.stroke();
  ctx.lineWidth = 3;
  ctx.strokeStyle = "rgba(143,184,255,0.55)";
  for (let i = 0; i < 6; i += 1) {
    const ang = -Math.PI / 2 + (i * Math.PI) / 3;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(Math.cos(ang) * R, Math.sin(ang) * R);
    ctx.stroke();
  }
  ctx.beginPath();
  for (let i = 0; i < 6; i += 1) {
    const ang = -Math.PI / 2 + (i * Math.PI) / 3;
    const px = Math.cos(ang) * R * 0.62;
    const py = Math.sin(ang) * R * 0.62;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
  ctx.stroke();
  ctx.restore();
}

function drawBeams(refs) {
  const { ctx } = refs;
  for (const t of refs.transients) {
    if (t.delay > 0) continue;
    if (t.kind === "pulse") {
      const p = progressOf(t);
      ctx.globalAlpha = (1 - p) * (t.a || 1);
      ctx.strokeStyle = t.col;
      ctx.lineWidth = (t.w || 10) * (1 - p * 0.7);
      ctx.beginPath();
      ctx.arc(t.x, t.y, (t.maxR || 420) * easeOut(p), 0, 6.283);
      ctx.stroke();
      ctx.globalAlpha = 1;
    } else if (t.kind === "beam") {
      const p = progressOf(t);
      const n = refs.nodes[t.host] || nodePos(refs, t.host);
      const s = refs.src.blue;
      const a = Math.min(1, p * 6) * Math.min(1, (1 - p) * 5);
      const dx = n.x - s.x;
      const dy = n.y - s.y;
      const l = Math.hypot(dx, dy) || 1;
      const nx = -dy / l;
      const ny = dx / l;
      const w = 10 + (n.r * 1.3 - 10) * Math.min(1, p * 3);
      ctx.save();
      ctx.globalAlpha = a;
      ctx.fillStyle = "rgba(47,123,255,0.18)";
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(n.x + nx * w, n.y + ny * w);
      ctx.lineTo(n.x - nx * w, n.y - ny * w);
      ctx.closePath();
      ctx.fill();
      ctx.setLineDash([26, 16]);
      ctx.lineDashOffset = -refs.clock * 520;
      ctx.strokeStyle = BLUE_LIGHT;
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      ctx.lineTo(n.x, n.y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.translate(n.x, n.y);
      const R = n.r * 1.45;
      ctx.strokeStyle = BLUE;
      ctx.lineWidth = 5;
      ctx.rotate(refs.clock * 1.2);
      ctx.setLineDash([40, 24]);
      ctx.beginPath();
      ctx.arc(0, 0, R, 0, 6.283);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.rotate(-refs.clock * 1.2);
      const c = R * 0.72;
      ctx.lineWidth = 7;
      const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]];
      for (const [sx, sy] of corners) {
        ctx.beginPath();
        ctx.moveTo(sx * c, sy * (c - 34));
        ctx.lineTo(sx * c, sy * c);
        ctx.lineTo(sx * (c - 34), sy * c);
        ctx.stroke();
      }
      ctx.restore();
    } else if (t.kind === "wall") {
      const p = progressOf(t);
      ctx.globalAlpha = p < 0.9 ? 1 : 1 - (p - 0.9) / 0.1;
      ctx.strokeStyle = t.col;
      ctx.lineWidth = 6;
      ctx.setLineDash([26, 16]);
      ctx.beginPath();
      ctx.moveTo(t.x - 170, t.y - 210);
      ctx.lineTo(t.x - 170, t.y + 210);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(t.x + 170, t.y - 210);
      ctx.lineTo(t.x + 170, t.y + 210);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    } else if (t.kind === "flash") {
      const p = progressOf(t);
      const n = nodePos(refs, t.host);
      const r = t.r || 130;
      ctx.save();
      ctx.translate(n.x, n.y);
      ctx.rotate(refs.clock * 5);
      ctx.globalAlpha = (1 - p) * 0.85;
      ctx.fillStyle = t.col;
      for (let i = 0; i < 8; i += 1) {
        ctx.save();
        ctx.rotate((i * Math.PI) / 4);
        ctx.fillRect(0, -12, r * (1 - p * 0.55), 24);
        ctx.restore();
      }
      ctx.fillStyle = BONE;
      ctx.beginPath();
      ctx.arc(0, 0, 44 * (1 - p * 0.5), 0, 6.283);
      ctx.fill();
      ctx.restore();
    } else if (t.kind === "idle") {
      const p = progressOf(t);
      ctx.globalAlpha = Math.sin(p * Math.PI) * 0.8;
      ctx.fillStyle = t.col;
      ctx.beginPath();
      ctx.arc(t.x, t.y, 10, 0, 6.283);
      ctx.fill();
      ctx.globalAlpha = 1;
    } else if (t.kind === "sweep") {
      drawSweep(refs, t);
    }
  }
}

function drawScans(refs) {
  const { ctx } = refs;
  for (const key of refs.hosts) {
    const n = refs.nodes[key];
    if (!n || n.scanT <= 0) continue;
    const p = 1 - n.scanT / (n.scanMax || 1);
    const a = Math.sin(Math.min(1, Math.max(0, p)) * Math.PI);
    ctx.save();
    ctx.translate(n.x, n.y);
    ctx.globalAlpha = a;
    const R = n.r * 2.2;
    const ang = p * 9.4;
    ctx.fillStyle = "rgba(255,59,47,0.28)";
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.arc(0, 0, R, ang - 0.7, ang);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = RED;
    ctx.lineWidth = 5;
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(Math.cos(ang) * R, Math.sin(ang) * R);
    ctx.stroke();
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(0, 0, R, 0, 6.283);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(0, 0, R * 0.6, 0, 6.283);
    ctx.stroke();
    ctx.restore();
  }
}

function drawPackets(refs) {
  const { ctx } = refs;
  for (const q of refs.transients) {
    if (q.delay > 0 || !q.pts) continue;
    const p = progressOf(q);
    const fade = q.fade ? clamp(1 - p, 0, 1) : 1;
    ctx.save();
    ctx.globalAlpha = fade;
    ctx.lineCap = "round";
    // Tapering trail, head bright and wide.
    for (let i = 1; i < q.trail.length; i += 1) {
      const f = i / q.trail.length;
      ctx.strokeStyle = q.col;
      ctx.globalAlpha = fade * f * 0.8;
      ctx.lineWidth = 2 + f * (q.kind === "key" ? 16 : 10);
      ctx.beginPath();
      ctx.moveTo(q.trail[i - 1].x, q.trail[i - 1].y);
      ctx.lineTo(q.trail[i].x, q.trail[i].y);
      ctx.stroke();
    }
    ctx.globalAlpha = fade;
    if (q.kind === "icon") {
      ctx.translate(q.x, q.y);
      ctx.globalAlpha = fade * (1 - p * 0.6);
      ctx.fillStyle = q.fill || q.col;
      ctx.fillRect(-16, -22, 32, 44);
      ctx.fillStyle = INK;
      for (let i = 0; i < 3; i += 1) ctx.fillRect(-10, -12 + i * 11, 20, 5);
    } else if (q.kind === "key") {
      ctx.translate(q.x, q.y);
      ctx.rotate(q.ang);
      ctx.fillStyle = BONE;
      ctx.strokeStyle = q.col;
      ctx.lineWidth = 5;
      ctx.beginPath();
      ctx.arc(-4, 0, 15, 0, 6.283);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = INK;
      ctx.beginPath();
      ctx.arc(-4, 0, 5, 0, 6.283);
      ctx.fill();
      ctx.fillStyle = BONE;
      ctx.fillRect(10, -4, 28, 8);
      ctx.fillRect(28, 4, 6, 10);
      ctx.fillRect(19, 4, 5, 8);
    } else {
      ctx.translate(q.x, q.y);
      ctx.fillStyle = BONE;
      ctx.beginPath();
      ctx.arc(0, 0, 11, 0, 6.283);
      ctx.fill();
      ctx.strokeStyle = q.col;
      ctx.lineWidth = 5;
      ctx.stroke();
    }
    ctx.restore();
  }
}

function drawFx(refs) {
  const { ctx } = refs;
  // Shockwave rings.
  for (const b of refs.bursts) {
    if (b.delay > 0) continue;
    const p = progressOf(b);
    ctx.globalAlpha = (1 - p) * 0.7;
    ctx.strokeStyle = b.col;
    ctx.lineWidth = 5;
    ctx.beginPath();
    ctx.arc(b.ox, b.oy, 40 + p * 320, 0, 6.283);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  // Radial particles with tails.
  for (const pt of refs.particles) {
    if (pt.delay > 0) continue;
    const p = progressOf(pt);
    ctx.globalAlpha = 1 - p;
    ctx.strokeStyle = pt.col;
    ctx.lineWidth = 5;
    ctx.beginPath();
    ctx.moveTo(pt.x, pt.y);
    const sp = Math.hypot(pt.vx, pt.vy) || 1;
    ctx.lineTo(pt.x - (pt.vx / sp) * 28, pt.y - (pt.vy / sp) * 28);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
}

function drawRays(refs) {
  const { ctx } = refs;
  const reduced = reducedMotion();
  for (const b of refs.bursts) {
    if (b.delay > 0) continue;
    const p = progressOf(b);
    ctx.save();
    ctx.translate(b.ox, b.oy);
    ctx.strokeStyle = b.col;
    ctx.globalAlpha = 1 - p;
    ctx.lineCap = "round";
    for (let i = 0; i < 30; i += 1) {
      const a = i * 0.2094 + (i % 3) * 0.04;
      const len = (160 + (i % 5) * 70) * (1 - p * 0.5);
      const in0 = 110 + p * 420;
      ctx.lineWidth = 3 + (i % 3) * 3;
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * in0, Math.sin(a) * in0);
      ctx.lineTo(Math.cos(a) * (in0 + len), Math.sin(a) * (in0 + len));
      ctx.stroke();
    }
    ctx.restore();
  }
  // Speed lines: white streaks converging on the impact point.
  if (!reduced) {
    for (const sl of refs.speedLines) {
      if (sl.delay > 0) continue;
      const p = progressOf(sl);
      ctx.globalAlpha = (1 - p) * 0.8;
      ctx.strokeStyle = "#f2efe8";
      ctx.lineWidth = 6;
      const x = sl.x0 + (sl.x1 - sl.x0) * p;
      const y = sl.y;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + 240 * (sl.dir || 1), y);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }
}

function drawSweep(refs, t) {
  const { ctx } = refs;
  const p = progressOf(t);
  const n = nodePos(refs, t.host);
  const dir = t.dir || 1;
  // Diagonal green band crossing the host, shrink loop replaced by pass.
  const off = (p - 0.5) * 700 * dir;
  ctx.save();
  ctx.translate(n.x + off, n.y);
  ctx.rotate(-Math.PI / 6);
  ctx.globalAlpha = Math.sin(p * Math.PI) * 0.75;
  ctx.fillStyle = t.col;
  ctx.fillRect(-34, -230, 68, 460);
  ctx.restore();
}

function drawLabels(refs) {
  const { ctx } = refs;
  for (const t of refs.texts) {
    if (t.delay > 0) continue;
    const p = progressOf(t);
    const rise = (1 - Math.pow(1 - p, 2)) * (t.rise ?? 90);
    let scale = p < 0.12 ? 0.6 + (p / 0.12) * 0.55 : 1.15 - (p - 0.12) * 0.12;
    scale = Math.max(0.4, scale);
    ctx.save();
    ctx.translate(t.x, t.y - rise);
    ctx.scale(scale, scale);
    ctx.globalAlpha = p > 0.82 ? 1 - (p - 0.82) / 0.18 : 1;
    if (t.kind === "glitch") {
      const fs = Math.min(90, Math.max(44, 46 + t.s.length * 1.2));
      textOut(ctx, t.s, -12, 0, DISP, fs, RED, 900, "center");
      textOut(ctx, t.s, 12, 4, DISP, fs, BLUE, 900, "center");
    } else if (t.kind === "stamp") {
      const fs = 46;
      ctx.font = `900 ${fs}px ${DISP}`;
      const w = ctx.measureText(t.s).width + 34;
      ctx.save();
      ctx.rotate(-0.14);
      ctx.strokeStyle = t.c;
      ctx.lineWidth = 7;
      textOut(ctx, t.s, 0, 0, DISP, fs, t.c, 900, "center");
      ctx.strokeRect(-w / 2, -fs / 2 - 12, w, fs + 24);
      ctx.restore();
    } else if (t.kind === "burst") {
      const fs = 54;
      ctx.font = `900 ${fs}px ${DISP}`;
      const w = ctx.measureText(t.s).width;
      const nw = w * 0.62 + 60;
      const nh = fs * 1.15 + 40;
      ctx.fillStyle = GOLD;
      ctx.beginPath();
      const spikes = 14;
      for (let i = 0; i <= spikes * 2; i += 1) {
        const r = (i % 2 === 0) ? Math.max(nw, nh) * 0.5 : Math.max(nw, nh) * 0.38;
        const a = (i / (spikes * 2)) * Math.PI * 2;
        const px = Math.cos(a) * (nw / nh) * r;
        const py = Math.sin(a) * r;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fill();
      ctx.lineWidth = 5;
      ctx.strokeStyle = INK;
      ctx.stroke();
      textOut(ctx, t.s, 0, 0, DISP, fs, INK, 900, "center");
    } else if (t.kind === "info") {
      textOut(ctx, t.s, 0, 0, DISP, 40, t.c || BONE, 900, "center");
    } else {
      textOut(ctx, t.s, 0, 0, DISP, 37, t.c || "#dfe3ea", 900, "center");
    }
    ctx.restore();
  }
}

function shake(refs, px) {
  if (reducedMotion()) return;
  refs.trauma = Math.min(64, refs.trauma + px);
}
function flash(refs, c, a) {
  refs.flash = { c, a };
}
function hitstop(refs, ms) {
  if (reducedMotion()) return;
  refs.hitstopUntil = Math.max(refs.hitstopUntil, now() + ms);
}

function focusHost(refs, host, mag) {
  if (reducedMotion()) return;
  const n = nodePos(refs, host);
  refs.camTarget = { x: n.x, y: n.y, z: (mag || 1.5) };
  refs.focusUntil = now() + 2200;
}

function updateHud(refs) {
  const hud = refs.hud;
  if (!hud) return;
  const snap = refs.snap;
  const shown = refs.shown;
  // Tug bar with a live wobble while actions are in flight.
  const flying = refs.transients.length > 0 || refs.pending.length > 0;
  const jit = reducedMotion() ? 0 : (Math.sin(refs.clock * 9) * 0.028 + Math.sin(refs.clock * 23) * 0.014) * (flying ? 1.5 : 0.6);
  const pp = clamp(shown.pressure + jit, -1, 1);
  const pc = `${((0.5 + pp / 2) * 100).toFixed(1)}%`;
  if (hud.tugR) hud.tugR.style.width = pc;
  if (hud.tugM) hud.tugM.style.left = pc;
  // Meters: red progress notches, blue held blocks per host.
  // Meters rebuild their segments when the count changes: red shows
  // one segment per episode notch, blue one block per host.
  if (hud.redMeter) {
    const segs = ensureSegs(hud.redMeter, snap.notches.length);
    for (let i = 0; i < segs.length; i += 1) {
      segs[i].className = snap.notches[i] ? "on" : "";
    }
  }
  if (hud.blueMeter) {
    const order = snap.hostOrder && snap.hostOrder.length > 0 ? snap.hostOrder : refs.hosts;
    const segs = ensureSegs(hud.blueMeter, order.length);
    for (let i = 0; i < segs.length; i += 1) {
      const block = snap.blocks[order[i]];
      segs[i].className = block === "breached" ? "bad" : block === "isolated" ? "iso" : block === "patched" ? "fixed" : "on";
    }
  }
  if (hud.score) hud.score.textContent = String(Math.round(shown.score));
  if (hud.exercise) hud.exercise.textContent = snap.label || "Exercise";
  if (hud.move) hud.move.textContent = String(snap.moves).padStart(2, "0");
  if (hud.total) hud.total.textContent = String(snap.total);
  if (hud.redLine) hud.redLine.textContent = snap.redLine;
  if (hud.blueLine) hud.blueLine.textContent = snap.blueLine;
  // Caption and banners only touch the DOM when the words change.
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
    if (hud.cap && !reducedMotion()) {
      hud.cap.classList.remove("slam");
      void hud.cap.offsetWidth;
      hud.cap.classList.add("slam");
    }
  }
  if (hud.readout) {
    const live = refs.transients.filter((t) => t.pts).length;
    const lat = Math.round(refs.latencyEma);
    const link = shown.pressure > 0.3 ? "CONTESTED" : shown.pressure < -0.3 ? "HELD" : "OPEN";
    hud.readout.textContent =
      `${snap.label || "EX"}  MOVE ${snap.moves}/${snap.total}\n` +
      `PKT ${String(live).padStart(2, "0")}  LAT ${lat}MS\n` +
      `LINK ${link}`;
  }
}

// ---------- synthesized sound ----------
// Web Audio bleeps, no files. Off until the viewer opts in.
let soundOn = false;
let audioCtx = null;

function ensureAudio() {
  if (audioCtx || typeof window === "undefined") return audioCtx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  try {
    audioCtx = new AC();
  } catch {
    return null;
  }
  return audioCtx;
}

export function toggleArenaSound() {
  soundOn = !soundOn;
  if (soundOn) {
    const ctx = ensureAudio();
    if (ctx && ctx.state === "suspended") void ctx.resume().catch(() => {});
  }
  return soundOn;
}

function tone(freq, dur, type, vol, slideTo) {
  if (!soundOn) return;
  const ctx = ensureAudio();
  if (!ctx) return;
  try {
    const t = ctx.currentTime;
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = type || "sine";
    o.frequency.setValueAtTime(freq, t);
    if (slideTo) o.frequency.exponentialRampToValueAtTime(slideTo, t + dur);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(vol || 0.04, t + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g);
    g.connect(ctx.destination);
    o.start(t);
    o.stop(t + dur + 0.02);
  } catch {
    // Sound is decorative; never interrupt the scene.
  }
}

// One subtle voice per effect family: [freq, seconds, type, gain, slideTo].
const EFFECT_SOUND = {
  "scan-ring": [520, 0.12, "sine", 0.025, 780],
  packet: [660, 0.08, "sine", 0.03],
  "packet-fail": [330, 0.1, "sine", 0.02, 220],
  "key-try": [440, 0.07, "triangle", 0.035],
  "key-bounce": [300, 0.09, "triangle", 0.03, 180],
  "key-through": [440, 0.14, "triangle", 0.04, 880],
  denied: [180, 0.18, "sawtooth", 0.028, 120],
  foothold: [330, 0.1, "sine", 0.03],
  "file-stream": [740, 0.08, "sine", 0.028],
  crack: [140, 0.3, "sine", 0.05, 70],
  banner: [110, 0.4, "sine", 0.06, 55],
  "log-beam": [590, 0.14, "sine", 0.025, 880],
  "alert-pulse": [980, 0.1, "sine", 0.03],
  flag: [880, 0.08, "sine", 0.025],
  "isolate-snap": [240, 0.12, "square", 0.022],
  shield: [320, 0.1, "square", 0.02, 480],
  wrench: [700, 0.09, "triangle", 0.03, 990],
  check: [880, 0.12, "triangle", 0.03, 1174],
  "green-sweep": [520, 0.12, "sine", 0.03, 780],
  reconnect: [620, 0.1, "sine", 0.028, 930],
  "block-pulse": [200, 0.15, "square", 0.02],
  "bounce-blocked": [200, 0.15, "square", 0.022, 140],
  "idle-tick": [880, 0.05, "sine", 0.014],
  verdict: [760, 0.1, "sine", 0.03, 1140],
};

function playSound(effect) {
  const preset = EFFECT_SOUND[effect];
  if (!preset) return;
  tone(preset[0], preset[1], preset[2], preset[3], preset[4]);
}

// ---------- transient spawners ----------

function pushCapped(list, cap, item) {
  list.push(item);
  if (list.length > cap) list.splice(0, list.length - cap);
}

function after(refs, ms, fn) {
  refs.pending.push({ at: now() + Math.max(0, ms), fn });
}

function ringAt(refs, x, y, col, max, ttl, w, delay) {
  pushCapped(refs.transients, MAX_TRANSIENTS, {
    kind: "pulse", x, y, col, maxR: max || 420, w: w || 10, a: 1,
    t: 0, ttl: ttl || 900, delay: delay || 0,
  });
}

function burstAt(refs, x, y, col, n, speed, delay, ttl) {
  const count = reducedMotion() ? Math.min(n || 24, 10) : (n || 24);
  for (let i = 0; i < count; i += 1) {
    const a = Math.random() * Math.PI * 2;
    const sp = (0.25 + Math.random()) * (speed || 500);
    pushCapped(refs.particles, MAX_PARTICLES, {
      x, y, col, delay: delay || 0, t: 0, ttl: (ttl || 700) + Math.random() * 400,
      vx: Math.cos(a) * sp, vy: Math.sin(a) * sp,
    });
  }
}

function raysAt(refs, x, y, col, delay, ttl) {
  if (reducedMotion()) return;
  pushCapped(refs.bursts, MAX_BURSTS, {
    ox: x, oy: y, col, delay: delay || 0, t: 0, ttl: ttl || 500,
  });
}

function packetAlong(refs, pts, opts) {
  const o = opts || {};
  const p0 = pts[0];
  const ttl = o.ms || 900;
  const delay = o.delay || 0;
  pushCapped(refs.transients, MAX_TRANSIENTS, {
    kind: o.kind || "packet", col: o.col || RED, fill: o.fill, word: o.word,
    pts, x: p0.x, y: p0.y, ang: 0, trail: [{ x: p0.x, y: p0.y }],
    fade: o.fade ? 1 : 0, t: 0, ttl, delay,
  });
  if (typeof o.onArrive === "function") after(refs, delay + ttl, o.onArrive);
}

function textAt(refs, s, x, y, opts) {
  const o = opts || {};
  pushCapped(refs.texts, MAX_TEXTS, {
    s, x, y, c: o.c || BONE, kind: o.kind || "info",
    sub: o.sub, rise: o.rise == null ? 90 : o.rise,
    t: 0, ttl: o.ttl || 1800, delay: o.delay || 0,
  });
}

function speedlinesAt(refs, x, y, count, delay, ttl) {
  if (reducedMotion()) return;
  const n = count || 22;
  for (let i = 0; i < n; i += 1) {
    const side = i % 2 === 0 ? -1 : 1;
    pushCapped(refs.speedLines, SPEED_LINES, {
      x0: x + side * (500 + Math.random() * 500), x1: x + side * 140,
      y: y + (Math.random() - 0.5) * 900, dir: -side,
      delay: (delay || 0) + Math.random() * 120, t: 0, ttl: ttl || 700,
    });
  }
}

function glitchCanvas(refs) {
  if (reducedMotion() || !refs.canvas) return;
  refs.canvas.classList.remove("glitch");
  void refs.canvas.offsetWidth;
  refs.canvas.classList.add("glitch");
}

// Big-moment combo shared by breaches, containment and the climax:
// expanding rays, screen shake, a brief hit stop, a color-split glitch.
function impactAt(refs, x, y, col, opts) {
  const o = opts || {};
  raysAt(refs, x, y, col || BONE, o.delay || 0, 500);
  if (reducedMotion()) return;
  after(refs, o.delay || 0, () => {
    shake(refs, o.shake == null ? 22 : o.shake);
    hitstop(refs, o.hit == null ? 120 : o.hit);
    glitchCanvas(refs);
  });
}

function stopShort(from, to, gap) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const l = Math.hypot(dx, dy) || 1;
  return { x: to.x - (dx / l) * gap, y: to.y - (dy / l) * gap };
}

function ensureSegs(meter, count) {
  while (meter.children.length < count) meter.append(document.createElement("i"));
  while (meter.children.length > count) meter.lastChild.remove();
  return meter.children;
}

function bluePathTo(refs, target) {
  const src = refs.src.blue;
  const node = target && refs.nodes[target];
  if (!node) return [{ ...src }, { x: src.x - 220, y: src.y }];
  return [{ ...src }, { x: node.x, y: node.y }];
}

// ---------- step player ----------
// Each choreograph() step becomes canvas transients plus an optional
// DOM beat (auditor card). Returns the step's pacing duration in ms.

function stepHost(refs, step) {
  return step.target && refs.nodes[step.target] ? step.target : null;
}

function runStep(refs, step, ev, speed) {
  const S = (ms) => Math.max(1, Math.round((ms || 0) / (speed || 1)));
  playSound(step.effect);
  const host = stepHost(refs, step);
  const n = host ? refs.nodes[host] : null;
  const p = n ? { x: n.x, y: n.y } : { x: 800, y: 450 };
  const r = n ? n.r : 88;
  const redSide = step.from === "red" || (step.from !== "blue" && ev.actor !== "blue");
  const col = redSide ? RED : BLUE;

  switch (step.effect) {
    case "scan-ring": {
      if (!n) break;
      n.scanMax = S(1600) / 1000;
      n.scanT = n.scanMax;
      focusHost(refs, host, 1.5);
      ringAt(refs, p.x, p.y, RED, r * 3.4, S(1100), 8, 0);
      after(refs, S(500), () => ringAt(refs, p.x, p.y, RED, r * 3.4, S(1100), 5, 0));
      break;
    }
    case "scan-fail":
      ringAt(refs, p.x, p.y, "#5b6678", 200, S(600), 6, 0);
      break;
    case "target-ping":
      ringAt(refs, p.x, p.y, col, 220, S(700), 7, 0);
      break;
    case "packet": {
      const pts = redSide ? redPathTo(refs, host) : bluePathTo(refs, host);
      if (host) focusHost(refs, host, 1.4);
      packetAlong(refs, pts, {
        kind: "packet", col, ms: S(900),
        onArrive: () => {
          if (!host) return;
          burstAt(refs, p.x, p.y, GOLD, 26, 520, 0);
          ringAt(refs, p.x, p.y, GOLD, 260, S(700), 7, 0);
          shake(refs, 6);
          if (ev.tool === "http_request") {
            const tokens = httpLeakTokens(ev, refs.hosts);
            textAt(refs, String((ev.args || {}).path || "page"), p.x, p.y - r - 58,
              { c: GOLD, kind: "info", ttl: S(2000) });
            tokens.slice(0, 3).forEach((token, i) => {
              textAt(refs, token, p.x + r + 30, p.y - 20 + i * 34,
                { c: BONE, kind: "plain", ttl: S(2400), rise: 8, delay: S(350 + i * 350) });
            });
          }
        },
      });
      break;
    }
    case "packet-fail": {
      const pts = redSide ? redPathTo(refs, host) : bluePathTo(refs, host);
      if (host) {
        const tip = stopShort(pts[pts.length - 2] || pts[0], pts[pts.length - 1], r * 1.2);
        pts[pts.length - 1] = tip;
      }
      packetAlong(refs, pts, {
        kind: "packet", col: "#5b6678", ms: S(700),
        onArrive: () => burstAt(refs, pts[pts.length - 1].x, pts[pts.length - 1].y, "#5b6678", 14, 320, 0),
      });
      break;
    }
    case "key-try": {
      const pts = redPathTo(refs, host);
      const tip = host ? stopShort(pts[pts.length - 2] || pts[0], pts[pts.length - 1], r * 1.55) : pts[pts.length - 1];
      pts[pts.length - 1] = tip;
      packetAlong(refs, pts, {
        kind: "key", col: RED, ms: S(800),
        onArrive: () => burstAt(refs, tip.x, tip.y, RED, 16, 420, 0),
      });
      break;
    }
    case "key-bounce": {
      const pts = redPathTo(refs, host);
      const tip = host ? stopShort(pts[pts.length - 2] || pts[0], pts[pts.length - 1], r * 1.55) : pts[pts.length - 1];
      const back = pts.length > 2 ? pts[pts.length - 2] : refs.src.red;
      packetAlong(refs, [tip, back], { kind: "key", col: "#5b6678", fade: true, ms: S(500) });
      burstAt(refs, tip.x, tip.y, BLUE_LIGHT, 34, 700, 0);
      ringAt(refs, tip.x, tip.y, BLUE, 200, S(500), 8, 0);
      shake(refs, 14);
      break;
    }
    case "denied":
      textAt(refs, String(step.mark || "Denied").toUpperCase(), p.x, p.y - r - 80,
        { c: BLUE_LIGHT, kind: "stamp", ttl: S(1600) });
      shake(refs, 8);
      if (host) focusHost(refs, host, 1.4);
      break;
    case "key-through": {
      packetAlong(refs, redPathTo(refs, host), {
        kind: "key", col: RED, ms: S(800),
        onArrive: () => {
          burstAt(refs, p.x, p.y, RED, 60, 820, 0);
          ringAt(refs, p.x, p.y, RED, 560, S(1000), 16, 0);
          ringAt(refs, p.x, p.y, RED, 420, S(900), 9, S(120));
          flash(refs, RED, 0.28);
          impactAt(refs, p.x, p.y, BONE, { shake: 26, hit: 120 });
          speedlinesAt(refs, p.x, p.y, 22, 0);
          textAt(refs, "ACCESS", p.x, p.y - r - 80, { c: RED, kind: "burst", ttl: S(1800) });
        },
      });
      if (host) focusHost(refs, host, 1.35);
      break;
    }
    case "flash-red":
      pushCapped(refs.transients, MAX_TRANSIENTS,
        { kind: "flash", host, col: RED, r: 130, t: 0, ttl: S(350), delay: 0 });
      break;
    case "foothold":
      ringAt(refs, p.x, p.y, RED, 420, S(900), 9, 0);
      if (n) n.pop = 1;
      break;
    case "file-stream": {
      const pts = redPathTo(refs, host).reverse();
      if (host) focusHost(refs, host, 1.45);
      packetAlong(refs, pts, {
        kind: "icon", col: RED, fill: GOLD, ms: S(900),
        onArrive: () => {
          ringAt(refs, refs.src.red.x, refs.src.red.y, GOLD, 260, S(700), 7, 0);
          burstAt(refs, refs.src.red.x, refs.src.red.y, GOLD, 18, 420, 0);
        },
      });
      break;
    }
    case "file-fail":
      burstAt(refs, p.x, p.y, "#5b6678", 14, 320, 0);
      break;
    case "crack":
      shake(refs, 30);
      flash(refs, RED, 0.3);
      hitstop(refs, S(140));
      glitchCanvas(refs);
      speedlinesAt(refs, p.x, p.y, 26, 0);
      ringAt(refs, p.x, p.y, RED, 700, S(1100), 14, 0);
      if (host) focusHost(refs, host, 1.7);
      break;
    case "vignette-red":
      flash(refs, RED, 0.45);
      after(refs, S(400), () => flash(refs, RED, 0.3));
      break;
    case "banner":
      textAt(refs, String(step.text || "Secret exposed").toUpperCase(), p.x - 40, p.y - r - 120,
        { c: GOLD, kind: "glitch", ttl: S(3400) });
      burstAt(refs, p.x, p.y - 40, GOLD, 110, 1100, S(900), 900);
      ringAt(refs, p.x, p.y, GOLD, 900, S(1300), 26, S(900));
      ringAt(refs, p.x, p.y, RED, 700, S(1100), 14, S(900));
      after(refs, S(900), () => {
        shake(refs, 44);
        flash(refs, GOLD, 0.5);
        impactAt(refs, p.x, p.y, GOLD, { shake: 0, hit: 250 });
      });
      break;
    case "log-beam": {
      pushCapped(refs.transients, MAX_TRANSIENTS,
        { kind: "beam", host, col: BLUE, t: 0, ttl: S(2600), delay: 0 });
      if (!step.variant && ev.tool === "read_logs") {
        logLineIds(ev, 4).forEach((id, i) => {
          textAt(refs, id, p.x + r + 26, p.y - 62 + i * 30,
            { c: BLUE_LIGHT, kind: "plain", ttl: S(2200), rise: 8, delay: S(500 + i * 500) });
        });
      }
      if (host) focusHost(refs, host, 1.5);
      break;
    }
    case "beam-fail":
      ringAt(refs, p.x, p.y, "#5b6678", 200, S(600), 6, 0);
      break;
    case "alert-pulse":
      ringAt(refs, p.x, p.y, BLUE, 500, S(1000), 12, 0);
      ringAt(refs, p.x, p.y, BLUE, 340, S(800), 8, S(150));
      if (n) n.pop = 1;
      break;
    case "flag":
      ringAt(refs, p.x, p.y, BLUE, 300, S(700), 8, 0);
      burstAt(refs, p.x, p.y, BLUE, 22, 480, 0);
      break;
    case "isolate-snap":
      shake(refs, 22);
      flash(refs, BLUE, 0.24);
      ringAt(refs, p.x, p.y, BLUE, 620, S(900), 18, 0);
      burstAt(refs, p.x, p.y, BLUE, 50, 760, 0);
      impactAt(refs, p.x, p.y, BONE, { shake: 0, hit: 120 });
      textAt(refs, "ISOLATED", p.x, p.y - r - 90, { c: BLUE, kind: "burst", ttl: S(1800) });
      if (n) n.shScale = 2.4;
      if (host) focusHost(refs, host, 1.5);
      break;
    case "barrier":
      pushCapped(refs.transients, MAX_TRANSIENTS,
        { kind: "wall", x: p.x, y: p.y, col: BLUE, t: 0, ttl: S(1200), delay: 0 });
      break;
    case "shield":
      ringAt(refs, p.x, p.y, BLUE, 400, S(800), 10, 0);
      break;
    case "isolate-fail":
      ringAt(refs, p.x, p.y, "#5b6678", 220, S(600), 6, 0);
      break;
    case "wrench":
      ringAt(refs, p.x, p.y, BLUE_LIGHT, 500, S(1000), 12, 0);
      burstAt(refs, p.x, p.y, BLUE_LIGHT, 40, 600, 0);
      shake(refs, 10);
      impactAt(refs, p.x, p.y, BLUE_LIGHT, { shake: 0, hit: 100 });
      if (host) focusHost(refs, host, 1.4);
      break;
    case "ring-blue":
      ringAt(refs, p.x, p.y, BLUE, 420, S(900), 9, 0);
      break;
    case "check":
      textAt(refs, String(step.mark || "Patched").toUpperCase(), p.x, p.y - r - 70,
        { c: BLUE_LIGHT, kind: "stamp", ttl: S(1600) });
      if (host) focusHost(refs, host, 1.4);
      break;
    case "patch-fail":
    case "restore-fail":
    case "block-fail":
      ringAt(refs, p.x, p.y, "#5b6678", 220, S(600), 6, 0);
      break;
    case "green-sweep":
      pushCapped(refs.transients, MAX_TRANSIENTS,
        { kind: "sweep", host, col: GREEN, dir: 1, t: 0, ttl: S(1100), delay: 0 });
      ringAt(refs, p.x, p.y, GREEN, 700, S(1100), 16, 0);
      burstAt(refs, p.x, p.y, GREEN, 44, 640, 0);
      if (host) focusHost(refs, host, 1.4);
      break;
    case "reconnect":
      ringAt(refs, p.x, p.y, GREEN, 420, S(900), 9, S(200));
      break;
    case "block-pulse":
      ringAt(refs, 800, 450, BLUE, 420, S(900), 9, 0);
      if (step.ip) {
        textAt(refs, String(step.ip), 800, 450 - 120, { c: BLUE_LIGHT, kind: "plain", ttl: S(2000) });
      }
      break;
    case "bounce-blocked":
      burstAt(refs, p.x, p.y, BLUE_LIGHT, 34, 700, 0);
      ringAt(refs, p.x, p.y, BLUE, 200, S(500), 8, 0);
      textAt(refs, String(step.mark || "Blocked").toUpperCase(), p.x, p.y - r - 80,
        { c: BLUE_LIGHT, kind: "burst", ttl: S(1600) });
      shake(refs, 14);
      impactAt(refs, p.x, p.y, BONE, { shake: 0, hit: 100 });
      if (host) focusHost(refs, host, 1.4);
      break;
    case "idle-tick": {
      const side = ev.actor === "blue" ? refs.src.blue : refs.src.red;
      pushCapped(refs.transients, MAX_TRANSIENTS,
        { kind: "idle", x: side.x, y: side.y, col: ev.actor === "blue" ? BLUE : RED, t: 0, ttl: S(350), delay: 0 });
      break;
    }
    case "verdict":
      showAuditor(refs, ev);
      break;
    default:
      break;
  }
  return S(BASE_MS[step.effect] || 400);
}

// Serial event queue: animations never pile up. Static end-state is
// already applied by renderArenaStatic, so dropping queued transient
// work under pressure is always safe.
export function arenaNotify(refs, ev, states, speed) {
  if (!ev || !refs) return;
  const spd = Number(speed) || 1;
  refs.speed = spd;
  let steps = choreograph(ev, { states, hosts: refs.hosts });
  if (spd >= 4) steps = steps.filter((step) => !step.secondary);
  if (steps.length === 0) return;
  refs.queue.push({ ev, steps, speed: spd });
  if (refs.queue.length > 6) refs.queue.splice(0, refs.queue.length - 6);
  if (!refs.playing) pump(refs);
}

function pump(refs) {
  refs.playing = true;
  const myRun = refs.runId;
  const next = () => {
    if (myRun !== refs.runId) return;
    const job = refs.queue.shift();
    if (!job) {
      refs.playing = false;
      return;
    }
    let dur = 0;
    for (const step of job.steps) {
      if (myRun !== refs.runId) return;
      dur = Math.max(dur, runStep(refs, step, job.ev, job.speed));
    }
    setTimeout(next, Math.min(Math.max(dur, 120), 1200));
  };
  next();
}

// Thought-stream chip hover: flash the cited log's host once.
export function flashArenaHost(refs, host) {
  if (!refs || !host || !refs.nodes[host]) return;
  const n = refs.nodes[host];
  n.flicker = 0.6;
  ringAt(refs, n.x, n.y, BONE, n.r * 2.4, 700, 6, 0);
}

// One brief stinger the first time red signs in during an exercise.
export function firstAccessStinger(refs, host) {
  if (!refs) return;
  const n = host && refs.nodes[host] ? refs.nodes[host] : { x: 800, y: 450, r: 88 };
  textAt(refs, "FIRST ACCESS", n.x, n.y - n.r - 80, { c: RED, kind: "burst", ttl: 1800 });
  ringAt(refs, n.x, n.y, RED, 560, 1000, 14, 0);
  if (reducedMotion()) return;
  burstAt(refs, n.x, n.y, RED, 30, 640, 0);
  impactAt(refs, n.x, n.y, BONE, { shake: 14, hit: 90 });
}

// Climax stinger for a Secret exposed end card: red and gold glitch.
export function glitchStinger(refs) {
  if (!refs || reducedMotion()) return;
  glitchCanvas(refs);
  flash(refs, GOLD, 0.35);
  shake(refs, 18);
}

// Scrub, jump or view switch: drop everything transient immediately.
export function cancelArena(refs) {
  if (!refs) return;
  refs.runId += 1;
  refs.queue.length = 0;
  refs.playing = false;
  refs.pending.length = 0;
  refs.particles.length = 0;
  refs.texts.length = 0;
  refs.transients.length = 0;
  refs.bursts.length = 0;
  refs.speedLines.length = 0;
  if (refs.ambient) {
    refs.ambient.dots.length = 0;
    refs.ambient.next = 0;
  }
  refs.flash = null;
  refs.trauma = 0;
  refs.camTarget = { ...CAM_HOME };
  hideAuditor(refs);
  if (refs.canvas) refs.canvas.classList.remove("glitch");
}

// ---------- auditor card ----------

function showAuditor(refs, ev) {
  const card = verdictCard(ev);
  const hud = refs.hud;
  if (!card || !hud.aud) return;
  if (hud.audNum) hud.audNum.textContent = `${card.score == null ? "-" : card.score}/10`;
  if (hud.audWord) hud.audWord.textContent = String(card.word || "").toUpperCase();
  if (hud.audCite) hud.audCite.textContent = card.ids.length > 0 ? `cites ${card.ids.join(", ")}` : "no cited lines";
  if (hud.audNote) hud.audNote.textContent = String(card.rationale || "").slice(0, 140);
  hud.aud.className = card.word === "justified" ? "show" : "show bad";
  layOutRail(refs);
  if (refs.audTimer) clearTimeout(refs.audTimer);
  refs.audTimer = setTimeout(() => hideAuditor(refs), 4200);
}

function hideAuditor(refs) {
  if (!refs) return;
  if (refs.audTimer) {
    clearTimeout(refs.audTimer);
    refs.audTimer = 0;
  }
  if (refs.hud.aud) refs.hud.aud.className = "";
  layOutRail(refs);
}

// Keep the thought panels clear of the score block, the auditor
// card, and the caption from measured boxes. Runs on auditor
// show and hide and on every canvas refit (resize, tab switch).
export function layOutRail(refs) {
  if (!refs || !refs.hud) return null;
  const hud = refs.hud;
  return applyRail({
    wrap: hud.wrapEl,
    mid: hud.midEl,
    aud: hud.aud,
    cap: hud.cap,
    red: hud.thoughtRed,
    blue: hud.thoughtBlue,
  });
}

// ---------- static snapshot ----------
// Scrub-safe end state: host visuals, bars, caption and banners are
// rebuilt purely from the event slice, with no leftover particles.

function lastLaneEvent(slice) {
  for (let i = slice.length - 1; i >= 0; i -= 1) {
    const actor = slice[i] && slice[i].actor;
    if (actor === "red" || actor === "blue") return slice[i];
  }
  return null;
}

const OUTCOME_TEXT = {
  blue_held: "Exercise over: Blue held.",
  red_succeeded: "Exercise over: Red read the protected file.",
  stalemate: "Exercise over: no result.",
};

function applyStatic(refs, states, blocks, slice, instant) {
  const secret = secretRead(slice);
  const flagged = {};
  for (const ev of slice) {
    if (ev && ev.actor === "blue" && ev.tool === "report_finding" && (ev.result || {}).ok === true) {
      const h = effectHost(ev, refs.hosts);
      if (h) flagged[h] = true;
    }
  }
  for (const key of refs.hosts) {
    const n = refs.nodes[key];
    if (!n) continue;
    const st = states[key];
    const block = blocks[key];
    n.flag = Boolean(flagged[key]);
    if (block === "breached") {
      n.tint = "comp";
      n.tag = "BREACHED";
      n.floodTarget = 1;
      n.shTarget = 0;
    } else if (block === "isolated") {
      n.tint = "iso";
      n.tag = "ISOLATED";
      n.floodTarget = 0;
      n.shTarget = 1;
    } else if (block === "patched") {
      n.tint = "patched";
      n.tag = "ROTATED";
      n.floodTarget = 0;
      n.shTarget = 0;
    } else if (st === "restored") {
      n.tint = "restored";
      n.tag = "RESTORED";
      n.floodTarget = 0;
      n.shTarget = 0;
    } else if (st === "probed") {
      n.tint = "probed";
      n.tag = "PROBED";
      n.floodTarget = 0;
      n.shTarget = 0;
    } else {
      n.tint = "ok";
      n.tag = "CLEAN";
      n.floodTarget = 0;
      n.shTarget = 0;
    }
    if (secret && key === refs.jewel) {
      n.openTarget = 1;
      n.goldTarget = 1;
    } else {
      n.openTarget = 0;
      n.goldTarget = 0;
    }
    if (instant) {
      n.flood = n.floodTarget;
      n.open = n.openTarget;
      n.gold = n.goldTarget;
      n.sh = n.shTarget;
      n.scanT = 0;
      n.flicker = 0;
      n.pop = 0;
    } else if (n.prevTint !== undefined && n.tint !== n.prevTint) {
      n.flicker = 0.5;
      n.pop = 1;
    }
    n.prevTint = n.tint;
  }
}

export function renderArenaStatic(refs, events, cursor, opts, instant) {
  const list = events || [];
  const at = Math.max(0, Math.min(list.length, cursor || 0));
  const slice = list.slice(0, at);
  const o = opts || {};
  // Rebuild geometry only when the exercise layout changed; scrubbing
  // within one exercise keeps the same nodes.
  if (refs) ensureLayout(refs, o.layout || null);
  const layout = (refs && refs.layout) || o.layout || classicLayout();
  const hosts = layoutHosts(layout);
  const weights = { ...DEFAULT_WEIGHTS, ...(o.weights || {}) };
  const states = reduceHostStates(slice, hosts);
  const blocks = hostBlocks(slice, hosts);
  const prog = redNotches(list, slice);
  const pressure = pressureValue(slice, layout);
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
  let caption = `${hosts.length} ${hostWord}. One secret.`;
  let sub = list.length === 0 ? "No exercise loaded." : "Waiting for the first move.";
  if (ended) {
    // Honest end line: the display outcome plus its plain sentence.
    const display = o.display || null;
    caption = display ? `Exercise over: ${display.label}.` : (OUTCOME_TEXT[o.finalScore.outcome] || "Exercise over.");
    sub = display ? display.line : `Final score ${Math.round(score)} after ${at} moves.`;
  } else if (latest) {
    tag = latest.actor === "red" ? "RED" : "BLUE";
    tone = latest.actor === "red" ? "red" : "blue";
    // The caption bar fits about two lines: keep the sentence short.
    const sentence = describeEvent(latest);
    caption = sentence.length > 110 ? `${sentence.slice(0, 107).trimEnd()}...` : sentence;
    sub = `Move ${at} of ${list.length}: ${shortResult(latest).slice(0, 90)}`;
  }

  if (refs) {
    if (Math.abs(pressure - refs.snap.pressure) >= 0.3 && refs.hud.tug && !reducedMotion()) {
      refs.hud.tug.classList.remove("hit");
      void refs.hud.tug.offsetWidth;
      refs.hud.tug.classList.add("hit");
    }
    refs.snap = {
      states, blocks, hostOrder: hosts, pressure,
      level: prog.level, notches: prog.notches,
      score, outcome: ended ? o.finalScore.outcome : null,
      moves: at, total: list.length, label: o.label || "Exercise",
      weights, secret: secretRead(slice),
      caption, sub, tag, tagTone: tone,
      redLine: redLast ? describeEvent(redLast) : "Red is waiting.",
      blueLine: blueLast ? describeEvent(blueLast) : "Blue is waiting.",
    };
    applyStatic(refs, states, blocks, slice, Boolean(instant));
  }
  return { states, blocks, pressure, level: prog.level, score };
}


