// Guided tour: one featured real replay with timed captions that
// point at the red thought card, the blue thought card, the
// auditor verdict, and the end card. Each caption sits above the
// stage while a ring marks the card it names, so the box never
// covers the transport controls below the stage. Pausable,
// skippable, no network beyond opening the replay itself. Works
// on the live page and the static replay site.
export const TOUR_STEPS = [
  { id: "red", target: "#thoughtRed", caption: "Red thought: what red tries next." },
  { id: "blue", target: "#thoughtBlue", caption: "Blue thought: how blue answers." },
  { id: "auditor", target: "#arenaAud", caption: "Auditor verdict: was the blue move fair?" },
  { id: "end", target: "#endCard", caption: "End card: how the exercise ended." },
];

// Caption placement: "top" means above the stage, clear of the
// transport controls and the move strip. Every step uses it.
export const TOUR_PLACE = "top";

// Delay before each caption appears, in ms. Short enough to hold
// attention, long enough to read the card behind it.
export const TOUR_DELAYS = [2500, 6000, 6000, 9000];

export const TOUR_DONE_LIVE = "Tour done. Try a live exercise next.";
export const TOUR_DONE_STATIC = "Tour done. Watch another replay next.";

export function tourCaption(index) {
  const step = TOUR_STEPS[index];
  return step ? step.caption : "";
}

export function tourTarget(index) {
  const step = TOUR_STEPS[index];
  return step ? step.target : "";
}

export function tourPlacement(index) {
  return TOUR_STEPS[index] ? TOUR_PLACE : "";
}

export function nextTourStep(current) {
  const at = Number(current);
  if (!Number.isInteger(at) || at < 0) return 0;
  return at + 1 < TOUR_STEPS.length ? at + 1 : -1;
}

export function tourEndLine(isStatic) {
  return isStatic ? TOUR_DONE_STATIC : TOUR_DONE_LIVE;
}

// Pick the tour replay: the first featured id, which is always a
// real run on both the live page and the static bundle.
export function tourReplayId(featuredIds) {
  const ids = Array.isArray(featuredIds) ? featuredIds.filter(Boolean) : [];
  return ids.length > 0 ? ids[0] : null;
}

