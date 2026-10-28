// Lazy lobby thumbnails: mounting a full shelf must not paint,
// renders drain one card per idle slice, and live canvases stay
// capped. Uses a minimal DOM stub; pixels stay in the browser.
import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { classicLayout } from "../dashboard/js/layout.js";
import { renderLobby } from "../dashboard/js/lobby.js";
import {
  BITMAP_CACHE_MAX, clearArenaThumbCache, mountArenaThumb,
  setThumbScheduler, thumbCacheSize, thumbCanvasCount, thumbPendingCount, thumbnailState,
} from "../dashboard/js/thumbnail.js";

const realDocument = globalThis.document;
const realSessionStorage = globalThis.sessionStorage;
const realImage = globalThis.Image;
const realIO = globalThis.IntersectionObserver;

const stats = { canvas: 0, getContext: 0 };

function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    children: [],
    parentNode: null,
    style: {},
    dataset: {},
    className: "",
    id: "",
    title: "",
    textContent: "",
    _html: "",
    _listeners: {},
    append(...kids) {
      for (const kid of kids) {
        if (kid === null || kid === undefined) continue;
        this.children.push(kid);
        kid.parentNode = this;
      }
      return kids[kids.length - 1];
    },
    appendChild(kid) {
      this.children.push(kid);
      kid.parentNode = this;
      return kid;
    },
    replaceChild(next, prev) {
      const at = this.children.indexOf(prev);
      if (at >= 0) this.children[at] = next;
      next.parentNode = this;
      return prev;
    },
    setAttribute(key, value) {
      this[key] = value;
    },
    addEventListener(type, fn) {
      (this._listeners[type] = this._listeners[type] || []).push(fn);
    },
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return this._html; },
    set(value) {
      this._html = value;
      this.children = [];
    },
  });
  el.classList = {
    add: (...names) => {
      const have = new Set(el.className.split(" ").filter(Boolean));
      for (const name of names) have.add(name);
      el.className = [...have].join(" ");
    },
  };
  if (tag === "canvas") {
    stats.canvas += 1;
    el.width = 0;
    el.height = 0;
    el.getContext = () => {
      stats.getContext += 1;
      return null;
    };
    el.toDataURL = () => "data:image/png;stub-thumb";
  }
  return el;
}

function installDom() {
  stats.canvas = 0;
  stats.getContext = 0;
  globalThis.document = {
    createElement: (tag) => makeEl(tag),
    createTextNode: (text) => ({ textContent: text }),
    getElementById: () => null,
  };
  delete globalThis.IntersectionObserver;
  delete globalThis.sessionStorage;
  delete globalThis.Image;
}

function restoreDom() {
  if (realDocument === undefined) delete globalThis.document;
  else globalThis.document = realDocument;
  if (realSessionStorage === undefined) delete globalThis.sessionStorage;
  else globalThis.sessionStorage = realSessionStorage;
  if (realImage === undefined) delete globalThis.Image;
  else globalThis.Image = realImage;
  if (realIO === undefined) delete globalThis.IntersectionObserver;
  else globalThis.IntersectionObserver = realIO;
}

function ev(id, actor, tool, args, result) {
  return { id, actor, tool, args: args || {}, result: result || { ok: true } };
}

const SCAN = (host) => ev(`q-${host}`, "red", "scan_host", { host }, { ok: true, host });
const AUTH = (host) => ev(`a-${host}`, "red", "try_credential", { host },
  { ok: true, host, authenticated: true });
const ISOLATE = (host) => ev(`c-${host}`, "blue", "isolate_host", { host }, { ok: true, host });

const LANES = [
  [SCAN("web"), AUTH("app"), ISOLATE("app")],
  [SCAN("web"), AUTH("db")],
  [SCAN("web")],
];

const OUTCOMES = [
  { key: "contained", label: "Contained", tone: "blue" },
  { key: "secret-exposed", label: "Secret exposed", tone: "red" },
  { key: "no-intrusion", label: "No intrusion", tone: "gray" },
];

