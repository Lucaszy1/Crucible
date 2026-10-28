// Control bar wiring: run, speed, confirm dialog. Past runs moved to
// the lobby; this module keeps server status and the start flow.

export function setServer(els, state, label) {
  els.light.dataset.state = state;
  els.label.textContent = label;
}

// Pure status rule shared by the control bar and the tests: any idle
// snapshot reads "Server ready", a running one names the exercise.
export function serverStatus(spend) {
  if (spend && spend.running) {
    return { state: "busy", label: `Exercise running (${spend.agents || "?"})`, runDisabled: true };
  }
  return { state: "ok", label: "Server ready", runDisabled: false };
}

export function showConfirm(els, spend, capped, onConfirm, note) {
  els.cost.textContent = `$${Number(spend?.estimated_exercise_cost || 0).toFixed(2)} (estimate)`;
  els.spent.textContent = `$${Number(spend?.spent_today || 0).toFixed(2)}`;
  els.limit.textContent = `$${Number(spend?.daily_limit || 0).toFixed(2)}`;
  els.warn.hidden = !capped;
  if (capped) els.warn.textContent = capped;
  if (els.note) {
    els.note.hidden = !note;
    if (note) els.note.textContent = note;
  }
  els.overlay.hidden = false;
  const close = (ok) => {
    els.overlay.hidden = true;
    els.ok.onclick = null;
    els.cancel.onclick = null;
    if (ok) onConfirm();
  };
  els.ok.onclick = () => close(true);
  els.cancel.onclick = () => close(false);
}

export function initControls(els, deps) {
  els.run.addEventListener("click", () => deps.onRun(els.agents.value));
  els.speedSeg.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      els.speedSeg.querySelectorAll("button").forEach((other) => {
        other.classList.toggle("active", other === btn);
        other.setAttribute("aria-pressed", String(other === btn));
      });
      deps.onSpeed(Number(btn.dataset.speed));
    });
  });
  if (els.paceSeg) {
    els.paceSeg.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        deps.onPace(btn.dataset.pace);
      });
    });
  }
  if (els.motionSeg) {
    els.motionSeg.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        deps.onMotion(btn.dataset.motion);
      });
    });
  }
}
