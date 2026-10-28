"""Hermetic tests for Stage 2: generated stacks without Docker.

Covers the compose renderer, the classic-shaped adapter, scenario
selection, the active-spec sandbox, spec-driven gates and agents,
CLI flag plumbing, episode grouping, frozen playbooks, classic
regression, and the parity harness against fakes.
"""

import csv
import json

import pytest

from crucible import batch, bench, campaign, orchestrator, parity, run_exercise, sandbox, tools
from crucible.agents import _common, auditor
from crucible.agents import blue as blue_agent
from crucible.agents import red as red_agent
from crucible.agents.scripted_blue import ScriptedBlue
from crucible.agents.scripted_red import ScriptedRed
from crucible.scenarios import chain as chain_mod
from crucible.scenarios import compat, generate, render, seeds, select, simulate, stack, words


@pytest.fixture()
def spec():
    return generate.generate(7, "hard")


@pytest.fixture()
def active(spec):
    sandbox.set_active_spec(spec)
    try:
        yield spec
    finally:
        sandbox.set_active_spec(None)


# -- renderer ----------------------------------------------------------


def test_render_services_network_limits_names(spec):
    text = render.render(spec)
    assert "internal: true" in text
    assert "ports:" not in text
    assert "EXPOSE" not in text
    assert "cpus: '0.50'" in text
    assert "memory: 256M" in text
    for host in [item["name"] for item in spec["hosts"]]:
        assert f"  {host}:" in text
        assert f"container_name: crucible-{host}" in text
        assert f"hostname: {host}" in text
        assert f"TOYHOST_ID: {host}" in text
    assert "./spec.gen.json:/run/toyhost/spec.json:ro" in text
    assert "networks:\n  range:" in text


def test_render_builds_shared_image_once(spec):
    text = render.render(spec)
    assert text.count("build: ./toyhost") == 1
    names = [item["name"] for item in spec["hosts"]]
    assert text.count(f"image: {render.IMAGE}") == len(names)


def test_render_caps_containers_and_validates_names(spec):
    big = dict(spec)
    big["hosts"] = [{"name": f"h{n}"} for n in range(11)]
    with pytest.raises(ValueError):
        render.render(big)
    bad = dict(spec)
    bad["hosts"] = [{"name": "not a host!"}]
    with pytest.raises(ValueError):
        render.render(bad)


def test_write_stack_writes_files_and_noise_flag(spec, tmp_path):
    out = render.write_stack(spec, tmp_path)
    assert json.loads(out["spec"].read_text())["scenario_id"] == spec["scenario_id"]
    assert "TOYHOST_NOISE" not in out["compose"].read_text()
    out = render.write_stack(spec, tmp_path, noise=False)
    text = out["compose"].read_text()
    assert text.count("TOYHOST_NOISE: '0'") == len(spec["hosts"])


# -- adapter -----------------------------------------------------------


def test_adapter_classic_shape(spec):
    view = compat.as_classic_dict(spec)
    names = [item["name"] for item in spec["hosts"]]
    assert view["hosts"] == names
    assert view["secret"] == {"host": spec["jewel"]["host"],
                              "path": spec["jewel"]["path"]}
    assert view["business_weights"] == {item["name"]: item["weight"]
                                        for item in spec["hosts"]}
    assert view["blue_inventory"] == spec["blue_inventory"]
    assert view["entry_host"] == spec["entry_host"]
    assert view["readable_roots"] == ["/"]
    http_roles = [item["name"] for item in spec["hosts"]
                  if item["role"] in words.HTTP_ROLES]
    assert view["ports"] == {name: 8000 for name in http_roles}
    for name in names:
        assert view["services"][name]["service"]


def test_adapter_shipped_and_gated(spec):
    view = compat.as_classic_dict(spec)
    weak = next(step for step in spec["path"]
                if step["kind"] == "weak_creds")
    entry = view["shipped_defaults"][weak["host"]]
    assert weak["account"] in entry["users"]
    filed = next(step for step in spec["path"]
                 if step["kind"] in ("config_creds", "backup_creds"))
    gated = view["gated_paths"][filed["host"]]
    assert filed["path"] in gated and filed["dir"] in gated
    jewel_gated = view["gated_paths"][spec["jewel"]["host"]]
    assert spec["jewel"]["path"] in jewel_gated


