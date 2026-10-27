// Learning tab: campaign score chart plus playbook table.
// Data helpers are pure (node-testable); renderLearn touches the DOM.

const SVG_NS = "http://www.w3.org/2000/svg";

// Trailing rolling mean over the last `window` numeric values.
export function rollingMean(values, window = 3) {
  const out = [];
  const queue = [];
  for (const value of values || []) {
    if (typeof value === "number" && Number.isFinite(value)) queue.push(value);
    const tail = queue.slice(-window);
    out.push(tail.length > 0 ? tail.reduce((sum, v) => sum + v, 0) / tail.length : null);
  }
  return out;
}

// Normalize campaign episodes to sorted [episode_no, blue_score] pairs.
export function seriesFor(detail) {
  const rows = [];
  for (const row of (detail && detail.episodes) || []) {
    const rawX = row.episode_no;
    const rawY = row.blue_score;
    if (rawX === null || rawX === undefined || rawX === "") continue;
    if (rawY === null || rawY === undefined || rawY === "") continue;
    const x = Number(rawX);
    const y = Number(rawY);
    if (Number.isFinite(x) && Number.isFinite(y)) rows.push([x, y]);
  }
  return rows.sort((a, b) => a[0] - b[0]);
}

function el(name, attrs, text) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

const CHART = { width: 660, height: 280, left: 44, right: 16, top: 14, bottom: 30 };

export function renderLearn(els, mem, ctl) {
  const memSeries = seriesFor(mem);
  const ctlSeries = seriesFor(ctl);
  const svg = els.svg;
  svg.innerHTML = "";
  svg.setAttribute("viewBox", `0 0 ${CHART.width} ${CHART.height}`);
  const maxX = Math.max(1, ...memSeries.map(([x]) => x), ...ctlSeries.map(([x]) => x));
  const plotW = CHART.width - CHART.left - CHART.right;
  const plotH = CHART.height - CHART.top - CHART.bottom;
  const xPos = (v) => CHART.left + (maxX <= 1 ? plotW / 2 : ((v - 1) / (maxX - 1)) * plotW);
  const yPos = (v) => CHART.top + (1 - Math.min(100, Math.max(0, v)) / 100) * plotH;

  for (const grid of [0, 25, 50, 75, 100]) {
    svg.append(el("line", {
      x1: CHART.left, y1: yPos(grid),
      x2: CHART.width - CHART.right, y2: yPos(grid),
      class: "chart-grid",
    }));
    svg.append(el("text", { x: CHART.left - 8, y: yPos(grid) + 4, class: "chart-axis" }, String(grid)));
  }
  for (let n = 1; n <= maxX; n += 1) {
    svg.append(el("text", { x: xPos(n), y: CHART.height - 10, class: "chart-axis chart-x" }, String(n)));
  }

  const draw = (series, cls, rollCls) => {
    if (series.length === 0) return;
    const pts = series.map(([x, y]) => `${xPos(x).toFixed(1)},${yPos(y).toFixed(1)}`).join(" ");
    svg.append(el("polyline", { points: pts, class: cls }));
    const means = rollingMean(series.map(([, y]) => y));
    const roll = series.map(([x], i) => [x, means[i]]).filter(([, m]) => m !== null);
    if (roll.length > 1) {
      svg.append(el("polyline", {
        points: roll.map(([x, m]) => `${xPos(x).toFixed(1)},${yPos(m).toFixed(1)}`).join(" "),
        class: rollCls,
      }));
    }
    for (const [x, y] of series) {
      svg.append(el("circle", { cx: xPos(x), cy: yPos(y), r: 3.5, class: `${cls}-dot` }));
    }
  };
  draw(memSeries, "chart-mem", "chart-mem-roll");
  draw(ctlSeries, "chart-ctl", "chart-ctl-roll");

  if (memSeries.length === 0 && ctlSeries.length === 0) {
    const idle = el("text", {
      x: CHART.width / 2, y: CHART.height / 2, class: "chart-empty",
    }, "Pick two campaigns to compare.");
    svg.append(idle);
  }

  els.legendMem.textContent = (mem && mem.name) || "memory";
  els.legendCtl.textContent = (ctl && ctl.name) || "control";

  const book = (mem && mem.playbook && mem.playbook.length > 0 ? mem.playbook : (ctl && ctl.playbook) || []);
  els.table.innerHTML = "";
  if (book.length === 0) {
    els.empty.hidden = false;
    return;
  }
  els.empty.hidden = true;
  for (const entry of book) {
    const row = document.createElement("tr");
    const rule = document.createElement("td");
    rule.textContent = entry.rule || "";
    const from = document.createElement("td");
    from.className = "episode-cell";
    from.textContent = entry.episode || "";
    row.append(rule, from);
    els.table.append(row);
  }
}

export function fillCampaignSelects(select, campaigns, preferredMemory) {
  select.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = campaigns.length === 0 ? "No campaigns yet" : "Select…";
  select.append(blank);
  for (const item of campaigns) {
    const opt = document.createElement("option");
    opt.value = item.name;
    opt.textContent = `${item.name} (${item.episodes} episodes, memory ${item.memory || "?"})`;
    select.append(opt);
  }
  const pick = campaigns.find((item) => item.memory === preferredMemory) || campaigns[0];
  if (pick) select.value = pick.name;
}
