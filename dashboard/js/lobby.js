// Judge-first lobby: hero actions, a dealing moment for unseen
// networks, featured replays, and a collapsed archive. Rendering
// plus the small pure rules behind it (scenario draw, story lines,
// durations, featured partition); ranking and outcomes live in
// state.js. Runs whose ids start with fake- never appear anywhere.
import { classicLayout } from "./layout.js";
import { eventHost, motionReduced, rankFeatured } from "./state.js";
import { mountArenaThumb, mountThumbnail, outcomeGlyph, thumbnailState } from "./thumbnail.js";

const FLAG_LABEL = { test: "test", fallback: "fallback", scripted: "scripted", mismatch: "title mismatch" };

// Outcome key a curated card title promises, from its plain words:
// "contained" titles promise Contained, "exposed" titles promise a
// secret read. Anything else promises nothing (null).
export function titleOutcomeKey(title) {
  const text = String(title || "").toLowerCase();
  if (text.includes("exposed")) return "secret-exposed";
  if (text.includes("contained")) return "contained";
  return null;
}

export const HELDOUT_MIN = 9001;
export const HELDOUT_MAX = 9020;
export const MAX_FEATURED = 6;

// One random unseen network: a held-out seed plus a difficulty
// weighted toward medium and hard. randomFn defaults to Math.random
// and is injectable for tests.
export function drawLiveScenario(randomFn) {
  const rand = typeof randomFn === "function" ? randomFn : Math.random;
  const seed = HELDOUT_MIN + Math.floor(rand() * (HELDOUT_MAX - HELDOUT_MIN + 1));
  const roll = rand();
  const difficulty = roll < 0.2 ? "easy" : roll < 0.6 ? "medium" : "hard";
  return { seed, difficulty, token: `gen:${seed}:${difficulty}` };
}

// Plain noun for a host role, for story lines. Never a hostname.
export function hostNoun(role) {
  const text = String(role || "").toLowerCase();
  if (/workstation/.test(text)) return "workstation";
  if (/data store|database|backup/.test(text)) return "database";
  if (/jump host/.test(text)) return "jump host";
  if (/file share/.test(text)) return "file share";
  if (/portal|\bapi\b|public|front end/.test(text)) return "public site";
  if (/report|\bapp\b/.test(text)) return "app server";
  return "host";
}

function isSignIn(ev) {
  return Boolean(ev && ev.actor === "red" && (ev.result || {}).authenticated === true);
}

function isContain(ev) {
  return Boolean(ev && ev.actor === "blue" && (ev.result || {}).ok === true &&
    (ev.tool === "isolate_host" || ev.tool === "patch_service"));
}

function isSecretRead(ev) {
  return Boolean(ev && ev.actor === "red" && ev.tool === "read_file" &&
    (ev.result || {}).ok === true &&
    String((ev.result || {}).path || "").startsWith("/secret/"));
}

// One story line per exercise, from the honest outcome key plus
// move positions. roles maps host ids to role strings.
export function storyLine(events, roles, outcomeKey) {
  const lane = (events || []).filter((ev) => ev && (ev.actor === "red" || ev.actor === "blue"));
  const authAt = lane.findIndex(isSignIn);
  const holdAt = lane.findIndex(isContain);
  const noun = (ev) => hostNoun(ev ? (roles || {})[eventHost(ev)] : "");
  const verb = (ev) => (ev && ev.tool === "patch_service" ? "patched" : "isolated");
  if (outcomeKey === "secret-exposed") {
    const at = lane.findIndex(isSecretRead);
    return at < 0
      ? "Red read the protected file."
      : `Red read the protected file on move ${at + 1}.`;
  }
  if (outcomeKey === "contained" && authAt >= 0 && holdAt >= 0) {
    if (holdAt <= authAt) {
      return `Blue ${verb(lane[holdAt])} the ${noun(lane[holdAt])} on move ${holdAt + 1} and held.`;
    }
    const gap = holdAt - authAt;
    return `Blue ${verb(lane[holdAt])} the ${noun(lane[holdAt])} ${gap} event${gap === 1 ? "" : "s"} after the first sign-in.`;
  }
  if ((outcomeKey === "contained" || outcomeKey === "held") && holdAt >= 0) {
    return `Blue ${verb(lane[holdAt])} the ${noun(lane[holdAt])} and red never got in.`;
  }
  if (outcomeKey === "held" || outcomeKey === "no-intrusion") {
    return "Red never found a way in.";
  }
  return "An unfinished exercise.";
}

