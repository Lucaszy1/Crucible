"""Hermetic tests for the learning campaign runner.

Sandbox, exercise loop, and referee stay stubbed; playbook files land
in a temp dir. No Docker or model calls here.
"""

import csv
import json

from crucible import campaign, events, llm, orchestrator, sandbox
from crucible.agents import referee


def _stub_common(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(sandbox, "start", lambda: {"web": True})
    monkeypatch.setattr(sandbox, "stop", lambda: seen.setdefault("stopped", True))
    monkeypatch.setattr(campaign.tools, "restore_host",
                        lambda host: {"ok": True, "host": host})
    monkeypatch.setattr(campaign.tools, "reset_state", lambda: None)
    monkeypatch.setattr(events, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setattr(referee, "PLAYBOOK_JSON", tmp_path / "blue.json")
    monkeypatch.setattr(referee, "PLAYBOOK_MD", tmp_path / "blue.md")
    return seen


def _score(outcome="blue_held", blue=80.0, delay=2):
    return {"outcome": outcome, "blue_score": blue, "downtime_pct": 5.0,
            "detection_delay": delay, "time_to_contain": 4,
            "turns_used": 4, "total_events": 4}


def _stub_exercise(monkeypatch, seen, scores):
    def fake_exercise(red, blue, max_turns=40, run_path=None,
                      exercise_id=None, audit="off", sensors="on", seed=None,
                      blue_reasoning_effort=None):
        seen.setdefault("ids", []).append(exercise_id)
        seen.setdefault("audits", []).append(audit)
        seen.setdefault("sensors", []).append(sensors)
        seen["blue_playbook"] = getattr(blue, "_playbook", "scripted")
        score = scores[min(len(seen["ids"]) - 1, len(scores) - 1)]
        return {"events": [], "score": dict(score),
                "run_path": str(run_path),
                "audit_summary": {"mode": audit}}
    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(campaign.orchestrator, "run_exercise", fake_exercise)


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


def test_campaign_runs_episodes_with_ids(tmp_path, monkeypatch, capsys):
    seen = _stub_common(monkeypatch, tmp_path)
    _stub_exercise(monkeypatch, seen, [_score(), _score(), _score()])
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "mem1", "--episodes", "3", "--memory",
                          "on", "--agents", "model"]) == 0
    assert seen["ids"] == ["mem1-1", "mem1-2", "mem1-3"]
    assert seen["reviews"] == ["mem1-1", "mem1-2", "mem1-3"]
    assert seen["stopped"] is True
    assert seen["audits"] == ["off", "off", "off"]
    assert seen["sensors"] == ["on", "on", "on"]
    out = capsys.readouterr().out
    assert "mem1-1: outcome=blue_held" in out
    assert "Campaign done: 3 episodes" in out
    assert "Total cost: $" in out


def test_playbook_resets_then_grows(tmp_path, monkeypatch):
    _stub_common(monkeypatch, tmp_path)
    (tmp_path / "blue.json").write_text(
        json.dumps({"episode": "old", "rules": [{"rule": "Stale.", "episode": "old"}],
                    "model_used": "x"}), encoding="utf-8")
    seen = {}
    _stub_exercise(monkeypatch, seen, [_score(), _score()])
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "fresh", "--episodes", "2", "--memory",
                          "on", "--agents", "scripted"]) == 0
    live = json.loads((tmp_path / "blue.json").read_text(encoding="utf-8"))
    assert [rule["episode"] for rule in live["rules"]] == ["fresh-1", "fresh-2"]
    copy = tmp_path / "runs" / "campaign-fresh" / "playbook.json"
    assert json.loads(copy.read_text(encoding="utf-8")) == live
    snap = tmp_path / "runs" / "campaign-fresh" / "playbooks" / "fresh-1.json"
    assert len(json.loads(snap.read_text(encoding="utf-8"))["rules"]) == 1


def test_fresh_mem_arm_feeds_blue_empty_playbook_first(tmp_path, monkeypatch):
    seen = _stub_common(monkeypatch, tmp_path)
    (tmp_path / "blue.json").write_text(
        json.dumps({"episode": "old", "rules": [{"rule": "Stale.", "episode": "old"}],
                    "model_used": "x"}), encoding="utf-8")
    _stub_exercise(monkeypatch, seen, [_score(), _score()])
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "memx", "--episodes", "1", "--memory",
                          "on", "--agents", "model"]) == 0
    assert seen["blue_playbook"] == []


