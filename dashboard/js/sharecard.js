// Shareable result card: a 1200x675 PNG drawn on a canvas, saved
// from the end card or the Story results view. The model and layout
// helpers below are pure (no DOM), so node tests them; only the
// paint and download steps touch the browser. Everything is drawn
// with shapes and system fonts, so the download works fully offline,
// including from the static replay bundle. Copy rules: exercise,
// containment, and intrusion words only, no em dashes, and never
// any secret or local path on the card.

export const CARD_W = 1200;

export const CARD_H = 675;

const SANS = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif';

const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

// Dark card theme, matching the dashboard dark theme tokens.
const INK = {
  bg: "#17191e",
  panel: "#20232a",
  line: "#353b47",
  text: "#e9e5da",
  muted: "#a3a097",
  blue: "#7fb3e8",
  red: "#e08a76",
  amber: "#d9a94a",
  gray: "#a3a097",
  edge: "#4a5160",
  jewel: "#d9a94a",
};

// Host state ring colors, matching the dashboard dark theme nodes.
const STATE_INK = {
  clean: "#8fae9b",
  probed: "#d9a94a",
  "signed-in": "#e09a5f",
  compromised: "#e08a76",
  isolated: "#7fb3e8",
  restored: "#7fbfa8",
};

const BANNED = ["attack", "battle", "enemy", "kill", "war ", "warfare", "combat"];

const EMDASH = "—";

// True when card text follows the copy rules: no combat words and
// no em dashes. Mirrors the dashboard show-copy check.
export function cardCopyClean(text) {
  const value = String(text || "");
  if (value.includes(EMDASH)) return false;
  const lower = ` ${value.toLowerCase()} `;
  for (const word of BANNED) {
    if (lower.includes(word)) return false;
  }
  return true;
}

// True when a value looks like a secret token: a key prefix, a key
// label, or one unbroken run of 32 or more word characters. Bench
// and episode ids are dashed words, so they never match.
export function looksSecret(text) {
  const value = String(text || "");
  if (/sk-[A-Za-z0-9]{8,}/.test(value)) return true;
  const lowerKey = value.toLowerCase();
  if (lowerKey.includes("key") && lowerKey.includes("api")) return true;
  if (/AKIA[0-9A-Z]{10,}/.test(value)) return true;
  if (/[A-Za-z0-9_]{32,}/.test(value)) return true;
  return false;
}

// True when a value looks like a local path or URL, which must
// never appear on the card.
export function looksLocalPath(text) {
  const value = String(text || "").trim();
  if (!value) return false;
  if (value.startsWith("/") || value.startsWith("~")) return true;
  if (/^[A-Za-z]:[\\/]/.test(value)) return true;
  if (value.includes("://")) return true;
  if (value.startsWith("./") || value.startsWith("../")) return true;
  if (/\.(pem|key|crt|env)$/i.test(value)) return true;
  return false;
}

// Control characters become spaces (tab, newline, and carriage
// return fold into the whitespace collapse below). Written with
// char codes so the source holds no raw control bytes.
function stripControls(text) {
  let out = "";
  for (const ch of String(text)) {
    const code = ch.charCodeAt(0);
    if (code < 32 || code === 127) {
      out += " ";
    } else {
      out += ch;
    }
  }
  return out;
}

// One sanitized card string: em dashes become commas, control
// characters and extra spaces collapse, overlong text trims, and
// anything breaking the copy rules, secret-like, or path-like is
// dropped to an empty string.
export function sanitizeCardText(value, maxLen) {
  let text = String(value ?? "");
  text = text.split(EMDASH).join(", ");
  text = stripControls(text);
  text = text.replace(/\s+/g, " ").trim();
  const limit = maxLen || 80;
  if (text.length > limit) text = text.slice(0, limit).trimEnd();
  if (!cardCopyClean(text)) return "";
  if (looksSecret(text) || looksLocalPath(text)) return "";
  return text;
}

// Provenance the card may show: bench, episode, and seed for a
// replay, or null when any of them is missing or unsafe.
export function safeProvenance(prov) {
  if (!prov || typeof prov !== "object") return null;
  const bench = sanitizeCardText(prov.bench, 48);
  const episode = sanitizeCardText(prov.episode, 64);
  const seed = prov.seed;
  const seedText = seed === null || seed === undefined || seed === ""
    ? "?"
    : sanitizeCardText(seed, 16) || "?";
  if (!bench || !episode) return null;
  return { bench, episode, seed: seedText };
}

