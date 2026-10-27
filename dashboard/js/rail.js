// Right-rail layout: thought panels must never cover the HUD
// score block, the auditor card, or the caption. Positions derive
// from measured boxes, so the rule holds at every width instead of
// only the width the CSS was tuned for. Priority order is fixed:
// score and tug bar first, then the caption, then the auditor card.
// The auditor loses only on very short stages while it shows its
// four-second verdict; everywhere else every box clears.
export function railGeometry(input) {
  const wrapH = Math.max(0, input.wrapH || 0);
  const hudBottom = Math.max(0, input.hudBottom || 0);
  const capTop = input.capTop === undefined || input.capTop === null ? wrapH : input.capTop;
  const audBottom = input.audBottom === undefined || input.audBottom === null ? null : input.audBottom;
  const gap = wrapH < 260 ? 4 : 10;
  const floor = 32;
  const defTop = Math.round(wrapH * 0.14);
  const maxTop = Math.max(defTop, capTop - gap - floor);
  const redTop = Math.min(Math.max(defTop, hudBottom + gap), maxTop);
  const wantBlue = audBottom === null ? redTop : Math.max(redTop, audBottom + gap);
  const blueTop = Math.min(wantBlue, maxTop);
  const redMaxH = Math.max(floor, capTop - redTop - gap);
  const blueMaxH = Math.max(floor, capTop - blueTop - gap);
  return { redTop, blueTop, redMaxH, blueMaxH };
}

// Apply the geometry to the live stage. measure(rect) reads one
// element box in viewport pixels; the auditor counts only while
// its show class is on, since its hidden rest pose sits offstage.
export function applyRail(els, measure) {
  if (!els || !els.wrap || !els.mid || !els.cap || !els.red || !els.blue) return null;
  const read = typeof measure === "function"
    ? measure
    : (node) => node.getBoundingClientRect();
  let wrap;
  let mid;
  let cap;
  try {
    wrap = read(els.wrap);
    mid = read(els.mid);
    cap = read(els.cap);
  } catch {
    return null;
  }
  if (!wrap || !mid || !cap) return null;
  let audBottom = null;
  if (els.aud && els.aud.classList && els.aud.classList.contains("show")) {
    try {
      const aud = read(els.aud);
      if (aud) audBottom = aud.bottom - wrap.top;
    } catch {
      audBottom = null;
    }
  }
  const geo = railGeometry({
    wrapH: wrap.height,
    hudBottom: mid.bottom - wrap.top,
    capTop: cap.top - wrap.top,
    audBottom,
  });
  els.red.style.top = `${geo.redTop}px`;
  els.red.style.maxHeight = `${geo.redMaxH}px`;
  els.blue.style.top = `${geo.blueTop}px`;
  els.blue.style.maxHeight = `${geo.blueMaxH}px`;
  return geo;
}