def test_memory_off_skips_referee(tmp_path, monkeypatch):
    seen = _stub_common(monkeypatch, tmp_path)
    _stub_exercise(monkeypatch, seen, [_score()])
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "ctl", "--episodes", "1", "--memory",
                          "off", "--agents", "model"]) == 0
    assert seen.get("reviews") is None
    assert seen["blue_playbook"] is None
    book = referee.load_playbook(tmp_path / "blue.json")
    assert book["rules"] == []


def test_memory_on_feeds_blue_growing_rules(tmp_path, monkeypatch):
    _stub_common(monkeypatch, tmp_path)
    fed = []
    real_blue = campaign.ModelBlue

    class _RecordingBlue(real_blue):
        def __init__(self, *args, **kwargs):
            fed.append(list(kwargs.get("playbook") or []))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(campaign, "ModelBlue", _RecordingBlue)
    seen = {}
    _stub_exercise(monkeypatch, seen, [_score(), _score()])
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "grow", "--episodes", "2", "--memory",
                          "on", "--agents", "model"]) == 0
    assert fed[0] == []
    assert [rule["episode"] for rule in fed[1]] == ["grow-1"]


def test_summary_csv_and_json(tmp_path, monkeypatch):
    _stub_common(monkeypatch, tmp_path)
    seen = {}
    scores = [_score("blue_held", 70.0, 3), _score("red_succeeded", 0.0, None),
              _score("blue_held", 90.0, 1)]
    _stub_exercise(monkeypatch, seen, scores)
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "rep", "--episodes", "3", "--memory",
                          "on"]) == 0
    csv_path = tmp_path / "runs" / "campaign-rep" / "summary.csv"
    with csv_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["episode"] for row in rows] == ["rep-1", "rep-2", "rep-3"]
    assert rows[0]["blue_score"] == "70.0"
    assert rows[0]["playbook_rule_count"] == "1"
    assert rows[1]["detection_delay"] == ""
    assert rows[2]["playbook_rule_count"] == "3"
    assert rows[0]["sensors"] == "on"
    state = json.loads((tmp_path / "runs" / "campaign-rep"
                        / "summary.json").read_text(encoding="utf-8"))
    assert state["name"] == "rep"
    assert state["memory"] == "on"
    assert state["sensors"] == "on"
    assert len(state["episodes"]) == 3
    assert state["episodes"][0]["referee_model"] == "muse-wonder-1.0"


def test_campaign_reports_intrusion_conditional_numbers(tmp_path, monkeypatch,
                                                            capsys):
    _stub_common(monkeypatch, tmp_path)
    seen = {}
    scores = [
        {"outcome": "red_succeeded", "blue_score": 0.0, "downtime_pct": 5.0,
         "detection_delay": 2, "time_to_contain": 6, "turns_used": 8,
         "total_events": 8, "intrusion": True,
         "contained_after_intrusion": True, "secret_read": True,
         "relevant_detection_delay": 3},
        {"outcome": "blue_held", "blue_score": 100.0, "downtime_pct": 0.0,
         "detection_delay": 1, "time_to_contain": None, "turns_used": 4,
         "total_events": 4, "intrusion": False,
         "contained_after_intrusion": False, "secret_read": False,
         "relevant_detection_delay": None},
    ]
    _stub_exercise(monkeypatch, seen, scores)
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "cond", "--episodes", "2", "--memory",
                          "off"]) == 0
    out = capsys.readouterr().out
    assert "Episodes with intrusion: 1/2" in out
    assert "Secret-read rate given intrusion: 100.0%" in out
    assert "Containment rate given intrusion: 100.0%" in out
    assert "Median relevant detection delay given intrusion: 3 events" in out
    assert "Median time to contain given intrusion: 6 events" in out
    assert "Mean downtime among contained: 5.00%" in out
    with (tmp_path / "runs" / "campaign-cond" / "summary.csv").open(
            encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["intrusion"] == "True"
    assert rows[0]["relevant_detection_delay"] == "3"
    assert rows[1]["intrusion"] == "False"
    assert rows[1]["relevant_detection_delay"] == ""
    header = list(rows[0].keys())
    assert header[:8] == ["episode", "outcome", "blue_score", "downtime_pct",
                          "detection_delay", "time_to_contain", "cost",
                          "playbook_rule_count"]
    assert header[8:] == ["sensors", "intrusion", "contained_after_intrusion",
                          "secret_read", "relevant_detection_delay",
                          "scenario_id", "scenario_seed",
                          "scenario_difficulty"]
    assert rows[0]["scenario_id"] == "classic"


def test_thirds_compare_first_vs_last(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    seen = {}
    scores = [_score("blue_held", float(60 + number * 10)) for number in range(6)]
    _stub_exercise(monkeypatch, seen, scores)
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "trend", "--episodes", "6", "--memory",
                          "on"]) == 0
    out = capsys.readouterr().out
    assert "Mean blue_score first third: 65.00 vs last third: 105.00" in out


