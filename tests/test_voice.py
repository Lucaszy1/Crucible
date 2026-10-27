"""Hermetic voice tests: stubbed HTTP plus socketpair WS fakes.

No real network (the sandbox blocks TCP bind), no Docker, no model
spend. Spend accounting uses a temporary spend file with an isolated
limit.
"""

import base64
import hashlib
import io
import json
import socket
import struct
import threading
import urllib.error

import pytest

from crucible import llm, voice


@pytest.fixture()
def spend(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    monkeypatch.setenv("VOICE_API_KEY", "test-key")
    return tmp_path / "spend.jsonl"


def _clip(seconds=1.0):
    return voice.make_probe_wav(seconds=seconds)


# -- file endpoint (urlopen stub) ---------------------------------------


class _StubReply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _stub_urlopen(monkeypatch, seen, payload=b'{"text": "isolate db"}',
                  error=None):
    def fake_open(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["content_type"] = request.get_header("Content-type")
        seen["body"] = request.data
        seen["timeout"] = timeout
        if error is not None:
            raise error
        return _StubReply(payload)

    monkeypatch.setattr(voice.urllib.request, "urlopen", fake_open)


def test_transcribe_file_success(spend, monkeypatch):
    seen = {}
    _stub_urlopen(monkeypatch, seen,
                  b'{"text": "isolate the database"}')
    result = voice.transcribe_file(_clip(), exercise_id="voice-1")
    assert result["text"] == "isolate the database"
    assert result["seconds"] == pytest.approx(1.0, abs=0.01)
    assert result["cost_usd"] == pytest.approx(0.0001)
    assert seen["url"] == "https://api.meta.ai/v1/asr/transcribe"
    assert seen["auth"] == "Bearer test-key"
    assert seen["content_type"].startswith("multipart/form-data")
    assert b"RIFF" in seen["body"]
    assert b"judge.wav" in seen["body"]
    entries = [json.loads(line) for line in spend.read_text().splitlines()]
    assert len(entries) == 1
    assert entries[0]["model"] == voice.VOICE_MODEL
    assert entries[0]["cost_usd"] == pytest.approx(0.0001)


def test_transcribe_guard_blocks_before_network(spend, monkeypatch):
    seen = {}
    _stub_urlopen(monkeypatch, seen)
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "0.00001")
    with pytest.raises(llm.SpendLimitError):
        voice.transcribe_file(_clip())
    assert seen == {}


def test_transcribe_http_error(spend, monkeypatch):
    seen = {}
    error = urllib.error.HTTPError(
        "https://api.meta.ai/v1/asr/transcribe", 500, "bad",
        {}, io.BytesIO(b"bad"))
    _stub_urlopen(monkeypatch, seen, error=error)
    with pytest.raises(voice.VoiceError, match="HTTP 500") as caught:
        voice.transcribe_file(_clip())
    assert "test-key" not in str(caught.value)


def test_transcribe_timeout(spend, monkeypatch):
    seen = {}
    _stub_urlopen(monkeypatch, seen, error=socket.timeout("slow"))
    with pytest.raises(voice.VoiceError, match="failed"):
        voice.transcribe_file(_clip(), timeout_s=0.2)


def test_transcribe_missing_key(spend, monkeypatch):
    monkeypatch.delenv("VOICE_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(voice.VoiceError, match="no voice API key"):
        voice.transcribe_file(_clip())


def test_transcribe_empty_reply(spend, monkeypatch):
    seen = {}
    _stub_urlopen(monkeypatch, seen, b'{"text": ""}')
    with pytest.raises(voice.VoiceError, match="no transcript"):
        voice.transcribe_file(_clip())


def test_validate_wav_rejects():
    with pytest.raises(voice.VoiceError, match="empty"):
        voice.validate_wav(b"")
    with pytest.raises(voice.VoiceError, match="too large"):
        voice.validate_wav(b"x" * (voice.MAX_WAV_BYTES + 1))
    with pytest.raises(voice.VoiceError, match="readable WAV"):
        voice.validate_wav(b"not a wav file at all....")


def test_probe_wav_validates():
    facts = voice.validate_wav(voice.make_probe_wav(seconds=2.0))
    assert facts["seconds"] == pytest.approx(2.0, abs=0.01)
    assert facts["channels"] == 1
    assert voice.estimate_cost(2.0) < 0.25


# -- realtime endpoint (socketpair fake) ---------------------------------


def _ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    header = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header += bytes([length])
    elif length < 65536:
        header += bytes([126]) + struct.pack(">H", length)
    else:
        header += bytes([127]) + struct.pack(">Q", length)
    return header + payload


def _ws_pair(replies, script):
    """Socketpair with a fake WS server thread; returns the client end."""
    client, server = socket.socketpair()

    def read_exact(count):
        data = b""
        while len(data) < count:
            piece = server.recv(count - len(data))
            if not piece:
                raise ConnectionError("client went away")
            data += piece
        return data

    def run():
        with server:
            request = b""
            while b"\r\n\r\n" not in request:
                piece = server.recv(4096)
                if not piece:
                    return
                request += piece
            headers = request.decode("latin1")
            key = ""
            authed = False
            for line in headers.split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
                if line.lower().startswith("authorization: bearer "):
                    authed = True
            script.append({"authed": authed})
            accept = base64.b64encode(hashlib.sha1(
                (key + voice._WS_GUID).encode("ascii")).digest()).decode()
            server.sendall(f"HTTP/1.1 101 Switching Protocols\r\n"
                          f"Upgrade: websocket\r\n"
                          f"Connection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                          .encode("ascii"))
            first, second = read_exact(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", read_exact(8))[0]
            mask = read_exact(4)
            payload = read_exact(length)
            script.append({"opcode": first & 0x0F,
                           "masked": bool(second & 0x80),
                           "bytes": bytes(byte ^ mask[index % 4]
                                          for index, byte in
                                          enumerate(payload))})
            for reply in replies:
                if reply is None:
                    server.sendall(bytes([0x88, 0x00]))
                else:
                    server.sendall(_ws_frame(json.dumps(reply).encode()))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return client


def _handshaken(client):
    import os as _os

    voice._handshake(client, "api.meta.ai", 443, "/v1/asr/realtime",
                     "test-key",
                     base64.b64encode(_os.urandom(16)).decode("ascii"))
    return voice.RealtimeSession(client)


def test_realtime_round_trip():
    script = []
    client = _ws_pair(
        [{"type": "partial", "text": "isolate", "final": False},
         {"type": "transcript", "text": "isolate the database",
          "final": True}],
        script)
    session = _handshaken(client)
    try:
        session.send_audio(b"\x00\x01" * 800)
        first = session.recv_message(timeout_s=5.0)
        second = session.recv_message(timeout_s=5.0)
    finally:
        session.close()
    assert first == {"type": "partial", "text": "isolate", "final": False}
    assert second["text"] == "isolate the database"
    assert script[0]["authed"] is True
    assert script[1]["opcode"] == 0x2
    assert script[1]["masked"] is True
    assert script[1]["bytes"] == b"\x00\x01" * 800


def test_stream_transcribe_collects_final(monkeypatch):
    script = []
    client = _ws_pair(
        [{"type": "transcript", "text": "stand down", "final": True}],
        script)

    def fake_connect(url, api_key=None, timeout_s=10.0):
        return _handshaken(client)

    monkeypatch.setattr(voice.RealtimeSession, "connect", fake_connect)
    assert voice.stream_transcribe([b"\x00" * 320]) == "stand down"


def test_realtime_server_error(monkeypatch):
    client = _ws_pair([{"type": "error", "message": "nope"}], [])

    def fake_connect(url, api_key=None, timeout_s=10.0):
        return _handshaken(client)

    monkeypatch.setattr(voice.RealtimeSession, "connect", fake_connect)
    with pytest.raises(voice.VoiceError, match="nope"):
        voice.stream_transcribe([b"\x00" * 320])
