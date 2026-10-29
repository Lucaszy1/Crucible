// Mic recording path: fake MediaRecorder coverage for the chunk flow,
// the quick-stop guard, empty audio, and mime-type fallback.
import { describe, it, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  MIN_RECORD_MS,
  NO_AUDIO_MESSAGE,
  RECORD_TIMESLICE_MS,
  TOO_QUICK_MESSAGE,
  initCommandBar,
  pickRecorderMimeType,
} from "../dashboard/js/commandbar.js";

const cleanups = [];
afterEach(() => {
  while (cleanups.length > 0) {
    const fn = cleanups.pop();
    try {
      fn();
    } catch {
      // One teardown never hides the test result.
    }
  }
});

async function settle(rounds = 20) {
  for (let i = 0; i < rounds; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

function makeEls() {
  const handlers = {};
  const live = new Set();
  const on = (name) => (event, fn) => {
    handlers[`${name}:${event}`] = fn;
  };
  const mic = {
    textContent: "Speak",
    attrs: {},
    classList: {
      add: (cls) => live.add(cls),
      remove: (cls) => live.delete(cls),
      contains: (cls) => live.has(cls),
    },
    setAttribute: (key, value) => {
      mic.attrs[key] = value;
    },
    addEventListener: on("mic"),
  };
  const els = {
    bar: { hidden: false },
    mic,
    input: { value: "", addEventListener: on("input"), focus() {} },
    send: { addEventListener: on("send") },
    demo: { addEventListener: on("demo") },
    takeover: { hidden: true, disabled: false, textContent: "", addEventListener: on("takeover") },
    transcript: { hidden: true, textContent: "" },
    preview: { hidden: true },
    previewLine: { textContent: "" },
    confirm: { hidden: true, textContent: "" },
    spend: { textContent: "" },
    note: { hidden: true, textContent: "" },
  };
  return { els, handlers, live };
}

function audioChunk(text = "audio-bytes") {
  return new Blob([text], { type: "audio/webm" });
}

function emptyChunk() {
  return new Blob([], { type: "audio/webm" });
}

function makeStream(order) {
  const track = {
    stopped: false,
    stop() {
      this.stopped = true;
      if (order) order.push("track");
    },
  };
  return {
    track,
    getTracks: () => [track],
  };
}

// Fake MediaRecorder: emits scripted chunks during start and stop,
// like a real recorder with a timeslice plus a final stop flush.
function makeFakeRecorder({ supported, during = [], onStop = [] } = {}) {
  const support = new Set(
    supported === undefined ? ["audio/webm;codecs=opus", "audio/webm"] : supported,
  );
  return class FakeMediaRecorder {
    static instances = [];
    static isTypeSupported(type) {
      return support.has(type);
    }
    constructor(stream, options) {
      this.stream = stream;
      this.options = options;
      this.mimeType = (options && options.mimeType) || "";
      this.state = "inactive";
      this.ondataavailable = null;
      this.onstop = null;
      this.startArg = undefined;
      this.requestDataCalls = 0;
      this.constructor.instances.push(this);
    }
    _emit(data) {
      if (this.ondataavailable) this.ondataavailable({ data });
    }
    start(timeslice) {
      this.state = "recording";
      this.startArg = timeslice;
      for (const chunk of during) this._emit(chunk);
    }
    requestData() {
      this.requestDataCalls += 1;
    }
    stop() {
      if (this.state === "inactive") throw new Error("InvalidStateError");
      for (const chunk of onStop) this._emit(chunk);
      this.state = "inactive";
      if (this.onstop) this.onstop();
    }
  };
}

function mount({ recorderOptions, uploads, transcripts } = {}) {
  const { els, handlers, live } = makeEls();
  const order = [];
  const stream = makeStream(order);
  const FakeRecorder = makeFakeRecorder(recorderOptions);
  let nowMs = 10000;
  const calls = { audio: uploads || [], parse: 0, order: 0 };
  const ctl = initCommandBar(els, {
    document: { addEventListener() {} },
    mediaDevices: { getUserMedia: async () => stream },
    MediaRecorder: FakeRecorder,
    now: () => nowMs,
    decodeAudioData: async () => ({ samples: [0, 0.1, -0.1, 0.2], sampleRate: 16000 }),
    sendAudio: async (wav) => {
      calls.audio.push(wav);
      return { transcript: (transcripts && transcripts.shift()) || "isolate the willow host" };
    },
    parseOrder: async () => {
      calls.parse += 1;
      return { status: "ok", tool: "isolate_host", args: { host: "willow" } };
    },
    sendOrder: async () => {
      calls.order += 1;
      return { status: "queued" };
    },
    fetchCommandState: async () => ({ attached: true }),
  });
  cleanups.push(() => ctl.stop());
  const clickMic = async () => {
    handlers["mic:click"]();
    await settle();
  };
  const recorder = () => FakeRecorder.instances[FakeRecorder.instances.length - 1];
  return {
    els, handlers, live, ctl, stream, order, calls,
    recorder, setNow: (value) => {
      nowMs = value;
    },
    now: () => nowMs,
    clickMic,
  };
}

describe("pickRecorderMimeType", () => {
  it("prefers opus webm when supported", () => {
    const Fake = makeFakeRecorder({ supported: ["audio/webm;codecs=opus", "audio/webm"] });
    assert.equal(pickRecorderMimeType(Fake), "audio/webm;codecs=opus");
  });

  it("falls through the candidate list", () => {
    const Fake = makeFakeRecorder({ supported: ["audio/mp4"] });
    assert.equal(pickRecorderMimeType(Fake), "audio/mp4");
  });

  it("returns empty for the browser default when nothing matches", () => {
    const Fake = makeFakeRecorder({ supported: [] });
    assert.equal(pickRecorderMimeType(Fake), "");
  });

  it("returns empty when isTypeSupported is missing", () => {
    assert.equal(pickRecorderMimeType({}), "");
    assert.equal(pickRecorderMimeType(undefined), "");
  });
});

describe("mic recording", () => {
  it("collects chunks across start and stop, then uploads WAV", async () => {
    const bar = mount({
      recorderOptions: { during: [audioChunk("first")], onStop: [audioChunk("last")] },
    });
    await bar.clickMic();
    assert.equal(bar.els.mic.textContent, "Stop");
    assert.ok(bar.els.note.textContent.startsWith("Listening"));
    assert.equal(bar.recorder().startArg, RECORD_TIMESLICE_MS);
    assert.equal(bar.recorder().mimeType, "audio/webm;codecs=opus");

    bar.setNow(bar.now() + MIN_RECORD_MS + 100);
    await bar.clickMic();
    assert.equal(bar.recorder().requestDataCalls, 1);
    assert.ok(bar.stream.track.stopped);
    assert.equal(bar.calls.audio.length, 1);
    const wav = bar.calls.audio[0];
    assert.ok(wav instanceof Uint8Array);
    assert.ok(wav.length > 44);
    assert.ok(bar.els.transcript.textContent.includes("Heard (voice)"));
    bar.ctl.stop();
  });

  it("stops stream tracks only after the recorder stops", async () => {
    const bar = mount({ recorderOptions: { onStop: [audioChunk()] } });
    await bar.clickMic();
    const rec = bar.recorder();
    bar.order.length = 0;
    const originalStop = rec.stop.bind(rec);
    rec.stop = () => {
      bar.order.push("recorder");
      originalStop();
    };
    bar.setNow(bar.now() + MIN_RECORD_MS + 100);
    await bar.clickMic();
    assert.deepEqual(bar.order, ["recorder", "track"]);
    bar.ctl.stop();
  });

  it("ignores a stop within 400 ms and keeps recording", async () => {
    const bar = mount({ recorderOptions: { onStop: [audioChunk()] } });
    await bar.clickMic();
    bar.setNow(bar.now() + 100);
    await bar.clickMic();

    assert.equal(bar.els.note.textContent, TOO_QUICK_MESSAGE);
    assert.ok(bar.els.note.textContent.includes("speak first"));
    assert.equal(bar.recorder().state, "recording");
    assert.equal(bar.els.mic.textContent, "Stop");
    assert.equal(bar.calls.audio.length, 0);

    bar.setNow(bar.now() + MIN_RECORD_MS + 100);
    await bar.clickMic();
    assert.equal(bar.calls.audio.length, 1);
    bar.ctl.stop();
  });

  it("reports no audio when the assembled blob is empty", async () => {
    const bar = mount({ recorderOptions: { during: [emptyChunk()], onStop: [emptyChunk()] } });
    await bar.clickMic();
    bar.setNow(bar.now() + MIN_RECORD_MS + 100);
    await bar.clickMic();

    assert.equal(bar.els.note.textContent, NO_AUDIO_MESSAGE);
    assert.equal(
      bar.els.note.textContent,
      "No audio captured. Hold for a couple of seconds and check your Chrome microphone setting.",
    );
    assert.equal(bar.calls.audio.length, 0);
    bar.ctl.stop();
  });

  it("falls back to the browser default when no mime type is supported", async () => {
    const bar = mount({
      recorderOptions: { supported: [], onStop: [audioChunk()] },
    });
    await bar.clickMic();
    assert.equal(bar.recorder().options, undefined);
    assert.equal(bar.recorder().startArg, RECORD_TIMESLICE_MS);
    bar.setNow(bar.now() + MIN_RECORD_MS + 100);
    await bar.clickMic();
    assert.equal(bar.calls.audio.length, 1);
    bar.ctl.stop();
  });
});
