// Side-by-side red and blue reasoning streams, newest cards on top.
import { formatArgs, shortResult } from "./state.js";

function tagFor(actor, tool, attackMap) {
  if (!attackMap) return null;
  if (actor === "red") {
    const entry = attackMap.red?.[tool];
    return entry ? { text: entry.technique, cls: "red", title: entry.name } : null;
  }
  const entry = attackMap.blue?.[tool];
  return entry ? { text: entry.label, cls: "blue", title: tool } : null;
}

function card(ev, index, attackMap, onEvidence) {
  const wrap = document.createElement("article");
  wrap.className = "card";

  const head = document.createElement("div");
  head.className = "card-head";
  const num = document.createElement("span");
  num.className = "card-index";
  num.textContent = `#${index}`;
  const tool = document.createElement("span");
  tool.className = "card-tool";
  tool.textContent = ev.tool || "unknown";
  head.append(num, tool);
  const tag = tagFor(ev.actor, ev.tool, attackMap);
  if (tag) {
    const chip = document.createElement("span");
    chip.className = `tag ${tag.cls}`;
    chip.textContent = tag.text;
    chip.title = tag.title;
    head.append(chip);
  }
  wrap.append(head);

  const args = document.createElement("div");
  args.className = "card-args";
  args.textContent = formatArgs(ev.args);
  wrap.append(args);

  const result = document.createElement("div");
  result.className = "card-result" + ((ev.result || {}).ok === false ? " fail" : "");
  result.textContent = shortResult(ev);
  wrap.append(result);

  if (ev.reasoning_summary) {
    const reason = document.createElement("div");
    reason.className = "card-reason";
    reason.textContent = ev.reasoning_summary;
    wrap.append(reason);
  }

  const refs = ev.evidence_refs || [];
  if (refs.length > 0) {
    const box = document.createElement("div");
    box.className = "card-evidence";
    for (const ref of refs.slice(0, 5)) {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.textContent = ref;
      chip.addEventListener("click", () => onEvidence(ref));
      box.append(chip);
    }
    wrap.append(box);
  }
  return wrap;
}

function fillColumn(root, countEl, events, actor, cursor, attackMap, onEvidence, emptyText) {
  root.innerHTML = "";
  const visible = events.slice(0, cursor).map((ev, i) => ({ ev, index: i + 1 })).filter(({ ev }) => ev.actor === actor);
  countEl.textContent = String(visible.length);
  if (visible.length === 0) {
    const idle = document.createElement("p");
    idle.className = "empty";
    idle.textContent = emptyText;
    root.append(idle);
    return;
  }
  for (const { ev, index } of visible.reverse().slice(0, 60)) {
    root.append(card(ev, index, attackMap, onEvidence));
  }
}

export function renderStreams(roots, events, cursor, attackMap, onEvidence) {
  fillColumn(roots.red, roots.redCount, events, "red", cursor, attackMap, onEvidence, "Red reasoning appears here during an exercise.");
  fillColumn(roots.blue, roots.blueCount, events, "blue", cursor, attackMap, onEvidence, "Blue reasoning appears here during an exercise.");
}
