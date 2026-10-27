"""Hermetic tests for the config-driven toyhost image. No Docker.

Drives toyhost.py routing and config in-process and the shell
handlers through sh with TOYHOST_STATE overrides, pinning log lines
against the simulator's real-host regexes.
"""

import io
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from crucible.scenarios import generate, simulate, words

ROOT = Path(__file__).resolve().parent.parent
TOYHOST = ROOT / "sandbox" / "toyhost"
SH = shutil.which("sh") or "/bin/sh"

sys.path.insert(0, str(TOYHOST))
import toyhost  # noqa: E402  (config-driven host entrypoint)


@pytest.fixture()
def spec():
    return generate.generate(7, "hard")


def run_script(name, args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        [SH, str(TOYHOST / name), *args],
        capture_output=True, text=True, timeout=30, env=env,
    )


def _make_state(tmp_path, role="app", accounts=(), gated=()):
    state = tmp_path / "state"
    state.mkdir()
    (state / "role").write_text(role)
    passwd = "".join(f"{user}:{password}\n" for user, password in accounts)
    (state / "passwd").write_text(passwd)
    (state / "passwd.shipped").write_text(passwd)
    (state / "gated.txt").write_text("".join(f"{path}\n" for path in gated))
    log = tmp_path / "toy.log"
    log.write_text("")
    return state, {"TOYHOST_STATE": str(state), "TOY_LOG_FILE": str(log)}


def _log_lines(tmp_path):
    text = (tmp_path / "toy.log").read_text()
    return [line for line in text.splitlines() if line.strip()]


# -- routing -----------------------------------------------------------


def test_leaky_page_returns_hint(spec):
    entry = spec["entry_host"]
    config = toyhost.host_config(spec, entry)
    page = next(step["page_path"] for step in spec["path"]
                if step["kind"] == "exposed_page")
    code, body = toyhost.route_request("GET", page, config, patched=False)
    assert code == 200
    assert body["admin"] is True
    assert "misconfiguration" in body["note"]
    assert spec["path"][1]["account"] in body["hint"]


def test_patch_closes_leaky_and_decoy_pages(spec):
    entry = spec["entry_host"]
    config = toyhost.host_config(spec, entry)
    page = next(step["page_path"] for step in spec["path"]
                if step["kind"] == "exposed_page")
    assert toyhost.route_request("GET", page, config,
                                 patched=True)[0] == 404
    decoy = next(path for path, info in config["pages"].items()
                 if "decoy" in info)
    assert toyhost.route_request("GET", decoy, config,
                                 patched=False)[0] == 200
    assert toyhost.route_request("GET", decoy, config,
                                 patched=True)[0] == 404


def test_decoy_leaks_nothing(spec):
    entry = spec["entry_host"]
    config = toyhost.host_config(spec, entry)
    decoy = next(path for path, info in config["pages"].items()
                 if "decoy" in info)
    code, body = toyhost.route_request("GET", decoy, config, patched=False)
    assert code == 200
    assert "hint" not in body


def test_root_status_unknown_and_post(spec):
    entry = spec["entry_host"]
    config = toyhost.host_config(spec, entry)
    code, body = toyhost.route_request("GET", "/", config, patched=False)
    assert code == 200 and body["state"] == "up"
    status = next(path for path, info in config["pages"].items()
                  if "status" in info)
    assert toyhost.route_request("GET", status, config,
                                 patched=False)[0] == 200
    assert toyhost.route_request("GET", "/no-such-page", config,
                                 patched=False)[0] == 404
    assert toyhost.route_request("POST", "/", config,
                                 patched=False)[0] == 404


def test_query_string_does_not_change_routing(spec):
    entry = spec["entry_host"]
    config = toyhost.host_config(spec, entry)
    page = next(step["page_path"] for step in spec["path"]
                if step["kind"] == "exposed_page")
    plain = toyhost.route_request("GET", page, config, patched=False)
    assert toyhost.route_request("GET", f"{page}?x=1", config,
                                 patched=False) == plain


def test_request_log_line_uses_real_format():
    class Stub:
        client_address = ("172.18.0.9", 1234)
        command = "GET"
        path = "/admin"

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        toyhost.Handler._log_request(Stub(), 200, "/admin")
    line = buffer.getvalue().strip()
    assert simulate.LINE_FORMATS["request"].match(line), line


# -- config ------------------------------------------------------------


def test_host_config_collects_accounts_files_gates(spec):
    weak = next(step for step in spec["path"]
                if step["kind"] == "weak_creds")
    config = toyhost.host_config(spec, weak["host"])
    assert config["accounts"][weak["account"]] == weak["password"]
    filed = next(step for step in spec["path"]
                 if step["kind"] in ("config_creds", "backup_creds"))
    fconfig = toyhost.host_config(spec, filed["host"])
    assert filed["path"] in fconfig["files"]
    assert filed["reveals_password"] in fconfig["files"][filed["path"]]
    assert filed["path"] in fconfig["gated"]
    assert filed["dir"] in fconfig["gated"]
    jewel = toyhost.host_config(spec, spec["jewel"]["host"])
    assert jewel["files"][spec["jewel"]["path"]].startswith("TOY-SECRET-")


