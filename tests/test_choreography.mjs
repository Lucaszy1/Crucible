// Arena choreography tests: every tool maps to its beat, pressure
// and integrity derive purely from events, verdicts fold to three words.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  choreograph, effectHost, integrityFromEvents, isBlockedAttempt,
  isSecretRead, latestByActor, pressureFromEvents, verdictWord,
} from "../dashboard/js/choreography.js";

function ev(actor, tool, args, result) {
  return { id: "evt-0001", actor, tool, args: args || {}, result: result || { ok: true } };
}

const BANNED = ["attack", "battle", "enemy", "kill", "war ", "warfare", "combat"];
function assertArenaCopy(steps) {
  for (const step of steps) {
    for (const field of [step.mark, step.text]) {
      if (!field) continue;
      const lower = ` ${String(field).toLowerCase()} `;
      for (const word of BANNED) {
        assert.ok(!lower.includes(word), `${word} leaked into: ${field}`);
      }
    }
  }
}

describe("choreograph red recon", () => {
  it("sends a scan ring from red ending on the target", () => {
    const steps = choreograph(ev("red", "scan_host", { host: "web" }, { ok: true, running: true }));
    assert.equal(steps[0].effect, "scan-ring");
    assert.equal(steps[0].from, "red");
    assert.equal(steps[0].target, "web");
    assertArenaCopy(steps);
  });

  it("sends one packet along the links for http requests", () => {
    const steps = choreograph(ev("red", "http_request", { host: "app" }, { ok: true, status: 200 }));
    assert.equal(steps[0].effect, "packet");
    assert.equal(steps[0].from, "red");
    assert.equal(steps[0].target, "app");
  });

  it("fails scans quietly", () => {
    const steps = choreograph(ev("red", "scan_host", { host: "web" }, { ok: false, error: "down" }));
    assert.equal(steps[0].effect, "scan-fail");
  });
});

describe("choreograph sign-in", () => {
  it("bounces keys back with a Denied mark on failure", () => {
    const steps = choreograph(ev("red", "try_credential", { host: "db" }, { ok: true, authenticated: false }));
    assert.deepEqual(steps.map((s) => s.effect), ["key-try", "key-bounce", "denied"]);
    assert.equal(steps[2].mark, "Denied");
  });

  it("punches through with a flash and foothold ring on success", () => {
    const steps = choreograph(ev("red", "try_credential", { host: "db" }, { ok: true, authenticated: true }));
    assert.deepEqual(steps.map((s) => s.effect), ["key-through", "flash-red", "foothold"]);
    assert.equal(steps[2].target, "db");
  });
});

describe("choreograph file moves", () => {
  it("streams a file icon back to red", () => {
    const steps = choreograph(ev("red", "list_files", { host: "db" }, { ok: true, host: "db", path: "/secret" }));
    assert.equal(steps[0].effect, "file-stream");
    assert.equal(steps[0].from, "db");
    assert.equal(steps[0].to, "red");
  });

  it("makes a secret read climactic", () => {
    const steps = choreograph(ev("red", "read_file", { host: "db" },
      { ok: true, host: "db", path: "/secret/exercise_secret.txt" }));
    assert.deepEqual(steps.map((s) => s.effect),
      ["file-stream", "crack", "vignette-red", "banner", "foothold"]);
    assert.equal(steps[3].text, "Secret exposed");
    assertArenaCopy(steps);
  });

  it("shows Denied when sign-in is required first", () => {
    const steps = choreograph(ev("red", "list_files", { host: "db" },
      { ok: false, error: "sign-in on 'db' is required first" }));
    assert.equal(steps[0].effect, "denied");
  });

  it("fails other file errors quietly", () => {
    const steps = choreograph(ev("red", "read_file", { host: "db" }, { ok: false, error: "no such file" }));
    assert.equal(steps[0].effect, "file-fail");
  });
});

describe("choreograph blue moves", () => {
  it("sweeps a beam from blue for log reads", () => {
    const steps = choreograph(ev("blue", "read_logs", { host: "db" }, { ok: true }));
    assert.deepEqual(steps, [{ effect: "log-beam", from: "blue", target: "db" }]);
  });

  it("raises an alert pulse and flag for findings", () => {
    const steps = choreograph(
      { ...ev("blue", "report_finding", { text: "odd" }, { ok: true }), evidence_refs: ["db:1"] },
    );
    assert.deepEqual(steps.map((s) => s.effect), ["alert-pulse", "flag"]);
    assert.equal(steps[0].target, "db");
  });

  it("snaps links shut with a barrier and shield on isolate", () => {
    const steps = choreograph(ev("blue", "isolate_host", { host: "db" }, { ok: true }));
    assert.deepEqual(steps.map((s) => s.effect), ["isolate-snap", "barrier", "shield"]);
  });

  it("spins a wrench and turns the ring blue on patch", () => {
    const steps = choreograph(ev("blue", "patch_service", { host: "web" }, { ok: true }));
    assert.deepEqual(steps.map((s) => s.effect), ["wrench", "ring-blue", "check"]);
  });

  it("reconnects links with a green sweep on restore", () => {
    const steps = choreograph(ev("blue", "restore_host", { host: "db" }, { ok: true }));
    assert.deepEqual(steps.map((s) => s.effect), ["green-sweep", "reconnect"]);
  });

  it("ticks quietly on wait", () => {
    assert.deepEqual(choreograph(ev("blue", "wait", {}, { ok: true })), [{ effect: "idle-tick" }]);
  });

  it("pulses once for block_ip", () => {
    const steps = choreograph(ev("blue", "block_ip", { ip: "1.2.3.4" }, { ok: true, ip: "1.2.3.4" }));
    assert.equal(steps[0].effect, "block-pulse");
    assert.equal(steps[0].ip, "1.2.3.4");
  });
});

