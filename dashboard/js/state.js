// Shared state helpers for the Operations Center dashboard.
// Pure functions only: no DOM access, so node can test this module.

export const HOSTS = ["web", "app", "db", "workstation"];

export const CONTAINMENT_TOOLS = ["isolate_host", "patch_service", "restore_host", "block_ip"];

export const DEFAULT_WEIGHTS = { web: 3, app: 3, db: 5, workstation: 1 };

export const RECON_TOOLS = ["scan_host", "http_request", "list_files"];

// Single shared host-state reducer. Every panel derives node state from
// the event stream through this function and nothing else.
export function reduceHostStates(eventList) {
  const states = { web: "clean", app: "clean", db: "clean", workstation: "clean" };
  for (const ev of eventList || []) {
    const host = eventHost(ev);
    if (!host || !(host in states)) continue;
    const tool = ev.tool || "";
    const result = ev.result || {};
    const ok = result.ok === true;
    if (ev.actor === "red") {
      if (tool === "read_file" && ok && String(result.path || "").startsWith("/secret/")) {
        states[host] = "compromised";
      } else if (tool === "try_credential" && result.authenticated === true) {
        if (states[host] !== "compromised") states[host] = "signed-in";
      } else if (RECON_TOOLS.includes(tool) && ok) {
        if (states[host] === "clean" || states[host] === "restored") states[host] = "probed";
      }
    } else if (ev.actor === "blue" && ok) {
      if (tool === "isolate_host" || tool === "patch_service") {
        states[host] = "isolated";
      } else if (tool === "restore_host") {
        states[host] = "restored";
      }
    }
  }
  return states;
}

export function eventHost(ev) {
  if (!ev) return null;
  return (ev.args && ev.args.host) || (ev.result && ev.result.host) || null;
}

export function secretRead(eventList) {
  for (const ev of eventList || []) {
    if (ev.actor !== "red" || ev.tool !== "read_file") continue;
    const result = ev.result || {};
    if (result.ok === true && String(result.path || "").startsWith("/secret/")) return true;
  }
  return false;
}

// Frontend mirror of orchestrator.score_events so the scoreboard can
// update live. The server score at the end of a run is authoritative.
export function scoreEvents(eventList, weights) {
  // Auditor and system events never shift positions or downtime,
  // mirroring the server. The clean-start check is not a move.
  const list = (eventList || []).filter((ev) => ev && ev.actor !== "auditor" && ev.actor !== "system");
  const w = { ...DEFAULT_WEIGHTS, ...(weights || {}) };

  const compromised = new Set();
  for (const ev of list) {
    if (ev.actor !== "red") continue;
    const r = ev.result || {};
    const host = r.host || (ev.args || {}).host;
    if (ev.tool === "try_credential" && r.authenticated === true && host) compromised.add(host);
    if (ev.tool === "read_file" && r.ok === true && String(r.path || "").startsWith("/secret/") && host) {
      compromised.add(host);
    }
  }
  const secret = secretRead(list);

  let firstCompromiseAt = null;
  list.forEach((ev, i) => {
    if (firstCompromiseAt !== null || ev.actor !== "red") return;
    const r = ev.result || {};
    if (ev.tool === "try_credential" && r.authenticated === true) firstCompromiseAt = i + 1;
    if (ev.tool === "read_file" && r.ok === true && String(r.path || "").startsWith("/secret/")) {
      firstCompromiseAt = i + 1;
    }
  });

  let firstRedAt = null;
  list.forEach((ev, i) => {
    if (firstRedAt === null && ev.actor === "red") firstRedAt = i + 1;
  });

  let timeToDetect = null;
  list.forEach((ev, i) => {
    if (timeToDetect !== null) return;
    if (ev.actor === "blue" && ev.tool === "report_finding" && (ev.result || {}).ok === true) {
      timeToDetect = i + 1;
    }
  });

  let detectionDelay = null;
  if (firstRedAt !== null && timeToDetect !== null) detectionDelay = timeToDetect - firstRedAt;

  let timeToContain = null;
  if (compromised.size > 0 && firstCompromiseAt !== null) {
    list.forEach((ev, i) => {
      if (timeToContain !== null || i + 1 < firstCompromiseAt || ev.actor !== "blue") return;
      if (!CONTAINMENT_TOOLS.includes(ev.tool)) return;
      if ((ev.result || {}).ok === true) timeToContain = i + 1;
    });
  }

  let blueActed = false;
  for (const ev of list) {
    if (ev.actor !== "blue" || (ev.result || {}).ok !== true) continue;
    if (ev.tool === "report_finding" || CONTAINMENT_TOOLS.includes(ev.tool)) blueActed = true;
  }
  const outcome = secret ? "red_succeeded" : blueActed ? "blue_held" : "stalemate";

  let counted = list;
  const secretIndex = list.findIndex(
    (ev) =>
      ev.actor === "red" &&
      ev.tool === "read_file" &&
      (ev.result || {}).ok === true &&
      String((ev.result || {}).path || "").startsWith("/secret/"),
  );
  if (secretIndex >= 0) counted = list.slice(0, secretIndex + 1);

  const down = new Set();
  let downtimeCost = 0;
  for (const ev of counted) {
    const r = ev.result || {};
    if (r.ok === true) {
      const host = (ev.args || {}).host || r.host;
      if ((ev.tool === "isolate_host" || ev.tool === "patch_service") && host) down.add(host);
      if (ev.tool === "restore_host" && host) down.delete(host);
    }
    for (const host of down) downtimeCost += Number(w[host] || 0);
  }
  const totalWeight = Object.values(w).reduce((sum, v) => sum + (Number(v) || 0), 0);
  const maximum = totalWeight * counted.length;
  const downtimePct = maximum > 0 ? Math.round(Math.min(100, Math.max(0, (downtimeCost / maximum) * 100)) * 100) / 100 : 0;
  const blueScore = secret ? 0 : Math.round(Math.max(0, 100 - downtimePct) * 100) / 100;

  return {
    time_to_detect: timeToDetect,
    detection_delay: detectionDelay,
    time_to_contain: timeToContain,
    hosts_compromised: compromised.size,
    compromised_hosts: [...compromised].sort(),
    secret_exfiltrated: secret,
    outcome,
    total_events: list.length,
    downtime_cost: downtimeCost,
    downtime_pct: downtimePct,
    blue_score: blueScore,
  };
}

