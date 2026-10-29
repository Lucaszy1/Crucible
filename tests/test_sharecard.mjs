// Shareable result card: canvas layout helper geometry and card
// text content. The model builder scrubs combat words, em dashes,
// secrets, and local paths; the layout stays inside the 1200x675
// frame; the painter draws only clean strings.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  buildCardModel,
  CARD_H,
  CARD_W,
  cardFilename,
  drawResultCard,
  layoutCard,
  looksLocalPath,
  looksSecret,
  safeProvenance,
  sanitizeCardText,
} from "../dashboard/js/sharecard.js";

// Independent copy check, mirroring the dashboard show-copy rule:
// exercise, containment, and intrusion words only, no em dashes.
const BANNED = ["attack", "battle", "enemy", "kill", "war ", "warfare", "combat", "—"];
function assertCardCopy(text) {
  const lower = ` ${String(text).toLowerCase()} `;
  for (const word of BANNED) {
    assert.ok(!lower.includes(word.toLowerCase()), `${JSON.stringify(word)} leaked into: ${text}`);
  }
}

function sampleInput() {
  return {
    outcomeLabel: "Contained",
    tone: "blue",
    blueScore: 98.5,
    downtimePct: 1.23,
    eventCount: 42,
    difficulty: "hard",
    hosts: [
      { id: "web", state: "probed", jewel: false },
      { id: "app", state: "isolated", jewel: false },
      { id: "db", state: "clean", jewel: true },
      { id: "workstation", state: "clean", jewel: false },
    ],
    edges: [["web", "app"], ["app", "db"], ["workstation", "app"]],
    provenance: { bench: "bench-stage3-heldout", episode: "gen-9018-hard-solver-1", seed: 9018 },
    assisted: false,
    runId: "sensorsoff-hard-exposed",
    explanation: "Red got in, then blue isolated a host and the secret stayed safe.",
  };
}

function stubCanvas() {
  const texts = [];
  const methods = ["fillRect", "strokeRect", "beginPath", "moveTo", "lineTo",
    "arcTo", "arc", "closePath", "fill", "stroke"];
  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    font: "",
    textAlign: "left",
    textBaseline: "alphabetic",
    fillText(text) {
      texts.push(String(text));
    },
    measureText(text) {
      return { width: String(text).length * 10 };
    },
  };
  for (const name of methods) ctx[name] = () => {};
  return { canvas: { width: 0, height: 0, getContext: () => ctx }, texts };
}

describe("card frame", () => {
  it("is a 1200x675 PNG frame", () => {
    assert.equal(CARD_W, 1200);
    assert.equal(CARD_H, 675);
  });
});

describe("buildCardModel", () => {
  it("keeps the honest outcome, figures, and replay provenance", () => {
    const model = buildCardModel(sampleInput());
    assert.equal(model.outcome, "Contained");
    assert.equal(model.tone, "blue");
    assert.deepEqual(model.figures, [
      { label: "Blue score", value: "98.5" },
      { label: "Downtime", value: "1.23%" },
      { label: "Events", value: "42" },
      { label: "Difficulty", value: "Hard" },
    ]);
    assert.deepEqual(model.provenance, {
      bench: "bench-stage3-heldout",
      episode: "gen-9018-hard-solver-1",
      seed: "9018",
    });
    assert.equal(model.nodes.length, 4);
    assert.deepEqual(model.edges, [["web", "app"], ["app", "db"], ["workstation", "app"]]);
    assert.equal(model.runLabel, "sensorsoff-hard-exposed");
  });

  it("keeps the Secret exposed outcome and red tone", () => {
    const model = buildCardModel({ ...sampleInput(), outcomeLabel: "Secret exposed", tone: "red" });
    assert.equal(model.outcome, "Secret exposed");
    assert.equal(model.tone, "red");
    assertCardCopy(model.outcome);
  });

  it("falls back cleanly when numbers are missing", () => {
    const model = buildCardModel({ outcomeLabel: "Contained", tone: "blue" });
    assert.deepEqual(
      model.figures.map((fig) => fig.value),
      ["--", "n/a", "0", "n/a"],
    );
    assert.equal(model.provenance, null);
    assert.equal(model.nodes.length, 0);
  });

  it("scrubs combat words from every field", () => {
    const model = buildCardModel({
      ...sampleInput(),
      outcomeLabel: "Battle won",
      difficulty: "combat",
      hosts: [{ id: "attack-1", state: "clean", jewel: false }],
      explanation: "The enemy fell.",
    });
    assert.equal(model.outcome, "Exercise result");
    assert.equal(model.figures[3].value, "n/a");
    assert.equal(model.nodes.length, 0);
    assert.equal(model.caption, "");
  });

  it("replaces em dashes instead of dropping the text", () => {
    const model = buildCardModel({ ...sampleInput(), explanation: "Red probed — then blue held." });
    assert.ok(!model.caption.includes("—"));
    assert.ok(model.caption.includes("Red probed"));
    assertCardCopy(model.caption);
  });

  it("drops secrets and local paths", () => {
    assert.equal(sanitizeCardText("sk-abcdefgh12345678", 40), "");
    assert.equal(sanitizeCardText("my api key: abc", 40), "");
    assert.equal(sanitizeCardText("x".repeat(40), 48), "");
    assert.equal(sanitizeCardText("/Users/someone/runs/x", 48), "");
    assert.equal(sanitizeCardText("~/Desktop/key.pem", 48), "");
    assert.ok(looksSecret("sk-abcdefgh12345678"));
    assert.ok(looksLocalPath("/tmp/bench"));
    assert.ok(!looksSecret("gen-9018-hard-solver-1"));
    const model = buildCardModel({
      ...sampleInput(),
      provenance: { bench: "/tmp/bench", episode: "gen-1", seed: 1 },
      runId: "/Users/someone/x",
    });
    assert.equal(model.provenance, null);
    assert.equal(model.runLabel, "");
  });

  it("keeps dashed bench and episode ids (not secrets)", () => {
    const prov = safeProvenance({
      bench: "bench-stage3-heldout-sensors",
      episode: "gen-9001-easy-solver-sensors-on-memory-on-1",
      seed: 9001,
    });
    assert.deepEqual(prov, {
      bench: "bench-stage3-heldout-sensors",
      episode: "gen-9001-easy-solver-sensors-on-memory-on-1",
      seed: "9001",
    });
  });

  it("drops edges to unknown hosts and dedupes repeats", () => {
    const model = buildCardModel({
      ...sampleInput(),
      edges: [["web", "app"], ["app", "web"], ["web", "ghost"], ["app", "app"], "nope"],
    });
    assert.deepEqual(model.edges, [["web", "app"]]);
  });
});

