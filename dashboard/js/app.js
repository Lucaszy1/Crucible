// Operations Center bootstrap: lobby, arena show flow, tabs, themes.
import { fetchAttackMap, fetchCampaign, fetchCampaigns, fetchConfig, fetchFeatured, fetchPreview, fetchRun, fetchRuns, fetchStatus, openLive, startExercise } from "./api.js";
import { fillCampaignSelects, renderLearn } from "./learn.js";
import {
  autoplayQueue, displayOutcome, endCardStats, eventHost, motionReduced, outcomeLine,
  rankFeatured, reduceHostStates, runFlags, scoreEvents,
} from "./state.js";
import { describeEvent, statusSentence } from "./story.js";
import { initMap, updateMap } from "./network.js";
import { renderFeed } from "./feed.js";
import { renderStrip, renderTransport, stripModel } from "./timeline.js";
import { renderTechniques } from "./techniques.js";
import { reduceAudits, renderAuditor } from "./audit.js";
import { renderScoreboard, renderScoreDetails } from "./scoreboard.js";
import { focusEvidenceRef, renderEvidence } from "./evidence.js";
import { initControls, serverStatus, setServer, showConfirm } from "./controls.js";
import {
  HELDOUT_MAX, HELDOUT_MIN, drawLiveScenario, formatDuration, hideDeal, partitionLobby,
  renderDeal, renderLobby, storyLine,
} from "./lobby.js";
import { playOpening } from "./opening.js";
import { initThoughtHeads, renderThoughts, setThoughtsCollapsed } from "./thoughts.js";
import { endCardVisible, hideEndCard, initEndCard, setEndNext, showEndCard } from "./endcard.js";
import { thumbnailState } from "./thumbnail.js";
import {
  arenaNotify, cancelArena, firstAccessStinger, flashArenaHost, glitchStinger,
  initArena, renderArenaStatic, setArenaActive, setArenaLayout, toggleArenaSound,
} from "./arena.js";
import {
  classicLayout, layoutFromEvents, layoutHosts, layoutWeights, scenarioMeta, scenarioText,
} from "./layout.js";

const REPLAY_INTERVAL_MS = 1200;
const LOBBY_FETCH_POOL = 6;
const ADVANCE_SECONDS = 8;

const els = {
  mode: document.getElementById("modeBadge"),
  title: document.getElementById("exerciseTitle"),
  eventCount: document.getElementById("eventCount"),
  status: document.getElementById("statusLine"),
  notice: document.getElementById("notice"),
  map: document.getElementById("mapSvg"),
  stages: document.getElementById("stageStrip"),
  redTech: document.getElementById("redTechList"),
  blueTech: document.getElementById("blueLabelList"),
  feed: document.getElementById("feedList"),
  presentLatest: document.getElementById("presentLatest"),
  play: document.getElementById("playBtn"),
  pause: document.getElementById("pauseBtn"),
  scrubber: document.getElementById("scrubber"),
  position: document.getElementById("positionLabel"),
  banner: document.getElementById("finalBanner"),
  outcome: document.getElementById("sbOutcome"),
  detect: document.getElementById("sbDetect"),
  contain: document.getElementById("sbContain"),
  downtime: document.getElementById("sbDowntime"),
  blueScore: document.getElementById("sbBlue"),
  costLabel: document.getElementById("costLabel"),
  costBar: document.getElementById("costBar"),
  exerciseCost: document.getElementById("exerciseCost"),
  chips: document.getElementById("evidenceChips"),
  tabs: document.getElementById("logTabs"),
  viewer: document.getElementById("logViewer"),
  auditList: document.getElementById("auditList"),
  auditChips: document.getElementById("auditChips"),
  auditCost: document.getElementById("auditCost"),
  auditSummary: document.getElementById("auditSummary"),
  tabLobby: document.getElementById("tabLobby"),
  tabArena: document.getElementById("tabArena"),
  tabStory: document.getElementById("tabStory"),
  tabDetails: document.getElementById("tabDetails"),
  tabLearn: document.getElementById("tabLearn"),
  lobbyView: document.getElementById("lobbyView"),
  featuredGrid: document.getElementById("featuredGrid"),
  archiveGrid: document.getElementById("archiveGrid"),
  archiveCount: document.getElementById("archiveCount"),
  autoplayBox: document.getElementById("autoplayBox"),
  liveBtn: document.getElementById("liveBtn"),
  replayBtn: document.getElementById("replayBtn"),
  seedInput: document.getElementById("seedInput"),
  dealDifficulty: document.getElementById("dealDifficulty"),
  dealSlot: document.getElementById("dealSlot"),
  arenaView: document.getElementById("arenaView"),
  arenaCanvas: document.getElementById("arenaCanvas"),
  arenaOutcome: document.getElementById("arenaOutcome"),
  arenaBlue: document.getElementById("arenaBlue"),
  arenaDowntime: document.getElementById("arenaDowntime"),
  arenaPlay: document.getElementById("arenaPlay"),
  arenaPause: document.getElementById("arenaPause"),
  arenaScrubber: document.getElementById("arenaScrubber"),
  arenaPosition: document.getElementById("arenaPosition"),
  soundBtn: document.getElementById("soundBtn"),
  moveStrip: document.getElementById("moveStrip"),
  slate: document.getElementById("slate"),
  slateTitle: document.getElementById("slateTitle"),
  slateHosts: document.getElementById("slateHosts"),
  slateCount: document.getElementById("slateCount"),
  thoughtRed: document.getElementById("thoughtRed"),
  thoughtRedHead: document.getElementById("thoughtRedHead"),
  thoughtRedText: document.getElementById("thoughtRedText"),
  thoughtRedChips: document.getElementById("thoughtRedChips"),
  thoughtBlue: document.getElementById("thoughtBlue"),
  thoughtBlueHead: document.getElementById("thoughtBlueHead"),
  thoughtBlueText: document.getElementById("thoughtBlueText"),
  thoughtBlueChips: document.getElementById("thoughtBlueChips"),
  endCard: document.getElementById("endCard"),
  endEyebrow: document.getElementById("endEyebrow"),
  endTitle: document.getElementById("endTitle"),
  endScore: document.getElementById("endScore"),
  endStats: document.getElementById("endStats"),
  endLine: document.getElementById("endLine"),
  endNext: document.getElementById("endNext"),
  endReplay: document.getElementById("endReplay"),
  endNextBtn: document.getElementById("endNextBtn"),
  endLobbyBtn: document.getElementById("endLobbyBtn"),
  storyView: document.getElementById("storyView"),
  detailsView: document.getElementById("detailsView"),
  learnView: document.getElementById("learnView"),
  memSelect: document.getElementById("memSelect"),
  ctlSelect: document.getElementById("ctlSelect"),
  learnSvg: document.getElementById("learnSvg"),
  legendMem: document.getElementById("legendMem"),
  legendCtl: document.getElementById("legendCtl"),
  playBody: document.getElementById("playBody"),
  playEmpty: document.getElementById("playEmpty"),
  themeBtn: document.getElementById("themeBtn"),
  presentBtn: document.getElementById("presentBtn"),
  settingsBtn: document.getElementById("settingsBtn"),
  settingsPop: document.getElementById("settingsPop"),
};

