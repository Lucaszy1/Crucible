// Arena bar reducers: pressure slider, red progress notches,
// blue held blocks. Pure functions over the event stream.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { hostBlocks, pressureValue, redNotches, scoreEvents } from "../dashboard/js/state.js";

function ev(id, actor, tool, args, result, extra) {
  return { id, actor, tool, args: args || {}, result: result || { ok: true }, ...(extra || {}) };
}

const ADMIN = ev("e1", "red", "http_request", { host: "web", path: "/admin" },
  { ok: true, host: "web", status: 200, body: "{\"hint\": \"host 'app'\"}" });
const APP_AUTH = ev("e2", "red", "try_credential", { host: "app" },
  { ok: true, host: "app", authenticated: true });
const CONFIG = ev("e3", "red", "read_file", { host: "app", path: "/srv/app/config.ini" },
  { ok: true, host: "app", path: "/srv/app/config.ini" });
const DB_AUTH = ev("e4", "red", "try_credential", { host: "db" },
  { ok: true, host: "db", authenticated: true });
const SECRET = ev("e5", "red", "read_file", { host: "db", path: "/secret/exercise_secret.txt" },
  { ok: true, host: "db", path: "/secret/exercise_secret.txt" });
const REPORT = ev("e6", "blue", "report_finding", { text: "odd login" },
  { ok: true, id: "finding-1" }, { evidence_refs: ["app:3"] });
const ISOLATE_APP = ev("e7", "blue", "isolate_host", { host: "app" },
  { ok: true, host: "app" });
const BLOCKED = ev("e8", "red", "http_request", { host: "app", path: "/" },
  { ok: false, error: "host 'app' is unreachable (isolated from the range)" });

describe("pressureValue", () => {
  it("starts at zero and stays in range", () => {
    assert.equal(pressureValue([]), 0);
    const v = pressureValue([ADMIN, APP_AUTH, CONFIG, DB_AUTH, SECRET, REPORT, ISOLATE_APP, BLOCKED]);
    assert.ok(v >= -1 && v <= 1);
  });

  it("pushes toward red on each milestone, weighted by progress", () => {
    const admin = pressureValue([ADMIN]);
    const app = pressureValue([ADMIN, APP_AUTH]);
    const config = pressureValue([ADMIN, APP_AUTH, CONFIG]);
    const db = pressureValue([ADMIN, APP_AUTH, CONFIG, DB_AUTH]);
    assert.ok(admin > 0 && app > admin && config > app && db > config);
  });

  it("pegs at full red on a secret read", () => {
    assert.equal(pressureValue([ADMIN, SECRET]), 1);
  });

  it("pushes toward blue on cited findings and real containment", () => {
    const v = pressureValue([ADMIN, APP_AUTH, REPORT, ISOLATE_APP]);
    assert.ok(v < pressureValue([ADMIN, APP_AUTH]));
  });

  it("ignores containment on hosts red never touched", () => {
    const touched = pressureValue([ADMIN, APP_AUTH, ISOLATE_APP]);
    const untouched = pressureValue([ADMIN, APP_AUTH,
      ev("x", "blue", "isolate_host", { host: "workstation" }, { ok: true, host: "workstation" })]);
    assert.ok(touched < untouched);
  });

  it("counts blocked attempts for blue", () => {
    assert.ok(pressureValue([ADMIN, BLOCKED]) < pressureValue([ADMIN]));
  });

  it("eases back toward zero over quiet events", () => {
    const quiet = Array.from({ length: 30 }, (_, i) =>
      ev(`w${i}`, "blue", "wait", {}, { ok: true }));
    const hot = pressureValue([ADMIN, APP_AUTH]);
    const cooled = pressureValue([ADMIN, APP_AUTH, ...quiet]);
    assert.ok(Math.abs(cooled) < Math.abs(hot));
    assert.ok(cooled > 0);
  });
});

