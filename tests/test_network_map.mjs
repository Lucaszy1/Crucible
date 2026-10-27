// Topology tests for the network map: only real links, routed pulses.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { MAP_EDGES, NODE_R, blueLinkFor, pathBetween, stateLabel } from "../dashboard/js/network.js";

const link = ([a, b]) => [a, b].sort().join("|");

describe("map topology", () => {
  it("draws only the real links", () => {
    assert.deepEqual(
      MAP_EDGES.map(link).sort(),
      ["app|web", "app|db", "app|workstation"].sort(),
    );
  });

  it("sizes nodes larger than the old ring", () => {
    assert.ok(NODE_R >= 30);
  });

  it("routes pulses along the chain", () => {
    assert.deepEqual(pathBetween("web", "db"), ["web", "app", "db"]);
    assert.deepEqual(pathBetween("workstation", "db"), ["workstation", "app", "db"]);
    assert.deepEqual(pathBetween("db", "db"), ["db"]);
    assert.equal(pathBetween("web", "unknown"), null);
  });

  it("points blue responses at the target host", () => {
    assert.deepEqual(blueLinkFor("db"), ["app", "db"]);
    assert.deepEqual(blueLinkFor("app"), ["web", "app"]);
    assert.deepEqual(blueLinkFor("unknown"), null);
  });

  it("labels states in plain words", () => {
    assert.equal(stateLabel("clean"), "Clean");
    assert.equal(stateLabel("signed-in"), "Signed in");
    assert.equal(stateLabel("compromised"), "Compromised");
    assert.equal(stateLabel(null), "Clean");
  });
});