const controls = {
  agents: document.getElementById("agentsSelect"),
  run: document.getElementById("runBtn"),
  speedSeg: document.getElementById("speedSeg"),
  light: document.getElementById("serverLight"),
  label: document.getElementById("serverLabel"),
};

const confirm = {
  overlay: document.getElementById("confirmOverlay"),
  cost: document.getElementById("confirmCost"),
  spent: document.getElementById("confirmSpent"),
  limit: document.getElementById("confirmLimit"),
  warn: document.getElementById("confirmWarn"),
  ok: document.getElementById("confirmOk"),
  cancel: document.getElementById("confirmCancel"),
};

const store = {
  mode: "idle", // idle | live | replay
  runId: null,
  events: [],
  cursor: 0,
  playing: false,
  speed: 1,
  timer: null,
  weights: { web: 3, app: 3, db: 5, workstation: 1 },
  layout: null,
  scenario: "classic",
  attackMap: null,
  spend: null,
  finalScore: null,
  label: "No exercise loaded",
  liveSource: null,
  liveExerciseId: null,
  arenaCursor: 0,
  firstAuthIndex: null,
  firstAccessShown: false,
  opening: null,
  advance: null,
  presentLoop: false,
  thoughtState: null,
  lobby: { runs: [], cards: [], featuredIds: [], autoplay: false, armByEpisode: new Map(), loading: true },
};

function thoughtPanels() {
  return {
    red: { root: els.thoughtRed, head: els.thoughtRedHead, text: els.thoughtRedText, chips: els.thoughtRedChips },
    blue: { root: els.thoughtBlue, head: els.thoughtBlueHead, text: els.thoughtBlueText, chips: els.thoughtBlueChips },
  };
}

function endEls() {
  return {
    root: els.endCard, eyebrow: els.endEyebrow, title: els.endTitle, score: els.endScore,
    stats: els.endStats, line: els.endLine, nextUp: els.endNext,
    replay: els.endReplay, next: els.endNextBtn, lobby: els.endLobbyBtn,
  };
}

function reducedMotion() {
  return motionReduced();
}

let mapRefs = null;
let arenaRefs = null;

function arenaScoreEls() {
  return { outcome: els.arenaOutcome, blue: els.arenaBlue, downtime: els.arenaDowntime };
}

function arenaTransportEls() {
  return { scrubber: els.arenaScrubber, position: els.arenaPosition, play: els.arenaPlay, pause: els.arenaPause };
}

function notice(text, isError) {
  if (!text) {
    els.notice.hidden = true;
    els.notice.textContent = "";
    return;
  }
  els.notice.hidden = false;
  els.notice.textContent = text;
  els.notice.classList.toggle("error", Boolean(isError));
}

function setMode(mode, label) {
  store.mode = mode;
  els.mode.dataset.mode = mode === "live" ? "live" : mode === "replay" ? "replay" : mode === "booting" ? "booting" : "idle";
  els.mode.textContent = mode === "live" ? "Live" : mode === "replay" ? "Replay" : mode === "booting" ? "Starting" : "Idle";
  if (label) store.label = label;
  els.title.textContent = store.label;
}

function latestLaneEvent(slice) {
  for (let i = slice.length - 1; i >= 0; i -= 1) {
    if (slice[i].actor === "red" || slice[i].actor === "blue") return slice[i];
  }
  return null;
}