def test_resume_continues_where_left_off(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    seen = {}
    _stub_exercise(monkeypatch, seen, [_score()])
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "long", "--episodes", "1", "--memory",
                          "on"]) == 0
    assert campaign.main(["--name", "long", "--episodes", "3", "--memory",
                          "on", "--resume"]) == 0
    assert seen["ids"] == ["long-1", "long-2", "long-3"]
    out = capsys.readouterr().out
    assert "Resuming campaign long at episode 2 of 3" in out
    state = json.loads((tmp_path / "runs" / "campaign-long"
                        / "summary.json").read_text(encoding="utf-8"))
    assert [row["episode"] for row in state["episodes"]] == ["long-1", "long-2",
                                                             "long-3"]
    assert [rule["episode"] for rule in
            json.loads((tmp_path / "blue.json").read_text(
                encoding="utf-8"))["rules"]] == ["long-1", "long-2", "long-3"]


def test_resume_rejects_unknown_or_mismatch(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    seen = {}
    _stub_exercise(monkeypatch, seen, [_score()])
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "ghost", "--episodes", "2", "--memory",
                          "on", "--resume"]) == 1
    assert "no resumable campaign" in capsys.readouterr().err
    assert campaign.main(["--name", "real", "--episodes", "1", "--memory",
                          "on"]) == 0
    assert campaign.main(["--name", "real", "--episodes", "2", "--memory",
                          "off", "--resume"]) == 1
    assert "differs from the saved campaign" in capsys.readouterr().err
    assert campaign.main(["--name", "real", "--episodes", "2", "--memory",
                          "on", "--sensors", "off", "--resume"]) == 1
    assert "differs from the saved campaign" in capsys.readouterr().err


def test_bad_name_and_count_rejected(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    assert campaign.main(["--name", "../evil", "--episodes", "2", "--memory",
                          "on"]) == 1
    assert "campaign name" in capsys.readouterr().err
    assert campaign.main(["--name", "ok", "--episodes", "0", "--memory",
                          "on"]) == 1
    assert "at least 1" in capsys.readouterr().err


def test_existing_dir_refuses_without_resume_or_force(tmp_path, monkeypatch,
                                                      capsys):
    _stub_common(monkeypatch, tmp_path)
    _stub_exercise(monkeypatch, {}, [_score()])
    _stub_referee(monkeypatch, {})
    assert campaign.main(["--name", "dup", "--episodes", "1", "--memory",
                          "off"]) == 0
    capsys.readouterr()
    assert campaign.main(["--name", "dup", "--episodes", "1", "--memory",
                          "off"]) == 1
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "--resume" in err and "--force" in err


def test_force_archives_old_dir_and_starts_over(tmp_path, monkeypatch, capsys):
    seen = _stub_common(monkeypatch, tmp_path)
    _stub_exercise(monkeypatch, seen, [_score()])
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "redo", "--episodes", "1", "--memory",
                          "off"]) == 0
    old_csv = tmp_path / "runs" / "campaign-redo" / "summary.csv"
    assert old_csv.exists()
    assert campaign.main(["--name", "redo", "--episodes", "1", "--memory",
                          "off", "--force"]) == 0
    out = capsys.readouterr().out
    assert "Archived previous campaign" in out
    archived = list((tmp_path / "runs" / "archive").glob("campaign-redo-*"))
    assert len(archived) == 1
    assert (archived[0] / "summary.csv").exists()
    assert seen["ids"] == ["redo-1", "redo-1"]
    with (tmp_path / "runs" / "campaign-redo" / "summary.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) == 1


def _stub_seeded_exercise(monkeypatch, seen, events_by_id=None):
    def fake_exercise(red, blue, max_turns=40, run_path=None,
                      exercise_id=None, audit="off", sensors="on", seed=None,
                      blue_reasoning_effort=None):
        seen.setdefault("ids", []).append(exercise_id)
        seen.setdefault("seeds_logged", []).append(seed)
        seen.setdefault("seeds", []).append((getattr(red, "_seed", None),
                                             getattr(blue, "_seed", None)))
        seen.setdefault("fed", []).append(list(
            getattr(blue, "_playbook", None) or []))
        events = (events_by_id or {}).get(exercise_id, [])
        return {"events": events, "score": _score(),
                "run_path": str(run_path),
                "audit_summary": {"mode": audit}}
    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(campaign.orchestrator, "run_exercise", fake_exercise)


def test_arms_interleave_with_pair_seeds(tmp_path, monkeypatch):
    seen = _stub_common(monkeypatch, tmp_path)
    starts = []
    monkeypatch.setattr(sandbox, "start",
                        lambda: starts.append(1) or {"web": True})
    _stub_seeded_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--arms", "mem,ctrl", "--episodes", "2",
                          "--agents", "model"]) == 0
    assert seen["ids"] == ["mem-1", "ctrl-1", "mem-2", "ctrl-2"]
    assert seen["seeds"] == [("pair-1", "pair-1")] * 2 + [("pair-2", "pair-2")] * 2
    assert seen["seeds_logged"] == ["pair-1", "pair-1", "pair-2", "pair-2"]
    assert starts == [1]
    for arm in ("mem", "ctrl"):
        summary = tmp_path / "runs" / f"campaign-{arm}" / "summary.json"
        state = json.loads(summary.read_text(encoding="utf-8"))
        assert [row["episode"] for row in state["episodes"]] == [f"{arm}-1",
                                                                 f"{arm}-2"]
    assert [rule["episode"] for rule in seen["fed"][2]] == ["mem-1"]
    assert seen["fed"][3] == []


