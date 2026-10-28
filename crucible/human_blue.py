"""Human blue: judge orders executed as defender turns.

A judge speaks or types orders ("isolate the database"); each order is
parsed by crucible.commands and executed as one blue tool action with
the same scoring, auditor, and logs as AI turns, logged under actor
"human" so assisted runs stay identifiable. Human turns wait on a
command queue with a turn clock; no command means hold.

Two agent types:

- HumanBlue ("human"): every turn waits for an order, else holds.
- AIWithOverride ("ai_with_override"): an inner AI blue plays until a
  human order arrives; the order interrupts the AI's next turn and
  latches human control until "hand back" returns control to the AI.

Every executed human action carries _human_source ("voice" or "text")
plus _human_transcript; the run loop records those as event fields.
The scripted demo path feeds a fixed transcript list offline, so a
demo never depends on network speech recognition.
"""

from __future__ import annotations

import inspect
import queue
import threading
import time

from crucible import commands

TURN_TIMEOUT_S = 30.0
HOLD_REASON = "holding for orders"

DEFAULT_DEMO_TRANSCRIPTS = (
    "read the web logs",
    "report probing on the web host",
    "isolate the database",
    "stand down",
)


def default_inventory() -> dict:
    """Read the live asset inventory (roles, crown jewel host)."""
    from crucible import sandbox

    try:
        return sandbox.blue_inventory()
    except Exception:
        return {"hosts": {}, "crown_jewel_host": None}


def default_services() -> dict:
    """Read the live host-to-service map used for patch orders."""
    from crucible import sandbox

    try:
        scenario = sandbox.load_scenario()
    except Exception:
        return {}
    services = scenario.get("services", {}) or {}
    return {host: (entry or {}).get("service", "")
            for host, entry in services.items()}


class CommandQueue:
    """Thread-safe judge order queue with a last-result slot."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._last: dict | None = None
        self._counter = 0

    def push(self, text: str, source: str = "text") -> dict:
        """Queue one order; returns its receipt."""
        cleaned = str(text or "").strip()
        if not cleaned:
            raise ValueError("cannot queue an empty order")
        if source not in ("voice", "text"):
            raise ValueError(f"source must be 'voice' or 'text': {source!r}")
        with self._lock:
            self._counter += 1
            receipt = {"id": self._counter, "text": cleaned,
                       "source": source, "received_at": time.time()}
        self._queue.put(receipt)
        return dict(receipt)

    def pop(self, timeout: float | None = None) -> dict | None:
        """Take the next order, waiting up to timeout seconds."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def pending(self) -> int:
        """Count orders still waiting."""
        return self._queue.qsize()

    def note_result(self, result: dict) -> None:
        """Record the outcome of the latest order."""
        with self._lock:
            self._last = dict(result)

    def last_result(self) -> dict | None:
        """Return the latest order outcome, if any."""
        with self._lock:
            return dict(self._last) if self._last is not None else None


def _hold_action(reason: str = HOLD_REASON) -> dict:
    return {"tool": "wait", "args": {"reason": reason},
            "reasoning": f"Blue holds: {reason}.", "evidence_refs": []}


def _human_action(parsed: dict, receipt: dict) -> dict:
    action = {"tool": parsed["tool"], "args": dict(parsed["args"]),
              "reasoning": parsed["reasoning"], "evidence_refs": [],
              "_actor": "human",
              "_human_source": receipt["source"],
              "_human_transcript": receipt["text"]}
    return action


