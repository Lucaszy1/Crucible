// Story sentences: every tool reads as plain English, failures and
// all, with no jargon, no war wording, and no credentials.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { actorName, describeEvent, groupRounds, keyMoments, statusSentence } from "../dashboard/js/story.js";

function ev(actor, tool, args, result) {
  return { id: "evt-0001", actor, tool, args: args || {}, result: result || { ok: true } };
}

const BANNED = ["attack", "battle", "enemy", "kill", "war ", "warfare", " T10", " T11", "T1110"];

function assertPlain(text) {
  const lower = ` ${text.toLowerCase()} `;
  for (const word of BANNED) {
    assert.ok(!lower.includes(word.toLowerCase()), `${word} leaked into: ${text}`);
  }
}

describe("describeEvent red tools", () => {
  const cases = [
    [ev("red", "scan_host", { host: "web" }, { ok: true, running: true }), "Red is checking whether web is up."],
    [ev("red", "scan_host", { host: "db" }, { ok: true, running: false }), "Red checked db, which is down."],
    [ev("red", "scan_host", { host: "web" }, { ok: false, error: "down" }), "Red tried to check web but the check failed (down)."],
    [ev("red", "http_request", { host: "web", path: "/admin" }, { ok: true, status: 200 }), "Red asked web for /admin (got 200)."],
    [ev("red", "http_request", { host: "web", path: "/" }, { ok: false, error: "cut off" }), "Red tried to ask web for / but it failed (cut off)."],
    [ev("red", "try_credential", { host: "db", user: "admin" }, { ok: true, authenticated: true }), "Red signed in to db as admin."],
    [ev("red", "try_credential", { host: "db", user: "admin" }, { ok: true, authenticated: false }), "Red is trying to sign in to db."],
    [ev("red", "try_credential", { host: "web" }, { ok: true, note: "no sign-in gate" }), "Red tried web, which has no sign-in."],
    [ev("red", "read_file", { host: "db", path: "/secret/x" }, { ok: true, path: "/secret/x" }), "Red read the protected file on db."],
    [ev("red", "read_file", { host: "app", path: "/srv/app/c.ini" }, { ok: true, path: "/srv/app/c.ini" }), "Red read /srv/app/c.ini on app."],
    [ev("red", "read_file", { host: "db", path: "/secret/x" }, { ok: false, error: "denied" }), "Red tried to read /secret/x on db but could not (denied)."],
    [ev("red", "list_files", { host: "db", path: "/secret" }, { ok: true }), "Red listed files in /secret on db."],
    [ev("red", "list_files", { host: "db", path: "/secret" }, { ok: false }), "Red tried to list /secret on db but could not."],
    [ev("red", "report_finding", { text: "found it" }, { ok: true }), "Red noted: found it"],
  ];
  for (const [event, want] of cases) {
    it(`${event.tool} reads plainly`, () => {
      const got = describeEvent(event);
      assert.equal(got, want);
      assertPlain(got);
    });
  }
});

