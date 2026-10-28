"""Hermetic tests for the solver red profile.

Specs come from the deterministic generator, execution runs in the
in-memory simulator, and the benchmark harness stays stubbed. No
Docker or model calls here.
"""

import json

from crucible import bench, events, llm, orchestrator, sandbox
from crucible.scenarios import chain as chain_mod
from crucible.scenarios import generate
from crucible.scenarios.simulate import Range
from crucible.solver_red import MAX_CHAIN_RETRIES, SolverRed, solver_plan

TRAIN = [(1001, "easy"), (1002, "easy"), (1003, "easy"),
         (1004, "medium"), (1005, "medium"), (1006, "medium"),
         (1007, "hard"), (1008, "hard"), (1009, "hard")]

SEEDS = [f"solver-seed-{index}" for index in range(5)]


def _spec(seed, difficulty):
    return generate.generate(seed, difficulty)


def _real_triples(spec):
    triples = set()
    for step in spec.get("path", []):
        if step.get("kind") in ("weak_creds", "stale_reuse",
                                "signin_known"):
            triples.add((step.get("host"), step.get("account"),
                         step.get("password")))
    for end in spec.get("dead_ends", []):
        triples.update({
            (end.get("host"), end.get("account"), end.get("password")),
            (end.get("terminal_host"), end.get("terminal_account"),
             end.get("password")),
        })
    return triples


def _chain_moves(plan):
    return [(move["tool"], json.dumps(move["args"], sort_keys=True))
            for move in plan if move.get("class") == "chain"]


def test_solver_plan_deterministic_per_seed():
    spec = _spec(1001, "easy")
    assert solver_plan(spec, "pair-seed") == solver_plan(spec, "pair-seed")


def test_solver_plan_varies_across_seeds():
    spec = _spec(1008, "hard")
    plans = {json.dumps(solver_plan(spec, seed), sort_keys=True)
             for seed in SEEDS}
    assert len(plans) == len(SEEDS)


def test_solver_plan_keeps_chain_order_on_all_train_specs():
    for seed, difficulty in TRAIN:
        spec = _spec(seed, difficulty)
        for plan_seed in SEEDS[:3]:
            plan = solver_plan(spec, plan_seed)
            want = [(move["tool"],
                     json.dumps(move["args"], sort_keys=True))
                    for move in chain_mod.chain_plan(spec)]
            assert _chain_moves(plan) == want


def test_solver_guesses_never_match_live_triples():
    for seed, difficulty in TRAIN:
        spec = _spec(seed, difficulty)
        real = _real_triples(spec)
        for plan_seed in SEEDS:
            for move in solver_plan(spec, plan_seed):
                if move.get("class") not in ("decoy", "wrong"):
                    continue
                if move["tool"] != "try_credential":
                    continue
                args = move["args"]
                assert (args["host"], args["user"],
                        args["password"]) not in real


def test_solver_plan_fits_turn_budget_with_fast_intrusion():
    for seed, difficulty in TRAIN:
        spec = _spec(seed, difficulty)
        for plan_seed in SEEDS:
            plan = solver_plan(spec, plan_seed)
            assert len(plan) <= 20
            first_signin = next(index for index, move in enumerate(plan)
                                if move["tool"] == "try_credential"
                                and move.get("class") == "chain")
            assert first_signin <= 2


def _run_in_simulator(spec, seed):
    """Drive SolverRed through Range, returning (events, jewel_ok)."""
    red = SolverRed(seed, spec=spec)
    runner = Range(spec)
    history = []
    jewel_ok = False
    turn = 1
    while True:
        action = red.next_action(turn, history)
        if action is None:
            break
        assert "class" not in action
        result = runner.run_action(action)
        history.append({"actor": "red", "tool": action["tool"],
                        "args": dict(action["args"]), "result": result})
        if action["tool"] == "read_file" and result.get("ok") and \
                result.get("path") == spec["jewel"]["path"]:
            jewel_ok = "TOY-SECRET" in result.get("content", "")
        turn += 1
        assert turn <= 30
    return history, jewel_ok


