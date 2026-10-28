// Defender command bar: speak or type orders during a live exercise.
// Pure helpers (previewLine, demoOrder, WAV encode) stay testable in
// node; initCommandBar wires the DOM. All URLs are relative.
import {
  fetchCommandState,
  parseOrder,
  sendAudio,
  sendOrder,
  sendTakeover,
} from "./api.js";

export const CONFIRM_MS = 1500;
export const STATE_POLL_MS = 3000;
export const MIC_RATE = 16000;

const TOOL_VERBS = {
  isolate_host: "Isolate",
  patch_service: "Patch",
  restore_host: "Restore",
  read_logs: "Read logs on",
  report_finding: "Report",
  list_processes: "Check status on",
  scan_host: "Scan",
  wait: "Hold",
};

// One plain line for a parsed preview: the action, the question, or
// the refusal. Pure, so the confirm step and the tests share it.
export function previewLine(parsed) {
  if (!parsed || typeof parsed !== "object") return "No reading yet.";
  if (parsed.status === "control") {
    return parsed.control === "hand-back"
      ? "Hand the defender seat back to the agent."
      : "Take the defender seat from the agent.";
  }
  if (parsed.status === "ok") {
    const verb = TOOL_VERBS[parsed.tool] || parsed.tool || "Run";
    const args = parsed.args || {};
    if (parsed.tool === "report_finding") {
      const text = String(args.text || "").slice(0, 80);
      return text ? `${verb}: ${text}` : `${verb} a finding.`;
    }
    if (parsed.tool === "wait") return "Hold this turn.";
    const host = args.host ? ` ${args.host}` : "";
    const extra = parsed.tool === "read_logs" && args.n ? ` (${args.n} lines)` : "";
    return `${verb}${host}${extra}.`;
  }
  if (parsed.status === "clarify") return parsed.question || "Which host?";
  return parsed.reason || "That order is outside the allowlist.";
}

// Canned demo order from the visible network: isolate a host that is
// not the crown jewel when one exists, else read logs on the first.
export function demoOrder(hosts, jewelHost) {
  const names = (hosts || []).map((h) => (typeof h === "string" ? h : h && h.id)).filter(Boolean);
  if (names.length === 0) return "read logs";
  const pick = names.find((n) => n !== jewelHost) || names[0];
  if (names.length > 1) return `isolate the ${pick} host`;
  return `read logs on ${pick}`;
}

export function spendLine(spendUsd, capUsd) {
  const spend = Number(spendUsd || 0);
  const cap = Number(capUsd || 0);
  return `Model spend $${spend.toFixed(4)} of $${cap.toFixed(2)}`;
}

// Linear downsample of float PCM to a target rate. Pure.
export function downsampleLinear(samples, fromRate, toRate) {
  const input = samples || [];
  if (!(fromRate > 0) || !(toRate > 0) || input.length === 0) return [];
  if (fromRate === toRate) return Array.from(input);
  const ratio = fromRate / toRate;
  const length = Math.max(1, Math.floor(input.length / ratio));
  const out = new Array(length);
  for (let i = 0; i < length; i += 1) {
    const at = i * ratio;
    const lo = Math.floor(at);
    const hi = Math.min(input.length - 1, lo + 1);
    const frac = at - lo;
    out[i] = input[lo] * (1 - frac) + input[hi] * frac;
  }
  return out;
}

// Encode mono float PCM as 16-bit WAV bytes. Pure.
export function encodeWavPCM(samples, sampleRate) {
  const rate = Math.max(8000, Math.min(48000, Math.floor(sampleRate) || MIC_RATE));
  const data = samples || [];
  const buffer = new ArrayBuffer(44 + data.length * 2);
  const view = new DataView(buffer);
  const writeAscii = (offset, text) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };
  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + data.length * 2, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, "data");
  view.setUint32(40, data.length * 2, true);
  for (let i = 0; i < data.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, data[i] || 0));
    view.setInt16(44 + i * 2, Math.round(clamped * 32767), true);
  }
  return new Uint8Array(buffer);
}

