// Debrief card helper: evidence chips jump the replay cursor to the
// cited event (1-based), or nowhere when the id is not present.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { jumpTarget } from "../dashboard/js/debrief.js";

const events = [
  { id: "evt-0002", actor: "red", tool: "scan_host" },
  { id: "evt-0003", actor: "blue", tool: "read_logs" },
  { id: "evt-0005", actor: "blue", tool: "report_finding" },
];

describe("jumpTarget", () => {
  it("returns the 1-based cursor for a cited event", () => {
    assert.equal(jumpTarget(events, "evt-0002"), 1);
    assert.equal(jumpTarget(events, "evt-0005"), 3);
  });

  it("returns null for unknown or missing ids", () => {
    assert.equal(jumpTarget(events, "evt-9999"), null);
    assert.equal(jumpTarget(events, null), null);
    assert.equal(jumpTarget([], "evt-0002"), null);
    assert.equal(jumpTarget(null, "evt-0002"), null);
  });
});
