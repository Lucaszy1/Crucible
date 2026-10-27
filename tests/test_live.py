"""Live integration tests against the real sandbox range.

Boot the range first, then run only these:
    python -c "from crucible import sandbox; sandbox.start()"
    pytest tests/test_live.py -v -m live

Each test leaves its hosts restored, so the file stays repeatable without
rebuilding the images.
"""

import pytest

from crucible import sandbox, tools

pytestmark = pytest.mark.live

BOOT_HINT = (
    "sandbox is not up; run: pip install -r requirements.txt, then "
    'python -c "from crucible import sandbox; sandbox.start()"'
)
ROTATED_PASS = "r0tated-toy-9"


@pytest.fixture()
def live_range():
    tools.reset_state()
    if not all(sandbox.status().values()):
        pytest.skip(BOOT_HINT)
    yield
    tools.reset_state()


def _shipped_default(host="db"):
    return sandbox.load_scenario()["shipped_defaults"][host]


def test_admin_roundtrip_returns_real_body(live_range):
    out = tools.http_request("web", "/admin")
    assert out["ok"] is True, out
    assert out["status"] == 200
    assert "toy misconfiguration" in out["body"]
    assert "'app'" in out["body"]
    assert "'svc-report'" in out["body"]
    assert "password" not in out["body"].lower()


def test_root_post_and_missing_paths(live_range):
    root = tools.http_request("web", "/")
    assert root["ok"] is True, root
    assert root["status"] == 200
    assert "up" in root["body"]
    posted = tools.http_request("web", "/", method="POST", body="x=1")
    assert posted["ok"] is True, posted
    assert posted["status"] == 404
    missing = tools.http_request("web", "/no-such-page")
    assert missing["ok"] is True, missing
    assert missing["status"] == 404


def test_signin_and_secret_flow(live_range):
    tools.restore_host("app")
    tools.restore_host("db")
    app_default = _shipped_default("app")
    db_default = _shipped_default("db")
    app_wrong = tools.try_credential("app", app_default["user"], "not-the-pass")
    assert app_wrong == {"ok": True, "host": "app", "authenticated": False}
    app_right = tools.try_credential("app", app_default["user"], app_default["pass"])
    assert app_right == {"ok": True, "host": "app", "authenticated": True}
    config = tools.read_file("app", "/srv/app/config.ini")
    assert config["ok"] is True, config
    assert db_default["user"] in config["content"]
    assert db_default["pass"] in config["content"]
    stale = tools.try_credential("db", "admin", "changeme")
    assert stale["authenticated"] is False, stale
    db_wrong = tools.try_credential("db", db_default["user"], "not-the-pass")
    assert db_wrong == {"ok": True, "host": "db", "authenticated": False}
    db_right = tools.try_credential("db", db_default["user"], db_default["pass"])
    assert db_right == {"ok": True, "host": "db", "authenticated": True}
    listed = tools.list_files("db", "/secret/")
    assert listed["ok"] is True, listed
    secret = tools.read_file("db", "/secret/exercise_secret.txt")
    assert secret["ok"] is True, secret
    assert "CRUCIBLE-FAKE-SECRET" in secret["content"]


def test_isolation_cuts_relay_traffic(live_range):
    try:
        tools.isolate_host("web")
        scan = tools.scan_host("web")
        assert scan["reachable"] is False, scan
        cut = tools.http_request("web", "/")
        assert cut["ok"] is False, cut
    finally:
        tools.restore_host("web")
    back = tools.http_request("web", "/")
    assert back["ok"] is True, back
    assert back["status"] == 200


def test_logs_show_traffic(live_range):
    tools.http_request("web", "/admin")
    logs = tools.read_logs("web")
    assert logs["ok"] is True, logs
    assert any("request from=" in entry["line"] for entry in logs["lines"])
    procs = tools.list_processes("web")
    assert procs["ok"] is True, procs
    assert procs["processes"]


def test_app_logs_capture_signin_and_read(live_range):
    tools.restore_host("app")
    default = _shipped_default("app")
    wrong = tools.try_credential("app", default["user"], "not-the-pass")
    assert wrong["authenticated"] is False, wrong
    right = tools.try_credential("app", default["user"], default["pass"])
    assert right["authenticated"] is True, right
    config = tools.read_file("app", "/srv/app/config.ini")
    assert config["ok"] is True, config
    logs = tools.read_logs("app", n=200)
    assert logs["ok"] is True, logs
    assert logs["lines"], "app produced no log lines for the sign-in flow"
    text = "\n".join(entry["line"] for entry in logs["lines"])
    assert f"signin fail user={default['user']} from=" in text
    assert f"signin ok user={default['user']} from=" in text
    assert ("file read path=/srv/app/config.ini "
            f"bytes={len(config['content'])} from=") in text


