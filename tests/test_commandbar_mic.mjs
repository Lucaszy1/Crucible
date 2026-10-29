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
  const input = {
    value: "",
    focusCount: 0,
    addEventListener: on("input"),
    focus() {
      input.focusCount += 1;
    },
  };
  const els = {
    bar: { hidden: false },
    mic,
    input,
    chips: { hidden: true, innerHTML: "", addEventListener: on("chips") },
    tryline: { hidden: true, innerHTML: "", addEventListener: on("tryline") },
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

function mount({ recorderOptions, uploads, transcripts, parseResult, decode, sendAudioImpl } = {}) {
  const { els, handlers, live } = makeEls();
  const order = [];
  const streams = [];
  const FakeRecorder = makeFakeRecorder(recorderOptions);
  let nowMs = 10000;
  let liveState = { attached: true, hosts: [] };
  const calls = { audio: uploads || [], parse: 0, order: 0 };
  const ctl = initCommandBar(els, {
    document: { addEventListener: (event, fn) => {
      handlers[`doc:${event}`] = fn;
    } },
    mediaDevices: { getUserMedia: async () => {
      const stream = makeStream(order);
      streams.push(stream);
      return stream;
    } },
    MediaRecorder: FakeRecorder,
    now: () => nowMs,
    decodeAudioData: decode || (async () => ({ samples: [0, 0.1, -0.1, 0.2], sampleRate: 16000 })),
    sendAudio: sendAudioImpl || (async (wav) => {
      calls.audio.push(wav);
      return { transcript: (transcripts && transcripts.shift()) || "isolate the willow host" };
    }),
    parseOrder: async () => {
      calls.parse += 1;
      return parseResult || { status: "ok", tool: "isolate_host", args: { host: "willow" } };
    },
    sendOrder: async () => {
      calls.order += 1;
      return { status: "queued" };
    },
    fetchCommandState: async () => liveState,
  });
  cleanups.push(() => ctl.stop());
  const clickMic = async () => {
    handlers["mic:click"]();
    await settle();
  };
  const recorder = () => FakeRecorder.instances[FakeRecorder.instances.length - 1];
  return {
    els, handlers, live, ctl, streams, order, calls,
    recorder,
    recorders: () => FakeRecorder.instances.slice(),
    setNow: (value) => {
      nowMs = value;
    },
    now: () => nowMs,
    clickMic,
    setState: (state) => {
      liveState = state;
    },
    refresh: async () => {
      await ctl.refresh();
    },
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
    assert.ok(bar.streams[0].track.stopped);
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

  it("shows the fuzzy match note with the transcript", async () => {
    const bar = mount({
      recorderOptions: { onStop: [audioChunk()] },
      transcripts: ["isolate quil"],
      parseResult: {
        status: "ok", tool: "isolate_host", args: { host: "quill" },
        heard: "Heard 'quil', using quill.",
      },
    });
    await bar.clickMic();
    bar.setNow(bar.now() + MIN_RECORD_MS + 100);
    await bar.clickMic();
    assert.ok(bar.els.transcript.textContent.includes("Heard (voice): isolate quil"));
    assert.ok(bar.els.transcript.textContent.includes("Heard 'quil', using quill."));
    bar.ctl.stop();
  });
});

function chipClick(bar, host) {
  bar.handlers["chips:click"]({
    target: {
      closest: (sel) => (sel === "[data-host]"
        ? { getAttribute: () => host }
        : null),
    },
  });
}

function tryClick(bar, order) {
  bar.handlers["tryline:click"]({
    target: {
      closest: (sel) => (sel === "[data-order]"
        ? { getAttribute: () => order }
        : null),
    },
  });
}

describe("host chips and examples", () => {
  it("renders one chip per current host with roles", async () => {
    const bar = mount();
    bar.setState({
      attached: true,
      hosts: [
        { id: "quill", role: "web portal" },
        { id: "flint", role: "database" },
      ],
    });
    await bar.refresh();
    assert.equal(bar.els.chips.hidden, false);
    assert.ok(bar.els.chips.innerHTML.includes("quill"));
    assert.ok(bar.els.chips.innerHTML.includes("public site"));
    assert.ok(bar.els.chips.innerHTML.includes("flint"));
    assert.equal(bar.els.tryline.hidden, false);
    assert.ok(bar.els.tryline.innerHTML.startsWith("Try:"));
    assert.ok(bar.els.tryline.innerHTML.includes("read logs on quill"));
    bar.ctl.stop();
  });

  it("re-renders per exercise and hides when detached", async () => {
    const bar = mount();
    bar.setState({ attached: true, hosts: [{ id: "quill", role: "app" }] });
    await bar.refresh();
    assert.ok(bar.els.chips.innerHTML.includes("quill"));
    bar.setState({ attached: true, hosts: [{ id: "zephyr", role: "app" }] });
    await bar.refresh();
    assert.ok(!bar.els.chips.innerHTML.includes("quill"));
    assert.ok(bar.els.chips.innerHTML.includes("zephyr"));
    bar.setState({ attached: false, hosts: [] });
    await bar.refresh();
    assert.equal(bar.els.chips.hidden, true);
    assert.equal(bar.els.chips.innerHTML, "");
    assert.equal(bar.els.tryline.hidden, true);
    bar.ctl.stop();
  });

  it("chip click inserts the host name into the box", async () => {
    const bar = mount();
    chipClick(bar, "quill");
    assert.equal(bar.els.input.value, "quill");
    chipClick(bar, "flint");
    assert.equal(bar.els.input.value, "quill flint");
    assert.ok(bar.els.input.focusCount >= 2);
    bar.ctl.stop();
  });

  it("example click fills the box", async () => {
    const bar = mount();
    bar.els.input.value = "stale text";
    tryClick(bar, "scan the quill host");
    assert.equal(bar.els.input.value, "scan the quill host");
    bar.ctl.stop();
  });
});

describe("push to talk", () => {
  it("hold V records, release stops", async () => {
    const bar = mount({ recorderOptions: { onStop: [audioChunk()] } });
    bar.setState({ attached: true, hosts: [] });
    await bar.refresh();
    bar.handlers["doc:keydown"]({
      key: "v", target: { tagName: "DIV" }, preventDefault() {},
    });
    await settle();
    assert.equal(bar.els.mic.textContent, "Stop");
    bar.handlers["doc:keyup"]({ key: "v" });
    await settle();
    assert.equal(bar.calls.audio.length, 1);
    assert.equal(bar.els.mic.textContent, "Speak");
    bar.ctl.stop();
  });

  it("ignores V while typing", async () => {
    const bar = mount();
    bar.setState({ attached: true, hosts: [] });
    await bar.refresh();
    bar.handlers["doc:keydown"]({ key: "v", target: { tagName: "INPUT" } });
    await settle();
    assert.equal(bar.els.mic.textContent, "Speak");
    bar.ctl.stop();
  });
});

function pressV(bar) {
  bar.handlers["doc:keydown"]({
    key: "v", target: { tagName: "DIV" }, preventDefault() {},
  });
}

function releaseV(bar) {
  bar.handlers["doc:keyup"]({ key: "v" });
}

function allQuiet(bar) {
  assert.ok(bar.recorders().every((rec) => rec.state === "inactive"),
    "no recorder left running");
  assert.ok(bar.streams.every((stream) => stream.track.stopped),
    "no stream left live");
}

describe("overlapping starts", () => {
  it("collapses a double click on Speak into one recorder", async () => {
    const bar = mount({ recorderOptions: { onStop: [audioChunk()] } });
    bar.handlers["mic:click"]();
    bar.handlers["mic:click"]();
    await settle();
    assert.equal(bar.recorders().length, 1);
    assert.equal(bar.els.mic.textContent, "Stop");
    bar.setNow(bar.now() + MIN_RECORD_MS + 100);
    await bar.clickMic();
    assert.equal(bar.calls.audio.length, 1);
    assert.equal(bar.els.mic.textContent, "Speak");
    allQuiet(bar);
    bar.ctl.stop();
  });

  it("V during a pending click start shares the one recorder", async () => {
    const bar = mount({ recorderOptions: { onStop: [audioChunk()] } });
    bar.setState({ attached: true, hosts: [] });
    await bar.refresh();
    bar.handlers["mic:click"]();
    pressV(bar);
    await settle();
    assert.equal(bar.recorders().length, 1);
    releaseV(bar);
    await settle();
    assert.equal(bar.calls.audio.length, 1);
    allQuiet(bar);
    bar.ctl.stop();
  });

  it("ignores a click while a V start is pending", async () => {
    const bar = mount({ recorderOptions: { onStop: [audioChunk()] } });
    bar.setState({ attached: true, hosts: [] });
    await bar.refresh();
    pressV(bar);
    bar.handlers["mic:click"]();
    await settle();
    assert.equal(bar.recorders().length, 1);
    releaseV(bar);
    await settle();
    assert.equal(bar.calls.audio.length, 1);
    allQuiet(bar);
    bar.ctl.stop();
  });
});

describe("tap and restart", () => {
  it("a V tap stops the pending start instead of orphaning it", async () => {
    const bar = mount();
    bar.setState({ attached: true, hosts: [] });
    await bar.refresh();
    pressV(bar);
    releaseV(bar);
    await settle();
    assert.equal(bar.els.mic.textContent, "Speak");
    assert.equal(bar.calls.audio.length, 0);
    assert.ok(bar.els.note.textContent.includes("No audio captured"));
    allQuiet(bar);
    bar.ctl.stop();
  });

  it("a restart during stop waits instead of corrupting", async () => {
    const bar = mount({ recorderOptions: { onStop: [audioChunk()] } });
    await bar.clickMic();
    bar.setNow(bar.now() + MIN_RECORD_MS + 100);
    bar.handlers["mic:click"]();
    bar.handlers["mic:click"]();
    await settle();
    assert.equal(bar.calls.audio.length, 1);
    assert.equal(bar.els.mic.textContent, "Speak");
    await bar.clickMic();
    assert.equal(bar.els.mic.textContent, "Stop");
    bar.setNow(bar.now() + MIN_RECORD_MS + 100);
    await bar.clickMic();
    assert.equal(bar.calls.audio.length, 2);
    allQuiet(bar);
    bar.ctl.stop();
  });
});

describe("staged voice errors", () => {
  async function recordAndStop(bar) {
    await bar.clickMic();
    bar.setNow(bar.now() + MIN_RECORD_MS + 100);
    await bar.clickMic();
  }

  it("names the decode stage", async () => {
    const bar = mount({
      recorderOptions: { onStop: [audioChunk()] },
      decode: async () => {
        throw new Error("cannot decode");
      },
    });
    await recordAndStop(bar);
    assert.ok(bar.els.note.textContent.includes("Voice failed at decode"),
      bar.els.note.textContent);
    assert.equal(bar.calls.audio.length, 0);
    bar.ctl.stop();
  });

  it("names the upload stage on network failure", async () => {
    const bar = mount({
      recorderOptions: { onStop: [audioChunk()] },
      sendAudioImpl: async () => {
        throw new Error("fetch failed");
      },
    });
    await recordAndStop(bar);
    assert.ok(bar.els.note.textContent.includes("Voice failed at upload"),
      bar.els.note.textContent);
    bar.ctl.stop();
  });

  it("names the transcribe stage on server failure", async () => {
    const bar = mount({
      recorderOptions: { onStop: [audioChunk()] },
      sendAudioImpl: async () => {
        const err = new Error("/api/audio returned 502");
        err.status = 502;
        throw err;
      },
    });
    await recordAndStop(bar);
    assert.ok(bar.els.note.textContent.includes("Voice failed at transcribe"),
      bar.els.note.textContent);
    bar.ctl.stop();
  });
});