describe("layoutCard", () => {
  it("lays four hosts in one row inside the frame", () => {
    const plan = layoutCard(buildCardModel(sampleInput()));
    assert.equal(plan.w, 1200);
    assert.equal(plan.h, 675);
    assert.equal(plan.graph.nodes.length, 4);
    const rows = new Set(plan.graph.nodes.map((node) => node.y));
    assert.equal(rows.size, 1);
    for (const node of plan.graph.nodes) {
      assert.ok(node.x - node.r >= 0 && node.x + node.r <= 1200, `node ${node.id} x in frame`);
      assert.ok(node.y - node.r >= 0 && node.y + node.r + 40 <= 675, `node ${node.id} y in frame`);
    }
    assert.equal(plan.graph.links.length, 3);
    for (const link of plan.graph.links) {
      for (const [x, y] of [[link.x1, link.y1], [link.x2, link.y2]]) {
        assert.ok(x >= 0 && x <= 1200 && y >= 0 && y <= 675, "link endpoint in frame");
      }
    }
    assert.equal(plan.figures.length, 4);
  });

  it("spreads eight hosts over two rows inside the frame", () => {
    const hosts = ["a", "b", "c", "d", "e", "f", "g", "h"].map((id) => ({ id, state: "clean" }));
    const plan = layoutCard(buildCardModel({ ...sampleInput(), hosts, edges: [] }));
    assert.equal(plan.graph.nodes.length, 8);
    const rows = new Set(plan.graph.nodes.map((node) => node.y));
    assert.equal(rows.size, 2);
    for (const node of plan.graph.nodes) {
      assert.ok(node.x - node.r >= 0 && node.x + node.r <= 1200, `node ${node.id} x in frame`);
      assert.ok(node.y - node.r >= 0 && node.y + node.r + 40 <= 675, `node ${node.id} y in frame`);
    }
  });

  it("handles an empty graph", () => {
    const plan = layoutCard(buildCardModel({ outcomeLabel: "Held", tone: "blue" }));
    assert.equal(plan.graph.nodes.length, 0);
    assert.equal(plan.graph.links.length, 0);
  });
});

describe("cardFilename", () => {
  it("slugs run ids into a safe PNG name", () => {
    assert.equal(cardFilename("sensorsoff-hard-exposed"), "crucible-result-sensorsoff-hard-exposed.png");
    assert.equal(cardFilename("Day 3B / run 1"), "crucible-result-day-3b-run-1.png");
    assert.equal(cardFilename(""), "crucible-result-card.png");
    assert.equal(cardFilename(null), "crucible-result-card.png");
  });
});

describe("drawResultCard", () => {
  it("paints a full replay card with clean copy", () => {
    const { canvas, texts } = stubCanvas();
    drawResultCard(canvas, buildCardModel(sampleInput()));
    assert.equal(canvas.width, 1200);
    assert.equal(canvas.height, 675);
    const joined = texts.join("\n");
    assert.ok(joined.includes("Crucible"), "wordmark drawn");
    assert.ok(joined.includes("Contained"), "outcome drawn");
    assert.ok(joined.includes("98.5"), "blue score drawn");
    assert.ok(joined.includes("1.23%"), "downtime drawn");
    assert.ok(joined.includes("42"), "event count drawn");
    assert.ok(joined.includes("Hard"), "difficulty drawn");
    assert.ok(joined.includes("Real run"), "badge drawn");
    assert.ok(joined.includes("bench-stage3-heldout"), "bench drawn");
    assert.ok(joined.includes("gen-9018-hard-solver-1"), "episode drawn");
    assert.ok(joined.includes("9018"), "seed drawn");
    assert.ok(joined.includes("holds the protected file"), "jewel legend drawn");
    for (const text of texts) assertCardCopy(text);
  });

  it("paints an assisted live card with no provenance line", () => {
    const { canvas, texts } = stubCanvas();
    drawResultCard(canvas, buildCardModel({
      ...sampleInput(),
      provenance: null,
      assisted: true,
    }));
    assert.equal(canvas.width, 1200);
    const joined = texts.join("\n");
    assert.ok(joined.includes("Human-assisted exercise"), "assisted note drawn");
    assert.ok(!joined.includes("Real run"), "no badge without provenance");
    for (const text of texts) assertCardCopy(text);
  });

  it("paints an empty model without throwing", () => {
    const { canvas, texts } = stubCanvas();
    drawResultCard(canvas, {});
    assert.equal(canvas.width, 1200);
    assert.equal(canvas.height, 675);
    assert.ok(texts.join("\n").includes("No host data"));
    for (const text of texts) assertCardCopy(text);
  });
});
