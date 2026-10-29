// UX polish: disabled reasons, boot line, takeover and mic copy,
// start-here storage, tooltips, and the Learning glossary.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  RANGE_BOOTING_REASON, RANGE_BUSY_REASON, STUCK_CLEANUP_COMMAND, STUCK_HINT,
  isStuckRange, runDisabledReason,
} from "../dashboard/js/controls.js";
import { BOOT_EXPECTED_SEC, formatBootLine } from "../dashboard/js/state.js";
import { statusSentence } from "../dashboard/js/story.js";
import {
  MIC_BLOCKED_MESSAGE, TAKEOVER_NOT_LIVE, chipTitle, chipsHtml, takeoverMessage,
} from "../dashboard/js/commandbar.js";
import {
  START_HERE_LINES, rememberStartHereDismissed, startHereVisible,
} from "../dashboard/js/lobby.js";
import {
  AUDITOR_TIP, SCORE_TIPS, SPEED_TIP, applyTooltips, verdictExplained,
} from "../dashboard/js/ux.js";
import { GLOSSARY, renderGlossary } from "../dashboard/js/learn.js";

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
  assert.ok(!/sk-[a-z0-9]{4,}/i.test(shown), `possible key leaked into: ${shown}`);
  assert.ok(!/\/(users|home|tmp)\//i.test(shown), `local path leaked into: ${shown}`);
}

describe("runDisabledReason", () => {
  it("names a busy range in plain words", () => {
    assert.equal(
      runDisabledReason({ running: true, exercise_id: "exercise-1" }),
      "A range is already running. Stop it or wait for it to finish.",
    );
    assert.equal(RANGE_BUSY_REASON, "A range is already running. Stop it or wait for it to finish.");
    assertCopy(RANGE_BUSY_REASON);
  });

  it("names a boot with its expected duration", () => {
    assert.equal(
      runDisabledReason({ running: false }, { booting: true }),
      "Starting the range, about 40 seconds.",
    );
    assert.equal(RANGE_BOOTING_REASON, "Starting the range, about 40 seconds.");
    assertCopy(RANGE_BOOTING_REASON);
  });

  it("prefers booting over busy and stays empty when idle", () => {
    assert.equal(
      runDisabledReason({ running: true, exercise_id: "x" }, { booting: true }),
      RANGE_BOOTING_REASON,
    );
    assert.equal(runDisabledReason({ running: false }), "");
    assert.equal(runDisabledReason(null), "");
  });
});

describe("isStuckRange", () => {
  it("flags a busy range with no active exercise", () => {
    assert.equal(isStuckRange({ running: true, exercise_id: null }), true);
    assert.equal(isStuckRange({ running: true }), true);
    assert.equal(isStuckRange({ running: true, exercise_id: "" }), true);
  });

  it("stays quiet for live runs and idle servers", () => {
    assert.equal(isStuckRange({ running: true, exercise_id: "exercise-1" }), false);
    assert.equal(isStuckRange({ running: false }), false);
    assert.equal(isStuckRange(null), false);
  });

  it("pairs the hint with the exact cleanup command", () => {
    assert.equal(
      STUCK_HINT,
      "If this looks stuck, restart the server and remove leftover range containers",
    );
    assert.equal(
      STUCK_CLEANUP_COMMAND,
      "docker ps -aq --filter name=crucible- | xargs docker rm -f",
    );
    assertCopy(STUCK_HINT);
  });
});

describe("formatBootLine", () => {
  it("counts elapsed seconds against the expected 40", () => {
    assert.equal(BOOT_EXPECTED_SEC, 40);
    assert.equal(formatBootLine(12, 40), "Booting the range: 12 s of about 40 s");
    assert.equal(formatBootLine(0), "Booting the range: 0 s of about 40 s");
    assertCopy(formatBootLine(12, 40));
  });

  it("clamps garbage input instead of printing it", () => {
    assert.equal(formatBootLine(-5), "Booting the range: 0 s of about 40 s");
    assert.equal(formatBootLine(null), "Booting the range: 0 s of about 40 s");
  });
});

describe("statusSentence booting", () => {
  it("shows elapsed time when timing is present", () => {
    assert.equal(
      statusSentence({ mode: "booting", bootStartedAt: 1000000, nowMs: 1012000 }),
      "Booting the range: 12 s of about 40 s",
    );
  });

  it("keeps the old line without timing", () => {
    assert.equal(statusSentence({ ended: false, mode: "booting" }), "Starting the range…");
  });
});

describe("takeoverMessage", () => {
  it("says plainly when no exercise is live", () => {
    assert.equal(
      TAKEOVER_NOT_LIVE,
      "Takeover works while an exercise is live. Start one, then click Take over blue.",
    );
    assertCopy(TAKEOVER_NOT_LIVE);
    const err409 = new Error("no live exercise: start one before sending orders");
    err409.status = 409;
    assert.equal(takeoverMessage(err409), TAKEOVER_NOT_LIVE);
    assert.equal(takeoverMessage(new Error("no live exercise")), TAKEOVER_NOT_LIVE);
  });

  it("never shows a bare error code", () => {
    const err = new Error("takeover failed with 409 conflict");
    err.status = 500;
    const got = takeoverMessage(err);
    assert.ok(!got.includes("409"), got);
    assert.ok(got.includes("Takeover did not work"), got);
    assertCopy(got);
  });

  it("falls back to the live line on empty errors", () => {
    assert.equal(takeoverMessage(null), TAKEOVER_NOT_LIVE);
    assert.equal(takeoverMessage(new Error("409")), TAKEOVER_NOT_LIVE);
  });
});