// Wall-clock duration from event timestamps, falling back to the
// move count when timestamps are missing or out of order.
export function formatDuration(firstTs, lastTs, moves) {
  const start = Date.parse(firstTs || "");
  const end = Date.parse(lastTs || "");
  if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
    const secs = Math.round((end - start) / 1000);
    if (secs < 90) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    return `${mins}m ${String(secs % 60).padStart(2, "0")}s`;
  }
  return `${moves} move${moves === 1 ? "" : "s"}`;
}

// Split classified cards into the curated featured set (in the
// curated order, at most six) and the ranked archive. Unknown
// featured ids are skipped; fake- fixtures never appear.
export function partitionLobby(cards, featuredIds) {
  const real = (cards || []).filter((card) => !String((card && card.id) || "").startsWith("fake-"));
  const byId = new Map(real.map((card) => [card.id, card]));
  const featured = [];
  for (const id of featuredIds || []) {
    if (featured.length >= MAX_FEATURED) break;
    const card = byId.get(id);
    if (card && !featured.includes(card)) featured.push(card);
  }
  const picked = new Set(featured.map((card) => card.id));
  return { featured, archive: rankFeatured(real.filter((card) => !picked.has(card.id))) };
}

// Mini range thumbnail: an arena miniature on canvas, painted
// lazily and cached per card. opts: { w, h, key, label }.
export function thumbnail(layout, tstate, opts) {
  const options = opts || {};
  const clean = layout && layout.hosts ? layout : classicLayout();
  const state = tstate && tstate.hosts ? tstate : thumbnailState([], clean);
  const thumb = document.createElement("canvas");
  thumb.className = "lobby-thumb";
  thumb.setAttribute("role", "img");
  thumb.setAttribute("aria-label",
    options.label || `Range map: ${clean.hosts.map((host) => host.id).join(", ")}`);
  thumb.title = clean.hosts.map((host) => host.id).join(", ");
  mountThumbnail(thumb, clean, state, {
    w: options.w || 120,
    h: options.h || 68,
    key: options.key || "",
    reduced: motionReduced(),
  });
  return thumb;
}

// Arena-frame thumbnail for a classified replay: the episode's
// real stage at its poster event. env: { art, attackMap }. The
// canvas mounts empty and fills in from the idle queue; featured
// cards also persist through sessionStorage.
export function arenaThumb(model, env, opts) {
  const options = opts || {};
  const thumb = document.createElement("canvas");
  thumb.className = "lobby-thumb";
  thumb.setAttribute("role", "img");
  thumb.setAttribute("aria-label",
    options.label || `Arena frame for ${model.id}: ${model.outcome.label}`);
  thumb.title = `${model.id}: ${model.outcome.label}`;
  mountArenaThumb(thumb, model, env || {}, {
    w: options.w || 120,
    h: options.h || 68,
    featured: options.featured,
    cursor: options.cursor,
  });
  return thumb;
}

function outcomeBadge(outcome) {
  const badge = document.createElement("span");
  badge.className = "lobby-outcome";
  badge.textContent = (outcome && outcome.label) || "Unknown";
  return badge;
}

function badgesRow(flags) {
  const badges = document.createElement("div");
  badges.className = "lobby-badges";
  for (const flag of flags || []) {
    const badge = document.createElement("span");
    badge.className = "lobby-badge";
    badge.textContent = FLAG_LABEL[flag] || flag;
    badges.append(badge);
  }
  return badges;
}

function playButton(id, label, callbacks) {
  const play = document.createElement("button");
  play.type = "button";
  play.className = "btn small primary";
  play.textContent = label || "Play";
  play.addEventListener("click", () => callbacks.onPlay(id));
  return play;
}

// Provenance line for real runs: "bench / episode, seed N".
// Pure; provenanceLine renders it into a card.
export function provText(prov) {
  if (!prov || !prov.bench || !prov.episode) return "";
  return `${prov.bench} / ${prov.episode}, seed ${prov.seed ?? "?"}`;
}

function provenanceLine(model) {
  const text = provText(model && model.provenance);
  if (!text) return null;
  const line = document.createElement("p");
  line.className = "lobby-meta lobby-prov";
  const chip = document.createElement("span");
  chip.className = "lobby-badge";
  chip.textContent = "Real run";
  line.append(chip, document.createTextNode(` ${text}`));
  line.title = `Real run: ${text}`;
  return line;
}

