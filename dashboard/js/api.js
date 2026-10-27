// Thin fetch helpers for the Operations Center API.

async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} returned ${res.status}`);
  return res.json();
}

export function fetchRuns() {
  return getJson("/api/runs");
}

export function fetchRun(runId) {
  return getJson(`/api/runs/${encodeURIComponent(runId)}`);
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

export async function startExercise(agents, exerciseId) {
  const res = await fetch("/api/exercise/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agents, exercise_id: exerciseId || undefined }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : data.detail?.message;
    throw new Error(detail || `start returned ${res.status}`);
  }
  return data;
}

// Open the live event stream. Returns the EventSource for closing.
export function openLive(onMessage, onError) {
  const source = new EventSource("/api/live");
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
