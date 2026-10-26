"""Model-driven blue team agent for validation exercises.

Calls Muse Spark once per turn through crucible.llm and runs the single
tool call the model returns. Findings and containment actions carry log
line ids as evidence. On any model failure or invalid tool call, falls
back to the scripted baseline move so an exercise never hangs.
"""

from __future__ import annotations

from crucible import llm
from crucible.agents import _common
from crucible.agents.scripted_blue import (  # noqa: F401 (compat re-export)
    ScriptedBlue,
    _evidence_from_history,
)

ACTOR = "blue"
ROLE = "blue"
MODEL_ID = "muse-spark-1.3"
MAX_TOKENS = _common.MAX_TOKENS

SYSTEM_PROMPT = (
    "You are the blue team in a defensive validation exercise inside an "
    "isolated sandbox. Objective: detect suspicious activity fast, call "
    "report_finding citing log line ids as evidence_refs as soon as logs "
    "show probing, then contain with isolate_host or patch_service before "
    "the secret is read. Logs only change when something happens, so "
    "repeating read_logs with no new lines is wasted; call wait when there "
    "is nothing new to check. Rules: call exactly one tool per turn, only "
    "target hosts web, db, and workstation, and put a one or two sentence "
    "reasoning summary in your reply text. When you report a finding or take "
    "a containment action (block_ip, isolate_host, patch_service, "
    "restore_host), cite log line ids such as web:3 in an evidence_refs "
    "argument and in your reasoning text. Your only tools are read_logs, "
    "list_processes, wait, block_ip, isolate_host, patch_service, "
    "restore_host, and report_finding."
)

ALLOWED_TOOLS = (
    "read_logs",
    "list_processes",
    "wait",
    "block_ip",
    "isolate_host",
    "patch_service",
    "restore_host",
    "report_finding",
)

EVIDENCE_TOOLS = ("report_finding", "block_ip", "isolate_host", "patch_service", "restore_host")


def _tool(name: str, description: str, properties: dict, required: list) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOL_SCHEMAS = [
    _tool(
        "read_logs",
        "Return recent log lines from one sandbox host with line ids.",
        {
            "host": {"type": "string"},
            "n": {"type": "integer", "description": "Lines to return, 1 to 200."},
        },
        ["host"],
    ),
    _tool(
        "list_processes",
        "List processes running inside one sandbox host.",
        {"host": {"type": "string"}},
        ["host"],
    ),
    _tool(
        "wait",
        "Do nothing for one turn when there is nothing new to check.",
        {"reason": {"type": "string"}},
        [],
    ),
    _tool(
        "block_ip",
        "Record a block for one IPv4 address.",
        {
            "ip": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
        ["ip"],
    ),
    _tool(
        "isolate_host",
        "Detach one sandbox host from the range network.",
        {
            "host": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
        ["host"],
    ),
    _tool(
        "patch_service",
        "Apply the toy fix for one service on one sandbox host.",
        {
            "host": {"type": "string"},
            "service": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
        ["host", "service"],
    ),
    _tool(
        "restore_host",
        "Bring one sandbox host back to a clean attached state.",
        {
            "host": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
        ["host"],
    ),
    _tool(
        "report_finding",
        "Record a detection with supporting log lines.",
        {
            "text": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
        ["text"],
    ),
]


def _build_messages(turn: int, history: list,
                    max_turns: int = _common.DEFAULT_MAX_TURNS) -> list:
    recent = _common.summarize_history(history)
    own = _common.summarize_own_history(history, ACTOR)
    remaining = max(0, int(max_turns) - int(turn) + 1)
    user_text = (
        f"This is turn {turn} of {max_turns}. "
        f"Turns remaining (including this one): {remaining}. "
        f"Call exactly one blue team tool for this turn. "
        f"Your previous moves:\n{own}\n"
        f"Recent exercise moves:\n{recent}\n"
        "Reply text must hold a one or two sentence reasoning summary. "
        "Cite log line ids when you report or contain."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]


def _evidence_for(name: str, args: dict, content: str, history: list) -> list:
    """Collect evidence refs for findings and containment actions."""
    refs = []
    claimed = args.get("evidence_refs", [])
    if isinstance(claimed, list):
        refs.extend([ref for ref in claimed if isinstance(ref, str)])
    refs.extend(_common.extract_evidence_ids(content))
    refs = [ref for ref in dict.fromkeys(refs) if ref]
    if name in EVIDENCE_TOOLS and not refs:
        refs = _evidence_from_history(history)
    return refs[:5]


class ModelBlue:
    """Blue team agent backed by one model call per turn."""

    actor = ACTOR

    def __init__(self, client=None, exercise_id: str | None = None,
                 max_tokens: int = MAX_TOKENS, temperature: float = 0.0,
                 reasoning_effort: str | None = None,
                 max_turns: int = _common.DEFAULT_MAX_TURNS):
        self._client = client
        self._exercise_id = exercise_id
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._max_turns = max_turns
        self._fallback = ScriptedBlue()

    def _fallback_action(self, turn: int, history: list, note: str) -> dict | None:
        action = self._fallback.next_action(turn, history)
        if action is None:
            return None
        reason = action.get("reasoning", "")
        action["reasoning"] = f"{note} Baseline move: {reason}".strip()
        return action

    def next_action(self, turn: int, history: list,
                    max_turns: int | None = None) -> dict | None:
        """Ask the model for one tool call, else use the baseline move."""
        total = max_turns if max_turns is not None else self._max_turns
        try:
            response = llm.chat(
                ROLE,
                _build_messages(turn, history, total),
                tools=TOOL_SCHEMAS,
                exercise_id=self._exercise_id,
                client=self._client,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                reasoning_effort=self._reasoning_effort,
            )
        except Exception as exc:
            return self._fallback_action(
                turn, history, f"Model call failed ({exc}), using baseline."
            )
        calls = (response or {}).get("tool_calls", []) or []
        if not calls:
            return self._fallback_action(
                turn, history, _common.fallback_note_for_empty_reply(response)
            )
        first = calls[0]
        name = first.get("name", "")
        if name not in ALLOWED_TOOLS:
            return self._fallback_action(
                turn, history, f"Model asked for unknown tool {name!r}, using baseline."
            )
        try:
            args = _common.parse_tool_args(first.get("arguments", {}))
        except ValueError as exc:
            return self._fallback_action(
                turn, history, f"Model gave bad arguments ({exc}), using baseline."
            )
        content = str((response or {}).get("content", ""))
        evidence = _evidence_for(name, args, content, history)
        args.pop("evidence_refs", None)
        reasoning = _common.clean_reasoning(content, f"Model chose {name}.")
        return {"tool": name, "args": args, "reasoning": reasoning, "evidence_refs": evidence}
