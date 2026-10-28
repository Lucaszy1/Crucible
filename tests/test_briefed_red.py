"""Hermetic tests for the model-briefed red profile.

Specs are staged in memory via set_active_spec (no Docker), the model
client stays stubbed, and the benchmark harness stays stubbed. No
Docker or model calls here.
"""

import contextlib
import re

from crucible import bench, events, llm, orchestrator, sandbox
from crucible.agents import _common, briefed_red, red
from crucible.scenarios import generate, words

TRAIN = [(1001, "easy"), (1002, "easy"), (1003, "easy"),
         (1004, "medium"), (1005, "medium"), (1006, "medium"),
         (1007, "hard"), (1008, "hard"), (1009, "hard")]


@contextlib.contextmanager
def _stage(seed, difficulty):
    """Stage one generated spec without Docker, then reset to classic."""
    sandbox.set_active_spec(generate.generate(seed, difficulty))
    try:
        yield sandbox.active_spec()
    finally:
        sandbox.set_active_spec(None)


def _dmz_hosts(spec):
    return tuple(host["name"] for host in spec["hosts"]
                 if host["segment"] == "dmz")


def _spec_secrets(spec):
    """Every answer string the briefing must never disclose."""
    secrets = set()
    for step in spec.get("path", []):
        for key in ("account", "password", "page_path", "path",
                    "reveals_account", "reveals_password"):
            value = step.get(key)
            if value:
                secrets.add(str(value))
    jewel = spec.get("jewel", {}) or {}
    if jewel.get("path"):
        secrets.add(jewel["path"])
    return secrets


def test_briefing_scope_lists_exactly_dmz_hosts():
    for seed, difficulty in TRAIN:
        with _stage(seed, difficulty) as spec:
            assert briefed_red.in_scope_hosts() == _dmz_hosts(spec)
            assert spec["entry_host"] in briefed_red.in_scope_hosts()
    sandbox.set_active_spec(None)
    assert briefed_red.in_scope_hosts() == ("web",)


def test_briefing_covers_endpoint_pool_and_weak_pool():
    with _stage(1005, "medium"):
        text = briefed_red.engagement_briefing()
    for endpoint in words.ADMIN_PAGES:
        assert endpoint in text
    for password in words.WEAK_PASSWORDS:
        assert password in text
    assert "list_files /" in text


def test_briefing_discloses_no_answers():
    for seed, difficulty in TRAIN:
        with _stage(seed, difficulty) as spec:
            text = briefed_red.engagement_briefing()
            for secret in _spec_secrets(spec):
                if secret in words.WEAK_PASSWORDS:
                    continue  # the weak pool is shared product knowledge
                if secret in words.ADMIN_PAGES:
                    continue  # the endpoint pool is listed whole, see below
                for hit in re.finditer(r"\b" + re.escape(secret) + r"\b",
                                       text):
                    # An account word may only surface inside a listed
                    # pool endpoint (e.g. account "metrics" in
                    # "/admin/metrics"), never as a named credential.
                    before = text[max(0, hit.start() - 7):hit.start()]
                    assert before == "/admin/", secret
            # Endpoints are disclosed only as the undifferentiated pool:
            # all eight appear, so none is singled out as the live one.
            for endpoint in words.ADMIN_PAGES:
                assert endpoint in text


def test_briefing_names_no_single_entry_host():
    for seed, difficulty in TRAIN:
        with _stage(seed, difficulty) as spec:
            text = briefed_red.engagement_briefing()
            scope = briefed_red.in_scope_hosts()
            for host in scope:
                assert host in text
            # The scope is the whole dmz surface, never one picked host.
            assert len(scope) == len(_dmz_hosts(spec))


def _red_event(tool, args, index=1, actor="red"):
    return {
        "id": f"evt-{index:04d}", "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": actor, "tool": tool, "args": args, "result": {"ok": True},
        "reasoning_summary": "probe", "evidence_refs": [],
    }


def test_known_state_tracks_probed_paths_without_repeats():
    history = [
        _red_event("http_request", {"host": "thistle", "path": "/"}),
        _red_event("http_request", {"host": "thistle", "path": "/admin"},
                   index=2),
        _red_event("http_request", {"host": "thistle", "path": "/"}, index=3),
        _red_event("scan_host", {"host": "thistle"}, index=4),
        _red_event("http_request", {"host": "blue-host", "path": "/x"},
                   index=5, actor="blue"),
    ]
    state = briefed_red.known_state_briefed(history)
    assert "paths probed: thistle /, thistle /admin" in state
    assert state.startswith(_common.red_known_state(history))
    assert "blue-host" not in state


def test_known_state_reports_no_paths_when_empty():
    assert briefed_red.known_state_briefed([]).endswith("paths probed: none")


def test_base_model_profile_unchanged():
    with _stage(1001, "easy"):
        assert "ENGAGEMENT BRIEFING" not in red.system_prompt()
        assert "ENGAGEMENT BRIEFING" not in red._build_messages(1, [])[0][
            "content"]
        assert "ENGAGEMENT BRIEFING" in briefed_red.system_prompt()
    assert "ENGAGEMENT BRIEFING" not in red.SYSTEM_PROMPT
    assert red.ModelRed()._max_turns == 40
    assert briefed_red.ModelBriefedRed()._max_turns == 60


