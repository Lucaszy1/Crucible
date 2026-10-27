// Scoreboard: three big numbers on the Story view, everything else
// (banner, detection, containment, cost meter) in the Details tab.
// Outcome words always come from displayOutcome: a quiet exercise
// reads No intrusion, never a blue victory.
import { displayOutcome, scoreEvents } from "./state.js";

function fmtDelay(value) {
  return value === null || value === undefined ? "--" : `${value} events`;
}

function fmtContain(value) {
  return value === null || value === undefined ? "--" : `event ${value}`;
}

function figures(events, cursor, weights, finalScore) {
  const slice = events.slice(0, cursor);
  const live = scoreEvents(slice, weights);
  const ended = Boolean(finalScore);
  const shown = ended ? finalScore : live;
  if (slice.length === 0) return { live, ended, shown, outcome: "--", tone: "" };
  const display = displayOutcome(slice);
  return { live, ended, shown, outcome: display.label, tone: display.tone };
}

export function renderScoreboard(els, events, cursor, weights, spend, finalScore) {
  const { shown, outcome, ended, tone } = figures(events, cursor, weights, finalScore);
  void spend;
  els.outcome.textContent = outcome;
  els.outcome.dataset.tone = tone || "";
  els.downtime.textContent = `${Number(shown.downtime_pct || 0).toFixed(2)}%`;
  els.blue.textContent = ended ? String(shown.blue_score ?? "--") : "--";
}

export function renderScoreDetails(els, events, cursor, weights, spend, finalScore) {
  const { shown, ended } = figures(events, cursor, weights, finalScore);
  els.detect.textContent = fmtDelay(shown.detection_delay);
  els.contain.textContent = fmtContain(shown.time_to_contain);

  if (ended) {
    const display = displayOutcome(events);
    els.banner.hidden = false;
    els.banner.textContent = display.label;
    els.banner.className = `banner ${display.tone === "red" ? "red" : display.tone === "blue" ? "blue" : display.tone === "amber" ? "amber" : "stalemate"}`;
  } else {
    els.banner.hidden = true;
  }

  const spent = Number(spend?.spent_today || 0);
  const limit = Number(spend?.daily_limit || 0);
  els.costLabel.textContent = `$${spent.toFixed(2)}${limit ? ` / $${limit.toFixed(2)}` : ""}`;
  const pct = limit > 0 ? Math.min(100, (spent / limit) * 100) : 0;
  els.costBar.style.width = `${pct}%`;
  els.costBar.className = `meter-fill${pct >= 100 ? " over" : pct >= 75 ? " warn" : ""}`;
  const liveCost = spend?.live_exercise_cost;
  const lastCost = spend?.last_exercise_cost;
  els.exerciseCost.textContent =
    liveCost !== undefined && liveCost !== null && spend?.running
      ? `Exercise cost: $${Number(liveCost).toFixed(4)} (live)`
      : lastCost
        ? `Last exercise cost: $${Number(lastCost).toFixed(4)}`
        : "Exercise cost: --";
}