// One-line human summary of a tool result for stream cards.
export function shortResult(ev) {
  const r = ev.result || {};
  if (r.ok === false) return `failed: ${(r.error || "unknown error").slice(0, 90)}`;
  switch (ev.tool) {
    case "scan_host":
      return r.running ? `up, reachable=${r.reachable}, ports=${(r.ports || []).join(",") || "none"}` : "host is not up";
    case "http_request":
      return `status=${r.status} ${(r.body || "").slice(0, 80)}`;
    case "try_credential":
      return r.authenticated ? `signed in on ${r.host}` : `sign-in refused on ${r.host || ""}`.trim();
    case "read_file":
      return `${r.path} (${(r.content || "").length} chars)`;
    case "list_files":
      return `${r.path}: ${(r.files || []).length} entries`;
    case "report_finding":
      return `recorded as ${r.id}`;
    case "read_logs":
      return `${(r.lines || []).length} lines from ${r.host}`;
    case "list_processes":
      return `${(r.processes || []).length} processes on ${r.host}`;
    case "wait":
      return r.reason || "stood by";
    case "block_ip":
      return `blocked ${r.ip}`;
    case "isolate_host":
      return `${r.host} detached from the range`;
    case "patch_service":
      return `${r.service} on ${r.host}: ${r.detail || "patched"}`;
    case "restore_host":
      return `${r.host} restored to clean state`;
    case "agent_error":
      return (r.error || "agent turn failed").slice(0, 90);
    default:
      return JSON.stringify(r).slice(0, 100);
  }
}

// Format tool args for display, masking any password value.
export function formatArgs(args) {
  const masked = { ...(args || {}) };
  if ("password" in masked) masked.password = "...";
  if ("pass" in masked) masked.pass = "...";
  return JSON.stringify(masked);
}

// Error text that means an attempt never reached the host: the range
// refused it. Mirrors the blocked-attempt check in choreography.js,
// kept local so this module stays dependency-free.
const BLOCKED_ERROR = /outside|allowlist|sandbox|blocked|off-range|off range|unreachable|cut off|detached|no route/i;

function isBlockedResult(ev) {
  const r = (ev && ev.result) || {};
  if (r.ok !== false) return false;
  const host = eventHost(ev);
  if (host && !HOSTS.includes(host)) return true;
  return BLOCKED_ERROR.test(String(r.error || ""));
}

function isAdminRead(ev) {
  if (!ev || ev.actor !== "red" || ev.tool !== "http_request") return false;
  if ((ev.result || {}).ok !== true) return false;
  return String((ev.args || {}).path || "").startsWith("/admin");
}

function authHost(ev) {
  if (!ev || ev.tool !== "try_credential") return null;
  if ((ev.result || {}).authenticated !== true) return null;
  return eventHost(ev);
}

