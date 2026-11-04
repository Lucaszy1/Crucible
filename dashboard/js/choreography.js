// Arena choreography: pure mapping from one event plus current state
// to a list of animation steps. No DOM access, so node can test it.
// Copy rules: exercise, contest, containment vocabulary only; the
// banner and mark words below are the only Arena copy this module owns.
import { HOSTS, RECON_TOOLS, CONTAINMENT_TOOLS, eventHost } from "./state.js";

export const ARENA_HOSTS = HOSTS;

const BLOCKED_ERROR = /outside|allowlist|sandbox|blocked|off-range|off range|unreachable|cut off|detached|no route/i;

// A failed attempt that never reached the host: off-range host or a
// network-level refusal. Sign-in refusals and missing files are not
// blocked; they get their own Denied or fail beat. Hosts default to
// the classic four; generated exercises pass their own.
export function isBlockedAttempt(ev, states, hosts) {
  if (!ev || (ev.result || {}).ok !== false) return false;
  const names = Array.isArray(hosts) && hosts.length > 0 ? hosts : HOSTS;
  const host = eventHost(ev);
  if (host && !names.includes(host)) return true;
  const err = String((ev.result || {}).error || "");
  if (BLOCKED_ERROR.test(err)) return true;
  if (host && states && states[host] === "isolated" && ev.actor === "red") return true;
  return false;
}

export function isSecretRead(ev) {
  return Boolean(
    ev && ev.actor === "red" && ev.tool === "read_file" &&
    (ev.result || {}).ok === true &&
    String((ev.result || {}).path || "").startsWith("/secret/"),
  );
}

function evidenceHost(ev, hosts) {
  const names = Array.isArray(hosts) && hosts.length > 0 ? hosts : HOSTS;
  const refs = ev.evidence_refs || [];
  for (const ref of refs) {
    const host = String(ref).split(":")[0];
    if (names.includes(host)) return host;
  }
  return null;
}

// Target host for effects that need one. report_finding carries no
// host arg, so fall back to its first cited host. Off-range hosts
// are not valid targets and resolve to null.
export function effectHost(ev, hosts) {
  const names = Array.isArray(hosts) && hosts.length > 0 ? hosts : HOSTS;
  const direct = eventHost(ev);
  if (direct && names.includes(direct)) return direct;
  return evidenceHost(ev, names) || null;
}

function blockedSteps(ev, hosts) {
  return [{ effect: "bounce-blocked", target: effectHost(ev, hosts) || "stage", mark: "Blocked" }];
}

