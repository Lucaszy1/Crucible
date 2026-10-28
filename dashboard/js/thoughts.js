// Thought streams: each side's latest reasoning, typed out beside
// its banner. Each panel shows only that team's own log lines: red
// never sees blue text and blue never sees red text. Evidence chips
// name the log lines the move cited; hovering one flashes the host.
import { describeEvent } from "./story.js";

const CLASSIC_REF_ID = /^(web|app|db|workstation):\d+$/;
const TYPE_MS = 12;
const TYPE_BUDGET_MS = 2600;

// Thought hold time scales with playback speed: at half speed
// the text stays up twice as long, at double speed half as long.
// Missing or bad speeds hold the normal budget. Reduced motion
// skips typing outright, so speed never changes that path.
export function holdMsForSpeed(speed) {
  const value = Number(speed);
  if (!Number.isFinite(value) || value <= 0) return TYPE_BUDGET_MS;
  return Math.round(TYPE_BUDGET_MS / value);
}

// Margin budget: longer thoughts truncate (sentence, else word)
// and open full in the expander.
export const THOUGHT_MAX_CHARS = 220;

// Truncate without ever splitting a word: a sentence end wins when
// one keeps at least half the cap, else the last fitting space.
// Short text passes through; a lone over-long token stays whole.
export function truncateThought(text, max) {
  const clean = String(text || "");
  const cap = Math.max(20, Math.floor(max) || THOUGHT_MAX_CHARS);
  if (clean.length <= cap) return { text: clean, cut: false };
  const floor = cap / 2;
  let cut = -1;
  const re = /[.!?](\s|$)/g;
  let hit = re.exec(clean);
  while (hit !== null) {
    const end = hit.index + 1;
    if (end > cap) break;
    if (end >= floor) cut = end;
    hit = re.exec(clean);
  }
  if (cut < 0) {
    const space = clean.lastIndexOf(" ", cap);
    if (space > 0) cut = space;
  }
  if (cut < 0) return { text: clean, cut: false };
  return { text: `${clean.slice(0, cut).trimEnd()}…`, cut: true };
}

// Evidence refs name a range host and line: upland:12. Hosts come
// from the exercise layout; without one the classic four apply.
function refPattern(hosts) {
  if (!Array.isArray(hosts) || hosts.length === 0) return CLASSIC_REF_ID;
  const safe = hosts.map((host) => String(host).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`^(${safe.join("|")}):\\d+$`);
}

function latestFor(slice, actor) {
  for (let i = slice.length - 1; i >= 0; i -= 1) {
    if (slice[i] && slice[i].actor === actor) return slice[i];
  }
  return null;
}

// Baseten tool-call replies carry no text, so blue's summary is
// the honest fallback below; real thinking replaces it, labeled.
const MODEL_CHOSE_RE = /^Model chose [\w-]+\.$/;

// Full bubble text for one event: the reasoning summary, or the
// event sentence when empty. Blue thinking (Baseten
// reasoning_content, stored on the event) shows labeled as
// thinking: alone when the summary is the fallback, after a real
// summary otherwise. Pure and node-tested; the caller truncates
// and wires the expander exactly as before.
export function thoughtText(ev, actor) {
  if (!ev) return "";
  const raw = String(ev.reasoning_summary || "").trim();
  const base = raw !== "" ? raw : describeEvent(ev);
  if (actor !== "blue") return base;
  const thinking = String(ev.thinking || "").trim().replace(/\s+/g, " ");
  if (thinking === "") return base;
  if (raw === "" || MODEL_CHOSE_RE.test(raw)) return `Thinking: ${thinking}`;
  return `${base} Thinking: ${thinking}`;
}

function thoughtRefs(ev, hosts) {
  if (!ev || !Array.isArray(ev.evidence_refs)) return [];
  const pattern = refPattern(hosts);
  return ev.evidence_refs.filter((ref) => pattern.test(String(ref))).slice(0, 5);
}

function typeInto(textEl, text, reduced, state, speed) {
  if (state.timer) {
    clearInterval(state.timer);
    state.timer = 0;
  }
  if (reduced || text.length === 0) {
    textEl.textContent = text;
    return;
  }
  const budget = holdMsForSpeed(speed);
  const step = Math.max(1, Math.ceil(text.length / (budget / TYPE_MS)));
  let at = 0;
  textEl.textContent = "";
  state.timer = setInterval(() => {
    at += step;
    textEl.textContent = text.slice(0, at);
    if (at >= text.length && state.timer) {
      clearInterval(state.timer);
      state.timer = 0;
    }
  }, TYPE_MS);
}

