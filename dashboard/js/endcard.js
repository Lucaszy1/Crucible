// End card: the full stop at the end of an exercise. Big display
// outcome word, figures, one plain explanation line, and Replay,
// Next exercise, Back to lobby. Tone follows the outcome: gray for
// quiet exercises, blue for contained ones, red and gold with the
// glitch stinger for a secret read.

function fmtMoves(value, unit) {
  if (value === null || value === undefined) return "n/a";
  return `${value} ${unit}`;
}

export function initEndCard(els, callbacks) {
  els.replay.addEventListener("click", () => callbacks.onReplay());
  els.next.addEventListener("click", () => callbacks.onNext());
  els.lobby.addEventListener("click", () => callbacks.onLobby());
  if (els.save) els.save.addEventListener("click", () => {
    if (callbacks.onSave) callbacks.onSave();
  });
}

export function showEndCard(els, stats, justifiedPct, nextId) {
  els.root.hidden = false;
  els.root.dataset.tone = stats.tone;
  els.eyebrow.textContent = stats && stats.assisted
    ? "Exercise complete · Human-assisted"
    : "Exercise complete";
  els.title.textContent = stats.label;
  els.score.textContent = `Blue score ${stats.blueScore ?? "--"}`;
  els.stats.innerHTML = "";
  const rows = [
    ["To detect", fmtMoves(stats.detectionDelay, "moves")],
    ["To contain", stats.containMove === null ? "n/a" : `move ${stats.containMove}`],
    ["Downtime", `${Number(stats.downtimePct || 0).toFixed(2)}%`],
  ];
  if (justifiedPct !== null && justifiedPct !== undefined) {
    rows.push(["Auditor justified", `${justifiedPct}%`]);
  }
  for (const [name, value] of rows) {
    const cell = document.createElement("div");
    const num = document.createElement("b");
    num.textContent = value;
    cell.append(num, document.createTextNode(name));
    els.stats.append(cell);
  }
  els.line.textContent = stats.explanation;
  els.next.disabled = !nextId;
  setEndNext(els, "");
}

export function setEndNext(els, text) {
  if (!text) {
    els.nextUp.hidden = true;
    els.nextUp.textContent = "";
    return;
  }
  els.nextUp.hidden = false;
  els.nextUp.textContent = text;
}

export function hideEndCard(els) {
  els.root.hidden = true;
  setEndNext(els, "");
}

export function endCardVisible(els) {
  return Boolean(els.root) && els.root.hidden === false;
}
