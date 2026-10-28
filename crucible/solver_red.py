"""Deterministic solver red for defense evaluation.

Follows the proven intrusion path from the scenario generator (the
same chain the simulator validates), with seeded imperfection: a
short random start delay, decoy probes, and occasional wrong guesses.
The same seed always yields the same move list, so benchmark arms
stay matched; different seeds probe different decoys, so the action
logs differ across episodes. Makes zero model calls.

The opening chain moves stay front-loaded: neither the start delay
nor any decoy ever pushes the first sign-in past red move 3, so the
intrusion lands even against a fast isolating blue. Later chain
moves retry a few times when they visibly fail (an isolated host
refuses the tool call) instead of stalling forever.
"""

from __future__ import annotations

import random

from crucible import sandbox
from crucible.scenarios import chain as chain_mod
from crucible.scenarios import words

# Retries per chain move when the last attempt visibly failed
# (refused, error, or no authentication). Decoys and deliberate
# wrong guesses never retry; after the cap the solver advances
# rather than burning the whole episode on one step.
MAX_CHAIN_RETRIES = 3

# Start-delay ceiling in red moves. The actual delay is further
# capped per spec so the first sign-in still lands by red move 3,
# which runs before a fast blue's third move can isolate the target.
MAX_START_DELAY = 1

# Decoy count range, mirroring the noisy profile.
MIN_DECOYS = 2
MAX_DECOYS = 5

# Chance of one deliberate wrong guess ahead of a credential step.
WRONG_GUESS_CHANCE = 0.4


def _real_triples(spec: dict) -> set:
    """Every live (host, user, password) triple, for decoy avoidance."""
    triples = set()
    for step in spec.get("path", []):
        if step.get("kind") in ("weak_creds", "stale_reuse",
                                "signin_known"):
            triples.add((step.get("host"), step.get("account"),
                         step.get("password")))
    for end in spec.get("dead_ends", []):
        triples.update({
            (end.get("host"), end.get("account"), end.get("password")),
            (end.get("terminal_host"), end.get("terminal_account"),
             end.get("password")),
        })
    return triples


def _wrong_password(rng: random.Random, host: str, user: str,
                    real: set) -> str:
    """Pick a password that cannot authenticate as user on host."""
    pool = [password for password in words.WEAK_PASSWORDS
            if (host, user, password) not in real]
    if pool:
        return rng.choice(pool)
    return "wrongpw"


def _decoy(rng: random.Random, spec: dict, real: set) -> dict:
    """Return one seeded decoy move (never part of the chain)."""
    hosts = [host["name"] for host in spec["hosts"]]
    kind = rng.choice(("scan", "probe", "guess", "idle"))
    if kind == "scan":
        return {"tool": "scan_host",
                "args": {"host": rng.choice(hosts)},
                "reasoning": "Decoy scan of one host.",
                "evidence_refs": [], "class": "decoy"}
    if kind == "probe":
        pool = ([entry["path"] for entry in
                 spec.get("decoys", {}).get("admin_pages", [])]
                + list(words.BENIGN_PAGES))
        return {"tool": "http_request",
                "args": {"host": spec["entry_host"],
                         "path": rng.choice(pool)},
                "reasoning": "Decoy page probe.",
                "evidence_refs": [], "class": "decoy"}
    if kind == "guess":
        host = rng.choice(hosts)
        user = rng.choice(words.NOISE_USERS)
        return {"tool": "try_credential",
                "args": {"host": host, "user": user,
                         "password": _wrong_password(rng, host, user,
                                                     real)},
                "reasoning": "Decoy sign-in guess.",
                "evidence_refs": [], "class": "decoy"}
    return {"tool": "report_finding",
            "args": {"text": "Continuing probes, nothing conclusive yet."},
            "reasoning": "Idle note while probing.",
            "evidence_refs": [], "class": "decoy"}