function renderPanel(panel, slice, actor, opts, state) {
  const ev = latestFor(slice, actor);
  const id = ev ? ev.id : "";
  const chips = panel.chips;
  chips.innerHTML = "";
  if (!ev) {
    if (panel.text.dataset.lastId !== "") {
      panel.text.dataset.lastId = "";
      panel.text.textContent = actor === "red" ? "Red is waiting." : "Blue is waiting.";
    }
    panel.root.classList.remove("cut");
    closeExpander(state, opts);
    return;
  }
  for (const ref of thoughtRefs(ev, opts && opts.hosts)) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "thought-chip";
    chip.textContent = ref;
    const host = String(ref).split(":")[0];
    chip.addEventListener("mouseenter", () => opts.onFlash(host));
    chip.addEventListener("focus", () => opts.onFlash(host));
    chips.append(chip);
  }
  if (panel.text.dataset.lastId === id) return;
  panel.text.dataset.lastId = id;
  closeExpander(state, opts);
  const full = thoughtText(ev, actor);
  const shown = truncateThought(full, THOUGHT_MAX_CHARS);
  panel.text.dataset.full = full;
  panel.root.classList.toggle("cut", shown.cut);
  wireExpander(panel, state, opts);
  typeInto(panel.text, shown.text, opts.reduced, state, opts.speed);
}

// One open expander at a time across both panels.
let openExpander = null;

function expanderEl(panel) {
  let box = panel.root.querySelector(".thought-full");
  if (box) return box;
  box = document.createElement("div");
  box.className = "thought-full";
  box.hidden = true;
  const body = document.createElement("p");
  body.className = "thought-full-text";
  const shut = document.createElement("button");
  shut.type = "button";
  shut.className = "thought-full-close";
  shut.textContent = "Close";
  shut.setAttribute("aria-label", "Close full thought");
  box.append(body, shut);
  panel.root.append(box);
  return box;
}

function openExpanderFor(panel, state, opts) {
  if (panel.text.dataset.lastId === "") return;
  if (!panel.root.classList.contains("cut")) return;
  const box = expanderEl(panel);
  if (openExpander && openExpander !== box) closeExpander(openExpander._state, openExpander._opts);
  box.querySelector(".thought-full-text").textContent = panel.text.dataset.full || "";
  box.hidden = false;
  box._state = state;
  box._opts = opts;
  openExpander = box;
  if (opts && typeof opts.onExpandChange === "function") opts.onExpandChange(true);
}

function closeExpander(state, opts) {
  const box = openExpander;
  if (!box) return;
  box.hidden = true;
  openExpander = null;
  const hook = (opts && opts.onExpandChange)
    || (box._opts && box._opts.onExpandChange);
  if (typeof hook === "function") hook(false);
}

function wireExpander(panel, state, opts) {
  if (panel.text.dataset.wired === "1") return;
  panel.text.dataset.wired = "1";
  panel.text.setAttribute("tabindex", "0");
  panel.text.setAttribute("role", "button");
  panel.text.setAttribute("aria-label", "Open the full thought");
  panel.text.addEventListener("mouseenter", () => openExpanderFor(panel, state, opts));
  panel.root.addEventListener("mouseleave", () => closeExpander(state, opts));
  panel.text.addEventListener("click", () => {
    const box = panel.root.querySelector(".thought-full");
    if (box && !box.hidden) closeExpander(state, opts);
    else openExpanderFor(panel, state, opts);
  });
  panel.text.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openExpanderFor(panel, state, opts);
    } else if (ev.key === "Escape") {
      closeExpander(state, opts);
    }
  });
  panel.root.addEventListener("click", (ev) => {
    if (ev.target && ev.target.classList && ev.target.classList.contains("thought-full-close")) {
      closeExpander(state, opts);
    }
  });
}

// panels: { red: {root, text, chips}, blue: {...} }. opts:
// { reduced, onFlash, onExpandChange }. Hidden when the slice holds
// no moves. onExpandChange(open) fires when the full-text expander
// opens or closes so the caller can pause and resume playback.
export function renderThoughts(panels, slice, opts, state) {
  const st = state || { red: {}, blue: {} };
  const lane = (slice || []).filter((ev) => ev && (ev.actor === "red" || ev.actor === "blue"));
  const show = lane.length > 0;
  for (const side of ["red", "blue"]) {
    panels[side].root.classList.toggle("empty", !show);
  }
  renderPanel(panels.red, lane, "red", opts, st.red);
  renderPanel(panels.blue, lane, "blue", opts, st.blue);
  return st;
}

export function setThoughtsCollapsed(panels, collapsed) {
  for (const side of ["red", "blue"]) {
    panels[side].root.classList.toggle("collapsed", Boolean(collapsed));
    const head = panels[side].head;
    if (head) head.setAttribute("aria-expanded", String(!collapsed));
  }
}

export function initThoughtHeads(panels) {
  for (const side of ["red", "blue"]) {
    const head = panels[side].head;
    if (!head) continue;
    head.addEventListener("click", () => {
      const root = panels[side].root;
      const collapsed = !root.classList.contains("collapsed");
      root.classList.toggle("collapsed", collapsed);
      head.setAttribute("aria-expanded", String(!collapsed));
    });
  }
}