def test_db_logs_capture_signin_list_and_read(live_range):
    tools.restore_host("db")
    default = _shipped_default("db")
    wrong = tools.try_credential("db", default["user"], "not-the-pass")
    assert wrong["authenticated"] is False, wrong
    right = tools.try_credential("db", default["user"], default["pass"])
    assert right["authenticated"] is True, right
    listed = tools.list_files("db", "/secret/")
    assert listed["ok"] is True, listed
    secret = tools.read_file("db", "/secret/exercise_secret.txt")
    assert secret["ok"] is True, secret
    logs = tools.read_logs("db", n=200)
    assert logs["ok"] is True, logs
    assert logs["lines"], "db produced no log lines for the sign-in flow"
    text = "\n".join(entry["line"] for entry in logs["lines"])
    assert f"signin fail user={default['user']} from=" in text
    assert f"signin ok user={default['user']} from=" in text
    assert f"dir list path=/secret entries={len(listed['files'])} from=" in text
    assert ("file read path=/secret/exercise_secret.txt "
            f"bytes={len(secret['content'])} from=") in text
    assert default["pass"] not in text


def test_db_logs_capture_rotate_and_reset(live_range):
    try:
        tools.restore_host("db")
        patched = tools.patch_service("db", "fakestore")
        assert patched["ok"] is True, patched
        logs = tools.read_logs("db", n=200)
        assert logs["ok"] is True, logs
        text = "\n".join(entry["line"] for entry in logs["lines"])
        assert "signin rotated from=" in text
    finally:
        tools.restore_host("db")
    logs = tools.read_logs("db", n=200)
    assert logs["ok"] is True, logs
    text = "\n".join(entry["line"] for entry in logs["lines"])
    assert "signin reset from=" in text


def test_app_logs_capture_rotate_and_reset(live_range):
    try:
        tools.restore_host("app")
        patched = tools.patch_service("app", "reportapi")
        assert patched["ok"] is True, patched
        logs = tools.read_logs("app", n=200)
        assert logs["ok"] is True, logs
        text = "\n".join(entry["line"] for entry in logs["lines"])
        assert "signin rotated from=" in text
    finally:
        tools.restore_host("app")
    logs = tools.read_logs("app", n=200)
    assert logs["ok"] is True, logs
    text = "\n".join(entry["line"] for entry in logs["lines"])
    assert "signin reset from=" in text


def test_db_log_ids_stay_stable(live_range):
    first = tools.read_logs("db", n=200)
    assert first["ok"] is True, first
    second = tools.read_logs("db", n=200)
    assert second["ok"] is True, second
    first_ids = [entry["id"] for entry in first["lines"]]
    second_ids = [entry["id"] for entry in second["lines"]]
    assert first_ids == second_ids
    numbers = [int(entry_id.split(":")[1]) for entry_id in second_ids]
    if numbers:
        assert numbers == list(range(numbers[0], numbers[0] + len(numbers)))


def test_workstation_logs_file_reads_and_signin(live_range):
    noted = tools.read_file("workstation", "/srv/data/notes.txt")
    assert noted["ok"] is True, noted
    denied = tools.try_credential("workstation", "admin", "changeme")
    assert denied["authenticated"] is False, denied
    assert "no sign-in gate" in denied.get("note", "")
    logs = tools.read_logs("workstation", n=200)
    assert logs["ok"] is True, logs
    assert logs["lines"], "workstation produced no log lines"
    text = "\n".join(entry["line"] for entry in logs["lines"])
    assert (f"file read path=/srv/data/notes.txt "
            f"bytes={len(noted['content'])} from=") in text
    assert "signin fail user=admin from=" in text
    assert "note=no-gate" in text


def test_patch_closes_admin_and_rotates_signin(live_range):
    app_default = _shipped_default("app")
    db_default = _shipped_default("db")
    try:
        before = tools.http_request("web", "/admin")
        assert before["status"] == 200, before
        web_patch = tools.patch_service("web", "webapp")
        assert web_patch["ok"] is True, web_patch
        closed = tools.http_request("web", "/admin")
        assert closed["ok"] is True, closed
        assert closed["status"] == 404
        app_patch = tools.patch_service("app", "reportapi")
        assert app_patch["ok"] is True, app_patch
        assert "rotated" in app_patch["detail"]
        app_stale = tools.try_credential("app", app_default["user"], app_default["pass"])
        assert app_stale["authenticated"] is False, app_stale
        app_fresh = tools.try_credential("app", app_default["user"], ROTATED_PASS)
        assert app_fresh["authenticated"] is True, app_fresh
        db_patch = tools.patch_service("db", "fakestore")
        assert db_patch["ok"] is True, db_patch
        assert "rotated" in db_patch["detail"]
        stale = tools.try_credential("db", db_default["user"], db_default["pass"])
        assert stale["authenticated"] is False, stale
        fresh = tools.try_credential("db", db_default["user"], ROTATED_PASS)
        assert fresh["authenticated"] is True, fresh
    finally:
        tools.restore_host("web")
        tools.restore_host("app")
        tools.restore_host("db")
    reopened = tools.http_request("web", "/admin")
    assert reopened["status"] == 200, reopened
    app_usable = tools.try_credential("app", app_default["user"], app_default["pass"])
    assert app_usable["authenticated"] is True, app_usable
    usable = tools.try_credential("db", db_default["user"], db_default["pass"])
    assert usable["authenticated"] is True, usable
