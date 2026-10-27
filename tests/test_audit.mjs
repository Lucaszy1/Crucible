// Auditor reducer tests: verdict mapping, cursor visibility, summary.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { reduceAudits, verdictTone } from "../dashboard/js/audit.js";
import { scoreEvents } from "../dashboard/js/state.js";

function blue(id, tool) {
  return { id, actor: "blue", tool, args: {}, result: { ok: true }, evidence_refs: [] };
}

function audit(id, target, verdict, score, refsValid, cost) {
  return {
    id, actor: "auditor", tool: "audit",
    args: { target_event_id: target },
    result: {
      ok: true, target_event_id: target, verdict, score,
      rationale: "reason", evidence_checked: [], refs_valid: refsValid,
      model_used: "muse-wonder-1.0", estimated_cost_usd: cost,
    },
    evidence_refs: [],
  };
}

const events = [
  blue("evt-0001", "read_logs"),
  audit("evt-0002", "evt-0001", "justified", 8, true, 0.01),
  blue("evt-0003", "isolate_host"),
  audit("evt-0004", "evt-0003", "unsupported", 2, false, 0.02),
];

describe("verdictTone", () => {
  it("maps each verdict to its chip color", () => {
    assert.equal(verdictTone("justified"), "green");
    assert.equal(verdictTone("premature"), "amber");
    assert.equal(verdictTone("disproportionate"), "amber");
    assert.equal(verdictTone("unsupported"), "red");
    assert.equal(verdictTone("late"), "grey");
    assert.equal(verdictTone("redundant"), "cyan");
    assert.equal(verdictTone("moot"), "muted");
  });
});

describe("reduceAudits", () => {
  it("keys audits by target event", () => {
    const reduced = reduceAudits(events, 4);
    assert.equal(reduced.byTarget.get("evt-0001").result.verdict, "justified");
    assert.equal(reduced.byTarget.get("evt-0003").result.verdict, "unsupported");
    assert.equal(reduced.audits.length, 2);
  });

  it("hides audits past the cursor", () => {
    const reduced = reduceAudits(events, 2);
    assert.equal(reduced.audits.length, 1);
    assert.equal(reduced.summary.justifiedPct, 100);
    assert.equal(reduceAudits(events, 0).summary.justifiedPct, null);
  });

  it("summarizes verdicts and cost", () => {
    const { summary } = reduceAudits(events, 4);
    assert.equal(summary.count, 2);
    assert.equal(summary.justifiedPct, 50);
    assert.equal(summary.unsupportedCount, 1);
    assert.equal(summary.invalidRefsCount, 1);
    assert.equal(summary.meanScore, 5);
    assert.deepEqual(summary.verdictCounts, { justified: 1, unsupported: 1 });
    assert.equal(summary.cost, 0.03);
  });

  it("reports nulls when no audits are visible", () => {
    const { summary } = reduceAudits(events, 1);
    assert.equal(summary.count, 0);
    assert.equal(summary.justifiedPct, null);
    assert.equal(summary.meanScore, null);
    assert.equal(summary.cost, 0);
  });
});

describe("scoreEvents with audits", () => {
  it("ignores auditor events for positions and totals", () => {
    const plain = scoreEvents(events.filter((ev) => ev.actor !== "auditor"));
    const mixed = scoreEvents(events);
    assert.deepEqual(mixed, plain);
    assert.equal(mixed.total_events, 2);
  });
});
