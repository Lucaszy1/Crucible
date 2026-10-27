// Slim scrubber plus transport state. Transport state lives in app.js.
// The move strip model below is pure (node-testable); rendering stays
// in renderStrip so tests never need a DOM.

// One pip per red or blue move: team color, current highlight, and
// the auditor verdict when one judges that move. System and auditor
// events are not moves and never get pips.
export function stripModel(eventList, cursor, auditsByTarget) {
  const lane = (eventList || []).filter(
    (ev) => ev && (ev.actor === "red" || ev.actor === "blue"),
  );
  const verdictFor = (id) => {
    if (!id || !auditsByTarget) return null;
    const audit = typeof auditsByTarget.get === "function"
      ? auditsByTarget.get(id)
      : auditsByTarget[id];
    return (audit && audit.result && audit.result.verdict) || null;
  };
  let seen = 0;
  return lane.map((ev) => {
    seen += 1;
    return {
      index: (eventList || []).indexOf(ev),
      move: seen,
      actor: ev.actor,
      tool: ev.tool,
      current: seen === cursorLane(cursor, eventList),
      verdict: verdictFor(ev.id),
    };
  });
}

// Cursor position counted in lane moves, so the strip highlight
// matches the scrubber even when auditor events sit between moves.
function cursorLane(cursor, eventList) {
  let moves = 0;
  const list = eventList || [];
  const at = Math.max(0, Math.min(list.length, cursor || 0));
  for (let i = 0; i < at; i += 1) {
    if (list[i] && (list[i].actor === "red" || list[i].actor === "blue")) moves += 1;
  }
  return moves;
}

export function renderStrip(root, model, onJump, live) {
  root.innerHTML = "";
  if (!model || model.length === 0) {
    root.setAttribute("aria-hidden", "true");
    return;
  }
  root.removeAttribute("aria-hidden");
  for (const pip of model) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `strip-pip ${pip.actor}${pip.current ? " current" : ""}`;
    btn.disabled = Boolean(live);
    btn.title = `Move ${pip.move}: ${pip.actor} ${pip.tool || ""}`.trim();
    btn.setAttribute("aria-label", btn.title + (pip.verdict ? `, auditor ${pip.verdict}` : ""));
    if (pip.verdict) {
      btn.dataset.verdict = pip.verdict;
      const dot = document.createElement("i");
      dot.className = "strip-verdict";
      dot.setAttribute("aria-hidden", "true");
      btn.append(dot);
    }
    btn.addEventListener("click", () => onJump(pip.index + 1));
    root.append(btn);
  }
}

export function renderTransport(els, cursor, total, playing, canPlay) {
  els.scrubber.max = String(total);
  els.scrubber.value = String(cursor);
  els.scrubber.disabled = total === 0 || !canPlay;
  els.position.textContent = `${cursor} / ${total}`;
  els.play.disabled = !canPlay || total === 0 || (playing && cursor < total) || cursor >= total;
  els.pause.disabled = !canPlay || !playing;
}