class HumanBlue:
    """Blue agent driven entirely by judge orders."""

    actor = "human"

    def __init__(self, cmd_queue: CommandQueue, *,
                 inventory: dict | None = None,
                 services: dict | None = None,
                 inventory_provider=default_inventory,
                 services_provider=default_services,
                 turn_timeout_s: float = TURN_TIMEOUT_S,
                 model_fallback: bool = False,
                 exercise_id: str | None = None,
                 script: list | tuple | None = None):
        self._queue = cmd_queue
        self._inventory = inventory
        self._services = services
        self._inventory_provider = inventory_provider
        self._services_provider = services_provider
        self._timeout = turn_timeout_s
        self._model_fallback = model_fallback
        self._exercise_id = exercise_id
        self._script = list(script) if script is not None else None

    def _context(self) -> tuple:
        inventory = self._inventory
        if inventory is None:
            inventory = self._inventory_provider()
        services = self._services
        if services is None:
            services = self._services_provider()
        return inventory, services

    def _next_receipt(self) -> dict | None:
        if self._script is not None:
            if not self._script:
                return None
            text = self._script.pop(0)
            return {"id": 0, "text": str(text), "source": "text",
                    "received_at": time.time(), "scripted": True}
        return self._queue.pop(timeout=self._timeout)

    def _run_order(self, receipt: dict) -> dict:
        inventory, services = self._context()
        parsed = commands.parse(receipt["text"], inventory, services)
        if parsed["status"] != "ok" and self._model_fallback:
            parsed = commands.parse_with_model(
                receipt["text"], inventory, services,
                exercise_id=self._exercise_id)
        if parsed["status"] == "ok":
            action = _human_action(parsed, receipt)
            self._queue.note_result(
                {"id": receipt["id"], "text": receipt["text"],
                 "source": receipt["source"], "status": "ok",
                 "tool": action["tool"], "args": action["args"]})
            return action
        if parsed["status"] == "clarify":
            question = parsed.get("question", "Which host?")
        else:
            question = parsed.get("reason", "Order refused.")
        action = _hold_action(question)
        action["_actor"] = "human"
        action["_human_source"] = receipt["source"]
        action["_human_transcript"] = receipt["text"]
        self._queue.note_result(
            {"id": receipt["id"], "text": receipt["text"],
             "source": receipt["source"], "status": parsed["status"],
             "message": question})
        return action

    def next_action(self, turn: int, history: list) -> dict | None:
        """Wait for one order and run it; hold when none arrives."""
        del turn, history
        receipt = self._next_receipt()
        if receipt is None:
            if self._script is not None:
                return None
            return _hold_action()
        return self._run_order(receipt)


class AIWithOverride:
    """AI blue that yields to judge orders until hand-back.

    The inner agent plays every turn with nothing queued. A queued
    order interrupts the AI's next turn and latches human control;
    later turns wait on the queue (holding when empty) until "hand
    back" returns control to the AI.
    """

    actor = "blue"

    def __init__(self, inner_blue, cmd_queue: CommandQueue, *,
                 inventory: dict | None = None,
                 services: dict | None = None,
                 inventory_provider=default_inventory,
                 services_provider=default_services,
                 turn_timeout_s: float = TURN_TIMEOUT_S,
                 model_fallback: bool = False,
                 exercise_id: str | None = None):
        self._inner = inner_blue
        self._queue = cmd_queue
        self._human = HumanBlue(
            cmd_queue, inventory=inventory, services=services,
            inventory_provider=inventory_provider,
            services_provider=services_provider,
            turn_timeout_s=turn_timeout_s,
            model_fallback=model_fallback, exercise_id=exercise_id)
        self._timeout = turn_timeout_s
        self._latched = False

    @property
    def latched(self) -> bool:
        """Whether human control currently holds the agent."""
        return self._latched

    def _delegate(self, turn: int, history: list,
                  max_turns=None, alerts=None):
        try:
            params = inspect.signature(
                self._inner.next_action).parameters
        except (TypeError, ValueError):
            return self._inner.next_action(turn, history)
        kwargs: dict = {}
        if "max_turns" in params:
            kwargs["max_turns"] = max_turns
        if "alerts" in params:
            kwargs["alerts"] = alerts
        if kwargs:
            return self._inner.next_action(turn, history, **kwargs)
        return self._inner.next_action(turn, history)

    def next_action(self, turn: int, history: list,
                    max_turns=None, alerts=None) -> dict | None:
        """Play the AI turn, or the queued human order when present."""
        wait = self._timeout if self._latched else 0
        receipt = self._queue.pop(timeout=wait)
        if receipt is None:
            if self._latched:
                held = _hold_action()
                held["_actor"] = "human"
                return held
            return self._delegate(turn, history, max_turns, alerts)
        if commands.is_hand_back(receipt["text"]):
            self._latched = False
            self._queue.note_result(
                {"id": receipt["id"], "text": receipt["text"],
                 "source": receipt["source"], "status": "handed-back"})
            return self._delegate(turn, history, max_turns, alerts)
        if commands.is_take_over(receipt["text"]):
            self._latched = True
            self._queue.note_result(
                {"id": receipt["id"], "text": receipt["text"],
                 "source": receipt["source"], "status": "taken-over"})
            return None
        self._latched = True
        return self._human._run_order(receipt)
