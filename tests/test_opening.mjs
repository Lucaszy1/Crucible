// Opening slate: bare title, countdown start, skip and destroy.
// Synchronous only; the beat timers are cleared before they fire.
import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { playOpening } from "../dashboard/js/opening.js";

const realDocument = globalThis.document;

function installDom() {
  globalThis.document = {
    addEventListener() {},
    removeEventListener() {},
  };
}

function restoreDom() {
  if (realDocument === undefined) delete globalThis.document;
  else globalThis.document = realDocument;
}

function els() {
  return {
    root: { hidden: true },
    title: { textContent: "" },
    count: {
      textContent: "",
      classList: { remove() {}, add() {} },
      offsetWidth: 0,
    },
  };
}

describe("playOpening", () => {
  beforeEach(installDom);
  afterEach(restoreDom);

  it("shows the bare exercise label as the title", () => {
    const slate = els();
    const opening = playOpening(slate, {
      label: "scn-0007-contained",
      reduced: false,
      onDone: () => {},
    });
    try {
      assert.equal(slate.title.textContent, "scn-0007-contained");
      assert.equal(slate.root.hidden, false);
    } finally {
      opening.destroy();
    }
  });

  it("starts the countdown at 3", () => {
    const slate = els();
    const opening = playOpening(slate, {
      label: "run-1",
      reduced: false,
      onDone: () => {},
    });
    try {
      assert.equal(slate.count.textContent, "3");
    } finally {
      opening.destroy();
    }
  });

  it("skip finishes once and hides the slate", () => {
    const slate = els();
    let done = 0;
    const opening = playOpening(slate, {
      label: "run-1",
      reduced: false,
      onDone: () => { done += 1; },
    });
    opening.skip();
    opening.skip();
    assert.equal(done, 1);
    assert.equal(slate.root.hidden, true);
  });
});
