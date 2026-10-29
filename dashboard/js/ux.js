// Plain-language tooltips for first-time visitors. Pure strings
// plus one DOM applier, so node tests the copy and the live and
// static pages share it. Copy rules: plain words, no em dashes,
// exercise and containment wording only.
export const SCORE_TIPS = {
  outcome: "How the exercise ended. Contained means blue stopped the intrusion and the file stayed safe.",
  blueScore: "Blue score from 0 to 100. Starts at 100 and falls when hosts stay offline.",
  downtime: "Share of host time offline for containment. Lower is better.",
};

export const SPEED_TIP = "Replay speed. Brackets change it: [ slows down, ] speeds up.";
export const SPEED_BUTTON_TIPS = {
  "0.5": "Half speed. Press [ for slower.",
  1: "Normal speed.",
  2: "Double speed. Press ] for faster.",
  4: "Quadruple speed.",
};

export const AUDITOR_TIP = "Auditor verdict. The auditor rates each blue move from 0 to 10 and checks the log lines.";

// verdictExplained mirrors verdictChip in audit.js: one plain line
// per verdict word, shared by tests and the chip title.
export function verdictExplained(verdict, score) {
  const word = String(verdict || "verdict");
  const num = score === null || score === undefined ? "" : ` ${score}/10`;
  return `Auditor verdict: ${word}${num}. The auditor checks if the blue move was fair.`;
}

// Apply static tooltips to score labels, the speed strip, and the
// auditor box. Safe to call twice; missing nodes are skipped.
export function applyTooltips(doc) {
  const root = doc || (typeof document !== "undefined" ? document : null);
  if (!root || typeof root.querySelectorAll !== "function") return;
  const setTips = (selector, tip) => {
    for (const node of root.querySelectorAll(selector)) {
      if (node && typeof node.setAttribute === "function") node.setAttribute("title", tip);
    }
  };
  setTips("#sbOutcome, #arenaOutcome", SCORE_TIPS.outcome);
  setTips("#sbBlue, #arenaBlue", SCORE_TIPS.blueScore);
  setTips("#sbDowntime, #arenaDowntime", SCORE_TIPS.downtime);
  const strip = typeof root.getElementById === "function" ? root.getElementById("speedStrip") : null;
  if (strip && typeof strip.setAttribute === "function") strip.setAttribute("title", SPEED_TIP);
  if (strip && typeof strip.querySelectorAll === "function") {
    for (const btn of strip.querySelectorAll("button[data-speed]")) {
      const tip = SPEED_BUTTON_TIPS[String(btn.dataset && btn.dataset.speed)];
      if (tip && typeof btn.setAttribute === "function") btn.setAttribute("title", tip);
    }
  }
  const aud = typeof root.getElementById === "function" ? root.getElementById("arenaAud") : null;
  if (aud && typeof aud.setAttribute === "function") aud.setAttribute("title", AUDITOR_TIP);
}