export function safeOutcomeLabel(label) {
  return sanitizeCardText(label, 40) || "Exercise result";
}

export function toneInk(tone) {
  if (tone === "red") return INK.red;
  if (tone === "blue") return INK.blue;
  if (tone === "amber") return INK.amber;
  return INK.gray;
}

// Pure model for one card: outcome, four figures, caption, host
// nodes with edges, replay provenance, and run label. Every string
// is sanitized, so the painter can draw the model as is.
export function buildCardModel(input) {
  const src = input || {};
  const blueRaw = src.blueScore;
  const blueScore = typeof blueRaw === "number" && Number.isFinite(blueRaw)
    ? String(Math.round(blueRaw * 100) / 100)
    : "--";
  const downRaw = Number(src.downtimePct);
  const downtime = Number.isFinite(downRaw) ? `${downRaw.toFixed(2)}%` : "n/a";
  const eventCount = Number.isInteger(src.eventCount) && src.eventCount >= 0 ? src.eventCount : 0;
  const diffRaw = sanitizeCardText(src.difficulty, 24);
  const difficulty = diffRaw ? diffRaw.charAt(0).toUpperCase() + diffRaw.slice(1) : "n/a";
  const nodes = [];
  for (const host of Array.isArray(src.hosts) ? src.hosts.slice(0, 10) : []) {
    const id = sanitizeCardText(host && host.id, 24);
    if (!id) continue;
    const state = host && STATE_INK[host.state] ? host.state : "clean";
    nodes.push({
      id,
      label: id.length > 12 ? `${id.slice(0, 12)}...` : id,
      state,
      jewel: Boolean(host && host.jewel),
    });
  }
  const ids = new Set(nodes.map((node) => node.id));
  const edges = [];
  const seen = new Set();
  for (const pair of Array.isArray(src.edges) ? src.edges : []) {
    if (!Array.isArray(pair) || pair.length < 2) continue;
    const [a, b] = pair;
    if (a === b || !ids.has(a) || !ids.has(b)) continue;
    const key = [a, b].sort().join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push([a, b]);
  }
  return {
    outcome: safeOutcomeLabel(src.outcomeLabel || src.outcome),
    tone: src.tone === "red" || src.tone === "blue" || src.tone === "amber" ? src.tone : "gray",
    figures: [
      { label: "Blue score", value: blueScore },
      { label: "Downtime", value: downtime },
      { label: "Events", value: String(eventCount) },
      { label: "Difficulty", value: difficulty },
    ],
    caption: sanitizeCardText(src.explanation || src.caption, 96),
    nodes,
    edges,
    provenance: safeProvenance(src.provenance),
    assisted: Boolean(src.assisted),
    runLabel: sanitizeCardText(src.runId, 48),
  };
}

// Pure geometry for one card: fixed text anchors plus the simplified
// host graph spread across the lower panel (one row to six hosts,
// two rows beyond that). Every coordinate stays in the frame.
export function layoutCard(model) {
  const card = model || {};
  const nodes = Array.isArray(card.nodes) ? card.nodes.slice(0, 10) : [];
  const edges = Array.isArray(card.edges) ? card.edges : [];
  const twoRows = nodes.length > 6;
  const rows = twoRows
    ? [nodes.slice(0, Math.ceil(nodes.length / 2)), nodes.slice(Math.ceil(nodes.length / 2))]
    : [nodes];
  const box = { x: 48, y: 462, w: 1104, h: 150 };
  const r = nodes.length <= 4 ? 30 : nodes.length <= 6 ? 26 : 18;
  const centers = twoRows ? [478, 560] : [518];
  const placed = [];
  rows.forEach((row, ri) => {
    row.forEach((node, i) => {
      placed.push({
        id: node.id,
        label: node.label,
        state: node.state,
        jewel: Boolean(node.jewel),
        x: Math.round(box.x + (box.w * (i + 1)) / (row.length + 1)),
        y: centers[ri],
        r,
      });
    });
  });
  const byId = new Map(placed.map((node) => [node.id, node]));
  const links = [];
  for (const pair of edges) {
    if (!Array.isArray(pair) || pair.length < 2) continue;
    const [a, b] = pair;
    const from = byId.get(a);
    const to = byId.get(b);
    if (!from || !to || a === b) continue;
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const dist = Math.hypot(dx, dy) || 1;
    const trim = r + 4;
    links.push({
      x1: Math.round(from.x + (dx / dist) * trim),
      y1: Math.round(from.y + (dy / dist) * trim),
      x2: Math.round(to.x - (dx / dist) * trim),
      y2: Math.round(to.y - (dy / dist) * trim),
    });
  }
  return {
    w: CARD_W,
    h: CARD_H,
    wordmark: { x: 48, y: 92 },
    submark: { x: 48, y: 120 },
    runLabel: { x: 1152, y: 92 },
    outcome: { x: 48, y: 210 },
    caption: { x: 48, y: 252 },
    figures: [{ x: 48 }, { x: 348 }, { x: 648 }, { x: 948 }],
    figureLabelY: 322,
    figureValueY: 372,
    divider: { x1: 48, x2: 1152, y: 408 },
    graphTitle: { x: 48, y: 448 },
    graph: { box, nodes: placed, links },
    footer: { x: 48, y: 650 },
  };
}

