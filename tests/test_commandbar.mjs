// Defender command bar: preview lines, demo orders, WAV encoding.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  demoOrder,
  downsampleLinear,
  encodeWavPCM,
  previewLine,
  spendLine,
} from "../dashboard/js/commandbar.js";

const BANNED = ["attack", "battle", "enemy", "kill", "war ", "warfare", "combat", " "];
function assertShowCopy(text) {
  const lower = ` ${String(text).toLowerCase()} `;
  for (const word of BANNED) {
    assert.ok(!lower.includes(word.toLowerCase()), `${JSON.stringify(word)} leaked into: ${text}`);
  }
}

describe("previewLine", () => {
  const cases = [
    [{ status: "ok", tool: "isolate_host", args: { host: "willow" } }, "Isolate willow."],
    [{ status: "ok", tool: "patch_service", args: { host: "web", service: "webapp" } }, "Patch web."],
    [{ status: "ok", tool: "restore_host", args: { host: "db" } }, "Restore db."],
    [{ status: "ok", tool: "read_logs", args: { host: "wren", n: 100 } }, "Read logs on wren (100 lines)."],
    [{ status: "ok", tool: "report_finding", args: { text: "odd sign-ins" } }, "Report: odd sign-ins"],
    [{ status: "ok", tool: "scan_host", args: { host: "wren" } }, "Scan wren."],
    [{ status: "ok", tool: "wait", args: {} }, "Hold this turn."],
    [{ status: "clarify", question: "Which host?" }, "Which host?"],
    [{ status: "refused", reason: "Outside the allowlist." }, "Outside the allowlist."],
    [{ status: "control", control: "take-over" }, "Take the defender seat from the agent."],
    [{ status: "control", control: "hand-back" }, "Hand the defender seat back to the agent."],
  ];
  for (const [parsed, want] of cases) {
    it(`reads ${parsed.status} plainly`, () => {
      const got = previewLine(parsed);
      assert.equal(got, want);
      assertShowCopy(got);
    });
  }
});

describe("demoOrder", () => {
  it("isolates a non-jewel host", () => {
    assert.equal(demoOrder([{ id: "willow" }, { id: "wren" }], "willow"), "isolate the wren host");
  });

  it("accepts plain host names", () => {
    assert.equal(demoOrder(["web", "db"], "db"), "isolate the web host");
  });

  it("falls back to a log read on one host", () => {
    assert.equal(demoOrder(["db"], "db"), "read logs on db");
  });

  it("never returns an empty order", () => {
    assert.ok(demoOrder([], null).length > 0);
  });
});

describe("spendLine", () => {
  it("shows spend against the cap", () => {
    assert.equal(spendLine(0.0123, 1), "Model spend $0.0123 of $1.00");
  });
});

describe("wav encode", () => {
  it("writes a readable 16-bit mono header", () => {
    const bytes = encodeWavPCM([0, 0.5, -0.5, 1, -1], 16000);
    const ascii = (at, len) => String.fromCharCode(...bytes.slice(at, at + len));
    assert.equal(ascii(0, 4), "RIFF");
    assert.equal(ascii(8, 4), "WAVE");
    assert.equal(ascii(12, 4), "fmt ");
    assert.equal(ascii(36, 4), "data");
    const view = new DataView(bytes.buffer);
    assert.equal(view.getUint16(20, true), 1);
    assert.equal(view.getUint16(22, true), 1);
    assert.equal(view.getUint32(24, true), 16000);
    assert.equal(view.getUint32(40, true), 10);
    assert.equal(bytes.length, 44 + 10);
    assert.equal(view.getInt16(44, true), 0);
    assert.equal(view.getInt16(46, true), 16384);
  });

  it("downsamples toward the mic rate", () => {
    const input = [0, 1, 2, 3, 4, 5, 6, 7];
    const out = downsampleLinear(input, 8, 4);
    assert.equal(out.length, 4);
    assert.deepEqual(out, [0, 2, 4, 6]);
  });
});