// One event plus current host states to an ordered step list. Each
// step is { effect, target?, secondary? } plus effect-specific fields.
// secondary steps are decorative and skipped at 4x playback.
// ctx.hosts names the range hosts; without it the classic four apply.
export function choreograph(ev, ctx) {
  const states = (ctx && ctx.states) || null;
  const hosts = (ctx && ctx.hosts) || null;
  if (!ev || typeof ev !== "object") return [{ effect: "idle-tick", secondary: true }];
  if (isBlockedAttempt(ev, states, hosts)) return blockedSteps(ev, hosts);

  const tool = ev.tool || "";
  const result = ev.result || {};
  const ok = result.ok === true;
  const target = effectHost(ev, hosts);

  switch (tool) {
    case "scan_host":
      if (!ok) return [{ effect: "scan-fail", target, secondary: true }];
      return [
        { effect: "scan-ring", from: "red", target },
        { effect: "target-ping", target, secondary: true },
      ];
    case "http_request":
      if (!ok) return [{ effect: "packet-fail", from: "red", target }];
      return [
        { effect: "packet", from: "red", target, shape: "dot" },
        { effect: "target-ping", target, secondary: true },
      ];
    case "try_credential":
      if (result.authenticated === true) {
        return [
          { effect: "key-through", from: "red", target },
          { effect: "flash-red", target, secondary: true },
          { effect: "foothold", target },
        ];
      }
      return [
        { effect: "key-try", from: "red", target },
        { effect: "key-bounce", target, secondary: true },
        { effect: "denied", target, mark: "Denied" },
      ];
    case "list_files":
    case "read_file": {
      if (!ok) {
        const err = String(result.error || "").toLowerCase();
        if (err.includes("sign-in") || err.includes("signin") || err.includes("auth")) {
          return [{ effect: "denied", target, mark: "Denied" }];
        }
        return [{ effect: "file-fail", target }];
      }
      const steps = [{ effect: "file-stream", from: target, to: "red" }];
      if (isSecretRead(ev)) {
        steps.push(
          { effect: "crack", target },
          { effect: "vignette-red" },
          { effect: "banner", text: "Secret exposed" },
          { effect: "foothold", target, secondary: true },
        );
      }
      return steps;
    }
    case "read_logs":
      if (!ok) return [{ effect: "beam-fail", from: "blue", target }];
      return [{ effect: "log-beam", from: "blue", target }];
    case "list_processes":
      if (!ok) return [{ effect: "beam-fail", from: "blue", target }];
      return [{ effect: "log-beam", from: "blue", target, variant: "process" }];
    case "report_finding":
      if (!ok) return [{ effect: "idle-tick", secondary: true }];
      return [
        { effect: "alert-pulse", target },
        { effect: "flag", target },
      ];
    case "isolate_host":
      if (!ok) return [{ effect: "isolate-fail", target }];
      return [
        { effect: "isolate-snap", target },
        { effect: "barrier", target },
        { effect: "shield", target },
      ];
    case "patch_service":
      if (!ok) return [{ effect: "patch-fail", target }];
      return [
        { effect: "wrench", target },
        { effect: "ring-blue", target },
        { effect: "check", target, mark: "Patched" },
      ];
    case "restore_host":
      if (!ok) return [{ effect: "restore-fail", target }];
      return [
        { effect: "green-sweep", target },
        { effect: "reconnect", target },
      ];
    case "block_ip":
      if (!ok) return [{ effect: "block-fail", target: "stage" }];
      return [{ effect: "block-pulse", ip: result.ip || (ev.args || {}).ip || null }];
    case "wait":
      return [{ effect: "idle-tick" }];
    case "agent_error":
      return [{ effect: "idle-tick", secondary: true }];
    case "audit":
      return [{
        effect: "verdict",
        target: result.target_event_id || (ev.args || {}).target_event_id || null,
        verdict: result.verdict || null,
        word: verdictWord(result.verdict),
      }];
    default:
      if (!ok) return [{ effect: "idle-tick", secondary: true }];
      if (target) {
        return [
          { effect: "packet", from: ev.actor === "blue" ? "blue" : "red", target, shape: "dot" },
          { effect: "target-ping", target, secondary: true },
        ];
      }
      return [{ effect: "idle-tick", secondary: true }];
  }
}

// Auditor verdicts fold to three Arena words.
export function verdictWord(verdict) {
  if (verdict === "justified") return "justified";
  if (verdict === "unsupported") return "flagged";
  if (verdict) return "caution";
  return null;
}

// Pressure bar, purely from events: red progress along the chain
// (recon, sign-in, secret listing, secret read) versus blue
// detection plus containment. Both 0-100.
export function pressureFromEvents(eventList) {
  let red = 0;
  let detected = false;
  let contained = false;
  for (const ev of eventList || []) {
    if (!ev) continue;
    const r = ev.result || {};
    if (ev.actor === "red" && r.ok === true) {
      if (RECON_TOOLS.includes(ev.tool) || ev.tool === "http_request") red = Math.max(red, 25);
      if (ev.tool === "try_credential" && r.authenticated === true) red = Math.max(red, 50);
      if ((ev.tool === "list_files" || ev.tool === "read_file") &&
        String(r.path || "").startsWith("/secret")) red = Math.max(red, 75);
      if (isSecretRead(ev)) red = 100;
    }
    if (ev.actor === "blue" && r.ok === true) {
      if (ev.tool === "report_finding") detected = true;
      if (CONTAINMENT_TOOLS.includes(ev.tool)) contained = true;
    }
  }
  return { red, blue: (detected ? 50 : 0) + (contained ? 50 : 0) };
}