def test_host_config_marks_http_roles(spec):
    entry = toyhost.host_config(spec, spec["entry_host"])
    assert entry["http"] is True
    filler = next(host["name"] for host in spec["hosts"]
                  if host["role"] not in words.HTTP_ROLES)
    assert toyhost.host_config(spec, filler)["http"] is False


# -- noise -------------------------------------------------------------


def test_noise_lines_are_deterministic_and_well_formed(spec):
    entry = spec["entry_host"]
    first = toyhost.noise_lines_for_host(spec, entry, "seed-1")
    second = toyhost.noise_lines_for_host(spec, entry, "seed-1")
    assert first == second and first
    for line in first:
        assert any(regex.match(line)
                   for regex in simulate.LINE_FORMATS.values()), line


def test_noise_never_signs_in_as_real_accounts(spec):
    from crucible import tools as tools_mod

    real = set()
    for step in spec["path"]:
        for key in ("account", "reveals_account"):
            if key in step:
                real.add(step[key])
    for end in spec.get("dead_ends", []):
        real.add(end["account"])
        real.add(end["terminal_account"])
    for host in [item["name"] for item in spec["hosts"]]:
        for line in toyhost.noise_lines_for_host(spec, host, "seed-1"):
            assert not tools_mod._line_names_account(line, real), line


# -- signin handler ----------------------------------------------------


def test_signin_ok_and_fail_with_session(tmp_path):
    _state, env = _make_state(tmp_path, accounts=(("usher", "letmein"),))
    assert run_script("signin.sh", ["usher", "wrong", "controller"],
                      env).stdout.strip() == "AUTH_FAIL"
    assert not (tmp_path / "state" / "session").exists()
    assert run_script("signin.sh", ["usher", "letmein", "controller"],
                      env).stdout.strip() == "AUTH_OK"
    assert (tmp_path / "state" / "session").exists()
    lines = _log_lines(tmp_path)
    assert simulate.LINE_FORMATS["signin_fail"].match(lines[0])
    assert simulate.LINE_FORMATS["signin_ok"].match(lines[1])
    assert "letmein" not in "\n".join(lines)


def test_signin_unknown_user_fails_without_note(tmp_path):
    _state, env = _make_state(tmp_path, accounts=(("usher", "letmein"),))
    assert run_script("signin.sh", ["nobody", "letmein", "controller"],
                      env).stdout.strip() == "AUTH_FAIL"
    line = _log_lines(tmp_path)[0]
    assert simulate.LINE_FORMATS["signin_fail"].match(line)
    assert "no-gate" not in line


def test_workstation_signin_always_fails_with_note(tmp_path):
    _state, env = _make_state(tmp_path, role="workstation",
                         accounts=(("usher", "letmein"),))
    assert run_script("signin.sh", ["usher", "letmein", "controller"],
                      env).stdout.strip() == "NO_GATE"
    line = _log_lines(tmp_path)[0]
    assert simulate.LINE_FORMATS["signin_fail_nogate"].match(line)


# -- gated file handlers -----------------------------------------------


def test_read_file_gates_without_session(tmp_path):
    target = tmp_path / "export.ini"
    target.write_text("host=x user=y password=z")
    _state, env = _make_state(tmp_path, gated=(str(target), str(tmp_path)))
    done = run_script("read-file.sh", [str(target), "controller"], env)
    assert done.returncode == 1
    assert done.stdout.strip() == "GATE_REQUIRED"
    assert _log_lines(tmp_path) == []
    (tmp_path / "state" / "session").write_text("")
    done = run_script("read-file.sh", [str(target), "controller"], env)
    assert done.returncode == 0
    assert "password=z" in done.stdout
    line = _log_lines(tmp_path)[0]
    assert simulate.LINE_FORMATS["file_read"].match(line), line


def test_secret_tree_always_gates(tmp_path):
    target = tmp_path / "vault.txt"
    target.write_text("shh")
    _state, env = _make_state(tmp_path)
    (tmp_path / "state" / "session").write_text("")
    (tmp_path / "state" / "session").unlink()
    done = run_script("read-file.sh", ["/secret/x", "controller"], env)
    assert done.returncode == 1
    assert done.stdout.strip() == "GATE_REQUIRED"


