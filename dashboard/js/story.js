// Plain-English event sentences for the Story view. Pure functions only:
// no DOM access, no model calls, so node can test this module. Copy
// rules: Operations Center vocabulary, cyber defense framing,
// credentials never shown.
import { BOOT_EXPECTED_SEC, formatBootLine } from "./state.js";

export function actorName(ev) {
  if (!ev) return "Someone";
  if (ev.actor === "red") return "Red";
  if (ev.actor === "blue") return "Blue";
  if (ev.actor === "auditor") return "Auditor";
  if (ev.actor === "human") return "Operator";
  return "Someone";
}

function hostOf(ev) {
  const host = (ev.args && ev.args.host) || (ev.result && ev.result.host);
  return host || "a host";
}

function failed(ev) {
  return (ev.result || {}).ok === false;
}

function failText(ev) {
  const err = String((ev.result || {}).error || "").slice(0, 80);
  return err ? ` (${err})` : "";
}

// Tools with their own sentence below; anything else a human says
// stays an operator note.
const TOOL_SENTENCES = new Set([
  "scan_host", "http_request", "try_credential", "read_file",
  "list_files", "report_finding", "read_logs", "list_processes",
  "wait", "block_ip", "isolate_host", "patch_service", "restore_host",
]);

// One plain sentence per tool event, using only fields in the event.
export function describeEvent(ev) {
  if (!ev || typeof ev !== "object") return "Something happened.";
  const humanTook = ev.actor === "human" || ev.human === true;
  const who = humanTook ? "Human defender" : actorName(ev);
  const host = hostOf(ev);
  const args = ev.args || {};
  const result = ev.result || {};
  const tool = ev.tool || "unknown";

  if (ev.actor === "human" && !TOOL_SENTENCES.has(tool)) {
    const said = args.text || args.command || result.text || "";
    const clipped = String(said).slice(0, 120);
    return clipped ? `Operator note: ${clipped}` : "Operator added a note.";
  }

  if (ev.actor === "auditor" || tool === "audit") {
    const verdict = result.verdict || "judged";
    const target = result.target_event_id || args.target_event_id || "";
    const score = typeof result.score === "number" ? ` (${result.score}/10)` : "";
    return target
      ? `Auditor judged ${target} ${verdict}${score}.`
      : `Auditor recorded a ${verdict} verdict${score}.`;
  }

  if (tool === "agent_error") {
    return `${who} hit an error and skipped a turn.`;
  }

  switch (tool) {
    case "scan_host":
      if (ev.actor === "blue" || humanTook) {
        if (failed(ev)) return `${who} tried to check ${host} but the check failed${failText(ev)}.`;
        if (result.running === false) return `${who} checked ${host}, which is down.`;
        return `${who} checked whether ${host} is up.`;
      }
      if (failed(ev)) return `Red tried to check ${host} but the check failed${failText(ev)}.`;
      if (result.running === false) return `Red checked ${host}, which is down.`;
      return `Red is checking whether ${host} is up.`;
    case "http_request": {
      const path = args.path || "a page";
      if (failed(ev)) return `Red tried to ask ${host} for ${path} but it failed${failText(ev)}.`;
      const status = result.status !== undefined && result.status !== null ? ` (got ${result.status})` : "";
      return `Red asked ${host} for ${path}${status}.`;
    }
    case "try_credential": {
      const user = args.user || "an account";
      if (result.authenticated === true) return `Red signed in to ${host} as ${user}.`;
      if (String(result.note || "").includes("no sign-in gate")) {
        return `Red tried ${host}, which has no sign-in.`;
      }
      return `Red is trying to sign in to ${host}.`;
    }
    case "read_file": {
      const path = args.path || result.path || "a file";
      const secret = String(result.path || args.path || "").startsWith("/secret/");
      if (failed(ev)) return `Red tried to read ${path} on ${host} but could not${failText(ev)}.`;
      if (secret && result.ok === true) return `Red read the protected file on ${host}.`;
      return `Red read ${path} on ${host}.`;
    }
    case "list_files": {
      const path = args.path || result.path || "a folder";
      if (failed(ev)) return `Red tried to list ${path} on ${host} but could not${failText(ev)}.`;
      return `Red listed files in ${path} on ${host}.`;
    }
    case "report_finding": {
      const text = String(args.text || "").slice(0, 90);
      if (who === "Blue" || humanTook) return text ? `${who} reported: ${text}` : `${who} reported a finding.`;
      return text ? `Red noted: ${text}` : "Red recorded a note.";
    }
    case "read_logs":
      if (failed(ev)) return `${who} tried to check logs on ${host} but could not${failText(ev)}.`;
      return `${who} checked recent logs on ${host}.`;
    case "list_processes":
      if (failed(ev)) return `${who} tried to check programs on ${host} but could not${failText(ev)}.`;
      return `${who} checked running programs on ${host}.`;
    case "wait":
      return `${who} waited this round.`;
    case "block_ip": {
      const ip = args.ip || result.ip || "an address";
      if (failed(ev)) return `${who} tried to block ${ip} but it failed${failText(ev)}.`;
      return `${who} blocked network traffic from ${ip}.`;
    }
    case "isolate_host":
      if (failed(ev)) return `${who} tried to isolate ${host} but it failed${failText(ev)}.`;
      return `${who} isolated ${host}.`;
    case "patch_service": {
      const service = args.service || result.service || "a service";
      if (failed(ev)) return `${who} tried to patch ${service} on ${host} but it failed${failText(ev)}.`;
      return `${who} patched ${service} on ${host}.`;
    }
    case "restore_host": {
      if (failed(ev)) return `${who} tried to restore ${host} but it failed${failText(ev)}.`;
      return `${who} restored ${host}.`;
    }
    default:
      if (failed(ev)) return `${who} tried ${tool} but it failed${failText(ev)}.`;
      return `${who} ran ${tool}.`;
  }
}

