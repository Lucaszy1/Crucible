// Evidence chips plus per-host log viewer with cited-line highlight.
import { HOSTS } from "./state.js";

let activeHost = "web";
let focusRef = null;

export function setActiveHost(host) {
  activeHost = host;
}

export function focusEvidenceRef(ref) {
  focusRef = ref;
  const [host] = String(ref).split(":");
  if (host && HOSTS.includes(host)) activeHost = host;
}

// Merge every read_logs window per host, newest line text wins per id.
export function collectLogs(events, cursor) {
  const perHost = new Map(HOSTS.map((host) => [host, new Map()]));
  for (const ev of events.slice(0, cursor)) {
    if (ev.tool !== "read_logs" || (ev.result || {}).ok !== true) continue;
    const lines = (ev.result || {}).lines || [];
    for (const row of lines) {
      const id = row.id || "";
      const [host] = id.split(":");
      if (perHost.has(host)) perHost.get(host).set(id, row.line || "");
    }
  }
  return perHost;
}

export function citedRefs(events, cursor) {
  const refs = [];
  for (const ev of events.slice(0, cursor)) {
    for (const ref of ev.evidence_refs || []) {
      if (!refs.includes(ref)) refs.push(ref);
    }
  }
  return refs.slice(-12).reverse();
}

export function renderEvidence(els, events, cursor) {
  const refs = citedRefs(events, cursor);
  els.chips.innerHTML = "";
  if (refs.length === 0) {
    const idle = document.createElement("span");
    idle.className = "empty";
    idle.textContent = "Cited log lines appear here.";
    els.chips.append(idle);
  }
  for (const ref of refs) {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.textContent = ref;
    chip.addEventListener("click", () => {
      focusEvidenceRef(ref);
      renderEvidence(els, events, cursor);
    });
    els.chips.append(chip);
  }

  const perHost = collectLogs(events, cursor);
  els.tabs.innerHTML = "";
  for (const host of HOSTS) {
    const tab = document.createElement("button");
    tab.textContent = host;
    tab.className = activeHost === host ? "active" : "";
    tab.setAttribute("role", "tab");
    tab.addEventListener("click", () => {
      activeHost = host;
      focusRef = null;
      renderEvidence(els, events, cursor);
    });
    els.tabs.append(tab);
  }

  els.viewer.innerHTML = "";
  const lines = [...(perHost.get(activeHost) || new Map()).entries()].sort(([a], [b]) =>
    a.localeCompare(b, undefined, { numeric: true }),
  );
  if (lines.length === 0) {
    const idle = document.createElement("p");
    idle.className = "empty";
    idle.textContent = "Host logs appear here.";
    els.viewer.append(idle);
    return;
  }
  const cited = new Set(refs);
  let focusEl = null;
  for (const [id, text] of lines) {
    const row = document.createElement("div");
    row.className = "log-line" + (cited.has(id) ? " hit" : "");
    row.id = `log-${id}`;
    const lid = document.createElement("span");
    lid.className = "lid";
    lid.textContent = id;
    row.append(lid, document.createTextNode(text));
    els.viewer.append(row);
    if (id === focusRef) focusEl = row;
  }
  if (focusEl) focusEl.scrollIntoView({ block: "center" });
}
