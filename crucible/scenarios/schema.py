"""Scenario spec schema: dataclass layer plus JSON validation.

A spec is plain JSON-able data: id, seed, difficulty, hosts, the
attack path as weakness records, decoys, noise, the crown jewel, and
the blue-visible inventory. from_dict validates strictly so a bad
spec fails fast with the field named.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from crucible.scenarios import words

DIFFICULTIES = ("easy", "medium", "hard", "frontier")

WEAKNESS_KINDS = (
    "exposed_page",
    "weak_creds",
    "config_creds",
    "backup_creds",
    "stale_reuse",
)

STEP_KINDS = WEAKNESS_KINDS + ("signin_known", "jewel_read")

# Path lengths per difficulty: weaknesses plus the final jewel read.
PATH_LENGTHS = {"easy": 3, "medium": 4, "hard": 5, "frontier": 7}

MIN_HOSTS = 6
MAX_HOSTS = 10

# The jewel directory stays /secret/ because tools.py gates sign-in
# checks on that prefix; Stage 2 keeps the same gate.
JEWEL_DIR = "/secret"

SCENARIO_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Crucible generated scenario",
    "type": "object",
    "required": [
        "scenario_id", "seed", "difficulty", "hosts", "entry_host",
        "path", "dead_ends", "decoys", "noise", "jewel",
        "blue_inventory",
    ],
    "properties": {
        "scenario_id": {"type": "string"},
        "seed": {"type": "integer"},
        "difficulty": {"enum": list(DIFFICULTIES)},
        "hosts": {
            "type": "array", "minItems": MIN_HOSTS, "maxItems": MAX_HOSTS,
            "items": {
                "type": "object",
                "required": ["name", "role", "segment", "weight",
                             "links", "services"],
                "properties": {
                    "name": {"type": "string"},
                    "role": {"enum": list(words.ROLES)},
                    "segment": {"enum": list(words.SEGMENTS)},
                    "weight": {"type": "integer", "minimum": 1,
                               "maximum": 5},
                    "links": {"type": "array", "items": {"type": "string"}},
                    "services": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "entry_host": {"type": "string"},
        "path": {
            "type": "array", "minItems": 3, "maxItems": 7,
            "items": {
                "type": "object",
                "required": ["kind", "host"],
                "properties": {
                    "kind": {"enum": list(STEP_KINDS)},
                    "host": {"type": "string"},
                },
            },
        },
        "dead_ends": {"type": "array"},
        "decoys": {"type": "object"},
        "noise": {"type": "object"},
        "jewel": {
            "type": "object",
            "required": ["host", "path"],
            "properties": {
                "host": {"type": "string"},
                "path": {"type": "string"},
            },
        },
        "blue_inventory": {
            "type": "object",
            "required": ["hosts", "crown_jewel_host"],
        },
    },
}


@dataclass
class Host:
    name: str
    role: str
    segment: str
    weight: int
    links: list = field(default_factory=list)
    services: list = field(default_factory=list)


@dataclass
class PathStep:
    kind: str
    host: str
    detail: dict = field(default_factory=dict)


@dataclass
class Scenario:
    scenario_id: str
    seed: int
    difficulty: str
    hosts: list = field(default_factory=list)
    entry_host: str = ""
    path: list = field(default_factory=list)
    dead_ends: list = field(default_factory=list)
    decoys: dict = field(default_factory=dict)
    noise: dict = field(default_factory=dict)
    jewel: dict = field(default_factory=dict)
    blue_inventory: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def host_map(self) -> dict:
        return {host["name"] if isinstance(host, dict) else host.name: host
                for host in self.hosts}


def _need(cond: bool, message: str, errors: list) -> None:
    if not cond:
        errors.append(message)


def validate_shape(spec: dict) -> list:
    """Check a spec dict against the schema shape, returning errors."""
    errors: list = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    for key in SCENARIO_SCHEMA["required"]:
        _need(key in spec, f"missing required field: {key}", errors)
    if errors:
        return errors
    _need(spec.get("difficulty") in DIFFICULTIES,
          f"bad difficulty: {spec.get('difficulty')!r}", errors)
    hosts = spec.get("hosts", [])
    _need(isinstance(hosts, list) and MIN_HOSTS <= len(hosts) <= MAX_HOSTS,
          f"hosts must list {MIN_HOSTS}-{MAX_HOSTS} entries", errors)
    names = []
    for host in hosts if isinstance(hosts, list) else []:
        if not isinstance(host, dict):
            errors.append("host entry must be an object")
            continue
        names.append(host.get("name"))
        _need(host.get("role") in words.ROLES,
              f"bad role for {host.get('name')!r}", errors)
        _need(host.get("segment") in words.SEGMENTS,
              f"bad segment for {host.get('name')!r}", errors)
        weight = host.get("weight")
        _need(isinstance(weight, int) and 1 <= weight <= 5,
              f"bad weight for {host.get('name')!r}", errors)
        _need(isinstance(host.get("links"), list),
              f"bad links for {host.get('name')!r}", errors)
        _need(isinstance(host.get("services"), list) and host.get("services"),
              f"bad services for {host.get('name')!r}", errors)
    _need(len(set(names)) == len(names), "host names must be unique", errors)
    _need(spec.get("entry_host") in names, "entry_host must name a host",
          errors)
    path = spec.get("path", [])
    want = PATH_LENGTHS.get(spec.get("difficulty"), 0)
    _need(isinstance(path, list) and len(path) == want,
          f"{spec.get('difficulty')} path must hold {want} steps", errors)
    for step in path if isinstance(path, list) else []:
        _need(isinstance(step, dict) and step.get("kind") in STEP_KINDS,
              f"bad step kind: {(step or {}).get('kind')!r}", errors)
        _need((step or {}).get("host") in names,
              f"step host must name a host: {(step or {}).get('host')!r}",
              errors)
    if isinstance(path, list) and path:
        _need(path[-1].get("kind") == "jewel_read",
              "path must end with the jewel read", errors)
    jewel = spec.get("jewel", {})
    _need(isinstance(jewel, dict) and jewel.get("host") in names,
          "jewel.host must name a host", errors)
    _need(isinstance(jewel, dict)
          and str(jewel.get("path", "")).startswith(JEWEL_DIR + "/"),
          f"jewel.path must sit under {JEWEL_DIR}/", errors)
    inventory = spec.get("blue_inventory", {})
    _need(isinstance(inventory, dict)
          and set(inventory.get("hosts", {})) == set(names),
          "blue_inventory must cover every host exactly once", errors)
    _need(inventory.get("crown_jewel_host") == jewel.get("host"),
          "blue_inventory crown jewel must match jewel.host", errors)
    for name, entry in (inventory.get("hosts", {}) or {}).items():
        _need(isinstance(entry, dict)
              and isinstance(entry.get("role"), str)
              and isinstance(entry.get("connected_to"), list)
              and isinstance(entry.get("business_weight"), int),
              f"bad blue_inventory entry for {name!r}", errors)
    return errors


def from_dict(spec: dict) -> Scenario:
    """Validate a spec dict and build the dataclass layer."""
    errors = validate_shape(spec)
    if errors:
        raise ValueError("; ".join(errors))
    hosts = [Host(**host) for host in spec["hosts"]]
    path = [PathStep(kind=step["kind"], host=step["host"],
                     detail={key: value for key, value in step.items()
                             if key not in ("kind", "host")})
            for step in spec["path"]]
    return Scenario(
        scenario_id=spec["scenario_id"],
        seed=spec["seed"],
        difficulty=spec["difficulty"],
        hosts=hosts,
        entry_host=spec["entry_host"],
        path=path,
        dead_ends=list(spec.get("dead_ends", [])),
        decoys=dict(spec.get("decoys", {})),
        noise=dict(spec.get("noise", {})),
        jewel=dict(spec["jewel"]),
        blue_inventory=dict(spec["blue_inventory"]),
    )