def test_solver_reaches_jewel_in_simulator():
    for seed, difficulty in [(1001, "easy"), (1005, "medium"),
                             (1008, "hard")]:
        for plan_seed in SEEDS[:3]:
            _, jewel_ok = _run_in_simulator(_spec(seed, difficulty),
                                            plan_seed)
            assert jewel_ok


def test_solver_action_logs_differ_across_seeds():
    spec = _spec(1005, "medium")
    logs = set()
    for plan_seed in SEEDS:
        history, _ = _run_in_simulator(spec, plan_seed)
        logs.add(json.dumps([(event["tool"], event["args"])
                             for event in history], sort_keys=True))
    assert len(logs) == len(SEEDS)


def test_solver_retries_failed_chain_move_then_advances():
    spec = _spec(1001, "easy")
    red = SolverRed("retry-seed", spec=spec)
    # Walk to the first chain move, answering every delay ok.
    history = []
    turn = 1
    while True:
        action = red.next_action(turn, history)
        plan_move = red.plan[len([event for event in history
                                  if event["actor"] == "red"])]
        history.append({"actor": "red", "tool": action["tool"],
                        "args": dict(action["args"]),
                        "result": {"ok": True}})
        turn += 1
        if plan_move.get("class") == "chain":
            break
    chain_move = plan_move
    # Fail it: the solver must retry the identical move.
    history[-1] = {"actor": "red", "tool": action["tool"],
                   "args": dict(action["args"]),
                   "result": {"ok": False, "error": "isolated"}}
    # Rebuild: re-issue history through a fresh agent is complex, so
    # check retry directly on the live agent state.
    retry = red.next_action(turn, history)
    assert (retry["tool"], retry["args"]) == \
        (chain_move["tool"], chain_move["args"])
    for _ in range(MAX_CHAIN_RETRIES - 1):
        history.append({"actor": "red", "tool": retry["tool"],
                        "args": dict(retry["args"]),
                        "result": {"ok": False, "error": "isolated"}})
        retry = red.next_action(turn, history)
        assert (retry["tool"], retry["args"]) == \
            (chain_move["tool"], chain_move["args"])
    # Past the cap it advances instead of stalling.
    history.append({"actor": "red", "tool": retry["tool"],
                    "args": dict(retry["args"]),
                    "result": {"ok": False, "error": "isolated"}})
    advanced = red.next_action(turn, history)
    assert (advanced["tool"], advanced["args"]) != \
        (chain_move["tool"], chain_move["args"])


def test_solver_failed_signin_retries_but_decoy_failure_advances():
    spec = _spec(1002, "easy")
    red = SolverRed("signin-seed", spec=spec)
    plan = red.plan
    signin_index = next(index for index, move in enumerate(plan)
                        if move["tool"] == "try_credential"
                        and move.get("class") == "chain")
    history = []
    for turn, move in enumerate(plan[:signin_index], start=1):
        action = red.next_action(turn, history)
        assert action["tool"] == move["tool"]
        history.append({"actor": "red", "tool": action["tool"],
                        "args": dict(action["args"]),
                        "result": {"ok": True, "authenticated": True}})
    turn = signin_index + 1
    action = red.next_action(turn, history)
    assert action["tool"] == "try_credential"
    history.append({"actor": "red", "tool": action["tool"],
                    "args": dict(action["args"]),
                    "result": {"ok": True, "authenticated": False}})
    retry = red.next_action(turn + 1, history)
    assert retry["args"] == action["args"]
    # A failed decoy is never retried: find one and fail it.
    decoy_index = next((index for index, move in enumerate(plan)
                        if move.get("class") == "decoy"), None)
    if decoy_index is not None:
        red2 = SolverRed("signin-seed", spec=spec)
        history2 = []
        for turn2, move in enumerate(plan[:decoy_index], start=1):
            action2 = red2.next_action(turn2, history2)
            history2.append({"actor": "red", "tool": action2["tool"],
                             "args": dict(action2["args"]),
                             "result": {"ok": True,
                                        "authenticated": True}})
        action2 = red2.next_action(decoy_index + 1, history2)
        history2.append({"actor": "red", "tool": action2["tool"],
                         "args": dict(action2["args"]),
                         "result": {"ok": False, "error": "boom"}})
        advanced = red2.next_action(decoy_index + 2, history2)
        assert advanced["args"] != action2["args"] or \
            advanced["tool"] != action2["tool"] or advanced is None


