// Honest display outcomes: derived from events, never from the server
// outcome field alone. Covers the four display cases plus stalemate,
// featured ranking, the test-run filter, the autoplay queue, end card
// stats, and the timeline strip model.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  acceptLiveTraffic,
  autoplayQueue,
  bootStale,
  bootTimedOut,
  controlBar,
  controlBarTip,
  displayOutcome,
  endCardStats,
  errorRetryTarget,
  finishSide,
  followOpensLive,
  motionReduced,
  motionSetting,
  outcomeLine,
  phaseLabel,
  rankFeatured,
  runFlags,
  streamDead,
} from "../dashboard/js/state.js";
import { stripModel } from "../dashboard/js/timeline.js";

function ev(actor, tool, args, result, extra) {
  return {
    id: `evt-${Math.floor(Math.random() * 100000)}`,
    actor, tool, args: args || {}, result: result || { ok: true }, ...(extra || {}),
  };
}

const AUTH_APP = () => ev("red", "try_credential", { host: "app", user: "a" },
  { ok: true, host: "app", authenticated: true });
const AUTH_DB = () => ev("red", "try_credential", { host: "db", user: "b" },
  { ok: true, host: "db", authenticated: true });
const SECRET = () => ev("red", "read_file", { host: "db", path: "/secret/exercise_secret.txt" },
  { ok: true, host: "db", path: "/secret/exercise_secret.txt" });
const ISOLATE = (host) => ev("blue", "isolate_host", { host: host || "app" },
  { ok: true, host: host || "app" });
const PATCH = () => ev("blue", "patch_service", { host: "web", service: "webapp" },
  { ok: true, host: "web", service: "webapp" });
const REPORT = () => ev("blue", "report_finding", { text: "odd" }, { ok: true, id: "finding-1" });
const FAIL_AUTH = () => ev("red", "try_credential", { host: "app", user: "a" },
  { ok: true, host: "app", authenticated: false });

const BANNED = ["attack", "battle", "enemy", "kill", "war ", "warfare", "combat", "\u2014"];
function assertShowCopy(text) {
  const lower = ` ${String(text).toLowerCase()} `;
  for (const word of BANNED) {
    assert.ok(!lower.includes(word.toLowerCase()), `${JSON.stringify(word)} leaked into: ${text}`);
  }
}

describe("displayOutcome", () => {
  it("calls a secret read Secret exposed even when blue contained", () => {
    const out = displayOutcome([AUTH_APP(), ISOLATE("app"), AUTH_DB(), SECRET()]);
    assert.equal(out.key, "secret-exposed");
    assert.equal(out.label, "Secret exposed");
    assert.equal(out.secret, true);
    assertShowCopy(out.label);
  });

  it("calls a stopped intrusion Contained", () => {
    const out = displayOutcome([AUTH_APP(), REPORT(), ISOLATE("app")]);
    assert.equal(out.key, "contained");
    assert.equal(out.label, "Contained");
    assert.equal(out.gotIn, true);
    assert.equal(out.contained, true);
    assertShowCopy(out.label);
  });

  it("counts a patch as containment too", () => {
    const out = displayOutcome([AUTH_APP(), PATCH()]);
    assert.equal(out.key, "contained");
  });

  it("calls quiet failures No intrusion, never a blue victory", () => {
    const out = displayOutcome([FAIL_AUTH(), REPORT(), FAIL_AUTH()]);
    assert.equal(out.key, "no-intrusion");
    assert.equal(out.label, "No intrusion");
    assert.equal(out.tone, "gray");
  });

  it("calls containment without intrusion Held", () => {
    const out = displayOutcome([FAIL_AUTH(), REPORT(), ISOLATE("app")]);
    assert.equal(out.key, "held");
    assert.equal(out.label, "Held");
  });

  it("keeps stalemate when blue never acted", () => {
    const out = displayOutcome([FAIL_AUTH(), ev("blue", "wait", {}, { ok: true })]);
    assert.equal(out.key, "stalemate");
    assert.equal(out.label, "Stalemate");
  });

  it("names an uncontained intrusion honestly instead of Held", () => {
    const out = displayOutcome([AUTH_APP(), REPORT()]);
    assert.equal(out.key, "uncontained");
    assert.equal(out.gotIn, true);
    assert.equal(out.contained, false);
    assertShowCopy(out.label);
    assertShowCopy(outcomeLine(out.key));
  });

  it("ignores failed containment and skips system and auditor events", () => {
    const failed = ev("blue", "isolate_host", { host: "app" }, { ok: false, error: "busy" });
    const sys = ev("system", "clean_start_check", {}, { ok: true });
    const audit = ev("auditor", "audit", {}, { ok: true, verdict: "justified" });
    const out = displayOutcome([sys, FAIL_AUTH(), failed, REPORT(), audit]);
    assert.equal(out.key, "no-intrusion");
    assert.equal(out.contained, false);
  });

  it("does not count restore or block as containment", () => {
    const restore = ev("blue", "restore_host", { host: "app" }, { ok: true, host: "app" });
    const block = ev("blue", "block_ip", { ip: "1.2.3.4" }, { ok: true, ip: "1.2.3.4" });
    const out = displayOutcome([FAIL_AUTH(), restore, block, REPORT()]);
    assert.equal(out.key, "no-intrusion");
  });

  it("explains every outcome in plain words", () => {
    for (const key of ["contained", "secret-exposed", "held", "no-intrusion", "uncontained", "stalemate"]) {
      const line = outcomeLine(key);
      assert.ok(line && line.length > 8, `missing line for ${key}`);
      assertShowCopy(line);
    }
    assert.match(outcomeLine("no-intrusion"), /never found a way in/);
  });
});