function stateWord(state) {
  const words = String(state || "clean").split("-").join(" ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function rr(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arcTo(x + w, y, x + w, y + r, r);
  ctx.lineTo(x + w, y + h - r);
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
  ctx.lineTo(x + r, y + h);
  ctx.arcTo(x, y + h, x, y + h - r, r);
  ctx.lineTo(x, y + r);
  ctx.arcTo(x, y, x + r, y, r);
  ctx.closePath();
}

function diamond(ctx, x, y, r, color) {
  ctx.beginPath();
  ctx.moveTo(x, y - r);
  ctx.lineTo(x + r, y);
  ctx.lineTo(x, y + r);
  ctx.lineTo(x - r, y);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
}

function fitFont(ctx, text, base, maxW, weight) {
  let size = base;
  ctx.font = `${weight} ${size}px ${SANS}`;
  while (size > 20 && ctx.measureText(text).width > maxW) {
    size -= 4;
    ctx.font = `${weight} ${size}px ${SANS}`;
  }
  return ctx.font;
}

function ellipsize(ctx, text, maxW) {
  const value = String(text || "");
  if (ctx.measureText(value).width <= maxW) return value;
  let cut = value.length;
  while (cut > 1 && ctx.measureText(`${value.slice(0, cut)}…`).width > maxW) cut -= 1;
  return `${value.slice(0, cut)}…`;
}

// Paint one card onto a canvas at 1200x675. Accepts a built model
// or raw input; shapes and system fonts only, so the canvas stays
// untainted and the PNG download works fully offline.
export function drawResultCard(canvas, model) {
  const card = model && Array.isArray(model.figures) ? model : buildCardModel(model || {});
  const plan = layoutCard(card);
  canvas.width = CARD_W;
  canvas.height = CARD_H;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = INK.bg;
  ctx.fillRect(0, 0, CARD_W, CARD_H);
  ctx.strokeStyle = INK.line;
  ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, CARD_W - 2, CARD_H - 2);
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";

  ctx.fillStyle = INK.text;
  ctx.font = `700 46px ${SANS}`;
  ctx.fillText("Crucible", plan.wordmark.x, plan.wordmark.y);
  ctx.fillStyle = INK.muted;
  ctx.font = `400 20px ${SANS}`;
  ctx.fillText("Operations Center", plan.submark.x, plan.submark.y);
  if (card.runLabel) {
    ctx.textAlign = "right";
    ctx.font = `400 20px ${MONO}`;
    ctx.fillText(ellipsize(ctx, card.runLabel, 560), plan.runLabel.x, plan.runLabel.y);
    ctx.textAlign = "left";
  }
  if (card.assisted) {
    ctx.textAlign = "right";
    ctx.font = `400 20px ${SANS}`;
    ctx.fillText("Human-assisted exercise", plan.runLabel.x, plan.submark.y);
    ctx.textAlign = "left";
  }

  ctx.fillStyle = toneInk(card.tone);
  ctx.font = fitFont(ctx, card.outcome, 76, 1104, 800);
  ctx.fillText(card.outcome, plan.outcome.x, plan.outcome.y);
  if (card.caption) {
    ctx.fillStyle = INK.muted;
    ctx.font = `400 23px ${SANS}`;
    ctx.fillText(ellipsize(ctx, card.caption, 1104), plan.caption.x, plan.caption.y);
  }

  card.figures.forEach((fig, i) => {
    const fx = plan.figures[i].x;
    ctx.fillStyle = INK.muted;
    ctx.font = `400 21px ${SANS}`;
    ctx.fillText(fig.label, fx, plan.figureLabelY);
    ctx.fillStyle = INK.text;
    ctx.font = `700 46px ${SANS}`;
    ctx.fillText(fig.value, fx, plan.figureValueY);
  });

  ctx.strokeStyle = INK.line;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(plan.divider.x1, plan.divider.y);
  ctx.lineTo(plan.divider.x2, plan.divider.y);
  ctx.stroke();

  ctx.fillStyle = INK.muted;
  ctx.font = `400 21px ${SANS}`;
  ctx.fillText("Exercise range", plan.graphTitle.x, plan.graphTitle.y);
  const jewel = plan.graph.nodes.find((node) => node.jewel);
  if (jewel) {
    ctx.textAlign = "right";
    ctx.font = `400 18px ${SANS}`;
    const legend = "holds the protected file";
    const legendW = ctx.measureText(legend).width;
    diamond(ctx, plan.graphTitle.x + 1104 - legendW - 22, plan.graphTitle.y - 6, 7, INK.jewel);
    ctx.fillStyle = INK.muted;
    ctx.fillText(legend, plan.graphTitle.x + 1104, plan.graphTitle.y);
    ctx.textAlign = "left";
  }
  if (plan.graph.nodes.length === 0) {
    ctx.fillStyle = INK.muted;
    ctx.font = `400 20px ${SANS}`;
    ctx.fillText("No host data", plan.graph.box.x, plan.graph.box.y + 60);
  }
  ctx.strokeStyle = INK.edge;
  ctx.lineWidth = 2;
  for (const link of plan.graph.links) {
    ctx.beginPath();
    ctx.moveTo(link.x1, link.y1);
    ctx.lineTo(link.x2, link.y2);
    ctx.stroke();
  }
  for (const node of plan.graph.nodes) {
    ctx.beginPath();
    ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
    ctx.fillStyle = INK.panel;
    ctx.fill();
    ctx.strokeStyle = STATE_INK[node.state] || STATE_INK.clean;
    ctx.lineWidth = 3;
    ctx.stroke();
    if (node.jewel) {
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.r - 7, 0, Math.PI * 2);
      ctx.strokeStyle = INK.jewel;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      diamond(ctx, node.x, node.y - node.r - 14, 6, INK.jewel);
    }
    ctx.fillStyle = INK.muted;
    ctx.font = `400 16px ${SANS}`;
    ctx.textAlign = "center";
    ctx.fillText(node.label, node.x, node.y + node.r + 22);
    ctx.fillStyle = STATE_INK[node.state] || STATE_INK.clean;
    ctx.font = `400 13px ${SANS}`;
    ctx.fillText(stateWord(node.state), node.x, node.y + node.r + 40);
    ctx.textAlign = "left";
  }

  if (card.provenance) {
    ctx.font = `700 20px ${SANS}`;
    const badgeText = "Real run";
    const badgeW = ctx.measureText(badgeText).width + 36;
    const badgeX = plan.footer.x;
    const badgeY = plan.footer.y - 27;
    rr(ctx, badgeX, badgeY, badgeW, 36, 10);
    ctx.fillStyle = INK.panel;
    ctx.fill();
    ctx.strokeStyle = INK.line;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = INK.text;
    ctx.textAlign = "center";
    ctx.fillText(badgeText, badgeX + badgeW / 2, plan.footer.y - 2);
    ctx.textAlign = "left";
    ctx.fillStyle = INK.muted;
    ctx.font = `400 20px ${MONO}`;
    const line = `${card.provenance.bench} / ${card.provenance.episode}, seed ${card.provenance.seed}`;
    ctx.fillText(ellipsize(ctx, line, 1152 - (badgeX + badgeW + 16)), badgeX + badgeW + 16, plan.footer.y - 2);
  } else if (card.runLabel) {
    ctx.fillStyle = INK.muted;
    ctx.font = `400 20px ${MONO}`;
    ctx.fillText(ellipsize(ctx, `Exercise ${card.runLabel}`, 1104), plan.footer.x, plan.footer.y - 2);
  }
  return canvas;
}

// Safe download name for one run id.
export function cardFilename(runId) {
  const slug = String(runId || "")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean)
    .join("-")
    .slice(0, 48);
  return slug ? `crucible-result-${slug}.png` : "crucible-result-card.png";
}

// Download one painted canvas as a PNG. The canvas holds only
// shapes and system-font text, so this works from file hosting
// with no server and no network.
export function downloadResultCard(canvas, filename) {
  const name = filename || "crucible-result-card.png";
  const url = canvas.toDataURL("image/png");
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  return name;
}