function onEvidenceRef(ref) {
  focusEvidenceRef(ref);
  renderEvidence({ chips: els.chips, tabs: els.tabs, viewer: els.viewer }, store.events, store.cursor,
    layoutHosts(store.layout || classicLayout()));
  switchTab("details");
}

// Apply one exercise's range layout: arena geometry, map, weights,
// and the scenario label. Old logs without a layout render the
// classic range exactly as before.
function applyLayout(layout, meta) {
  store.layout = layout && layout.hosts ? layout : classicLayout();
  store.scenario = scenarioText(meta || { id: "classic" });
  store.weights = { ...layoutWeights(store.layout) };
  if (arenaRefs) setArenaLayout(arenaRefs, store.layout);
  mapRefs = initMap(els.map, store.weights, store.layout);
}

function scoreEls() {
  return {
    outcome: els.outcome, detect: els.detect, contain: els.contain, downtime: els.downtime,
    blue: els.blueScore, banner: els.banner, costLabel: els.costLabel,
    costBar: els.costBar, exerciseCost: els.exerciseCost,
  };
}

function renderPresent(slice) {
  els.presentLatest.innerHTML = "";
  const lane = slice.filter((ev) => ev.actor === "red" || ev.actor === "blue").slice(-2);
  for (const ev of lane) {
    const item = document.createElement("li");
    item.textContent = describeEvent(ev);
    els.presentLatest.append(item);
  }
}

function render(animate) {
  const slice = store.events.slice(0, store.cursor);
  const layout = store.layout || classicLayout();
  const hosts = layoutHosts(layout);
  const states = reduceHostStates(slice, hosts);
  updateMap(mapRefs, states, slice, animate);
  const display = store.events.length > 0 ? displayOutcome(store.events) : null;
  const arenaStates = renderArenaStatic(
    arenaRefs, store.events, store.cursor,
    {
      weights: store.weights, finalScore: store.finalScore, label: store.label,
      display: display ? { label: display.label, line: outcomeLine(display.key) } : null,
      layout,
    },
    !animate,
  );
  if (animate && store.cursor > 0) {
    const ev = store.events[store.cursor - 1];
    if (ev && store.arenaCursor !== store.cursor) {
      store.arenaCursor = store.cursor;
      arenaNotify(arenaRefs, ev, arenaStates.states, store.speed);
      if (!store.firstAccessShown && store.firstAuthIndex !== null &&
        store.cursor - 1 === store.firstAuthIndex) {
        store.firstAccessShown = true;
        firstAccessStinger(arenaRefs, eventHost(ev));
      }
    }
  } else {
    store.arenaCursor = store.cursor;
  }
  const audits = reduceAudits(store.events, store.cursor);
  renderFeed(els.feed, store.events, store.cursor, store.attackMap, onEvidenceRef, audits.byTarget, {
    autoscroll: store.mode === "live",
  });
  renderPresent(slice);
  els.status.textContent = statusSentence({
    latest: latestLaneEvent(slice),
    ended: Boolean(store.finalScore),
    outcome: store.finalScore?.outcome,
    display: store.finalScore && display ? display.label : null,
    mode: store.mode,
  });
  renderAuditor(
    { list: els.auditList, chips: els.auditChips, cost: els.auditCost, count: null, summary: els.auditSummary },
    store.events, store.cursor, audits, onEvidenceRef,
  );
  const canScrub = store.mode === "replay";
  renderTransport(
    { scrubber: els.scrubber, position: els.position, play: els.play, pause: els.pause },
    store.cursor, store.events.length, store.playing, canScrub,
  );
  renderTransport(arenaTransportEls(), store.cursor, store.events.length, store.playing, canScrub);
  renderStrip(els.moveStrip, stripModel(store.events, store.cursor, audits.byTarget),
    (pos) => jump(pos), store.mode !== "replay");
  store.thoughtState = renderThoughts(thoughtPanels(), slice, {
    reduced: reducedMotion(),
    hosts,
    onFlash: (host) => flashArenaHost(arenaRefs, host),
  }, store.thoughtState);
  renderTechniques(
    { stages: els.stages, redTech: els.redTech, blueTech: els.blueTech },
    store.attackMap, store.events, store.cursor,
  );
  renderScoreboard(scoreEls(), store.events, store.cursor, store.weights, store.spend, store.finalScore);
  renderScoreboard(arenaScoreEls(), store.events, store.cursor, store.weights, store.spend, store.finalScore);
  renderScoreDetails(scoreEls(), store.events, store.cursor, store.weights, store.spend, store.finalScore);
  renderEvidence({ chips: els.chips, tabs: els.tabs, viewer: els.viewer }, store.events, store.cursor, hosts);
  els.eventCount.textContent = `${store.events.length} event${store.events.length === 1 ? "" : "s"}`;
}

function stopTimer() {
  if (store.timer) {
    clearInterval(store.timer);
    store.timer = null;
  }
  store.playing = false;
}

function cancelAdvance() {
  if (!store.advance) return;
  clearTimeout(store.advance.timer);
  clearInterval(store.advance.ticker);
  store.advance = null;
  setEndNext(endEls(), "");
}