# -- selection ---------------------------------------------------------


def test_parse_token_classic_and_gen():
    assert select.parse_token("classic") == "classic"
    assert select.parse_token("gen:7:hard") == ("gen", 7, "hard")
    for bad in ("", "gen:7", "gen:x:hard", "gen:7:evil", "other"):
        with pytest.raises(ValueError):
            select.parse_token(bad)


def test_selection_defaults_dedups_and_appends_sets():
    assert select.selection(None, None, "medium") == ["classic"]
    keys = select.selection("classic,gen:1:easy,gen:1:easy", None, "easy")
    assert keys == ["classic", ("gen", 1, "easy")]
    keys = select.selection("classic", "heldout", "hard")
    assert keys[0] == "classic"
    assert len(keys) == 1 + len(seeds.HELDOUT_SEEDS)
    assert keys[1] == ("gen", seeds.HELDOUT_SEEDS[0], "hard")
    assert select.label(("gen", 7, "hard")) == "gen-7-hard"
    assert select.label("classic") == "classic"


# -- active sandbox ----------------------------------------------------


def test_classic_allowlist_by_default():
    assert sandbox.active_spec() is None
    assert sandbox.active_hosts() == sandbox.ALLOWED_HOSTS
    assert sandbox.active_compose_file() == sandbox.COMPOSE_FILE
    assert sandbox.check_host("web") == "web"
    with pytest.raises(sandbox.AllowlistError):
        sandbox.check_host("upland")


def test_active_spec_drives_allowlist_compose_and_inventory(active):
    names = [item["name"] for item in active["hosts"]]
    assert sandbox.active_hosts() == tuple(names)
    assert sandbox.active_compose_file() == sandbox.GEN_COMPOSE_FILE
    assert sandbox.check_host(names[0]) == names[0]
    with pytest.raises(sandbox.AllowlistError):
        sandbox.check_host("web")
    inventory = sandbox.blue_inventory()
    assert inventory["crown_jewel_host"] == active["jewel"]["host"]
    assert inventory["hosts"][names[0]]["weight"] == \
        active["hosts"][0]["weight"]
    sandbox.set_active_spec(None)
    assert sandbox.active_hosts() == sandbox.ALLOWED_HOSTS


def test_load_scenario_adapter_shape(active):
    view = sandbox.load_scenario()
    assert view["entry_host"] == active["entry_host"]
    assert view["scenario_id"] == active["scenario_id"]


def test_stop_reports_active_hosts(monkeypatch, active):
    monkeypatch.setattr(sandbox, "_compose", lambda *a, **k: "")
    out = sandbox.stop()
    assert set(out) == set(sandbox.active_hosts())
    assert not any(out.values())


# -- spec-driven gates -------------------------------------------------


def test_gen_config_read_needs_session(monkeypatch, active):
    monkeypatch.setattr(sandbox, "is_isolated", lambda host: False)
    monkeypatch.setattr(tools, "reset_state", lambda: None)
    tools._state["authed"].clear()
    filed = next(step for step in active["path"]
                 if step["kind"] in ("config_creds", "backup_creds"))
    try:
        out = tools.read_file(filed["host"], filed["path"])
        assert out == {"ok": False,
                       "error": f"sign-in on {filed['host']!r} "
                                f"is required first"}
        out = tools.list_files(filed["host"], filed["dir"])
        assert out["error"] == f"sign-in on {filed['host']!r} " \
                               f"is required first"
        tools._state["authed"].add(filed["host"])
        monkeypatch.setattr(sandbox, "exec",
                            lambda host, cmd, timeout=30: {
                                "exit_code": 0, "output": "k=v",
                                "stdout": "k=v", "stderr": ""})
        assert tools.read_file(filed["host"], filed["path"])["ok"] is True
    finally:
        tools._state["authed"].clear()


