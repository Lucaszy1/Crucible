"""Hermetic tests for the scenario generator. No Docker, no model calls.

Covers determinism, solvability across 200 seeds per difficulty,
no-shortcut, secret hygiene, inventory purity, name uniqueness, the
untouched classic scenario, and simulator log-format parity with the
real sandbox host scripts (run directly with TOY_* overrides).
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from crucible.scenarios import (
    catalog, chain, generate, schema, seeds, simulate, words,
)

ROOT = Path(__file__).resolve().parent.parent
SH = shutil.which("sh") or "/bin/sh"
DIFFICULTIES = ("easy", "medium", "hard")

# Real-looking secret shapes that must never appear in a spec.
SECRET_SHAPES = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{8,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}"),
)


def run_script(path, args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        [SH, str(path), *args],
        capture_output=True, text=True, timeout=30, env=env,
    )


# -- determinism -----------------------------------------------------


@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_deterministic_per_seed(difficulty):
    first = generate.generate(42, difficulty)
    second = generate.generate(42, difficulty)
    assert first == second
    assert generate.generate(43, difficulty) != first


def test_deterministic_noisy_plan():
    spec = generate.generate(11, "hard")
    assert chain.noisy_plan(spec, "n1") == chain.noisy_plan(spec, "n1")
    assert chain.noisy_plan(spec, "n1") != chain.noisy_plan(spec, "n2")


# -- solvability and shape across 200 seeds ---------------------------


@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_solvability_across_200_seeds(difficulty):
    uncovered = []
    for seed in range(200):
        spec = generate.generate(seed, difficulty)
        assert spec["seed"] == seed
        assert spec["difficulty"] == difficulty
        assert schema.MIN_HOSTS <= len(spec["hosts"]) <= schema.MAX_HOSTS
        assert len(spec["path"]) == schema.PATH_LENGTHS[difficulty]
        assert schema.validate_shape(spec) == []
        assert simulate.validate_spec(spec) == []
        assert simulate.validate_simulation(spec) == []
        steps = chain.chain_steps(spec)
        ok, runner, step_lines = simulate.run_plan(
            spec, steps, known_only=True)
        assert ok, f"seed={seed}"
        table = simulate.coverage(spec, runner, step_lines)
        for index, rules in table.items():
            if not rules:
                uncovered.append((seed, index))
    assert uncovered == []


# -- no shortcut ------------------------------------------------------


@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_no_shortcut_to_crown_jewel(difficulty):
    for seed in (0, 1, 2, 3, 4):
        spec = generate.generate(seed, difficulty)
        assert simulate.shortcut_errors(spec) == [], f"seed={seed}"


# -- secret hygiene ----------------------------------------------------


def _password_values(spec):
    values = []
    for step in spec.get("path", []):
        if "password" in step:
            values.append(step["password"])
        if "reveals_password" in step:
            values.append(step["reveals_password"])
    for end in spec.get("dead_ends", []):
        values.append(end["password"])
    return values


@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_no_forbidden_strings_or_real_secrets(difficulty):
    for seed in range(50):
        spec = generate.generate(seed, difficulty)
        for value in _password_values(spec):
            assert value in words.WEAK_PASSWORDS or \
                simulate.LEAKED_PASSWORD_RE.match(value), value
        blob = json.dumps(spec)
        for shape in SECRET_SHAPES:
            assert not shape.search(blob), (seed, shape.pattern)


def test_leaked_passwords_never_guessable():
    for seed in range(20):
        spec = generate.generate(seed, "hard")
        for value in _password_values(spec):
            if value not in words.WEAK_PASSWORDS:
                assert value.startswith("toy-"), value


# -- inventory purity --------------------------------------------------


@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_blue_inventory_contains_no_weaknesses(difficulty):
    for seed in range(50):
        spec = generate.generate(seed, difficulty)
        assert simulate.inventory_errors(spec) == [], f"seed={seed}"


def test_inventory_hides_accounts_and_paths():
    spec = generate.generate(5, "hard")
    blob = json.dumps(spec["blue_inventory"])
    for step in spec["path"]:
        for key in ("account", "reveals_account", "path", "page_path"):
            if key in step:
                assert step[key] not in blob, (key, step[key])


# -- name uniqueness ---------------------------------------------------


@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_name_pools_never_collide(difficulty):
    for seed in range(50):
        spec = generate.generate(seed, difficulty)
        names = [host["name"] for host in spec["hosts"]]
        assert len(set(names)) == len(names)
        assert simulate.validate_spec(spec) == []


# -- classic scenario untouched ----------------------------------------


def test_classic_scenario_untouched():
    path = ROOT / "sandbox" / "scenario.json"
    classic = json.loads(path.read_text())
    assert classic["hosts"] == ["web", "app", "db", "workstation"]
    for key in ("ports", "services", "shipped_defaults", "secret",
                "app_config", "readable_roots", "admin_endpoint",
                "business_weights", "blue_inventory"):
        assert key in classic, key


# -- log-format parity -------------------------------------------------


def _matches_any_format(line):
    return any(regex.match(line)
               for regex in simulate.LINE_FORMATS.values())


def test_simulator_lines_match_known_formats():
    for difficulty in DIFFICULTIES:
        for seed in seeds.SMOKE_SEEDS:
            spec = generate.generate(seed, difficulty)
            steps = chain.chain_steps(spec)
            ok, runner, _ = simulate.run_plan(spec, steps,
                                              known_only=True)
            assert ok
            for host, lines in runner.logs.items():
                for _ref, line in lines:
                    assert _matches_any_format(line), (host, line)


def test_real_scripts_match_simulator_formats(tmp_path):
    user_file = tmp_path / "user"
    pass_file = tmp_path / "pass"
    user_file.write_text("svc-report")
    pass_file.write_text("toy-secret-0001")
    env_files = {"TOY_USER_FILE": str(user_file),
                 "TOY_PASS_FILE": str(pass_file)}
    app = ROOT / "sandbox" / "app"

    log = tmp_path / "toy.log"
    base = {"TOY_LOG_FILE": str(log)}
    base.update(env_files)
    done = run_script(app / "signin.sh",
                      ["svc-report", "toy-secret-0001", "controller"], base)
    assert done.returncode == 0
    assert done.stdout.strip() == "AUTH_OK"
    done = run_script(app / "signin.sh", ["nope", "wrong", "controller"],
                      base)
    assert done.stdout.strip() == "AUTH_FAIL"
    lines = log.read_text().splitlines()
    assert simulate.LINE_FORMATS["signin_ok"].match(lines[0])
    assert simulate.LINE_FORMATS["signin_fail"].match(lines[1])

    target = tmp_path / "note.txt"
    target.write_text("hello")
    log.write_text("")
    done = run_script(app / "read-file.sh", [str(target), "controller"],
                      {"TOY_LOG_FILE": str(log)})
    assert done.returncode == 0
    line = log.read_text().splitlines()[0]
    assert simulate.LINE_FORMATS["file_read"].match(line), line

    log.write_text("")
    done = run_script(app / "list-files.sh", [str(tmp_path), "controller"],
                      {"TOY_LOG_FILE": str(log)})
    assert done.returncode == 0
    line = log.read_text().splitlines()[0]
    assert simulate.LINE_FORMATS["dir_list"].match(line), line

    ws = ROOT / "sandbox" / "workstation"
    log.write_text("")
    done = run_script(ws / "signin.sh", ["anyone", "whatever",
                                         "controller"],
                      {"TOY_LOG_FILE": str(log)})
    assert done.returncode == 0
    line = log.read_text().splitlines()[0]
    assert simulate.LINE_FORMATS["signin_fail_nogate"].match(line), line


def test_real_web_request_line_matches_simulator_format():
    sys.path.insert(0, str(ROOT / "sandbox" / "web"))
    try:
        import app as webapp
    finally:
        sys.path.pop(0)

    class Stub:
        client_address = ("172.18.0.5", 1234)
        command = "GET"
        path = "/admin"

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        webapp.Handler._log_request(Stub(), 200)
    line = buffer.getvalue().strip()
    assert simulate.LINE_FORMATS["request"].match(line), line


# -- chain / noisy red -------------------------------------------------


def test_noisy_decoy_guesses_never_match_live_credentials():
    for difficulty in DIFFICULTIES:
        spec = generate.generate(9, difficulty)
        real = set()
        for step in spec["path"]:
            if step["kind"] in ("weak_creds", "stale_reuse",
                                "signin_known"):
                real.add((step["host"], step["account"],
                          step["password"]))
        for end in spec["dead_ends"]:
            real.add((end["host"], end["account"], end["password"]))
            real.add((end["terminal_host"], end["terminal_account"],
                      end["password"]))
        planned = {(move["tool"], json.dumps(move["args"], sort_keys=True))
                   for move in chain.chain_plan(spec)}
        for move in chain.noisy_plan(spec, "probe"):
            key = (move["tool"], json.dumps(move["args"], sort_keys=True))
            if move["tool"] == "try_credential" and key not in planned:
                triple = (move["args"]["host"], move["args"]["user"],
                          move["args"]["password"])
                assert triple not in real, triple


def test_noisy_run_still_reaches_jewel():
    for difficulty in DIFFICULTIES:
        spec = generate.generate(9, difficulty)
        runner = simulate.Range(spec)
        for move in chain.noisy_plan(spec, "probe"):
            runner.run_action(move)
        reads = [line for _ref, line in
                 runner.logs[spec["jewel"]["host"]]
                 if line.startswith("file read")]
        assert any(spec["jewel"]["path"] in line for line in reads)


def test_chain_red_classes_follow_plan():
    spec = generate.generate(3, "medium")
    red = chain.ChainRed(spec)
    moves = []
    while True:
        action = red.next_action(0, [])
        if action is None:
            break
        moves.append(action)
    assert moves == chain.chain_plan(spec)
    noisy = chain.NoisyRed(spec, "x")
    first = noisy.next_action(0, [])
    assert first is not None


# -- seed sets ----------------------------------------------------------


def test_seed_sets_disjoint_and_usable():
    assert not set(seeds.TRAIN_SEEDS) & set(seeds.HELDOUT_SEEDS)
    assert len(seeds.TRAIN_SEEDS) == 40
    assert len(seeds.HELDOUT_SEEDS) == 20
    for seed in (seeds.TRAIN_SEEDS[0], seeds.HELDOUT_SEEDS[0]):
        spec = generate.generate(seed, "easy")
        assert simulate.validate_simulation(spec) == []


# -- catalog sanity -----------------------------------------------------


def test_catalog_templates_use_known_kinds():
    for difficulty, templates in catalog.TEMPLATES.items():
        assert difficulty in schema.DIFFICULTIES
        for template in templates:
            assert len(template) == schema.PATH_LENGTHS[difficulty]
            assert template[-1] == "jewel_read"
            for kind in template[:-1]:
                assert kind in schema.WEAKNESS_KINDS or \
                    kind == "signin_known", kind
