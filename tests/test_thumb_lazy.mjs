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
    removeChild(kid) {
      const at = this.children.indexOf(kid);
      if (at >= 0) this.children.splice(at, 1);
      kid.parentNode = null;
      return kid;
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

// Controllable visibility: observers register here, tests fire
// enter/exit per target to simulate scrolling.
const ioWatchers = new Map();

function installIO() {
  ioWatchers.clear();
  globalThis.IntersectionObserver = class {
    constructor(cb) {
      this.cb = cb;
    }
    observe(target) {
      let list = ioWatchers.get(target);
      if (!list) {
        list = [];
        ioWatchers.set(target, list);
      }
      list.push(this);
    }
    unobserve(target) {
      const list = ioWatchers.get(target) || [];
      ioWatchers.set(target, list.filter((o) => o !== this));
    }
    disconnect() {
      for (const [target, list] of ioWatchers) {
        ioWatchers.set(target, list.filter((o) => o !== this));
      }
    }
  };
}

function fireIO(target, visible) {
  const list = ioWatchers.get(target) || [];
  for (const o of [...list]) o.cb([{ isIntersecting: visible, target }]);
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
    assert.equal(stats.getContext, 0, "no paint context touched during mount");
    assert.equal(thumbPendingCount(), 100, "every card queues for idle");
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
    let pending = thumbPendingCount();
    assert.equal(pending, 100);
    const slices = [];
    while (scheduled.length > 0) {
      const run = scheduled.shift();
      const t0 = performance.now();
      run();
      slices.push(performance.now() - t0);
      const next = thumbPendingCount();
      assert.equal(next, pending - 1, "one card per slice");
      pending = next;
    }
    assert.equal(pending, 0);
    for (const ms of slices) assert.ok(ms < 50, `slice took ${ms.toFixed(1)}ms`);
    assert.equal(thumbCacheSize(), BITMAP_CACHE_MAX, "memory holds the last 60");
    assert.equal(attachedCanvases(featuredRoot, archiveRoot), 100, "no observer, every row stays mounted");
    assert.equal(thumbCanvasCount(), 2, "one shared render canvas plus one snap canvas");
  });

  it("renders archive rows only while on screen", () => {
    installIO();
    const cards = [];
    for (let i = 0; i < 20; i += 1) cards.push(card(i));
    const featuredRoot = makeEl("div");
    const archiveRoot = makeEl("div");
    renderLobby(featuredRoot, archiveRoot, makeEl("span"), {
      cards,
      featuredIds: cards.slice(0, 2).map((c) => c.id),
      loading: false,
    }, { onPlay: () => {} }, { art: {}, attackMap: null });
    assert.equal(attachedCanvases(featuredRoot, archiveRoot), 2, "off-screen rows hold the glyph only");
    assert.equal(thumbPendingCount(), 2, "featured jobs queue unseen");
    // Hover mounts the canvas early, but nothing renders until
    // the row itself is on screen.
    const row = archiveRoot.children[0];
    row._listeners.mouseenter[0]();
    const findCanvas = (root) => {
      const walk = (el) => {
        if (el.tagName === "CANVAS") return el;
        for (const kid of el.children || []) {
          const hit = walk(kid);
          if (hit) return hit;
        }
        return null;
      };
      return walk(root);
    };
    const early = findCanvas(row);
    assert.ok(early, "hover mounts the canvas");
    assert.equal(scheduled.length, 0, "no render queued off screen");
    fireIO(row, true);
    assert.equal(findCanvas(row), early, "entry does not remount");
    fireIO(early, true);
    assert.equal(scheduled.length, 1, "entry queues one render");
    drainAll(scheduled);
    assert.equal(thumbPendingCount(), 2, "featured jobs stay queued unseen");
    assert.equal(thumbCacheSize(), 1);
    // Scrolling away releases the canvas; coming back blits the
    // cached bitmap without queueing a render.
    fireIO(row, false);
    assert.equal(findCanvas(row), null, "exit releases the canvas");
    assert.equal(attachedCanvases(featuredRoot, archiveRoot), 2);
    fireIO(row, true);
    assert.ok(findCanvas(row), "re-entry remounts");
    assert.equal(scheduled.length, 0, "memory hit renders nothing");
    assert.equal(thumbPendingCount(), 2);
  });

  it("releases canvases for rows scrolled off screen", () => {
    installIO();
    const cards = [];
    for (let i = 0; i < 100; i += 1) cards.push(card(i));
    const featuredRoot = makeEl("div");
    const archiveRoot = makeEl("div");
    renderLobby(featuredRoot, archiveRoot, makeEl("span"), {
      cards,
      featuredIds: cards.slice(0, 6).map((c) => c.id),
      loading: false,
    }, { onPlay: () => {} }, { art: {}, attackMap: null });
    const rows = archiveRoot.children.slice(0, 8);
    const canvases = [];
    for (const row of rows) {
      fireIO(row, true);
      const walk = (el) => {
        if (el.tagName === "CANVAS") return el;
        for (const kid of el.children || []) {
          const hit = walk(kid);
          if (hit) return hit;
        }
        return null;
      };
      const canvas = walk(row);
      canvases.push(canvas);
      fireIO(canvas, true);
    }
    const slices = drainAll(scheduled);
    for (const ms of slices) assert.ok(ms < 50, `slice took ${ms.toFixed(1)}ms`);
    assert.equal(thumbCacheSize(), 8);
    assert.equal(attachedCanvases(featuredRoot, archiveRoot), 14);
    for (const row of rows.slice(0, 7)) fireIO(row, false);
    assert.equal(attachedCanvases(featuredRoot, archiveRoot), 7, "exits release all but one");
    assert.equal(thumbCacheSize(), 8, "bitmaps stay cached while canvases go");
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

  it("keys cursor frames separately from the poster", () => {
    const model = card(0);
    mountArenaThumb(makeEl("canvas"), model, {}, {});
    mountArenaThumb(makeEl("canvas"), model, {}, { cursor: 2 });
    mountArenaThumb(makeEl("canvas"), model, {}, { cursor: 2 });
    mountArenaThumb(makeEl("canvas"), model, {}, { cursor: 4 });
    assert.equal(thumbPendingCount(), 3, "poster plus one job per cursor");
    drainAll(scheduled);
    assert.equal(thumbPendingCount(), 0);
    assert.equal(thumbCacheSize(), 3);
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
    assert.equal(BITMAP_CACHE_MAX, 60);
    for (let i = 0; i < 70; i += 1) {
      mountArenaThumb(makeEl("canvas"), card(i), {}, {});
    }
    assert.equal(thumbPendingCount(), 70);
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
    mountArenaThumb(makeEl("canvas"), card(1), {}, {});
    drainAll(scheduled);
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(store.size, 2, "featured and archive renders persist");
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

  it("survives sessionStorage quota failures", () => {
    globalThis.sessionStorage = {
      getItem: () => null,
      setItem: () => {
        throw new Error("quota exceeded");
      },
    };
    mountArenaThumb(makeEl("canvas"), card(0), {}, {});
    drainAll(scheduled);
    assert.equal(thumbPendingCount(), 0);
    assert.equal(thumbCacheSize(), 1, "memory holds what the session refused");
  });
});
