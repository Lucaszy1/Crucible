// Arena-miniature thumbnails for exercise cards. Each thumbnail
// draws the episode's scenario layout the way the arena sees it:
// segment columns, role-styled blocks, thin link conduits, and the
// red path through compromised hosts. Outcome shows in the
// picture: red glow where red won ground, blue shield rings where
// blue held, a gold jewel that cracks red when exposed, and a calm
// or alert wash behind it all. Rendering is cheap on purpose: one
// static paint per card, lazily when it scrolls into view, with a
// short path-draw flourish on hover and focus (static under
// reduced motion).
import { classicLayout, columnsOf, edgesOf, roleKind } from "./layout.js";
import { displayOutcome, eventHost } from "./state.js";

const RECON = ["scan_host", "http_request", "list_files"];

function isSecretRead(ev) {
  return Boolean(ev && ev.actor === "red" && ev.tool === "read_file" &&
    (ev.result || {}).ok === true &&
    String((ev.result || {}).path || "").startsWith("/secret/"));
}

// Per-host picture state plus the red path, derived from the
// episode's events. hosts: { id: { state, shield, jewel, exposed } }
// with state one of clean, probed, compromised, held. path lists
// compromised hosts in the order red won them.
export function thumbnailState(events, layout) {
  const clean = layout && layout.hosts ? layout : classicLayout();
  const out = displayOutcome(events || []);
  const hosts = {};
  for (const host of clean.hosts) {
    hosts[host.id] = {
      state: "clean",
      shield: false,
      jewel: host.id === clean.jewel_host,
      exposed: false,
    };
  }
  const path = [];
  const pathed = new Set();
  const compromise = (id) => {
    if (!id || !hosts[id]) return;
    hosts[id].state = "compromised";
    if (!pathed.has(id)) {
      pathed.add(id);
      path.push(id);
    }
  };
  for (const ev of events || []) {
    if (!ev) continue;
    const host = eventHost(ev);
    if (!host || !hosts[host]) continue;
    const ok = (ev.result || {}).ok === true;
    if (ev.actor === "red") {
      if (isSecretRead(ev)) {
        compromise(host);
        hosts[host].exposed = true;
      } else if (ev.tool === "try_credential" && (ev.result || {}).authenticated === true) {
        compromise(host);
      } else if (RECON.includes(ev.tool) && ok && hosts[host].state === "clean") {
        hosts[host].state = "probed";
      }
    } else if (ev.actor === "blue" && ok) {
      if (ev.tool === "isolate_host" || ev.tool === "patch_service") {
        hosts[host].state = "held";
        hosts[host].shield = true;
      } else if (ev.tool === "restore_host") {
        hosts[host].state = "clean";
        hosts[host].shield = false;
      }
    }
  }
  const wash = out.key === "contained" || out.key === "held"
    ? "calm"
    : out.key === "secret-exposed" || out.key === "uncontained"
      ? "alert"
      : "none";
  return { outcome: out.key, wash, hosts, path };
}

// Draw model for a w-by-h canvas: column positions, block sizes by
// role, link pairs, and the red path in order. Pure and tested.
export function thumbnailModel(layout, tstate, w, h) {
  const clean = layout && layout.hosts ? layout : classicLayout();
  const state = tstate && tstate.hosts ? tstate : thumbnailState([], clean);
  const width = Math.max(48, w || 120);
  const height = Math.max(32, h || 68);
  const kinds = {};
  for (const host of clean.hosts) {
    kinds[host.id] = roleKind(host.role, host.id === clean.jewel_host);
  }
  const columns = columnsOf(clean);
  const pad = 8;
  const dense = Math.max(1, ...columns.map((column) => column.length));
  const colW = (width - pad * 2) / 3;
  const rowH = (height - pad * 2) / dense;
  const unit = Math.min(colW * 0.52, rowH * 0.42, 26);
  const blocks = [];
  const seen = new Set();
  columns.forEach((column, ci) => {
    const x = pad + colW * (ci + 0.5);
    column.forEach((id, i) => {
      const y = pad + rowH * (i + 0.5);
      const kind = kinds[id] || "standard";
      const r = kind === "vault" ? unit * 1.05 : kind === "small" ? unit * 0.72 : unit * 0.88;
      const cell = (state.hosts || {})[id] || {};
      blocks.push({
        id,
        x: Math.round(x * 10) / 10,
        y: Math.round(y * 10) / 10,
        r: Math.round(Math.max(4, r) * 10) / 10,
        kind,
        state: cell.state || "clean",
        shield: Boolean(cell.shield),
        jewel: Boolean(cell.jewel),
        exposed: Boolean(cell.exposed),
      });
      seen.add(id);
    });
  });
  const links = edgesOf(clean).filter(([a, b]) => seen.has(a) && seen.has(b));
  const path = (state.path || []).filter((id) => seen.has(id));
  return { w: width, h: height, wash: state.wash || "none", blocks, links, path };
}

