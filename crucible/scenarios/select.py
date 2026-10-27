"""Scenario selection for runs over generated specs.

A scenario key is "classic" or ("gen", seed, difficulty). The CLIs
accept --scenarios as a comma list of `classic` and
`gen:<seed>:<difficulty>` tokens, plus --scenario-set train|heldout
to append the named seed sets at one difficulty. Episodes group by
scenario (outer loop) so the compose stack restarts as rarely as
possible.
"""

from __future__ import annotations

from crucible.scenarios.schema import DIFFICULTIES
from crucible.scenarios.seeds import HELDOUT_SEEDS, TRAIN_SEEDS

SETS = {"train": list(TRAIN_SEEDS), "heldout": list(HELDOUT_SEEDS)}


def parse_token(text: str):
    """Parse one scenario token into a scenario key."""
    cleaned = (text or "").strip()
    if cleaned == "classic":
        return "classic"
    parts = cleaned.split(":")
    if len(parts) != 3 or parts[0] != "gen":
        raise ValueError(
            f"scenario {text!r} must be 'classic' or "
            f"'gen:<seed>:<difficulty>'")
    try:
        seed = int(parts[1])
    except (TypeError, ValueError):
        raise ValueError(
            f"scenario {text!r} has a non-integer seed") from None
    difficulty = parts[2].strip()
    if difficulty not in DIFFICULTIES:
        raise ValueError(
            f"scenario {text!r} difficulty must be one of "
            f"{', '.join(DIFFICULTIES)}")
    return ("gen", seed, difficulty)


def parse_list(raw: str | None) -> list:
    """Parse a --scenarios comma list, defaulting to classic only."""
    if not (raw or "").strip():
        return ["classic"]
    return [parse_token(piece) for piece in raw.split(",")
            if piece.strip()]


def expand_set(name: str, difficulty: str) -> list:
    """Expand a named seed set into scenario keys."""
    if name not in SETS:
        raise ValueError(f"scenario set {name!r} must be one of "
                         f"{', '.join(sorted(SETS))}")
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"difficulty {difficulty!r} must be one of "
                         f"{', '.join(DIFFICULTIES)}")
    return [("gen", seed, difficulty) for seed in SETS[name]]


def selection(scenarios_raw: str | None, set_name: str | None,
              difficulty: str) -> list:
    """Build the ordered, deduplicated scenario key list.

    --scenarios comes first (default classic), then the named set if
    given. Duplicates collapse to the first occurrence so a stack is
    never started twice for the same scenario.
    """
    keys = parse_list(scenarios_raw)
    if set_name:
        keys.extend(expand_set(set_name, difficulty))
    seen = set()
    ordered = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def label(key) -> str:
    """Short stable label for episode ids and CSV rows."""
    if key == "classic":
        return "classic"
    _kind, seed, difficulty = key
    return f"gen-{seed}-{difficulty}"


def is_classic(key) -> bool:
    return key == "classic"
