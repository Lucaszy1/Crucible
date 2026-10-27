"""Hermetic tests for the referee and the blue playbook.

A fake client stands in for the model. The no-credentials test reads
its denied strings from scenario.json itself, so any planted secret
or file name is covered without hardcoding answers here.
"""

import json
from types import SimpleNamespace

from crucible import llm
from crucible.agents import referee
from crucible.agents.referee import (
    empty_playbook,
    load_playbook,
    parse_playbook,
    render_markdown,
    review_episode,
    rule_is_clean,
    save_playbook,
    scrub_rules,
)


def _response(content, model="muse-wonder-1.0"):
    message = SimpleNamespace(content=content, tool_calls=[])
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=usage)


class _FakeClient:
    def __init__(self, responses=None, error=None, errors=None):
        self.calls = []
        if responses is None:
            responses = []
        if not isinstance(responses, list):
            responses = [responses]
        self._responses = list(responses)
        self.error = error
        self._errors = list(errors or [])
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._errors:
            raise self._errors.pop(0)
        if self.error is not None:
            raise self.error
        if not self._responses:
            raise AssertionError("fake client ran out of responses")
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


def _isolated_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")


def _book(*rules):
    return {"episode": "camp-1",
            "rules": [{"rule": text, "episode": "camp-1"} for text in rules],
            "model_used": "muse-wonder-1.0"}


def _events():
    return [
        {"id": "evt-0001", "timestamp": "2026-01-01T00:00:00+00:00",
         "actor": "red", "tool": "scan_host", "args": {"host": "web"},
         "result": {"ok": True}, "reasoning_summary": "probe",
         "evidence_refs": []},
        {"id": "evt-0002", "timestamp": "2026-01-01T00:00:01+00:00",
         "actor": "blue", "tool": "read_logs", "args": {"host": "web"},
         "result": {"ok": True}, "reasoning_summary": "inspect",
         "evidence_refs": []},
    ]


def _score():
    return {"outcome": "blue_held", "blue_score": 90.0}


