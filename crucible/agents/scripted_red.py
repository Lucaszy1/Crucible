"""Scripted red team agent for baseline exercises.

Follows a fixed probe sequence against the toy scenario using only the
shared tools module. Makes zero model calls, so exercises stay fully
deterministic and free to run. Used by tests and as the fallback when a
model turn fails.
"""

from __future__ import annotations

from crucible import sandbox

ACTOR = "red"


def _scenario_defaults() -> dict:
    try:
        scenario = sandbox.load_scenario()
    except Exception:
        scenario = {}
    shipped = scenario.get("shipped_defaults", {}) or {}
    app = shipped.get("app", {}) or {}
    db = shipped.get("db", {}) or {}
    secret = scenario.get("secret", {}) or {}
    admin = scenario.get("admin_endpoint", {}) or {}
    app_config = scenario.get("app_config", {}) or {}
    return {
        "app_host": app_config.get("host", "app"),
        "app_config_path": app_config.get("path", "/srv/app/config.ini"),
        "app_user": app.get("user", "svc-report"),
        "app_password": app.get("pass", "password"),
        "db_user": db.get("user", "dbops"),
        "db_password": db.get("pass", "dbops-fake-19"),
        "secret_host": secret.get("host", "db"),
        "secret_path": secret.get("path", "/secret/exercise_secret.txt"),
        "admin_host": admin.get("host", "web"),
        "admin_path": admin.get("path", "/admin"),
    }


class ScriptedRed:
    """Fixed red team plan: probe, sign in twice, read the exercise secret."""

    actor = ACTOR

    def __init__(self):
        self._step = 0
        self._defaults = _scenario_defaults()

    def next_action(self, turn: int, history: list) -> dict | None:
        """Return the next planned tool call, or None when finished."""
        del turn, history
        plan = [
            {
                "tool": "scan_host",
                "args": {"host": "web"},
                "reasoning": "Check whether the web host is reachable.",
                "evidence_refs": [],
            },
            {
                "tool": "http_request",
                "args": {
                    "host": self._defaults["admin_host"],
                    "path": self._defaults["admin_path"],
                },
                "reasoning": "Look at the exposed admin page noted in the scenario.",
                "evidence_refs": [],
            },
            {
                "tool": "try_credential",
                "args": {
                    "host": self._defaults["app_host"],
                    "user": self._defaults["app_user"],
                    "password": self._defaults["app_password"],
                },
                "reasoning": "Try the shipped default sign-in on the app host.",
                "evidence_refs": [],
            },
            {
                "tool": "read_file",
                "args": {
                    "host": self._defaults["app_host"],
                    "path": self._defaults["app_config_path"],
                },
                "reasoning": "Read the app config for the next sign-in.",
                "evidence_refs": [],
            },
            {
                "tool": "try_credential",
                "args": {
                    "host": "db",
                    "user": self._defaults["db_user"],
                    "password": self._defaults["db_password"],
                },
                "reasoning": "Try the config sign-in on the data host.",
                "evidence_refs": [],
            },
            {
                "tool": "list_files",
                "args": {
                    "host": self._defaults["secret_host"],
                    "path": "/secret/",
                },
                "reasoning": "List the secret directory before reading.",
                "evidence_refs": [],
            },
            {
                "tool": "read_file",
                "args": {
                    "host": self._defaults["secret_host"],
                    "path": self._defaults["secret_path"],
                },
                "reasoning": "Read the exercise secret after signing in.",
                "evidence_refs": [],
            },
            {
                "tool": "report_finding",
                "args": {"text": "Exercise secret obtained from the data host."},
                "reasoning": "Record what the probe sequence obtained.",
                "evidence_refs": [],
            },
        ]
        if self._step >= len(plan):
            return None
        action = plan[self._step]
        self._step += 1
        return action
