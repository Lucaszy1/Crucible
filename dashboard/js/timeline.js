// Timeline ticks plus scrubber position. Transport state lives in app.js.

export function renderTicks(strip, events, cursor, clickable, onJump) {
  strip.innerHTML = "";
  if (events.length === 0) {
    const hint = document.createElement("span");
    hint.className = "strip-hint";
    hint.textContent = "Timeline ticks appear here once an exercise loads.";
    strip.append(hint);
    strip.classList.add("locked");
    return;
  }
  strip.classList.toggle("locked", !clickable);
  events.forEach((ev, i) => {
    const tick = document.createElement("button");
    const seen = i < cursor;
    tick.className = `tick ${ev.actor === "red" ? "red" : "blue"}${seen ? " seen" : " future"}`;
    if (i === cursor - 1) tick.classList.add("current");
    tick.title = `#${i + 1} ${ev.actor} ${ev.tool}`;
    tick.setAttribute("aria-label", tick.title);
    if (clickable) tick.addEventListener("click", () => onJump(i + 1));
    strip.append(tick);
  });
  const current = strip.querySelector(".tick.current");
  if (current) current.scrollIntoView({ block: "nearest", inline: "nearest" });
}

export function renderTransport(els, cursor, total, playing, canPlay) {
  els.scrubber.max = String(total);
  els.scrubber.value = String(cursor);
  els.scrubber.disabled = total === 0 || !canPlay;
  els.position.textContent = `${cursor} / ${total}`;
  els.play.disabled = !canPlay || total === 0 || (playing && cursor < total) || cursor >= total;
  els.pause.disabled = !canPlay || !playing;
}
