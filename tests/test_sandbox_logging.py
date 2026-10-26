"""Hermetic tests for container access logging. No Docker needed.

Runs the sandbox shell scripts directly with sh plus TOY_* overrides to
check their verdicts and exact log lines, and checks the tools invoke the
logging entry points with the source label.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from crucible import sandbox, tools

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sandbox" / "db"
WS = ROOT / "sandbox" / "workstation"
SH = shutil.which("sh") or "/bin/sh"

DB_SCRIPTS = ["signin.sh", "rotate-signin.sh", "reset-signin.sh",
              "read-file.sh", "list-files.sh"]


def run_script(path, args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        [SH, str(path), *args],
        capture_output=True, text=True, timeout=30, env=env,
    )


@pytest.fixture(autouse=True)
def clean_state():
    tools.reset_state()
    yield
    tools.reset_state()


def test_gate_scripts_pass_syntax_check():
    for name in DB_SCRIPTS:
        done = subprocess.run(
            [SH, "-n", str(DB / name)],
            capture_output=True, text=True, timeout=30,
        )
        assert done.returncode == 0, (name, done.stderr)


def test_db_signin_logs_ok_and_fail_without_password(tmp_path):
    user_file = tmp_path / "toy_user"
    pass_file = tmp_path / "toy_pass"
    log_file = tmp_path / "toy.log"
    user_file.write_text("admin", encoding="utf-8")
    pass_file.write_text("s3cr3t-value", encoding="utf-8")
    env = {
        "TOY_USER_FILE": str(user_file),
        "TOY_PASS_FILE": str(pass_file),
        "TOY_LOG_FILE": str(log_file),
    }
    good = run_script(DB / "signin.sh", ["admin", "s3cr3t-value", "controller"], env)
    assert good.stdout.strip() == "AUTH_OK"
    bad = run_script(DB / "signin.sh", ["admin", "wrong", "controller"], env)
    assert bad.stdout.strip() == "AUTH_FAIL"
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "signin ok user=admin from=controller",
        "signin fail user=admin from=controller",
    ]
    assert "s3cr3t-value" not in log_file.read_text(encoding="utf-8")


def test_db_signin_keeps_user_to_one_log_line(tmp_path):
    user_file = tmp_path / "toy_user"
    pass_file = tmp_path / "toy_pass"
    log_file = tmp_path / "toy.log"
    user_file.write_text("admin", encoding="utf-8")
    pass_file.write_text("s3cr3t-value", encoding="utf-8")
    env = {
        "TOY_USER_FILE": str(user_file),
        "TOY_PASS_FILE": str(pass_file),
        "TOY_LOG_FILE": str(log_file),
    }
    done = run_script(DB / "signin.sh", ["a b\nc", "wrong", "controller"], env)
    assert done.stdout.strip() == "AUTH_FAIL"
    assert log_file.read_text(encoding="utf-8").splitlines() == [
        "signin fail user=a_b_c from=controller"
    ]


def test_db_rotate_and_reset_log_outcomes(tmp_path):
    pass_file = tmp_path / "toy_pass"
    log_file = tmp_path / "toy.log"
    pass_file.write_text("changeme", encoding="utf-8")
    env = {"TOY_PASS_FILE": str(pass_file), "TOY_LOG_FILE": str(log_file)}
    rotated = run_script(DB / "rotate-signin.sh", ["controller"], env)
    assert rotated.stdout.strip() == "ROTATED_OK"
    assert pass_file.read_text(encoding="utf-8") == "r0tated-toy-9"
    user_file = tmp_path / "toy_user"
    env["TOY_USER_FILE"] = str(user_file)
    reset = run_script(DB / "reset-signin.sh", ["controller"], env)
    assert reset.stdout.strip() == "RESET_OK"
    assert pass_file.read_text(encoding="utf-8") == "changeme"
    assert log_file.read_text(encoding="utf-8").splitlines() == [
        "signin rotated from=controller",
        "signin reset from=controller",
    ]


def test_db_rotate_and_reset_without_gate_log_none(tmp_path):
    log_file = tmp_path / "toy.log"
    env = {
        "TOY_PASS_FILE": str(tmp_path / "missing"),
        "TOY_LOG_FILE": str(log_file),
    }
    rotated = run_script(DB / "rotate-signin.sh", ["controller"], env)
    assert rotated.stdout.strip() == "ROTATE_NONE"
    reset = run_script(DB / "reset-signin.sh", ["controller"], env)
    assert reset.stdout.strip() == "RESET_NONE"
    assert log_file.read_text(encoding="utf-8").splitlines() == [
        "signin rotate none from=controller",
        "signin reset none from=controller",
    ]


def test_read_file_wrapper_logs_path_and_bytes(tmp_path):
    data = tmp_path / "report.txt"
    data.write_bytes(b"quarterly report draft\n")
    log_file = tmp_path / "toy.log"
    env = {"TOY_LOG_FILE": str(log_file)}
    done = run_script(DB / "read-file.sh", [str(data), "controller"], env)
    assert done.returncode == 0
    assert done.stdout == "quarterly report draft\n"
    assert log_file.read_text(encoding="utf-8").splitlines() == [
        f"file read path={data} bytes=23 from=controller"
    ]


def test_read_file_wrapper_skips_log_on_missing_file(tmp_path):
    log_file = tmp_path / "toy.log"
    env = {"TOY_LOG_FILE": str(log_file)}
    done = run_script(DB / "read-file.sh", [str(tmp_path / "nope.txt"), "controller"], env)
    assert done.returncode != 0
    assert not log_file.exists()


def test_list_files_wrapper_logs_dir_and_entries(tmp_path):
    logged_dir = tmp_path / "data"
    logged_dir.mkdir()
    (logged_dir / "a.txt").write_text("a", encoding="utf-8")
    (logged_dir / "b.txt").write_text("b", encoding="utf-8")
    log_file = tmp_path / "toy.log"
    env = {"TOY_LOG_FILE": str(log_file)}
    done = run_script(DB / "list-files.sh", [str(logged_dir), "controller"], env)
    assert done.returncode == 0
    assert done.stdout.splitlines() == ["a.txt", "b.txt"]
    assert log_file.read_text(encoding="utf-8").splitlines() == [
        f"dir list path={logged_dir} entries=2 from=controller"
    ]


def test_list_files_wrapper_skips_log_on_missing_dir(tmp_path):
    log_file = tmp_path / "toy.log"
    env = {"TOY_LOG_FILE": str(log_file)}
    done = run_script(DB / "list-files.sh", [str(tmp_path / "nope"), "controller"], env)
    assert done.returncode != 0
    assert not log_file.exists()


def test_credential_passes_log_source(monkeypatch):
    seen = {}

    def fake_exec(host, cmd, timeout=30):
        seen["cmd"] = cmd
        text = "AUTH_OK\n"
        return {"exit_code": 0, "output": text, "stdout": text, "stderr": ""}

    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "exec", fake_exec)
    out = tools.try_credential("db", "admin", "changeme")
    assert out["authenticated"] is True
    assert seen["cmd"] == ["signin", "admin", "changeme", "controller"]


def test_credential_no_gate_verdict_keeps_note(monkeypatch):
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(
        sandbox, "exec",
        lambda host, cmd, timeout=30: {"exit_code": 0, "output": "NO_GATE\n",
                                      "stdout": "NO_GATE\n", "stderr": ""})
    out = tools.try_credential("workstation", "admin", "changeme")
    assert out == {"ok": True, "host": "workstation", "authenticated": False,
                   "note": "no sign-in gate on 'workstation'"}


def test_file_tools_use_logging_wrappers(monkeypatch):
    seen = []

    def fake_exec(host, cmd, timeout=30):
        seen.append(cmd)
        if cmd[0] == "signin":
            text = "AUTH_OK\n"
        elif cmd[0] == "read-file":
            text = "secret bytes"
        else:
            text = "a.txt\n"
        return {"exit_code": 0, "output": text, "stdout": text, "stderr": ""}

    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "exec", fake_exec)
    tools.try_credential("db", "admin", "changeme")
    assert tools.read_file("db", "/secret/exercise_secret.txt")["ok"] is True
    assert tools.list_files("db", "/secret/")["ok"] is True
    assert ["read-file", "/secret/exercise_secret.txt", "controller"] in seen
    assert ["list-files", "/secret", "controller"] in seen


def test_patch_and_restore_pass_log_source(monkeypatch):
    seen = []
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "reattach",
                        lambda host: {"host": host, "isolated": False})

    def fake_exec(host, cmd, timeout=30):
        seen.append(cmd)
        if cmd[0] == "rotate-signin":
            text = "ROTATED_OK\n"
        elif cmd[0] == "reset-signin":
            text = "RESET_OK\n"
        else:
            text = ""
        return {"exit_code": 0, "output": text, "stdout": text, "stderr": ""}

    monkeypatch.setattr(sandbox, "exec", fake_exec)
    tools.patch_service("db", "fakestore")
    tools.restore_host("db")
    assert ["rotate-signin", "controller"] in seen
    assert ["reset-signin", "controller"] in seen