def test_classic_config_read_has_no_new_gate(monkeypatch):
    monkeypatch.setattr(sandbox, "is_isolated", lambda host: False)
    seen = []
    monkeypatch.setattr(sandbox, "exec",
                        lambda host, cmd, timeout=30: seen.append(cmd) or {
                            "exit_code": 0, "output": "x", "stdout": "x",
                            "stderr": ""})
    out = tools.read_file("app", "/srv/app/config.ini")
    assert out["ok"] is True
    assert seen[0][:2] == ["read-file", "/srv/app/config.ini"]


def test_relay_host_classic_and_gen(active):
    assert tools._relay_host() == active["entry_host"]
    sandbox.set_active_spec(None)
    assert tools._relay_host() == "workstation"


def test_line_names_account_matches_tokens_only():
    names = {"metrics", "svc-report"}
    assert tools._line_names_account(
        "request from=1.2.3.4 method=GET path=/metrics status=200", names) is False
    assert tools._line_names_account(
        "signin ok user=metrics from=controller", names) is True
    assert tools._line_names_account(
        "signin fail user=guest from=controller", names) is False


# -- spec-driven agents -------------------------------------------------


def test_scripted_red_uses_chain_under_spec(active):
    red = ScriptedRed()
    moves = []
    while True:
        action = red.next_action(0, [])
        if action is None:
            break
        moves.append(action)
    assert moves[0] == {"tool": "scan_host",
                        "args": {"host": active["entry_host"]},
                        "reasoning": "Check whether the entry host "
                                     "is reachable.",
                        "evidence_refs": []}
    assert moves[-1]["tool"] == "report_finding"
    assert len(moves) == len(chain_mod.chain_plan(active)) + 2


def test_scripted_red_classic_unchanged():
    red = ScriptedRed()
    first = red.next_action(0, [])
    assert first["args"] == {"host": "web"}


def test_scripted_blue_reads_entry_host(active):
    blue = ScriptedBlue()
    first = blue.next_action(0, [])
    assert first["args"] == {"host": active["entry_host"], "n": 50}
    sandbox.set_active_spec(None)
    classic = ScriptedBlue()
    assert classic.next_action(0, [])["args"] == {"host": "web", "n": 50}


def test_bench_reds_follow_spec(active):
    assert bench.ChainRed()._plan == chain_mod.chain_plan(active)
    assert bench.NoisyRed("seed-9")._plan == chain_mod.noisy_plan(
        active, "seed-9")
    assert chain_mod.noisy_plan(active, "seed-9") == \
        chain_mod.noisy_plan(active, "seed-9")
    sandbox.set_active_spec(None)
    assert bench.ChainRed()._plan == bench.chain_plan()
    assert bench.NoisyRed("seed-9")._plan == bench.noisy_plan("seed-9")


def test_auditor_and_evidence_follow_spec(active):
    host = active["hosts"][0]["name"]
    event = {"args": {"host": host}, "result": {"ok": True},
             "tool": "read_logs"}
    assert auditor.action_host(event) == host
    assert auditor.hosts_named_in(f"saw probing on {host} today") == {host}
    assert _common.extract_evidence_ids(f"see {host}:3") == [f"{host}:3"]
    sandbox.set_active_spec(None)
    assert auditor.action_host(event) is None
    assert _common.extract_evidence_ids("see web:3") == ["web:3"]


def test_prompts_classic_identical_gen_substituted(active):
    sandbox.set_active_spec(None)
    assert blue_agent.system_prompt() == blue_agent.SYSTEM_PROMPT
    assert red_agent.system_prompt() == red_agent.SYSTEM_PROMPT
    sandbox.set_active_spec(active)
    blue_text = blue_agent.system_prompt()
    red_text = red_agent.system_prompt()
    first = active["hosts"][0]["name"]
    assert first in blue_text and "web, app, db" not in blue_text
    assert f"such as {first}:3" in blue_text
    assert active["jewel"]["host"] in red_text
    assert "db host" not in red_text
    schemas = red_agent.tool_schemas()
    scan = next(entry for entry in schemas
                if entry["function"]["name"] == "scan_host")
    assert first in scan["function"]["parameters"]["properties"][
        "host"]["description"]
    sandbox.set_active_spec(None)
    assert red_agent.tool_schemas() is red_agent.TOOL_SCHEMAS


# -- CLI flags ----------------------------------------------------------


