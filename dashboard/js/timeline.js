// Slim scrubber plus transport state. Transport state lives in app.js.

export function renderTransport(els, cursor, total, playing, canPlay) {
  els.scrubber.max = String(total);
  els.scrubber.value = String(cursor);
  els.scrubber.disabled = total === 0 || !canPlay;
  els.position.textContent = `${cursor} / ${total}`;
  els.play.disabled = !canPlay || total === 0 || (playing && cursor < total) || cursor >= total;
  els.pause.disabled = !canPlay || !playing;
}
