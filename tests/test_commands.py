"""Table tests for the judge command parser (fully offline)."""

import json
from types import SimpleNamespace

import pytest

from crucible import commands


def _inventory():
    return {"hosts": {
        "web": {"role": "web portal"},
        "app": {"role": "app"},
        "db": {"role": "database"},
        "workstation": {"role": "workstation"},
    }, "crown_jewel_host": "db"}


def _services():
    return {"web": "webapp", "app": "reportapi", "db": "fakestore",
            "workstation": "desktop"}


PARSE_CASES = [
    # (transcript, expected tool, expected args)
    ("isolate the database", "isolate_host", {"host": "db"}),
    ("Isolate db!", "isolate_host", {"host": "db"}),
    ("please quarantine the web server", "isolate_host", {"host": "web"}),
    ("disconnect app from the network", "isolate_host", {"host": "app"}),
    ("cut off the workstation", "isolate_host", {"host": "workstation"}),
    ("take db offline", "isolate_host", {"host": "db"}),
    ("contain the app host", "isolate_host", {"host": "app"}),
    ("isolate the crown jewel", "isolate_host", {"host": "db"}),
    ("patch the web portal", "patch_service",
     {"host": "web", "service": "webapp"}),
    ("fix db", "patch_service", {"host": "db", "service": "fakestore"}),
    ("remediate the app", "patch_service",
     {"host": "app", "service": "reportapi"}),
    ("update the database service", "patch_service",
     {"host": "db", "service": "fakestore"}),
    ("restore app", "restore_host", {"host": "app"}),
    ("reconnect the database", "restore_host", {"host": "db"}),
    ("bring web back", "restore_host", {"host": "web"}),
    ("recover the workstation", "restore_host", {"host": "workstation"}),
    ("read the web logs", "read_logs", {"host": "web", "n": 50}),
    ("show me the last 100 lines on db", "read_logs",
     {"host": "db", "n": 100}),
    ("check app logs", "read_logs", {"host": "app", "n": 50}),
    ("inspect the database", "read_logs", {"host": "db", "n": 50}),
    ("what happened on web", "read_logs", {"host": "web", "n": 50}),
    ("read logs n=200 on workstation", "read_logs",
     {"host": "workstation", "n": 200}),
    ("status of the application", "list_processes", {"host": "app"}),
    ("log it: probing on web", "report_finding",
     {"text": "probing on web"}),
    ("check processes on db", "list_processes", {"host": "db"}),
    ("is the web portal healthy", "list_processes", {"host": "web"}),
    ("stand down", "wait", {"reason": "judge ordered a hold"}),
    ("hold position", "wait", {"reason": "judge ordered a hold"}),
    ("do nothing for now", "wait", {"reason": "judge ordered a hold"}),
    ("scan the app host", "scan_host", {"host": "app"}),
    ("sweep db for open ports", "scan_host", {"host": "db"}),
    ("rotate sign-in on app", "patch_service",
     {"host": "app", "service": "reportapi"}),
    ("rotate the secret on the database", "patch_service",
     {"host": "db", "service": "fakestore"}),
]


@pytest.mark.parametrize("text,tool,args", PARSE_CASES)
def test_parse_phrasings(text, tool, args):
    result = commands.parse(text, _inventory(), _services())
    assert result["status"] == "ok", result
    assert result["tool"] == tool
    assert result["args"] == args
    assert result["reasoning"]


def _gen_inventory():
    return {"hosts": {
        "quill": {"role": "web portal"},
        "willow": {"role": "app"},
        "wren": {"role": "workstation"},
        "flint": {"role": "database"},
        "harbor": {"role": "file share"},
        "gate": {"role": "jump host"},
    }, "crown_jewel_host": "flint"}


def _gen_services():
    return {"quill": "webapp", "willow": "reportapi", "wren": "desktop",
            "flint": "fakestore", "harbor": "fileshare", "gate": "bastion"}


# Transcription-tolerant phrasings: misheard verbs, dropped
# fillers, and role words against generated-style host names.
GEN_PARSE_CASES = [
    ("read blogs on willow", "read_logs", {"host": "willow", "n": 50}),
    ("read lugs on flint", "read_logs", {"host": "flint", "n": 50}),
    ("read logs willow", "read_logs", {"host": "willow", "n": 50}),
    ("read the logs on quill", "read_logs", {"host": "quill", "n": 50}),
    ("check logs on flint", "read_logs", {"host": "flint", "n": 50}),
    ("look at the willow logs", "read_logs", {"host": "willow", "n": 50}),
    ("view logs on wren", "read_logs", {"host": "wren", "n": 50}),
    ("read the block logs on quill", "read_logs", {"host": "quill", "n": 50}),
    ("i solate the database", "isolate_host", {"host": "flint"}),
    ("please isolate flint now", "isolate_host", {"host": "flint"}),
    ("skin the willow host", "scan_host", {"host": "willow"}),
    ("isolate the public site", "isolate_host", {"host": "quill"}),
    ("read logs on the reports server", "read_logs",
     {"host": "willow", "n": 50}),
    ("status of the file share", "list_processes", {"host": "harbor"}),
    ("scan the jump host", "scan_host", {"host": "gate"}),
    ("restore the desktop please", "restore_host", {"host": "wren"}),
    ("patch the data store", "patch_service",
     {"host": "flint", "service": "fakestore"}),
]


