// Right-rail geometry: thought panels clear the score block,
// the auditor card, and the caption from measured boxes.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { applyRail, railGeometry } from "../dashboard/js/rail.js";

// Approximate stage chrome from a viewport width: the wrap keeps
// 16:9, the HUD mid block and caption scale mildly with size.
function chrome(wrapW) {
  const wrapH = Math.round((wrapW * 9) / 16);
  const hudBottom = Math.round(Math.min(130, Math.max(64, wrapH * 0.2)));
  const capH = Math.round(Math.min(150, Math.max(64, wrapH * 0.19)));
  return { wrapH, hudBottom, capTop: wrapH - capH };
}

describe("railGeometry", () => {
  it("sits red below the HUD and short of the caption", () => {
    const geo = railGeometry({ wrapH: 506, hudBottom: 110, capTop: 420, audBottom: null });
    assert.ok(geo.redTop >= 110 + 10);
    assert.ok(geo.redTop + geo.redMaxH + 10 <= 420);
    assert.equal(geo.blueTop, geo.redTop);
  });

  it("drops blue below the auditor card when it shows", () => {
    const geo = railGeometry({ wrapH: 506, hudBottom: 110, capTop: 420, audBottom: 300 });
    assert.ok(geo.blueTop >= 300 + 10);
    assert.ok(geo.blueTop + geo.blueMaxH + 10 <= 420);
    assert.equal(geo.redTop, 120);
  });

  it("never covers score, tug, or caption from 400px to 2560px", () => {
    for (let w = 400; w <= 2560; w += 40) {
      const { wrapH, hudBottom, capTop } = chrome(Math.min(w - 48, 1560));
      for (const aud of [null, hudBottom + 200]) {
        const geo = railGeometry({ wrapH, hudBottom, capTop, audBottom: aud });
        assert.ok(geo.redTop >= hudBottom, `red clears HUD at ${w}`);
        assert.ok(geo.blueTop >= hudBottom, `blue clears HUD at ${w}`);
        assert.ok(geo.redTop + geo.redMaxH <= capTop, `red clears caption at ${w}`);
        assert.ok(geo.blueTop + geo.blueMaxH <= capTop, `blue clears caption at ${w}`);
        if (aud !== null && aud + 10 + 32 <= capTop - 10) {
          assert.ok(geo.blueTop >= aud + 10, `blue clears auditor at ${w}`);
        }
      }
    }
  });

  it("keeps a usable minimum height on short stages", () => {
    const geo = railGeometry({ wrapH: 198, hudBottom: 76, capTop: 134, audBottom: null });
    assert.ok(geo.redMaxH >= 32);
    assert.ok(geo.blueMaxH >= 32);
    assert.ok(geo.redTop + geo.redMaxH <= 134);
  });

  it("prefers the caption over the auditor when both cannot clear", () => {
    const geo = railGeometry({ wrapH: 198, hudBottom: 76, capTop: 134, audBottom: 190 });
    assert.ok(geo.blueTop + geo.blueMaxH <= 134);
  });
});

describe("applyRail", () => {
  function fakeBox(top, height, extra) {
    return { top, bottom: top + height, height, left: 0, right: 100, ...(extra || {}) };
  }

  function fakeEl() {
    const classes = new Set();
    return {
      style: {},
      classList: {
        add: (c) => classes.add(c),
        remove: (c) => classes.delete(c),
        contains: (c) => classes.has(c),
      },
    };
  }

  it("positions panels from measured boxes and returns the geometry", () => {
    const els = { wrap: fakeEl(), mid: fakeEl(), aud: fakeEl(), cap: fakeEl(), red: fakeEl(), blue: fakeEl() };
    els.aud.classList.add("show");
    const boxes = new Map([
      [els.wrap, fakeBox(100, 506)],
      [els.mid, fakeBox(100, 110)],
      [els.aud, fakeBox(170, 190)],
      [els.cap, fakeBox(520, 86)],
    ]);
    const geo = applyRail(els, (node) => boxes.get(node));
    assert.equal(els.blue.style.top, "270px");
    assert.ok(Number.parseInt(els.blue.style.maxHeight, 10) > 0);
    assert.deepEqual(geo, railGeometry({ wrapH: 506, hudBottom: 110, capTop: 420, audBottom: 260 }));
  });

  it("ignores the auditor box while its show class is off", () => {
    const els = { wrap: fakeEl(), mid: fakeEl(), aud: fakeEl(), cap: fakeEl(), red: fakeEl(), blue: fakeEl() };
    const boxes = new Map([
      [els.wrap, fakeBox(0, 506)],
      [els.mid, fakeBox(0, 110)],
      [els.cap, fakeBox(420, 86)],
    ]);
    const geo = applyRail(els, (node) => boxes.get(node));
    assert.equal(geo.blueTop, geo.redTop);
  });

  it("no-ops when elements are missing", () => {
    assert.equal(applyRail(null), null);
    assert.equal(applyRail({}), null);
  });
});
