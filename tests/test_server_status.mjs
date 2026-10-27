// Control bar status rule: the label tracks the running flag exactly.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { serverStatus } from "../dashboard/js/controls.js";

describe("serverStatus", () => {
  it("reads Server ready when idle", () => {
    assert.deepEqual(serverStatus({ running: false }), {
      state: "ok",
      label: "Server ready",
      runDisabled: false,
    });
  });

  it("names the running exercise while busy", () => {
    assert.deepEqual(serverStatus({ running: true, agents: "scripted" }), {
      state: "busy",
      label: "Exercise running (scripted)",
      runDisabled: true,
    });
  });

  it("returns to Server ready once the run flag clears", () => {
    const busy = serverStatus({ running: true, agents: "model" });
    assert.equal(busy.label, "Exercise running (model)");
    const idle = serverStatus({ running: false, agents: null });
    assert.equal(idle.label, "Server ready");
    assert.equal(idle.state, "ok");
    assert.equal(idle.runDisabled, false);
  });
});
