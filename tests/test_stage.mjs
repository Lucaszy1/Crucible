// Cinematic stage core: layout without overlaps, a camera that
// converges without jitter, lane lines from real events, and
// entity levels from real progress.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  applyCamera, blueLine, dollyTarget, frameBudget, frameP95, guardianPulse, intrusionLevel,
  laneFeed, paintGuardian, paintIntrusion, redLine, stageLayout, stageWash, stepCamera,
} from "../dashboard/js/stage.js";
import { arenaGeometry, classicLayout, normalizeLayout } from "../dashboard/js/layout.js";

function ev(id, actor, tool, args, result, extra) {
  return { id, actor, tool, args: args || {}, result: result || { ok: true }, ...(extra || {}) };
}

function genLayout(count) {
  const roles = ["web portal", "app", "api", "file share", "backup", "workstation", "jump host", "database"];
  const segments = ["dmz", "internal", "internal", "data"];
  const hosts = [];
  for (let i = 0; i < count; i += 1) {
    hosts.push({
      id: `h${i}`,
      role: roles[i % roles.length],
      segment: segments[i % segments.length],
      links: i + 1 < count ? [`h${i + 1}`] : [],
      weight: 2,
    });
  }
  return normalizeLayout({ scenario_id: "t", hosts, jewel_host: `h${count - 1}` });
}

// Viewport plus DOM chrome boxes in the same proportions the
// dashboard draws: HUD block on top, caption at the bottom, the
// auditor card under the blue banner while it shows.
function chromeFor(w, h, audOn) {
  const hudBottom = Math.round(Math.min(130, Math.max(60, h * 0.2)));
  const capTop = h - Math.round(Math.min(150, Math.max(56, h * 0.18)));
  return {
    hudBottom,
    capTop,
    audBottom: audOn ? hudBottom + Math.round(Math.min(220, Math.max(120, h * 0.32))) : null,
  };
}