// Minimal tour controller over injected DOM and playback hooks.
// deps: { doc, caption, box, endLine, liveBtn, moreBtn, pauseBtn,
// skipBtn, nextBtn, overlay, highlightClass, isStatic, onOpen(id),
// onPause(), onResume(), isEnded(), endVisible() }.
// Timers use deps.setTimeout/clearTimeout when given, so tests can
// drive the tour without waiting.
export function initTour(deps) {
  const api = deps || {};
  const doc = api.doc || (typeof document !== "undefined" ? document : null);
  const later = typeof api.setTimeout === "function" ? api.setTimeout : setTimeout;
  const cancel = typeof api.clearTimeout === "function" ? api.clearTimeout : clearTimeout;
  const ctl = {
    running: false,
    paused: false,
    step: -1,
    timer: null,
    highlighted: null,
  };

  function find(target) {
    if (!doc || typeof doc.querySelector !== "function") return null;
    try {
      return doc.querySelector(target);
    } catch {
      return null;
    }
  }

  function clearHighlight() {
    if (ctl.highlighted && ctl.highlighted.classList) {
      ctl.highlighted.classList.remove(api.highlightClass || "tour-highlight");
    }
    ctl.highlighted = null;
  }

  function showStep(index) {
    clearHighlight();
    ctl.step = index;
    const step = TOUR_STEPS[index];
    if (!step) {
      finish();
      return;
    }
    if (api.caption) api.caption.textContent = `${index + 1} of ${TOUR_STEPS.length}. ${step.caption}`;
    if (api.overlay) api.overlay.hidden = false;
    if (api.overlay && api.overlay.dataset) api.overlay.dataset.place = tourPlacement(index);
    if (api.box) api.box.hidden = false;
    if (api.endWrap) api.endWrap.hidden = true;
    const node = find(step.target);
    // The end card may still be hidden mid-replay: point at the
    // arena canvas instead so the caption still lands somewhere.
    const fallback = step.id === "end" ? find("#arenaWrap") || find("#arenaCanvas") : null;
    const mark = node && !(step.id === "end" && node.hidden) ? node : fallback;
    if (mark && mark.classList) {
      mark.classList.add(api.highlightClass || "tour-highlight");
      ctl.highlighted = mark;
    }
    if (api.pauseBtn) api.pauseBtn.textContent = "Pause";
    ctl.paused = false;
  }

  function finish() {
    clearHighlight();
    ctl.running = false;
    ctl.paused = false;
    ctl.step = TOUR_STEPS.length;
    if (api.box) api.box.hidden = true;
    if (api.endWrap) api.endWrap.hidden = false;
    if (api.overlay) api.overlay.hidden = false;
    if (api.endLine) api.endLine.textContent = tourEndLine(Boolean(api.isStatic));
    if (api.liveBtn) api.liveBtn.hidden = Boolean(api.isStatic);
    if (api.moreBtn) api.moreBtn.hidden = !api.isStatic;
  }

  function stop() {
    if (ctl.timer) {
      cancel(ctl.timer);
      ctl.timer = null;
    }
    clearHighlight();
    ctl.running = false;
    ctl.paused = false;
    ctl.step = -1;
    if (api.overlay) api.overlay.hidden = true;
  }

  function armFinish() {
    if (ctl.timer) cancel(ctl.timer);
    ctl.timer = later(() => {
      ctl.timer = null;
      if (ctl.running && !ctl.paused) finish();
    }, 5000);
  }

  function schedule(index, delay) {
    if (ctl.timer) cancel(ctl.timer);
    ctl.timer = later(() => {
      ctl.timer = null;
      if (!ctl.running || ctl.paused) return;
      // The end step waits for the end card when the replay is
      // still running: recheck once per second instead of pointing
      // at an empty slot. Without an end hook the step still plays
      // against the arena fallback and then ends on its timer, so
      // step 4 never parks the tour.
      if (index === TOUR_STEPS.length - 1 && typeof api.endVisible === "function") {
        let visible = false;
        try {
          visible = Boolean(api.endVisible());
        } catch {
          visible = true;
        }
        if (!visible && typeof api.isEnded !== "function") {
          showStep(index);
          armFinish();
          return;
        }
        if (!visible) {
          schedule(index, 1000);
          return;
        }
      }
      showStep(index);
      const next = nextTourStep(index);
      if (next >= 0) schedule(next, TOUR_DELAYS[next] || 5000);
      else armFinish();
    }, delay);
  }

  ctl.start = (replayId) => {
    stop();
    ctl.running = true;
    if (replayId && typeof api.onOpen === "function") {
      try {
        api.onOpen(replayId);
      } catch {
        // Opening never blocks the captions.
      }
    }
    schedule(0, TOUR_DELAYS[0]);
  };

  ctl.pause = () => {
    if (!ctl.running || ctl.paused) return;
    ctl.paused = true;
    if (ctl.timer) {
      cancel(ctl.timer);
      ctl.timer = null;
    }
    if (typeof api.onPause === "function") {
      try {
        api.onPause();
      } catch {
        // Playback pause is best effort.
      }
    }
    if (api.pauseBtn) api.pauseBtn.textContent = "Resume";
  };

  ctl.resume = () => {
    if (!ctl.running || !ctl.paused) return;
    ctl.paused = false;
    if (typeof api.onResume === "function") {
      try {
        api.onResume();
      } catch {
        // Playback resume is best effort.
      }
    }
    if (api.pauseBtn) api.pauseBtn.textContent = "Pause";
    const next = ctl.step < 0 ? 0 : ctl.step;
    schedule(next, 1200);
  };

  ctl.toggle = () => {
    if (ctl.paused) ctl.resume();
    else ctl.pause();
  };

  ctl.skip = () => {
    stop();
  };

  ctl.next = () => {
    if (!ctl.running) return;
    if (ctl.timer) {
      cancel(ctl.timer);
      ctl.timer = null;
    }
    const next = ctl.step < 0 ? 0 : nextTourStep(ctl.step);
    if (next < 0) finish();
    else {
      showStep(next);
      const after = nextTourStep(next);
      if (after >= 0) schedule(after, TOUR_DELAYS[after] || 5000);
      else armFinish();
    }
  };

  ctl.finish = finish;
  ctl.stop = stop;

  if (api.pauseBtn && typeof api.pauseBtn.addEventListener === "function") {
    api.pauseBtn.addEventListener("click", () => ctl.toggle());
  }
  if (api.skipBtn && typeof api.skipBtn.addEventListener === "function") {
    api.skipBtn.addEventListener("click", () => ctl.skip());
  }
  if (api.nextBtn && typeof api.nextBtn.addEventListener === "function") {
    api.nextBtn.addEventListener("click", () => ctl.next());
  }
  return ctl;
}
