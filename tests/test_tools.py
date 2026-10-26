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
    lambda h: tools.list_files(h, "/srv/data/"),
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


def _gate_exec(live):
    def fake_exec(host, cmd, timeout=30):
        assert cmd[0] == "signin"
        good = cmd[1] == live["user"] and cmd[2] == live["pass"]
        text = "AUTH_OK\n" if good else "AUTH_FAIL\n"
        return {"exit_code": 0, "output": text, "stdout": text, "stderr": ""}
    return fake_exec


def test_credential_flow(monkeypatch):
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "exec",
                        _gate_exec({"user": "admin", "pass": "changeme"}))
    assert tools.try_credential("db", "admin", "wrong")["authenticated"] is False
    assert tools.try_credential("db", "admin", "changeme")["authenticated"] is True
    with pytest.raises(ValueError):
        tools.try_credential("db", "", "changeme")


def test_credential_follows_container_not_docs(monkeypatch):
    """A doc-listed default means nothing when the live gate disagrees."""
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "exec",
                        _gate_exec({"user": "admin", "pass": "r0tated-toy-9"}))
    assert tools.try_credential("db", "admin", "changeme")["authenticated"] is False
    assert tools.try_credential("db", "admin", "r0tated-toy-9")["authenticated"] is True


def test_credential_needs_host_up(monkeypatch):
    monkeypatch.setattr(sandbox, "is_running", lambda host: False)
    out = tools.try_credential("db", "admin", "changeme")
    assert out["ok"] is False
    assert "not up" in out["error"]


def test_credential_without_gate(monkeypatch):
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(
        sandbox, "exec",
        lambda host, cmd, timeout=30: {"exit_code": 127, "output": "",
                                       "stdout": "", "stderr": "not found"})
    out = tools.try_credential("web", "admin", "changeme")
    assert out["authenticated"] is False
    assert "no sign-in gate" in out["note"]


def test_read_file_gates_and_validates(monkeypatch):
    def fake_exec(host, cmd, timeout=30):
        text = "AUTH_OK\n" if cmd[0] == "signin" else "data"
        return {"exit_code": 0, "output": text, "stdout": text, "stderr": ""}

    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "exec", fake_exec)
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


def test_list_files_gates_and_validates(monkeypatch):
    def fake_exec(host, cmd, timeout=30):
        if cmd[0] == "signin":
            return {"exit_code": 0, "output": "AUTH_OK\n",
                    "stdout": "AUTH_OK\n", "stderr": ""}
        assert cmd[0] == "list-files"
        assert cmd[2] == "controller"
        return {"exit_code": 0, "output": "a.txt\nb.txt\n",
                "stdout": "a.txt\nb.txt\n", "stderr": ""}

    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "exec", fake_exec)
    with pytest.raises(ValueError):
        tools.list_files("web", "relative/dir")
    with pytest.raises(ValueError):
        tools.list_files("web", "/etc")
    gated = tools.list_files("db", "/secret/")
    assert gated["ok"] is False
    assert "sign-in" in gated["error"]
    tools.try_credential("db", "admin", "changeme")
    opened = tools.list_files("db", "/secret/")
    assert opened == {"ok": True, "host": "db", "path": "/secret",
                      "files": ["a.txt", "b.txt"]}
    listed = tools.list_files("web", "/srv/data/")
    assert listed["ok"] is True
    assert listed["files"] == ["a.txt", "b.txt"]


def test_list_files_backend_failure_returns_error(monkeypatch):
    def boom(*args, **kwargs):
        raise sandbox.SandboxError("daemon away")

    monkeypatch.setattr(sandbox, "exec", boom)
    out = tools.list_files("web", "/srv/data/")
    assert out["ok"] is False


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


def test_read_logs_ids_count_from_first_line(monkeypatch):
    seen = {}
    stored = ["a", "b", "c", "d"]

    def fake_logs(host, tail=100):
        seen["tail"] = tail
        return list(stored)

    monkeypatch.setattr(sandbox, "container_logs", fake_logs)
    out = tools.read_logs("db", 2)
    assert out["lines"] == [{"id": "db:2", "line": "c"},
                            {"id": "db:3", "line": "d"}]
    assert seen["tail"] is None
    stored.append("e")
    again = tools.read_logs("db", 3)
    assert [entry["id"] for entry in again["lines"]] == ["db:2", "db:3", "db:4"]


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


def _patch_exec(rotate_text):
    def fake_exec(host, cmd, timeout=30):
        text = rotate_text if cmd[0] == "rotate-signin" else ""
        code = 0 if cmd[0] in ("touch", "rotate-signin") else 127
        return {"exit_code": code, "output": text, "stdout": text, "stderr": ""}
    return fake_exec


def test_patch_rotates_gate_when_present(monkeypatch):
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "exec", _patch_exec("ROTATED_OK\n"))
    out = tools.patch_service("db", "fakestore")
    assert out["ok"] is True
    assert "rotated" in out["detail"]


def test_patch_skips_rotation_without_gate(monkeypatch):
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "exec", _patch_exec(""))
    out = tools.patch_service("web", "webapp")
    assert out == {"ok": True, "host": "web", "service": "webapp",
                   "detail": "patch flag applied"}


def test_wait_is_logged_noop():
    out = tools.wait()
    assert out["ok"] is True
    assert out["waited"] is True


def test_restore_resets_gate(monkeypatch):
    def fake_exec(host, cmd, timeout=30):
        text = "RESET_OK\n" if cmd[0] == "reset-signin" else ""
        return {"exit_code": 0, "output": text, "stdout": text, "stderr": ""}

    monkeypatch.setattr(sandbox, "reattach",
                        lambda host: {"host": host, "isolated": False})
    monkeypatch.setattr(sandbox, "exec", fake_exec)
    out = tools.restore_host("db")
    assert out["sign_in_reset"] is True
    assert out["patch_cleared"] is True
