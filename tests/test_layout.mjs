import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  CLASSIC_LAYOUT, SEGMENTS, WORLD, arenaGeometry, classicLayout, columnsOf,
  edgesOf, entryOf, layoutFromEvents, layoutHosts, layoutWeights, mapGeometry,
  nodeLabel, nodeLabels, normalizeLayout, roleKind, roleSub, scenarioMeta, scenarioText,
} from "../dashboard/js/layout.js";
import { MAP_EDGES, MAP_POSITIONS, MAP_VIEWBOX } from "../dashboard/js/network.js";
import { redNotches } from "../dashboard/js/state.js";

// Ten-host generated-style range: dmz pair, six internal, data pair.
function genLayout() {
  return normalizeLayout({
    scenario_id: "scn-0007-hard-a0",
    hosts: [
      { id: "lark", role: "web portal", segment: "dmz", links: ["inlet"], weight: 2 },
      { id: "inlet", role: "file share", segment: "internal", links: ["lark", "willow", "sedge"], weight: 3 },
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

function ev(actor, tool, args, result, id) {
  return { id: id || "e", actor, tool, args, result: result || { ok: true } };
}

describe("layout extraction", () => {
  it("reads the layout from the first event", () => {
    const layout = genLayout();
    const events = [
      ev("system", "clean_start_check", { scenario_layout: layout, scenario_id: layout.scenario_id }),
      ev("red", "scan_host", { host: "lark" }),
    ];
    assert.deepEqual(layoutFromEvents(events), layout);
  });

  it("falls back to classic for old logs and garbage", () => {
    assert.deepEqual(layoutFromEvents([ev("red", "scan_host", { host: "web" })]), CLASSIC_LAYOUT);
    assert.deepEqual(layoutFromEvents([]), CLASSIC_LAYOUT);
    const garbage = [ev("system", "clean_start_check", { scenario_layout: { hosts: "nope" } })];
    assert.deepEqual(layoutFromEvents(garbage), CLASSIC_LAYOUT);
    const empty = [ev("system", "clean_start_check", { scenario_layout: { hosts: [] } })];
    assert.deepEqual(layoutFromEvents(empty), CLASSIC_LAYOUT);
  });

  it("normalizes segments, links, weights, and the jewel", () => {
    const clean = normalizeLayout({
      hosts: [
        { id: "a", role: "app", segment: "nowhere", links: ["b", "ghost", "a"], weight: 0 },
        { id: "b", role: "web portal", segment: "dmz", links: ["a"], weight: 3 },
      ],
      jewel_host: "ghost",
    });
    assert.equal(clean.hosts[0].segment, "internal");
    assert.deepEqual(clean.hosts[0].links, ["b"]);
    assert.equal(clean.hosts[0].weight, 1);
    assert.equal(clean.jewel_host, "");
  });

  it("reads scenario identity with a classic default", () => {
    assert.deepEqual(scenarioMeta([]), { id: "classic", seed: null, difficulty: null });
    const events = [ev("system", "clean_start_check",
      { scenario_id: "scn-1", scenario_seed: 1, scenario_difficulty: "hard" })];
    assert.deepEqual(scenarioMeta(events), { id: "scn-1", seed: 1, difficulty: "hard" });
    assert.equal(scenarioText({ id: "classic" }), "classic");
    assert.equal(scenarioText({ id: "scn-1", difficulty: "hard" }), "scn-1 · hard");
  });
});

describe("role styling", () => {
  it("vaults the jewel, shrinks workstations, standards the rest", () => {
    assert.equal(roleKind("database", true), "vault");
    assert.equal(roleKind("backup", true), "vault");
    assert.equal(roleKind("workstation", false), "small");
    assert.equal(roleKind("analyst workstation", false), "small");
    assert.equal(roleKind("web portal", false), "standard");
    assert.equal(roleKind("app", false), "standard");
  });

  it("labels workstations WS and truncates long names", () => {
    assert.equal(nodeLabel("workstation", "analyst workstation"), "WS");
    assert.equal(nodeLabel("wren", "workstation"), "WS");
    assert.equal(nodeLabel("web", "public web front end"), "WEB");
    assert.equal(nodeLabel("harborlight", "file share"), "HARBORL");
  });

  it("keeps block labels unique across the range", () => {
    const labels = nodeLabels(normalizeLayout({
      hosts: [
        { id: "r7", role: "workstation", segment: "internal" },
        { id: "wren", role: "workstation", segment: "internal" },
        { id: "harborlight", role: "file share", segment: "dmz" },
        { id: "harborlog", role: "file share", segment: "data" },
      ],
    }));
    assert.equal(labels.r7, "WS");
    assert.equal(labels.wren, "WS·WR");
    assert.equal(labels.harborlight, "HARBORL");
    assert.equal(labels.harborlog, "HARBORL·HA");
    const classic = nodeLabels(classicLayout());
    assert.deepEqual(classic, { web: "WEB", app: "APP", db: "DB", workstation: "WS" });
  });

  it("captions roles like the classic range did", () => {
    assert.equal(roleSub("public web front end", false), "public site");
    assert.equal(roleSub("internal reporting service", false), "reports server");
    assert.equal(roleSub("analyst workstation", false), "staff desktop");
    assert.equal(roleSub("internal data store", false), "");
    assert.equal(roleSub("database", true), "");
    assert.equal(roleSub("web portal", false), "public site");
    assert.equal(roleSub("file share", false), "file share");
  });
});

describe("arena geometry", () => {
  it("lays classic hosts on segment columns", () => {
    const geo = arenaGeometry(classicLayout());
    assert.deepEqual(geo.nodes.web, { x: 330, y: 450, r: 88, kind: "standard" });
    assert.deepEqual(geo.nodes.app, { x: 790, y: 450, r: 88, kind: "standard" });
    assert.deepEqual(geo.nodes.db, { x: 1250, y: 450, r: 100, kind: "vault" });
    assert.deepEqual(geo.nodes.workstation, { x: 790, y: 704, r: 72, kind: "small" });
    assert.deepEqual(geo.edges, [["web", "app"], ["app", "db"], ["app", "workstation"], ["red", "web"]]);
    assert.deepEqual(geo.red, { x: 90, y: 450 });
    assert.deepEqual(geo.blue, { x: 1490, y: 790 });
    assert.equal(geo.entry, "web");
    assert.equal(geo.jewel, "db");
  });

  it("keeps ten hosts inside the world without overlaps", () => {
    const layout = genLayout();
    const geo = arenaGeometry(layout);
    const ids = Object.keys(geo.nodes);
    assert.equal(ids.length, 10);
    for (const id of ids) {
      const node = geo.nodes[id];
      assert.ok(node.x - node.r - 10 >= 0, `${id} left`);
      assert.ok(node.x + node.r + 50 <= WORLD.w, `${id} right`);
      assert.ok(node.y - node.r - 10 >= 0, `${id} top`);
      assert.ok(node.y + node.r + 10 <= WORLD.h, `${id} bottom`);
    }
    const plate = (node) => ({
      x0: node.x - node.r - 8, x1: node.x + node.r + 8,
      y0: node.y - node.r - 8, y1: node.y + node.r + 8,
    });
    const drawn = (node) => ({
      x0: node.x - node.r * 1.3, x1: node.x + node.r * 1.3,
      y0: node.y - node.r * 1.35,
      y1: node.y + node.r * (node.kind === "vault" ? 2.1 : 1.6),
    });
    const hits = (p, q) => p.x0 < q.x1 && q.x0 < p.x1 && p.y0 < q.y1 && q.y0 < p.y1;
    for (let i = 0; i < ids.length; i += 1) {
      for (let j = i + 1; j < ids.length; j += 1) {
        const a = geo.nodes[ids[i]];
        const b = geo.nodes[ids[j]];
        assert.ok(!hits(plate(a), plate(b)), `${ids[i]} plate overlaps ${ids[j]}`);
        if (a.x === b.x) {
          assert.ok(!hits(drawn(a), drawn(b)), `${ids[i]} drawn overlaps ${ids[j]}`);
        }
      }
    }
  });

  it("shrinks dense columns instead of stacking plates", () => {
    const geo = arenaGeometry(genLayout());
    for (const id of ["inlet", "willow", "sedge", "quill", "wren", "ochre"]) {
      assert.ok(geo.nodes[id].r < 88, `${id} r=${geo.nodes[id].r}`);
      assert.ok(geo.nodes[id].r >= 34, `${id} r=${geo.nodes[id].r}`);
    }
  });

  it("puts the jewel in the last column and red left of dmz", () => {
    const geo = arenaGeometry(genLayout());
    const xs = Object.values(geo.nodes).map((node) => node.x);
    assert.equal(geo.nodes.kelp.x, Math.max(...xs));
    assert.ok(geo.red.x < Math.min(...xs));
    assert.ok(geo.blue.x > Math.max(...xs));
  });

  it("derives columns, edges, entry, and weights", () => {
    const layout = genLayout();
    assert.deepEqual(columnsOf(layout), [["lark", "beacon"], ["inlet", "willow", "sedge", "quill", "wren", "ochre"], ["kelp", "tarn"]]);
    assert.ok(edgesOf(layout).some(([a, b]) => (a === "inlet" && b === "lark") || (a === "lark" && b === "inlet")));
    assert.equal(entryOf(layout), "lark");
    assert.equal(layoutWeights(layout).kelp, 5);
    assert.deepEqual(layoutHosts(layout).length, 10);
  });
});

describe("map geometry", () => {
  const sorted = (edges) => edges.map(([a, b]) => [a, b].sort().join("|")).sort();

  it("reproduces the classic map exactly", () => {
    const geo = mapGeometry(classicLayout());
    assert.deepEqual(geo.positions, MAP_POSITIONS);
    assert.deepEqual(sorted(geo.edges), sorted(MAP_EDGES));
    assert.equal(geo.jewel, "db");
    assert.equal(geo.radius, 26);
  });

  it("keeps generated maps inside the viewbox", () => {
    const geo = mapGeometry(genLayout());
    for (const [id, pos] of Object.entries(geo.positions)) {
      assert.ok(pos.x - geo.radius - 60 >= 0, `${id} left`);
      assert.ok(pos.x + geo.radius + 60 <= MAP_VIEWBOX.w, `${id} right`);
      assert.ok(pos.y - geo.radius - 55 >= 0, `${id} top`);
      assert.ok(pos.y + geo.radius + 25 <= MAP_VIEWBOX.h, `${id} bottom`);
    }
  });

  it("spaces a three-high column so labels clear", () => {
    // scn-0007 shape: vetch, ember, wren share the internal column.
    const layout = normalizeLayout({
      scenario_id: "scn-0007-easy-a0",
      hosts: [
        { id: "nimbus", role: "web portal", segment: "dmz", links: [], weight: 3 },
        { id: "vetch", role: "file share", segment: "internal", links: [], weight: 2 },
        { id: "ember", role: "jump host", segment: "internal", links: [], weight: 2 },
        { id: "wren", role: "app", segment: "internal", links: [], weight: 3 },
        { id: "grove", role: "database", segment: "data", links: [], weight: 5 },
      ],
      jewel_host: "grove",
    });
    const geo = mapGeometry(layout);
    assert.equal(geo.crowded, false);
    const ys = ["vetch", "ember", "wren"].map((id) => geo.positions[id].y).sort((a, b) => a - b);
    // Name above plus state below need daylight between centers.
    for (let i = 1; i < ys.length; i += 1) {
      assert.ok(ys[i] - ys[i - 1] >= 100, `pitch ${ys[i] - ys[i - 1]}`);
    }
    for (const y of ys) {
      assert.ok(y - geo.radius - 55 >= 0, "name label fits");
      assert.ok(y + geo.radius + 25 <= MAP_VIEWBOX.h, "state label fits");
    }
  });

  it("splits columns past three hosts into sub-columns", () => {
    const layout = normalizeLayout({
      scenario_id: "tall",
      hosts: [
        { id: "a", role: "app", segment: "dmz", links: [], weight: 1 },
        { id: "b", role: "app", segment: "internal", links: [], weight: 1 },
        { id: "c", role: "app", segment: "internal", links: [], weight: 1 },
        { id: "d", role: "app", segment: "internal", links: [], weight: 1 },
        { id: "e", role: "app", segment: "internal", links: [], weight: 1 },
        { id: "f", role: "database", segment: "data", links: [], weight: 1 },
      ],
      jewel_host: "f",
    });
    const geo = mapGeometry(layout);
    assert.equal(geo.crowded, true);
    const xs = new Set(["b", "c", "d", "e"].map((id) => geo.positions[id].x));
    assert.equal(xs.size, 2, "four-high column splits in two");
  });
});

describe("red notches", () => {
  const ADMIN = ev("red", "http_request", { host: "web", path: "/admin" }, { ok: true });
  const AUTH = (host) => ev("red", "try_credential", { host }, { ok: true, authenticated: true });
  const CREDS = (host, path) => ev("red", "read_file", { host, path }, { ok: true, path });
  const SECRET = ev("red", "read_file", { host: "db", path: "/secret/flag" },
    { ok: true, path: "/secret/flag" });
  const ISOLATE = (host) => ev("blue", "isolate_host", { host }, { ok: true });

  it("sizes a classic chain at five lit notches", () => {
    const full = [ADMIN, AUTH("app"), CREDS("app", "/srv/app/config.ini"), AUTH("db"), SECRET];
    const out = redNotches(full, full);
    assert.equal(out.total, 5);
    assert.equal(out.level, 5);
    assert.deepEqual(out.notches, [true, true, true, true, true]);
    assert.deepEqual(out.milestones.map((m) => m.kind), ["leak", "signin", "creds", "signin", "secret"]);
    assert.equal(out.milestones[1].label, "Sign-in app");
  });

  it("sizes an easy chain at three notches", () => {
    const leak = ev("red", "http_request", { host: "upland", path: "/admin/status" }, { ok: true });
    const auth = AUTH("tarn");
    const jewel = ev("red", "read_file", { host: "tarn", path: "/secret/x" }, { ok: true, path: "/secret/x" });
    const out = redNotches([leak, auth, jewel], [leak, auth]);
    assert.equal(out.total, 3);
    assert.equal(out.level, 2);
    assert.deepEqual(out.notches, [true, true, false]);
  });

  it("pads quiet episodes to three dark notches", () => {
    const out = redNotches([ev("red", "scan_host", { host: "web" })], []);
    assert.equal(out.total, 3);
    assert.equal(out.level, 0);
    assert.deepEqual(out.notches, [false, false, false]);
  });

  it("drops back when blue contains the relied-on host", () => {
    const full = [ADMIN, AUTH("app"), ISOLATE("app")];
    const out = redNotches(full, full);
    assert.deepEqual(out.notches.slice(0, 2), [true, false]);
    assert.equal(out.level, 1);
    const relit = redNotches(full, [...full, AUTH("app")]);
    assert.deepEqual(relit.notches.slice(0, 2), [true, true]);
  });

  it("caps sprawling episodes at five with the secret last", () => {
    const full = [ADMIN, AUTH("a"), AUTH("b"), AUTH("c"), AUTH("d"), CREDS("a", "/srv/x/config.ini"), SECRET];
    const out = redNotches(full, full);
    assert.equal(out.total, 5);
    assert.equal(out.milestones[4].kind, "secret");
    assert.equal(out.level, 5);
  });
});
