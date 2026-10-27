// Operations Center bootstrap: live and replay modes, render loop.
import { fetchAttackMap, fetchConfig, fetchRun, fetchRuns, fetchStatus, openLive, startExercise } from "./api.js";
import { reduceHostStates } from "./state.js";
import { initMap, updateMap } from "./network.js";
import { renderStreams } from "./streams.js";
import { renderTicks, renderTransport } from "./timeline.js";
import { renderAttack } from "./attack.js";
import { reduceAudits, renderAuditor } from "./audit.js";
import { renderScoreboard } from "./scoreboard.js";
import { focusEvidenceRef, renderEvidence } from "./evidence.js";
import { fillRuns, initControls, serverStatus, setServer, showConfirm } from "./controls.js";

const REPLAY_INTERVAL_MS = 1200;

const els = {
  mode: document.getElementById("modeBadge"),
  title: document.getElementById("exerciseTitle"),
  eventCount: document.getElementById("eventCount"),
  notice: document.getElementById("notice"),
  map: document.getElementById("mapSvg"),
  stages: document.getElementById("stageStrip"),
  redTech: document.getElementById("redTechList"),
  blueTech: document.getElementById("blueLabelList"),
  red: document.getElementById("redStream"),
  redCount: document.getElementById("redCount"),
  blue: document.getElementById("blueStream"),
  blueCount: document.getElementById("blueCount"),
  play: document.getElementById("playBtn"),
  pause: document.getElementById("pauseBtn"),
  scrubber: document.getElementById("scrubber"),
  position: document.getElementById("positionLabel"),
  ticks: document.getElementById("tickStrip"),
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
  auditCount: document.getElementById("auditCount"),
};

const controls = {
  agents: document.getElementById("agentsSelect"),
  run: document.getElementById("runBtn"),
  replay: document.getElementById("replaySelect"),
  replayBtn: document.getElementById("replayBtn"),
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
  events: [],
  cursor: 0,
  playing: false,
  speed: 1,
  timer: null,
  weights: { web: 3, app: 3, db: 5, workstation: 1 },
  attackMap: null,
  spend: null,
  finalScore: null,
  label: "No exercise loaded",
  liveSource: null,
  animateNext: false,
};

let mapRefs = null;

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
  els.mode.textContent = mode === "live" ? "Live" : mode === "replay" ? "Replay" : mode === "booting" ? "Booting" : "Idle";
  if (label) store.label = label;
  els.title.textContent = store.label;
}

function onEvidenceRef(ref) {
  focusEvidenceRef(ref);
  renderEvidence({ chips: els.chips, tabs: els.tabs, viewer: els.viewer }, store.events, store.cursor);
}

function render(animate) {
  const slice = store.events.slice(0, store.cursor);
  const states = reduceHostStates(slice);
  updateMap(mapRefs, states, slice, animate);
  const audits = reduceAudits(store.events, store.cursor);
  renderStreams(
    { red: els.red, redCount: els.redCount, blue: els.blue, blueCount: els.blueCount },
    store.events, store.cursor, store.attackMap, onEvidenceRef, audits.byTarget,
  );
  renderAuditor(
    { list: els.auditList, chips: els.auditChips, cost: els.auditCost, count: els.auditCount },
    store.events, store.cursor, audits, onEvidenceRef,
  );
  const canScrub = store.mode === "replay";
  renderTicks(els.ticks, store.events, store.cursor, canScrub, (index) => jump(index));
  renderTransport(
    { scrubber: els.scrubber, position: els.position, play: els.play, pause: els.pause },
    store.cursor, store.events.length, store.playing, canScrub,
  );
  renderAttack(
    { stages: els.stages, redTech: els.redTech, blueTech: els.blueTech },
    store.attackMap, store.events, store.cursor,
  );
  renderScoreboard(
    {
      outcome: els.outcome, detect: els.detect, contain: els.contain, downtime: els.downtime,
      blue: els.blueScore, banner: els.banner, costLabel: els.costLabel,
      costBar: els.costBar, exerciseCost: els.exerciseCost,
    },
    store.events, store.cursor, store.weights, store.spend, store.finalScore,
  );
  renderEvidence({ chips: els.chips, tabs: els.tabs, viewer: els.viewer }, store.events, store.cursor);
  els.eventCount.textContent = `${store.events.length} event${store.events.length === 1 ? "" : "s"}`;
}

function stopTimer() {
  if (store.timer) {
    clearInterval(store.timer);
    store.timer = null;
  }
  store.playing = false;
}

function tickReplay() {
  if (store.cursor >= store.events.length) {
    stopTimer();
    render(false);
    return;
  }
  store.cursor += 1;
  render(true);
}

function playReplay() {
  if (store.mode !== "replay" || store.events.length === 0) return;
  if (store.cursor >= store.events.length) store.cursor = 0;
  stopTimer();
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
  store.cursor = Math.max(0, Math.min(store.events.length, index));
  store.finalScore = store.cursor >= store.events.length ? store.finalScore : null;
  if (store.replayScore && store.cursor >= store.events.length) store.finalScore = store.replayScore;
  render(false);
}

