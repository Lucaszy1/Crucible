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

// MediaRecorder tuning: a short timeslice keeps chunks arriving while
// the judge speaks, and a stop inside the guard window is a mistap.
export const RECORD_TIMESLICE_MS = 250;
export const MIN_RECORD_MS = 400;
export const LISTEN_TICK_MS = 500;
export const STOP_TIMEOUT_MS = 2000;
export const RECORDER_MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
];
export const NO_AUDIO_MESSAGE =
  "No audio captured. Hold for a couple of seconds and check your Chrome microphone setting.";
export const TOO_QUICK_MESSAGE = "Still starting — speak first, then press Stop.";

// First candidate the browser accepts, or "" for its default.
// Pure, so the unsupported-mime fallback stays testable in node.
export function pickRecorderMimeType(MediaRecorderLike) {
  const check = MediaRecorderLike && MediaRecorderLike.isTypeSupported;
  if (typeof check !== "function") return "";
  for (const candidate of RECORDER_MIME_CANDIDATES) {
    try {
      if (check.call(MediaRecorderLike, candidate)) return candidate;
    } catch {
      // One rejected probe never blocks the rest.
    }
  }
  return "";
}

// Short role caption for a host chip, never blank. Inventory
// roles arrive as short names or classic sentences; unknown roles
// show themselves so no host reads nameless.
export function chipRole(role) {
  const raw = String(role || "").trim();
  if (!raw) return "host";
  const map = {
    "web portal": "public site",
    "public web front end": "public site",
    api: "public site",
    app: "reports server",
    "internal reporting service": "reports server",
    workstation: "staff desktop",
    "analyst workstation": "staff desktop",
  };
  return map[raw.toLowerCase()] || raw;
}

export function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function hostIds(hosts) {
  return (hosts || [])
    .map((h) => (typeof h === "string" ? h : h && h.id))
    .filter(Boolean);
}

// Three example orders grounded in live host names, cycling short
// lists; empty when no hosts are known, never invented names.
export function exampleOrders(hosts) {
  const names = hostIds(hosts);
  if (names.length === 0) return [];
  const picks = [0, 1, 2].map((i) => names[i % names.length]);
  return [
    `read logs on ${picks[0]}`,
    `scan the ${picks[1]} host`,
    `isolate the ${picks[2]} host`,
  ];
}

// One chip button per host: name plus role caption. Clicks resolve
// through delegation on the container (data-host).
export function chipsHtml(hosts) {
  return (hosts || [])
    .filter((h) => h && (typeof h === "string" || h.id))
    .map((h) => {
      const id = typeof h === "string" ? h : h.id;
      const role = typeof h === "string" ? "" : h.role;
      return `<button type="button" class="cmd-chip" data-host="${escapeHtml(id)}">`
        + `<span class="cmd-chip-name">${escapeHtml(id)}</span>`
        + `<span class="cmd-chip-role">${escapeHtml(chipRole(role))}</span></button>`;
    })
    .join("");
}

export function tryHtml(hosts) {
  const orders = exampleOrders(hosts);
  if (orders.length === 0) return "";
  return "Try: " + orders
    .map((o) => `<button type="button" class="cmd-try-ex" data-order="${escapeHtml(o)}">${escapeHtml(o)}</button>`)
    .join(" · ");
}

