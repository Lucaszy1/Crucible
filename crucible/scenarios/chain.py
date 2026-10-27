"""Scripted chain red derived from a scenario spec, plus noise.

chain_steps(spec) turns the spec path into ordered steps of tool
calls, mirroring the classic chain profile move for move (probe the
page, sign in, list then read, sign in, list then read the jewel).
noisy_plan(spec, seed) interleaves seeded decoy probes (scans, page
probes, wrong guesses that can never match real credentials, idle
notes), mirroring the classic noisy profile. The simulator executes
both; Stage 3 runs them against Docker.
"""

from __future__ import annotations

import random

from crucible.scenarios import words


def chain_steps(spec: dict) -> list:
    """Derive ordered chain steps (kind, host, actions) from a spec."""
    steps = []
    for step in spec.get("path", []):
        kind, host = step["kind"], step["host"]
        if kind == "exposed_page":
            actions = [{
                "tool": "http_request",
                "args": {"host": host, "path": step["page_path"]},
                "reasoning": "Probe the exposed admin page for a lead.",
            }]
        elif kind in ("weak_creds", "stale_reuse", "signin_known"):
            actions = [{
                "tool": "try_credential",
                "args": {"host": host, "user": step["account"],
                         "password": step["password"]},
                "reasoning": "Sign in with the known credential.",
            }]
        elif kind in ("config_creds", "backup_creds"):
            actions = [
                {"tool": "list_files",
                 "args": {"host": host, "path": step["dir"]},
                 "reasoning": "List the directory before reading."},
                {"tool": "read_file",
                 "args": {"host": host, "path": step["path"]},
                 "reasoning": "Read the file for the next credential."},
            ]
        elif kind == "jewel_read":
            actions = [
                {"tool": "list_files",
                 "args": {"host": host, "path": step["dir"]},
                 "reasoning": "List the protected directory before reading."},
                {"tool": "read_file",
                 "args": {"host": host, "path": step["path"]},
                 "reasoning": "Read the protected file after signing in."},
            ]
        else:
            raise ValueError(f"unknown step kind: {kind!r}")
        steps.append({"kind": kind, "host": host, "actions": actions})
    return steps


def chain_plan(spec: dict) -> list:
    """Flatten chain steps into one ordered move list."""
    plan = []
    for step in chain_steps(spec):
        for action in step["actions"]:
            plan.append({"tool": action["tool"],
                         "args": dict(action["args"]),
                         "reasoning": action["reasoning"],
                         "evidence_refs": []})
    return plan


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


def noisy_plan(spec: dict, seed: str) -> list:
    """Return the chain with seeded decoy probes mixed in.

    Same seed always yields the same move list. Decoys are scans,
    benign page probes, wrong guesses that can never match a live
    credential, and idle notes, inserted at seeded positions ahead
    of chain steps. Chain steps keep their relative order.
    """
    rng = random.Random(f"{spec['scenario_id']}:{seed}")
    real = _real_triples(spec)
    hosts = [host["name"] for host in spec["hosts"]]
    http_hosts = [name for name in hosts
                  if name == spec["entry_host"]]
    decoy_pages = [entry["path"] for entry in
                   spec.get("decoys", {}).get("admin_pages", [])]
    benign = list(words.BENIGN_PAGES)
    chain = chain_plan(spec)
    count = rng.randint(2, 5)
    slots = sorted(rng.sample(range(len(chain)),
                              min(count, len(chain))))
    decoys: dict = {}
    for slot in slots:
        kind = rng.choice(("scan", "probe", "guess", "idle"))
        if kind == "scan":
            decoys.setdefault(slot, []).append({
                "tool": "scan_host",
                "args": {"host": rng.choice(hosts)},
                "reasoning": "Decoy scan of one host.",
                "evidence_refs": []})
        elif kind == "probe":
            pool = decoy_pages + benign
            decoys.setdefault(slot, []).append({
                "tool": "http_request",
                "args": {"host": rng.choice(http_hosts),
                         "path": rng.choice(pool)},
                "reasoning": "Decoy page probe.",
                "evidence_refs": []})
        elif kind == "guess":
            for _ in range(10):
                host = rng.choice(hosts)
                user = rng.choice(words.NOISE_USERS)
                password = rng.choice(words.WEAK_PASSWORDS)
                if (host, user, password) not in real:
                    break
            decoys.setdefault(slot, []).append({
                "tool": "try_credential",
                "args": {"host": host, "user": user,
                         "password": password},
                "reasoning": "Decoy sign-in guess.",
                "evidence_refs": []})
        else:
            decoys.setdefault(slot, []).append({
                "tool": "report_finding",
                "args": {"text": "Continuing probes, nothing conclusive yet."},
                "reasoning": "Idle note while probing.",
                "evidence_refs": []})
    plan = []
    for index, move in enumerate(chain):
        plan.extend(decoys.get(index, []))
        plan.append(move)
    return plan


class ChainRed:
    """Scripted path-following red for one generated spec."""

    actor = "red"

    def __init__(self, spec: dict):
        self._plan = chain_plan(spec)
        self._step = 0

    def next_action(self, turn: int, history: list) -> dict | None:
        """Return the next planned move, or None when finished."""
        del turn, history
        if self._step >= len(self._plan):
            return None
        action = self._plan[self._step]
        self._step += 1
        return {"tool": action["tool"], "args": dict(action["args"]),
                "reasoning": action["reasoning"], "evidence_refs": []}


class NoisyRed:
    """Scripted chain red with seeded decoys, zero model calls."""

    actor = "red"

    def __init__(self, spec: dict, seed: str):
        self._plan = noisy_plan(spec, seed)
        self._step = 0

    def next_action(self, turn: int, history: list) -> dict | None:
        """Return the next planned move, or None when finished."""
        del turn, history
        if self._step >= len(self._plan):
            return None
        action = self._plan[self._step]
        self._step += 1
        return {"tool": action["tool"], "args": dict(action["args"]),
                "reasoning": action["reasoning"], "evidence_refs": []}