function cardTitle(model) {
  return model.title || model.id;
}

// Visible marker for episodes from the broken-sensors benches.
// Pure text; voidLine renders it into a card.
export function voidNoteText(isVoid) {
  return isVoid ? "Void: sensor bug" : "";
}

function voidLine(model) {
  const text = voidNoteText(model && model.void);
  if (!text) return null;
  const line = document.createElement("p");
  line.className = "lobby-meta lobby-void-line";
  const chip = document.createElement("span");
  chip.className = "lobby-badge lobby-void";
  chip.textContent = text;
  line.append(chip);
  line.title = "This episode ran with silently broken sensors; its result is void.";
  return line;
}

function cardTitleAttr(model) {
  return model.title ? `${model.title} (${model.id})` : model.id;
}

// Large featured card: thumbnail, outcome badge, story, duration.
function featureCard(model, callbacks, env) {
  const el = document.createElement("article");
  el.className = "feature-card";
  el.dataset.tone = model.outcome.tone;
  const thumb = arenaThumb(model, env, {
    w: 176, h: 100,
    label: `Arena frame for ${model.id}: ${model.outcome.label}`,
    featured: true,
  });
  thumb.classList.add("feature-thumb");
  const body = document.createElement("div");
  body.className = "feature-body";
  const head = document.createElement("div");
  head.className = "feature-head";
  const id = document.createElement("h3");
  id.className = "lobby-id";
  id.textContent = cardTitle(model);
  id.title = cardTitleAttr(model);
  head.append(id, outcomeBadge(model.outcome));
  const story = document.createElement("p");
  story.className = "feature-story";
  story.textContent = model.story || "An unfinished exercise.";
  const meta = document.createElement("p");
  meta.className = "lobby-meta";
  meta.textContent = `${model.duration || `${model.moves} moves`} · ${model.scenario || "classic"}`;
  if (model.arm) {
    const arm = document.createElement("span");
    arm.className = "lobby-arm";
    arm.textContent = model.arm;
    meta.append(document.createTextNode(" · "), arm);
  }
  const foot = document.createElement("div");
  foot.className = "feature-foot";
  foot.append(playButton(model.id, "Watch", callbacks));
  body.append(head);
  const voidMark = voidLine(model);
  if (voidMark) body.append(voidMark);
  body.append(story, meta);
  const provLine = provenanceLine(model);
  if (provLine) body.append(provLine);
  if (model.flags && model.flags.length > 0) body.append(badgesRow(model.flags));
  body.append(foot);
  el.append(thumb, body);
  return el;
}

// Compact archive card: outcome and shape, never a bare score.
// Archive rows mount a real arena frame only while on screen: a
// static outcome glyph holds the slot, a canvas mounts over it on
// entry (or hover/focus), and the canvas is released on exit. The
// bitmap stays cached, so scrolling back blits from memory.
function archiveCard(model, callbacks, env) {
  const el = document.createElement("article");
  el.className = "lobby-card";
  el.dataset.tone = model.outcome.tone;
  const head = document.createElement("div");
  head.className = "lobby-card-head";
  const id = document.createElement("h3");
  id.className = "lobby-id";
  id.textContent = cardTitle(model);
  id.title = cardTitleAttr(model);
  head.append(id, outcomeBadge(model.outcome));
  const meta = document.createElement("p");
  meta.className = "lobby-meta";
  meta.textContent = `${model.moves} moves · ${model.scenario || "classic"}`;
  if (model.arm) {
    const arm = document.createElement("span");
    arm.className = "lobby-arm";
    arm.textContent = model.arm;
    meta.append(document.createTextNode(" · "), arm);
  }
  const foot = document.createElement("div");
  foot.className = "lobby-foot";
  const slot = document.createElement("span");
  slot.className = "lobby-states thumb-slot";
  slot.append(outcomeGlyph(model.outcome));
  foot.append(slot, playButton(model.id, "Play", callbacks));
  let live = null;
  const upgrade = () => {
    if (live) return;
    live = arenaThumb(model, env, {
      w: 120, h: 68,
      label: `Arena frame for ${model.id}: ${model.outcome.label}`,
    });
    live.classList.add("lobby-states");
    slot.append(live);
  };
  const downgrade = () => {
    if (!live) return;
    if (live._thumbSeen) {
      live._thumbSeen.disconnect();
      live._thumbSeen = null;
    }
    slot.removeChild(live);
    live = null;
  };
  el.addEventListener("mouseenter", upgrade);
  el.addEventListener("focusin", upgrade);
  if (typeof IntersectionObserver === "function") {
    const watch = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) upgrade();
        else downgrade();
      }
    }, { rootMargin: "80px" });
    watch.observe(el);
    el._thumbWatch = watch;
  } else {
    upgrade();
  }
  el.append(head);
  const voidMark = voidLine(model);
  if (voidMark) el.append(voidMark);
  el.append(meta);
  const provLine = provenanceLine(model);
  if (provLine) el.append(provLine);
  if (model.flags && model.flags.length > 0) el.append(badgesRow(model.flags));
  el.append(foot);
  return el;
}