def test_arms_positional_memory_and_suffix_override(tmp_path, monkeypatch):
    seen = _stub_common(monkeypatch, tmp_path)
    _stub_seeded_exercise(monkeypatch, seen)
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--arms", "mem,ctrl", "--episodes", "1",
                          "--agents", "model"]) == 0
    assert seen.get("reviews") == ["mem-1"]
    mem_state = json.loads((tmp_path / "runs" / "campaign-mem"
                            / "summary.json").read_text(encoding="utf-8"))
    ctrl_state = json.loads((tmp_path / "runs" / "campaign-ctrl"
                             / "summary.json").read_text(encoding="utf-8"))
    assert mem_state["memory"] == "on"
    assert ctrl_state["memory"] == "off"

    seen2 = _stub_common(monkeypatch, tmp_path)
    _stub_seeded_exercise(monkeypatch, seen2)
    _stub_referee(monkeypatch, seen2)
    assert campaign.main(["--arms", "a:off,b:on", "--episodes", "1",
                          "--agents", "model"]) == 0
    assert seen2.get("reviews") == ["b-1"]


def _red_event(tool, args, result, index):
    return {"id": f"evt-{index:04d}", "timestamp": "2026-01-01T00:00:00+00:00",
            "actor": "red", "tool": tool, "args": args, "result": result,
            "reasoning_summary": "r", "evidence_refs": []}


def test_arms_report_admin_in_and_wins(tmp_path, monkeypatch, capsys):
    seen = _stub_common(monkeypatch, tmp_path)
    admin = _red_event("http_request", {"host": "web", "path": "/admin"},
                       {"ok": True, "status": 200}, 1)
    breach = _red_event("try_credential", {"host": "db"},
                        {"ok": True, "authenticated": True}, 2)
    _stub_seeded_exercise(monkeypatch, seen, {
        "mem-1": [admin], "mem-2": [admin, breach], "ctrl-1": [breach],
        "ctrl-2": []})
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--arms", "mem,ctrl", "--episodes", "2"]) == 0
    out = capsys.readouterr().out
    assert "mem: red /admin 2/2 (1.0) | red in 1/2 (0.5) | wins 2" in out
    assert "ctrl: red /admin 0/2 (0.0) | red in 1/2 (0.5) | wins 2" in out


def test_arms_rejects_bad_specs(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    for spec in ("solo", "a,b,c", "same,same", "a:maybe,b:on"):
        assert campaign.main(["--arms", spec, "--episodes", "1"]) == 1
        assert "campaign:" in capsys.readouterr().err
    assert campaign.main(["--arms", "mem,ctrl", "--episodes", "1",
                          "--memory", "on"]) == 1
    assert "conflicts with --arms" in capsys.readouterr().err
    assert campaign.main(["--episodes", "1", "--memory", "on"]) == 1
    assert "--name is required" in capsys.readouterr().err


def test_single_arm_reports_stats_line(tmp_path, monkeypatch, capsys):
    seen = _stub_common(monkeypatch, tmp_path)
    admin = _red_event("http_request", {"host": "web", "path": "/admin"},
                       {"ok": True, "status": 200}, 1)
    _stub_seeded_exercise(monkeypatch, seen, {"solo-1": [admin]})
    _stub_referee(monkeypatch, seen)
    assert campaign.main(["--name", "solo", "--episodes", "1", "--memory",
                          "off"]) == 0
    out = capsys.readouterr().out
    assert "solo: red /admin 1/1 (1.0) | red in 0/1 (0.0) | wins 1" in out