function hits(a, b) {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

function inside(inner, outer) {
  return inner.x >= outer.x - 0.5 && inner.y >= outer.y - 0.5 &&
    inner.x + inner.w <= outer.x + outer.w + 0.5 &&
    inner.y + inner.h <= outer.y + outer.h + 0.5;
}

describe("stageLayout", () => {
  const sizes = [
    [1440, 812],
    [1920, 1080],
    [400, 225],
  ];

  it("uses side lanes wide and stacked strips narrow", () => {
    const geo = arenaGeometry(classicLayout());
    assert.equal(stageLayout({ w: 1440, h: 812 }, geo, chromeFor(1440, 812, false)).mode, "side");
    assert.equal(stageLayout({ w: 400, h: 225 }, geo, chromeFor(400, 225, false)).mode, "stack");
  });

  it("keeps lanes, entities, hosts, and chrome disjoint", () => {
    for (const [w, h] of sizes) {
      for (const count of [4, 6, 8, 10]) {
        for (const audOn of [false, true]) {
          const layout = audOn || count !== 4 ? genLayout(count) : classicLayout();
          const geo = arenaGeometry(layout);
          const chrome = chromeFor(w, h, audOn);
          const stage = stageLayout({ w, h }, geo, chrome);
          const frame = { x: 0, y: 0, w, h };
          const hud = { x: 0, y: 0, w, h: chrome.hudBottom };
          const cap = { x: 0, y: chrome.capTop, w, h: h - chrome.capTop };
          const tag = `${w}x${h} n=${count} aud=${audOn}`;
          for (const side of ["red", "blue"]) {
            const lane = stage.lanes[side];
            assert.ok(inside(lane, frame), `${tag} ${side} lane inside`);
            assert.ok(!hits(lane, hud), `${tag} ${side} lane clears HUD`);
            assert.ok(!hits(lane, cap), `${tag} ${side} lane clears caption`);
          }
          assert.ok(!hits(stage.lanes.red, stage.lanes.blue), `${tag} lanes disjoint`);
          if (audOn) {
            const aud = { x: w - 260, y: chrome.hudBottom, w: 250, h: chrome.audBottom - chrome.hudBottom };
            // Priority matches the rail: HUD and caption first, then
            // the transient auditor card. Tiny stages cannot clear
            // all three while the four-second verdict shows.
            if (chrome.audBottom + 10 + 40 <= chrome.capTop - 10) {
              assert.ok(!hits(stage.lanes.blue, aud), `${tag} blue lane clears auditor`);
            }
          }
          for (const [id, host] of Object.entries(stage.hosts)) {
            const box = { x: host.x - host.r, y: host.y - host.r, w: host.r * 2, h: host.r * 2 };
            assert.ok(inside(box, frame), `${tag} ${id} inside`);
            assert.ok(!hits(box, stage.lanes.red), `${tag} ${id} clears red lane`);
            assert.ok(!hits(box, stage.lanes.blue), `${tag} ${id} clears blue lane`);
            assert.ok(!hits(box, hud), `${tag} ${id} clears HUD`);
            assert.ok(!hits(box, cap), `${tag} ${id} clears caption`);
            // Visibility floors scale with available area: a 10-host
            // range on a 400px stage resolves to dots, honestly small
            // but inside and disjoint, which the checks above pin.
            if (stage.area.bottom - stage.area.top >= 120) {
              assert.ok(host.r >= 4, `${tag} ${id} stays visible`);
            }
            if (stage.area.bottom - stage.area.top >= 200) {
              assert.ok(host.r >= 6, `${tag} ${id} stays readable`);
            }
          }
          const ents = [stage.entities.intrusion, stage.entities.guardian];
          for (const [name, ent] of [["intrusion", ents[0]], ["guardian", ents[1]]]) {
            const box = { x: ent.x - ent.r, y: ent.y - ent.r, w: ent.r * 2, h: ent.r * 2 };
            assert.ok(inside(box, frame), `${tag} ${name} inside`);
            assert.ok(!hits(box, hud), `${tag} ${name} clears HUD`);
            assert.ok(!hits(box, cap), `${tag} ${name} clears caption`);
            if (stage.mode === "side") {
              assert.ok(!hits(box, stage.lanes.red), `${tag} ${name} clears red lane`);
              assert.ok(!hits(box, stage.lanes.blue), `${tag} ${name} clears blue lane`);
            } else {
              // Narrow stages dock the glyphs inside their own strips.
              const home = name === "intrusion" ? stage.lanes.red : stage.lanes.blue;
              const away = name === "intrusion" ? stage.lanes.blue : stage.lanes.red;
              assert.ok(inside(box, home), `${tag} ${name} docks in its strip`);
              assert.ok(!hits(box, away), `${tag} ${name} clears the far strip`);
            }
            for (const id of Object.keys(stage.hosts)) {
              const host = stage.hosts[id];
              const dist = Math.hypot(ent.x - host.x, ent.y - host.y);
              assert.ok(dist >= ent.r + host.r, `${tag} ${name} clears ${id}`);
            }
          }
          const pair = Math.hypot(ents[0].x - ents[1].x, ents[0].y - ents[1].y);
          assert.ok(pair >= ents[0].r + ents[1].r, `${tag} entities disjoint`);
          if (stage.mode === "side") {
            // Each entity spans roughly 30 to 35 percent of play height.
            const playH = stage.area.bottom - stage.area.top;
            for (const [name, ent] of [["intrusion", ents[0]], ["guardian", ents[1]]]) {
              const share = (ent.r * 2) / playH;
              assert.ok(share >= 0.28 && share <= 0.38, `${tag} ${name} share ${share}`);
            }
          }
        }
      }
    }
  });

  it("falls back to classic for missing geometry", () => {
    const stage = stageLayout({ w: 1440, h: 812 }, null, chromeFor(1440, 812, false));
    assert.equal(Object.keys(stage.hosts).length, 0);
    assert.equal(stage.mode, "side");
  });
});

describe("camera", () => {
  function rig() {
    const geo = arenaGeometry(classicLayout());
    const stage = stageLayout({ w: 1440, h: 812 }, geo, chromeFor(1440, 812, false));
    return { stage, nodes: geo.nodes };
  }

  it("converges without overshoot", () => {
    let cam = { dx: 0, dy: 0, z: 1 };
    const target = { dx: 120, dy: -60, z: 1.22 };
    let last = Infinity;
    for (let i = 0; i < 240; i += 1) {
      cam = stepCamera(cam, target, 16);
      const dist = Math.hypot(target.dx - cam.dx, target.dy - cam.dy, (target.z - cam.z) * 200);
      assert.ok(dist <= last + 1e-9, "distance never grows");
      last = dist;
    }
    assert.deepEqual(cam, target);
  });

  it("dollies toward the host while every host stays in band", () => {
    const { stage, nodes } = rig();
    for (const id of Object.keys(stage.hosts)) {
      const target = dollyTarget(stage, nodes, id);
      assert.ok(target.z >= 1 && target.z <= 1.23, `${id} zoom sane`);
      for (const [other, node] of Object.entries(nodes)) {
        const at = applyCamera(stage, target, node.x, node.y);
        const r = stage.hosts[other].r * target.z + 6;
        assert.ok(at.x - r >= stage.band.x0 - 0.5, `${id} keeps ${other} left`);
        assert.ok(at.x + r <= stage.band.x1 + 0.5, `${id} keeps ${other} right`);
        assert.ok(at.y - r >= stage.area.top - 0.5, `${id} keeps ${other} top`);
        assert.ok(at.y + r <= stage.area.bottom + 0.5, `${id} keeps ${other} bottom`);
      }
    }
    assert.deepEqual(dollyTarget(stage, nodes, "ghost"), { dx: 0, dy: 0, z: 1 });
  });
});

describe("lane lines", () => {
  it("renders red tools as commands", () => {
    assert.equal(redLine(ev("a", "red", "scan_host", { host: "web" })), "> scan web");
    assert.equal(
      redLine(ev("b", "red", "http_request", { host: "web", path: "/admin" }, { ok: true, status: 200 })),
      "> GET web/admin → 200");
    assert.equal(
      redLine(ev("c", "red", "try_credential", { host: "app", user: "svc" }, { ok: true, authenticated: true })),
      "> login app as svc · open");
    assert.equal(
      redLine(ev("d", "red", "read_file", { host: "db", path: "/secret/x" }, { ok: true })),
      "> read db:/secret/x");
    assert.equal(
      redLine(ev("e", "red", "http_request", { host: "app", path: "/" }, { ok: false })),
      "> GET app/ → … ✗");
  });

  it("never renders credential secrets", () => {
    const secret = "Sup3rS3cretValue99";
    const line = redLine(ev("c", "red", "try_credential",
      { host: "app", user: "svc", password: secret },
      { ok: true, authenticated: true }));
    assert.ok(!line.includes(secret), line);
    assert.ok(!line.toLowerCase().includes("password"), line);
  });

  it("renders blue moves, reasoning, and verdicts", () => {
    assert.equal(blueLine(ev("a", "blue", "read_logs", { host: "web" })), "· review logs on web");
    assert.equal(blueLine(ev("b", "blue", "isolate_host", { host: "app" })), "· isolate app ✓");
    assert.equal(
      blueLine(ev("c", "auditor", "audit", {}, { verdict: "justified", score: 8 })),
      "◆ auditor: justified 8/10");
    const feed = laneFeed([
      ev("a", "blue", "isolate_host", { host: "app" }, { ok: true }, { reasoning_summary: "Holding app." }),
      ev("b", "auditor", "audit", {}, { verdict: "justified", score: 9 }),
      ev("c", "red", "scan_host", { host: "web" }),
    ], "blue");
    assert.deepEqual(feed.map((line) => line.text),
      ["· isolate app ✓", "Holding app.", "◆ auditor: justified 9/10"]);
    assert.deepEqual(feed.map((line) => line.tone), ["cmd", "dim", "verdict"]);
    const reds = laneFeed([ev("c", "red", "scan_host", { host: "web" })], "red");
    assert.deepEqual(reds.map((line) => line.text), ["> scan web"]);
  });
});

describe("entity levels", () => {
  const ADMIN = ev("e1", "red", "http_request", { host: "web", path: "/admin" }, { ok: true, host: "web" });
  const AUTH = (host) => ev(`a-${host}`, "red", "try_credential", { host },
    { ok: true, host, authenticated: true });

  it("grows the intrusion with notch progress", () => {
    const full = [ADMIN, AUTH("app"), AUTH("db")];
    assert.equal(intrusionLevel(full, []), 0);
    const half = intrusionLevel(full, [ADMIN, AUTH("app")]);
    const done = intrusionLevel(full, full);
    assert.ok(half > 0 && half < done, `${half} < ${done}`);
    assert.ok(done <= 1);
  });

  it("pulses the guardian on blue containment", () => {
    assert.equal(guardianPulse([]), 0.15);
    assert.equal(guardianPulse([AUTH("app"), ev("x", "blue", "isolate_host", { host: "app" }, { ok: true })]), 1);
    const faded = guardianPulse([
      ev("x", "blue", "isolate_host", { host: "app" }, { ok: true }),
      AUTH("db"), AUTH("db"), AUTH("db"),
    ]);
    assert.ok(faded < 1 && faded >= 0.15, String(faded));
  });

  it("washes calm, alert, or neutral by outcome", () => {
    assert.equal(stageWash([ADMIN, AUTH("app"), ev("x", "blue", "isolate_host", { host: "app" }, { ok: true })]), "calm");
    assert.equal(stageWash([ev("s", "red", "read_file", { host: "db" }, { ok: true, path: "/secret/x" })]), "alert");
    assert.equal(stageWash([ev("q", "red", "scan_host", { host: "web" }, { ok: true })]), "none");
  });

  it("stays full detail on fast frames", () => {
    let avg = 16;
    let low = false;
    for (let i = 0; i < 60; i += 1) {
      const next = frameBudget(avg, low, 12);
      avg = next.avg;
      low = next.low;
    }
    assert.equal(low, false);
    assert.ok(avg < 17, String(avg));
  });

  it("drops to low detail on sustained slow frames", () => {
    let avg = 16;
    let low = false;
    for (let i = 0; i < 120; i += 1) {
      const next = frameBudget(avg, low, 50);
      avg = next.avg;
      low = next.low;
    }
    assert.equal(low, true);
  });

  it("reports frame time p95 for the debug overlay", () => {
    assert.equal(frameP95([]), 0);
    assert.equal(frameP95([16]), 16);
    const samples = [];
    for (let i = 0; i < 95; i += 1) samples.push(16);
    for (let i = 0; i < 5; i += 1) samples.push(50);
    assert.equal(frameP95(samples), 50);
    assert.equal(frameP95([16, null, 0, -3, "x"]), 16);
  });

  it("paints both entities on a stub context without throwing", () => {
    const calls = [];
    const stub = new Proxy({}, {
      get(target, prop) {
        if (prop === "measureText") return () => ({ width: 10 });
        if (prop === "canvas") return { width: 128, height: 128 };
        if (!(prop in target)) target[prop] = () => calls.push(prop);
        return target[prop];
      },
      set(target, prop, value) {
        target[prop] = value;
        return true;
      },
    });
    const tex = { width: 64, height: 64 };
    assert.doesNotThrow(() => paintIntrusion(stub, {}, { intrusionTex: tex }, 64, 64, 52, 1, 1.7));
    assert.doesNotThrow(() => paintGuardian(stub, {}, { guardianTex: tex }, 64, 64, 52, 1, 1.7));
    assert.doesNotThrow(() => paintIntrusion(stub, {}, {}, 64, 64, 52, 0, 0));
    assert.doesNotThrow(() => paintGuardian(stub, {}, {}, 64, 64, 52, 0.15, 0));
    assert.ok(calls.includes("clip"), "texture path clips");
    assert.ok(calls.includes("fillText"), "glyphs paint");
  });

  it("recovers to full detail only when frames are fast again", () => {
    const held = frameBudget(22, true, 20);
    assert.equal(held.low, true);
    let avg = 22;
    let low = true;
    for (let i = 0; i < 120; i += 1) {
      const next = frameBudget(avg, low, 12);
      avg = next.avg;
      low = next.low;
    }
    assert.equal(low, false);
  });
});