describe("mic blocked copy", () => {
  it("tells Chrome users how to allow the mic", () => {
    assert.ok(MIC_BLOCKED_MESSAGE.includes("Microphone is blocked"), MIC_BLOCKED_MESSAGE);
    assert.ok(MIC_BLOCKED_MESSAGE.includes("Chrome"), MIC_BLOCKED_MESSAGE);
    assert.ok(MIC_BLOCKED_MESSAGE.includes("Allow"), MIC_BLOCKED_MESSAGE);
    assert.ok(MIC_BLOCKED_MESSAGE.includes("type the order instead"), MIC_BLOCKED_MESSAGE);
    assertCopy(MIC_BLOCKED_MESSAGE);
  });
});

describe("host chip titles", () => {
  it("names the host and the click action", () => {
    assert.equal(
      chipTitle("willow", "web portal"),
      "Host willow (public site). Click to add it to your order.",
    );
    assertCopy(chipTitle("willow", "web portal"));
  });

  it("embeds an escaped title in every chip", () => {
    const html = chipsHtml([{ id: "a<b", role: "app" }]);
    assert.ok(html.includes("title=\"Host a&lt;b"), html);
    assert.ok(html.includes("Click to add it to your order"), html);
  });
});

describe("start here strip", () => {
  it("covers red, blue, and the auditor in three lines", () => {
    assert.equal(START_HERE_LINES.length, 3);
    assert.ok(START_HERE_LINES[0].startsWith("Red"), START_HERE_LINES[0]);
    assert.ok(START_HERE_LINES[1].startsWith("Blue"), START_HERE_LINES[1]);
    assert.ok(START_HERE_LINES[2].includes("auditor"), START_HERE_LINES[2]);
    assert.ok(START_HERE_LINES[2].includes("Watch a replay"), START_HERE_LINES[2]);
    for (const line of START_HERE_LINES) assertCopy(line);
  });

  it("shows until dismissed and survives broken storage", () => {
    const store = new Map();
    const storage = {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, value),
    };
    assert.equal(startHereVisible(storage), true);
    rememberStartHereDismissed(storage);
    assert.equal(startHereVisible(storage), false);
    assert.equal(startHereVisible(null), true);
    assert.equal(startHereVisible({ getItem() { throw new Error("denied"); } }), true);
    rememberStartHereDismissed({ setItem() { throw new Error("denied"); } });
  });
});

describe("tooltips", () => {
  it("explains the score labels in plain words", () => {
    assert.ok(SCORE_TIPS.outcome.includes("Contained"), SCORE_TIPS.outcome);
    assert.ok(SCORE_TIPS.blueScore.includes("0 to 100"), SCORE_TIPS.blueScore);
    assert.ok(SCORE_TIPS.downtime.includes("Lower is better"), SCORE_TIPS.downtime);
    for (const tip of Object.values(SCORE_TIPS)) assertCopy(tip);
  });

  it("explains the speed strip and the auditor box", () => {
    assert.ok(SPEED_TIP.includes("Replay speed"), SPEED_TIP);
    assert.ok(AUDITOR_TIP.includes("0 to 10"), AUDITOR_TIP);
    assertCopy(SPEED_TIP);
    assertCopy(AUDITOR_TIP);
  });

  it("explains each verdict word plainly", () => {
    const got = verdictExplained("justified", 9);
    assert.equal(got, "Auditor verdict: justified 9/10. The auditor checks if the blue move was fair.");
    assertCopy(got);
    assertCopy(verdictExplained("unsupported", null));
  });

  it("applies titles to the live DOM nodes", () => {
    const made = [];
    const nodes = new Map();
    const doc = {
      querySelectorAll(selector) {
        if (selector.includes("#sbOutcome")) {
          return [stubNode("arenaOutcome"), stubNode("sbOutcome")];
        }
        return [];
      },
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, stubNode(id));
        return nodes.get(id);
      },
    };
    function stubNode(id) {
      const node = {
        id,
        dataset: {},
        attrs: {},
        setAttribute(key, value) { this.attrs[key] = value; },
        querySelectorAll() { return []; },
      };
      made.push(node);
      return node;
    }
    applyTooltips(doc);
    const strip = nodes.get("speedStrip");
    assert.equal(strip.attrs.title, SPEED_TIP);
    const aud = nodes.get("arenaAud");
    assert.equal(aud.attrs.title, AUDITOR_TIP);
  });
});

describe("glossary", () => {
  it("covers all seven terms in plain words", () => {
    const terms = GLOSSARY.map((entry) => entry.term);
    assert.deepEqual(terms, [
      "intrusion",
      "containment",
      "auditor",
      "sensors",
      "held-out",
      "assisted (human takeover)",
      "frontier tier",
    ]);
    for (const entry of GLOSSARY) {
      assert.ok(entry.def.length > 10, entry.term);
      assertCopy(entry.term);
      assertCopy(entry.def);
    }
  });

  it("renders one term and definition per entry", () => {
    const kids = [];
    const root = { innerHTML: "old", append(...nodes) { kids.push(...nodes); } };
    const doc = {
      createElement(tag) {
        return { tagName: tag, className: "", textContent: "" };
      },
    };
    renderGlossary(doc, root, GLOSSARY);
    assert.equal(root.innerHTML, "");
    assert.equal(kids.length, GLOSSARY.length * 2);
    assert.equal(kids[0].tagName, "dt");
    assert.equal(kids[1].tagName, "dd");
    assert.equal(kids[0].textContent, "intrusion");
  });
});