const OUTCOME_SENTENCE = {
  blue_held: "Exercise over: Blue held.",
  red_succeeded: "Exercise over: Red read the protected file.",
  stalemate: "Exercise over: no result.",
};

// Header status sentence: latest red/blue activity, or the final outcome.
// Callers pass the honest display label when the exercise ends; the
// server-outcome sentences stay as the fallback. While booting with
// timing, the line counts up to the expected 40 seconds.
export function statusSentence({ latest, ended, outcome, mode, display, bootStartedAt, nowMs }) {
  if (ended) {
    if (display) return `Exercise over: ${display}.`;
    return OUTCOME_SENTENCE[outcome] || "Exercise over.";
  }
  if (mode === "booting") {
    const start = Number(bootStartedAt);
    const now = Number(nowMs);
    if (Number.isFinite(start) && start > 0 && Number.isFinite(now)) {
      const elapsed = Math.max(0, Math.floor((now - start) / 1000));
      return formatBootLine(elapsed, BOOT_EXPECTED_SEC);
    }
    return "Starting the range…";
  }
  if (!latest) {
    if (mode === "live" || mode === "replay") return "Waiting for the first move…";
    return "No exercise loaded.";
  }
  return describeEvent(latest);
}

// Blue moves that show blue noticed something: looks, reports,
// and every containment action. A plain wait is not detection.
const BLUE_LOOKS = [
  "read_logs", "report_finding", "list_processes",
  "isolate_host", "patch_service", "restore_host", "block_ip",
];

// Key moments for the Story timeline: first sign-in, first sign
// blue noticed, first containment, first secret read. Each carries
// its move index so the timeline can frame it, in move order.
// Detection folds into containment when blue's first noticing move
// is the containment itself. Pure.
export function keyMoments(events) {
  const list = events || [];
  const find = (pred) => list.findIndex((ev) => ev && pred(ev));
  const signin = find((ev) => ev.actor === "red" && ev.tool === "try_credential" &&
    (ev.result || {}).authenticated === true);
  const detection = find((ev) => ev.actor === "blue" && BLUE_LOOKS.includes(ev.tool));
  const containment = find((ev) => ev.actor === "blue" &&
    (ev.tool === "isolate_host" || ev.tool === "patch_service") && (ev.result || {}).ok === true);
  const secret = find((ev) => ev.actor === "red" && ev.tool === "read_file" &&
    (ev.result || {}).ok === true &&
    String((ev.result || {}).path || "").startsWith("/secret/"));
  const out = [];
  const push = (key, index, title) => {
    if (index < 0) return;
    out.push({
    key, index, title,
    caption: describeEvent(list[index]),
    reasoning: String(list[index].reasoning_summary || ""),
  });
  };
  push("signin", signin, "First sign-in");
  if (detection >= 0 && detection !== containment) push("detection", detection, "Detection");
  push("containment", containment, "Containment");
  push("secret", secret, "Secret read");
  return out.sort((a, b) => a.index - b.index);
}

// Group red/blue items into rounds (one red move plus one blue move),
// newest round first. Auditor items are skipped; the feed shows them
// as icons on the blue items they judge instead.
export function groupRounds(items) {
  const rounds = [];
  const lane = (items || []).filter(({ ev }) => ev && (ev.actor === "red" || ev.actor === "blue"));
  lane.forEach(({ ev, index }, k) => {
    // Input is newest first; round from the oldest-first position so
    // a trailing lone move still lands in the latest round.
    const oldestFirst = lane.length - 1 - k;
    const round = Math.floor(oldestFirst / 2) + 1;
    let group = rounds.find((g) => g.round === round);
    if (!group) {
      group = { round, items: [] };
      rounds.push(group);
    }
    group.items.push({ ev, index });
  });
  return rounds;
}
