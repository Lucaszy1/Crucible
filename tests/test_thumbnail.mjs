// Thumbnail state and geometry: outcome storytelling per host,
// the red path in compromise order, and column layout in bounds.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { classicLayout, normalizeLayout } from "../dashboard/js/layout.js";
import { thumbnailModel, thumbnailState } from "../dashboard/js/thumbnail.js";

function ev(id, actor, tool, args, result) {
  return { id, actor, tool, args: args || {}, result: result || { ok: true } };
}

const AUTH = (host) => ev(`a-${host}`, "red", "try_credential", { host },
  { ok: true, host, authenticated: true });
const ISOLATE = (host) => ev(`c-${host}`, "blue", "isolate_host", { host }, { ok: true, host });
const PATCH = (host) => ev(`p-${host}`, "blue", "patch_service", { host }, { ok: true, host });
const SECRET = (host) => ev(`s-${host}`, "red", "read_file", { host },
  { ok: true, host, path: "/secret/exercise_secret.txt" });
const SCAN = (host) => ev(`q-${host}`, "red", "scan_host", { host }, { ok: true, host });
const NOTE = (host) => ev(`l-${host}`, "blue", "report_finding", { host }, { ok: true, host });

function genLayout() {
  return normalizeLayout({
    scenario_id: "scn-0007-hard-a0",
    hosts: [
      { id: "lark", role: "web portal", segment: "dmz", links: ["inlet"], weight: 2 },
      { id: "inlet", role: "file share", segment: "internal", links: ["lark", "willow"], weight: 3 },
      { id: "willow", role: "web portal", segment: "internal", links: ["inlet", "quill"], weight: 3 },
      { id: "sedge", role: "app", segment: "internal", links: ["inlet", "wren"], weight: 3 },
      { id: "quill", role: "api", segment: "internal", links: ["willow", "kelp"], weight: 2 },
      { id: "wren", role: "workstation", segment: "internal", links: ["sedge"], weight: 1 },
      { id: "ochre", role: "jump host", segment: "internal", links: ["quill", "kelp"], weight: 2 },
      { id: "kelp", role: "backup", segment: "data", links: ["quill", "ochre"], weight: 5 },
      { id: "tarn", role: "database", segment: "data", links: ["kelp"], weight: 4 },
      { id: "beacon", role: "api", segment: "dmz", links: ["tarn"], weight: 2 },
    ],
    jewel_host: "kelp",
  });
}

describe("thumbnailState", () => {
  it("marks contained hosts held with shields and a calm wash", () => {
    const st = thumbnailState([SCAN("web"), AUTH("app"), ISOLATE("app")], classicLayout());
    assert.equal(st.outcome, "contained");
    assert.equal(st.wash, "calm");
    assert.equal(st.hosts.app.state, "held");
    assert.equal(st.hosts.app.shield, true);
    assert.equal(st.hosts.web.state, "probed");
    assert.deepEqual(st.path, ["app"]);
  });

  it("leaves no-intrusion runs quiet with no wash", () => {
    const st = thumbnailState([SCAN("web"), NOTE("web")], classicLayout());
    assert.equal(st.outcome, "no-intrusion");
    assert.equal(st.wash, "none");
    assert.equal(st.hosts.web.state, "probed");
    assert.equal(st.hosts.db.shield, false);
    assert.deepEqual(st.path, []);
  });

  it("shows held runs calm with a shield and no path", () => {
    const st = thumbnailState([SCAN("web"), ISOLATE("app")], classicLayout());
    assert.equal(st.outcome, "held");
    assert.equal(st.wash, "calm");
    assert.equal(st.hosts.app.state, "held");
    assert.deepEqual(st.path, []);
  });

  it("exposes the jewel with a crack and a red edge", () => {
    const st = thumbnailState([AUTH("app"), AUTH("db"), SECRET("db")], classicLayout());
    assert.equal(st.outcome, "secret-exposed");
    assert.equal(st.wash, "alert");
    assert.equal(st.hosts.db.state, "compromised");
    assert.equal(st.hosts.db.jewel, true);
    assert.equal(st.hosts.db.exposed, true);
    assert.deepEqual(st.path, ["app", "db"]);
  });

  it("traces uncontained paths in compromise order", () => {
    const st = thumbnailState([AUTH("app"), SCAN("db"), AUTH("db"), NOTE("app")], classicLayout());
    assert.equal(st.outcome, "uncontained");
    assert.equal(st.wash, "alert");
    assert.deepEqual(st.path, ["app", "db"]);
    assert.equal(st.hosts.db.state, "compromised");
  });

  it("patches shield like isolates and restores clear", () => {
    const held = thumbnailState([AUTH("app"), PATCH("app")], classicLayout());
    assert.equal(held.hosts.app.state, "held");
    assert.equal(held.hosts.app.shield, true);
    const cleared = thumbnailState(
      [AUTH("app"), ISOLATE("app"), ev("r", "blue", "restore_host", { host: "app" }, { ok: true, host: "app" })],
      classicLayout());
    assert.equal(cleared.hosts.app.state, "clean");
    assert.equal(cleared.hosts.app.shield, false);
    assert.deepEqual(cleared.path, ["app"]);
  });

  it("lists each compromised host once in first-won order", () => {
    const st = thumbnailState(
      [AUTH("app"), ISOLATE("app"), AUTH("app"), AUTH("db"), SECRET("db")],
      classicLayout());
    assert.deepEqual(st.path, ["app", "db"]);
  });

  it("ignores unknown hosts and failed actions", () => {
    const st = thumbnailState([
      ev("x", "red", "try_credential", { host: "ghost" }, { ok: true, host: "ghost", authenticated: true }),
      ev("y", "blue", "isolate_host", { host: "app" }, { ok: false, error: "no" }),
    ], classicLayout());
    assert.deepEqual(st.path, []);
    assert.equal(st.hosts.app.state, "clean");
  });
});

