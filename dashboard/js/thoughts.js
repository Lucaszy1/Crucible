// Thought streams: each side's latest reasoning, typed out beside
// its banner. Each panel shows only that team's own log lines: red
// never sees blue text and blue never sees red text. Evidence chips
// name the log lines the move cited; hovering one flashes the host.
import { describeEvent } from "./story.js";

const REF_ID = /^(web|app|db|workstation):\d+$/;
const TYPE_MS = 12;
const TYPE_BUDGET_MS = 2600;

function latestFor(slice, actor) {
  for (let i = slice.length - 1; i >= 0; i -= 1) {
    if (slice[i] && slice[i].actor === actor) return slice[i];
  }
  return null;
}

function thoughtText(ev) {
  if (!ev) return "";
  const raw = String(ev.reasoning_summary || "").trim();
  return raw !== "" ? raw : describeEvent(ev);
}

function thoughtRefs(ev) {
  if (!ev || !Array.isArray(ev.evidence_refs)) return [];
  return ev.evidence_refs.filter((ref) => REF_ID.test(String(ref))).slice(0, 5);
}

function typeInto(textEl, text, reduced, state) {
  if (state.timer) {
    clearInterval(state.timer);
    state.timer = 0;
  }
  if (reduced || text.length === 0) {
    textEl.textContent = text;
    return;
  }
  const step = Math.max(1, Math.ceil(text.length / (TYPE_BUDGET_MS / TYPE_MS)));
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
    return;
  }
  for (const ref of thoughtRefs(ev)) {
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
  typeInto(panel.text, thoughtText(ev), opts.reduced, state);
}

// panels: { red: {root, text, chips}, blue: {...} }. opts:
// { reduced, onFlash }. Hidden when the slice holds no moves.
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