@pytest.mark.parametrize("text,tool,args", GEN_PARSE_CASES)
def test_parse_transcription_tolerant_phrasings(text, tool, args):
    result = commands.parse(text, _gen_inventory(), _gen_services())
    assert result["status"] == "ok", result
    assert result["tool"] == tool
    assert result["args"] == args


def _classic_live_inventory():
    return {"hosts": {
        "web": {"role": "public web front end"},
        "app": {"role": "internal reporting service"},
        "db": {"role": "internal data store"},
        "workstation": {"role": "analyst workstation"},
    }, "crown_jewel_host": "db"}


CLASSIC_ROLE_CASES = [
    ("isolate the database", "isolate_host", {"host": "db"}),
    ("isolate the web host", "isolate_host", {"host": "web"}),
    ("read logs on app", "read_logs", {"host": "app", "n": 50}),
    ("status of the desktop", "list_processes", {"host": "workstation"}),
    ("patch the public site", "patch_service",
     {"host": "web", "service": "webapp"}),
]


@pytest.mark.parametrize("text,tool,args", CLASSIC_ROLE_CASES)
def test_parse_live_sentence_roles(text, tool, args):
    result = commands.parse(text, _classic_live_inventory(), _services())
    assert result["status"] == "ok", result
    assert result["tool"] == tool
    assert result["args"] == args


def test_report_keeps_finding_text():
    result = commands.parse("report repeated sign-in failures on app",
                            _inventory(), _services())
    assert result["status"] == "ok"
    assert result["tool"] == "report_finding"
    assert "sign-in failures on app" in result["args"]["text"]


def test_report_without_text_asks():
    result = commands.parse("report", _inventory(), _services())
    assert result["status"] == "clarify"
    assert "finding" in result["question"]


def test_role_collision_asks_never_guesses():
    inventory = {"hosts": {
        "db": {"role": "database"},
        "db2": {"role": "database"},
    }, "crown_jewel_host": "db"}
    result = commands.parse("isolate the database", inventory, {})
    assert result["status"] == "clarify"
    assert result["options"] == ["db", "db2"]


def test_unknown_host_lists_known():
    result = commands.parse("isolate zephyr", _inventory(), _services())
    assert result["status"] == "clarify"
    assert "web" in result["options"]


def test_fuzzy_unique_match_names_host():
    result = commands.parse("read logs on quil", _gen_inventory(),
                            _gen_services())
    assert result["status"] == "ok", result
    assert result["args"] == {"host": "quill", "n": 50}
    assert result["heard"] == "Heard 'quil', using quill."


def test_fuzzy_phonetic_match():
    result = commands.parse("scan kwil", _gen_inventory(), _gen_services())
    assert result["status"] == "ok", result
    assert result["args"] == {"host": "quill"}


def test_fuzzy_ambiguous_hosts_ask():
    inventory = {"hosts": {
        "quill": {"role": "web portal"},
        "quilt": {"role": "app"},
    }, "crown_jewel_host": "quill"}
    result = commands.parse("isolate quil", inventory, {})
    assert result["status"] == "clarify"
    assert result["options"] == ["quill", "quilt"]
    assert "Which host?" in result["question"]


def test_fuzzy_never_leaves_current_hosts():
    result = commands.parse("read logs on zephyr", _gen_inventory(),
                            _gen_services())
    assert result["status"] == "clarify"
    assert result["options"] == ["flint", "gate", "harbor", "quill",
                                 "willow", "wren"]
    assert "zephyr" not in result["question"] or "I heard" in result["question"]


def test_clarify_lists_hosts_and_real_examples():
    result = commands.parse("isolate zephyr", _gen_inventory(),
                            _gen_services())
    assert result["status"] == "clarify"
    assert "Known hosts:" in result["question"]
    assert "quill" in result["question"]
    assert "Try:" in result["question"]
    assert "read logs on" in result["question"]


def test_refused_names_hosts_and_real_examples():
    result = commands.parse("frobnicate the database", _gen_inventory(),
                            _gen_services())
    assert result["status"] == "refused"
    assert "willow" in result["reason"]
    assert "Try:" in result["reason"]
    assert "zephyr" not in result["reason"]


