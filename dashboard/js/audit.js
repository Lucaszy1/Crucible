// Auditor verdicts: reducer plus panel and chip rendering.
// The reducer is pure (node-testable) and reads the same event stream
// as every other panel, so live and replay stay identical.

export const VERDICT_TONES = {
  justified: "green",
  premature: "amber",
  disproportionate: "amber",
  unsupported: "red",
  late: "grey",
  redundant: "cyan",
  moot: "muted",
};

export function verdictTone(verdict) {
  return VERDICT_TONES[verdict] || "grey";
}

// Collect auditor events visible at the cursor, keyed by target event.
export function reduceAudits(eventList, cursor) {
  const slice = (eventList || []).slice(0, cursor);
  const byTarget = new Map();
  for (const ev of slice) {
    if (!ev || ev.actor !== "auditor") continue;
    const target = (ev.result || {}).target_event_id || (ev.args || {}).target_event_id;
    if (target) byTarget.set(target, ev);
  }
  const audits = [...byTarget.values()];
  let justified = 0;
  let unsupported = 0;
  let invalidRefs = 0;
  let scoreSum = 0;
  let scoreCount = 0;
  let cost = 0;
  const verdictCounts = {};
  for (const ev of audits) {
    const r = ev.result || {};
    if (r.verdict === "justified") justified += 1;
    if (r.verdict === "unsupported") unsupported += 1;
    if (r.verdict) verdictCounts[r.verdict] = (verdictCounts[r.verdict] || 0) + 1;
    if (r.refs_valid === false) invalidRefs += 1;
    if (typeof r.score === "number") {
      scoreSum += r.score;
      scoreCount += 1;
    }
    if (typeof r.estimated_cost_usd === "number") cost += r.estimated_cost_usd;
  }
  return {
    byTarget,
    audits,
    summary: {
      count: audits.length,
      justifiedPct: audits.length > 0 ? Math.round((justified / audits.length) * 10000) / 100 : null,
      unsupportedCount: unsupported,
      invalidRefsCount: invalidRefs,
      meanScore: scoreCount > 0 ? Math.round((scoreSum / scoreCount) * 100) / 100 : null,
      verdictCounts,
      cost: Math.round(cost * 1000000) / 1000000,
    },
  };
}

export function verdictChip(verdict, score) {
  const chip = document.createElement("span");
  chip.className = "verdict";
  chip.dataset.tone = verdictTone(verdict);
  chip.textContent = score === null || score === undefined ? verdict : `${verdict} ${score}/10`;
  const num = score === null || score === undefined ? "" : ` ${score}/10`;
  chip.title = `Auditor verdict: ${verdict}${num}. The auditor checks if the blue move was fair.`;
  return chip;
}

function auditRow(ev, targetIndex, onRef) {
  const r = ev.result || {};
  const wrap = document.createElement("article");
  wrap.className = "audit-row";
  wrap.dataset.target = (ev.result || {}).target_event_id || "";

  const head = document.createElement("div");
  head.className = "card-head";
  const num = document.createElement("span");
  num.className = "card-index";
  num.textContent = targetIndex ? `#${targetIndex}` : (r.target_event_id || "audit");
  head.append(num, verdictChip(r.verdict, r.score));
  if (r.refs_valid === false) {
    const flag = document.createElement("span");
    flag.className = "verdict-flag";
    flag.textContent = "refs invalid";
    flag.title = "Cited log lines were not visible to blue at that time";
    head.append(flag);
  }
  wrap.append(head);

  if (r.rationale) {
    const reason = document.createElement("div");
    reason.className = "card-reason";
    reason.textContent = r.rationale;
    wrap.append(reason);
  }

  const refs = r.evidence_checked || ev.evidence_refs || [];
  if (refs.length > 0) {
    const box = document.createElement("div");
    box.className = "card-evidence";
    for (const ref of refs.slice(0, 5)) {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.textContent = ref;
      chip.addEventListener("click", () => onRef(ref));
      box.append(chip);
    }
    wrap.append(box);
  } else {
    wrap.addEventListener("click", () => {
      const first = (r.evidence_checked || [])[0];
      if (first) onRef(first);
    });
  }
  return wrap;
}

export function renderAuditor(els, eventList, cursor, reduced, onRef) {
  const { byTarget, audits, summary } = reduced;
  void byTarget;
  if (els.count) els.count.textContent = String(summary.count);

  els.chips.innerHTML = "";
  const justified = document.createElement("span");
  justified.className = "chip static";
  justified.dataset.tone = "green";
  justified.textContent = summary.justifiedPct === null ? "Justified --" : `Justified ${summary.justifiedPct}%`;
  const invalid = document.createElement("span");
  invalid.className = "chip static";
  invalid.dataset.tone = summary.invalidRefsCount > 0 ? "red" : "green";
  invalid.textContent = `Invalid refs ${summary.invalidRefsCount}`;
  els.chips.append(justified, invalid);
  if (summary.count > 0) {
    const parts = [`${summary.count} verdict${summary.count === 1 ? "" : "s"}`];
    if (summary.meanScore !== null) parts.push(`mean ${summary.meanScore}/10`);
    const counts = Object.entries(summary.verdictCounts)
      .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(b[0]))
      .map(([verdict, n]) => `${verdict} ${n}`)
      .join(", ");
    if (counts) parts.push(counts);
    els.summary.textContent = parts.join(" · ");
  } else {
    els.summary.textContent = "No verdicts yet.";
  }
  els.cost.textContent = summary.count > 0 ? `Audit cost: $${summary.cost.toFixed(4)}` : "Audit cost: --";

  els.list.innerHTML = "";
  if (audits.length === 0) {
    const idle = document.createElement("p");
    idle.className = "empty";
    idle.textContent = "Verdicts appear here once blue acts.";
    els.list.append(idle);
    return;
  }
  const indexOf = new Map((eventList || []).slice(0, cursor).map((ev, i) => [ev.id, i + 1]));
  for (const ev of [...audits].reverse().slice(0, 60)) {
    const target = (ev.result || {}).target_event_id;
    els.list.append(auditRow(ev, indexOf.get(target), onRef));
  }
}