// model: { cards, featuredIds, loading }. Renders the featured row
// and the archive; the hero lives in static HTML.
export function renderLobby(featuredRoot, archiveRoot, archiveCount, model, callbacks, env) {
  const { featured, archive } = partitionLobby(model.cards, model.featuredIds);
  featuredRoot.innerHTML = "";
  if (model.loading && model.cards.length === 0) {
    const idle = document.createElement("p");
    idle.className = "empty";
    idle.textContent = "Reading the run shelf…";
    featuredRoot.append(idle);
  } else if (featured.length === 0) {
    const idle = document.createElement("p");
    idle.className = "empty";
    idle.textContent = model.loading
      ? "Reading the run shelf…"
      : "No featured replays yet. Start a live exercise to make one.";
    featuredRoot.append(idle);
  } else {
    for (const item of featured) featuredRoot.append(featureCard(item, callbacks, env));
  }
  archiveRoot.innerHTML = "";
  archiveCount.textContent = model.loading
    ? `Archive (${archive.length} so far…)`
    : `Archive (${archive.length} run${archive.length === 1 ? "" : "s"})`;
  if (archive.length === 0 && !model.loading) {
    const idle = document.createElement("p");
    idle.className = "empty";
    idle.textContent = "Nothing archived yet.";
    archiveRoot.append(idle);
    return;
  }
  for (const item of archive) archiveRoot.append(archiveCard(item, callbacks, env));
}

// The dealing moment: a drawn scenario preview with its range
// thumbnail before the exercise starts. deal: { phase, scenarioId,
// difficulty, hostCount, layout, error }. phases: drawing, ready,
// error.
export function renderDeal(slot, deal, callbacks) {
  slot.innerHTML = "";
  slot.hidden = false;
  const box = document.createElement("div");
  box.className = "deal";
  box.setAttribute("role", "status");
  if (deal.phase === "drawing") {
    const wait = document.createElement("p");
    wait.className = "deal-wait";
    wait.textContent = "Dealing a new network…";
    box.append(wait);
    slot.append(box);
    return;
  }
  if (deal.phase === "error") {
    const msg = document.createElement("p");
    msg.className = "deal-error";
    msg.textContent = deal.error || "Could not draw a network.";
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "btn small";
    retry.textContent = "Try again";
    retry.addEventListener("click", () => callbacks.onRedeal());
    box.append(msg, retry);
    slot.append(box);
    return;
  }
  const head = document.createElement("p");
  head.className = "deal-eyebrow";
  head.textContent = "Fresh network dealt";
  const title = document.createElement("p");
  title.className = "deal-id";
  title.textContent = deal.scenarioId || "unknown network";
  const meta = document.createElement("p");
  meta.className = "deal-meta";
  meta.textContent =
    `${deal.difficulty || "medium"} · ${deal.hostCount || 0} hosts · held-out seed ${deal.seed || "?"}`;
  const thumb = thumbnail(deal.layout, null, {
    w: 176, h: 100, key: `deal@${deal.scenarioId || "new"}`,
    label: `Range map for ${deal.scenarioId || "the dealt network"}`,
  });
  thumb.classList.add("deal-thumb");
  const btns = document.createElement("div");
  btns.className = "deal-btns";
  const start = document.createElement("button");
  start.type = "button";
  start.className = "btn primary";
  start.textContent = "Start exercise";
  start.addEventListener("click", () => callbacks.onStart());
  const redeal = document.createElement("button");
  redeal.type = "button";
  redeal.className = "btn";
  redeal.textContent = "Re-deal";
  redeal.addEventListener("click", () => callbacks.onRedeal());
  btns.append(start, redeal);
  box.append(head, title, meta, thumb, btns);
  slot.append(box);
  start.focus();
}

export function hideDeal(slot) {
  slot.innerHTML = "";
  slot.hidden = true;
}