describe("redNotches", () => {
  const CHAIN = [ADMIN, APP_AUTH, DB_AUTH, SECRET];

  it("sizes the bar from the full episode and starts empty", () => {
    const empty = redNotches(CHAIN, []);
    assert.equal(empty.total, 4);
    assert.equal(empty.level, 0);
    assert.deepEqual(empty.notches, [false, false, false, false]);
    assert.deepEqual(empty.milestones.map((m) => m.kind), ["leak", "signin", "signin", "secret"]);
  });

  it("fills notches along the chain in order", () => {
    assert.deepEqual(redNotches(CHAIN, [ADMIN]).notches, [true, false, false, false]);
    const half = redNotches(CHAIN, [ADMIN, APP_AUTH]);
    assert.equal(half.level, 2);
    assert.deepEqual(half.notches, [true, true, false, false]);
    const full = redNotches(CHAIN, CHAIN);
    assert.equal(full.level, 4);
    assert.deepEqual(full.notches, [true, true, true, true]);
  });

  it("shows level zero for a lone late notch", () => {
    const lone = redNotches(CHAIN, [DB_AUTH]);
    assert.equal(lone.level, 0);
    assert.deepEqual(lone.notches, [false, false, true, false]);
  });

  it("drops back when blue isolates the relied-on host", () => {
    const before = redNotches(CHAIN, [ADMIN, APP_AUTH]);
    assert.equal(before.level, 2);
    const after = redNotches(CHAIN, [ADMIN, APP_AUTH, ISOLATE_APP]);
    assert.equal(after.level, 1);
    assert.deepEqual(after.notches, [true, false, false, false]);
  });

  it("drops back on patch and recovers when red re-earns", () => {
    const patch = ev("p", "blue", "patch_service", { host: "app" }, { ok: true, host: "app" });
    const dropped = redNotches(CHAIN, [ADMIN, APP_AUTH, patch]);
    assert.equal(dropped.level, 1);
    const again = ev("e9", "red", "try_credential", { host: "app" },
      { ok: true, host: "app", authenticated: true });
    assert.equal(redNotches(CHAIN, [ADMIN, APP_AUTH, patch, again]).level, 2);
  });

  it("ignores failed containment", () => {
    const fail = ev("f", "blue", "isolate_host", { host: "app" }, { ok: false, error: "nope" });
    assert.equal(redNotches(CHAIN, [ADMIN, APP_AUTH, fail]).level, 2);
  });
});

describe("hostBlocks", () => {
  it("starts clean everywhere", () => {
    assert.deepEqual(hostBlocks([]), { web: "clean", app: "clean", db: "clean", workstation: "clean" });
  });

  it("marks signed-in and compromised hosts breached", () => {
    assert.equal(hostBlocks([APP_AUTH]).app, "breached");
    assert.equal(hostBlocks([DB_AUTH, SECRET]).db, "breached");
  });

  it("treats probed and restored hosts as clean", () => {
    const scan = ev("s", "red", "scan_host", { host: "web" }, { ok: true, host: "web" });
    assert.equal(hostBlocks([scan]).web, "clean");
    const restore = ev("r", "blue", "restore_host", { host: "app" }, { ok: true, host: "app" });
    assert.equal(hostBlocks([APP_AUTH, restore]).app, "clean");
  });

  it("distinguishes isolated from patched", () => {
    assert.equal(hostBlocks([ISOLATE_APP]).app, "isolated");
    const patch = ev("p", "blue", "patch_service", { host: "db" }, { ok: true, host: "db" });
    assert.equal(hostBlocks([patch]).db, "patched");
    assert.equal(hostBlocks([ISOLATE_APP, patch]).app, "isolated");
  });
});

describe("scoreEvents skips system events", () => {
  const check = ev("e0", "system", "clean_start_check", {}, { ok: true });
  const isolate = ev("e1", "blue", "isolate_host", { host: "db" }, { ok: true, host: "db" });

  it("does not count the check as downtime or a move", () => {
    const withSystem = scoreEvents([check, isolate], null);
    const without = scoreEvents([isolate], null);
    assert.deepEqual(withSystem, without);
  });
});