function setText(el, text) {
  if (el) el.textContent = text;
}

export function initCommandBar(els, deps) {
  const api = {
    parseOrder, sendOrder, sendAudio, sendTakeover, fetchCommandState,
    ...(deps || {}),
  };
  const bar = els.bar;
  if (!bar) return null;
  const ctl = {
    visible: false,
    recording: false,
    media: null,
    chunks: [],
    timer: null,
    poller: null,
    pending: null,
  };

  function show(message) {
    if (els.note) {
      els.note.hidden = !message;
      setText(els.note, message || "");
    }
  }

  function clearConfirm() {
    if (ctl.timer) {
      clearTimeout(ctl.timer);
      ctl.timer = null;
    }
    ctl.pending = null;
    if (els.preview) els.preview.hidden = true;
    if (els.confirm) els.confirm.hidden = true;
  }

  function renderPreview(transcript, source, parsed) {
    setText(els.transcript, transcript ? `Heard (${source}): ${transcript}` : "");
    if (els.transcript) els.transcript.hidden = !transcript;
    setText(els.previewLine, previewLine(parsed));
    if (els.preview) els.preview.hidden = false;
    const ok = parsed && parsed.status === "ok";
    if (els.confirm) {
      els.confirm.hidden = !ok;
      if (ok) setText(els.confirm, `Sending in ${(CONFIRM_MS / 1000).toFixed(1)}s — Esc cancels`);
    }
    return ok;
  }

  async function executePending() {
    const job = ctl.pending;
    clearConfirm();
    if (!job) return;
    try {
      await api.sendOrder(job.text, job.source, job.action);
      show(`Queued: ${previewLine({ status: "ok", tool: job.action.tool, args: job.action.args })}`);
    } catch (err) {
      show(`Could not queue the order: ${(err && err.message) || err}`);
    }
    void refreshState();
  }

  async function previewText(text, source) {
    clearConfirm();
    const cleaned = String(text || "").trim();
    if (!cleaned) {
      show("Type an order first, or use the Demo command button.");
      return;
    }
    show("");
    let parsed;
    try {
      parsed = await api.parseOrder(cleaned);
    } catch (err) {
      show(`Could not read the order: ${(err && err.message) || err}`);
      return;
    }
    const action = parsed && parsed.status === "ok"
      ? { tool: parsed.tool, args: parsed.args } : null;
    const ok = renderPreview(cleaned, source, parsed);
    if (!ok) {
      show(parsed && parsed.status === "refused"
        ? "Outside the allowlist: nothing was queued."
        : "Needs a clearer host or order: nothing was queued.");
      return;
    }
    ctl.pending = { text: cleaned, source, action };
    ctl.timer = setTimeout(() => void executePending(), CONFIRM_MS);
  }

  async function stopRecorder() {
    const rec = ctl.media && ctl.media.recorder;
    ctl.recording = false;
    if (els.mic) {
      els.mic.classList.remove("live");
      els.mic.setAttribute("aria-pressed", "false");
      setText(els.mic, "Speak");
    }
    if (!rec) return;
    const chunks = ctl.chunks;
    ctl.chunks = [];
    const stopped = new Promise((resolve) => {
      rec.onstop = () => resolve();
    });
    try {
      rec.stop();
    } catch {
      return;
    }
    await stopped;
    if (ctl.media && ctl.media.stream) {
      for (const track of ctl.media.stream.getTracks()) {
        try {
          track.stop();
        } catch {
          // A spent track never breaks the bar.
        }
      }
    }
    ctl.media = null;
    if (chunks.length === 0) {
      show("No audio captured: type the order instead.");
      return;
    }
    show("Transcribing…");
    try {
      const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
      const raw = await blob.arrayBuffer();
      const OfflineCtx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
      if (!OfflineCtx) throw new Error("this browser cannot decode audio");
      const probe = new OfflineCtx(1, 1, 44100);
      const decoded = await probe.decodeAudioData(raw.slice(0));
      const channel = decoded.getChannelData(0);
      const small = downsampleLinear(Array.from(channel), decoded.sampleRate, MIC_RATE);
      const wav = encodeWavPCM(small, MIC_RATE);
      const reply = await api.sendAudio(wav);
      ctl.pending = null;
      await previewText(reply.transcript || "", "voice");
    } catch (err) {
      show(`Voice failed (${(err && err.message) || err}): type the order instead.`);
      if (els.input) els.input.focus();
    }
  }

  async function toggleMic() {
    if (ctl.recording) {
      await stopRecorder();
      return;
    }
    clearConfirm();
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices) {
      show("This browser cannot record: type the order instead.");
      if (els.input) els.input.focus();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      ctl.chunks = [];
      recorder.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) ctl.chunks.push(ev.data);
      };
      recorder.start();
      ctl.media = { recorder, stream };
      ctl.recording = true;
      if (els.mic) {
        els.mic.classList.add("live");
        els.mic.setAttribute("aria-pressed", "true");
        setText(els.mic, "Stop");
      }
      show("Listening… press Stop to transcribe, Esc to cancel.");
    } catch (err) {
      show(`Microphone blocked (${(err && err.message) || err}): type the order instead.`);
      if (els.input) els.input.focus();
    }
  }

  async function refreshState() {
    let state = null;
    try {
      state = await api.fetchCommandState();
    } catch {
      return;
    }
    const attached = Boolean(state && state.attached);
    ctl.visible = attached;
    bar.hidden = !attached;
    if (!attached) {
      clearConfirm();
      return;
    }
    setText(els.spend, spendLine(state.spend_usd, state.spend_cap_usd));
    if (els.takeover) {
      const humanMode = state.mode === "human";
      els.takeover.hidden = humanMode;
      els.takeover.disabled = false;
      setText(els.takeover, state.latched ? "Hand back" : "Take over blue");
    }
    if (state.last && state.last.status) {
      const last = state.last;
      if (last.status === "taken-over") show("Defender seat held: your orders run next.");
      else if (last.status === "handed-back") show("Seat handed back to the agent.");
      else if (last.status === "ok" && last.tool) show(`Last order ran: ${previewLine({ status: "ok", tool: last.tool, args: last.args })}`);
      else if (last.message) show(`Last order: ${last.message}`);
    }
  }

  function onKeydown(ev) {
    if (ev.key === "Escape" && (ctl.pending || ctl.recording)) {
      ev.stopPropagation();
      if (ctl.recording) {
        void stopRecorder();
        show("Recording cancelled.");
      } else {
        clearConfirm();
        show("Order cancelled before sending.");
      }
    }
  }

  if (els.mic) els.mic.addEventListener("click", () => void toggleMic());
  if (els.send) els.send.addEventListener("click", () => {
    if (els.input) void previewText(els.input.value, "text");
  });
  if (els.input) els.input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      void previewText(els.input.value, "text");
    }
  });
  if (els.demo) els.demo.addEventListener("click", () => {
    const order = demoOrder(deps && deps.hosts ? deps.hosts() : [], deps && deps.jewel ? deps.jewel() : null);
    if (els.input) {
      els.input.value = order;
      els.input.focus();
    }
    void previewText(order, "text");
  });
  if (els.takeover) els.takeover.addEventListener("click", async () => {
    let state = null;
    try {
      state = await api.fetchCommandState();
    } catch (err) {
      show(`Takeover failed: ${(err && err.message) || err}`);
      return;
    }
    try {
      await api.sendTakeover(!(state && state.latched));
    } catch (err) {
      show(`Takeover failed: ${(err && err.message) || err}`);
      return;
    }
    void refreshState();
  });
  document.addEventListener("keydown", onKeydown);

  ctl.refresh = refreshState;
  ctl.start = () => {
    void refreshState();
    if (!ctl.poller) ctl.poller = setInterval(() => void refreshState(), STATE_POLL_MS);
  };
  ctl.stop = () => {
    if (ctl.poller) {
      clearInterval(ctl.poller);
      ctl.poller = null;
    }
    clearConfirm();
  };
  return ctl;
}
