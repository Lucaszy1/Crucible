// Thin fetch helpers for the Operations Center API.
import { normalizeActor } from "./state.js";

async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = typeof data.detail === "string" ? data.detail : null;
    throw new Error(detail || `${path} returned ${res.status}`);
  }
  return res.json();
}

export function fetchRuns() {
  return getJson("/api/runs");
}

export async function fetchRun(runId, bench) {
  const qs = bench ? `?bench=${encodeURIComponent(bench)}` : "";
  const data = await getJson(`/api/runs/${encodeURIComponent(runId)}${qs}`);
  // Human defender turns ride the blue lane in every view.
  if (data && Array.isArray(data.events)) {
    data.events = data.events.map((ev) => normalizeActor(ev));
  }
  return data;
}

// Defender-order routes (relative URLs, same origin as the page).
async function postJson(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : null;
    const err = new Error(detail || `${path} returned ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export function parseOrder(text) {
  return postJson("/api/command/parse", { text });
}

export function sendOrder(text, source, action) {
  return postJson("/api/command", { text, source, action });
}

export function sendDemo(index) {
  return postJson("/api/command/demo", index === undefined ? {} : { index });
}

export function sendTakeover(take) {
  return postJson("/api/takeover", { take: Boolean(take) });
}

export function fetchCommandState() {
  return getJson("/api/command/state");
}

export async function sendAudio(wavBytes) {
  const res = await fetch("/api/audio", {
    method: "POST",
    headers: { "Content-Type": "audio/wav" },
    body: wavBytes,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : null;
    const err = new Error(detail || `/api/audio returned ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export function fetchStatus() {
  return getJson("/api/status");
}

export function fetchConfig() {
  return getJson("/api/config");
}

export function fetchAttackMap() {
  return getJson("/attack_map.json");
}

export function fetchFeatured() {
  return getJson("/featured.json");
}

export function fetchPreview(token) {
  return getJson(`/api/scenario/preview?scenario=${encodeURIComponent(token)}`);
}

export function fetchCampaigns() {
  return getJson("/api/campaigns");
}

export function fetchCampaign(name) {
  return getJson(`/api/campaigns/${encodeURIComponent(name)}`);
}

export function fetchStage3() {
  return getJson("/api/bench/stage3");
}

export function fetchDebrief(runId, bench) {
  const qs = bench ? `?bench=${encodeURIComponent(bench)}` : "";
  return getJson(`/api/runs/${encodeURIComponent(runId)}/debrief${qs}`);
}

export async function startExercise(agents, exerciseId, scenario) {
  const res = await fetch("/api/exercise/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      agents,
      exercise_id: exerciseId || undefined,
      scenario: scenario || undefined,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : data.detail?.message;
    throw new Error(detail || `start returned ${res.status}`);
  }
  return data;
}

// Open the live event stream. Returns the EventSource for closing.
// The path override replays an archived run through the same live
// rendering path (for example /api/stream/day3b-1?speed=8); callers
// translate that feed's payload shape before handling it.
export function openLive(onMessage, onError, path) {
  const source = new EventSource(path || "/api/live");
  source.onmessage = (msg) => {
    try {
      onMessage(JSON.parse(msg.data));
    } catch (err) {
      onError(err);
    }
  };
  source.onerror = () => onError(new Error("live stream interrupted"));
  return source;
}