// Per-host integrity, purely from events: red ground drops it,
// restore_host refills it. 0-100 per host. Hosts default to the
// classic four; generated exercises pass their own.
export function integrityFromEvents(eventList, hosts) {
  const names = Array.isArray(hosts) && hosts.length > 0 ? hosts : HOSTS;
  const integrity = {};
  for (const host of names) integrity[host] = 100;
  for (const ev of eventList || []) {
    if (!ev) continue;
    const host = eventHost(ev);
    if (!host || !(host in integrity)) continue;
    const r = ev.result || {};
    if (ev.actor === "red" && r.ok === true) {
      if (isSecretRead(ev)) {
        integrity[host] = 0;
      } else if (ev.tool === "try_credential" && r.authenticated === true) {
        integrity[host] = Math.min(integrity[host], 50);
      } else if (RECON_TOOLS.includes(ev.tool) || ev.tool === "http_request") {
        if (integrity[host] === 100) integrity[host] = 85;
      }
    } else if (ev.actor === "blue" && ev.tool === "restore_host" && r.ok === true) {
      integrity[host] = 100;
    }
  }
  return integrity;
}

// Latest red/blue event in a slice for the Arena consoles.
export function latestByActor(eventList, actor) {
  const list = eventList || [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i] && list[i].actor === actor) return list[i];
  }
  return null;
}

// Hostnames or account names a page leaked to red, for floating
// text. The admin page names its quoted hints; any other page only
// leaks a host it names as a whole word. Never passwords. Hosts
// default to the classic four; generated exercises pass their own.
export function httpLeakTokens(ev, hosts) {
  if (!ev || ev.tool !== "http_request" || (ev.result || {}).ok !== true) return [];
  const body = String((ev.result || {}).body || "");
  if (!body) return [];
  const names = Array.isArray(hosts) && hosts.length > 0 ? hosts : HOSTS;
  const path = String((ev.args || {}).path || "");
  const tokens = [];
  if (path.startsWith("/admin")) {
    for (const match of body.matchAll(/'([^']{2,28})'/g)) {
      const token = match[1].trim();
      if (token && !tokens.includes(token)) tokens.push(token);
      if (tokens.length >= 3) break;
    }
    return tokens;
  }
  for (const host of names) {
    if (tokens.length >= 2) break;
    const safe = String(host).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (new RegExp(`\\b${safe}\\b`).test(body) && !tokens.includes(host)) tokens.push(host);
  }
  return tokens;
}

// Cited log line ids from a blue log read, newest few in order.
export function logLineIds(ev, max) {
  if (!ev || ev.tool !== "read_logs" || (ev.result || {}).ok !== true) return [];
  const lines = (ev.result || {}).lines || [];
  const count = Math.max(1, Math.min(6, Number(max) || 4));
  return lines.slice(-count).map((line) => String((line && line.id) || "")).filter(Boolean);
}

// Auditor verdict as card data: score, plain word, cited ids,
// rationale. Null when the event carries no verdict.
export function verdictCard(ev) {
  if (!ev) return null;
  const result = ev.result || {};
  const word = verdictWord(result.verdict);
  if (!word) return null;
  const ids = (result.evidence_checked || ev.evidence_refs || []).slice(0, 4).map(String);
  return {
    word,
    verdict: result.verdict,
    score: typeof result.score === "number" ? result.score : null,
    target: result.target_event_id || (ev.args || {}).target_event_id || null,
    ids,
    rationale: String(result.rationale || ""),
  };
}

// Most recent auditor verdict at or before the cursor. The verdict
// card follows this, not a playback timer, so pause and scrub keep
// the card and it clears only before its event. Pure.
export function latestAuditorEvent(eventList, cursor) {
  const list = eventList || [];
  const at = Math.max(0, Math.min(list.length, cursor || 0));
  for (let i = at - 1; i >= 0; i -= 1) {
    const ev = list[i];
    if (ev && ev.actor === "auditor" && verdictCard(ev)) return ev;
  }
  return null;
}