def test_example_orders_use_real_hosts_only():
    assert commands.example_orders(_gen_inventory()) == [
        "read logs on flint",
        "scan the gate host",
        "isolate the harbor host",
    ]
    assert commands.example_orders({"hosts": {}}) == []


def test_missing_host_lists_known():
    result = commands.parse("isolate", _inventory(), _services())
    assert result["status"] == "clarify"
    assert result["options"] == ["app", "db", "web", "workstation"]


def test_missing_service_asks():
    result = commands.parse("patch db", _inventory(), {})
    assert result["status"] == "clarify"
    assert "db" in result["question"]


def test_exact_hostname_beats_role_word():
    inventory = {"hosts": {
        "web": {"role": "app"},
        "app": {"role": "app"},
    }, "crown_jewel_host": "app"}
    result = commands.parse("isolate web", inventory, {})
    assert result["status"] == "ok"
    assert result["args"] == {"host": "web"}


REFUSALS = [
    "shut down the whole range",
    "delete the logs on db",
    "run nmap against web",
    "stop the exercise",
    "end the exercise now",
    "what is the capital of France",
    "hello there",
    "",
    "block 10.0.0.5",
]


@pytest.mark.parametrize("text", REFUSALS)
def test_refusals_stay_plain(text):
    result = commands.parse(text, _inventory(), _services())
    assert result["status"] == "refused", result
    assert result["reason"]
    assert result["examples"]


def test_validator_rejects_unknown_tool():
    result = commands.validate_action({"tool": "block_ip",
                                       "args": {"ip": "10.0.0.1"}},
                                      _inventory(), _services())
    assert result["status"] == "refused"


def test_validator_rejects_unknown_host():
    result = commands.validate_action({"tool": "isolate_host",
                                       "args": {"host": "zephyr"}},
                                      _inventory(), _services())
    assert result["status"] == "clarify"


def test_validator_ignores_model_service():
    result = commands.validate_action(
        {"tool": "patch_service",
         "args": {"host": "db", "service": "wrong-name"}},
        _inventory(), _services())
    assert result["status"] == "ok"
    assert result["args"] == {"host": "db", "service": "fakestore"}


def test_hand_back_detection():
    assert commands.is_hand_back("hand back") is True
    assert commands.is_hand_back("Thanks, resume AI control") is True
    assert commands.is_hand_back("isolate the database") is False


def test_take_over_detection():
    assert commands.is_take_over("take over") is True
    assert commands.is_take_over("pause the ai, my turn") is True
    assert commands.is_take_over("take db offline") is False
    assert commands.is_take_over("isolate the database") is False


def test_rotate_runs_patch_and_says_so():
    result = commands.parse("rotate sign-in on app",
                            _inventory(), _services())
    assert result["status"] == "ok"
    assert result["tool"] == "patch_service"
    assert "rotation ships with the patch" in result["reasoning"]


def test_scan_needs_a_known_host():
    result = commands.parse("scan the moon", _inventory(), _services())
    assert result["status"] == "clarify"
    assert "web" in result["question"]


def _fake_client(content):
    message = SimpleNamespace(content=content, tool_calls=[])
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    response = SimpleNamespace(choices=[choice], usage=usage)
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: response)))


def test_model_fallback_validates(monkeypatch, tmp_path):
    from crucible import llm

    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    body = json.dumps({"tool": "isolate_host", "args": {"host": "db"},
                       "reasoning": "judge order"})
    result = commands.parse_with_model("cut the data host off",
                                       _inventory(), _services(),
                                       client=_fake_client(body))
    assert result["status"] == "ok"
    assert result["args"] == {"host": "db"}
    assert result["cost_usd"] >= 0.0


def test_model_fallback_bad_json_refuses(monkeypatch, tmp_path):
    from crucible import llm

    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    result = commands.parse_with_model("do something odd", _inventory(),
                                       _services(),
                                       client=_fake_client("not json"))
    assert result["status"] == "refused"


def test_model_fallback_failure_names_hosts(monkeypatch, tmp_path):
    from crucible import llm

    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    result = commands.parse_with_model("do something odd", _gen_inventory(),
                                       _gen_services(),
                                       client=_fake_client("not json"))
    assert result["status"] == "refused"
    assert "example orders" not in result["reason"]
    assert "Known hosts:" in result["reason"]
    assert "quill" in result["reason"]
    assert "Try:" in result["reason"]


def test_model_fallback_bad_host_clarifies(monkeypatch, tmp_path):
    from crucible import llm

    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    body = json.dumps({"tool": "isolate_host",
                       "args": {"host": "invented-host"}})
    result = commands.parse_with_model("isolate it", _inventory(),
                                       _services(),
                                       client=_fake_client(body))
    assert result["status"] == "clarify"
