// Guided tour: step copy, targets, timings, and the pausable
// controller with injected timers. No network.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  TOUR_DELAYS, TOUR_STEPS, initTour, nextTourStep,
  tourCaption, tourEndLine, tourReplayId, tourTarget,
} from "../dashboard/js/tour.js";

const BANNED = ["attack", "battle", "enemy", "kill", "war ", "warfare", "combat", " "];
const EM_DASH = "—";

function assertCopy(text) {
  const shown = String(text);
  assert.ok(!shown.includes(EM_DASH), `em dash leaked into: ${shown}`);
  const lower = ` ${shown.toLowerCase()} `;
  for (const word of BANNED) {
    if (!word) continue;
    assert.ok(!lower.includes(word.toLowerCase()), `${JSON.stringify(word)} leaked into: ${shown}`);
  }
}

describe("tour steps", () => {
  it("points at red, blue, auditor, and the end card", () => {
    assert.deepEqual(
      TOUR_STEPS.map((step) => step.target),
      ["#thoughtRed", "#thoughtBlue", "#arenaAud", "#endCard"],
    );
    assert.deepEqual(
      TOUR_STEPS.map((step) => step.id),
      ["red", "blue", "auditor", "end"],
    );
    for (const step of TOUR_STEPS) assertCopy(step.caption);
  });

  it("reads captions and targets by index", () => {
    assert.equal(tourCaption(0), "Red thought: what red tries next.");
    assert.equal(tourCaption(3), "End card: how the exercise ended.");
    assert.equal(tourCaption(9), "");
    assert.equal(tourTarget(1), "#thoughtBlue");
    assert.equal(tourTarget(9), "");
  });

  it("steps forward and ends after the last card", () => {
    assert.equal(nextTourStep(0), 1);
    assert.equal(nextTourStep(2), 3);
    assert.equal(nextTourStep(3), -1);
    assert.equal(nextTourStep(-1), 0);
  });

  it("uses short local delays with no network", () => {
    assert.equal(TOUR_DELAYS.length, TOUR_STEPS.length);
    for (const delay of TOUR_DELAYS) {
      assert.ok(delay >= 1000 && delay <= 15000, `delay=${delay}`);
    }
  });
});

describe("tour ending", () => {
  it("offers a live exercise on the live page", () => {
    assert.equal(tourEndLine(false), "Tour done. Try a live exercise next.");
    assertCopy(tourEndLine(false));
  });

  it("offers more replays on the static site", () => {
    assert.equal(tourEndLine(true), "Tour done. Watch another replay next.");
    assertCopy(tourEndLine(true));
  });

  it("picks the first featured real replay", () => {
    assert.equal(tourReplayId(["a", "b"]), "a");
    assert.equal(tourReplayId([]), null);
    assert.equal(tourReplayId(null), null);
  });
});

describe("tour controller", () => {
  function harness() {
    const timers = new Map();
    let nextId = 1;
    const api = {
      calls: [],
      timers,
      setTimeout(fn, ms) {
        const id = nextId++;
        timers.set(id, { fn, ms });
        return id;
      },
      clearTimeout(id) {
        timers.delete(id);
      },
      fire(id) {
        const job = timers.get(id);
        if (job) {
          timers.delete(id);
          job.fn();
        }
      },
      fireFirst() {
        const first = [...timers.keys()][0];
        if (first !== undefined) this.fire(first);
      },
    };
    const els = {};
    for (const key of ["caption", "box", "endWrap", "endLine", "overlay"]) {
      els[key] = { textContent: "", hidden: true };
    }
    els.liveBtn = { hidden: true };
    els.moreBtn = { hidden: true };
    els.pauseBtn = { textContent: "Pause", addEventListener() {} };
    els.skipBtn = { addEventListener() {} };
    els.nextBtn = { addEventListener() {} };
    const doc = {
      querySelector(target) {
        return { target, classList: { add() {}, remove() {} }, hidden: false };
      },
    };
    return { api, els, doc };
  }

  it("opens the replay and shows the first caption on time", () => {
    const { api, els, doc } = harness();
    let opened = null;
    const ctl = initTour({
      doc,
      caption: els.caption,
      box: els.box,
      endWrap: els.endWrap,
      endLine: els.endLine,
      liveBtn: els.liveBtn,
      moreBtn: els.moreBtn,
      pauseBtn: els.pauseBtn,
      overlay: els.overlay,
      isStatic: false,
      onOpen: (id) => { opened = id; },
      endVisible: () => true,
      setTimeout: api.setTimeout.bind(api),
      clearTimeout: api.clearTimeout.bind(api),
    });
    ctl.start("replay-1");
    assert.equal(opened, "replay-1");
    assert.equal(api.timers.size, 1);
    api.fireFirst();
    assert.ok(els.caption.textContent.includes("Red thought"), els.caption.textContent);
    assert.equal(els.overlay.hidden, false);
  });

  it("pauses and resumes without losing its place", () => {
    const { api, els, doc } = harness();
    let paused = 0;
    let resumed = 0;
    const ctl = initTour({
      doc,
      caption: els.caption,
      box: els.box,
      endWrap: els.endWrap,
      overlay: els.overlay,
      pauseBtn: els.pauseBtn,
      onPause: () => { paused += 1; },
      onResume: () => { resumed += 1; },
      endVisible: () => true,
      setTimeout: api.setTimeout.bind(api),
      clearTimeout: api.clearTimeout.bind(api),
    });
    ctl.start("replay-1");
    api.fireFirst();
    ctl.pause();
    assert.equal(paused, 1);
    assert.equal(els.pauseBtn.textContent, "Resume");
    assert.equal(api.timers.size, 0);
    ctl.resume();
    assert.equal(resumed, 1);
    assert.equal(els.pauseBtn.textContent, "Pause");
    assert.equal(api.timers.size, 1);
  });

  it("skips cleanly and hides the overlay", () => {
    const { api, els, doc } = harness();
    const ctl = initTour({
      doc,
      caption: els.caption,
      box: els.box,
      endWrap: els.endWrap,
      overlay: els.overlay,
      endVisible: () => true,
      setTimeout: api.setTimeout.bind(api),
      clearTimeout: api.clearTimeout.bind(api),
    });
    ctl.start("replay-1");
    api.fireFirst();
    ctl.skip();
    assert.equal(els.overlay.hidden, true);
    assert.equal(api.timers.size, 0);
  });

  it("ends with the static replay offer on the static site", () => {
    const { api, els, doc } = harness();
    const ctl = initTour({
      doc,
      caption: els.caption,
      box: els.box,
      endWrap: els.endWrap,
      endLine: els.endLine,
      liveBtn: els.liveBtn,
      moreBtn: els.moreBtn,
      overlay: els.overlay,
      isStatic: true,
      endVisible: () => true,
      setTimeout: api.setTimeout.bind(api),
      clearTimeout: api.clearTimeout.bind(api),
    });
    ctl.start("replay-1");
    ctl.finish();
    assert.equal(els.endLine.textContent, "Tour done. Watch another replay next.");
    assert.equal(els.liveBtn.hidden, true);
    assert.equal(els.moreBtn.hidden, false);
  });
});