function tickReplay() {
  if (store.cursor >= store.events.length) {
    stopTimer();
    if (store.replayScore) store.finalScore = store.replayScore;
    render(false);
    showEndCardFor("replay");
    return;
  }
  store.cursor += 1;
  render(true);
}

function playReplay() {
  if (store.mode !== "replay" || store.events.length === 0) return;
  if (store.cursor >= store.events.length) {
    store.cursor = 0;
    store.firstAccessShown = false;
    store.finalScore = null;
    hideEndCard(endEls());
    cancelAdvance();
    cancelArena(arenaRefs);
  }
  stopTimer();
  hideEndCard(endEls());
  cancelAdvance();
  store.playing = true;
  store.timer = setInterval(tickReplay, REPLAY_INTERVAL_MS / store.speed);
  render(false);
}

function pauseReplay() {
  stopTimer();
  render(false);
}

function jump(index) {
  if (store.mode !== "replay") return;
  cancelArena(arenaRefs);
  hideEndCard(endEls());
  cancelAdvance();
  store.cursor = Math.max(0, Math.min(store.events.length, index));
  if (store.firstAuthIndex !== null && store.cursor - 1 < store.firstAuthIndex) {
    store.firstAccessShown = false;
  }
  store.finalScore = store.cursor >= store.events.length ? store.replayScore : null;
  render(false);
}

function closeLive() {
  if (store.liveSource) {
    store.liveSource.close();
    store.liveSource = null;
  }
}

function destroyOpening() {
  if (store.opening) {
    store.opening.destroy();
    store.opening = null;
  }
}

// Opening an exercise for playback: slate, countdown, then motion.
// Scrub and jump never pass through here, so the slate plays once.
async function openExercise(runId, opts) {
  const o = opts || {};
  destroyOpening();
  hideEndCard(endEls());
  cancelAdvance();
  closeLive();
  stopTimer();
  cancelArena(arenaRefs);
  notice("");
  setMode("replay", `Replaying ${runId}`);
  switchTab("arena");
  store.runId = runId;
  store.events = [];
  store.cursor = 0;
  store.finalScore = null;
  store.replayScore = null;
  store.firstAuthIndex = null;
  store.firstAccessShown = false;
  store.thoughtState = null;
  setThoughtsCollapsed(thoughtPanels(), window.innerWidth < 800);
  render(false);
  let data;
  try {
    data = await fetchRun(runId);
  } catch (err) {
    notice(`Could not load run ${runId}: ${err.message}`, true);
    setMode("idle", "No exercise loaded");
    switchTab("lobby");
    return;
  }
  if (store.runId !== runId) return;
  // The first event carries the range layout and scenario identity;
  // read them before the system events are filtered out of the moves.
  applyLayout(layoutFromEvents(data.events || []), scenarioMeta(data.events || []));
  // System events are harness notes, not moves: skip them everywhere.
  store.events = (data.events || []).filter((ev) => ev && ev.actor !== "system");
  store.replayScore = data.score || null;
  store.cursor = 0;
  store.firstAuthIndex = store.events.findIndex(
    (ev) => ev.actor === "red" && ev.tool === "try_credential" && (ev.result || {}).authenticated === true,
  );
  if (store.firstAuthIndex < 0) store.firstAuthIndex = null;
  render(false);
  if (o.at !== undefined && o.at !== null) {
    const pos = o.at === "end" ? store.events.length : Number(o.at) || 0;
    store.cursor = Math.max(0, Math.min(store.events.length, pos));
    store.finalScore = store.cursor >= store.events.length ? store.replayScore : null;
    render(false);
    if (store.cursor >= store.events.length) showEndCardFor("replay");
    return;
  }
  store.opening = playOpening(
    { root: els.slate, title: els.slateTitle, hosts: els.slateHosts, count: els.slateCount },
    {
      label: runId,
      hosts: layoutHosts(store.layout || classicLayout()),
      roles: Object.fromEntries(((store.layout || {}).hosts || []).map((host) => [host.id, host.role])),
      reduced: reducedMotion(),
      onDone: () => {
        store.opening = null;
        if (store.runId === runId) playReplay();
      },
    },
  );
}

async function beginRun(agents, scenarioToken) {
  closeLive();
  stopTimer();
  destroyOpening();
  hideEndCard(endEls());
  hideDeal(els.dealSlot);
  cancelAdvance();
  cancelArena(arenaRefs);
  notice("");
  controls.run.disabled = true;
  try {
    const data = await startExercise(agents, undefined, scenarioToken);
    store.liveExerciseId = data.exercise_id;
    setMode("booting", `Booting range for ${data.exercise_id}`);
    switchTab("arena");
    store.runId = null;
    store.events = [];
    store.cursor = 0;
    store.finalScore = null;
    store.firstAuthIndex = null;
    store.firstAccessShown = false;
    applyLayout(classicLayout(), { id: "classic" });
    if (data.warning) notice(data.warning, false);
    store.liveSource = openLive(onLiveMessage, onLiveError);
    render(false);
  } catch (err) {
    notice(`Could not start the exercise: ${err.message}`, true);
  } finally {
    controls.run.disabled = false;
  }
}