// Decode recorder bytes to mono PCM via an offline context. The bar
// uses this by default; tests inject a stub through deps.
export async function defaultDecodeAudioData(raw) {
  const scope = typeof window !== "undefined" ? window : {};
  const OfflineCtx = scope.OfflineAudioContext || scope.webkitOfflineAudioContext;
  if (!OfflineCtx) throw new Error("this browser cannot decode audio");
  const probe = new OfflineCtx(1, 1, 44100);
  const decoded = await probe.decodeAudioData(raw.slice(0));
  return {
    samples: Array.from(decoded.getChannelData(0)),
    sampleRate: decoded.sampleRate,
  };
}

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
    startedAt: 0,
    listenTimer: null,
    holdNoteUntil: 0,
    ptt: false,
    // Speak clicks and hold-V share one recorder: starting covers
    // the getUserMedia window, stopping covers stop through chunk
    // assembly, and stopRequested cancels a start already in flight.
    starting: false,
    stopping: false,
    stopRequested: false,
  };

  // Browser globals resolve through deps first so node tests can
  // inject a fake recorder, clock, and document. Production passes
  // only hosts/jewel and keeps the real globals.
  const MediaRecorderLike = api.MediaRecorder
    || (typeof MediaRecorder !== "undefined" ? MediaRecorder : undefined);
  const mediaDevices = api.mediaDevices
    || (typeof navigator !== "undefined" ? navigator.mediaDevices : undefined);
  const doc = api.document
    || (typeof document !== "undefined" ? document : null);
  const now = () => (typeof api.now === "function" ? api.now() : Date.now());

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
    const heard = parsed && parsed.heard ? ` — ${parsed.heard}` : "";
    setText(els.transcript, transcript ? `Heard (${source}): ${transcript}${heard}` : "");
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

  function listenLine() {
    const elapsed = Math.max(0, (now() - ctl.startedAt) / 1000);
    return `Listening… ${elapsed.toFixed(0)}s — press Stop to transcribe, Esc to cancel.`;
  }

  function stopListenTimer() {
    if (ctl.listenTimer) {
      clearInterval(ctl.listenTimer);
      ctl.listenTimer = null;
    }
  }

  function startListenTimer() {
    stopListenTimer();
    show(listenLine());
    ctl.listenTimer = setInterval(() => {
      if (now() < ctl.holdNoteUntil) return;
      show(listenLine());
    }, LISTEN_TICK_MS);
    if (ctl.listenTimer && typeof ctl.listenTimer.unref === "function") {
      ctl.listenTimer.unref();
    }
  }

  function stopStreamTracks() {
    if (ctl.media && ctl.media.stream) {
      for (const track of ctl.media.stream.getTracks()) {
        try {
          track.stop();
        } catch {
          // A spent track never breaks the bar.
        }
      }
    }
  }

  async function stopRecorder(opts) {
    const force = Boolean(opts && opts.force);
    if (ctl.recording && !force && ctl.startedAt > 0
        && now() - ctl.startedAt < MIN_RECORD_MS) {
      ctl.holdNoteUntil = now() + CONFIRM_MS;
      show(TOO_QUICK_MESSAGE);
      return;
    }
    // One stop at a time: a second stop would steal onstop and hang
    // the first, and a restart mid-stop would reset chunks the old
    // recorder still owns. The flag clears once chunks are consumed.
    if (ctl.stopping) return;
    ctl.stopping = true;
    const rec = ctl.media && ctl.media.recorder;
    ctl.recording = false;
    ctl.startedAt = 0;
    stopListenTimer();
    if (els.mic) {
      els.mic.classList.remove("live");
      els.mic.setAttribute("aria-pressed", "false");
      setText(els.mic, "Speak");
    }
    if (!rec) {
      ctl.stopping = false;
      return;
    }
    if (typeof rec.requestData === "function" && rec.state !== "inactive") {
      try {
        rec.requestData();
      } catch {
        // The stop flush still delivers the tail chunk.
      }
    }
    let done = null;
    const stopped = new Promise((resolve) => {
      done = resolve;
      rec.onstop = () => resolve();
    });
    try {
      rec.stop();
    } catch {
      if (done) done();
    }
    // A broken recorder must never wedge the bar: stop proceeds
    // with whatever chunks arrived.
    let stopTimer = null;
    const stopTimeout = new Promise((resolve) => {
      stopTimer = setTimeout(resolve, STOP_TIMEOUT_MS);
    });
    await Promise.race([stopped, stopTimeout]);
    if (stopTimer) clearTimeout(stopTimer);
    stopStreamTracks();
    ctl.media = null;
    // Chunks read here, after onstop: the final dataavailable lands
    // during stop, so an earlier snapshot always looks empty.
    const blob = new Blob(ctl.chunks, { type: rec.mimeType || "audio/webm" });
    ctl.chunks = [];
    ctl.stopping = false;
    if (!blob.size) {
      show(NO_AUDIO_MESSAGE);
      return;
    }
    show("Transcribing…");
    let wav;
    try {
      const raw = await blob.arrayBuffer();
      const decode = api.decodeAudioData || defaultDecodeAudioData;
      const { samples, sampleRate } = await decode(raw.slice(0));
      const small = downsampleLinear(samples, sampleRate, MIC_RATE);
      wav = encodeWavPCM(small, MIC_RATE);
    } catch (err) {
      show(`Voice failed at decode (${(err && err.message) || err}): type the order instead.`);
      if (els.input) els.input.focus();
      return;
    }
    let reply;
    try {
      reply = await api.sendAudio(wav);
    } catch (err) {
      // A status means the bytes reached the server and transcription
      // refused or failed there; without one the upload never landed.
      const stage = err && err.status ? "transcribe" : "upload";
      show(`Voice failed at ${stage} (${(err && err.message) || err}): type the order instead.`);
      if (els.input) els.input.focus();
      return;
    }
    ctl.pending = null;
    await previewText(reply.transcript || "", "voice");
  }

  async function startMic() {
    if (ctl.recording || ctl.stopping) return;
    // A second trigger during the getUserMedia window adopts the
    // pending start instead of opening a second recorder whose
    // chunks would mix into (and corrupt) this recording.
    if (ctl.starting) {
      ctl.stopRequested = false;
      return;
    }
    clearConfirm();
    if (!MediaRecorderLike || !mediaDevices) {
      show("This browser cannot record: type the order instead.");
      if (els.input) els.input.focus();
      return;
    }
    ctl.starting = true;
    ctl.stopRequested = false;
    if (els.mic) setText(els.mic, "Starting…");
    try {
      const stream = await mediaDevices.getUserMedia({ audio: true });
      const mimeType = pickRecorderMimeType(MediaRecorderLike);
      let recorder;
      try {
        recorder = mimeType
          ? new MediaRecorderLike(stream, { mimeType })
          : new MediaRecorderLike(stream);
      } catch {
        recorder = new MediaRecorderLike(stream);
      }
      ctl.chunks = [];
      recorder.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) ctl.chunks.push(ev.data);
      };
      try {
        recorder.start(RECORD_TIMESLICE_MS);
      } catch {
        recorder.start();
      }
      ctl.media = { recorder, stream };
      ctl.recording = true;
      ctl.starting = false;
      ctl.startedAt = now();
      ctl.holdNoteUntil = 0;
      if (els.mic) {
        els.mic.classList.add("live");
        els.mic.setAttribute("aria-pressed", "true");
        setText(els.mic, "Stop");
      }
      startListenTimer();
      // A V tap or Esc landed mid-start: stop at once instead of
      // orphaning a recording the user already released.
      if (ctl.stopRequested) {
        ctl.stopRequested = false;
        await stopRecorder({ force: true });
      }
    } catch (err) {
      ctl.starting = false;
      ctl.stopRequested = false;
      if (els.mic) setText(els.mic, "Speak");
      show(`Microphone blocked (${(err && err.message) || err}): type the order instead.`);
      if (els.input) els.input.focus();
    }
  }

  async function toggleMic() {
    if (ctl.recording) {
      await stopRecorder();
      return;
    }
    await startMic();
  }

  // Host chips plus the Try line, from the current exercise only.
  // Hidden while detached or when no hosts are known.
  function renderHosts(hosts) {
    if (!els.chips || !els.tryline) return;
    const list = (Array.isArray(hosts) ? hosts : []).filter((h) => h && h.id);
    if (!ctl.visible || list.length === 0) {
      els.chips.hidden = true;
      els.tryline.hidden = true;
      els.chips.innerHTML = "";
      els.tryline.innerHTML = "";
      return;
    }
    els.chips.innerHTML = chipsHtml(list);
    els.tryline.innerHTML = tryHtml(list);
    els.chips.hidden = false;
    els.tryline.hidden = els.tryline.innerHTML === "";
  }

  function delegateData(ev, attr) {
    const target = ev && ev.target;
    const btn = target && target.closest ? target.closest(`[${attr}]`) : null;
    if (!btn || typeof btn.getAttribute !== "function") return null;
    return btn.getAttribute(attr) || null;
  }

  function onChipsClick(ev) {
    const name = delegateData(ev, "data-host");
    if (!name || !els.input) return;
    const cur = els.input.value || "";
    els.input.value = cur && !/\s$/.test(cur) ? `${cur} ${name}` : `${cur}${name}`;
    els.input.focus();
  }

  function onTryClick(ev) {
    const order = delegateData(ev, "data-order");
    if (!order || !els.input) return;
    els.input.value = order;
    els.input.focus();
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
      renderHosts([]);
      return;
    }
    renderHosts(state.hosts);
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

  // Hold V to talk, release to stop. Ignored while typing, while
  // the bar is detached, and on key repeat; release always stops,
  // even inside the mistap window, since letting go means done.
  // Both triggers share startMic/stopRecorder and its guards, so
  // hold-V and Speak/Stop run the identical record path.
  function onKeydown(ev) {
    if (ev.key === "Escape" && (ctl.pending || ctl.recording || ctl.starting)) {
      ev.stopPropagation();
      ctl.ptt = false;
      if (ctl.recording) {
        void stopRecorder({ force: true });
        show("Recording cancelled.");
      } else if (ctl.starting) {
        ctl.stopRequested = true;
        show("Recording cancelled.");
      } else {
        clearConfirm();
        show("Order cancelled before sending.");
      }
      return;
    }
    if ((ev.key === "v" || ev.key === "V") && !ev.repeat
        && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
      const tag = (ev.target && ev.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (!ctl.visible || ctl.recording || ctl.stopping) return;
      ev.preventDefault();
      ctl.ptt = true;
      void startMic();
    }
  }

  function onKeyup(ev) {
    if ((ev.key === "v" || ev.key === "V") && ctl.ptt) {
      ctl.ptt = false;
      // A release during the getUserMedia window cannot stop yet:
      // the pending start auto-stops on completion instead.
      if (ctl.starting) {
        ctl.stopRequested = true;
        return;
      }
      if (ctl.recording) void stopRecorder({ force: true });
    }
  }

  if (els.mic) els.mic.addEventListener("click", () => void toggleMic());
  if (els.chips) els.chips.addEventListener("click", onChipsClick);
  if (els.tryline) els.tryline.addEventListener("click", onTryClick);
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
  if (doc) {
    doc.addEventListener("keydown", onKeydown);
    doc.addEventListener("keyup", onKeyup);
  }

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
    stopListenTimer();
    clearConfirm();
  };
  return ctl;
}
