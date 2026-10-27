// Plain-English event sentences for the Story view. Pure functions only:
// no DOM access, no model calls, so node can test this module. Copy
// rules: Operations Center vocabulary, no war wording (no attack,
// battle, enemy, kill, war), credentials never shown.

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

// One plain sentence per tool event, using only fields in the event.
export function describeEvent(ev) {
  if (!ev || typeof ev !== "object") return "Something happened.";
  const who = actorName(ev);
  const host = hostOf(ev);
  const args = ev.args || {};
  const result = ev.result || {};
  const tool = ev.tool || "unknown";

  if (ev.actor === "human") {
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
      if (who === "Blue") return text ? `Blue reported: ${text}` : "Blue reported a finding.";
      return text ? `Red noted: ${text}` : "Red recorded a note.";
    }
    case "read_logs":
      if (failed(ev)) return `Blue tried to check logs on ${host} but could not${failText(ev)}.`;
      return `Blue checked recent logs on ${host}.`;
    case "list_processes":
      if (failed(ev)) return `Blue tried to check programs on ${host} but could not${failText(ev)}.`;
      return `Blue checked running programs on ${host}.`;
    case "wait":
      return "Blue waited this round.";
    case "block_ip": {
      const ip = args.ip || result.ip || "an address";
      if (failed(ev)) return `Blue tried to block ${ip} but it failed${failText(ev)}.`;
      return `Blue blocked network traffic from ${ip}.`;
    }
    case "isolate_host":
      if (failed(ev)) return `Blue tried to isolate ${host} but it failed${failText(ev)}.`;
      return `Blue isolated ${host}.`;
    case "patch_service": {
      const service = args.service || result.service || "a service";
      if (failed(ev)) return `Blue tried to patch ${service} on ${host} but it failed${failText(ev)}.`;
      return `Blue patched ${service} on ${host}.`;
    }
    case "restore_host":
      if (failed(ev)) return `Blue tried to restore ${host} but it failed${failText(ev)}.`;
      return `Blue restored ${host}.`;
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
export function statusSentence({ latest, ended, outcome, mode }) {
  if (ended) return OUTCOME_SENTENCE[outcome] || "Exercise over.";
  if (mode === "booting") return "Starting the range…";
  if (!latest) {
    if (mode === "live" || mode === "replay") return "Waiting for the first move…";
    return "No exercise loaded.";
  }
  return describeEvent(latest);
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
