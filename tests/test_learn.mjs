// Learning tab data helpers: rolling means and series shaping.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { rollingMean, seriesFor } from "../dashboard/js/learn.js";

describe("rollingMean", () => {
  it("averages the trailing window", () => {
    assert.deepEqual(rollingMean([60, 80, 100], 3), [60, 70, 80]);
  });

  it("slides past the window edge", () => {
    assert.deepEqual(rollingMean([10, 20, 30, 40], 2), [10, 15, 25, 35]);
  });

  it("skips non-numeric entries", () => {
    assert.deepEqual(rollingMean([50, null, 70], 3), [50, 50, 60]);
  });

  it("reports null with no data yet", () => {
    assert.deepEqual(rollingMean([null, null], 3), [null, null]);
    assert.deepEqual(rollingMean([], 3), []);
  });
});

describe("seriesFor", () => {
  it("pairs episode numbers with scores in order", () => {
    const detail = {
      episodes: [
        { episode_no: 2, blue_score: 90 },
        { episode_no: 1, blue_score: 70 },
        { episode_no: 3, blue_score: null },
      ],
    };
    assert.deepEqual(seriesFor(detail), [[1, 70], [2, 90]]);
  });

  it("handles missing campaigns", () => {
    assert.deepEqual(seriesFor(null), []);
    assert.deepEqual(seriesFor({}), []);
  });
});
