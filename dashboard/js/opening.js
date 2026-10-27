// Opening slate: a 3 to 4 second build-up before playback. Title,
// sides, hosts, the protected file line, then a 3-2-1 countdown to
// BEGIN. Space or Enter skips. Never replays on scrub or jump: only
// opening an exercise starts it. Skipped under reduced motion.
import { HOSTS } from "./state.js";

const COUNT = ["3", "2", "1", "BEGIN"];
// Timings in ms: beat, beat, beat, BEGIN hold, then done.
const BEATS = [500, 1400, 2300, 3000];
const DONE_AT = 3600;

export function playOpening(els, opts) {
  const o = opts || {};
  const timers = [];
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    destroy();
    o.onDone();
  };
  const onKey = (ev) => {
    if (ev.code === "Space" || ev.key === "Enter") {
      ev.preventDefault();
      ev.stopPropagation();
      finish();
    }
  };
  const destroy = () => {
    for (const timer of timers) clearTimeout(timer);
    timers.length = 0;
    document.removeEventListener("keydown", onKey, true);
    if (els.root) els.root.hidden = true;
  };
  if (o.reduced) {
    timers.push(setTimeout(finish, 0));
    return { skip: finish, destroy };
  }
  if (els.title) els.title.textContent = `Exercise ${o.label || ""}`.trim();
  if (els.hosts) {
    els.hosts.innerHTML = "";
    const names = Array.isArray(o.hosts) && o.hosts.length > 0 ? o.hosts : HOSTS;
    const roles = (o && o.roles) || {};
    for (const host of names) {
      const chip = document.createElement("span");
      chip.className = "slate-host";
      chip.textContent = host;
      if (roles[host]) chip.title = `${host} · ${roles[host]}`;
      els.hosts.append(chip);
    }
  }
  if (els.count) els.count.textContent = COUNT[0];
  if (els.root) els.root.hidden = false;
  document.addEventListener("keydown", onKey, true);
  COUNT.forEach((word, i) => {
    timers.push(setTimeout(() => {
      if (els.count) {
        els.count.textContent = word;
        els.count.classList.remove("pop");
        void els.count.offsetWidth;
        els.count.classList.add("pop");
      }
    }, BEATS[i]));
  });
  timers.push(setTimeout(finish, DONE_AT));
  return { skip: finish, destroy };
}
