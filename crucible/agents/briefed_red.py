"""Briefed red team agent: model red with an engagement scope document.

Same model, tools, and turn flow as crucible.agents.red (which stays
unchanged), plus an engagement briefing: the in-scope dmz HTTP
surface, the known product endpoint list to enumerate, weak-pool
guidance for leaked accounts only, and post-auth discovery. The
briefing names every dmz host without singling out the entry, lists
every known endpoint without naming the live one, and gives no
accounts, passwords, or file paths. Path tracking extends the known
state so the model stops re-requesting paths.

Information boundary: prompts use only the red team's own moves,
exactly like the base profile; red never sees blue text.
"""

from __future__ import annotations

from crucible import llm, sandbox
from crucible.agents import _common, red
from crucible.scenarios import words

ACTOR = red.ACTOR
ROLE = red.ROLE
MODEL_ID = red.MODEL_ID
MAX_TOKENS = red.MAX_TOKENS
BRIEFED_MAX_TURNS = 60


def in_scope_hosts() -> tuple:
    """Return the in-scope dmz hosts from the blue-visible layout.

    Pure topology read: ids, roles, segments only. Never touches the
    spec's path, accounts, passwords, or page paths. Every dmz host
    answers HTTP (the generator re-segments anything else, and the
    classic dmz holds only the web host).
    """
    try:
        layout = sandbox.scenario_layout()
    except Exception:
        return ()
    return tuple(entry.get("id", "") for entry in layout.get("hosts", [])
                 if entry.get("segment") == "dmz" and entry.get("id"))


def engagement_briefing() -> str:
    """Return the engagement scope document for the active scenario."""
    scope = in_scope_hosts()
    if scope:
        scope_line = (
            "In-scope external surface: these dmz hosts answer HTTP: "
            + ", ".join(scope) + ". Probe these for entry; other hosts "
            "stay out of scope until a page hint or file names them, "
            "then enumerate the same endpoint list there."
        )
    else:
        scope_line = (
            "No dmz surface is listed; scan to find the hosts that "
            "answer HTTP, then enumerate the endpoint list below."
        )
    endpoints = ", ".join(words.ADMIN_PAGES)
    weak_pool = ", ".join(words.WEAK_PASSWORDS)
    return (
        "ENGAGEMENT BRIEFING (authoritative scope for this exercise). "
        + scope_line
        + " Enumeration: this product exposes admin endpoints from a "
        + "known list. Try each of these paths on a host before "
        + f"moving on: {endpoints}. Bare / and /status never leak "
        + "anything; do not spend turns there. Credentials: service "
        + "accounts use unguessable word names. Never guess admin or "
        + "root; only try accounts named by a hint or a file you "
        + "read. Against a leaked account only, try the weak pool: "
        + f"{weak_pool}. Post-auth discovery: after signing in, "
        + "locate files by listing: list_files / first, then /srv, "
        + "then the directory it names; always list a directory "
        + "before reading from it. No repeats: your known-state "
        + "lists probed paths; never request a host and path twice."
    )


def known_state_briefed(history: list) -> str:
    """Extend the red known state with probed host paths.

    Own http_request moves only, so the information boundary holds:
    no blue content can appear here.
    """
    probed: list = []
    for event in history or []:
        if event.get("actor") != ACTOR:
            continue
        if event.get("tool") != "http_request":
            continue
        args = event.get("args", {}) or {}
        host, path = args.get("host"), args.get("path")
        if not host or not path:
            continue
        entry = f"{host} {path}"
        if entry not in probed:
            probed.append(entry)
    base = _common.red_known_state(history)
    paths = ", ".join(probed) if probed else "none"
    return f"{base}; paths probed: {paths}"


def system_prompt() -> str:
    """Return the base red prompt plus the engagement briefing."""
    return red.system_prompt() + "\n\n" + engagement_briefing()


def tool_schemas() -> list:
    """Return the red tool schemas for the active scenario."""
    return red.tool_schemas()


def _build_messages(turn: int, history: list,
                    max_turns: int = BRIEFED_MAX_TURNS) -> list:
    recent = _common.summarize_own_recent(history, ACTOR)
    own = _common.summarize_own_history(history, ACTOR)
    last_result = _common.format_last_result(history, ACTOR)
    known = known_state_briefed(history)
    remaining = max(0, int(max_turns) - int(turn) + 1)
    user_text = (
        f"This is turn {turn} of {max_turns}. "
        f"Turns remaining (including this one): {remaining}. "
        f"Call exactly one red team tool for this turn. "
        f"What you already know: {known}\n"
        f"Your most recent tool result:\n{last_result}\n"
        f"Your previous moves:\n{own}\n"
        f"Your recent moves:\n{recent}\n"
        "Reply text must hold a one or two sentence reasoning summary."
    )
    return [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": user_text},
    ]


class ModelBriefedRed(red.ModelRed):
    """Model red with the engagement briefing and a 60-turn budget."""

    def __init__(self, client=None, exercise_id: str | None = None,
                 max_tokens: int = MAX_TOKENS, temperature: float | None = None,
                 reasoning_effort: str | None = None,
                 max_turns: int = BRIEFED_MAX_TURNS,
                 use_cache: bool = True, seed: str | None = None):
        super().__init__(client=client, exercise_id=exercise_id,
                         max_tokens=max_tokens, temperature=temperature,
                         reasoning_effort=reasoning_effort,
                         max_turns=max_turns, use_cache=use_cache, seed=seed)

    def next_action(self, turn: int, history: list,
                    max_turns: int | None = None) -> dict | None:
        """Ask the model for one tool call, else use the baseline move."""
        total = max_turns if max_turns is not None else self._max_turns
        try:
            response = llm.chat(
                ROLE,
                _build_messages(turn, history, total),
                tools=tool_schemas(),
                exercise_id=self._exercise_id,
                client=self._client,
                use_cache=self._use_cache,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                reasoning_effort=self._reasoning_effort,
                seed=self._seed,
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
        if name not in red.ALLOWED_TOOLS:
            return self._fallback_action(
                turn, history, f"Model asked for unknown tool {name!r}, using baseline."
            )
        try:
            args = _common.parse_tool_args(first.get("arguments", {}))
        except ValueError as exc:
            return self._fallback_action(
                turn, history, f"Model gave bad arguments ({exc}), using baseline."
            )
        if name == "try_credential":
            args = _common.normalize_credential_args(args)
            if not args.get("host") or not args.get("user") or not args.get("password"):
                return self._fallback_action(
                    turn, history, "Model gave an incomplete sign-in, using baseline."
                )
        reasoning = _common.clean_reasoning(
            (response or {}).get("content", ""), f"Model chose {name}."
        )
        return {"tool": name, "args": args, "reasoning": reasoning, "evidence_refs": []}