function closeLive() {
  if (store.liveSource) {
    store.liveSource.close();
    store.liveSource = null;
  }
}

async function loadReplay(runId, autoplay) {
  closeLive();
  stopTimer();
  notice("");
  setMode("replay", `Replaying ${runId}`);
  store.events = [];
  store.cursor = 0;
  store.finalScore = null;
  store.replayScore = null;
  render(false);
  try {
    const data = await fetchRun(runId);
    store.events = data.events || [];
    store.replayScore = data.score || null;
    store.cursor = 0;
    if (autoplay) {
      playReplay();
    } else {
      store.cursor = store.events.length;
      store.finalScore = store.replayScore;
      render(false);
    }
  } catch (err) {
    notice(`Could not load run ${runId}: ${err.message}`, true);
    setMode("idle", "No exercise loaded");
  }
}

async function beginRun(agents) {
  closeLive();
  stopTimer();
  notice("");
  controls.run.disabled = true;
  try {
    const data = await startExercise(agents);
    store.liveExerciseId = data.exercise_id;
    setMode("booting", `Booting range for ${data.exercise_id}`);
    store.events = [];
    store.cursor = 0;
    store.finalScore = null;
    if (data.warning) notice(data.warning, false);
    store.liveSource = openLive(onLiveMessage, onLiveError);
    render(false);
  } catch (err) {
    notice(`Could not start the exercise: ${err.message}`, true);
  } finally {
    controls.run.disabled = false;
  }
}

function onLiveMessage(msg) {
  if (msg.type === "event" && msg.event) {
    if (store.mode === "booting") {
      const name = store.liveExerciseId ? `Live exercise ${store.liveExerciseId}` : "Live exercise";
      setMode("live", name);
    }
    store.events.push(msg.event);
    store.cursor = store.events.length;
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
        showConfirm(confirm, spend, capped, () => beginRun("model"));
      })
      .catch((err) => notice(`Could not check spend: ${err.message}`, true));
    return;
  }
  beginRun("scripted");
}

async function refreshRuns(autoselect) {
  try {
    const data = await fetchRuns();
    fillRuns(controls.replay, data.runs || []);
    const params = new URLSearchParams(window.location.search);
    const wanted = params.get("run");
    if (wanted && (data.runs || []).some((run) => run.id === wanted)) {
      controls.replay.value = wanted;
      if (autoselect) loadReplay(wanted, true);
      return;
    }
    if (autoselect && data.runs && data.runs.length > 0) {
      controls.replay.value = data.runs[0].id;
      loadReplay(data.runs[0].id, false);
    }
  } catch (err) {
    fillRuns(controls.replay, []);
    notice(`Could not list past runs: ${err.message}`, true);
  }
}

async function pollStatus() {
  try {
    const spend = await fetchStatus();
    store.spend = spend;
    const status = serverStatus(spend);
    setServer(controls, status.state, status.label);
    controls.run.disabled = status.runDisabled;
    if (!spend.running && spend.last_warning && store.mode === "idle") {
      notice(spend.last_warning, false);
    }
    renderScoreboard(
      {
        outcome: els.outcome, detect: els.detect, contain: els.contain, downtime: els.downtime,
        blue: els.blueScore, banner: els.banner, costLabel: els.costLabel,
        costBar: els.costBar, exerciseCost: els.exerciseCost,
      },
      store.events, store.cursor, store.weights, store.spend, store.finalScore,
    );
  } catch (err) {
    setServer(controls, "down", "Server unreachable");
  }
}

// Replay shortcuts: space toggles play and pause, arrows step one event.
function onShortcut(ev) {
  if (ev.defaultPrevented) return;
  const tag = (ev.target && ev.target.tagName) || "";
  if (["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(tag)) return;
  if (!confirm.overlay.hidden) return;
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

async function boot() {
  try {
    const config = await fetchConfig();
    if (config.business_weights) store.weights = config.business_weights;
  } catch (err) {
    notice("Could not load range config, using default host weights.", false);
  }
  mapRefs = initMap(els.map, store.weights);
  try {
    store.attackMap = await fetchAttackMap();
  } catch (err) {
    store.attackMap = { red: {}, blue: {}, stages: [] };
  }
  initControls(controls, {
    onRun: onRunRequest,
    onReplay: (runId) => loadReplay(runId, true),
    onSpeed: (speed) => {
      store.speed = speed;
      if (store.playing) playReplay();
    },
  });
  els.play.addEventListener("click", playReplay);
  els.pause.addEventListener("click", pauseReplay);
  els.scrubber.addEventListener("input", () => jump(Number(els.scrubber.value)));
  document.addEventListener("keydown", onShortcut);
  render(false);
  await refreshRuns(true);
  await pollStatus();
  setInterval(pollStatus, 5000);
}

boot();