describe("rankFeatured", () => {
  it("orders Contained, Secret exposed, Held, then No intrusion last", () => {
    const runs = [
      { id: "q1", outcome: "no-intrusion" },
      { id: "h1", outcome: "held" },
      { id: "s1", outcome: "secret-exposed" },
      { id: "c1", outcome: "contained" },
      { id: "q2", outcome: "no-intrusion" },
      { id: "c2", outcome: "contained" },
    ];
    assert.deepEqual(rankFeatured(runs).map((r) => r.id), ["c1", "c2", "s1", "h1", "q1", "q2"]);
  });

  it("keeps the input order inside each group", () => {
    const runs = [
      { id: "b", outcome: "held" },
      { id: "a", outcome: "held" },
    ];
    assert.deepEqual(rankFeatured(runs).map((r) => r.id), ["b", "a"]);
  });

  it("does not mutate the input", () => {
    const runs = [{ id: "q", outcome: "no-intrusion" }, { id: "c", outcome: "contained" }];
    rankFeatured(runs);
    assert.deepEqual(runs.map((r) => r.id), ["q", "c"]);
  });

  it("ranks lobby cards by outcomeKey without touching outcome objects", () => {
    const runs = [
      { id: "q", outcomeKey: "no-intrusion", outcome: { label: "No intrusion", tone: "gray" } },
      { id: "c", outcomeKey: "contained", outcome: { label: "Contained", tone: "blue" } },
    ];
    const ranked = rankFeatured(runs);
    assert.deepEqual(ranked.map((r) => r.id), ["c", "q"]);
    assert.equal(ranked[0].outcome.label, "Contained");
  });
});