describe("thumbnailModel", () => {
  it("lays classic hosts on ordered segment columns in bounds", () => {
    const model = thumbnailModel(classicLayout(), thumbnailState([], classicLayout()), 120, 68);
    assert.equal(model.blocks.length, 4);
    const at = Object.fromEntries(model.blocks.map((b) => [b.id, b]));
    assert.ok(at.web.x < at.app.x && at.app.x < at.db.x);
    assert.equal(at.db.kind, "vault");
    assert.equal(at.workstation.kind, "small");
    assert.equal(at.app.kind, "standard");
    for (const block of model.blocks) {
      assert.ok(block.x - block.r >= 0, `${block.id} left`);
      assert.ok(block.x + block.r <= 120, `${block.id} right`);
      assert.ok(block.y - block.r >= 0, `${block.id} top`);
      assert.ok(block.y + block.r <= 68, `${block.id} bottom`);
    }
    for (const [a, b] of model.links) {
      assert.ok(at[a] && at[b], `link ${a}-${b} resolves`);
    }
  });

  it("keeps ten generated hosts inside with clear plates", () => {
    const layout = genLayout();
    const events = [AUTH("inlet"), AUTH("kelp"), SECRET("kelp"), ISOLATE("inlet")];
    const model = thumbnailModel(layout, thumbnailState(events, layout), 176, 100);
    assert.equal(model.blocks.length, 10);
    assert.equal(model.wash, "alert");
    assert.deepEqual(model.path, ["inlet", "kelp"]);
    const jewel = model.blocks.find((b) => b.id === "kelp");
    assert.equal(jewel.kind, "vault");
    assert.equal(jewel.exposed, true);
    for (const block of model.blocks) {
      assert.ok(block.x - block.r >= 0, `${block.id} left`);
      assert.ok(block.x + block.r <= 176, `${block.id} right`);
      assert.ok(block.y - block.r >= 0, `${block.id} top`);
      assert.ok(block.y + block.r <= 100, `${block.id} bottom`);
    }
    for (let i = 0; i < model.blocks.length; i += 1) {
      for (let j = i + 1; j < model.blocks.length; j += 1) {
        const a = model.blocks[i];
        const b = model.blocks[j];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        assert.ok(dist >= a.r + b.r, `${a.id} overlaps ${b.id}`);
      }
    }
  });

  it("falls back to classic for missing layouts and states", () => {
    const model = thumbnailModel(null, null, 120, 68);
    assert.equal(model.blocks.length, 4);
    assert.equal(model.wash, "none");
    assert.deepEqual(model.path, []);
  });
});
