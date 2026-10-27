// Activity feed: one plain sentence per move, newest on top, grouped
// by round. Each item expands to the tool call, evidence, technique
// tag, and auditor rationale. Auditor verdicts appear as small icons.
import { describeEvent, groupRounds } from "./story.js";
import { formatArgs } from "./state.js";

export function tagFor(actor, tool, attackMap) {
  if (!attackMap) return null;
  if (actor === "red") {
    const entry = attackMap.red?.[tool];
    return entry ? { text: entry.technique, cls: "red", title: entry.name } : null;
  }
  const entry = attackMap.blue?.[tool];
  return entry ? { text: entry.label, cls: "blue", title: tool } : null;
}

// Small verdict icon: check, caution, or cross, with the verdict word
// exposed on hover for sighted users and to assistive tech.
export function verdictIcon(verdict) {
  const mark = document.createElement("span");
  mark.className = "verdict-mark";
  mark.setAttribute("role", "img");
  const word = verdict || "unjudged";
  mark.setAttribute("aria-label", `Auditor verdict: ${word}`);
  mark.title = `Auditor verdict: ${word}`;
  if (verdict === "justified") {
    mark.dataset.tone = "good";
    mark.textContent = "✓";
  } else if (verdict === "unsupported") {
    mark.dataset.tone = "bad";
    mark.textContent = "✗";
  } else if (verdict === "late" || verdict === "moot") {
    mark.dataset.tone = "neutral";
    mark.textContent = "•";
  } else if (verdict) {
    mark.dataset.tone = "caution";
    mark.textContent = "!";
  } else {
    mark.dataset.tone = "neutral";
    mark.textContent = "•";
  }
  return mark;
}

function detail(ev, attackMap, onEvidence, audit) {
  const box = document.createElement("div");
  box.className = "feed-detail";
  box.hidden = true;

  const tool = document.createElement("div");
  tool.className = "feed-tool";
  tool.textContent = `${ev.tool || "unknown"} ${formatArgs(ev.args)}`;
  box.append(tool);

  const tag = tagFor(ev.actor, ev.tool, attackMap);
  if (tag) {
    const line = document.createElement("div");
    line.className = "feed-tag";
    const chip = document.createElement("span");
    chip.className = `tag ${tag.cls}`;
    chip.textContent = tag.text;
    chip.title = tag.title;
    line.append(chip);
    box.append(line);
  }

  const refs = ev.evidence_refs || [];
  if (refs.length > 0) {
    const row = document.createElement("div");
    row.className = "feed-refs";
    for (const ref of refs.slice(0, 5)) {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.textContent = ref;
      chip.addEventListener("click", (click) => {
        click.stopPropagation();
        onEvidence(ref);
      });
      row.append(chip);
    }
    box.append(row);
  }

  if (audit) {
    const r = audit.result || {};
    const note = document.createElement("p");
    note.className = "feed-audit";
    const score = typeof r.score === "number" ? ` (${r.score}/10)` : "";
    note.textContent = `Auditor: ${r.verdict || "?"}${score}. ${r.rationale || ""}`.trim();
    box.append(note);
  }
  return box;
}

function item(ev, index, attackMap, onEvidence, audit) {
  const wrap = document.createElement("div");
  wrap.className = "feed-item";

  const line = document.createElement("button");
  line.className = "feed-line";
  line.setAttribute("aria-expanded", "false");
  const dot = document.createElement("span");
  dot.className = `marker ${ev.actor === "red" ? "red" : "blue"}`;
  dot.setAttribute("aria-hidden", "true");
  const text = document.createElement("span");
  text.className = "feed-text";
  text.textContent = describeEvent(ev);
  const num = document.createElement("span");
  num.className = "feed-num";
  num.textContent = `#${index}`;
  line.append(dot, text);
  if (audit) line.append(verdictIcon((audit.result || {}).verdict));
  line.append(num);
  wrap.append(line);

  const extra = detail(ev, attackMap, onEvidence, audit);
  line.addEventListener("click", () => {
    extra.hidden = !extra.hidden;
    line.setAttribute("aria-expanded", String(!extra.hidden));
  });
  wrap.append(extra);
  return wrap;
}

export function renderFeed(root, events, cursor, attackMap, onEvidence, auditsByTarget, limit = 60) {
  root.innerHTML = "";
  const lane = events.slice(0, cursor)
    .map((ev, i) => ({ ev, index: i + 1 }))
    .filter(({ ev }) => ev.actor === "red" || ev.actor === "blue")
    .reverse()
    .slice(0, limit);
  if (lane.length === 0) {
    const idle = document.createElement("p");
    idle.className = "empty";
    idle.textContent = "Moves appear here once the exercise starts.";
    root.append(idle);
    return;
  }
  for (const group of groupRounds(lane)) {
    const head = document.createElement("div");
    head.className = "round-head";
    head.textContent = `Round ${group.round}`;
    root.append(head);
    for (const { ev, index } of group.items) {
      const audit = ev.actor === "blue" && auditsByTarget ? auditsByTarget.get(ev.id) : null;
      root.append(item(ev, index, attackMap, onEvidence, audit));
    }
  }
}