describe("runFlags", () => {
  it("flags test ids even before events load", () => {
    assert.deepEqual(runFlags("cap-test", null), ["test"]);
    assert.deepEqual(runFlags("aud1", null), ["test"]);
    assert.deepEqual(runFlags("day3b-1", null), []);
  });

  it("flags spend-cap fallback runs from reasoning text", () => {
    const fell = ev("red", "scan_host", { host: "web" }, { ok: true },
      { reasoning_summary: "Daily model spend cap reached, using baseline." });
    assert.deepEqual(runFlags("day3-1", [fell]), ["fallback"]);
  });

  it("flags fully scripted runs by their baseline wording", () => {
    const scripted = [
      ev("red", "scan_host", { host: "web" }, { ok: true, running: true },
        { reasoning_summary: "Check whether the web host is reachable." }),
      ev("blue", "read_logs", { host: "web" }, { ok: true },
        { reasoning_summary: "Inspect recent web host logs for odd requests." }),
    ];
    assert.deepEqual(runFlags("exercise-1", scripted), ["scripted"]);
  });

  it("leaves varied model wording unflagged", () => {
    const model = [
      ev("red", "scan_host", { host: "web" }, { ok: true, running: true },
        { reasoning_summary: "Starting recon to locate the secret, first checking web." }),
      ev("blue", "read_logs", { host: "web" }, { ok: true },
        { reasoning_summary: "Early recon on web, checking likely next targets for probing." }),
    ];
    assert.deepEqual(runFlags("day3b-1", model), []);
  });
});

describe("autoplayQueue", () => {
  it("queues the ids after the current one, wrapping once", () => {
    assert.deepEqual(autoplayQueue(["a", "b", "c"], "a"), ["b", "c"]);
    assert.deepEqual(autoplayQueue(["a", "b", "c"], "c"), ["a", "b"]);
    assert.deepEqual(autoplayQueue(["a"], "a"), []);
  });

  it("plays the whole list when the current id is unknown", () => {
    assert.deepEqual(autoplayQueue(["a", "b"], "zzz"), ["a", "b"]);
    assert.deepEqual(autoplayQueue([], "a"), []);
  });
});

describe("endCardStats", () => {
  it("packs outcome, score, detect, contain, downtime and the plain line", () => {
    const events = [FAIL_AUTH(), REPORT(), ISOLATE("app")];
    const score = {
      outcome: "blue_held", blue_score: 99.38, downtime_pct: 0.62,
      detection_delay: 3, time_to_contain: 3,
    };
    const stats = endCardStats(events, score, null);
    assert.equal(stats.key, "held");
    assert.equal(stats.label, "Held");
    assert.equal(stats.blueScore, 99.38);
    assert.equal(stats.detectionDelay, 3);
    assert.equal(stats.containMove, 3);
    assert.equal(stats.downtimePct, 0.62);
    assert.ok(stats.explanation.length > 8);
    assertShowCopy(stats.label);
    assertShowCopy(stats.explanation);
  });

  it("shows n/a containment when blue never isolated or patched", () => {
    const events = [FAIL_AUTH(), REPORT()];
    const score = {
      outcome: "blue_held", blue_score: 100, downtime_pct: 0,
      detection_delay: 1, time_to_contain: null,
    };
    const stats = endCardStats(events, score, null);
    assert.equal(stats.key, "no-intrusion");
    assert.equal(stats.containMove, null);
  });

  it("falls back to live scoring when no final score exists", () => {
    const events = [AUTH_APP(), SECRET()];
    const stats = endCardStats(events, null, null);
    assert.equal(stats.key, "secret-exposed");
    assert.equal(stats.blueScore, 0);
  });
});

describe("motionReduced", () => {
  it("reads full motion where there is no window", () => {
    assert.equal(motionReduced(), false);
  });

  it("prefers the URL override, then the setting, then the OS", () => {
    const realWindow = globalThis.window;
    const fake = (search, stored, osReduced) => {
      globalThis.window = {
        location: { search },
        localStorage: { getItem: () => stored },
        matchMedia: () => ({ matches: osReduced }),
      };
    };
    try {
      fake("?motion=full", "reduced", true);
      assert.equal(motionReduced(), false, "URL full wins over everything");
      fake("?motion=reduced", "full", false);
      assert.equal(motionReduced(), true, "URL reduced wins over everything");
      fake("", "full", true);
      assert.equal(motionReduced(), false, "stored full beats the OS");
      fake("", "reduced", false);
      assert.equal(motionReduced(), true, "stored reduced beats the OS");
      fake("", "auto", true);
      assert.equal(motionReduced(), true, "auto follows the OS");
      fake("", "auto", false);
      assert.equal(motionReduced(), false, "auto follows the OS");
      fake("", "bogus", true);
      assert.equal(motionReduced(), true, "bad stored value falls back to auto");
      assert.equal(motionSetting(), "auto");
      fake("", "reduced", false);
      assert.equal(motionSetting(), "reduced");
    } finally {
      if (realWindow === undefined) delete globalThis.window;
      else globalThis.window = realWindow;
    }
  });
});

