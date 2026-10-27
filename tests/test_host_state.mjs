// Host-state reducer tests over fixture events from a real exercise log.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { eventHost, reduceHostStates, scoreEvents } from "../dashboard/js/state.js";

const here = dirname(fileURLToPath(import.meta.url));
const events = readFileSync(join(here, "fixtures", "cap_test_events.jsonl"), "utf8")
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line));

describe("reduceHostStates", () => {
  it("starts clean everywhere", () => {
    assert.deepEqual(reduceHostStates([]), { web: "clean", app: "clean", db: "clean", workstation: "clean" });
  });

  it("marks probed hosts from recon", () => {
    const states = reduceHostStates(events.slice(0, 9));
    assert.equal(states.web, "probed");
    assert.equal(states.db, "probed");
    assert.equal(states.workstation, "probed");
    assert.equal(states.app, "clean");
  });

  it("marks a signed-in host after authentication", () => {
    const states = reduceHostStates(events.slice(0, 11));
    assert.equal(states.db, "signed-in");
  });

  it("marks an isolated host after containment", () => {
    const states = reduceHostStates(events.slice(0, 16));
    assert.equal(states.db, "isolated");
  });

  it("marks a compromised host after the secret read", () => {
    const states = reduceHostStates(events.slice(0, 17));
    assert.equal(states.db, "compromised");
  });

  it("marks a restored host after recovery", () => {
    const states = reduceHostStates(events.slice(0, 20));
    assert.equal(states.db, "restored");
  });

  it("tracks the full cycle back to isolated", () => {
    const states = reduceHostStates(events);
    assert.deepEqual(states, { web: "probed", app: "clean", db: "isolated", workstation: "probed" });
  });

  it("reads the host from args or result", () => {
    assert.equal(eventHost(events[0]), "web");
    assert.equal(eventHost({ args: {}, result: { host: "db" } }), "db");
    assert.equal(eventHost({ args: {}, result: {} }), null);
  });
});

describe("scoreEvents", () => {
  it("matches the exercise outcome on the real log", () => {
    const score = scoreEvents(events);
    assert.equal(score.outcome, "red_succeeded");
    assert.equal(score.secret_exfiltrated, true);
    assert.equal(score.time_to_detect, 14);
    assert.equal(score.time_to_contain, 16);
    assert.deepEqual(score.compromised_hosts, ["db"]);
    assert.equal(score.blue_score, 0);
  });
});