// Archived run through the live rendering path, without Docker and
// without starting an exercise. Only used with an explicit ?live=
// feed address for verification and demos.
function beginFakeLive(path) {
  closeLive();
  stopTimer();
  destroyOpening();
  hideEndCard(endEls());
  cancelAdvance();
  cancelArena(arenaRefs);
  notice("");
  setMode("live", `Live feed ${path}`);
  switchTab("arena");
  store.runId = null;
  store.events = [];
  store.cursor = 0;
  store.finalScore = null;
  store.firstAuthIndex = null;
  store.firstAccessShown = false;
  applyLayout(classicLayout(), { id: "classic" });
  const translate = (msg) => {
    if (msg && msg.event) onLiveMessage({ type: "event", event: msg.event });
    else if (msg && msg.done) onLiveMessage({ type: "done", score: msg.score });
  };
  store.liveSource = openLive(translate, onLiveError, path);
  render(false);
}

function firstAuthPosition() {
  return store.events.findIndex(
    (ev) => ev.actor === "red" && ev.tool === "try_credential" && (ev.result || {}).authenticated === true,
  );
}

function onLiveMessage(msg) {
  if (msg.type === "event" && msg.event) {
    // System events carry the range layout on the live stream; read
    // it before they are skipped like everywhere else. The layout
    // always precedes the first move, so only pre-move events apply.
    if (msg.event.actor === "system") {
      const args = msg.event.args || {};
      if (args.scenario_layout && store.events.length === 0) {
        applyLayout(layoutFromEvents([msg.event]), scenarioMeta([msg.event]));
        render(false);
      }
      return;
    }
    if (store.mode === "booting") {
      const name = store.liveExerciseId ? `Live exercise ${store.liveExerciseId}` : "Live exercise";
      setMode("live", name);
    }
    store.events.push(msg.event);
    store.cursor = store.events.length;
    if (store.firstAuthIndex === null) {
      const at = firstAuthPosition();
      store.firstAuthIndex = at < 0 ? null : at;
    }
    render(true);
  } else if (msg.type === "done") {
    store.finalScore = msg.score || null;
    if (msg.spend_cap_warning) notice(msg.spend_cap_warning, false);
    els.mode.dataset.mode = "ended";
    els.mode.textContent = "Ended";
    closeLive();
    refreshRuns(false);
    pollStatus();
    render(false);
    showEndCardFor("live");
  } else if (msg.type === "error") {
    notice(`Exercise failed: ${msg.text || "unknown error"}`, true);
    if (store.events.length === 0) setMode("idle", "No exercise loaded");
    closeLive();
    pollStatus();
    render(false);
  } else if (msg.type === "note" && msg.text) {
    notice(msg.text, false);
  }
}

function onLiveError() {
  // EventSource reconnects on its own; only flag when live is expected.
  if ((store.mode === "live" || store.mode === "booting") && store.liveSource) {
    notice("Live stream interrupted, reconnecting…", true);
  }
}

function onRunRequest(agents) {
  if (agents === "model") {
    fetchStatus()
      .then((spend) => {
        const capped = spend.spent_today >= spend.daily_limit
          ? "Spend cap already reached: this exercise would run on the scripted baseline."
          : null;
        showConfirm(confirm, spend, capped, () => dealLive(agents));
      })
      .catch((err) => notice(`Could not check spend: ${err.message}`, true));
    return;
  }
  dealLive(agents);
}

// One draw for the dealing moment: a fixed held-out seed and
// difficulty when Advanced names them, else a fresh random draw.
function dealDraw() {
  const raw = ((els.seedInput && els.seedInput.value) || "").trim();
  const drawn = drawLiveScenario();
  let seed = drawn.seed;
  if (raw !== "") {
    seed = Number(raw);
    if (!Number.isInteger(seed) || seed < HELDOUT_MIN || seed > HELDOUT_MAX) {
      return { error: `Seed must hold ${HELDOUT_MIN} to ${HELDOUT_MAX}.` };
    }
  }
  const picked = els.dealDifficulty ? els.dealDifficulty.value : "random";
  const difficulty = picked === "random" ? drawn.difficulty : picked;
  return { seed, difficulty, token: `gen:${seed}:${difficulty}` };
}

// Deal an unseen network and preview it before starting. Every
// live click draws fresh; Advanced pins the draw for reproductions.
async function dealLive(agents) {
  const draw = dealDraw();
  if (draw.error) {
    notice(draw.error, true);
    return;
  }
  switchTab("lobby");
  renderDeal(els.dealSlot, { phase: "drawing" }, {
    onStart: () => {},
    onRedeal: () => dealLive(agents),
  });
  try {
    const preview = await fetchPreview(draw.token);
    renderDeal(els.dealSlot, {
      phase: "ready",
      seed: draw.seed,
      scenarioId: preview.scenario_id,
      difficulty: preview.difficulty,
      hostCount: preview.host_count,
      layout: preview.layout,
    }, {
      onStart: () => beginRun(agents, draw.token),
      onRedeal: () => dealLive(agents),
    });
  } catch (err) {
    renderDeal(els.dealSlot, {
      phase: "error",
      error: `Could not draw a network: ${err.message}`,
    }, {
      onStart: () => {},
      onRedeal: () => dealLive(agents),
    });
  }
}

// Featured-first lobby order for autoplay and presentation: the
// curated set in order, then the ranked archive.
function visibleCards() {
  const { featured, archive } = partitionLobby(store.lobby.cards, effectiveFeaturedIds());
  return [...featured, ...archive];
}

