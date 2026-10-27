// Topology tests for the network map: only real links, routed pulses.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { MAP_EDGES, MAP_POSITIONS, MAP_VIEWBOX, NODE_R, blueLinkFor, pathBetween, stateLabel } from "../dashboard/js/network.js";

const link = ([a, b]) => [a, b].sort().join("|");

describe("map topology", () => {
  it("draws only the real links", () => {
    assert.deepEqual(
      MAP_EDGES.map(link).sort(),
      ["app|web", "app|db", "app|workstation"].sort(),
    );
  });

  it("keeps nodes compact enough to fit the viewport", () => {
    assert.ok(NODE_R <= 28);
  });

  it("flows left to right with the workstation below app", () => {
    assert.ok(MAP_POSITIONS.web.x < MAP_POSITIONS.app.x);
    assert.ok(MAP_POSITIONS.app.x < MAP_POSITIONS.db.x);
    assert.equal(MAP_POSITIONS.web.y, MAP_POSITIONS.app.y);
    assert.equal(MAP_POSITIONS.app.y, MAP_POSITIONS.db.y);
    assert.equal(MAP_POSITIONS.workstation.x, MAP_POSITIONS.app.x);
    assert.ok(MAP_POSITIONS.workstation.y > MAP_POSITIONS.app.y);
  });

  it("fits every node, pill, and label inside the viewBox", () => {
    // Name pill above (up to ~60 units) and state label below (~20).
    for (const [host, pos] of Object.entries(MAP_POSITIONS)) {
      assert.ok(pos.x - NODE_R - 60 > 0, `${host} left edge`);
      assert.ok(pos.x + NODE_R + 60 < MAP_VIEWBOX.w, `${host} right edge`);
      assert.ok(pos.y - NODE_R - 55 > 0, `${host} top edge`);
      assert.ok(pos.y + NODE_R + 25 < MAP_VIEWBOX.h, `${host} bottom edge`);
    }
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
