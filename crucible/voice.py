"""Client for Muse Voice Transcribe (file and realtime).

File endpoint: POST {base}/v1/asr/transcribe with WAV audio only.
Realtime endpoint: wss://api.meta.ai/v1/asr/realtime over a minimal
stdlib WebSocket client (no new dependencies).

Audio spend flows through the existing guard: every call estimates
seconds * USD_PER_SECOND, blocks past the daily cap, and records the
entry in spend.jsonl. Keys never appear in logs or error text.

Assumed contracts (no public spec was available; see docs/VOICE_API.md
and the report risks): the file endpoint accepts multipart form audio
and answers JSON with a text field; the realtime endpoint takes masked
binary PCM frames and answers JSON text messages. The live probe
covers only the file endpoint.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import socket
import ssl
import struct
import urllib.error
import urllib.parse
import urllib.request
import wave

VOICE_MODEL = "muse-voice-transcribe"
DEFAULT_BASE_URL = "https://api.meta.ai"
DEFAULT_REALTIME_URL = "wss://api.meta.ai/v1/asr/realtime"
FILE_PATH = "/v1/asr/transcribe"

MAX_WAV_BYTES = 5 * 1024 * 1024
MAX_DURATION_S = 60.0
MIN_FRAMERATE = 8000
MAX_FRAMERATE = 48000

# Placeholder until measured billing lands: $0.006 per audio minute.
# Feeds the spend guard only, never any user-facing invoice.
USD_PER_SECOND = 0.0001

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class VoiceError(RuntimeError):
    """Transcription failed; safe to show the judge (no secrets)."""


def _api_key(explicit: str | None = None) -> str:
    key = (explicit or os.environ.get("VOICE_API_KEY", "")
           or os.environ.get("MODEL_API_KEY", "")).strip()
    if not key:
        raise VoiceError("no voice API key: set VOICE_API_KEY "
                         "(or MODEL_API_KEY) first")
    return key


def validate_wav(data: bytes) -> dict:
    """Check WAV bytes; return format facts or raise VoiceError."""
    if not data:
        raise VoiceError("empty audio: no bytes received")
    if len(data) > MAX_WAV_BYTES:
        raise VoiceError(f"audio too large: {len(data)} bytes "
                         f"(cap {MAX_WAV_BYTES})")
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
    except (wave.Error, EOFError, struct.error) as exc:
        raise VoiceError(f"not a readable WAV file: {exc}") from None
    if channels not in (1, 2):
        raise VoiceError(f"WAV needs 1-2 channels, got {channels}")
    if width not in (1, 2):
        raise VoiceError(f"WAV needs 8/16-bit samples, got {width * 8}-bit")
    if not MIN_FRAMERATE <= rate <= MAX_FRAMERATE:
        raise VoiceError(f"WAV rate {rate} Hz outside "
                         f"{MIN_FRAMERATE}-{MAX_FRAMERATE} Hz")
    seconds = frames / rate if rate else 0.0
    if seconds <= 0:
        raise VoiceError("WAV holds no audio frames")
    if seconds > MAX_DURATION_S:
        raise VoiceError(f"audio {seconds:.1f}s exceeds the "
                         f"{MAX_DURATION_S:.0f}s cap")
    return {"seconds": seconds, "channels": channels,
            "sample_width": width, "framerate": rate,
            "frames": frames, "bytes": len(data)}


def estimate_cost(seconds: float) -> float:
    """Placeholder audio cost from the per-second rate."""
    return round(max(0.0, float(seconds)) * USD_PER_SECOND, 6)


def _multipart(fields: dict, file_bytes: bytes,
               filename: str = "judge.wav") -> tuple:
    boundary = "----crucible-voice-" + base64.urlsafe_b64encode(
        os.urandom(12)).decode("ascii")
    parts = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\n"
                     f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                     f"{value}\r\n".encode("utf-8"))
    parts.append(f"--{boundary}\r\n"
                 f'Content-Disposition: form-data; name="file"; '
                 f'filename="{filename}"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode("utf-8")
                 + file_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    return body, f"multipart/form-data; boundary={boundary}"


def transcribe_file(wav_bytes: bytes, *, exercise_id: str | None = None,
                    timeout_s: float = 30.0, base_url: str | None = None,
                    api_key: str | None = None,
                    model: str = VOICE_MODEL) -> dict:
    """Transcribe one WAV clip via the file endpoint.

    Returns {"text", "seconds", "cost_usd"}. Spend is guarded before
    any network traffic and recorded after a successful reply.
    """
    from crucible import llm

    facts = validate_wav(wav_bytes)
    key = _api_key(api_key)
    cost = estimate_cost(facts["seconds"])
    llm.check_spend_guard(cost)
    base = (base_url or os.environ.get("VOICE_BASE_URL", "")
            or DEFAULT_BASE_URL).rstrip("/")
    url = base + FILE_PATH
    body, content_type = _multipart({"model": model}, wav_bytes)
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": content_type,
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request,
                                    timeout=timeout_s) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        raise VoiceError(f"transcribe failed: HTTP {exc.code} "
                         f"{detail}") from None
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise VoiceError(f"transcribe request failed: {exc}") from None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise VoiceError("transcribe reply was not JSON") from None
    text = ""
    if isinstance(payload, dict):
        text = str(payload.get("text", "")
                   or payload.get("transcript", "")
                   or (payload.get("data", {}) or {}).get("text", "")
                   or "").strip()
    if not text:
        raise VoiceError("transcribe reply held no transcript text")
    llm.record_spend(model=model, input_tokens=0, output_tokens=0,
                     exercise_id=exercise_id, cost_usd=cost)
    return {"text": text, "seconds": facts["seconds"], "cost_usd": cost}


def _ws_accept(key: str) -> str:
    digest = hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _handshake(sock: socket.socket, host: str, port: int, path: str,
               key: str, ws_key: str) -> None:
    """Run the WS upgrade handshake over a connected socket."""
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Authorization: Bearer {key}\r\n\r\n"
    )
    try:
        sock.sendall(request.encode("ascii"))
        reply = b""
        while b"\r\n\r\n" not in reply:
            piece = sock.recv(4096)
            if not piece:
                break
            reply += piece
            if len(reply) > 16384:
                break
    except (OSError, socket.timeout) as exc:
        raise VoiceError(f"realtime handshake failed: {exc}") from None
    text = reply.decode("latin1", "replace")
    if " 101 " not in text.split("\r\n", 1)[0]:
        raise VoiceError("realtime handshake rejected (need HTTP 101)")
    accept = ""
    for line in text.split("\r\n"):
        if line.lower().startswith("sec-websocket-accept:"):
            accept = line.split(":", 1)[1].strip()
    if accept != _ws_accept(ws_key):
        raise VoiceError("realtime handshake accept mismatch")


def _read_exact(sock: socket.socket, count: int) -> bytes:
    chunks = []
    while sum(len(part) for part in chunks) < count:
        piece = sock.recv(count - sum(len(part) for part in chunks))
        if not piece:
            raise VoiceError("realtime connection closed mid-frame")
        chunks.append(piece)
    return b"".join(chunks)


class RealtimeSession:
    """One realtime transcription session (minimal WS client).

    Connects, sends masked binary audio frames, and reads JSON text
    messages until the server sends a final transcript or closes.
    """

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._closed = False

    @classmethod
    def connect(cls, url: str = DEFAULT_REALTIME_URL, *,
                api_key: str | None = None,
                timeout_s: float = 10.0) -> "RealtimeSession":
        """Open a session: TCP (+TLS for wss) plus WS handshake."""
        key = _api_key(api_key)
        parts = urllib.parse.urlparse(url)
        if parts.scheme not in ("ws", "wss"):
            raise VoiceError(f"realtime url must be ws(s): {url}")
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        if not host:
            raise VoiceError("realtime url has no host")
        try:
            sock = socket.create_connection((host, port),
                                            timeout=timeout_s)
            if parts.scheme == "wss":
                context = ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=host)
            sock.settimeout(timeout_s)
        except (OSError, ssl.SSLError) as exc:
            raise VoiceError(f"realtime connect failed: {exc}") from None
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        try:
            _handshake(sock, host, port, path, key, ws_key)
        except VoiceError:
            sock.close()
            raise
        return cls(sock)

    def send_audio(self, chunk: bytes) -> None:
        """Send one masked binary audio frame."""
        if self._closed:
            raise VoiceError("realtime session is closed")
        if not chunk:
            return
        mask = os.urandom(4)
        header = bytes([0x82])
        length = len(chunk)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        masked = bytes(byte ^ mask[index % 4]
                       for index, byte in enumerate(chunk))
        try:
            self._sock.sendall(header + mask + masked)
        except OSError as exc:
            raise VoiceError(f"realtime send failed: {exc}") from None

    def _send_control(self, opcode: int, payload: bytes = b"") -> None:
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4]
                       for index, byte in enumerate(payload))
        try:
            self._sock.sendall(bytes([0x80 | opcode, 0x80 | len(payload)])
                               + mask + masked)
        except OSError:
            pass

    def recv_message(self, timeout_s: float = 30.0) -> dict | None:
        """Read the next JSON message; None on clean server close."""
        if self._closed:
            return None
        self._sock.settimeout(timeout_s)
        data = bytearray()
        try:
            while True:
                first, second = _read_exact(self._sock, 2)
                fin = bool(first & 0x80)
                opcode = first & 0x0F
                length = second & 0x7F
                if length == 126:
                    length = struct.unpack(
                        ">H", _read_exact(self._sock, 2))[0]
                elif length == 127:
                    length = struct.unpack(
                        ">Q", _read_exact(self._sock, 8))[0]
                if second & 0x80:
                    mask = _read_exact(self._sock, 4)
                else:
                    mask = None
                payload = _read_exact(self._sock, length)
                if mask is not None:
                    payload = bytes(byte ^ mask[index % 4]
                                    for index, byte in enumerate(payload))
                if opcode == 0x9:
                    self._send_control(0xA, payload)
                    continue
                if opcode == 0x8:
                    self._send_control(0x8)
                    self._closed = True
                    return None
                if opcode in (0x1, 0x2, 0x0):
                    data += payload
                    if fin:
                        break
        except (OSError, socket.timeout) as exc:
            raise VoiceError("timed out waiting for transcript") from exc
        try:
            message = json.loads(bytes(data).decode("utf-8"))
        except ValueError:
            raise VoiceError("realtime reply was not JSON") from None
        if not isinstance(message, dict):
            raise VoiceError("realtime reply was not an object")
        return message

    def close(self) -> None:
        """Send close and release the socket."""
        if self._closed:
            return
        self._closed = True
        try:
            self._send_control(0x8)
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        finally:
            try:
                self._sock.close()
            except OSError:
                pass


def stream_transcribe(chunks, *, url: str = DEFAULT_REALTIME_URL,
                      api_key: str | None = None,
                      timeout_s: float = 30.0) -> str:
    """Send audio chunks and return the final transcript text."""
    session = RealtimeSession.connect(url, api_key=api_key,
                                      timeout_s=timeout_s)
    try:
        for chunk in chunks:
            session.send_audio(bytes(chunk))
        while True:
            message = session.recv_message(timeout_s=timeout_s)
            if message is None:
                raise VoiceError("realtime closed before a final "
                                 "transcript arrived")
            if message.get("type") == "error":
                raise VoiceError(f"realtime error: "
                                 f"{message.get('message', '?')}"[:200])
            if message.get("final"):
                text = str(message.get("text", "") or "").strip()
                if not text:
                    raise VoiceError("realtime final held no text")
                return text
    finally:
        session.close()


def make_probe_wav(seconds: float = 2.0,
                   framerate: int = 16000) -> bytes:
    """Generate a short local WAV (soft tone) for the live probe."""
    frames = int(seconds * framerate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(framerate)
        for index in range(frames):
            env = math.exp(-3.0 * index / frames)
            sample = int(9000 * env
                         * math.sin(2 * math.pi * 440 * index / framerate))
            buffer2 = struct.pack("<h", sample)
            handle.writeframes(buffer2)
    return buffer.getvalue()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Muse Voice Transcribe client.")
    parser.add_argument("--probe", action="store_true",
                        help="Transcribe one generated 2s clip and report "
                        "the text plus cost (tiny model spend)")
    args = parser.parse_args(argv)
    if not args.probe:
        parser.print_help()
        return 1
    try:
        result = transcribe_file(make_probe_wav(),
                                 exercise_id="voice-probe")
    except VoiceError as exc:
        print(f"probe failed: {exc}")
        return 1
    print(f"probe transcript: {result['text']!r}")
    print(f"probe audio: {result['seconds']:.2f}s, "
          f"cost ${result['cost_usd']:.6f} (placeholder rate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())