describe("stripModel", () => {
  it("makes one pip per red or blue move, skipping system and auditor", () => {
    const audit = ev("auditor", "audit", { target_event_id: "e2" },
      { ok: true, verdict: "justified", target_event_id: "e2" });
    const sys = ev("system", "clean_start_check", {}, { ok: true });
    const red = ev("red", "scan_host", { host: "web" }, { ok: true });
    red.id = "e1";
    const blue = ev("blue", "read_logs", { host: "web" }, { ok: true });
    blue.id = "e2";
    const model = stripModel([sys, red, blue, audit], 3, new Map([["e2", audit]]));
    assert.equal(model.length, 2);
    assert.equal(model[0].actor, "red");
    assert.equal(model[1].actor, "blue");
    assert.equal(model[1].verdict, "justified");
    assert.equal(model[0].verdict, null);
  });

  it("marks the current move by cursor", () => {
    const red = ev("red", "scan_host", { host: "web" }, { ok: true });
    const blue = ev("blue", "wait", {}, { ok: true });
    const model = stripModel([red, blue], 1, new Map());
    assert.equal(model[0].current, true);
    assert.equal(model[1].current, false);
  });

  it("accepts live traffic only for a shown live exercise", () => {
    assert.equal(acceptLiveTraffic("live", true), true);
    assert.equal(acceptLiveTraffic("booting", true), true);
    assert.equal(acceptLiveTraffic("replay", true), false);
    assert.equal(acceptLiveTraffic("idle", true), false);
    assert.equal(acceptLiveTraffic("live", false), false);
    assert.equal(acceptLiveTraffic("replay", false), false);
  });

  it("drops stale live boots", () => {
    assert.equal(bootStale(2, 2), false);
    assert.equal(bootStale(2, 3), true);
    assert.equal(bootStale(undefined, 1), true);
  });

  it("follow-live opens only when idle with a new running exercise", () => {
    const running = { running: true, exercise_id: "exercise-7" };
    assert.equal(followOpensLive(true, "idle", null, running), true);
    assert.equal(followOpensLive(false, "idle", null, running), false);
    assert.equal(followOpensLive(true, "replay", null, running), false);
    assert.equal(followOpensLive(true, "live", null, running), false);
    assert.equal(followOpensLive(true, "idle", "exercise-7", running), false);
    assert.equal(followOpensLive(true, "idle", null, { running: false }), false);
    assert.equal(followOpensLive(true, "idle", null, { running: true }), false);
    assert.equal(followOpensLive(true, "idle", null, null), false);
  });

  it("labels known boot phases and stays silent otherwise", () => {
    assert.equal(phaseLabel("starting"), "Starting the exercise…");
    assert.equal(phaseLabel("booting"), "Booting the range…");
    assert.equal(phaseLabel("done"), null);
    assert.equal(phaseLabel(null), null);
  });

  it("times boots out after three minutes without an event", () => {
    assert.equal(bootTimedOut(1000, 1000 + 180000), false);
    assert.equal(bootTimedOut(1000, 1000 + 180001), true);
    assert.equal(bootTimedOut(1000, 2000, 500), true);
  });

  it("declares the stream dead after four straight errors", () => {
    assert.equal(streamDead(0), false);
    assert.equal(streamDead(3), false);
    assert.equal(streamDead(4), true);
  });

  it("retries scripted errors in place and sends model errors to the lobby", () => {
    assert.equal(errorRetryTarget("scripted"), "again");
    assert.equal(errorRetryTarget("model"), "lobby");
    assert.equal(errorRetryTarget("human"), "lobby");
    assert.equal(errorRetryTarget(undefined), "lobby");
  });
});

