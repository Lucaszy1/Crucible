// Thought truncation: sentence, word, short, and single-token cases.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { THOUGHT_MAX_CHARS, truncateThought } from "../dashboard/js/thoughts.js";

describe("truncateThought", () => {
  it("leaves short text unchanged", () => {
    assert.deepEqual(truncateThought("Isolate the database.", 220),
      { text: "Isolate the database.", cut: false });
    assert.deepEqual(truncateThought("", 220), { text: "", cut: false });
  });

  it("cuts at a sentence boundary with an ellipsis", () => {
    const first = "The repeated sign-in failures on the app host look like password guessing, so the recent logs there come first today.";
    assert.ok(first.length > 115);
    const second = `Second sentence runs long: ${"detail ".repeat(60)}`;
    const got = truncateThought(`${first} ${second}`, 220);
    assert.equal(got.cut, true);
    assert.equal(got.text, `${first}…`);
  });

  it("falls back to a word boundary without splitting words", () => {
    const text = `scanning the range for anything odd ${"and onward ".repeat(40)}`;
    const got = truncateThought(text, 100);
    assert.equal(got.cut, true);
    assert.ok(got.text.endsWith("…"));
    const body = got.text.slice(0, -1);
    assert.ok(body.length <= 100);
    // The cut lands on a space: last word whole, next char a space.
    assert.ok(text[body.length] === " ");
    assert.ok(!/\s$/.test(body));
  });

  it("keeps a lone over-long token whole", () => {
    const token = `x${"y".repeat(300)}`;
    assert.deepEqual(truncateThought(token, 100), { text: token, cut: false });
  });

  it("ignores a sentence end that keeps almost nothing", () => {
    const text = `No. ${"filler words keep coming ".repeat(30)}`;
    const got = truncateThought(text, 100);
    assert.equal(got.cut, true);
    assert.ok(got.text.length > 10, got.text);
    assert.ok(!got.text.startsWith("No.…"));
  });

  it("exposes the margin budget", () => {
    assert.ok(THOUGHT_MAX_CHARS >= 100);
  });
});
