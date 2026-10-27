"""Hermetic tests for the benchmark harness.

Sandbox, exercise loop, and referee stay stubbed; bench files land in a
temp dir. No Docker or model calls here.
"""

import csv
import json

from crucible import bench, events, llm, orchestrator, sandbox
from crucible.agents import referee


def _stub_common(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(sandbox, "start", lambda: {"web": True})
    monkeypatch.setattr(sandbox, "stop", lambda: seen.setdefault("stopped", True))
    monkeypatch.setattr(bench.tools, "restore_host",
                        lambda host: {"ok": True, "host": host})
    monkeypatch.setattr(bench.tools, "reset_state", lambda: None)
    monkeypatch.setattr(events, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setattr(llm, "spent_today", lambda *a, **k: 0.0)
    return seen


def _score(**overrides):
    score = {"outcome": "blue_held", "blue_score": 80.0,
             "downtime_pct": 5.0, "detection_delay": 2,
             "relevant_detection_delay": 3, "time_to_contain": 4,
             "intrusion": True, "contained_after_intrusion": True,
             "secret_read": False, "turns_used": 4, "total_events": 4}
    score.update(overrides)
    return score


def _stub_exercise(monkeypatch, seen, score=None, events_list=None):
    def fake_exercise(red, blue, max_turns=40, run_path=None,
                      exercise_id=None, audit="off", sensors="on", seed=None,
                      blue_reasoning_effort=None, scenario=None):
        seen.setdefault("ids", []).append(exercise_id)
        seen.setdefault("reds", []).append(type(red).__name__)
        seen.setdefault("sensors", []).append(sensors)
        seen.setdefault("seeds", []).append(seed)
        seen.setdefault("audit", []).append(audit)
        seen.setdefault("effort", []).append(blue_reasoning_effort)
        return {"events": list(events_list or []),
                "score": dict(score or _score()),
                "run_path": str(run_path),
                "audit_summary": {"mode": audit}}
    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(bench.orchestrator, "run_exercise", fake_exercise)


def _stub_referee(monkeypatch, seen):
    def fake_review(found, score, exercise_id, playbook=None, **kwargs):
        seen.setdefault("reviews", []).append(exercise_id)
        rules = list((playbook or {}).get("rules", []) or [])
        rules.append({"rule": f"Lesson from {exercise_id}.",
                      "episode": exercise_id})
        return {"playbook": {"episode": exercise_id, "rules": rules,
                             "model_used": "muse-wonder-1.0"},
                "model_used": "muse-wonder-1.0", "dropped": 0,
                "estimated_cost_usd": 0.01}
    monkeypatch.setattr(referee, "review_episode", fake_review)


def test_chain_plan_fixed_sequence():
    plan = bench.chain_plan()
    assert [(step["tool"], step["args"].get("host")) for step in plan] == [
        ("http_request", "web"),
        ("try_credential", "app"),
        ("list_files", "app"),
        ("read_file", "app"),
        ("try_credential", "db"),
        ("list_files", "db"),
        ("read_file", "db"),
    ]
    assert plan[0]["args"]["path"] == "/admin"
    assert plan[1]["args"] == {"host": "app", "user": "svc-report",
                               "password": "password"}
    assert plan[2]["args"]["path"] == "/srv/app/"
    assert plan[3]["args"]["path"] == "/srv/app/config.ini"
    assert plan[4]["args"] == {"host": "db", "user": "dbops",
                               "password": "dbops-fake-19"}
    assert plan[5]["args"]["path"] == "/secret/"
    assert plan[6]["args"]["path"] == "/secret/exercise_secret.txt"
    red = bench.ChainRed()
    moves = [red.next_action(turn, []) for turn in range(1, 8)]
    assert [move["tool"] for move in moves] == [step["tool"] for step in plan]
    assert moves[1]["args"]["user"] == "svc-report"
    assert red.next_action(8, []) is None


def test_scripted_reds_make_zero_model_calls(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("scripted reds must not call the model client")

    monkeypatch.setattr(llm, "chat", _boom)
    for red in (bench.ChainRed(), bench.NoisyRed("seed-1")):
        for turn in range(1, 20):
            action = red.next_action(turn, [])
            if action is None:
                break
            assert "tool" in action


def test_noisy_plan_deterministic_per_seed():
    first = bench.noisy_plan("bench-cell-1")
    second = bench.noisy_plan("bench-cell-1")
    assert first == second
    assert len(first) > len(bench.chain_plan())


def test_noisy_plan_varies_across_seeds():
    plans = {json.dumps(bench.noisy_plan(f"seed-{index}"), sort_keys=True)
             for index in range(10)}
    assert len(plans) > 1


def test_noisy_plan_keeps_chain_order_with_clean_decoys():
    from crucible import sandbox as sandbox_module

    scenario = sandbox_module.load_scenario()
    shipped = scenario.get("shipped_defaults", {}) or {}
    real = set()
    for host, entry in shipped.items():
        if isinstance(entry, dict):
            real.add((host, entry.get("user"), entry.get("pass")))
    for index in range(10):
        plan = bench.noisy_plan(f"seed-{index}")
        chain = bench.chain_plan()
        positions = []
        cursor = 0
        for step in chain:
            found = next(i for i in range(cursor, len(plan))
                         if plan[i]["tool"] == step["tool"]
                         and plan[i]["args"] == step["args"])
            positions.append(found)
            cursor = found + 1
        assert positions == sorted(positions)
        for step in plan:
            if step["tool"] == "try_credential":
                triple = (step["args"].get("host"), step["args"].get("user"),
                          step["args"].get("password"))
                if triple in real:
                    continue
            if step["tool"] == "http_request":
                assert "admin" not in str(step["args"].get("path", "")) or \
                    step in chain


def test_parse_defaults_to_chain_noisy_cross_sensors():
    args = bench.parse_args(["--name", "oct27"])
    assert args.red == "chain,noisy"
    assert args.sensors == "on,off"
    assert args.memory == "off"
    assert args.episodes_per_cell == 10
    assert args.blue_model is None
    assert args.blue_base_url is None
    assert args.blue_api_key_env is None


def test_cells_expand_cross_product_in_flag_order():
    args = bench.parse_args(["--name", "x", "--red", "noisy,chain",
                             "--sensors", "off,on", "--memory", "off,on"])
    keys = [cell["key"] for cell in bench._cells(args)]
    assert keys == [
        "noisy-sensors-off-memory-off",
        "noisy-sensors-off-memory-on",
        "noisy-sensors-on-memory-off",
        "noisy-sensors-on-memory-on",
        "chain-sensors-off-memory-off",
        "chain-sensors-off-memory-on",
        "chain-sensors-on-memory-off",
        "chain-sensors-on-memory-on",
    ]


def test_bench_rejects_bad_values(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    assert bench.main(["--name", "x", "--red", "rogue",
                       "--episodes-per-cell", "1"]) == 1
    assert "bench:" in capsys.readouterr().err
    assert bench.main(["--name", "x", "--sensors", "maybe",
                       "--episodes-per-cell", "1"]) == 1
    assert "bench:" in capsys.readouterr().err
    assert bench.main(["--name", "../evil",
                       "--episodes-per-cell", "1"]) == 1
    assert "bench:" in capsys.readouterr().err
    assert bench.main(["--name", "x", "--episodes-per-cell", "0"]) == 1
    assert "at least 1" in capsys.readouterr().err
    assert bench.main(["--name", "x", "--episodes-per-cell", "1",
                       "--spend-cap-usd", "0"]) == 1
    assert "positive" in capsys.readouterr().err


def test_bench_interleaves_cells_with_fixed_seeds(tmp_path, monkeypatch):
    seen = _stub_common(monkeypatch, tmp_path)
    _stub_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    assert bench.main(["--name", "il", "--red", "chain,noisy",
                       "--sensors", "on,off", "--memory", "off",
                       "--episodes-per-cell", "2"]) == 0
    cell_a = "chain-sensors-on-memory-off"
    cell_b = "chain-sensors-off-memory-off"
    cell_c = "noisy-sensors-on-memory-off"
    cell_d = "noisy-sensors-off-memory-off"
    assert seen["ids"] == [f"{cell}-{rep}" for rep in (1, 2)
                           for cell in (cell_a, cell_b, cell_c, cell_d)]
    assert seen["reds"] == (["ChainRed"] * 2 + ["NoisyRed"] * 2) * 2
    assert seen["sensors"] == (["on", "off"] * 2) * 2
    # Seeds pair arms: same rep, every cell, one shared seed.
    assert seen["seeds"] == ["il:1"] * 4 + ["il:2"] * 4
    assert seen["stopped"] is True


def test_bench_writes_csv_and_plain_table(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    seen = {}
    _stub_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    assert bench.main(["--name", "rep", "--red", "chain", "--sensors", "on",
                       "--memory", "off", "--episodes-per-cell", "2"]) == 0
    out = capsys.readouterr().out
    assert "Benchmark done: 2 episodes (name=rep, blue_reasoning_effort=default)" in out
    assert "cell | n | intr | secret%" in out
    assert "chain-sensors-on-memory-off | 2 | 2 | 0.0% | 100.0%" in out
    assert "--- chain-sensors-on-memory-off ---" in out
    assert "Episodes with intrusion: 2/2" in out
    assert "Total cost: $" in out
    assert "CSV:" in out
    with (tmp_path / "runs" / "bench-rep" / "summary.csv").open(
            encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["episode"] for row in rows] == [
        "chain-sensors-on-memory-off-1", "chain-sensors-on-memory-off-2"]
    assert rows[0]["red_profile"] == "chain"
    assert rows[0]["seed"] == "rep:1"
    assert rows[0]["intrusion"] == "True"
    assert rows[0]["relevant_detection_delay"] == "3"
    assert list(rows[0].keys()) == list(bench.CSV_FIELDS)
    state = json.loads((tmp_path / "runs" / "bench-rep"
                        / "summary.json").read_text(encoding="utf-8"))
    assert state["episodes_per_cell"] == 2
    assert len(state["episodes"]) == 2


def test_bench_resume_continues_and_rejects_mismatch(tmp_path, monkeypatch,
                                                     capsys):
    _stub_common(monkeypatch, tmp_path)
    seen = {}
    _stub_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    assert bench.main(["--name", "long", "--red", "chain", "--sensors", "on",
                       "--memory", "off", "--episodes-per-cell", "1"]) == 0
    assert bench.main(["--name", "long", "--red", "chain", "--sensors", "on",
                       "--memory", "off", "--episodes-per-cell", "2",
                       "--resume"]) == 1
    assert "episodes-per-cell differs" in capsys.readouterr().err
    assert bench.main(["--name", "long", "--red", "noisy", "--sensors", "on",
                       "--memory", "off", "--episodes-per-cell", "1",
                       "--resume"]) == 1
    assert "cell set differs" in capsys.readouterr().err
    assert bench.main(["--name", "ghost", "--red", "chain",
                       "--episodes-per-cell", "1", "--resume"]) == 1
    assert "no resumable benchmark" in capsys.readouterr().err


def test_bench_resume_skips_done_episodes(tmp_path, monkeypatch):
    _stub_common(monkeypatch, tmp_path)
    seen = {}
    _stub_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    assert bench.main(["--name", "skip", "--red", "chain,noisy",
                       "--sensors", "on", "--memory", "off",
                       "--episodes-per-cell", "2"]) == 0
    assert len(seen["ids"]) == 4
    path = tmp_path / "runs" / "bench-skip" / "summary.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["episodes"] = state["episodes"][:3]
    path.write_text(json.dumps(state), encoding="utf-8")
    seen.clear()
    _stub_exercise(monkeypatch, seen)
    assert bench.main(["--name", "skip", "--red", "chain,noisy",
                       "--sensors", "on", "--memory", "off",
                       "--episodes-per-cell", "2", "--resume"]) == 0
    assert seen["ids"] == ["noisy-sensors-on-memory-off-2"]


def test_bench_spend_guard_stops_before_episodes(tmp_path, monkeypatch,
                                                 capsys):
    seen = _stub_common(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "spent_today", lambda *a, **k: 5.0)
    _stub_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    assert bench.main(["--name", "cap", "--red", "chain", "--sensors", "on",
                       "--episodes-per-cell", "2",
                       "--spend-cap-usd", "5"]) == 2
    assert seen.get("ids") is None
    assert "spend guard" in capsys.readouterr().err
    assert seen["stopped"] is True


def test_bench_aborts_when_spend_cap_degrades_episode(tmp_path, monkeypatch,
                                                       capsys):
    seen = _stub_common(monkeypatch, tmp_path)
    degraded = [{"actor": "blue", "tool": "read_logs", "args": {},
                 "result": {"ok": True}, "evidence_refs": [],
                 "reasoning_summary": "Model call failed (daily model spend "
                 "cap reached: $5.00 of $5.00 used), using baseline."}]
    _stub_exercise(monkeypatch, seen, events_list=degraded)
    _stub_referee(monkeypatch, seen)
    assert bench.main(["--name", "deg", "--red", "chain", "--sensors", "on",
                       "--episodes-per-cell", "2"]) == 2
    assert len(seen["ids"]) == 1
    err = capsys.readouterr().err
    assert "spend cap degraded" in err


def test_bench_memory_on_reviews_per_cell(tmp_path, monkeypatch):
    seen = _stub_common(monkeypatch, tmp_path)
    _stub_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    assert bench.main(["--name", "mem", "--red", "chain", "--sensors", "on",
                       "--memory", "on", "--episodes-per-cell", "2"]) == 0
    cell = "chain-sensors-on-memory-on"
    assert seen["reviews"] == [f"{cell}-1", f"{cell}-2"]
    book = tmp_path / "runs" / "bench-mem" / "playbooks" / f"{cell}.json"
    assert len(json.loads(book.read_text(encoding="utf-8"))["rules"]) == 2


def test_bench_blue_flags_flow_to_agents(tmp_path, monkeypatch):
    seen = _stub_common(monkeypatch, tmp_path)
    _stub_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    real_blue = bench.ModelBlue

    class _RecordingBlue(real_blue):
        def __init__(self, *args, **kwargs):
            seen.setdefault("blue_models", []).append(kwargs.get("model"))
            seen.setdefault("blue_clients", []).append(kwargs.get("client"))
            super().__init__(*args, **kwargs)

    def fake_client(base, env):
        seen["client_args"] = (base, env)
        return "fake-client"

    monkeypatch.setattr(bench, "ModelBlue", _RecordingBlue)
    monkeypatch.setattr(llm, "custom_client", fake_client)
    assert bench.main(["--name", "flags", "--red", "chain", "--sensors", "on",
                       "--episodes-per-cell", "1",
                       "--blue-model", "meta-models/muse-spark-open-1026",
                       "--blue-base-url", "https://example.test/v1",
                       "--blue-api-key-env", "BASETEN_API_KEY"]) == 0
    assert seen["blue_models"] == ["meta-models/muse-spark-open-1026"]
    assert seen["blue_clients"] == ["fake-client"]
    assert seen["client_args"] == ("https://example.test/v1",
                                   "BASETEN_API_KEY")


def test_bench_audit_cells_limits_auditing(tmp_path, monkeypatch):
    seen = _stub_common(monkeypatch, tmp_path)
    _stub_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    audited = "chain-sensors-on-memory-off"
    assert bench.main(["--name", "au", "--red", "chain",
                       "--sensors", "on,off", "--memory", "off",
                       "--episodes-per-cell", "1", "--audit", "end",
                       "--audit-cells", audited]) == 0
    assert seen["audit"] == ["end", "off"]
    state = json.loads((tmp_path / "runs" / "bench-au" /
                        "summary.json").read_text(encoding="utf-8"))
    assert state["audit_cells"] == [audited]


def test_bench_audit_cells_rejects_unknown(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    assert bench.main(["--name", "au", "--red", "chain",
                       "--episodes-per-cell", "1", "--audit", "end",
                       "--audit-cells", "no-such-cell"]) == 1
    assert "not a cell" in capsys.readouterr().err


def test_bench_audit_cells_resume_mismatch(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    seen = {}
    _stub_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    audited = "chain-sensors-on-memory-off"
    assert bench.main(["--name", "aur", "--red", "chain", "--sensors", "on",
                       "--memory", "off", "--episodes-per-cell", "1",
                       "--audit", "end", "--audit-cells", audited]) == 0
    assert bench.main(["--name", "aur", "--red", "chain", "--sensors", "on",
                       "--memory", "off", "--episodes-per-cell", "1",
                       "--audit", "end", "--resume"]) == 1
    assert "audit cell set differs" in capsys.readouterr().err
    assert bench.main(["--name", "aur", "--red", "chain", "--sensors", "on",
                       "--memory", "off", "--episodes-per-cell", "1",
                       "--audit", "off", "--audit-cells", audited,
                       "--resume"]) == 1
    assert "audit mode differs" in capsys.readouterr().err


def test_bench_playbook_from_seeds_frozen_cells(tmp_path, monkeypatch):
    seen = _stub_common(monkeypatch, tmp_path)
    _stub_exercise(monkeypatch, seen)
    seed = {"episode": "train-9", "model_used": "muse-wonder-1.0",
            "rules": [{"rule": "Contain the host the evidence points at.",
                       "episode": "train-3"},
                      {"rule": "Report probing before containing.",
                       "episode": "train-9"}]}
    seed_path = tmp_path / "frozen.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")

    def no_review(*args, **kwargs):
        raise AssertionError("referee must not run when frozen")

    monkeypatch.setattr(referee, "review_episode", no_review)
    assert bench.main(["--name", "pf", "--red", "chain", "--sensors", "on",
                       "--memory", "on,off", "--episodes-per-cell", "1",
                       "--freeze-playbook", "--playbook-from",
                       str(seed_path)]) == 0
    saved = json.loads((tmp_path / "runs" / "bench-pf" / "playbooks" /
                        "chain-sensors-on-memory-on.json").read_text(
                            encoding="utf-8"))
    assert saved["rules"] == seed["rules"]
    assert not (tmp_path / "runs" / "bench-pf" / "playbooks" /
                "chain-sensors-on-memory-off.json").exists()


def test_bench_playbook_from_missing_file(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    assert bench.main(["--name", "pf", "--red", "chain",
                       "--episodes-per-cell", "1", "--memory", "on",
                       "--playbook-from", str(tmp_path / "gone.json")]) == 1
    assert "not found" in capsys.readouterr().err


def test_bench_grouped_seeds_pair_cells(tmp_path, monkeypatch):
    from crucible.scenarios import stack as stack_mod

    seen = _stub_common(monkeypatch, tmp_path)
    monkeypatch.setattr(sandbox, "start_warm", lambda: None)

    def fake_activate(key):
        if key == "classic":
            return None
        return {"scenario_id": "scn-7", "seed": 7, "difficulty": "easy"}

    monkeypatch.setattr(stack_mod, "activate", fake_activate)
    _stub_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    assert bench.main(["--name", "gz", "--red", "chain",
                       "--sensors", "on,off", "--memory", "off",
                       "--episodes-per-cell", "1",
                       "--scenarios", "classic,gen:7:easy"]) == 0
    assert seen["seeds"] == ["gz:classic:1"] * 2 + ["gz:gen-7-easy:1"] * 2