function effectiveFeaturedIds() {
  if (store.lobby.featuredIds.length > 0) return store.lobby.featuredIds;
  const real = store.lobby.cards.filter(
    (card) => !String(card.id || "").startsWith("fake-") && (!card.flags || card.flags.length === 0),
  );
  return rankFeatured(real).slice(0, 6).map((card) => card.id);
}

function nextFeaturedId() {
  const ids = visibleCards().map((card) => card.id);
  const queue = autoplayQueue(ids, store.runId);
  return queue.length > 0 ? queue[0] : null;
}

function showEndCardFor() {
  const stats = endCardStats(store.events, store.finalScore, store.weights);
  const audits = reduceAudits(store.events, store.events.length);
  const nextId = nextFeaturedId();
  showEndCard(endEls(), stats, audits.summary.justifiedPct, nextId);
  if (stats.key === "secret-exposed") glitchStinger(arenaRefs);
  const auto = store.presentLoop || (store.lobby.autoplay && store.mode === "replay");
  if (auto && nextId) scheduleAdvance(nextId);
}

function scheduleAdvance(nextId) {
  cancelAdvance();
  let left = ADVANCE_SECONDS;
  const tick = () => {
    setEndNext(endEls(), `Next: ${nextId} in ${left}s`);
    left -= 1;
  };
  tick();
  const ticker = setInterval(tick, 1000);
  const timer = setTimeout(() => {
    cancelAdvance();
    openExercise(nextId);
  }, ADVANCE_SECONDS * 1000);
  store.advance = { timer, ticker };
}

function armLabel(memory) {
  if (memory === "on") return "Memory on";
  if (memory === "off") return "Memory off";
  return null;
}

async function classifyRun(run, armByEpisode) {
  try {
    const data = await fetchRun(run.id);
    const full = data.events || [];
    const layout = layoutFromEvents(full);
    const hosts = layoutHosts(layout);
    const weights = layoutWeights(layout);
    const events = full.filter((ev) => ev && ev.actor !== "system");
    const out = displayOutcome(events);
    const score = data.score || scoreEvents(events, weights);
    const lane = events.filter((ev) => ev.actor === "red" || ev.actor === "blue");
    const roles = {};
    for (const host of layout.hosts) roles[host.id] = host.role;
    return {
      id: run.id,
      outcomeKey: out.key,
      outcome: { label: out.label, tone: out.tone },
      blueScore: typeof score.blue_score === "number" ? score.blue_score : null,
      moves: lane.length,
      story: storyLine(events, roles, out.key),
      duration: formatDuration(
        full.length > 0 ? full[0].timestamp : null,
        full.length > 0 ? full[full.length - 1].timestamp : null,
        lane.length,
      ),
      arm: armLabel(armByEpisode.get(run.id)),
      blocks: reduceHostStates(events, hosts),
      tstate: thumbnailState(events, layout),
      flags: runFlags(run.id, events),
      layout,
      scenario: scenarioText(scenarioMeta(full)),
    };
  } catch {
    return {
      id: run.id,
      outcomeKey: "stalemate",
      outcome: { label: "Unreadable", tone: "gray" },
      blueScore: null,
      moves: 0,
      arm: armLabel(armByEpisode.get(run.id)),
      blocks: reduceHostStates([]),
      flags: ["test"],
      layout: classicLayout(),
      scenario: "classic",
    };
  }
}

function paintLobby() {
  renderLobby(els.featuredGrid, els.archiveGrid, els.archiveCount, {
    cards: store.lobby.cards,
    featuredIds: effectiveFeaturedIds(),
    loading: store.lobby.loading,
  }, { onPlay: (id) => openExercise(id) });
}

async function refreshRuns() {
  store.lobby.loading = true;
  store.lobby.cards = [];
  try {
    const feat = await fetchFeatured();
    store.lobby.featuredIds = Array.isArray(feat.featured) ? feat.featured : [];
  } catch {
    store.lobby.featuredIds = [];
  }
  paintLobby();
  let runs = [];
  try {
    const data = await fetchRuns();
    runs = data.runs || [];
  } catch (err) {
    store.lobby.loading = false;
    notice(`Could not list past runs: ${err.message}`, true);
    paintLobby();
    return;
  }
  store.lobby.runs = runs;
  const armByEpisode = new Map();
  try {
    const campaigns = await fetchCampaigns();
    for (const item of campaigns.campaigns || []) {
      try {
        const detail = await fetchCampaign(item.name);
        for (const row of detail.episodes || []) {
          if (row.episode) armByEpisode.set(row.episode, detail.memory);
        }
      } catch {
        // One unreadable campaign never blocks the lobby.
      }
    }
  } catch {
    // Campaigns are optional garnish on the cards.
  }
  store.lobby.armByEpisode = armByEpisode;
  const queue = [...runs];
  const workers = [];
  const pump = async () => {
    while (queue.length > 0) {
      const run = queue.shift();
      const card = await classifyRun(run, armByEpisode);
      store.lobby.cards.push(card);
      paintLobby();
    }
  };
  for (let i = 0; i < Math.min(LOBBY_FETCH_POOL, Math.max(1, queue.length)); i += 1) {
    workers.push(pump());
  }
  await Promise.all(workers);
  store.lobby.loading = false;
  paintLobby();
}