def test_list_files_gates_without_session(tmp_path):
    (tmp_path / "srv").mkdir()
    (tmp_path / "srv" / "a.ini").write_text("x")
    _state, env = _make_state(tmp_path, gated=(str(tmp_path / "srv"),))
    done = run_script("list-files.sh", [str(tmp_path / "srv"),
                                        "controller"], env)
    assert done.returncode == 1
    assert _log_lines(tmp_path) == []
    (tmp_path / "state" / "session").write_text("")
    done = run_script("list-files.sh", [str(tmp_path / "srv"),
                                        "controller"], env)
    assert done.returncode == 0
    assert done.stdout.strip() == "a.ini"
    line = _log_lines(tmp_path)[0]
    assert simulate.LINE_FORMATS["dir_list"].match(line), line


def test_missing_paths_fail_without_logging(tmp_path):
    _state, env = _make_state(tmp_path)
    (tmp_path / "state" / "session").write_text("")
    assert run_script("read-file.sh", [str(tmp_path / "missing"),
                                       "controller"], env).returncode == 1
    assert _log_lines(tmp_path) == []


# -- credential state (clean-start contract) ---------------------------
#
# The clean-start check treats a passwd file on an account-less host
# as an unexpected gate, and compares live vs shipped files on hosts
# with accounts. write_state must agree: passwd files exist if and
# only if the host has accounts.


def _write_state_for(spec, host, tmp_path):
    state = tmp_path / "state"
    toyhost.write_state(toyhost.host_config(spec, host), str(state))
    return state


def test_write_state_writes_passwd_with_accounts(tmp_path, spec):
    weak = next(step for step in spec["path"]
                if step["kind"] == "weak_creds")
    state = _write_state_for(spec, weak["host"], tmp_path)
    line = f"{weak['account']}:{weak['password']}"
    live = (state / "passwd").read_text().splitlines()
    shipped = (state / "passwd.shipped").read_text().splitlines()
    assert line in live
    assert live == shipped
    assert (state / "role").read_text()


def test_write_state_omits_passwd_without_accounts(tmp_path):
    spec = generate.generate(1, "easy")
    # harborlight is a gated-role host with no accounts; lantern is a
    # no-gate workstation. Neither may carry passwd files.
    for host in ("harborlight", "lantern"):
        assert toyhost.host_config(spec, host)["accounts"] == {}
        state = _write_state_for(spec, host, tmp_path / host)
        assert not (state / "passwd").exists()
        assert not (state / "passwd.shipped").exists()
        assert (state / "role").read_text()
        assert (state / "gated.txt").exists()


def test_signin_without_passwd_files(tmp_path):
    for role, want, key in (("workstation", "NO_GATE",
                             "signin_fail_nogate"),
                            ("web portal", "AUTH_FAIL", "signin_fail")):
        target = tmp_path / role.replace(" ", "_")
        target.mkdir()
        state, env = _make_state(target, role=role)
        (state / "passwd").unlink()
        (state / "passwd.shipped").unlink()
        done = run_script("signin.sh", ["probeuser", "probepass",
                                        "controller"], env)
        assert done.stdout.strip() == want
        line = _log_lines(target)[0]
        assert simulate.LINE_FORMATS[key].match(line), line
        assert "no-gate" in line if want == "NO_GATE" else "no-gate" not in line


def test_account_sets_match_shipped_defaults(spec):
    from crucible.scenarios import compat

    for candidate in (spec, generate.generate(1, "easy")):
        shipped = compat.as_classic_dict(candidate)["shipped_defaults"]
        for host in [item["name"] for item in candidate["hosts"]]:
            accounts = toyhost.host_config(candidate, host)["accounts"]
            assert bool(accounts) == (host in shipped), host


# -- rotate / reset ----------------------------------------------------


def test_rotate_and_reset_cycle(tmp_path):
    _state, env = _make_state(tmp_path, accounts=(("usher", "letmein"),))
    run_script("signin.sh", ["usher", "letmein", "controller"], env)
    done = run_script("rotate-signin.sh", ["controller"], env)
    assert done.stdout.strip() == "ROTATED_OK"
    assert not (tmp_path / "state" / "session").exists()
    assert run_script("signin.sh", ["usher", "letmein", "controller"],
                      env).stdout.strip() == "AUTH_FAIL"
    done = run_script("reset-signin.sh", ["controller"], env)
    assert done.stdout.strip() == "RESET_OK"
    assert run_script("signin.sh", ["usher", "letmein", "controller"],
                      env).stdout.strip() == "AUTH_OK"
    blob = "\n".join(_log_lines(tmp_path))
    assert "letmein" not in blob and "r0tated-toy-9" not in blob


def test_rotate_and_reset_none_without_gate(tmp_path):
    state, env = _make_state(tmp_path, role="workstation")
    (state / "passwd").unlink()
    (state / "passwd.shipped").unlink()
    assert run_script("rotate-signin.sh", ["controller"],
                      env).stdout.strip() == "ROTATE_NONE"
    assert run_script("reset-signin.sh", ["controller"],
                      env).stdout.strip() == "RESET_NONE"


def test_ps_stub_runs():
    done = run_script("ps.sh", [], {})
    assert done.returncode == 0
    assert "toyhost.py" in done.stdout
