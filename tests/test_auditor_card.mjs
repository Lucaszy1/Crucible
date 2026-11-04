// Auditor verdict card follows the replay cursor, not the clock.
// Pause keeps the card, scrubbing to at or after the auditor event
// shows it, and scrubbing before that event clears it. Covers the
// cinematic stage and the classic arena static renders.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { latestAuditorEvent } from "../dashboard/js/choreography.js";
import { initStage, renderStageStatic } from "../dashboard/js/stage.js";
import { initArena, renderArenaStatic } from "../dashboard/js/arena.js";

function ev(id, actor, tool, args, result) {
  return { id, actor, tool, args: args || {}, result: result || { ok: true } };
}

const RED = ev("e1", "red", "scan_host", { host: "web" }, { ok: true, host: "web" });
const BLUE = ev("e2", "blue", "isolate_host", { host: "web" }, { ok: true, host: "web" });
const AUDIT = ev("e3", "auditor", "audit", { target_event_id: "e2" }, {
  ok: true, target_event_id: "e2", verdict: "justified", score: 6,
  rationale: "Fair move.", evidence_checked: ["web:1"],
});
const AFTER = ev("e4", "red", "http_request", { host: "web", path: "/" },
  { ok: true, host: "web", status: 200, body: "hi" });
const EVENTS = [RED, BLUE, AUDIT, AFTER];

function fakeHud() {
  return {
    aud: { className: "" },
    audNum: { textContent: "" },
    audWord: { textContent: "" },
    audCite: { textContent: "" },
    audNote: { textContent: "" },
  };
}

function fakeCanvas() {
  return {
    clientWidth: 800, clientHeight: 450, width: 0, height: 0,
    getContext: () => null,
    classList: { remove() {} },
  };
}

function stageRefs() {
  const refs = initStage(fakeCanvas(), { reduced: true });
  refs.hud = { ...refs.hud, ...fakeHud() };
  return refs;
}

function arenaRefs() {
  const refs = initArena(fakeCanvas());
  refs.hud = { ...refs.hud, ...fakeHud() };
  return refs;
}

function shown(hud) {
  return String(hud.aud.className || "").includes("show");
}

describe("latestAuditorEvent", () => {
  it("finds nothing before the auditor event", () => {
    assert.equal(latestAuditorEvent(EVENTS, 0), null);
    assert.equal(latestAuditorEvent(EVENTS, 2), null);
  });

  it("finds the auditor event at and after its move", () => {
    assert.equal(latestAuditorEvent(EVENTS, 3), AUDIT);
    assert.equal(latestAuditorEvent(EVENTS, 4), AUDIT);
  });
});

describe("stage auditor card", () => {
  it("stays visible on pause at the auditor move", () => {
    const refs = stageRefs();
    renderStageStatic(refs, EVENTS, 3, { layout: null }, true);
    assert.ok(shown(refs.hud), "pause keeps the card");
    assert.equal(refs.hud.audNum.textContent, "6/10");
  });

  it("shows when scrubbing to after the auditor event", () => {
    const refs = stageRefs();
    renderStageStatic(refs, EVENTS, 1, { layout: null }, true);
    assert.ok(!shown(refs.hud), "no card before the event");
    renderStageStatic(refs, EVENTS, 4, { layout: null }, true);
    assert.ok(shown(refs.hud), "scrub past the event shows it");
    assert.equal(refs.hud.audNum.textContent, "6/10");
  });

  it("clears when scrubbing to before the auditor event", () => {
    const refs = stageRefs();
    renderStageStatic(refs, EVENTS, 4, { layout: null }, true);
    assert.ok(shown(refs.hud), "card visible after the event");
    renderStageStatic(refs, EVENTS, 2, { layout: null }, true);
    assert.ok(!shown(refs.hud), "scrub before the event hides it");
  });
});

describe("arena auditor card", () => {
  it("stays visible on pause at the auditor move", () => {
    const refs = arenaRefs();
    renderArenaStatic(refs, EVENTS, 3, { layout: null }, true);
    assert.ok(shown(refs.hud), "pause keeps the card");
    assert.equal(refs.hud.audNum.textContent, "6/10");
  });

  it("shows when scrubbing to after the auditor event", () => {
    const refs = arenaRefs();
    renderArenaStatic(refs, EVENTS, 1, { layout: null }, true);
    assert.ok(!shown(refs.hud), "no card before the event");
    renderArenaStatic(refs, EVENTS, 4, { layout: null }, true);
    assert.ok(shown(refs.hud), "scrub past the event shows it");
    assert.equal(refs.hud.audNum.textContent, "6/10");
  });

  it("clears when scrubbing to before the auditor event", () => {
    const refs = arenaRefs();
    renderArenaStatic(refs, EVENTS, 4, { layout: null }, true);
    assert.ok(shown(refs.hud), "card visible after the event");
    renderArenaStatic(refs, EVENTS, 2, { layout: null }, true);
    assert.ok(!shown(refs.hud), "scrub before the event hides it");
  });
});
