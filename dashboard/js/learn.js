// Learning tab: the Stage 3 memory experiment plus the frozen playbook.
// Numbers arrive from /api/bench/stage3; stats and sentence helpers
// are pure (node-testable) and renderLearning touches the DOM.

function binom(n, k) {
  const kk = Math.min(Math.max(0, k), n);
  let num = 1;
  for (let i = 0; i < kk; i += 1) num = (num * (n - i)) / (i + 1);
  return num;
}

// Exact two-sided McNemar p-value from discordant pair counts:
// b pairs where memory-on won, c where memory-off won. With no
// discordant pairs the arms agreed everywhere, so p = 1.
export function mcnemarExact(b, c) {
  const bb = Math.max(0, Math.floor(Number(b) || 0));
  const cc = Math.max(0, Math.floor(Number(c) || 0));
  const n = bb + cc;
  if (n === 0) return 1;
  let sum = 0;
  for (let i = 0; i <= Math.min(bb, cc); i += 1) sum += binom(n, i);
  return Math.min(1, 2 * sum * Math.pow(0.5, n));
}

export function formatP(p) {
  if (!(p >= 0)) return "n/a";
  if (p < 0.001) return "p < 0.001";
  return `p = ${p.toFixed(3)}`;
}

export function formatPct(part, whole) {
  if (!(whole > 0)) return "n/a";
  return `${Math.round((part / whole) * 100)}%`;
}

const RULE_ACTIONS = [
  [/isolate/i, "Isolate"],
  [/patch/i, "Patch"],
  [/restor/i, "Restore"],
  [/report/i, "Report"],
  [/monitor|watch|check/i, "Watch"],
  [/wait|hold/i, "Hold"],
];

// Action chips derived from the rule's own verbs, in a fixed order.
export function ruleActions(rule) {
  const text = String(rule || "");
  const out = [];
  for (const [re, label] of RULE_ACTIONS) {
    if (re.test(text)) out.push(label);
  }
  return out;
}

// One plain sentence for a memory on/off section, from its counts.
export function stage3Line(section, label) {
  const pairs = (section && section.pairs) || { n: 0 };
  if (!(pairs.n > 0)) return `No paired ${label} runs to compare.`;
  const agree = (pairs.contained.b + pairs.contained.c === 0) &&
    (pairs.secret.b + pairs.secret.c === 0);
  if (agree) {
    return `Memory made no measurable difference with ${label}: ` +
      `all ${pairs.n} paired runs agreed.`;
  }
  const { on, off } = section;
  const pc = mcnemarExact(pairs.contained.b, pairs.contained.c);
  const ps = mcnemarExact(pairs.secret.b, pairs.secret.c);
  return `With ${label}, memory on contained ${on.contained} of ${on.n} runs ` +
    `against ${off.contained} of ${off.n} without it ` +
    `(${pairs.n} paired runs, ${formatP(pc)}); secret reads ` +
    `${on.secretRead} against ${off.secretRead} (${formatP(ps)}).`;
}

function armRow(doc, name, cell, metric, noun) {
  const row = doc.createElement("div");
  row.className = `arm-row${metric === "secretRead" ? " secret" : ""}`;
  const who = doc.createElement("span");
  who.className = "arm-name";
  who.textContent = name;
  const bar = doc.createElement("div");
  bar.className = "bar";
  bar.setAttribute("role", "img");
  const pct = cell.n > 0 ? Math.round((cell[metric] / cell.n) * 100) : 0;
  bar.setAttribute("aria-label", `${name}: ${cell[metric]} of ${cell.n} ${noun}`);
  const fill = doc.createElement("div");
  fill.className = "bar-fill";
  fill.style.width = `${cell.n > 0 ? pct : 0}%`;
  bar.append(fill);
  const num = doc.createElement("span");
  num.className = "arm-num";
  num.textContent = `${cell[metric]} of ${cell.n} ${noun}`;
  row.append(who, bar, num);
  return row;
}

function renderSection(doc, root, section, title, tag, label) {
  root.innerHTML = "";
  if (!section) {
    const idle = doc.createElement("p");
    idle.className = "empty";
    idle.textContent = `${title}: no runs on file.`;
    root.append(idle);
    return;
  }
  const head = doc.createElement("h3");
  head.className = "stage3-head";
  head.textContent = title;
  if (tag) {
    const chip = doc.createElement("span");
    chip.className = "lobby-badge";
    chip.textContent = tag;
    head.append(doc.createTextNode(" "), chip);
  }
  const src = doc.createElement("p");
  src.className = "cost-sub";
  src.textContent = section.source || "";
  const wrap = doc.createElement("div");
  wrap.className = "arm-group";
  wrap.append(
    armRow(doc, "Memory on", section.on, "contained", "contained"),
    armRow(doc, "Memory off", section.off, "contained", "contained"),
    armRow(doc, "Memory on", section.on, "secretRead", "secret reads"),
    armRow(doc, "Memory off", section.off, "secretRead", "secret reads"),
  );
  const pairs = doc.createElement("p");
  pairs.className = "pairs-line";
  pairs.textContent = `${section.pairs.n} paired runs · containment ` +
    `${formatP(mcnemarExact(section.pairs.contained.b, section.pairs.contained.c))} · ` +
    `secret reads ${formatP(mcnemarExact(section.pairs.secret.b, section.pairs.secret.c))}`;
  const line = doc.createElement("p");
  line.className = "stage3-line";
  line.textContent = stage3Line(section, label);
  root.append(head, src, wrap, pairs, line);
}

export function renderLearning(els, data) {
  const doc = els.doc || document;
  els.empty.hidden = Boolean(data);
  els.note.hidden = !data || !data.voidNote;
  if (data && data.voidNote) els.note.textContent = data.voidNote;
  renderSection(doc, els.on, data && data.sensorsOn, "Sensors on", "corrected rerun", "sensors on");
  renderSection(doc, els.off, data && data.sensorsOff, "Sensors off", "valid half", "sensors off");
  els.rules.innerHTML = "";
  const book = (data && data.playbook && data.playbook.rules) || [];
  els.source.textContent = data && data.playbook
    ? `Frozen playbook · ${data.playbook.source}`
    : "";
  els.playEmpty.hidden = book.length > 0;
  for (const entry of book) {
    const item = doc.createElement("li");
    item.className = "rule";
    const text = doc.createElement("p");
    text.className = "rule-text";
    text.textContent = entry.rule || "";
    const meta = doc.createElement("p");
    meta.className = "rule-meta";
    for (const action of ruleActions(entry.rule)) {
      const chip = doc.createElement("span");
      chip.className = "lobby-badge";
      chip.textContent = action;
      meta.append(chip, doc.createTextNode(" "));
    }
    if (entry.episode) {
      const from = doc.createElement("span");
      from.className = "rule-from";
      from.textContent = `from ${entry.episode}`;
      meta.append(from);
    }
    item.append(text, meta);
    els.rules.append(item);
  }
}
