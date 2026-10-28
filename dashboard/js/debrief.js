// Exercise debrief card for the Story view. jumpTarget is pure
// (node-tested); renderDebrief builds the card DOM. Copy: exercise,
// containment, defender only; no em dashes.

// Replay cursor that shows through the cited event (1-based), or
// null when the id is not in this exercise.
export function jumpTarget(events, eventId) {
  const index = (events || []).findIndex((ev) => ev && ev.id === eventId);
  return index < 0 ? null : index + 1;
}

function evidenceIds(item) {
  if (item && Array.isArray(item.event_ids)) return item.event_ids;
  if (item && item.event_id) return [item.event_id];
  return [];
}

function evidenceBlock(item, onEvidence) {
  const ids = evidenceIds(item);
  const wrap = document.createElement("details");
  wrap.className = "evidence";
  const summary = document.createElement("summary");
  summary.textContent = `Evidence (${ids.length})`;
  const chips = document.createElement("div");
  chips.className = "chips";
  for (const id of ids) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = id;
    chip.title = `Jump the replay to ${id}`;
    chip.addEventListener("click", () => onEvidence(id));
    chips.append(chip);
  }
  wrap.append(summary, chips);
  return wrap;
}

function metaLine(report) {
  const delay = report.detection_delay_events;
  const delayText = delay === null || delay === undefined
    ? "Detection delay: none recorded"
    : `Detection delay: ${delay} ${delay === 1 ? "move" : "moves"}`;
  const move = report.containment_event_id
    ? `Containment: ${report.containment_move} (${report.containment_event_id})`
    : "Containment: none";
  return `${delayText} · ${move}`;
}

// Build the Debrief card: outcome, delay and containment, timeline,
// strengths, weaknesses with recommendations. Each item carries an
// expandable evidence list whose chips jump the replay.
export function renderDebrief(body, report, hooks) {
  const onEvidence = hooks && hooks.onEvidence ? hooks.onEvidence : () => {};
  const onCopy = hooks && hooks.onCopy ? hooks.onCopy : () => {};
  body.innerHTML = "";
  if (!report) {
    const idle = document.createElement("p");
    idle.className = "empty";
    idle.textContent = "Open an exercise to see its debrief.";
    body.append(idle);
    return;
  }
  if (report.error) {
    const idle = document.createElement("p");
    idle.className = "empty";
    idle.textContent = "The debrief is not available for this exercise.";
    body.append(idle);
    return;
  }
  const outcome = document.createElement("p");
  outcome.className = "story-line";
  outcome.textContent = report.outcome_text || "";
  const meta = document.createElement("p");
  meta.className = "lobby-meta";
  meta.textContent = metaLine(report);
  const layer = document.createElement("p");
  layer.className = "lobby-meta";
  layer.textContent = report.layer === 2
    ? "Plain-language rewrite."
    : "From the log, no model.";
  body.append(outcome, meta, layer);

  const sections = [
    { title: "Timeline", items: report.timeline || [], kind: "moment" },
    { title: "What the defender did well", items: report.strengths || [], kind: "text" },
    { title: "Weaknesses and recommendations", items: report.weaknesses || [], kind: "weakness" },
  ];
  for (const section of sections) {
    const head = document.createElement("h3");
    head.className = "mini-head";
    head.textContent = section.title;
    body.append(head);
    if (section.items.length === 0) {
      const idle = document.createElement("p");
      idle.className = "empty";
      idle.textContent = "None recorded.";
      body.append(idle);
      continue;
    }
    const list = document.createElement("ul");
    list.className = "debrief-list";
    for (const item of section.items) {
      const row = document.createElement("li");
      const line = document.createElement("p");
      if (section.kind === "moment") {
        const strong = document.createElement("strong");
        strong.textContent = item.title || "";
        line.append(strong, document.createTextNode(`: ${item.detail || ""}`));
      } else {
        line.textContent = item.text || "";
      }
      row.append(line);
      if (section.kind === "weakness" && item.recommendation) {
        const rec = document.createElement("p");
        rec.className = "debrief-rec";
        rec.textContent = `Recommendation: ${item.recommendation}`;
        row.append(rec);
      }
      row.append(evidenceBlock(item, onEvidence));
      list.append(row);
    }
    body.append(list);
  }

  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "btn small";
  copy.textContent = "Copy as markdown";
  copy.addEventListener("click", () => onCopy(report.markdown || "", copy));
  body.append(copy);
}