def test_scenario_flags_parse_on_all_clis():
    assert run_exercise.parse_args([]).scenario == "classic"
    assert run_exercise.parse_args(
        ["--scenario", "gen:7:hard"]).scenario == "gen:7:hard"
    for module in (batch, campaign, bench):
        args = module.parse_args(
            (["--count", "1", "--prefix", "p"] if module is batch else []) +
            (["--name", "n"] if module is bench else []) +
            (["--episodes", "1", "--name", "n", "--memory", "off"]
             if module is campaign else []) +
            ["--scenarios", "classic,gen:7:hard", "--scenario-set", "train",
             "--difficulty", "easy"])
        assert args.scenarios == "classic,gen:7:hard"
        assert args.scenario_set == "train"
        assert args.difficulty == "easy"
    assert campaign.parse_args(
        ["--episodes", "1", "--name", "n", "--memory", "off",
         ]).freeze_playbook is False
    assert campaign.parse_args(
        ["--episodes", "1", "--name", "n", "--memory", "off",
         "--freeze-playbook"]).freeze_playbook is True
    assert bench.parse_args(
        ["--name", "n", "--freeze-playbook"]).freeze_playbook is True


# -- first event scenario keys ------------------------------------------


def test_first_event_records_scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "begin_episode",
                        lambda: {"ok": True, "attempts": 1, "checks": {}})
    monkeypatch.setattr(orchestrator, "run_tool",
                        lambda name, args: {"ok": True})
    plain = orchestrator.run_exercise(ScriptedRed(), ScriptedBlue(),
                                      max_turns=2,
                                      run_path=tmp_path / "a.jsonl")
    args = plain["events"][0]["args"]
    assert (args["scenario_id"], args["scenario_seed"],
            args["scenario_difficulty"]) == ("classic", None, None)
    info = {"scenario_id": "scn-0007-hard-a0", "seed": 7,
            "difficulty": "hard"}
    gen = orchestrator.run_exercise(ScriptedRed(), ScriptedBlue(),
                                    max_turns=2,
                                    run_path=tmp_path / "b.jsonl",
                                    scenario=info)
    args = gen["events"][0]["args"]
    assert (args["scenario_id"], args["scenario_seed"],
            args["scenario_difficulty"]) == ("scn-0007-hard-a0", 7, "hard")


# -- grouping and boot order --------------------------------------------


