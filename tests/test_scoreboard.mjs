// Scoreboard outcome box: In progress until the exercise ends,
// the decided outcome after, nothing with no moves.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { renderScoreboard } from "../dashboard/js/scoreboard.js";

function els() {
  return {
    outcome: { textContent: "", dataset: {} },
    downtime: { textContent: "" },
    blue: { textContent: "" },
  };
}

function ev(actor, tool, args, result) {
  return { id: "e", actor, tool, args: args || {}, result: result || {} };
}

const QUIET = [
  ev("red", "scan_host", { host: "web" }, { ok: true }),
  ev("blue", "read_logs", { host: "web" }, { ok: true }),
  ev("red", "http_request", { host: "web", path: "/" }, { ok: true }),
];

const CONTAINED = [
  ...QUIET,
  ev("red", "try_credential", { host: "app" }, { ok: true, authenticated: true }),
  ev("blue", "isolate_host", { host: "app" }, { ok: true, host: "app" }),
];

describe("scoreboard outcome", () => {
  it("shows In progress while the exercise runs", () => {
    const box = els();
    renderScoreboard(box, QUIET, QUIET.length, {}, null, null);
    assert.equal(box.outcome.textContent, "In progress");
  });

  it("shows the decided outcome at the end", () => {
    const box = els();
    const finalScore = { blue_score: 95, downtime_pct: 5.11 };
    renderScoreboard(box, CONTAINED, CONTAINED.length, {}, null, finalScore);
    assert.equal(box.outcome.textContent, "Contained");
  });

  it("shows nothing with no moves", () => {
    const box = els();
    renderScoreboard(box, [], 0, {}, null, null);
    assert.equal(box.outcome.textContent, "--");
  });
});