describe("describeEvent blue tools", () => {
  const cases = [
    [ev("blue", "read_logs", { host: "db" }, { ok: true }), "Blue checked recent logs on db."],
    [ev("blue", "read_logs", { host: "db" }, { ok: false, error: "dark" }), "Blue tried to check logs on db but could not (dark)."],
    [ev("blue", "list_processes", { host: "app" }, { ok: true }), "Blue checked running programs on app."],
    [ev("blue", "list_processes", { host: "app" }, { ok: false }), "Blue tried to check programs on app but could not."],
    [ev("blue", "wait", {}, { ok: true }), "Blue waited this round."],
    [ev("blue", "block_ip", { ip: "1.2.3.4" }, { ok: true }), "Blue blocked network traffic from 1.2.3.4."],
    [ev("blue", "block_ip", { ip: "9.9.9.9" }, { ok: false }), "Blue tried to block 9.9.9.9 but it failed."],
    [ev("blue", "isolate_host", { host: "app" }, { ok: true }), "Blue isolated app."],
    [ev("blue", "isolate_host", { host: "app" }, { ok: false, error: "busy" }), "Blue tried to isolate app but it failed (busy)."],
    [ev("blue", "patch_service", { host: "web", service: "webapp" }, { ok: true }), "Blue patched webapp on web."],
    [ev("blue", "patch_service", { host: "web", service: "webapp" }, { ok: false }), "Blue tried to patch webapp on web but it failed."],
    [ev("blue", "restore_host", { host: "db" }, { ok: true }), "Blue restored db."],
    [ev("blue", "restore_host", { host: "db" }, { ok: false }), "Blue tried to restore db but it failed."],
    [ev("blue", "report_finding", { text: "odd sign-ins" }, { ok: true }), "Blue reported: odd sign-ins"],
  ];
  for (const [event, want] of cases) {
    it(`${event.tool} reads plainly`, () => {
      const got = describeEvent(event);
      assert.equal(got, want);
      assertPlain(got);
    });
  }
});

describe("describeEvent edge cases", () => {
  it("never prints passwords", () => {
    const got = describeEvent(ev("red", "try_credential",
      { host: "db", user: "admin", password: "s3cret!" },
      { ok: true, authenticated: true }));
    assert.ok(!got.includes("s3cret!"));
  });

  it("covers agent errors", () => {
    assert.equal(
      describeEvent(ev("blue", "agent_error", {}, { ok: false, error: "boom" })),
      "Blue hit an error and skipped a turn.");
  });

  it("covers audit events", () => {
    assert.equal(
      describeEvent(ev("auditor", "audit", {}, {
        ok: true, target_event_id: "evt-0004", verdict: "justified", score: 8,
      })),
      "Auditor judged evt-0004 justified (8/10).");
  });

  it("covers human voice and text sources", () => {
    assert.equal(
      describeEvent(ev("human", "voice_command", { text: "pause the run" }, { ok: true })),
      "Operator note: pause the run");
    assert.equal(
      describeEvent(ev("human", "text_note", { text: "watch db" }, { ok: true })),
      "Operator note: watch db");
    assert.equal(
      describeEvent(ev("human", "voice_command", {}, { ok: true })),
      "Operator added a note.");
  });

  it("covers unknown tools and missing events", () => {
    assert.equal(describeEvent(ev("red", "mystery", {}, { ok: true })), "Red ran mystery.");
    assert.equal(
      describeEvent(ev("blue", "mystery", {}, { ok: false, error: "no" })),
      "Blue tried mystery but it failed (no).");
    assert.equal(describeEvent(null), "Something happened.");
  });

  it("names actors", () => {
    assert.equal(actorName(ev("red", "wait", {}, {})), "Red");
    assert.equal(actorName(ev("blue", "wait", {}, {})), "Blue");
    assert.equal(actorName(ev("auditor", "audit", {}, {})), "Auditor");
    assert.equal(actorName(ev("human", "text_note", {}, {})), "Operator");
    assert.equal(actorName(null), "Someone");
  });
});

describe("statusSentence", () => {
  it("narrates the latest move", () => {
    assert.equal(
      statusSentence({ latest: ev("red", "try_credential", { host: "db" }, { ok: true }), ended: false, mode: "live" }),
      "Red is trying to sign in to db.");
    assert.equal(
      statusSentence({ latest: ev("blue", "isolate_host", { host: "app" }, { ok: true }), ended: false, mode: "live" }),
      "Blue isolated app.");
  });

  it("announces the ending plainly", () => {
    assert.equal(statusSentence({ ended: true, outcome: "blue_held" }), "Exercise over: Blue held.");
    assert.equal(statusSentence({ ended: true, outcome: "red_succeeded" }), "Exercise over: Red read the protected file.");
    assert.equal(statusSentence({ ended: true, outcome: "stalemate" }), "Exercise over: no result.");
  });

  it("covers idle and booting", () => {
    assert.equal(statusSentence({ ended: false, mode: "idle" }), "No exercise loaded.");
    assert.equal(statusSentence({ ended: false, mode: "booting" }), "Starting the range…");
    assert.equal(statusSentence({ ended: false, mode: "live" }), "Waiting for the first move…");
  });
});