describe("finishSide", () => {
  it("hands decisive endings to a winner and stays quiet otherwise", () => {
    assert.equal(finishSide("secret-exposed"), "red");
    assert.equal(finishSide("contained"), "blue");
    assert.equal(finishSide("held"), null);
    assert.equal(finishSide("no-intrusion"), null);
    assert.equal(finishSide("uncontained"), null);
    assert.equal(finishSide("stalemate"), null);
    assert.equal(finishSide("unknown"), null);
  });
});

describe("controlBar", () => {
  it("sits center on a quiet exercise", () => {
    const bar = controlBar([], []);
    assert.equal(bar.value, 0);
    assert.equal(bar.raw, 0);
    assert.equal(bar.settled, "open");
    assert.equal(bar.key, "stalemate");
  });

  it("pushes red on red progress and blue on containment", () => {
    const full = [AUTH_APP(), ISOLATE("app")];
    const red = controlBar(full, [AUTH_APP()]);
    assert.ok(red.value > 0, `uncontained reads red, got ${red.value}`);
    assert.equal(red.key, "uncontained");
    const blue = controlBar(full, full);
    assert.ok(blue.value < 0, `contained reads blue, got ${blue.value}`);
    assert.equal(blue.key, "contained");
    assert.equal(blue.lit, 0, "isolating the foothold drops the notch");
  });

  it("parks at the red end on a secret read", () => {
    const full = [AUTH_APP(), ISOLATE("app"), AUTH_DB(), SECRET()];
    const bar = controlBar(full, full);
    assert.equal(bar.value, 1);
    assert.equal(bar.settled, "red");
    assert.equal(bar.key, "secret-exposed");
  });

  it("never moves on failed moves", () => {
    const failScan = ev("red", "scan_host", { host: "web" }, { ok: false, host: "web" });
    const failIso = ev("blue", "isolate_host", { host: "app" }, { ok: false, host: "app" });
    const bar = controlBar([failScan, failIso, FAIL_AUTH()], [failScan, failIso, FAIL_AUTH()]);
    assert.equal(bar.value, 0);
    assert.equal(bar.lit, 0);
    assert.equal(bar.containments, 0);
  });

  it("agrees with displayOutcome on every final", () => {
    const cases = [
      { events: [AUTH_APP(), ISOLATE("app")], side: -1 },
      { events: [AUTH_DB(), SECRET()], side: 1 },
      { events: [ISOLATE("web")], side: -1 },
      { events: [REPORT()], side: -1 },
      { events: [AUTH_APP()], side: 1 },
      { events: [], side: 0 },
    ];
    for (const { events, side } of cases) {
      const bar = controlBar(events, events);
      const key = displayOutcome(events).key;
      if (side === 0) assert.equal(bar.value, 0, key);
      else if (side < 0) assert.ok(bar.value < 0, `${key} ends blue, got ${bar.value}`);
      else assert.ok(bar.value > 0, `${key} ends red, got ${bar.value}`);
    }
  });

  it("holds the blue side even when red leads on notches", () => {
    const full = [AUTH_APP(), AUTH_DB(), ISOLATE("web")];
    const bar = controlBar(full, full);
    assert.equal(bar.key, "contained");
    assert.ok(bar.raw > 0, `raw favors red, got ${bar.raw}`);
    assert.ok(bar.value < 0, `settle pins blue, got ${bar.value}`);
    assert.equal(bar.settled, "blue");
  });

  it("names its inputs in the tooltip", () => {
    const full = [AUTH_APP(), AUTH_DB()];
    const tip = controlBarTip(controlBar(full, [AUTH_APP()]));
    assert.ok(tip.includes("1 of "), tip);
    assert.ok(tip.includes("0 containment moves"), tip);
    assert.ok(tip.includes("red side"), tip);
    assertShowCopy(tip);
    const settled = controlBarTip(controlBar([SECRET()], [SECRET()]));
    assert.ok(settled.includes("Settled red"), settled);
    assertShowCopy(settled);
  });
});
