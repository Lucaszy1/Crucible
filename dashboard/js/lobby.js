// Exercise lobby: cards sorted featured-first with honest outcomes.
// Rendering only; ranking, flags and outcomes come from state.js.
import { HOSTS } from "./state.js";

const FLAG_LABEL = { test: "test", fallback: "fallback", scripted: "scripted" };

function dot(host, blocks) {
  const wrap = document.createElement("span");
  wrap.className = "lobby-dot";
  wrap.dataset.state = (blocks && blocks[host]) || "clean";
  wrap.title = `${host}: ${wrap.dataset.state}`;
  return wrap;
}

function card(model, callbacks) {
  const el = document.createElement("article");
  el.className = "lobby-card";
  el.dataset.tone = model.outcome.tone;

  const head = document.createElement("div");
  head.className = "lobby-card-head";
  const id = document.createElement("h3");
  id.className = "lobby-id";
  id.textContent = model.id;
  id.title = model.id;
  const outcome = document.createElement("span");
  outcome.className = "lobby-outcome";
  outcome.textContent = model.outcome.label;
  head.append(id, outcome);

  const meta = document.createElement("p");
  meta.className = "lobby-meta";
  const score = model.blueScore === null || model.blueScore === undefined ? "--" : model.blueScore;
  meta.textContent = `Score ${score} · ${model.moves} moves`;
  if (model.arm) {
    const arm = document.createElement("span");
    arm.className = "lobby-arm";
    arm.textContent = model.arm;
    meta.append(document.createTextNode(" · "), arm);
  }

  const foot = document.createElement("div");
  foot.className = "lobby-foot";
  const states = document.createElement("span");
  states.className = "lobby-states";
  for (const host of HOSTS) states.append(dot(host, model.blocks));
  const play = document.createElement("button");
  play.type = "button";
  play.className = "btn small primary";
  play.textContent = "Play";
  play.addEventListener("click", () => callbacks.onPlay(model.id));
  foot.append(states, play);

  el.append(head, meta);
  if (model.flags && model.flags.length > 0) {
    const badges = document.createElement("div");
    badges.className = "lobby-badges";
    for (const flag of model.flags) {
      const badge = document.createElement("span");
      badge.className = "lobby-badge";
      badge.textContent = FLAG_LABEL[flag] || flag;
      badges.append(badge);
    }
    el.append(badges);
  }
  el.append(foot);
  return el;
}

// model: { cards, showAll, hiddenCount, loading }. Cards arrive
// progressively as each run's events classify; render is idempotent.
export function renderLobby(root, sub, model, callbacks) {
  root.innerHTML = "";
  const visible = model.showAll
    ? model.cards
    : model.cards.filter((card) => !card.flags || card.flags.length === 0);
  if (model.loading && model.cards.length === 0) {
    sub.textContent = "Loading past exercises…";
    const idle = document.createElement("p");
    idle.className = "empty";
    idle.textContent = "Reading the run shelf…";
    root.append(idle);
    return;
  }
  const hidden = model.cards.length - visible.length;
  sub.textContent = model.loading
    ? `Reading the run shelf… ${model.cards.length} so far.`
    : visible.length === 0
      ? "No exercises to show."
      : `${visible.length} exercise${visible.length === 1 ? "" : "s"}` +
        (hidden > 0 ? ` · ${hidden} test run${hidden === 1 ? "" : "s"} hidden` : "");
  if (visible.length === 0 && !model.loading) {
    const idle = document.createElement("p");
    idle.className = "empty";
    idle.textContent = model.cards.length === 0
      ? "No past runs yet. Start a live exercise to make one."
      : "Every run is hidden. Flip Show all runs to see them.";
    root.append(idle);
    return;
  }
  for (const item of visible) root.append(card(item, callbacks));
}
