"""Hermetic tests for the auditor eval harness.

Cases are built from crafted run logs in a temp dir, and a fake client
stands in for the model, so no network, Docker, or real runs/ data is
needed.
"""

import json
from types import SimpleNamespace

from crucible import audit_eval, llm
from crucible.agents.auditor import check_evidence


def _response(content):
    message = SimpleNamespace(content=content, tool_calls=[])
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=usage)


class _FakeClient:
    def __init__(self, responses):
        self.calls = []
        self._responses = list(responses)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


def _judgment(verdict, score=7):
    return json.dumps({"verdict": verdict, "score": score,
                       "rationale": f"Why {verdict}."})


def _evt(index, actor, tool, args=None, result=None, reasoning="note",
         refs=None):
    return {
        "id": f"evt-{index:04d}",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": actor,
        "tool": tool,
        "args": args or {},
        "result": result or {"ok": True},
        "reasoning_summary": reasoning,
        "evidence_refs": refs or [],
    }


def _read(index, host, lines):
    return _evt(index, "blue", "read_logs", {"host": host, "n": 50},
                {"ok": True, "host": host,
                 "lines": [{"id": ref, "line": text}
                           for ref, text in lines]})


def _write_log(path, events):
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n",
                    encoding="utf-8")


def _rich_log():
    return [
        _evt(1, "red", "scan_host", {"host": "web"},
             {"ok": True, "host": "web"}),
        _read(2, "web", [("web:0", "webapp listening on 8000")]),
        _evt(3, "red", "try_credential", {"host": "app"},
             {"ok": True, "host": "app", "authenticated": True}),
        _read(4, "app", [("app:0", "signin reset from=controller"),
                         ("app:1", "signin fail user=admin from=controller")]),
        _evt(5, "blue", "report_finding",
             {"text": "Credential probing on app"}, {"ok": True},
             "Reporting app probing.", ["app:1"]),
        _read(6, "web", [("web:0", "webapp listening on 8000"),
                         ("web:1", "request from=1.2.3.4 method=GET "
                                   "path=/admin status=200")]),
        _read(7, "app", [("app:0", "signin reset from=controller"),
                         ("app:1", "signin fail user=admin from=controller"),
                         ("app:2", "signin fail user=root from=controller")]),
        _evt(8, "red", "try_credential", {"host": "db"},
             {"ok": True, "host": "db", "authenticated": True}),
        _read(9, "db", [("db:0", "signin reset from=controller"),
                        ("db:1", "signin ok user=dbops from=controller")]),
        _read(10, "db", [("db:0", "signin reset from=controller"),
                         ("db:1", "signin ok user=dbops from=controller"),
                         ("db:2", "dir list path=/secret entries=1 from=controller")]),
        _read(11, "db", [("db:2", "dir list path=/secret entries=1 from=controller"),
                         ("db:3", "file read path=/secret/exercise_secret.txt "
                                 "bytes=9 from=controller")]),
        _evt(12, "blue", "isolate_host", {"host": "db"},
             {"ok": True, "host": "db"}, "Isolating db.", ["db:1"]),
        _evt(13, "red", "read_file", {"host": "db"},
             {"ok": True, "host": "db", "path": "/secret/exercise_secret.txt"}),
    ]


def _sparse_log():
    return [
        _read(1, "web", [("web:0", "webapp listening on 8000")]),
        _read(2, "app", [("app:1", "signin fail user=admin from=controller")]),
        _read(3, "db", [("db:1", "signin ok user=x from=controller")]),
        _read(4, "db", [("db:2", "dir list path=/secret entries=1 from=controller")]),
        _read(5, "web", [("web:1", "request from=1.2.3.4 GET /admin")]),
        _read(6, "app", [("app:2", "signin fail user=root from=controller")]),
        _read(7, "app", [("app:3", "signin fail user=test from=controller")]),
        _read(8, "db", [("db:3", "file read path=/secret/x bytes=1 from=controller")]),
    ]


def _isolated_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")


