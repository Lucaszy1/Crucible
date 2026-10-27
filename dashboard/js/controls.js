// Control bar wiring: run, replay select, speed, confirm dialog.

export function fillRuns(select, runs) {
  select.innerHTML = "";
  if (!runs || runs.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No past runs yet";
    select.append(opt);
    return;
  }
  for (const run of runs) {
    const opt = document.createElement("option");
    opt.value = run.id;
    const score = run.blue_score === null || run.blue_score === undefined ? "--" : run.blue_score;
    opt.textContent = `${run.id} (${run.events} events, ${run.outcome || "?"}, score ${score})`;
    select.append(opt);
  }
}

export function setServer(els, state, label) {
  els.light.dataset.state = state;
  els.label.textContent = label;
}

export function showConfirm(els, spend, capped, onConfirm) {
  els.cost.textContent = `$${Number(spend?.estimated_exercise_cost || 0).toFixed(2)} (estimate)`;
  els.spent.textContent = `$${Number(spend?.spent_today || 0).toFixed(2)}`;
  els.limit.textContent = `$${Number(spend?.daily_limit || 0).toFixed(2)}`;
  els.warn.hidden = !capped;
  if (capped) els.warn.textContent = capped;
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
  els.replayBtn.addEventListener("click", () => {
    if (els.replay.value) deps.onReplay(els.replay.value);
  });
  els.speedSeg.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      els.speedSeg.querySelectorAll("button").forEach((other) => {
        other.classList.toggle("active", other === btn);
        other.setAttribute("aria-pressed", String(other === btn));
      });
      deps.onSpeed(Number(btn.dataset.speed));
    });
  });
}