function watchReplay() {
  const { featured } = partitionLobby(store.lobby.cards, effectiveFeaturedIds());
  const pool = featured.length > 0 ? featured : visibleCards();
  if (pool.length === 0) {
    notice("No exercises to open yet.", false);
    return;
  }
  const pick = pool[Math.floor(Math.random() * pool.length)];
  openExercise(pick.id);
}

async function pollStatus() {
  try {
    const spend = await fetchStatus();
    store.spend = spend;
    const status = serverStatus(spend);
    setServer(controls, status.state, status.label);
    controls.run.disabled = status.runDisabled;
    els.liveBtn.disabled = status.runDisabled;
    if (!spend.running && spend.last_warning && store.mode === "idle") {
      notice(spend.last_warning, false);
    }
    renderScoreboard(scoreEls(), store.events, store.cursor, store.weights, store.spend, store.finalScore);
    renderScoreboard(arenaScoreEls(), store.events, store.cursor, store.weights, store.spend, store.finalScore);
    renderScoreDetails(scoreEls(), store.events, store.cursor, store.weights, store.spend, store.finalScore);
  } catch (err) {
    setServer(controls, "down", "Server unreachable");
  }
}

const TABS = {
  lobby: ["tabLobby", "lobbyView"],
  arena: ["tabArena", "arenaView"],
  story: ["tabStory", "storyView"],
  details: ["tabDetails", "detailsView"],
  learn: ["tabLearn", "learnView"],
};

function switchTab(name) {
  const active = TABS[name] ? name : "lobby";
  for (const [key, [tabId, viewId]] of Object.entries(TABS)) {
    const on = key === active;
    els[tabId].classList.toggle("active", on);
    els[tabId].setAttribute("aria-selected", String(on));
    els[viewId].hidden = !on;
  }
  if (active === "lobby" && store.playing) pauseReplay();
  if (active === "learn" && !store.learnLoaded) {
    store.learnLoaded = true;
    loadLearning();
  }
  setArenaActive(arenaRefs, active === "arena");
}

async function drawLearning() {
  try {
    const [mem, ctl] = await Promise.all([
      els.memSelect.value ? fetchCampaign(els.memSelect.value) : Promise.resolve(null),
      els.ctlSelect.value ? fetchCampaign(els.ctlSelect.value) : Promise.resolve(null),
    ]);
    renderLearn(
      { svg: els.learnSvg, legendMem: els.legendMem, legendCtl: els.legendCtl, table: els.playBody, empty: els.playEmpty },
      mem, ctl,
    );
  } catch (err) {
    notice(`Could not load campaigns: ${err.message}`, true);
  }
}

async function loadLearning() {
  try {
    const data = await fetchCampaigns();
    fillCampaignSelects(els.memSelect, data.campaigns || [], "on");
    fillCampaignSelects(els.ctlSelect, data.campaigns || [], "off");
    await drawLearning();
  } catch (err) {
    notice(`Could not list campaigns: ${err.message}`, true);
  }
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem("crucible-theme", theme);
  } catch (err) {
    // Private browsing: theme just won't persist.
  }
  els.themeBtn.textContent = theme === "dark" ? "Light" : "Dark";
}

function toggleTheme() {
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

function setPresent(on) {
  document.body.classList.toggle("present", on);
  store.presentLoop = on;
  els.presentBtn.textContent = on ? "Exit present" : "Present";
  if (on) {
    switchTab("arena");
    // Presentation mode loops the featured lobby from the top.
    if (store.mode === "idle" || (store.mode === "replay" && store.cursor >= store.events.length)) {
      const cards = visibleCards();
      if (cards.length > 0 && store.runId !== cards[0].id) openExercise(cards[0].id);
      else if (cards.length > 0 && store.cursor >= store.events.length) playReplay();
    }
  } else {
    cancelAdvance();
  }
}

function toggleSound() {
  const on = toggleArenaSound();
  els.soundBtn.textContent = on ? "Sound on" : "Sound off";
  els.soundBtn.setAttribute("aria-pressed", String(on));
}

function togglePresent() {
  setPresent(!document.body.classList.contains("present"));
}

function setSettings(open) {
  els.settingsPop.hidden = !open;
  els.settingsBtn.setAttribute("aria-expanded", String(open));
}

function loadBool(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : raw === "1";
  } catch {
    return fallback;
  }
}

function saveBool(key, value) {
  try {
    window.localStorage.setItem(key, value ? "1" : "0");
  } catch {
    // Private browsing: the toggle just won't persist.
  }
}

