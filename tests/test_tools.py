"""Unit tests for agent tools. Hermetic: sandbox backends stay stubbed."""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from crucible import sandbox, tools

HOST_TOOLS = [
    lambda h: tools.scan_host(h),
    lambda h: tools.http_request(h, "/"),
    lambda h: tools.try_credential(h, "admin", "changeme"),
    lambda h: tools.read_file(h, "/srv/data/notes.txt"),
    lambda h: tools.read_logs(h),
    lambda h: tools.list_processes(h),
    lambda h: tools.isolate_host(h),
    lambda h: tools.patch_service(h, "webapp"),
    lambda h: tools.restore_host(h),
]

OFF_RANGE = [
    "evil.example.com",
    "web.evil.com",
    " WEB",
    "web ",
    "Web",
    "",
    "../db",
    "db; rm -rf /",
    None,
    123,
]


@pytest.fixture(autouse=True)
def clean_state():
    tools.reset_state()
    yield
    tools.reset_state()


def test_every_host_tool_rejects_off_range_hosts():
    for tool in HOST_TOOLS:
        for bad in OFF_RANGE:
            with pytest.raises(tools.AllowlistError):
                tool(bad)


def test_scan_reports_down_host(monkeypatch):
    monkeypatch.setattr(sandbox, "is_running", lambda host: False)
    out = tools.scan_host("web")
    assert out["ok"] is True
    assert out["reachable"] is False


def test_scan_reports_live_host(monkeypatch):
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "is_isolated", lambda host: False)
    out = tools.scan_host("web")
    assert out == {"ok": True, "host": "web", "running": True,
                   "reachable": True, "ports": [8000], "isolated": False}


def test_http_rejects_bad_args():
    with pytest.raises(ValueError):
        tools.http_request("web", "/", method="DELETE")
    with pytest.raises(ValueError):
        tools.http_request("web", "no-leading-slash")
    with pytest.raises(ValueError):
        tools.http_request("web", "/has space")
    with pytest.raises(ValueError):
        tools.http_request("web", "/../etc/passwd")
    with pytest.raises(ValueError):
        tools.http_request("web", "/", body="x" * 9000)


def test_http_needs_declared_service():
    out = tools.http_request("db", "/")
    assert out["ok"] is False
    assert "no http service" in out["error"]


def test_http_roundtrip_through_relay(monkeypatch):
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    seen = {}

    def fake_exec(host, cmd, timeout=30):
        seen["host"] = host
        assert cmd[0] == "python"
        text = '{"status": 200, "body": "hi"}'
        return {"exit_code": 0, "output": text, "stdout": text, "stderr": ""}

    monkeypatch.setattr(sandbox, "exec", fake_exec)
    out = tools.http_request("web", "/admin")
    assert out == {"ok": True, "host": "web", "status": 200, "body": "hi"}
    assert seen["host"] == "workstation"


def test_relay_script_compiles():
    script = tools._relay_script("http://web:8000/admin", "GET", "None")
    compile(script, "<relay>", "exec")


class _ProbeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"probe": "up"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_relay_script_fetches_real_http():
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
    except OSError:
        pytest.skip("this shell forbids binding a local probe port")
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        port = server.server_address[1]
        script = tools._relay_script(f"http://127.0.0.1:{port}/admin", "GET", "None")
        done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, timeout=30)
    finally:
        server.shutdown()
        server.server_close()
    assert done.returncode == 0, done.stderr
    parsed = json.loads(done.stdout.strip().splitlines()[-1])
    assert parsed["status"] == 200
    assert "probe" in parsed["body"]


def test_relay_script_reports_connection_errors_as_json():
    script = tools._relay_script("http://127.0.0.1:1/closed", "GET", "None")
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    parsed = json.loads(done.stdout.strip().splitlines()[-1])
    assert parsed["status"] is None
    assert parsed["error"]


def test_http_error_carries_stream_excerpts(monkeypatch):
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(
        sandbox, "exec",
        lambda host, cmd, timeout=30: {"exit_code": 1, "output": "junk\n",
                                       "stdout": "junk\n",
                                       "stderr": "Traceback: boom\n"})
    out = tools.http_request("web", "/admin")
    assert out["ok"] is False
    assert "exit=1" in out["error"]
    assert "stdout='junk" in out["error"]
    assert "Traceback: boom" in out["error"]


def test_credential_flow():
    assert tools.try_credential("db", "admin", "wrong")["authenticated"] is False
    assert tools.try_credential("db", "admin", "changeme")["authenticated"] is True
    with pytest.raises(ValueError):
        tools.try_credential("db", "", "changeme")


def test_read_file_gates_and_validates(monkeypatch):
    monkeypatch.setattr(
        sandbox, "exec",
        lambda host, cmd, timeout=30: {"exit_code": 0, "output": "data",
                                       "stdout": "data", "stderr": ""}),
    with pytest.raises(ValueError):
        tools.read_file("web", "relative/path.txt")
    with pytest.raises(ValueError):
        tools.read_file("web", "/etc/passwd")
    gated = tools.read_file("db", "/secret/exercise_secret.txt")
    assert gated["ok"] is False
    assert "sign-in" in gated["error"]
    tools.try_credential("db", "admin", "changeme")
    opened = tools.read_file("db", "/secret/exercise_secret.txt")
    assert opened["ok"] is True


def test_report_finding_ids():
    first = tools.report_finding("open admin page seen")
    second = tools.report_finding("default sign-in worked")
    assert (first["id"], second["id"]) == ("finding-1", "finding-2")
    with pytest.raises(ValueError):
        tools.report_finding("  ")


def test_read_logs_shapes_lines(monkeypatch):
    monkeypatch.setattr(sandbox, "container_logs",
                        lambda host, tail=100: ["a", "b"])
    out = tools.read_logs("web", 2)
    assert out["lines"] == [{"id": "web:0", "line": "a"},
                            {"id": "web:1", "line": "b"}]
    with pytest.raises(ValueError):
        tools.read_logs("web", True)


def test_backend_failures_return_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise sandbox.SandboxError("daemon away")

    monkeypatch.setattr(sandbox, "exec", boom)
    monkeypatch.setattr(sandbox, "container_logs", boom)
    monkeypatch.setattr(sandbox, "isolate", boom)
    monkeypatch.setattr(sandbox, "reattach", boom)
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "is_isolated", lambda host: False)
    assert tools.read_logs("web")["ok"] is False
    assert tools.list_processes("web")["ok"] is False
    assert tools.isolate_host("web")["ok"] is False
    assert tools.read_file("web", "/srv/data/notes.txt")["ok"] is False
    assert tools.patch_service("web", "webapp")["ok"] is False
    assert tools.restore_host("web")["ok"] is False


def test_block_ip_validates_and_dedupes():
    out = tools.block_ip("10.0.0.9")
    assert out == {"ok": True, "ip": "10.0.0.9", "blocked": ["10.0.0.9"]}
    tools.block_ip("10.0.0.9")
    assert tools.block_ip("10.0.0.10")["blocked"] == ["10.0.0.9", "10.0.0.10"]
    for bad in ("999.1.1.1", "not-an-ip", "::1", "", None):
        with pytest.raises(ValueError):
            tools.block_ip(bad)


def test_patch_rejects_unknown_service():
    with pytest.raises(ValueError):
        tools.patch_service("web", "no-such-service")