def test_build_cases_counts_and_kinds(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_log(runs / "eval-1.jsonl", _rich_log())
    cases = audit_eval.build_cases(runs)
    assert len(cases) == 16
    kinds = [case["kind"] for case in cases]
    assert kinds.count("good") == 6
    assert kinds.count("bad_evidence") == 5
    assert kinds.count("bad_judgment") == 5
    assert cases[0]["expected"] == "ok"
    assert cases[0]["note"].startswith("Real ")
    for case in cases[6:]:
        assert case["expected"] == "flag"
        assert "SYNTHETIC" in case["note"]


def test_build_cases_deterministic(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_log(runs / "eval-1.jsonl", _rich_log())
    first = audit_eval.build_cases(runs)
    second = audit_eval.build_cases(runs)
    assert [case["name"] for case in first] == [case["name"] for case in second]
    assert [json.dumps(case["blue_event"], sort_keys=True) for case in first] == [
        json.dumps(case["blue_event"], sort_keys=True) for case in second]


def test_bad_cases_hit_layer1_rules(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_log(runs / "eval-1.jsonl", _rich_log())
    by_name = {case["name"]: case for case in audit_eval.build_cases(runs)}
    for name in ("ev-fabricated-id", "ev-wrong-host", "ev-mixed-refs"):
        case = by_name[name]
        assert check_evidence(case["blue_event"], case["priors"])["refs_valid"] is False, name
    mismatch = by_name["ev-claim-mismatch"]
    assert check_evidence(mismatch["blue_event"],
                          mismatch["priors"])["refs_valid"] is True
    reread = by_name["judge-redundant-read"]
    assert check_evidence(reread["blue_event"],
                          reread["priors"])["redundant"] is True
    moot = by_name["judge-moot-exfil"]
    assert check_evidence(moot["blue_event"], moot["priors"])["moot"] is True
    quiet = by_name["judge-quiet-isolate"]
    assert check_evidence(quiet["blue_event"],
                          quiet["priors"])["suspicious_visible"] is False


def test_fallback_goods_when_no_strict(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_log(runs / "sparse.jsonl", _sparse_log())
    cases = audit_eval.build_cases(runs)
    assert len(cases) == 16
    goods = [case for case in cases if case["kind"] == "good"]
    assert len(goods) == 6
    assert all("SYNTHETIC" in case["note"] for case in goods)


def test_empty_runs_dir_raises(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    try:
        audit_eval.build_cases(runs)
    except audit_eval.EvalError as exc:
        assert "no run logs" in str(exc)
    else:
        raise AssertionError("expected EvalError")


def _scripted_client():
    return _FakeClient(
        [_response(_judgment("justified", 8))] * 6
        + [_response(_judgment("unsupported", 2))] * 5
        + [_response(_judgment("premature", 4)),
           _response(_judgment("premature", 4)),
           _response(_judgment("redundant", 3)),
           _response(_judgment("moot", 2)),
           _response(_judgment("late", 4))])


def test_run_scores_detection_and_false_positives(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_log(runs / "eval-1.jsonl", _rich_log())
    cases = audit_eval.build_cases(runs)
    results = audit_eval.run_cases(cases, client=_scripted_client())
    summary = audit_eval.summarize(results)
    assert summary["cases"] == 16
    assert summary["detection_rate"] == 1.0
    assert summary["false_positive_rate"] == 0.0
    assert summary["estimated_cost_usd"] > 0


def test_run_measures_weak_model(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_log(runs / "eval-1.jsonl", _rich_log())
    cases = audit_eval.build_cases(runs)
    lax = _FakeClient([_response(_judgment("justified", 7))])
    summary = audit_eval.summarize(audit_eval.run_cases(cases, client=lax))
    assert summary["detection_rate"] == 0.0
    assert summary["false_positive_rate"] == 0.0


def test_main_writes_markdown_report(tmp_path, monkeypatch, capsys):
    _isolated_llm(tmp_path, monkeypatch)
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_log(runs / "eval-1.jsonl", _rich_log())
    out = tmp_path / "AUDIT_EVAL.md"
    real_run_cases = audit_eval.run_cases
    monkeypatch.setattr(
        audit_eval, "run_cases",
        lambda cases, **kwargs: real_run_cases(
            cases, client=_scripted_client(), **kwargs))
    assert audit_eval.main(["--runs-dir", str(runs), "--audit-model", "auditor",
                            "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Model: `muse-wonder-1.0`." in text
    assert "| case | expected | actual | flagged |" in text
    assert "Detection rate (bad flagged): 1.0" in text
    assert "False positive rate (good flagged): 0.0" in text
    assert "Estimated cost: $" in text
    assert "SYNTHETIC" in text
    printed = capsys.readouterr().out
    assert "| case | expected | actual | flagged |" in printed
    assert printed.count("| yes |") == 10
    assert printed.count("| no |") == 6
