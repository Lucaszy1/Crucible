// Operations Center bootstrap: Story view, render loop, tabs, themes.
import { fetchAttackMap, fetchCampaign, fetchCampaigns, fetchConfig, fetchRun, fetchRuns, fetchStatus, openLive, startExercise } from "./api.js";
import { fillCampaignSelects, renderLearn } from "./learn.js";
import { reduceHostStates } from "./state.js";
import { describeEvent, statusSentence } from "./story.js";
import { initMap, updateMap } from "./network.js";
import { renderFeed } from "./feed.js";
import { renderTransport } from "./timeline.js";
import { renderAttack } from "./attack.js";
import { reduceAudits, renderAuditor } from "./audit.js";
import { renderScoreboard, renderScoreDetails } from "./scoreboard.js";
import { focusEvidenceRef, renderEvidence } from "./evidence.js";
import { fillRuns, initControls, serverStatus, setServer, showConfirm } from "./controls.js";

const REPLAY_INTERVAL_MS = 1200;

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
  tabStory: document.getElementById("tabStory"),
  tabDetails: document.getElementById("tabDetails"),
  tabLearn: document.getElementById("tabLearn"),
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
  renderEvidence({ chips: els.chips, tabs: els.tabs, viewer: els.viewer }, store.events, store.cursor);
  switchTab("details");
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
  const states = reduceHostStates(slice);
  updateMap(mapRefs, states, slice, animate);
  const audits = reduceAudits(store.events, store.cursor);
  renderFeed(els.feed, store.events, store.cursor, store.attackMap, onEvidenceRef, audits.byTarget);
  renderPresent(slice);
  els.status.textContent = statusSentence({
    latest: latestLaneEvent(slice),
    ended: Boolean(store.finalScore),
    outcome: store.finalScore?.outcome,
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
  renderAttack(
    { stages: els.stages, redTech: els.redTech, blueTech: els.blueTech },
    store.attackMap, store.events, store.cursor,
  );
  renderScoreboard(scoreEls(), store.events, store.cursor, store.weights, store.spend, store.finalScore);
  renderScoreDetails(scoreEls(), store.events, store.cursor, store.weights, store.spend, store.finalScore);
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
    renderScoreboard(scoreEls(), store.events, store.cursor, store.weights, store.spend, store.finalScore);
    renderScoreDetails(scoreEls(), store.events, store.cursor, store.weights, store.spend, store.finalScore);
  } catch (err) {
    setServer(controls, "down", "Server unreachable");
  }
}

const TABS = {
  story: ["tabStory", "storyView"],
  details: ["tabDetails", "detailsView"],
  learn: ["tabLearn", "learnView"],
};

function switchTab(name) {
  const active = TABS[name] ? name : "story";
  for (const [key, [tabId, viewId]] of Object.entries(TABS)) {
    const on = key === active;
    els[tabId].classList.toggle("active", on);
    els[tabId].setAttribute("aria-selected", String(on));
    els[viewId].hidden = !on;
  }
  if (active === "learn" && !store.learnLoaded) {
    store.learnLoaded = true;
    loadLearning();
  }
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
  els.presentBtn.textContent = on ? "Exit present" : "Present";
}

function togglePresent() {
  setPresent(!document.body.classList.contains("present"));
}

function setSettings(open) {
  els.settingsPop.hidden = !open;
  els.settingsBtn.setAttribute("aria-expanded", String(open));
}

// Shortcuts: P toggles presentation, arrows and space drive replay.
function onShortcut(ev) {
  if (ev.defaultPrevented) return;
  const tag = (ev.target && ev.target.tagName) || "";
  if (["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(tag)) return;
  if (!confirm.overlay.hidden) return;
  if (ev.key === "Escape") {
    if (document.body.classList.contains("present")) setPresent(false);
    setSettings(false);
    return;
  }
  if (ev.key === "p" || ev.key === "P") {
    ev.preventDefault();
    togglePresent();
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

async function boot() {
  let theme = "light";
  try {
    theme = window.localStorage.getItem("crucible-theme") || "light";
  } catch (err) {
    // Private browsing: fall back to the light theme.
  }
  setTheme(theme === "dark" ? "dark" : "light");
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
  els.tabStory.addEventListener("click", () => switchTab("story"));
  els.tabDetails.addEventListener("click", () => switchTab("details"));
  els.tabLearn.addEventListener("click", () => switchTab("learn"));
  els.memSelect.addEventListener("change", drawLearning);
  els.ctlSelect.addEventListener("change", drawLearning);
  els.themeBtn.addEventListener("click", toggleTheme);
  els.presentBtn.addEventListener("click", togglePresent);
  els.settingsBtn.addEventListener("click", (click) => {
    click.stopPropagation();
    setSettings(els.settingsPop.hidden);
  });
  els.settingsPop.addEventListener("click", (click) => click.stopPropagation());
  document.addEventListener("click", () => setSettings(false));
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