describe("groupRounds", () => {
  const items = [1, 2, 3, 4, 5].map((n) => ({
    ev: ev(n % 2 === 1 ? "red" : "blue", "wait", {}, { ok: true }),
    index: 6 - n,
  }));
  // items is newest first: indexes 5,4,3,2,1.

  it("pairs moves oldest-first into numbered rounds", () => {
    const groups = groupRounds(items);
    assert.deepEqual(groups.map((g) => g.round), [3, 2, 1]);
    assert.deepEqual(groups[0].items.map((i) => i.index), [5]);
    assert.deepEqual(groups[1].items.map((i) => i.index), [4, 3]);
    assert.deepEqual(groups[2].items.map((i) => i.index), [2, 1]);
  });

  it("skips auditor events", () => {
    const mixed = [
      { ev: ev("auditor", "audit", {}, { ok: true }), index: 9 },
      ...items,
    ];
    const groups = groupRounds(mixed);
    assert.equal(groups.flatMap((g) => g.items).length, 5);
  });

  it("handles an empty feed", () => {
    assert.deepEqual(groupRounds([]), []);
  });
});

describe("keyMoments", () => {
  const lane = [
    ev("red", "scan_host", { host: "web" }, { ok: true, running: true }),
    ev("red", "try_credential", { host: "web", user: "op" }, { ok: true, authenticated: true }),
    ev("blue", "read_logs", { host: "web" }, { ok: true }),
    ev("blue", "isolate_host", { host: "web" }, { ok: true }),
  ];

  it("lists sign-in, detection, and containment in move order", () => {
    const moments = keyMoments(lane);
    assert.deepEqual(moments.map((m) => m.key), ["signin", "detection", "containment"]);
    assert.deepEqual(moments.map((m) => m.index), [1, 2, 3]);
    assert.equal(moments[0].title, "First sign-in");
    assert.ok(moments[0].caption.includes("signed in"), moments[0].caption);
  });

  it("finds the secret read instead of containment", () => {
    const red = [
      ev("red", "try_credential", { host: "db", user: "op" }, { ok: true, authenticated: true }),
      ev("red", "read_file", { host: "db", path: "/secret/x" }, { ok: true, path: "/secret/x" }),
    ];
    const moments = keyMoments(red);
    assert.deepEqual(moments.map((m) => m.key), ["signin", "secret"]);
    assert.ok(moments[1].caption.includes("protected file"), moments[1].caption);
  });

  it("folds detection into containment on the same move", () => {
    const quick = [
      ev("red", "try_credential", { host: "web", user: "op" }, { ok: true, authenticated: true }),
      ev("blue", "isolate_host", { host: "web" }, { ok: true }),
    ];
    assert.deepEqual(keyMoments(quick).map((m) => m.key), ["signin", "containment"]);
  });

  it("ignores waits, failed sign-ins, and empty lanes", () => {
    assert.deepEqual(keyMoments([]), []);
    const idle = [
      ev("blue", "wait", {}, { ok: true }),
      ev("red", "try_credential", { host: "web", user: "op" }, { ok: true, authenticated: false }),
    ];
    assert.deepEqual(keyMoments(idle), []);
  });

  it("counts a failed containment as detection, not containment", () => {
    const failed = [
      ev("blue", "isolate_host", { host: "web" }, { ok: false, error: "down" }),
    ];
    assert.deepEqual(keyMoments(failed).map((m) => m.key), ["detection"]);
  });
});
