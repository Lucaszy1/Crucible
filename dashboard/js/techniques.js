// Technique coverage: stage strip plus red and blue tag lists.
import { secretRead } from "./state.js";

function stageOf(map, tool) {
  return map.red?.[tool]?.stage || null;
}

export function renderTechniques(els, attackMap, events, cursor) {
  const slice = events.slice(0, cursor);
  const stages = attackMap?.stages || [];
  els.stages.innerHTML = "";
  const reached = new Set(slice.filter((ev) => ev.actor === "red").map((ev) => stageOf(attackMap, ev.tool)).filter(Boolean));
  if (secretRead(slice)) reached.add("Exfiltrated");
  const contained = slice.some(
    (ev) => ev.actor === "blue" && ["isolate_host", "patch_service"].includes(ev.tool) && (ev.result || {}).ok === true,
  );
  for (const stage of stages) {
    const item = document.createElement("li");
    item.textContent = stage;
    if (reached.has(stage)) item.classList.add("hit");
    els.stages.append(item);
  }
  if (contained) {
    const held = document.createElement("li");
    held.textContent = "Contained";
    held.classList.add("held");
    els.stages.append(held);
  }

  renderList(els.redTech, countBy(slice, "red"), (tool, count) => {
    const entry = attackMap?.red?.[tool];
    return { tag: entry?.technique || tool, cls: "red", name: entry?.name || tool, title: tool, count };
  }, "No red activity yet.");

  renderList(els.blueTech, countBy(slice, "blue"), (tool, count) => {
    const entry = attackMap?.blue?.[tool];
    return { tag: entry?.label || tool, cls: "blue", name: tool, title: tool, count };
  }, "No blue activity yet.");
}

function countBy(slice, actor) {
  const counts = new Map();
  for (const ev of slice) {
    if (ev.actor !== actor) continue;
    counts.set(ev.tool, (counts.get(ev.tool) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function renderList(root, entries, shape, emptyText) {
  root.innerHTML = "";
  if (entries.length === 0) {
    const idle = document.createElement("li");
    idle.className = "empty";
    idle.textContent = emptyText;
    root.append(idle);
    return;
  }
  for (const [tool, count] of entries) {
    const item = document.createElement("li");
    const view = shape(tool, count);
    const tag = document.createElement("span");
    tag.className = `tag ${view.cls}`;
    tag.textContent = view.tag;
    tag.title = view.title;
    const name = document.createElement("span");
    name.className = "tech-name";
    name.textContent = view.name;
    const num = document.createElement("span");
    num.className = "tech-count";
    num.textContent = `x${view.count}`;
    item.append(tag, name, num);
    root.append(item);
  }
}
