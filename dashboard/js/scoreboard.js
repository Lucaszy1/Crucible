// Scoreboard: live figures, cost meter, final outcome banner.
import { scoreEvents } from "./state.js";

const OUTCOME_LABEL = {
  red_succeeded: "Red Succeeded",
  blue_held: "Blue Held",
  stalemate: "Stalemate",
};

function fmtDelay(value) {
  return value === null || value === undefined ? "--" : `${value} events`;
}

function fmtContain(value) {
  return value === null || value === undefined ? "--" : `event ${value}`;
}

export function renderScoreboard(els, events, cursor, weights, spend, finalScore) {
  const live = scoreEvents(events.slice(0, cursor), weights);
  const ended = Boolean(finalScore);
  const shown = ended ? finalScore : live;

  let outcome;
  if (ended) {
    outcome = OUTCOME_LABEL[shown.outcome] || shown.outcome;
  } else if (live.secret_exfiltrated) {
    outcome = "Red Succeeded";
  } else if (live.outcome === "blue_held") {
    outcome = "Blue holding";
  } else {
    outcome = "Under way";
  }
  els.outcome.textContent = outcome;
  els.detect.textContent = fmtDelay(shown.detection_delay);
  els.contain.textContent = fmtContain(shown.time_to_contain);
  els.downtime.textContent = `${Number(shown.downtime_pct || 0).toFixed(2)}%`;
  els.blue.textContent = ended ? String(shown.blue_score ?? "--") : "--";

  if (ended) {
    els.banner.hidden = false;
    els.banner.textContent = OUTCOME_LABEL[shown.outcome] || shown.outcome;
    els.banner.className = `banner ${shown.outcome === "red_succeeded" ? "red" : shown.outcome === "blue_held" ? "blue" : "stalemate"}`;
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
