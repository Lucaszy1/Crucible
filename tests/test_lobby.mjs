// Lobby rules: scenario draws, story lines, durations, and the
// featured/archive partition. Rendering stays in the DOM harness.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  HELDOUT_MAX, HELDOUT_MIN, drawLiveScenario, formatDuration, hostNoun,
  partitionLobby, storyLine,
} from "../dashboard/js/lobby.js";

function ev(id, actor, tool, args, result) {
  return { id, actor, tool, args: args || {}, result: result || { ok: true } };
}

const AUTH = (host) => ev("a", "red", "try_credential", { host },
  { ok: true, host, authenticated: true });
const ISOLATE = (host) => ev("c", "blue", "isolate_host", { host }, { ok: true, host });
const PATCH = (host) => ev("c", "blue", "patch_service", { host }, { ok: true, host });
const SECRET = ev("s", "red", "read_file", { host: "db" },
  { ok: true, host: "db", path: "/secret/exercise_secret.txt" });
const SCAN = ev("q", "red", "scan_host", { host: "web" }, { ok: true });

describe("drawLiveScenario", () => {
  it("draws held-out seeds with medium/hard weight", () => {
    const counts = { easy: 0, medium: 0, hard: 0 };
    let s = 42;
    const rand = () => {
      s = (s * 1103515245 + 12345) % 2147483648;
      return s / 2147483648;
    };
    for (let i = 0; i < 2000; i += 1) {
      const draw = drawLiveScenario(rand);
      assert.ok(draw.seed >= HELDOUT_MIN && draw.seed <= HELDOUT_MAX);
      assert.equal(draw.token, `gen:${draw.seed}:${draw.difficulty}`);
      counts[draw.difficulty] += 1;
    }
    assert.ok(counts.easy > 250 && counts.easy < 550, `easy=${counts.easy}`);
    assert.ok(counts.medium > 650 && counts.medium < 950, `medium=${counts.medium}`);
    assert.ok(counts.hard > 650 && counts.hard < 950, `hard=${counts.hard}`);
  });

  it("maps roll boundaries to difficulties", () => {
    const seq = [0.0, 0.19, 0.0, 0.2, 0.0, 0.59, 0.0, 0.6, 0.0, 0.99];
    let at = 0;
    const rand = () => seq[at++ % seq.length];
    assert.equal(drawLiveScenario(rand).difficulty, "easy");
    assert.equal(drawLiveScenario(rand).difficulty, "medium");
    assert.equal(drawLiveScenario(rand).difficulty, "medium");
    assert.equal(drawLiveScenario(rand).difficulty, "hard");
    assert.equal(drawLiveScenario(rand).difficulty, "hard");
  });
});

describe("hostNoun", () => {
  it("names roles without hostnames", () => {
    assert.equal(hostNoun("analyst workstation"), "workstation");
    assert.equal(hostNoun("internal data store"), "database");
    assert.equal(hostNoun("backup"), "database");
    assert.equal(hostNoun("web portal"), "public site");
    assert.equal(hostNoun("internal reporting service"), "app server");
    assert.equal(hostNoun("file share"), "file share");
    assert.equal(hostNoun("jump host"), "jump host");
    assert.equal(hostNoun(""), "host");
    assert.equal(hostNoun("mystery box"), "host");
  });
});

describe("storyLine", () => {
  const ROLES = { app: "internal reporting service", db: "internal data store" };

  it("narrates containment after a sign-in", () => {
    const line = storyLine([SCAN, AUTH("app"), SCAN, SCAN, ISOLATE("app")], ROLES, "contained");
    assert.equal(line, "Blue isolated the app server 3 events after the first sign-in.");
  });

  it("uses patch wording and singular events", () => {
    const line = storyLine([AUTH("db"), PATCH("db")], ROLES, "contained");
    assert.equal(line, "Blue patched the database 1 event after the first sign-in.");
  });

  it("covers containment before any sign-in", () => {
    const line = storyLine([ISOLATE("app"), SCAN, AUTH("app")], ROLES, "contained");
    assert.equal(line, "Blue isolated the app server on move 1 and held.");
  });

  it("covers held, exposure, quiet, and unfinished runs", () => {
    assert.equal(storyLine([ISOLATE("app")], ROLES, "held"),
      "Blue isolated the app server and red never got in.");
    assert.equal(storyLine([SCAN, AUTH("db"), SECRET], ROLES, "secret-exposed"),
      "Red read the protected file on move 3.");
    assert.equal(storyLine([SCAN], ROLES, "no-intrusion"), "Red never found a way in.");
    assert.equal(storyLine([], ROLES, "stalemate"), "An unfinished exercise.");
  });
});

describe("formatDuration", () => {
  it("formats wall clock and falls back to moves", () => {
    assert.equal(formatDuration("2026-10-27T00:00:00Z", "2026-10-27T00:00:45Z", 20), "45s");
    assert.equal(formatDuration("2026-10-27T00:00:00Z", "2026-10-27T00:12:30Z", 20), "12m 30s");
    assert.equal(formatDuration(null, null, 20), "20 moves");
    assert.equal(formatDuration("nope", "nope", 1), "1 move");
    assert.equal(
      formatDuration("2026-10-27T00:00:45Z", "2026-10-27T00:00:00Z", 20), "20 moves");
  });
});

describe("partitionLobby", () => {
  const card = (id, outcomeKey) => ({
    id, outcomeKey, outcome: { label: outcomeKey, tone: "blue" },
  });

  it("orders featured by curation and caps at six", () => {
    const cards = [
      card("a", "contained"), card("b", "held"), card("c", "contained"),
      card("d", "held"), card("e", "contained"), card("f", "held"),
      card("g", "contained"),
    ];
    const { featured, archive } = partitionLobby(
      cards, ["g", "f", "e", "d", "c", "b", "a", "missing"]);
    assert.deepEqual(featured.map((c) => c.id), ["g", "f", "e", "d", "c", "b"]);
    assert.deepEqual(archive.map((c) => c.id), ["a"]);
  });

  it("ranks the archive and drops fake fixtures everywhere", () => {
    const cards = [
      card("fake-1", "contained"), card("x", "no-intrusion"),
      card("y", "secret-exposed"), card("z", "contained"),
    ];
    const { featured, archive } = partitionLobby(cards, ["fake-1", "x"]);
    assert.deepEqual(featured.map((c) => c.id), ["x"]);
    assert.deepEqual(archive.map((c) => c.id), ["z", "y"]);
  });
});