function card(i) {
  const lane = LANES[i % LANES.length];
  const out = OUTCOMES[i % OUTCOMES.length];
  const layout = classicLayout();
  return {
    id: `run-${i}`,
    outcomeKey: out.key,
    outcome: { label: out.label, tone: out.tone },
    blueScore: 80,
    moves: lane.length,
    story: "A synthetic exercise.",
    duration: "3 moves",
    scenario: "classic",
    arm: null,
    blocks: {},
    tstate: thumbnailState([], layout),
    flags: [],
    layout,
    weights: {},
    events: lane,
  };
}

function drainAll(scheduled, budgetMs) {
  const slices = [];
  while (scheduled.length > 0) {
    const run = scheduled.shift();
    const t0 = performance.now();
    run();
    slices.push(performance.now() - t0);
  }
  return slices;
}

// Canvases attached under the roots: transient render garbage is
// detached and GC-able, so only attached ones count as alive.
function attachedCanvases(...roots) {
  let count = 0;
  const walk = (el) => {
    if (!el || !el.children) return;
    for (const kid of el.children) {
      if (kid.tagName === "CANVAS") count += 1;
      walk(kid);
    }
  };
  for (const root of roots) walk(root);
  return count;
}

describe("lazy lobby thumbnails", () => {
  let scheduled;

  beforeEach(() => {
    installDom();
    clearArenaThumbCache();
    scheduled = [];
    setThumbScheduler((run) => scheduled.push(run));
  });

  afterEach(() => {
    setThumbScheduler(null);
    clearArenaThumbCache();
    restoreDom();
  });

  it("mounts 100 cards with no synchronous render", () => {
    const cards = [];
    for (let i = 0; i < 100; i += 1) cards.push(card(i));
    const featuredRoot = makeEl("div");
    const archiveRoot = makeEl("div");
    const archiveCount = makeEl("span");
    const t0 = performance.now();
    renderLobby(featuredRoot, archiveRoot, archiveCount, {
      cards,
      featuredIds: cards.slice(0, 6).map((c) => c.id),
      loading: false,
    }, { onPlay: () => {} }, { art: {}, attackMap: null });
    const syncMs = performance.now() - t0;
    assert.ok(syncMs < 50, `sync mount took ${syncMs.toFixed(1)}ms`);
    assert.equal(stats.canvas, 6, "only featured cards own canvases");
    assert.equal(stats.getContext, 0, "no paint context touched during mount");
    assert.equal(thumbPendingCount(), 6, "featured frames queue for idle");
    assert.equal(archiveRoot.children.length, 94, "archive still lists every run");
  });

  it("drains one card per slice inside the task budget", () => {
    const cards = [];
    for (let i = 0; i < 100; i += 1) cards.push(card(i));
    const featuredRoot = makeEl("div");
    const archiveRoot = makeEl("div");
    renderLobby(featuredRoot, archiveRoot, makeEl("span"), {
      cards,
      featuredIds: cards.slice(0, 6).map((c) => c.id),
      loading: false,
    }, { onPlay: () => {} }, { art: {}, attackMap: null });
    assert.equal(scheduled.length, 1, "one idle callback queued");
    const seen = new Set([thumbPendingCount()]);
    const slices = [];
    while (scheduled.length > 0) {
      const run = scheduled.shift();
      const t0 = performance.now();
      run();
      slices.push(performance.now() - t0);
      seen.add(thumbPendingCount());
    }
    assert.deepEqual([...seen].sort((a, b) => a - b), [0, 1, 2, 3, 4, 5, 6]);
    for (const ms of slices) assert.ok(ms < 50, `slice took ${ms.toFixed(1)}ms`);
    assert.equal(thumbCacheSize(), 6);
    assert.equal(attachedCanvases(featuredRoot, archiveRoot), 6);
    assert.ok(6 + thumbCanvasCount() < 10, "attached plus scheduler canvases stay capped");
  });

  it("upgrades one archive row on hover without touching the rest", () => {
    const cards = [];
    for (let i = 0; i < 100; i += 1) cards.push(card(i));
    const featuredRoot = makeEl("div");
    const archiveRoot = makeEl("div");
    renderLobby(featuredRoot, archiveRoot, makeEl("span"), {
      cards,
      featuredIds: cards.slice(0, 6).map((c) => c.id),
      loading: false,
    }, { onPlay: () => {} }, { art: {}, attackMap: null });
    assert.equal(stats.canvas, 6);
    const row = archiveRoot.children[0];
    assert.ok(row._listeners.mouseenter, "row arms hover");
    row._listeners.mouseenter[0]();
    assert.equal(stats.canvas, 7, "hover mounts exactly one canvas");
    assert.equal(thumbPendingCount(), 7);
    row._listeners.mouseenter[0]();
    assert.equal(stats.canvas, 7, "second hover does not remount");
    drainAll(scheduled);
    assert.equal(thumbPendingCount(), 0);
    const alive = attachedCanvases(featuredRoot, archiveRoot) + thumbCanvasCount();
    assert.ok(alive < 10, `${alive} canvases alive`);
  });

  it("dedupes re-mounts of the same run into one job", () => {
    const first = makeEl("canvas");
    const second = makeEl("canvas");
    mountArenaThumb(first, card(0), {}, {});
    mountArenaThumb(second, card(0), {}, {});
    assert.equal(thumbPendingCount(), 1);
    drainAll(scheduled);
    assert.equal(thumbPendingCount(), 0);
    assert.equal(thumbCacheSize(), 1);
  });

  it("paints detached cards on memory hits", () => {
    globalThis.Image = class {
      set src(value) {
        this._src = value;
        if (this.onload) this.onload();
      }
    };
    const model = card(0);
    mountArenaThumb(makeEl("canvas"), model, {}, {});
    drainAll(scheduled);
    assert.equal(thumbCacheSize(), 1);
    // Cards mount detached (the article attaches later); a memory
    // hit must still paint synchronously, or re-renders go blank.
    const canvas = makeEl("canvas");
    canvas.isConnected = false;
    let draws = 0;
    canvas.getContext = () => ({ drawImage: () => { draws += 1; } });
    mountArenaThumb(canvas, model, {}, {});
    assert.equal(draws, 1, "memory hit paints the detached canvas");
  });

  it("prunes detached targets so the live canvas gets its blit", () => {
    const model = card(0);
    for (let i = 0; i < 5; i += 1) {
      const stale = makeEl("canvas");
      mountArenaThumb(stale, model, {}, {});
      stale.isConnected = false;
    }
    const live = makeEl("canvas");
    live.isConnected = true;
    mountArenaThumb(live, model, {}, {});
    assert.equal(thumbPendingCount(), 1);
    drainAll(scheduled);
    assert.equal(live._thumbKey, "", "live canvas blitted and released");
  });

  it("caps the bitmap cache with LRU eviction", () => {
    for (let i = 0; i < 30; i += 1) {
      mountArenaThumb(makeEl("canvas"), card(i), {}, {});
    }
    assert.equal(thumbPendingCount(), 30);
    drainAll(scheduled);
    assert.equal(thumbPendingCount(), 0);
    assert.equal(thumbCacheSize(), BITMAP_CACHE_MAX);
  });

  it("persists featured frames in sessionStorage and reuses them", async () => {
    const store = new Map();
    globalThis.sessionStorage = {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, value),
    };
    let decoded = 0;
    globalThis.Image = class {
      set src(value) {
        this._src = value;
        setTimeout(() => {
          decoded += 1;
          if (this.onload) this.onload();
        }, 0);
      }
    };
    const model = card(0);
    mountArenaThumb(makeEl("canvas"), model, {}, { featured: true });
    drainAll(scheduled);
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(store.size, 1, "featured render writes one session entry");
    const [[key, raw]] = [...store.entries()];
    assert.ok(key.includes(model.id), key);
    assert.equal(JSON.parse(raw).moves, model.moves);
    // A reload starts with cold memory: the session entry must win
    // without queueing a render.
    clearArenaThumbCache();
    scheduled.length = 0;
    decoded = 0;
    mountArenaThumb(makeEl("canvas"), model, {}, { featured: true });
    assert.equal(thumbPendingCount(), 0, "session hit skips the queue");
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(decoded, 1);
    assert.equal(thumbCacheSize(), 1);
  });
});