def test_merge_replaces_and_caps_at_ten(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    old = _book("Old vague rule about watching logs.")
    proposed = [{"rule": f"Fresh tactic number {number}.",
                 "episode": "camp-2"} for number in range(12)]
    client = _FakeClient(_response(json.dumps({"rules": proposed})))
    out = review_episode(_events(), _score(), "camp-2", old, client=client)
    assert out["model_used"] == "muse-wonder-1.0"
    assert len(out["playbook"]["rules"]) == 10
    assert out["playbook"]["rules"][0]["rule"] == "Fresh tactic number 0."
    assert out["playbook"]["episode"] == "camp-2"
    assert client.calls[0]["model"] == "muse-wonder-1.0"
    assert client.calls[0]["reasoning_effort"] == "medium"


def test_missing_episode_defaults_to_current(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    proposed = [{"rule": "Contain the host the evidence points at."}]
    client = _FakeClient(_response(json.dumps({"rules": proposed})))
    out = review_episode(_events(), _score(), "camp-3", empty_playbook(),
                         client=client)
    assert out["playbook"]["rules"] == [
        {"rule": "Contain the host the evidence points at.",
         "episode": "camp-3"}]


def test_no_credentials_or_file_names_from_scenario():
    from crucible import sandbox

    scenario = sandbox.load_scenario()
    shipped = scenario.get("shipped_defaults", {}) or {}
    secrets = []
    for entry in shipped.values():
        if not isinstance(entry, dict):
            continue
        secrets.extend(str(value) for value in entry.values())
    secrets.append(scenario["secret"]["path"])
    secrets.append(scenario["app_config"]["path"])
    assert len(secrets) >= 4
    for secret in secrets:
        assert rule_is_clean(
            f"Try the shipped value {secret} first.") is False, secret
        assert rule_is_clean(
            f"Always check {secret} before acting.") is False, secret
    assert rule_is_clean("Contain the host the evidence points at.") is True
    assert rule_is_clean("Check data host logs early for sign-in bursts.") is True
    assert rule_is_clean("") is False


def test_scrub_drops_dirty_long_and_empty_rules():
    from crucible import sandbox

    shipped = sandbox.load_scenario()["shipped_defaults"]
    first = next(entry for entry in shipped.values() if isinstance(entry, dict))
    password = first["pass"]
    kept, dropped = scrub_rules(
        [{"rule": "Contain the host the evidence points at.",
          "episode": "camp-1"},
         {"rule": f"Sign in with {password} to verify.", "episode": "camp-2"},
         {"rule": "x" * 500, "episode": "camp-2"},
         {"rule": "   ", "episode": "camp-2"},
         "not-a-dict"],
        "camp-2")
    assert kept == [{"rule": "Contain the host the evidence points at.",
                     "episode": "camp-1"}]
    assert dropped == 4


def test_bad_model_json_keeps_old_playbook(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    old = _book("Keep watching the data host.")
    client = _FakeClient(_response("not json at all"))
    out = review_episode(_events(), _score(), "camp-2", old, client=client)
    assert out["playbook"] == old
    assert out["estimated_cost_usd"] == 0.0
    assert parse_playbook("nope") is None
    assert parse_playbook('{"rules": "nope"}') is None


def test_model_unavailable_falls_back_to_spark(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    missing = RuntimeError("model muse-wonder-1.0 is unavailable here")
    proposed = [{"rule": "Contain the host the evidence points at.",
                 "episode": "camp-2"}]
    client = _FakeClient(_response(json.dumps({"rules": proposed})),
                         errors=[missing])
    out = review_episode(_events(), _score(), "camp-2", empty_playbook(),
                         client=client)
    assert len(client.calls) == 2
    assert client.calls[1]["model"] == "muse-spark-1.3"
    assert client.calls[1]["reasoning_effort"] == "high"
    assert len(out["playbook"]["rules"]) == 1


def test_spend_cap_keeps_playbook_unchanged(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "0.0001")
    llm.record_spend(model="muse-spark-1.3", input_tokens=100000,
                     output_tokens=0)
    old = _book("Keep watching the data host.")
    out = review_episode(_events(), _score(), "camp-2", old,
                         client=_FakeClient())
    assert out["playbook"] == old
    assert out["model_used"] == "none"


def test_save_and_load_roundtrip(tmp_path):
    book = {"episode": "camp-2",
            "rules": [{"rule": "Contain quickly.", "episode": "camp-1"},
                      {"rule": "Restore after quiet.", "episode": "camp-2"}],
            "model_used": "muse-wonder-1.0"}
    saved = save_playbook(book, tmp_path / "blue.json", tmp_path / "blue.md")
    assert saved["rules"] == book["rules"]
    assert load_playbook(tmp_path / "blue.json") == book
    text = (tmp_path / "blue.md").read_text(encoding="utf-8")
    assert "Contain quickly. (from camp-1)" in text
    assert "Restore after quiet. (from camp-2)" in text
    assert load_playbook(tmp_path / "missing.json") == empty_playbook()
    assert "No lessons yet." in render_markdown(empty_playbook())


def test_playbook_paths_live_at_repo_root():
    assert referee.PLAYBOOK_JSON == referee.ROOT / "playbooks" / "blue.json"
    assert referee.PLAYBOOK_MD == referee.ROOT / "playbooks" / "blue.md"
    assert (referee.ROOT / "crucible" / "agents").is_dir()


def test_whole_word_tier_spares_longer_words():
    assert rule_is_clean("Maintain monitoring of every host.",
                         denied=set(), names={"monitor"}) is True
    assert rule_is_clean("Isolate the monitor host at once.",
                         denied=set(), names={"monitor"}) is False
    assert rule_is_clean("Rotate the password vault.",
                         denied={"password"}, names=set()) is False


def test_classic_hostnames_rejected_whole_word():
    assert rule_is_clean("Isolate the app host at once.") is False
    assert rule_is_clean("Approve the application quickly.") is True


def test_gen_spec_secrets_and_names_rejected(monkeypatch):
    from crucible import sandbox
    from crucible.scenarios import generate

    spec = generate.generate(11, "easy")
    monkeypatch.setattr(sandbox, "_ACTIVE_SPEC", spec)
    host = spec["hosts"][0]["name"]
    assert rule_is_clean(f"Isolate {host} at once.") is False
    assert rule_is_clean(f"Watch the {host}s for lateral moves.") is True
    step = next(item for item in spec["path"]
                if item.get("kind") == "weak_creds")
    assert rule_is_clean(
        f"Guess {step['account']} credentials early.") is False
    assert rule_is_clean(
        f"Try {step['password']} on the gate.") is False
    jewel_base = spec["jewel"]["path"].rsplit("/", 1)[-1]
    assert rule_is_clean(f"Read {jewel_base} to verify.") is False
    assert rule_is_clean(
        "Maintain monitoring without isolation.") is True
    kept, dropped = scrub_rules(
        [{"rule": f"Isolate {host} at once.", "episode": "t-1"},
         {"rule": "Maintain monitoring without isolation.",
          "episode": "t-1"}],
        "t-1")
    assert [entry["rule"] for entry in kept] == [
        "Maintain monitoring without isolation."]
    assert dropped == 1