def test_solver_makes_zero_model_calls(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("solver red must not call the model client")

    monkeypatch.setattr(llm, "chat", _boom)
    for seed, difficulty in [(1001, "easy"), (1008, "hard")]:
        history, jewel_ok = _run_in_simulator(_spec(seed, difficulty),
                                              "no-model")
        assert jewel_ok


def test_solver_needs_generated_spec(monkeypatch):
    monkeypatch.setattr(sandbox, "active_spec", lambda: None)
    try:
        SolverRed("classic-seed")
    except ValueError as exc:
        assert "generated scenario" in str(exc)
    else:
        raise AssertionError("solver without a spec must fail loudly")


def test_cells_expand_solver_in_flag_order():
    args = bench.parse_args(["--name", "x", "--red", "solver",
                             "--sensors", "on,off", "--memory", "off"])
    assert [cell["key"] for cell in bench._cells(args)] == [
        "solver-sensors-on-memory-off",
        "solver-sensors-off-memory-off",
    ]


def test_bench_wires_solver_with_paired_seed(tmp_path, monkeypatch):
    from crucible.scenarios import stack as stack_mod

    monkeypatch.setattr(sandbox, "start", lambda: {"up": True})
    monkeypatch.setattr(sandbox, "start_warm", lambda: None)
    monkeypatch.setattr(sandbox, "stop", lambda: None)
    monkeypatch.setattr(bench.tools, "restore_host",
                        lambda host: {"ok": True, "host": host})
    monkeypatch.setattr(bench.tools, "reset_state", lambda: None)
    monkeypatch.setattr(events, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setattr(llm, "spent_today", lambda *a, **k: 0.0)
    monkeypatch.setattr(stack_mod, "activate",
                        lambda key: {"scenario_id": "scn-t",
                                     "seed": 1, "difficulty": "easy"})
    seen = {}

    class FakeSolver:
        actor = "red"

        def __init__(self, seed, spec=None):
            del spec
            seen.setdefault("solver_seeds", []).append(seed)

        def next_action(self, turn, history):
            del turn, history
            return None

    monkeypatch.setattr(bench, "SolverRed", FakeSolver)

    class FakeBlue:
        actor = "blue"

        def __init__(self, *args, **kwargs):
            pass

        def next_action(self, turn, history):
            del turn, history
            return None

    monkeypatch.setattr(bench, "ModelBlue", FakeBlue)

    def fake_exercise(red, blue, max_turns=40, run_path=None,
                      exercise_id=None, audit="off", sensors="on",
                      seed=None, blue_reasoning_effort=None,
                      scenario=None):
        del red, blue, max_turns, audit, sensors, \
            blue_reasoning_effort, scenario
        seen.setdefault("ids", []).append(exercise_id)
        return {"events": [],
                "score": {"outcome": "blue_held", "blue_score": 100.0,
                          "downtime_pct": 0.0, "intrusion": True,
                          "contained_after_intrusion": False,
                          "secret_read": True},
                "run_path": str(run_path or ""),
                "audit_summary": {"mode": "off"}}

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(bench.orchestrator, "run_exercise", fake_exercise)
    assert bench.main(["--name", "sv", "--red", "solver",
                       "--sensors", "on,off", "--memory", "off",
                       "--episodes-per-cell", "1",
                       "--scenarios", "gen:1:easy,gen:2:easy"]) == 0
    # Both arms share the paired seed within each scenario+rep.
    assert seen["solver_seeds"] == ["sv:gen-1-easy:1"] * 2 + \
        ["sv:gen-2-easy:1"] * 2
