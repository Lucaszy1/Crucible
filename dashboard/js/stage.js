// Cinematic 2.5D stage: the network plays center in an operations
// hall while a red terminal feed types on the left and a blue
// security console answers on the right. This module holds the pure
// core (layout, camera, lane lines, entity levels) plus the canvas
// renderer. Layout never overlaps: lanes, entities, hosts, HUD,
// caption, and the auditor card each own disjoint rects, and the
// camera only pans and zooms inside the slack that keeps every
// host on screen.
import { classicLayout, columnsOf, edgesOf, roleKind } from "./layout.js";
import { displayOutcome, eventHost, redNotches } from "./state.js";

export const STAGE_WORLD = { w: 1600, h: 900 };
// Hosts fit inside this share of their band, leaving slack on every
// side for the camera to dolly without pushing hosts under lanes.
const FIT_SHARE = 0.76;
const DOLLY_ZOOM = 1.22;

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

function laneWithEntity(rect, orientation) {
  if (orientation === "side") {
    const zone = Math.round(Math.min(170, Math.max(96, rect.h * 0.3)));
    return {
      ...rect,
      entity: { x: rect.x + rect.w / 2, y: rect.y + zone / 2, r: Math.min(64, zone / 2 - 8) },
      feed: { x: rect.x + 8, y: rect.y + zone + 4, w: rect.w - 16, h: rect.h - zone - 12 },
    };
  }
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
  let areaTop = top;
  let areaBottom = bottom;
  if (mode === "side") {
    const laneW = Math.round(Math.min(250, Math.max(150, w * 0.17)));
    const blueTop = audBottom === null ? top : Math.max(top, Math.min(bottom - 40, audBottom + gap));
    lanes = {
      red: laneWithEntity({ x: 8, y: top, w: laneW, h: Math.max(40, bottom - top) }, "side"),
      blue: laneWithEntity(
        { x: w - 8 - laneW, y: blueTop, w: laneW, h: Math.max(40, bottom - blueTop) }, "side"),
    };
    band = { x0: 8 + laneW + gap, x1: w - 8 - laneW - gap };
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
    mode, w, h, gap, view, lanes, band,
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