def solver_plan(spec: dict, seed: str) -> list:
    """Return the seeded solver move list for one spec.

    Chain moves keep their relative order; the start delay comes
    first and decoys land only after the first sign-in, so the
    intrusion always lands by red move 3. The delay itself is
    capped by the slack ahead of that sign-in (specs with two
    opening page probes get no delay). Deliberate wrong guesses sit
    directly ahead of the credential move they shadow, except the
    first sign-in, which is never shadowed.
    """
    rng = random.Random(f"{spec['scenario_id']}:{seed}")
    real = _real_triples(spec)
    chain = chain_mod.chain_plan(spec)
    first_cred = next(index for index, move in enumerate(chain)
                      if move["tool"] == "try_credential")
    slack = max(0, 2 - first_cred)
    plan: list = []
    for _ in range(rng.randint(0, min(MAX_START_DELAY, slack))):
        plan.append({"tool": "report_finding",
                     "args": {"text": "Starting recon sweep."},
                     "reasoning": "Idle note before probing.",
                     "evidence_refs": [], "class": "delay"})
    count = rng.randint(MIN_DECOYS, MAX_DECOYS)
    slots = sorted(rng.sample(range(first_cred + 1, len(chain)),
                              min(count, max(0, len(chain)
                                             - first_cred - 1))))
    decoys: dict = {}
    for slot in slots:
        decoys.setdefault(slot, []).append(_decoy(rng, spec, real))
    seen_cred = False
    for index, move in enumerate(chain):
        plan.extend(decoys.get(index, []))
        if move["tool"] == "try_credential" and seen_cred and \
                rng.random() < WRONG_GUESS_CHANCE:
            args = move["args"]
            plan.append({
                "tool": "try_credential",
                "args": {"host": args["host"], "user": args["user"],
                         "password": _wrong_password(rng, args["host"],
                                                     args["user"], real)},
                "reasoning": "Wrong guess before the real sign-in.",
                "evidence_refs": [], "class": "wrong"})
        if move["tool"] == "try_credential":
            seen_cred = True
        plan.append({"tool": move["tool"], "args": dict(move["args"]),
                     "reasoning": move["reasoning"], "evidence_refs": [],
                     "class": "chain"})
    return plan


def _chain_failed(move: dict, result: dict) -> bool:
    """Check whether a chain move visibly failed and deserves a retry."""
    result = result or {}
    if move["tool"] == "try_credential":
        return result.get("authenticated") is not True
    return result.get("ok") is not True


def _public(move: dict) -> dict:
    """Strip the planner's private class tag from one move."""
    return {"tool": move["tool"], "args": dict(move["args"]),
            "reasoning": move["reasoning"], "evidence_refs": []}


class SolverRed:
    """Seeded solver red: proven path plus imperfection, no models."""

    actor = "red"

    def __init__(self, seed: str, spec=None):
        if spec is None:
            spec = sandbox.active_spec()
        if spec is None:
            raise ValueError("solver red needs a generated scenario "
                             "(no active spec)")
        self._seed = seed
        self._plan = solver_plan(spec, seed)
        self._step = 0
        self._retries = 0

    @property
    def plan(self) -> list:
        """Return the full planned move list (for tests)."""
        return [dict(move) for move in self._plan]

    def next_action(self, turn: int, history: list) -> dict | None:
        """Return the next planned move, retrying failed chain moves."""
        del turn
        if self._step >= len(self._plan):
            return None
        if self._step > 0:
            last = self._plan[self._step - 1]
            if last["class"] == "chain" and \
                    self._retries < MAX_CHAIN_RETRIES:
                own = [event for event in (history or [])
                       if event.get("actor") == self.actor]
                if own:
                    event = own[-1]
                    if event.get("tool") == last["tool"] and \
                            (event.get("args") or {}) == last["args"]:
                        if _chain_failed(last,
                                         event.get("result", {}) or {}):
                            self._retries += 1
                            return _public(last)
        move = self._plan[self._step]
        self._step += 1
        self._retries = 0
        return _public(move)