def _stub_docker(monkeypatch, seen, runs_dir, monkey_spend):
    from crucible import events, llm

    monkeypatch.setattr(sandbox, "start",
                        lambda: seen.setdefault("boots", []).append("build"))
    monkeypatch.setattr(sandbox, "start_warm",
                        lambda: seen.setdefault("boots", []).append("warm"))
    monkeypatch.setattr(sandbox, "stop",
                        lambda: seen.setdefault("stops", []).append(1))
    monkeypatch.setattr(tools, "restore_host",
                        lambda host: {"ok": True, "host": host})
    monkeypatch.setattr(tools, "reset_state", lambda: None)
    monkeypatch.setattr(events, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(llm, "SPEND_FILE", monkey_spend)


def _fake_activate(monkeypatch, seen):
    def fake_activate(key):
        if select.is_classic(key):
            sandbox.set_active_spec(None)
            return None
        _kind, seed, difficulty = key
        spec = generate.generate(seed, difficulty)
        sandbox.set_active_spec(spec)
        return {"scenario_id": spec["scenario_id"], "seed": seed,
                "difficulty": difficulty}
    monkeypatch.setattr(stack, "activate", fake_activate)


def test_batch_groups_episodes_and_boots_once(tmp_path, monkeypatch):
    from crucible import events

    seen = {}
    _stub_docker(monkeypatch, seen, tmp_path / "runs",
                 tmp_path / "spend.jsonl")
    _fake_activate(monkeypatch, seen)

    def fake_exercise(red, blue, max_turns=40, run_path=None,
                      exercise_id=None, audit="off", sensors="on",
                      blue_reasoning_effort=None, scenario=None,
                      auditor_use_cache=True):
        seen.setdefault("ids", []).append(exercise_id)
        seen.setdefault("scenarios", []).append(
            (scenario or {}).get("scenario_id", "classic"))
        return {"events": [], "score": {"outcome": "blue_held",
                                       "blue_score": 90.0,
                                       "downtime_pct": 1.0,
                                       "detection_delay": 1,
                                       "time_to_contain": 2},
                "run_path": str(tmp_path / "x.jsonl"),
                "audit_summary": {"mode": audit}}

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(batch.orchestrator, "run_exercise", fake_exercise)
    assert batch.main(["--count", "2", "--prefix", "g",
                       "--scenarios", "classic,gen:7:easy"]) == 0
    assert seen["ids"] == ["g-classic-1", "g-classic-2",
                           "g-gen-7-easy-1", "g-gen-7-easy-2"]
    assert seen["boots"] == ["build", "warm"]
    assert sandbox.active_spec() is None
    with (tmp_path / "runs" / "batch-g.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["scenario_id"] == "classic"
    assert rows[2]["scenario_seed"] == "7"
    assert rows[2]["scenario_difficulty"] == "easy"


def test_campaign_freeze_skips_referee_but_keeps_playbook(tmp_path,
                                                           monkeypatch):
    from crucible import events, llm
    from crucible.agents import referee

    seen = {}
    _stub_docker(monkeypatch, seen, tmp_path / "runs",
                 tmp_path / "spend.jsonl")
    _fake_activate(monkeypatch, seen)
    monkeypatch.setattr(events, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(referee, "PLAYBOOK_JSON", tmp_path / "blue.json")
    monkeypatch.setattr(referee, "PLAYBOOK_MD", tmp_path / "blue.md")

    def fake_exercise(red, blue, max_turns=40, run_path=None,
                      exercise_id=None, audit="off", sensors="on", seed=None,
                      blue_reasoning_effort=None, scenario=None,
                      auditor_use_cache=True):
        seen.setdefault("ids", []).append(exercise_id)
        seen["blue_playbook"] = getattr(blue, "_playbook", "scripted")
        return {"events": [], "score": {"outcome": "blue_held",
                                       "blue_score": 80.0,
                                       "downtime_pct": 0.0,
                                       "detection_delay": 1,
                                       "time_to_contain": 2},
                "run_path": str(run_path),
                "audit_summary": {"mode": audit}}

    def no_review(*args, **kwargs):
        raise AssertionError("referee must not run when frozen")

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(campaign.orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(referee, "review_episode", no_review)
    assert campaign.main(["--name", "frz", "--episodes", "2", "--memory",
                          "on", "--freeze-playbook", "--scenarios",
                          "gen:7:easy"]) == 0
    assert seen["ids"] == ["frz-1", "frz-2"]
    summary = json.loads((tmp_path / "runs" / "campaign-frz" /
                          "summary.json").read_text())
    assert summary["playbook_frozen"] is True
    assert summary["scenarios"] == ["gen-7-easy"]
    assert all(row["playbook_rule_count"] == 0
               for row in summary["episodes"])
    assert summary["episodes"][0]["scenario_seed"] == 7


def test_bench_groups_freeze_and_records_scenarios(tmp_path, monkeypatch):
    from crucible import events, llm
    from crucible.agents import referee

    seen = {}
    _stub_docker(monkeypatch, seen, tmp_path / "runs",
                 tmp_path / "spend.jsonl")
    _fake_activate(monkeypatch, seen)
    monkeypatch.setattr(llm, "spent_today", lambda *a, **k: 0.0)

    def fake_exercise(red, blue, max_turns=40, run_path=None,
                      exercise_id=None, audit="off", sensors="on", seed=None,
                      blue_reasoning_effort=None, scenario=None,
                      auditor_use_cache=True):
        seen.setdefault("ids", []).append(exercise_id)
        return {"events": [], "score": {"outcome": "blue_held",
                                       "blue_score": 70.0,
                                       "downtime_pct": 0.0,
                                       "detection_delay": 1,
                                       "relevant_detection_delay": 1,
                                       "time_to_contain": 2,
                                       "intrusion": False,
                                       "contained_after_intrusion": False,
                                       "secret_read": False},
                "run_path": str(run_path),
                "audit_summary": {"mode": audit}}

    def no_review(*args, **kwargs):
        raise AssertionError("referee must not run when frozen")

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(bench.orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(referee, "review_episode", no_review)
    assert bench.main(["--name", "fz", "--red", "chain", "--sensors", "on",
                       "--memory", "on", "--episodes-per-cell", "1",
                       "--scenarios", "classic,gen:7:easy",
                       "--freeze-playbook"]) == 0
    assert seen["ids"] == ["classic-chain-sensors-on-memory-on-1",
                           "gen-7-easy-chain-sensors-on-memory-on-1"]
    assert seen["boots"] == ["build", "warm"]
    summary = json.loads((tmp_path / "runs" / "bench-fz" /
                          "summary.json").read_text())
    assert summary["playbook_frozen"] is True
    assert summary["scenarios"] == ["classic", "gen-7-easy"]


# -- parity harness with fakes ------------------------------------------


def test_parity_parses_seeds_and_difficulties():
    assert parity.parse_seeds("smoke") == [1, 2, 3]
    assert parity.parse_seeds("4, 5") == [4, 5]
    with pytest.raises(ValueError):
        parity.parse_seeds("nope")
    assert parity.parse_difficulties("easy,hard") == ["easy", "hard"]
    with pytest.raises(ValueError):
        parity.parse_difficulties("evil")


def test_parity_normalize_masks_only_request_from():
    assert parity.normalize_line(
        "request from=172.18.0.9 method=GET path=/x status=200") == \
        "request from=* method=GET path=/x status=200"
    line = "signin ok user=a from=controller"
    assert parity.normalize_line(line) == line


def test_parity_compare_logs_reports_every_gap():
    assert parity.compare_logs({"h": ["a"]}, {"h": ["a"]}) == []
    out = parity.compare_logs({"h": ["a", "b"]}, {"h": ["a", "c", "d"]})
    assert [(item["index"], item["expected"], item["actual"])
            for item in out] == [(1, "b", "c"), (2, None, "d")]
    out = parity.compare_logs({"h": ["a"]}, {"h": [], "x": ["z"]})
    assert {(item["host"], item["index"]) for item in out} == \
        {("h", 0), ("x", 0)}


def test_parity_run_one_clean_and_dirty(spec):
    from crucible.scenarios import chain as chain_mod

    plan = chain_mod.chain_plan(spec)
    expected = parity.expected_logs(spec, plan)
    clean = parity.run_one(spec,
                           lambda tool, args: {"ok": True},
                           lambda host: list(expected[host]))
    assert clean["mismatches"] == []
    assert len(clean["results"]) == len(plan)

    def dirty_reader(host):
        lines = list(expected[host])
        if lines:
            lines[0] = lines[0] + " TAMPERED"
        return lines

    dirty = parity.run_one(spec,
                           lambda tool, args: {"ok": True},
                           dirty_reader)
    assert dirty["mismatches"]
    assert all(item["index"] == 0 for item in dirty["mismatches"])

    failed = parity.run_one(
        spec,
        lambda tool, args: {"ok": False, "error": "boom"},
        lambda host: [])
    assert any(item["host"] == "" for item in failed["mismatches"])


def test_parity_run_suite_reports_per_scenario(spec):
    expected = parity.expected_logs(spec, chain_mod.chain_plan(spec))
    calls = []
    hooks = {
        "activate": lambda item: calls.append(("activate", item["seed"])),
        "boot": lambda: calls.append(("boot",)),
        "begin": lambda: calls.append(("begin",)),
        "run_tool": lambda tool, args: {"ok": True},
        "read_logs": lambda host: list(expected[host]),
        "stop": lambda: calls.append(("stop",)),
    }
    outcome = parity.run_suite([spec], hooks)
    assert outcome["scenarios"] == 1
    assert outcome["mismatches"] == 0
    assert [call[0] for call in calls] == ["activate", "boot", "begin",
                                           "stop"]
    lines = parity.format_report(outcome["reports"][0])
    assert "0 mismatches" in lines[0]


# -- stack lifecycle -----------------------------------------------------


def test_stack_activate_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "SANDBOX_DIR", tmp_path)
    try:
        assert stack.activate("classic") is None
        assert sandbox.active_spec() is None
        info = stack.activate(("gen", 7, "easy"))
        assert info["seed"] == 7 and info["difficulty"] == "easy"
        assert sandbox.active_spec()["scenario_id"] == info["scenario_id"]
        assert (tmp_path / "compose.gen.yml").exists()
        assert (tmp_path / "spec.gen.json").exists()
        stack.deactivate()
        assert sandbox.active_spec() is None
    finally:
        sandbox.set_active_spec(None)


def test_run_exercise_rejects_bad_scenario(capsys):
    assert run_exercise.main(["--scenario", "gen:7:evil"]) == 1
    assert "difficulty" in capsys.readouterr().err


# -- scenario layout for the dashboard ---------------------------------


def test_scenario_layout_classic_shape():
    sandbox.set_active_spec(None)
    layout = sandbox.scenario_layout()
    assert layout["scenario_id"] == "classic"
    assert layout["jewel_host"] == "db"
    by_id = {host["id"]: host for host in layout["hosts"]}
    assert set(by_id) == {"web", "app", "db", "workstation"}
    for host in layout["hosts"]:
        assert set(host) == {"id", "role", "segment", "links", "weight"}
    assert by_id["web"]["segment"] == "dmz"
    assert by_id["db"]["segment"] == "data"
    assert by_id["db"]["weight"] == 5
    assert by_id["web"]["links"] == ["app"]
    assert by_id["app"]["links"] == ["web", "db", "workstation"]
    assert by_id["db"]["links"] == ["app"]
    assert by_id["workstation"]["links"] == ["app"]
    assert set(layout) == {"scenario_id", "hosts", "jewel_host"}


def test_scenario_layout_generated_matches_spec_no_leaks(spec):
    sandbox.set_active_spec(spec)
    try:
        layout = sandbox.scenario_layout()
    finally:
        sandbox.set_active_spec(None)
    assert layout["scenario_id"] == spec["scenario_id"]
    assert layout["jewel_host"] == spec["jewel"]["host"]
    assert [host["id"] for host in layout["hosts"]] == \
        [host["name"] for host in spec["hosts"]]
    for entry, raw in zip(layout["hosts"], spec["hosts"]):
        assert entry["role"] == raw["role"]
        assert entry["segment"] == raw["segment"]
        assert entry["weight"] == raw["weight"]
        assert sorted(entry["links"]) == sorted(raw["links"])
    blob = json.dumps(layout)
    assert "password" not in blob
    assert "reveals" not in blob
    assert "page_path" not in blob
    assert "account" not in blob
    for step in spec["path"]:
        for key in ("account", "reveals_account", "password",
                    "reveals_password"):
            if key in step:
                assert step[key] not in blob


def test_first_event_carries_scenario_layout(tmp_path, monkeypatch):
    from crucible.agents.scripted_blue import ScriptedBlue
    from crucible.agents.scripted_red import ScriptedRed

    monkeypatch.setattr(tools, "begin_episode",
                        lambda: {"ok": True, "attempts": 1, "checks": {}})
    monkeypatch.setattr(orchestrator, "run_tool",
                        lambda name, args: {"ok": True})
    sandbox.set_active_spec(None)
    result = orchestrator.run_exercise(
        ScriptedRed(), ScriptedBlue(), max_turns=2,
        run_path=tmp_path / "layout.jsonl")
    layout = result["events"][0]["args"]["scenario_layout"]
    assert layout == sandbox.scenario_layout()
    assert layout["scenario_id"] == "classic"


# -- classic regression --------------------------------------------------


def test_classic_defaults_untouched():
    assert select.selection(None, None, "medium") == ["classic"]
    assert sandbox.active_compose_file() == sandbox.COMPOSE_FILE
    classic = sandbox.load_scenario()
    assert classic["hosts"] == ["web", "app", "db", "workstation"]
    assert "entry_host" not in classic
    inventory = sandbox.blue_inventory()
    assert inventory["hosts"]["db"]["weight"] == 5
    assert inventory["crown_jewel_host"] == "db"


def test_orchestrator_weights_follow_spec(active):
    weights = orchestrator._business_weights()
    assert weights[active["hosts"][0]["name"]] == \
        active["hosts"][0]["weight"]