describe("choreograph blocked attempts", () => {
  it("detects off-range hosts", () => {
    assert.equal(isBlockedAttempt(ev("red", "scan_host", { host: "nope" }, { ok: false, error: "nope" })), true);
    assert.equal(effectHost(ev("red", "scan_host", { host: "nope" }, { ok: false, error: "x" })), null);
  });

  it("detects sandbox refusals by wording", () => {
    const refused = ev("red", "http_request", { host: "web" }, { ok: false, error: "host 'web' is outside the sandbox" });
    assert.equal(isBlockedAttempt(refused), true);
    const steps = choreograph(refused);
    assert.equal(steps[0].effect, "bounce-blocked");
    assert.equal(steps[0].mark, "Blocked");
    assertArenaCopy(steps);
  });

  it("treats red failures on isolated hosts as blocked", () => {
    const failed = ev("red", "http_request", { host: "db" }, { ok: false, error: "cut off" });
    assert.equal(isBlockedAttempt(failed, { db: "isolated" }), true);
  });

  it("leaves ordinary failures alone", () => {
    assert.equal(isBlockedAttempt(ev("red", "read_file", { host: "db" }, { ok: false, error: "no such file" })), false);
    assert.equal(isBlockedAttempt(ev("red", "try_credential", { host: "db" }, { ok: true, authenticated: false })), false);
  });
});

describe("choreograph edge cases", () => {
  it("maps audits to verdict pops", () => {
    const steps = choreograph(ev("auditor", "audit", {},
      { ok: true, target_event_id: "evt-0004", verdict: "justified" }));
    assert.equal(steps[0].effect, "verdict");
    assert.equal(steps[0].target, "evt-0004");
    assert.equal(steps[0].word, "justified");
  });

  it("covers unknown tools and missing events", () => {
    assert.equal(choreograph(ev("red", "mystery", { host: "web" }, { ok: true }))[0].effect, "packet");
    assert.equal(choreograph(null)[0].effect, "idle-tick");
  });
});

describe("verdictWord", () => {
  it("folds verdicts to three words", () => {
    assert.equal(verdictWord("justified"), "justified");
    assert.equal(verdictWord("unsupported"), "flagged");
    assert.equal(verdictWord("premature"), "caution");
    assert.equal(verdictWord("late"), "caution");
    assert.equal(verdictWord(null), null);
  });
});

describe("pressureFromEvents", () => {
  it("climbs the red chain in order", () => {
    assert.deepEqual(pressureFromEvents([]), { red: 0, blue: 0 });
    const recon = [ev("red", "scan_host", { host: "web" }, { ok: true, running: true })];
    assert.deepEqual(pressureFromEvents(recon), { red: 25, blue: 0 });
    const authed = [...recon, ev("red", "try_credential", { host: "db" }, { ok: true, authenticated: true })];
    assert.deepEqual(pressureFromEvents(authed), { red: 50, blue: 0 });
    const listed = [...authed, ev("red", "list_files", { host: "db" }, { ok: true, path: "/secret" })];
    assert.deepEqual(pressureFromEvents(listed), { red: 75, blue: 0 });
    const read = [...listed, ev("red", "read_file", { host: "db" }, { ok: true, path: "/secret/x" })];
    assert.deepEqual(pressureFromEvents(read), { red: 100, blue: 0 });
  });

  it("adds blue detection and containment", () => {
    const found = [ev("blue", "report_finding", {}, { ok: true })];
    assert.deepEqual(pressureFromEvents(found), { red: 0, blue: 50 });
    const held = [...found, ev("blue", "isolate_host", { host: "db" }, { ok: true })];
    assert.deepEqual(pressureFromEvents(held), { red: 0, blue: 100 });
  });
});

describe("integrityFromEvents", () => {
  it("drops on red ground and refills on restore", () => {
    const clean = { web: 100, app: 100, db: 100, workstation: 100 };
    assert.deepEqual(integrityFromEvents([]), clean);
    const probed = [ev("red", "scan_host", { host: "web" }, { ok: true, running: true })];
    assert.equal(integrityFromEvents(probed).web, 85);
    const signed = [...probed, ev("red", "try_credential", { host: "db" }, { ok: true, authenticated: true })];
    const mid = integrityFromEvents(signed);
    assert.equal(mid.db, 50);
    assert.equal(mid.web, 85);
    const owned = [...signed, ev("red", "read_file", { host: "db" }, { ok: true, path: "/secret/x" })];
    assert.equal(integrityFromEvents(owned).db, 0);
    assert.equal(isSecretRead(owned[2]), true);
    const healed = [...owned, ev("blue", "restore_host", { host: "db" }, { ok: true })];
    assert.equal(integrityFromEvents(healed).db, 100);
  });
});

describe("latestByActor", () => {
  it("finds the newest event per side", () => {
    const list = [
      ev("red", "scan_host", { host: "web" }, { ok: true }),
      ev("blue", "read_logs", { host: "db" }, { ok: true }),
      ev("red", "http_request", { host: "web" }, { ok: true }),
    ];
    assert.equal(latestByActor(list, "red").tool, "http_request");
    assert.equal(latestByActor(list, "blue").tool, "read_logs");
    assert.equal(latestByActor([], "red"), null);
  });
});
