// Cinematic stage core: layout without overlaps, a camera that
// converges without jitter, lane lines from real events, and
// entity levels from real progress.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  applyCamera, auditorChrome, blueLine, calloutForEvent, clashCrop, dollyTarget, FINISH_PACES, finishPlan,
  flashHold, FLIGHT_MS, flightLaneRect, frameBudget, frameP95, flightLane, fxForEvent,
  guardianPulse, hitstopFor, initStage, intrusionLevel, laneFeed, lanePoint, LOW_STREAK,
  meltChar, meltDrift, meltGlyphAlpha, meltGlyphPx, meltHaloAlpha, meltSpriteAlpha, meltStride, moveSpanMs, paintGuardian,
  paintIntrusion, projBody, redLine, renderStageStatic, renderStageThumb, sampleMeltMask,
  setStageActive, setStageReduced, stageLayout, stageNotify, stageWash, stepCamera, trailSpan,
  winnerBeatTarget,
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
          // The control bar rides the HUD center; canvas work must
          // never enter its safe zone from below.
          const bar = { x: w * 0.3, y: 0, w: w * 0.4, h: chrome.hudBottom };
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
          // The projectile lane flies over the network but must stay
          // inside the frame and clear of every chrome box: the HUD
          // and the bar inside it, the caption, and the auditor card.
          // Hosts may pass under the lane; paint order keeps the
          // projectiles on top.
          const laneBox = flightLaneRect(stage.entities.intrusion, stage.entities.guardian);
          const curveBox = flightLaneRect(stage.entities.intrusion, stage.entities.guardian, 0);
          assert.ok(inside(curveBox, frame), `${tag} lane inside`);
          assert.ok(!hits(laneBox, hud), `${tag} lane clears HUD`);
          assert.ok(!hits(laneBox, bar), `${tag} lane clears bar zone`);
          assert.ok(!hits(laneBox, cap), `${tag} lane clears caption`);
          if (audOn && chrome.audBottom + 10 + 40 <= chrome.capTop - 10) {
            const laneAud = { x: w - 260, y: chrome.hudBottom, w: 250, h: chrome.audBottom - chrome.hudBottom };
            // The curve itself clears the transient card; impact
            // flashes at the entity rims may pass under it.
            assert.ok(!hits(curveBox, laneAud), `${tag} lane clears auditor`);
          }
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
    let slow = 0;
    for (let i = 0; i < 120; i += 1) {
      const next = frameBudget(avg, low, 50, slow);
      avg = next.avg;
      low = next.low;
      slow = next.slow;
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
    const body = { width: 512, height: 512 };
    const full = { sprites: {}, bodies: { intrusion: body, guardian: body },
      swayX: 4, flare: 0.5, recoil: 0.5, burst: 0.5 };
    assert.doesNotThrow(() => paintIntrusion(stub, full, 64, 64, 52, 1, 1.7));
    assert.doesNotThrow(() => paintGuardian(stub, full, 64, 64, 52, 1, 1.7));
    assert.doesNotThrow(() => paintIntrusion(stub, {}, 64, 64, 52, 0, 0));
    assert.doesNotThrow(() => paintGuardian(stub, {}, 64, 64, 52, 0.15, 0));
    assert.ok(calls.includes("drawImage"), "splash body draws");
    assert.ok(calls.includes("fillText"), "glyphs paint");
  });

  it("maps red tools to host marks plus a shard at the Guardian", () => {
    const kinds = (ev, ctx) => fxForEvent(ev, ctx).map((f) => f.kind);
    assert.deepEqual(kinds(ev("a", "red", "scan_host", { host: "web" }, { ok: true })), ["fan", "volley"]);
    assert.deepEqual(kinds(ev("b", "red", "http_request", { host: "web" }, { ok: true })), ["needle", "lance"]);
    assert.deepEqual(kinds(ev("c", "red", "list_files", { host: "app" }, { ok: true })), ["needle", "lance"]);
    assert.deepEqual(kinds(ev("d", "red", "try_credential", { host: "app" }, { ok: true })), ["keys", "lance"]);
    assert.deepEqual(kinds(ev("e", "red", "try_credential", { host: "app" },
      { ok: true, authenticated: true }), {}), ["pierce", "lance"]);
    assert.deepEqual(kinds(ev("f", "red", "try_credential", { host: "db" },
      { ok: true, authenticated: true }), { lastRedHost: "app" }), ["pierce", "lance", "tendril"]);
    const tendril = fxForEvent(ev("f", "red", "try_credential", { host: "db" },
      { ok: true, authenticated: true }), { lastRedHost: "app" })[2];
    assert.equal(tendril.from, "app");
    assert.deepEqual(kinds(ev("g", "red", "try_credential", { host: "app" },
      { ok: true, authenticated: true }), { lastRedHost: "app" }), ["pierce", "lance"]);
    assert.deepEqual(kinds(ev("h", "red", "read_file", { host: "db", path: "/etc/x" },
      { ok: true })), ["siphon", "lance"]);
    assert.deepEqual(kinds(ev("i", "red", "read_file", { host: "db" },
      { ok: true, path: "/secret/crown" })), ["grab", "lance"]);
    assert.deepEqual(kinds(ev("j", "red", "report_finding", {}, { ok: true })), []);
    assert.deepEqual(kinds(ev("k", "red", "scan_host", { host: "web" }, { ok: false })), ["fizzle", "lance"]);
    assert.deepEqual(kinds(ev("l", "red", "read_file", { host: "db" }, { ok: false })), ["fizzle", "lance"]);
  });

  it("sizes red projectiles by significance and marks failures", () => {
    const proj = (entry) => fxForEvent(entry, {}).find((f) => f.kind === "lance" || f.kind === "volley");
    const scan = proj(ev("a", "red", "scan_host", { host: "web" }, { ok: true }));
    assert.equal(scan.kind, "volley");
    assert.equal(scan.size, "small");
    assert.equal(scan.fail, false);
    assert.equal(scan.flight, FLIGHT_MS.small);
    const signin = proj(ev("b", "red", "try_credential", { host: "db" },
      { ok: true, authenticated: true }));
    assert.equal(signin.size, "heavy");
    assert.equal(signin.fail, false);
    const grab = proj(ev("c", "red", "read_file", { host: "db" }, { ok: true, path: "/secret/crown" }));
    assert.equal(grab.size, "heavy");
    const probe = proj(ev("d", "red", "http_request", { host: "web" }, { ok: true }));
    assert.equal(probe.size, "standard");
    const guess = proj(ev("e", "red", "try_credential", { host: "app" }, { ok: true }));
    assert.equal(guess.fail, true);
    const fail = proj(ev("f", "red", "read_file", { host: "db" }, { ok: false }));
    assert.equal(fail.fail, true);
  });

  it("maps blue tools to host marks plus an orb at the Intrusion", () => {
    const kinds = (ev, ctx) => fxForEvent(ev, ctx).map((f) => f.kind);
    assert.deepEqual(kinds(ev("a", "blue", "read_logs", { host: "web" }, { ok: true })), ["scanring", "orb"]);
    assert.deepEqual(kinds(ev("b", "blue", "report_finding", {}, { ok: true })), ["flare", "orb"]);
    assert.deepEqual(kinds(ev("c", "blue", "isolate_host", { host: "app" }, { ok: true })),
      ["slam", "ring", "orb"]);
    assert.deepEqual(kinds(ev("d", "blue", "patch_service", { host: "app" }, { ok: true })), ["sweep", "orb"]);
    assert.deepEqual(kinds(ev("e", "blue", "restore_host", { host: "app" }, { ok: true })), ["reconnect", "orb"]);
    assert.deepEqual(kinds(ev("f", "blue", "isolate_host", { host: "app" }, { ok: false })), ["fizzle", "orb"]);
    assert.deepEqual(kinds(ev("g", "auditor", "audit", {}, {})), []);
    assert.deepEqual(fxForEvent(null, {}), []);
  });

  it("sizes blue projectiles by significance and marks failures", () => {
    const proj = (entry) => fxForEvent(entry, {}).find((f) => f.kind === "orb");
    assert.equal(proj(ev("a", "blue", "read_logs", { host: "web" }, { ok: true })).size, "small");
    assert.equal(proj(ev("b", "blue", "report_finding", {}, { ok: true })).size, "small");
    assert.equal(proj(ev("c", "blue", "isolate_host", { host: "app" }, { ok: true })).size, "large");
    assert.equal(proj(ev("d", "blue", "patch_service", { host: "app" }, { ok: true })).size, "large");
    assert.equal(proj(ev("e", "blue", "restore_host", { host: "app" }, { ok: true })).size, "standard");
    const fail = proj(ev("f", "blue", "isolate_host", { host: "app" }, { ok: false }));
    assert.equal(fail.fail, true);
    assert.equal(fail.flight, FLIGHT_MS.standard);
  });

  it("flies hostless moves as projectiles alone", () => {
    const kinds = (entry) => fxForEvent(entry, {}).map((f) => f.kind);
    assert.deepEqual(kinds(ev("a", "red", "scan_host", {}, { ok: true })), ["volley"]);
    assert.deepEqual(kinds(ev("b", "blue", "patch_service", {}, { ok: true })), ["orb"]);
  });

  it("draws the flight lane over the network, deterministically", () => {
    const lane = flightLane({ x: 100, y: 400 }, { x: 900, y: 400 });
    assert.deepEqual(lanePoint(lane, 0), { x: 100, y: 400 });
    assert.deepEqual(lanePoint(lane, 1), { x: 900, y: 400 });
    const mid = lanePoint(lane, 0.5);
    assert.equal(mid.x, 500);
    assert.ok(mid.y < 400, `lane peaks above the chord, got ${mid.y}`);
    assert.deepEqual(flightLane({ x: 100, y: 400 }, { x: 900, y: 400 }), lane);
    assert.ok(lanePoint(lane, -1).y >= mid.y, "clamped ends stay below the peak");
  });

  it("names callouts from the technique data", () => {
    const map = {
      red: { try_credential: { technique: "T1110", name: "Brute Force" } },
      blue: { isolate_host: { label: "Contain" } },
    };
    const red = calloutForEvent(ev("a", "red", "try_credential", { host: "db" },
      { ok: true, authenticated: true }), map);
    assert.equal(red.tech, "T1110 · Brute Force");
    assert.equal(red.side, "red");
    assert.equal(red.failed, false);
    assert.ok(red.line.length > 8, red.line);
    const blue = calloutForEvent(ev("b", "blue", "isolate_host", { host: "db" }, { ok: true }), map);
    assert.equal(blue.tech, "Contain");
    assert.equal(blue.side, "blue");
    const failed = calloutForEvent(ev("c", "blue", "isolate_host", { host: "db" }, { ok: false }), map);
    assert.equal(failed.failed, true);
    const missing = calloutForEvent(ev("d", "red", "scan_host", { host: "web" }, { ok: true }), map);
    assert.equal(missing.tech, "scan_host");
    const nomap = calloutForEvent(ev("e", "red", "scan_host", { host: "web" }, { ok: true }), null);
    assert.equal(nomap.tech, "scan_host");
    assert.equal(calloutForEvent(ev("f", "auditor", "audit", {}, {}), map), null);
    assert.equal(calloutForEvent(null, map), null);
  });

  it("bounds the whole flight lane in its rect", () => {
    const box = flightLaneRect({ x: 100, y: 400, r: 60 }, { x: 900, y: 400, r: 60 });
    const lane = flightLane({ x: 100 + 42, y: 400 }, { x: 900 - 33, y: 400 });
    for (let i = 0; i <= 20; i += 1) {
      const pt = lanePoint(lane, i / 20);
      assert.ok(pt.x >= box.x && pt.x <= box.x + box.w, `sample ${i} x inside`);
      assert.ok(pt.y >= box.y && pt.y <= box.y + box.h, `sample ${i} y inside`);
    }
  });

  it("orders the finish as charge, titan, then beams two seconds apart", () => {
    const plan = finishPlan("standard");
    assert.equal(plan.mult, 1);
    assert.equal(plan.charge, 600);
    assert.equal(plan.gap, 2000);
    assert.ok(plan.titanFlight > 0, `titan flight ${plan.titanFlight}`);
    assert.ok(plan.total >= 7500 && plan.total <= 8500, `standard total ${plan.total}`);
    const quick = finishPlan("quick");
    assert.ok(quick.total >= 4800 && quick.total <= 5600, `quick total ${quick.total}`);
    assert.ok(quick.gap < plan.gap, "quick gaps shrink");
    const slow = finishPlan("slow");
    assert.ok(slow.total >= 13900 && slow.total <= 14900, `slow total ${slow.total}`);
    assert.ok(slow.gap > plan.gap, "slow gaps stretch");
    assert.deepEqual(finishPlan("bogus"), plan, "unknown pace falls back to standard");
    assert.equal(FINISH_PACES.standard, 1);
  });

  it("ends the card about a second after the explosion", () => {
    // The dissolve keeps its own timing and finishes behind the
    // card; the tail after the blast stays near one second.
    for (const pace of ["quick", "standard", "slow"]) {
      const plan = finishPlan(pace);
      const tail = plan.explodeDur + plan.cardDelay;
      const want = Math.round(1000 * (FINISH_PACES[pace] || 1));
      assert.ok(Math.abs(tail - want) <= 60, `${pace} tail ${tail}`);
    }
  });

  it("cuts the tail camera toward the winner", () => {
    const stage = {
      band: { x0: 100, x1: 900 },
      area: { top: 60, bottom: 660 },
      entities: {
        intrusion: { x: 250, y: 360, r: 80 },
        guardian: { x: 750, y: 360, r: 80 },
      },
    };
    const blue = winnerBeatTarget(stage, "blue");
    assert.equal(blue.z, 1);
    assert.ok(blue.dx < 0, "blue wins shift the frame left");
    assert.ok(Math.abs(blue.dx) <= 70, `beat drift ${blue.dx}`);
    const red = winnerBeatTarget(stage, "red");
    assert.ok(red.dx > 0, "red wins shift the frame right");
    assert.deepEqual(winnerBeatTarget(null, "blue"), { dx: 0, dy: 0, z: 1 });
  });

  it("keeps the flight lane peak above every endpoint", () => {
    for (const ends of [
      [{ x: 100, y: 400 }, { x: 900, y: 400 }],
      [{ x: 900, y: 380 }, { x: 100, y: 420 }],
      [{ x: 40, y: 200 }, { x: 120, y: 210 }],
    ]) {
      const lane = flightLane(ends[0], ends[1]);
      const mid = lanePoint(lane, 0.5).y;
      assert.ok(mid < ends[0].y && mid < ends[1].y, `peak clears: ${mid}`);
    }
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

describe("reduced-motion beat", () => {
  function fakeCanvas() {
    return {
      clientWidth: 800, clientHeight: 450, width: 0, height: 0,
      getContext: () => null,
    };
  }

  it("repaints on a slow beat with the clock frozen", () => {
    const realSetTimeout = globalThis.setTimeout;
    const realClearTimeout = globalThis.clearTimeout;
    const scheduled = [];
    let timerId = 0;
    globalThis.setTimeout = (fn, ms) => {
      timerId += 1;
      scheduled.push({ id: timerId, fn, ms });
      return timerId;
    };
    globalThis.clearTimeout = (id) => {
      const at = scheduled.findIndex((t) => t.id === id);
      if (at >= 0) scheduled.splice(at, 1);
    };
    try {
      const refs = initStage(fakeCanvas(), { reduced: true });
      setStageActive(refs, true);
      assert.equal(scheduled.length, 1, "one slow beat scheduled");
      assert.equal(scheduled[0].ms, 250, "about 4fps");
      const before = refs.fpsCount;
      scheduled[0].fn();
      assert.ok(refs.fpsCount > before, "the beat counts as a repaint");
      assert.equal(refs.clock, 0, "static: the clock never advances");
      setStageActive(refs, false);
      assert.equal(refs.slowTimer, 0, "deactivating clears the beat");
    } finally {
      globalThis.setTimeout = realSetTimeout;
      globalThis.clearTimeout = realClearTimeout;
    }
  });

  it("swaps between the full loop and the slow beat", () => {
    const realSetTimeout = globalThis.setTimeout;
    const scheduled = [];
    let timerId = 0;
    globalThis.setTimeout = (fn, ms) => {
      timerId += 1;
      scheduled.push({ id: timerId, fn, ms });
      return timerId;
    };
    try {
      const refs = initStage(fakeCanvas(), { reduced: false });
      setStageActive(refs, true);
      // No rAF in Node: the full loop falls back to 16ms timeouts.
      assert.equal(scheduled.length, 1, "full loop scheduled");
      assert.equal(scheduled[0].ms, 16);
      setStageReduced(refs, true);
      assert.equal(refs.reduced, true);
      assert.ok(refs.slowTimer, "slow beat scheduled after the swap");
      setStageReduced(refs, false);
      assert.equal(refs.reduced, false);
      assert.equal(refs.slowTimer, 0, "slow beat cleared after the swap back");
    } finally {
      globalThis.setTimeout = realSetTimeout;
    }
  });
});

describe("auditorChrome", () => {
  it("ignores the auditor once it docks outside the wrap", () => {
    // 1440x812: the wrap holds the canvas, the verdict card shows
    // in the right rail beside it, vertically overlapping but
    // horizontally clear. The canvas must ignore it entirely.
    const frame = { left: 0, top: 0, right: 860, bottom: 484, width: 860, height: 484 };
    const rail = { left: 876, top: 0, right: 1124, bottom: 484, width: 248, height: 484 };
    assert.equal(auditorChrome(frame, rail, true), null);
    assert.equal(auditorChrome(frame, rail, false), null);
    assert.equal(auditorChrome(frame, null, true), null);
    // A card overlapping the wrap still constrains the layout.
    const over = { left: 700, top: 130, right: 950, bottom: 350, width: 250, height: 220 };
    assert.equal(auditorChrome(frame, over, true), 350);
    assert.equal(auditorChrome(frame, over, false), null);
  });

  it("lays the blue lane from the top when no card overlaps", () => {
    const chrome = { hudBottom: 110, capTop: 420, audBottom: null };
    const stage = stageLayout({ w: 860, h: 484 }, arenaGeometry(classicLayout()), chrome);
    assert.equal(stage.lanes.blue.y, 110 + 10);
  });
});

describe("projectile bodies", () => {
  it("keeps small bodies above 28px at full stage scale", () => {
    const small = projBody("small", 1);
    assert.ok(small.lanceLen >= 28, String(small.lanceLen));
    assert.ok(small.orbD >= 28, String(small.orbD));
  });

  it("scales heavy bodies up from small", () => {
    const small = projBody("small", 1);
    const heavy = projBody("heavy", 1);
    const large = projBody("large", 1);
    assert.ok(heavy.lanceLen > small.lanceLen * 1.5, String(heavy.lanceLen));
    assert.ok(large.orbD > small.orbD * 1.5, String(large.orbD));
  });

  it("scales the titan to at least three heavy bodies", () => {
    const heavy = projBody("heavy", 1);
    const large = projBody("large", 1);
    const titan = projBody("titan", 1);
    assert.ok(titan.lanceLen >= 3 * heavy.lanceLen, String(titan.lanceLen));
    assert.ok(titan.orbD >= 3 * large.orbD, String(titan.orbD));
  });

  it("spans at least three body lengths of trail", () => {
    const laneLen = 600;
    for (const size of ["small", "standard", "heavy"]) {
      const body = projBody(size, 1);
      const span = trailSpan(body.lanceLen, laneLen);
      assert.ok(span * laneLen >= 3 * body.lanceLen - 1, `${size}: ${span}`);
      const orbSpan = trailSpan(body.orbD, laneLen);
      assert.ok(orbSpan * laneLen >= 3 * body.orbD - 1, `${size} orb: ${orbSpan}`);
    }
  });

  it("holds the impact flash through 250ms of the 350ms window", () => {
    assert.equal(flashHold(0), 1);
    assert.equal(flashHold(250 / 350), 1);
    assert.ok(flashHold(0.85) < 0.6, String(flashHold(0.85)));
    assert.equal(flashHold(1), 0);
  });
});

describe("move flow", () => {
  it("runs every move past the 1600ms replay beat", () => {
    for (const size of ["small", "standard", "heavy", "large"]) {
      assert.ok(moveSpanMs(size) > 1600, `${size}: ${moveSpanMs(size)}`);
    }
  });

  it("overlaps the next windup with the previous aftermath", () => {
    const fake = { clientWidth: 800, clientHeight: 450, width: 0, height: 0, getContext: () => null };
    const refs = initStage(fake, { reduced: false });
    const red = { id: "r0", actor: "red", tool: "scan_host", args: { host: "web" }, result: { ok: true } };
    const blue = { id: "b0", actor: "blue", tool: "read_logs", args: { host: "web" }, result: { ok: true, lines: [] } };
    renderStageStatic(refs, [red, blue], 1, { layout: null }, false);
    stageNotify(refs, red, {}, 1);
    assert.ok(refs.fx.some((fx) => fx.kind === "volley"), "scan launches a volley");
    refs.clock = 1600;
    renderStageStatic(refs, [red, blue], 2, { layout: null, keepFx: true }, false);
    assert.ok(refs.fx.some((fx) => fx.kind === "volley"), "volley aftermath survives the beat");
    assert.ok(!refs.fx.some((fx) => fx.kind === "fan"), "expired host marks drop");
    stageNotify(refs, blue, {}, 1);
    assert.ok(refs.fx.some((fx) => fx.kind === "orb"), "next windup starts over the aftermath");
  });

  it("clears every effect on scrub", () => {
    const fake = { clientWidth: 800, clientHeight: 450, width: 0, height: 0, getContext: () => null };
    const refs = initStage(fake, { reduced: false });
    const red = { id: "r0", actor: "red", tool: "scan_host", args: { host: "web" }, result: { ok: true } };
    renderStageStatic(refs, [red], 1, { layout: null }, false);
    stageNotify(refs, red, {}, 1);
    assert.ok(refs.fx.length > 0, "effects exist before the scrub");
    renderStageStatic(refs, [red], 1, { layout: null }, true);
    assert.equal(refs.fx.length, 0, "scrub rebuilds with no effects");
  });
});

describe("smoothness", () => {
  it("caps hit-stop at 3 frames standard and 5 heavy", () => {
    assert.equal(hitstopFor("small"), 0);
    assert.equal(hitstopFor("standard"), 3);
    assert.equal(hitstopFor("heavy"), 5);
    assert.equal(hitstopFor("large"), 5);
    assert.equal(hitstopFor("titan"), 6);
    assert.equal(hitstopFor("unknown"), 3);
  });

  it("ignores a lone slow spike when full detail", () => {
    let next = frameBudget(16, false, 100, 0);
    assert.equal(next.low, false);
    assert.equal(next.slow, 1);
    next = frameBudget(next.avg, next.low, 16, next.slow);
    assert.equal(next.low, false);
    assert.equal(next.slow, 0);
  });

  it("needs a full streak of slow frames to drop detail", () => {
    let avg = 16;
    let low = false;
    let slow = 0;
    for (let i = 0; i < LOW_STREAK - 1; i += 1) {
      const next = frameBudget(avg, low, 50, slow);
      avg = next.avg; low = next.low; slow = next.slow;
    }
    assert.equal(low, false);
    const last = frameBudget(avg, low, 50, slow);
    assert.equal(last.low, true);
  });
});

describe("clashCrop", () => {
  const red = { x: 110, y: 150, r: 45 };
  const blue = { x: 370, y: 150, r: 45 };

  it("holds both entities and the lane in 16:9 inside the frame", () => {
    const crop = clashCrop(red, blue, 480, 270);
    assert.ok(crop.sx >= 0 && crop.sy >= 0, JSON.stringify(crop));
    assert.ok(crop.sx + crop.sw <= 480 && crop.sy + crop.sh <= 270, JSON.stringify(crop));
    assert.ok(Math.abs(crop.sw / crop.sh - 16 / 9) < 0.05, JSON.stringify(crop));
    for (const p of [red, blue]) {
      assert.ok(p.x >= crop.sx && p.x <= crop.sx + crop.sw, "entity x inside");
      assert.ok(p.y >= crop.sy && p.y <= crop.sy + crop.sh, "entity y inside");
    }
    assert.ok(crop.sy < Math.min(red.y, blue.y) - 20, "lane apex has headroom");
    assert.ok(crop.sw < 480 && crop.sh < 270, "tighter than the full frame");
  });

  it("clamps to the frame when slots are missing", () => {
    const crop = clashCrop(null, null, 480, 270);
    assert.ok(crop.sx >= 0 && crop.sy >= 0, JSON.stringify(crop));
    assert.ok(crop.sx + crop.sw <= 480 && crop.sy + crop.sh <= 270, JSON.stringify(crop));
    assert.ok(Math.abs(crop.sw / crop.sh - 16 / 9) < 0.05, JSON.stringify(crop));
  });
});

describe("renderStageThumb", () => {
  function fakeCanvas() {
    return { clientWidth: 480, clientHeight: 270, width: 0, height: 0, getContext: () => null };
  }

  it("stages the poster event mid-flight and crops the clash", () => {
    const lane = [
      ev("r1", "red", "try_credential", { host: "web" }, { ok: true, host: "web", authenticated: true }),
      ev("b1", "blue", "isolate_host", { host: "web" }, { ok: true, host: "web" }),
    ];
    const refs = renderStageThumb(fakeCanvas(), lane, 2, { layout: classicLayout() });
    const kinds = refs.fx.map((f) => f.kind);
    assert.ok(kinds.includes("orb"), `poster orb staged: ${kinds}`);
    const orb = refs.fx.find((f) => f.kind === "orb");
    const age = refs.clock - orb.t0;
    assert.ok(age > 0 && age < orb.flight, `frozen mid-flight: age ${age}`);
    const crop = refs.thumbCrop;
    assert.ok(crop.sx >= 0 && crop.sy >= 0, JSON.stringify(crop));
    assert.ok(crop.sx + crop.sw <= 800 && crop.sy + crop.sh <= 450, JSON.stringify(crop));
    assert.ok(Math.abs(crop.sw / crop.sh - 16 / 9) < 0.05, JSON.stringify(crop));
    const ents = refs.stage.entities;
    assert.ok(ents.intrusion.r >= 30 && ents.guardian.r >= 30, "wide composition, not stacked specks");
  });

  it("renders a quiet still when the poster is not a move", () => {
    const lane = [ev("s1", "system", "clean_start_check", {}, { ok: true })];
    const refs = renderStageThumb(fakeCanvas(), lane, 1, { layout: classicLayout() });
    assert.equal(refs.fx.length, 0);
    assert.ok(refs.thumbCrop.sw > 0 && refs.thumbCrop.sh > 0);
  });
});

describe("sampleMeltMask", () => {
  // 4x4 sprite: opaque red corners and core, transparent elsewhere.
  function sprite() {
    const px = new Uint8ClampedArray(4 * 4 * 4);
    const dot = (x, y, r, g, b, a) => {
      const i = (y * 4 + x) * 4;
      px[i] = r; px[i + 1] = g; px[i + 2] = b; px[i + 3] = a;
    };
    dot(0, 0, 200, 20, 20, 255);
    dot(3, 0, 200, 20, 20, 255);
    dot(0, 3, 200, 20, 20, 255);
    dot(3, 3, 200, 20, 20, 255);
    dot(1, 1, 20, 20, 200, 255);
    dot(2, 2, 20, 20, 200, 10);
    return px;
  }

  it("keeps opaque pixels and drops transparent ones", () => {
    const slots = sampleMeltMask(sprite(), 4, [255, 110, 90], 90);
    assert.equal(slots.length, 5);
  });

  it("orders outer pixels first so the rim peels first", () => {
    const slots = sampleMeltMask(sprite(), 4, [255, 110, 90], 90);
    const rads = slots.map((s) => Math.hypot(s.x, s.y));
    for (let i = 1; i < rads.length; i += 1) {
      assert.ok(rads[i] <= rads[i - 1] + 1e-9, `slot ${i} inside slot ${i - 1}`);
    }
    assert.ok(rads[0] > rads[rads.length - 1], "rim leads the core");
  });

  it("tints white-hot toward the accent with a trace of the pixel", () => {
    const slots = sampleMeltMask(sprite(), 4, [0, 0, 0], 90);
    // 255 * 0.55 white plus 200 * 0.15 pixel, blended toward black.
    assert.ok(slots[0].fill.startsWith("rgb(170,143,143)"), slots[0].fill);
  });

  it("keeps bright pixels below the ceiling", () => {
    const slots = sampleMeltMask(sprite(), 4, [255, 255, 255], 90);
    for (const s of slots) {
      for (const v of s.fill.match(/\d+/g).map(Number)) {
        assert.ok(v >= 0 && v <= 255, s.fill);
      }
    }
  });

  it("caps the slot count", () => {
    const slots = sampleMeltMask(sprite(), 4, [255, 110, 90], 2);
    assert.equal(slots.length, 2);
  });

  it("survives empty input", () => {
    assert.deepEqual(sampleMeltMask(null, 4, [0, 0, 0], 90), []);
    assert.deepEqual(sampleMeltMask(new Uint8ClampedArray(0), 4, [0, 0, 0], 90), []);
  });
});

describe("melt timing", () => {
  it("holds the sprite full through the flare, above 70% through the peel", () => {
    assert.equal(meltSpriteAlpha(0), 1);
    assert.equal(meltSpriteAlpha(399), 1);
    assert.equal(meltSpriteAlpha(400), 1);
    assert.ok(meltSpriteAlpha(1200) > 0.7, String(meltSpriteAlpha(1200)));
    assert.equal(meltSpriteAlpha(2000), 0.7);
    assert.ok(meltSpriteAlpha(2200) < 0.7 && meltSpriteAlpha(2200) > 0);
    assert.equal(meltSpriteAlpha(2400), 0);
    assert.equal(meltSpriteAlpha(3000), 0);
    assert.equal(meltSpriteAlpha(-50), 1);
  });

  it("flips 0/1 every 120ms, phased per glyph", () => {
    assert.equal(meltChar(0, 0), "0");
    assert.equal(meltChar(0, 119), "0");
    assert.equal(meltChar(0, 120), "1");
    assert.equal(meltChar(1, 0), "1");
    assert.equal(meltChar(1, 120), "0");
    assert.ok(["0", "1"].includes(meltChar(7, 9999)));
  });

  it("strides LOW down to about 180 slots, full takes all", () => {
    assert.equal(meltStride(450, false), 1);
    assert.equal(meltStride(450, true), 3);
    assert.equal(meltStride(100, true), 1);
    assert.equal(meltStride(0, true), 1);
  });

  it("scales glyphs 16 to 26px with the rendered entity", () => {
    assert.equal(meltGlyphPx(82), 16);
    assert.equal(meltGlyphPx(150), 24);
    assert.equal(meltGlyphPx(300), 26);
    assert.equal(meltGlyphPx(8), 16);
  });

  it("ramps glyphs in fast and keeps late glyphs readable", () => {
    assert.equal(meltGlyphAlpha(0), 0);
    assert.ok(meltGlyphAlpha(0.25) > 0.5, String(meltGlyphAlpha(0.25)));
    // Soft falloff: at 90% of life the fill still reads.
    assert.ok(meltGlyphAlpha(0.9) > 0.2, String(meltGlyphAlpha(0.9)));
    assert.equal(meltGlyphAlpha(1), 0);
  });

  it("keeps the halo under the fill and fades it with life", () => {
    assert.ok(meltHaloAlpha(0) > meltGlyphAlpha(0));
    assert.ok(meltHaloAlpha(0.5) < meltGlyphAlpha(0.5));
    assert.ok(meltHaloAlpha(0.9) > 0.1, String(meltHaloAlpha(0.9)));
    assert.equal(meltHaloAlpha(1), 0);
  });

  it("spreads the cloud around the sprite instead of only upward", () => {
    const still = meltDrift(3, 0, 82, 0.5, 0.5);
    assert.deepEqual(still, { dx: 0, dy: 0 });
    // Bottom-rim glyphs drift down (positive canvas y), top ones rise.
    assert.ok(meltDrift(3, 1, 82, 0, 1).dy > 0);
    assert.ok(meltDrift(3, 1, 82, 0, -1).dy < 0);
    // Sideways shuffle is deterministic and moves glyphs apart.
    const a = meltDrift(3, 1, 82, 0, 0);
    const b = meltDrift(4, 1, 82, 0, 0);
    assert.equal(a.dx, meltDrift(3, 1, 82, 0, 0).dx);
    assert.ok(Math.abs(a.dx - b.dx) > 1, `${a.dx} vs ${b.dx}`);
  });
});