const PALETTE = {
  bg: "#10131a",
  grid: "rgba(143,184,255,0.07)",
  glow: "rgba(47,123,255,0.10)",
  link: "#2c3548",
  plate: "#1a2030",
  edge: "#5b6678",
  probed: "#c79a3b",
  red: "#ff3b2f",
  redSoft: "rgba(255,59,47,0.28)",
  blue: "#2f7bff",
  blueSoft: "rgba(47,123,255,0.16)",
  gold: "#ffc83d",
};

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

function pathPoints(model) {
  const at = new Map(model.blocks.map((block) => [block.id, block]));
  return model.path.map((id) => at.get(id)).filter(Boolean);
}

// Paint one static frame. progress reveals the red path from 0
// (hidden) to 1 (fully drawn); stills always use 1.
export function paintThumbnail(ctx, model, progress) {
  const { w, h } = model;
  const shown = progress === undefined || progress === null ? 1 : Math.max(0, Math.min(1, progress));
  ctx.save();
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = PALETTE.bg;
  ctx.fillRect(0, 0, w, h);
  const glow = ctx.createRadialGradient(w / 2, h / 2, 4, w / 2, h / 2, Math.max(w, h) * 0.7);
  glow.addColorStop(0, PALETTE.glow);
  glow.addColorStop(1, "rgba(47,123,255,0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = PALETTE.grid;
  ctx.lineWidth = 1;
  const step = 11;
  ctx.beginPath();
  for (let x = step / 2; x < w; x += step) {
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
  }
  for (let y = step / 2; y < h; y += step) {
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
  }
  ctx.stroke();
  if (model.wash === "calm") {
    ctx.fillStyle = PALETTE.blueSoft;
    ctx.fillRect(0, 0, w, h);
  }
  const at = new Map(model.blocks.map((block) => [block.id, block]));
  ctx.strokeStyle = PALETTE.link;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (const [a, b] of model.links) {
    const p = at.get(a);
    const q = at.get(b);
    if (!p || !q) continue;
    ctx.moveTo(p.x, p.y);
    ctx.lineTo(q.x, q.y);
  }
  ctx.stroke();
  // Red path as a dashed arc through compromised hosts in order.
  const pts = pathPoints(model);
  if (pts.length > 0 && shown > 0) {
    ctx.save();
    ctx.strokeStyle = PALETTE.red;
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 4]);
    ctx.lineCap = "round";
    ctx.beginPath();
    const startX = Math.max(2, pts[0].x - pts[0].r - 8);
    ctx.moveTo(startX, pts[0].y);
    const segs = pts.length;
    const full = Math.floor(shown * segs);
    for (let i = 0; i < full && i < pts.length; i += 1) {
      const prev = i === 0 ? { x: startX, y: pts[0].y } : pts[i - 1];
      const next = pts[i];
      ctx.moveTo(prev.x, prev.y);
      const mx = (prev.x + next.x) / 2;
      const my = (prev.y + next.y) / 2 - Math.min(14, Math.abs(next.x - prev.x) * 0.18);
      ctx.quadraticCurveTo(mx, my, next.x, next.y);
    }
    const frac = shown * segs - full;
    if (frac > 0 && full < pts.length) {
      const prev = full === 0 ? { x: startX, y: pts[0].y } : pts[full - 1];
      const next = pts[full];
      ctx.moveTo(prev.x, prev.y);
      ctx.lineTo(prev.x + (next.x - prev.x) * frac, prev.y + (next.y - prev.y) * frac);
    }
    ctx.stroke();
    ctx.restore();
  }
  for (const block of model.blocks) {
    const jewel = block.jewel;
    const exposed = jewel && block.exposed;
    const breached = block.state === "compromised";
    ctx.save();
    if (breached || exposed) {
      ctx.shadowColor = PALETTE.red;
      ctx.shadowBlur = 8;
    }
    if (block.kind === "vault") {
      ctx.fillStyle = PALETTE.plate;
      ctx.beginPath();
      ctx.arc(block.x, block.y, block.r, 0, 6.283);
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.lineWidth = 2;
      ctx.strokeStyle = exposed ? PALETTE.red : breached ? PALETTE.red : PALETTE.gold;
      ctx.beginPath();
      ctx.arc(block.x, block.y, block.r, 0, 6.283);
      ctx.stroke();
      ctx.strokeStyle = exposed || breached ? PALETTE.redSoft : "#39445a";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(block.x, block.y, block.r * 0.55, 0.6, 2.5);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(block.x, block.y, block.r * 0.55, Math.PI + 0.6, Math.PI + 2.5);
      ctx.stroke();
      if (exposed) {
        ctx.strokeStyle = "#0a0c10";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(block.x - block.r * 0.5, block.y - block.r * 0.7);
        ctx.lineTo(block.x + block.r * 0.1, block.y - block.r * 0.1);
        ctx.lineTo(block.x - block.r * 0.2, block.y + block.r * 0.6);
        ctx.stroke();
      }
    } else {
      const s = block.r * 2;
      ctx.fillStyle = breached ? "#3a1410" : PALETTE.plate;
      roundRectPath(ctx, block.x - block.r, block.y - block.r, s, s, 4);
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.lineWidth = 2;
      ctx.strokeStyle = breached
        ? PALETTE.red
        : block.state === "probed"
          ? PALETTE.probed
          : PALETTE.edge;
      roundRectPath(ctx, block.x - block.r, block.y - block.r, s, s, 4);
      ctx.stroke();
    }
    ctx.restore();
    if (block.shield) {
      ctx.save();
      ctx.strokeStyle = PALETTE.blue;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(block.x, block.y, block.r + 4, 0, 6.283);
      ctx.stroke();
      ctx.restore();
    }
  }
  if (model.wash === "alert") {
    ctx.save();
    ctx.strokeStyle = PALETTE.redSoft;
    ctx.lineWidth = 5;
    ctx.strokeRect(2.5, 2.5, w - 5, h - 5);
    ctx.restore();
  }
  ctx.restore();
  return model;
}