function isConfigRead(ev) {
  if (!ev || ev.actor !== "red" || ev.tool !== "read_file") return false;
  if ((ev.result || {}).ok !== true) return false;
  if (isSecretReadEvent(ev)) return false;
  return String((ev.result || {}).path || (ev.args || {}).path || "").toLowerCase().includes("config");
}

function isSecretReadEvent(ev) {
  return Boolean(
    ev && ev.actor === "red" && ev.tool === "read_file" &&
    (ev.result || {}).ok === true &&
    String((ev.result || {}).path || "").startsWith("/secret/"),
  );
}

// Contest pressure as a two-sided value in [-1, 1]: +1 means red
// owns the exercise, -1 means blue holds it fully. Red milestones
// push toward red weighted by progress; effective blue answers push
// back; the value eases toward zero with every passing event.
export function pressureValue(eventList) {
  const DECAY = 0.97;
  let value = 0;
  const redTouched = new Set();
  for (const ev of eventList || []) {
    if (!ev) continue;
    value *= DECAY;
    const r = ev.result || {};
    if (ev.actor === "red") {
      const host = eventHost(ev);
      if (host) redTouched.add(host);
      if (isSecretReadEvent(ev)) {
        value = 1;
      } else if (ev.tool === "try_credential" && r.authenticated === true) {
        value += eventHost(ev) === "db" ? 0.35 : 0.3;
      } else if (isAdminRead(ev)) {
        value += 0.3;
      } else if (isConfigRead(ev)) {
        value += 0.25;
      } else if (isBlockedResult(ev)) {
        value -= 0.1;
      }
    } else if (ev.actor === "blue" && r.ok === true) {
      if (ev.tool === "report_finding" && (ev.evidence_refs || []).length > 0) {
        value -= 0.15;
      } else if (ev.tool === "isolate_host" || ev.tool === "patch_service") {
        if (redTouched.has(eventHost(ev))) value -= 0.3;
      }
    }
    if (value > 1) value = 1;
    if (value < -1) value = -1;
  }
  return value;
}

// Red progress along the four-notch chain: admin page leak, app
// sign-in, database sign-in, secret read. Each notch names the host
// red relied on; a later blue isolate or patch on that host drops
// the notch until red earns it again. Level is the longest held
// prefix, so the bar only ever shows solid ground.
export const RED_NOTCHES = [
  { key: "admin", label: "Admin page leak", host: "web" },
  { key: "app", label: "App sign-in", host: "app" },
  { key: "db", label: "Database sign-in", host: "db" },
  { key: "secret", label: "Secret read", host: "db" },
];

function notchAchieved(index, ev) {
  if (index === 0) return isAdminRead(ev);
  if (index === 1) return authHost(ev) === "app";
  if (index === 2) return authHost(ev) === "db";
  return isSecretReadEvent(ev);
}

export function redProgress(eventList) {
  const achievedAt = [null, null, null, null];
  const containedAt = { web: -1, app: -1, db: -1, workstation: -1 };
  (eventList || []).forEach((ev, i) => {
    if (!ev) return;
    for (let n = 0; n < RED_NOTCHES.length; n += 1) {
      if (notchAchieved(n, ev)) achievedAt[n] = i;
    }
    const r = ev.result || {};
    if (ev.actor === "blue" && r.ok === true &&
        (ev.tool === "isolate_host" || ev.tool === "patch_service")) {
      const host = eventHost(ev);
      if (host && host in containedAt) containedAt[host] = i;
    }
  });
  const notches = RED_NOTCHES.map((notch, n) => {
    if (achievedAt[n] === null) return false;
    return containedAt[notch.host] < achievedAt[n];
  });
  let level = 0;
  while (level < notches.length && notches[level]) level += 1;
  return { level, notches };
}

// Blue network held blocks, one per host: clean (blue holds it),
// breached (red is in), isolated (cut off, drawn striped), patched
// (sign-in rotated, drawn solid). Probed or restored hosts count as
// clean: blue still holds them.
export function hostBlocks(eventList) {
  const states = reduceHostStates(eventList);
  const lastContain = {};
  for (const ev of eventList || []) {
    if (!ev || ev.actor !== "blue" || (ev.result || {}).ok !== true) continue;
    if (ev.tool !== "isolate_host" && ev.tool !== "patch_service") continue;
    const host = eventHost(ev);
    if (host) lastContain[host] = ev.tool;
  }
  const blocks = {};
  for (const host of HOSTS) {
    const state = states[host];
    if (state === "signed-in" || state === "compromised") blocks[host] = "breached";
    else if (state === "isolated") blocks[host] = lastContain[host] === "patch_service" ? "patched" : "isolated";
    else blocks[host] = "clean";
  }
  return blocks;
}
