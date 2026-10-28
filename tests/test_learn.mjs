// Learning tab: exact McNemar p-values, rule action chips, plain
// Stage 3 sentences, and the Learning render with empty states.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  formatP, formatPct, mcnemarExact, renderLearning, ruleActions, stage3Line,
} from "../dashboard/js/learn.js";

describe("mcnemarExact", () => {
  it("returns 1 when the arms agreed everywhere", () => {
    assert.equal(mcnemarExact(0, 0), 1);
  });

  it("matches the exact binomial tail", () => {
    assert.equal(mcnemarExact(10, 0), 2 / 1024);
    assert.equal(mcnemarExact(0, 11), 2 / 2048);
  });

  it("is symmetric and caps at 1", () => {
    assert.equal(mcnemarExact(3, 7), mcnemarExact(7, 3));
    assert.equal(mcnemarExact(1, 1), 1);
  });

  it("ignores garbage counts", () => {
    assert.equal(mcnemarExact(-2, null), 1);
  });
});

describe("formatP and formatPct", () => {
  it("formats p-values plainly", () => {
    assert.equal(formatP(1), "p = 1.000");
    assert.equal(formatP(0.00195), "p = 0.002");
    assert.equal(formatP(0.0009), "p < 0.001");
    assert.equal(formatP(NaN), "n/a");
  });

  it("formats percents with an empty guard", () => {
    assert.equal(formatPct(14, 20), "70%");
    assert.equal(formatPct(0, 0), "n/a");
  });
});

describe("ruleActions", () => {
  it("derives chips from the rule's own verbs", () => {
    assert.deepEqual(ruleActions("Isolate it immediately, then report the evidence."), ["Isolate", "Report"]);
    assert.deepEqual(ruleActions("Hold containment and wait."), ["Hold"]);
    assert.deepEqual(ruleActions("Monitor its logs and patch the service."), ["Patch", "Watch"]);
  });

  it("emits no chips for a verbless rule", () => {
    assert.deepEqual(ruleActions(""), []);
    assert.deepEqual(ruleActions(null), []);
  });
});

describe("stage3Line", () => {
  it("says plainly when every pair agreed", () => {
    const section = {
      on: { n: 17, contained: 17, secretRead: 0 },
      off: { n: 17, contained: 17, secretRead: 0 },
      pairs: { n: 17, contained: { b: 0, c: 0 }, secret: { b: 0, c: 0 } },
    };
    const line = stage3Line(section, "sensors on");
    assert.ok(line.includes("no measurable difference"), line);
    assert.ok(line.includes("17 paired runs"), line);
  });

  it("reports counts and both p-values when pairs split", () => {
    const section = {
      on: { n: 20, contained: 14, secretRead: 7 },
      off: { n: 20, contained: 4, secretRead: 18 },
      pairs: { n: 20, contained: { b: 10, c: 0 }, secret: { b: 0, c: 11 } },
    };
    const line = stage3Line(section, "sensors off");
    assert.ok(line.includes("14 of 20"), line);
    assert.ok(line.includes("4 of 20"), line);
    assert.ok(line.includes("p = 0.002"), line);
    assert.ok(line.includes("p < 0.001"), line);
  });

  it("admits unpaired sections", () => {
    assert.ok(stage3Line({ pairs: { n: 0 } }, "sensors on").includes("No paired"));
  });
});

function stubEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    children: [],
    style: {},
    dataset: {},
    className: "",
    textContent: "",
    hidden: false,
    append(...kids) {
      for (const kid of kids) {
        if (kid === null || kid === undefined) continue;
        this.children.push(kid);
      }
    },
    setAttribute() {},
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return ""; },
    set() { this.children = []; },
  });
  return el;
}

function stubDoc() {
  return {
    createElement: (tag) => stubEl(tag),
    createTextNode: (text) => ({ textContent: text }),
  };
}

function learnEls(doc) {
  return {
    doc,
    note: stubEl("p"),
    on: stubEl("div"),
    off: stubEl("div"),
    empty: stubEl("p"),
    source: stubEl("p"),
    rules: stubEl("ol"),
    playEmpty: stubEl("p"),
  };
}

function texts(root) {
  const out = [];
  const walk = (el) => {
    if (el.textContent) out.push(el.textContent);
    for (const kid of el.children || []) walk(kid);
  };
  walk(root);
  return out.join(" ");
}

describe("renderLearning", () => {
  const data = {
    voidNote: "The sensors-on half is void.",
    sensorsOn: {
      source: "runs/bench-stage3-heldout-sensors/summary.json",
      rerun: true,
      on: { n: 17, contained: 17, secretRead: 0 },
      off: { n: 16, contained: 16, secretRead: 0 },
      pairs: { n: 16, contained: { b: 0, c: 0 }, secret: { b: 0, c: 0 } },
    },
    sensorsOff: {
      source: "runs/bench-stage3-heldout/summary.json",
      rerun: false,
      on: { n: 20, contained: 14, secretRead: 7 },
      off: { n: 20, contained: 4, secretRead: 18 },
      pairs: { n: 20, contained: { b: 10, c: 0 }, secret: { b: 0, c: 11 } },
    },
    playbook: {
      source: "runs/bench-stage3-heldout/playbooks/solver-sensors-on-memory-on.json",
      frozen: true,
      rules: [
        { rule: "Isolate it immediately.", episode: "gen-1001-easy-1" },
        { rule: "Report early, then wait.", episode: "gen-1002-easy-1" },
      ],
    },
  };

  it("renders the void note, both sections, and the playbook", () => {
    const els = learnEls(stubDoc());
    renderLearning(els, data);
    assert.equal(els.empty.hidden, true);
    assert.equal(els.note.hidden, false);
    assert.ok(els.note.textContent.includes("void"));
    assert.ok(texts(els.on).includes("17 of 17 contained"), texts(els.on));
    assert.ok(texts(els.on).includes("no measurable difference"), texts(els.on));
    assert.ok(texts(els.off).includes("14 of 20 contained"), texts(els.off));
    assert.ok(texts(els.off).includes("p = 0.002"), texts(els.off));
    assert.equal(els.rules.children.length, 2);
    assert.equal(els.playEmpty.hidden, true);
    assert.ok(texts(els.rules).includes("Isolate"), texts(els.rules));
    assert.ok(els.source.textContent.includes("Frozen playbook"));
  });

  it("shows empty states when data is missing", () => {
    const els = learnEls(stubDoc());
    renderLearning(els, null);
    assert.equal(els.empty.hidden, false);
    assert.equal(els.note.hidden, true);
    assert.ok(texts(els.on).includes("no runs on file"), texts(els.on));
    assert.ok(texts(els.off).includes("no runs on file"), texts(els.off));
    assert.equal(els.rules.children.length, 0);
    assert.equal(els.playEmpty.hidden, false);
  });
});