// Shortcuts: P presents and loops, L opens the lobby, A/S switch
// views, M toggles arena sound, arrows and space drive replay.
function onShortcut(ev) {
  if (ev.defaultPrevented) return;
  const tag = (ev.target && ev.target.tagName) || "";
  if (["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(tag)) return;
  if (!confirm.overlay.hidden) return;
  if (ev.key === "Escape") {
    if (endCardVisible(endEls())) {
      hideEndCard(endEls());
      cancelAdvance();
      return;
    }
    if (document.body.classList.contains("present")) setPresent(false);
    setSettings(false);
    return;
  }
  if (ev.key === "p" || ev.key === "P") {
    ev.preventDefault();
    togglePresent();
    return;
  }
  if (ev.key === "l" || ev.key === "L") {
    ev.preventDefault();
    switchTab("lobby");
    return;
  }
  if (ev.key === "a" || ev.key === "A") {
    ev.preventDefault();
    switchTab("arena");
    return;
  }
  if (ev.key === "s" || ev.key === "S") {
    ev.preventDefault();
    switchTab("story");
    return;
  }
  if (ev.key === "m" || ev.key === "M") {
    ev.preventDefault();
    toggleSound();
    return;
  }
  if (store.mode !== "replay" || store.events.length === 0) return;
  if (ev.code === "Space") {
    ev.preventDefault();
    if (store.playing) pauseReplay();
    else playReplay();
  } else if (ev.key === "ArrowRight") {
    ev.preventDefault();
    stopTimer();
    jump(store.cursor + 1);
  } else if (ev.key === "ArrowLeft") {
    ev.preventDefault();
    stopTimer();
    jump(store.cursor - 1);
  }
}

function applySpeed(speed) {
  const value = Number(speed);
  if (!Number.isFinite(value) || value <= 0) return;
  store.speed = Math.min(16, value);
  controls.speedSeg.querySelectorAll("button").forEach((btn) => {
    const on = Number(btn.dataset.speed) === store.speed;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", String(on));
  });
  if (store.playing) playReplay();
}

async function boot() {
  let theme = "light";
  try {
    theme = window.localStorage.getItem("crucible-theme") || "light";
  } catch (err) {
    // Private browsing: fall back to the light theme.
  }
  setTheme(theme === "dark" ? "dark" : "light");
  store.lobby.autoplay = loadBool("crucible-autoplay", false);
  els.autoplayBox.checked = store.lobby.autoplay;
  try {
    const config = await fetchConfig();
    if (config.business_weights) store.weights = config.business_weights;
  } catch (err) {
    notice("Could not load range config, using default host weights.", false);
  }
  mapRefs = initMap(els.map, store.weights);
  arenaRefs = initArena(els.arenaCanvas);
  try {
    store.attackMap = await fetchAttackMap();
  } catch (err) {
    store.attackMap = { red: {}, blue: {}, stages: [] };
  }
  initControls(controls, {
    onRun: onRunRequest,
    onSpeed: (speed) => applySpeed(speed),
  });
  initThoughtHeads(thoughtPanels());
  setThoughtsCollapsed(thoughtPanels(), window.innerWidth < 800);
  initEndCard(endEls(), {
    onReplay: () => {
      hideEndCard(endEls());
      cancelAdvance();
      cancelArena(arenaRefs);
      store.cursor = 0;
      store.firstAccessShown = false;
      store.finalScore = null;
      playReplay();
    },
    onNext: () => {
      const next = nextFeaturedId();
      if (next) openExercise(next);
    },
    onLobby: () => switchTab("lobby"),
  });
  els.tabLobby.addEventListener("click", () => switchTab("lobby"));
  els.tabArena.addEventListener("click", () => switchTab("arena"));
  els.tabStory.addEventListener("click", () => switchTab("story"));
  els.tabDetails.addEventListener("click", () => switchTab("details"));
  els.tabLearn.addEventListener("click", () => switchTab("learn"));
  els.memSelect.addEventListener("change", drawLearning);
  els.ctlSelect.addEventListener("change", drawLearning);
  els.themeBtn.addEventListener("click", toggleTheme);
  els.presentBtn.addEventListener("click", togglePresent);
  els.liveBtn.addEventListener("click", () => onRunRequest(controls.agents.value));
  els.replayBtn.addEventListener("click", watchReplay);
  els.autoplayBox.addEventListener("change", () => {
    store.lobby.autoplay = els.autoplayBox.checked;
    saveBool("crucible-autoplay", store.lobby.autoplay);
  });
  els.settingsBtn.addEventListener("click", (click) => {
    click.stopPropagation();
    setSettings(els.settingsPop.hidden);
  });
  els.settingsPop.addEventListener("click", (click) => click.stopPropagation());
  document.addEventListener("click", () => setSettings(false));
  els.play.addEventListener("click", playReplay);
  els.pause.addEventListener("click", pauseReplay);
  els.scrubber.addEventListener("input", () => jump(Number(els.scrubber.value)));
  els.arenaPlay.addEventListener("click", playReplay);
  els.arenaPause.addEventListener("click", pauseReplay);
  els.arenaScrubber.addEventListener("input", () => jump(Number(els.arenaScrubber.value)));
  els.soundBtn.addEventListener("click", toggleSound);
  document.addEventListener("keydown", onShortcut);
  switchTab("lobby");
  render(false);
  const params = new URLSearchParams(window.location.search);
  const speed = params.get("speed");
  if (speed) applySpeed(speed);
  const livePath = params.get("live");
  const wanted = params.get("run");
  const view = params.get("view");
  const at = params.get("at");
  refreshRuns();
  await pollStatus();
  setInterval(pollStatus, 5000);
  if (livePath) {
    beginFakeLive(livePath);
    return;
  }
  if (wanted) {
    // Deep links open at once; the lobby keeps classifying behind.
    // Unknown ids fall back to the lobby with a notice inside open.
    openExercise(wanted, at === null ? {} : { at });
    return;
  }
  if (view && TABS[view]) switchTab(view);
}

boot();