def test_briefed_messages_keep_blue_text_out():
    history = [
        _red_event("scan_host", {"host": "thistle"}),
        _red_event("report_finding", {"text": "MARKER-BLUE-SECRET"},
                   index=2, actor="blue"),
    ]
    with _stage(1001, "easy"):
        messages = briefed_red._build_messages(1, history)
    blob = "\n".join(str(message.get("content", "")) for message in messages)
    assert "MARKER-BLUE-SECRET" not in blob
    assert "paths probed: none" in blob
    assert "turn 1 of 60" in blob


def test_briefed_red_calls_model_as_red_with_briefing(monkeypatch):
    seen = {}

    def fake_chat(role, messages, **kwargs):
        seen["role"] = role
        seen["messages"] = messages
        seen["kwargs"] = kwargs
        return {"content": "Probing the entry surface.",
                "tool_calls": [{"name": "scan_host",
                                "arguments": {"host": "thistle"}}]}

    monkeypatch.setattr(llm, "chat", fake_chat)
    with _stage(1001, "easy"):
        action = briefed_red.ModelBriefedRed(
            exercise_id="probe-1").next_action(1, [], max_turns=60)
    assert seen["role"] == "red"
    assert "ENGAGEMENT BRIEFING" in seen["messages"][0]["content"]
    assert "turn 1 of 60" in seen["messages"][1]["content"]
    assert action == {"tool": "scan_host", "args": {"host": "thistle"},
                      "reasoning": "Probing the entry surface.",
                      "evidence_refs": []}


def test_briefed_red_falls_back_when_model_fails(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("no model in this test")

    monkeypatch.setattr(llm, "chat", _boom)
    with _stage(1001, "easy"):
        action = briefed_red.ModelBriefedRed(
            exercise_id="probe-1").next_action(1, [])
    assert action["tool"] == "scan_host"
    assert "Model call failed" in action["reasoning"]


def _stub_bench(monkeypatch, tmp_path, seen):
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

    class FakeBriefed:
        actor = "red"

        def __init__(self, exercise_id=None, use_cache=True, seed=None):
            seen.setdefault("briefed", []).append((exercise_id, seed))

        def next_action(self, turn, history):
            del turn, history
            return None

    monkeypatch.setattr(bench, "ModelBriefedRed", FakeBriefed)

    class FakeBlue:
        actor = "blue"

        def __init__(self, *args, **kwargs):
            seen.setdefault("blue", []).append("model")

        def next_action(self, turn, history):
            del turn, history
            return None

    monkeypatch.setattr(bench, "ModelBlue", FakeBlue)

    class FakeScriptedBlue:
        actor = "blue"

        def __init__(self):
            seen.setdefault("blue", []).append("scripted")

        def next_action(self, turn, history):
            del turn, history
            return None

    monkeypatch.setattr(bench, "ScriptedBlue", FakeScriptedBlue)

    def fake_exercise(red, blue, max_turns=40, run_path=None,
                      exercise_id=None, audit="off", sensors="on",
                      seed=None, blue_reasoning_effort=None,
                      scenario=None, auditor_use_cache=True):
        del red, blue, max_turns, audit, sensors, seed, \
            blue_reasoning_effort, scenario, auditor_use_cache
        return {"events": [],
                "score": {"outcome": "blue_held", "blue_score": 100.0,
                          "downtime_pct": 0.0, "intrusion": False,
                          "contained_after_intrusion": False,
                          "secret_read": False},
                "run_path": str(run_path or ""),
                "audit_summary": {"mode": "off"}}

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(bench.orchestrator, "run_exercise", fake_exercise)


def test_bench_accepts_briefed_red_and_scripted_blue(tmp_path, monkeypatch):
    import csv
    import json

    seen = {}
    _stub_bench(monkeypatch, tmp_path, seen)
    assert bench.main(["--name", "bp", "--red", "model-briefed",
                       "--blue", "scripted", "--sensors", "on",
                       "--memory", "off", "--episodes-per-cell", "1",
                       "--scenarios", "gen:1:easy",
                       "--max-turns", "60"]) == 0
    assert seen["blue"] == ["scripted"]
    assert seen["briefed"] == [("model-briefed-sensors-on-memory-off-1",
                                "bp:1")]
    rows = list(csv.DictReader(
        (tmp_path / "runs" / "bench-bp" / "summary.csv").open()))
    assert rows[0]["blue_profile"] == "scripted"
    summary = json.loads(
        (tmp_path / "runs" / "bench-bp" / "summary.json").read_text())
    assert summary["blue_profile"] == "scripted"


def test_bench_blue_defaults_to_model_and_resume_guards_it(
        tmp_path, monkeypatch, capsys):
    assert bench.parse_args(["--name", "x"]).blue == "model"
    seen = {}
    _stub_bench(monkeypatch, tmp_path, seen)
    base = ["--name", "bg", "--red", "chain", "--sensors", "on",
            "--memory", "off", "--episodes-per-cell", "1",
            "--scenarios", "gen:1:easy"]
    assert bench.main(base + ["--blue", "scripted"]) == 0
    assert bench.main(base + ["--resume"]) == 1
    assert "blue profile differs" in capsys.readouterr().err


def test_turn_ceiling_sixty_with_forty_defaults():
    from crucible import batch, campaign, run_exercise

    assert orchestrator.MAX_TURNS == 60
    assert bench.parse_args(["--name", "x"]).max_turns == 40
    assert run_exercise.parse_args([]).max_turns == 40
    assert batch.parse_args(
        ["--count", "1", "--prefix", "x"]).max_turns == 40
    assert campaign.parse_args(
        ["--episodes", "1", "--name", "x",
         "--memory", "on"]).max_turns == 40
