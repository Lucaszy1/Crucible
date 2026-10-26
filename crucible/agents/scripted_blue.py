"""Scripted blue team agent for baseline exercises.

Follows a fixed response sequence: inspect logs, record a finding with
log line references, then contain and fix the affected hosts. Uses only
the shared tools module and makes zero model calls. Used by tests and
as the fallback when a model turn fails.
"""

from __future__ import annotations

from crucible import sandbox

ACTOR = "blue"


def _scenario_services() -> dict:
    try:
        scenario = sandbox.load_scenario()
    except Exception:
        scenario = {}
    services = scenario.get("services", {}) or {}
    secret = scenario.get("secret", {}) or {}
    return {
        "secret_host": secret.get("host", "db"),
        "web_service": (services.get("web", {}) or {}).get("service", "webapp"),
        "db_service": (services.get("db", {}) or {}).get("service", "fakestore"),
    }


def _evidence_from_history(history: list) -> list:
    """Collect up to three log line ids from the last blue log read."""
    for event in reversed(history or []):
        if event.get("actor") != ACTOR or event.get("tool") != "read_logs":
            continue
        lines = (event.get("result", {}) or {}).get("lines", [])
        refs = []
        for entry in lines:
            text = entry.get("line", "")
            if "request from=" in text or "/admin" in text:
                refs.append(entry.get("id"))
        refs = [ref for ref in refs if ref]
        if refs:
            return refs[:3]
        return [entry.get("id") for entry in lines[:2] if entry.get("id")]
    return []


class ScriptedBlue:
    """Fixed blue team plan: inspect, report, contain, then fix."""

    actor = ACTOR

    def __init__(self):
        self._step = 0
        self._info = _scenario_services()

    def next_action(self, turn: int, history: list) -> dict | None:
        """Return the next planned tool call, or None when finished."""
        step = self._step
        self._step += 1
        if step == 0:
            return {
                "tool": "read_logs",
                "args": {"host": "web", "n": 50},
                "reasoning": "Inspect recent web host logs for odd requests.",
                "evidence_refs": [],
            }
        if step == 1:
            return {
                "tool": "report_finding",
                "args": {"text": "Odd probing seen in web logs, likely admin page checks."},
                "reasoning": "Record the detection with supporting log lines.",
                "evidence_refs": _evidence_from_history(history),
            }
        if step == 2:
            return {
                "tool": "isolate_host",
                "args": {"host": self._info["secret_host"]},
                "reasoning": "Detach the data host to stop further access.",
                "evidence_refs": [],
            }
        if step == 3:
            return {
                "tool": "patch_service",
                "args": {"host": self._info["secret_host"], "service": self._info["db_service"]},
                "reasoning": "Apply the toy fix so the default sign-in stops working.",
                "evidence_refs": [],
            }
        if step == 4:
            return {
                "tool": "patch_service",
                "args": {"host": "web", "service": self._info["web_service"]},
                "reasoning": "Close the exposed admin page on the web host.",
                "evidence_refs": [],
            }
        return None