// Painted-model cache: one entry per card key, so re-renders and
// scroll-backs never recompute geometry.
const modelCache = new Map();

export function clearThumbnailCache() {
  modelCache.clear();
}

function drawOn(canvas, paintAt, durationMs) {
  const frames = typeof requestAnimationFrame === "function"
    ? requestAnimationFrame
    : (fn) => setTimeout(fn, 16);
  const start = Date.now();
  const step = () => {
    const t = Math.min(1, (Date.now() - start) / (durationMs || 700));
    const eased = 1 - (1 - t) * (1 - t);
    paintAt(eased);
    if (t < 1 && canvas._animating) frames(step);
    else {
      canvas._animating = false;
      paintAt(1);
    }
  };
  canvas._animating = true;
  frames(step);
}

// Mount one thumbnail canvas: size it, paint it lazily when it
// scrolls into view (immediately without IntersectionObserver),
// and arm the hover/focus path flourish unless reduced motion.
export function mountThumbnail(canvas, layout, tstate, opts) {
  const options = opts || {};
  const w = options.w || 120;
  const h = options.h || 68;
  const key = options.key || "";
  const reduced = Boolean(options.reduced);
  const dpr = Math.min(2, (typeof window !== "undefined" && window.devicePixelRatio) || 1);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  canvas.style.width = `${w}px`;
  canvas.style.height = `${h}px`;
  canvas.dataset.thumb = "1";
  let model = (key && modelCache.get(key)) || null;
  const build = () => {
    if (!model) {
      model = thumbnailModel(layout, tstate, w, h);
      if (key) modelCache.set(key, model);
    }
    return model;
  };
  const paintAt = (progress) => {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.save();
    ctx.scale(dpr, dpr);
    paintThumbnail(ctx, build(), progress);
    ctx.restore();
    canvas._model = model;
  };
  const paint = () => paintAt(1);
  if (typeof canvas.addEventListener === "function" && !reduced) {
    canvas.setAttribute("tabindex", "0");
    canvas.addEventListener("mouseenter", () => drawOn(canvas, paintAt, 700));
    canvas.addEventListener("mouseleave", () => {
      canvas._animating = false;
      paint();
    });
    canvas.addEventListener("focus", () => drawOn(canvas, paintAt, 700));
    canvas.addEventListener("blur", () => {
      canvas._animating = false;
      paint();
    });
  }
  canvas._thumbPaint = paint;
  if (typeof IntersectionObserver === "function") {
    const seen = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          seen.unobserve(canvas);
          paint();
        }
      }
    }, { rootMargin: "80px" });
    seen.observe(canvas);
    canvas._thumbSeen = seen;
  } else {
    paint();
  }
  return canvas;
}

// Paint every mounted but unseen thumbnail at once. Used where
// observers cannot run (tests, print); browsers lazy-paint above.
export function flushThumbnails(scope) {
  const root = scope || (typeof document !== "undefined" ? document : null);
  if (!root || typeof root.querySelectorAll !== "function") return 0;
  let count = 0;
  for (const canvas of root.querySelectorAll("canvas[data-thumb]")) {
    if (canvas._thumbSeen) {
      canvas._thumbSeen.disconnect();
      canvas._thumbSeen = null;
    }
    if (typeof canvas._thumbPaint === "function") {
      canvas._thumbPaint();
      count += 1;
    }
  }
  return count;
}
