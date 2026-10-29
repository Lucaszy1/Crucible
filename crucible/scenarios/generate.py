"""Deterministic scenario generator with validator re-rolls.

generate(seed, difficulty) builds a full spec: topology, names drawn
without replacement, one solvable attack path from the catalog, plus
decoys, dead ends, and noise. Every candidate runs through the
validator (shape, uniqueness, inventory purity, simulated
solvability, length, no-shortcut, sensor coverage); failures re-roll
with a derived seed, capped, so output is deterministic per
(seed, difficulty) and always valid.
"""

from __future__ import annotations

import random

from crucible.scenarios import catalog, words
from crucible.scenarios.schema import (
    JEWEL_DIR, PATH_LENGTHS, from_dict, validate_shape,
)

DIFFICULTY = {
    # host range, dead-end count, decoy admin pages, noise events,
    # decoy hosts, capped (sub-alert) noise.
    "easy": {"hosts": (6, 7), "dead_ends": 1, "decoy_pages": 2, "noise": 4},
    "medium": {"hosts": (7, 8), "dead_ends": 1, "decoy_pages": 4, "noise": 8},
    "hard": {"hosts": (9, 10), "dead_ends": 2, "decoy_pages": 6, "noise": 12},
    # Frontier fills the 10-host cap around a 4-host chain: three
    # dead ends, two target-looking decoy hosts, and busier but
    # still sub-alert background noise. Decoy pages stop at 6: the
    # pool holds 6, and _decoys caps there.
    "frontier": {"hosts": (10, 10), "dead_ends": 3, "decoy_pages": 6,
                 "noise": 20, "decoy_hosts": 2, "noise_capped": True},
}

MAX_REROLLS = 20


def _sample(rng: random.Random, pool, count: int) -> list:
    """Draw pool entries without replacement (deterministic)."""
    return rng.sample(list(pool), count)


def _leaked_password(rng: random.Random, used: set) -> str:
    """Mint a unique toy leaked password, never from the weak pool."""
    for _ in range(100):
        candidate = words.leaked_password(
            rng.choice(words.DIR_WORDS), rng.randint(0, 9999))
        if candidate not in used and candidate not in words.WEAK_PASSWORDS:
            used.add(candidate)
            return candidate
    raise ValueError("password pool exhausted")


class _Builder:
    """Holds one candidate's seeded draws and name registries."""

    def __init__(self, seed: int, difficulty: str, attempt: int):
        self.seed = seed
        self.difficulty = difficulty
        self.rng = random.Random(f"{seed}:{difficulty}:{attempt}")
        self.used_passwords: set = set()
        self.used_accounts: set = set()

    def account(self) -> str:
        for _ in range(100):
            candidate = self.rng.choice(words.ACCOUNT_WORDS)
            if candidate not in self.used_accounts:
                self.used_accounts.add(candidate)
                return candidate
        raise ValueError("account pool exhausted")

    def weak_password(self) -> str:
        candidate = self.rng.choice(words.WEAK_PASSWORDS)
        self.used_passwords.add(candidate)
        return candidate

    def leaked_password(self) -> str:
        return _leaked_password(self.rng, self.used_passwords)


def _instantiate(builder: _Builder, template: list, hosts: list,
                 dirs: list, configs: list, backups: list,
                 pages: list, jewel_file: str) -> tuple:
    """Fill template slots with accounts, passwords, and paths.

    A cursor walks the chain: each page or known sign-in advances to
    the leaked host, while config reads, the weak sign-in, and the
    jewel read stay where the cursor is.
    """
    chain = list(hosts)
    steps: list = []
    weak_account = builder.account()
    weak_password = builder.weak_password()
    pending = (weak_account, weak_password)
    at = 0
    for kind in template:
        if kind == "exposed_page":
            host = chain[at]
            nxt = chain[at + 1]
            leaked = pending if steps == [] else (builder.account(), None)
            if steps:
                pending = leaked
            steps.append({
                "kind": kind, "host": host,
                "page_path": pages.pop(0),
                "leaks_host": nxt,
                "leaks_account": leaked[0],
            })
            at += 1
        elif kind == "weak_creds":
            host = chain[at]
            steps.append({"kind": kind, "host": host,
                          "account": pending[0], "password": weak_password})
        elif kind in ("config_creds", "backup_creds"):
            pool = configs if kind == "config_creds" else backups
            directory = f"/srv/{dirs.pop(0)}"
            path = f"{directory}/{pool.pop(0)}"
            steps.append({
                "kind": kind, "host": chain[at],
                "dir": directory, "path": path,
                "reveals_host": chain[at + 1],
                "reveals_account": builder.account(),
                "reveals_password": builder.leaked_password(),
            })
        elif kind == "signin_known":
            prev = steps[-1]
            at += 1
            steps.append({"kind": kind, "host": chain[at],
                          "account": prev["reveals_account"],
                          "password": prev["reveals_password"]})
        elif kind == "jewel_read":
            steps.append({"kind": kind, "host": chain[at],
                          "dir": JEWEL_DIR,
                          "path": f"{JEWEL_DIR}/{jewel_file}"})
    return steps, chain


def _roles_for(builder: _Builder, template: list, chain: list) -> dict:
    """Pick plausible roles for chain hosts (entry dmz, jewel data)."""
    rng = builder.rng
    roles: dict = {}
    at = 0
    for kind in template:
        if kind == "exposed_page":
            roles[chain[at]] = rng.choice(("web portal", "api"))
            at += 1
        elif kind == "weak_creds":
            if "backup_creds" in template:
                roles[chain[at]] = "file share"  # the backup lives here
            else:
                roles[chain[at]] = rng.choice(("app", "api", "file share"))
        elif kind in ("config_creds", "backup_creds"):
            pass  # shares its host with the earlier sign-in
        elif kind == "signin_known":
            at += 1
        elif kind == "jewel_read":
            roles[chain[at]] = rng.choice(("database", "backup",
                                           "file share"))
    # Longer chains pass through middle hosts that own no page,
    # sign-in, or jewel step of their own; they still need a role
    # with a sign-in gate for the credential hop. Existing tiers
    # cover every chain host above, so this only touches frontier,
    # and it draws no randoms.
    for host in chain:
        if host not in roles:
            roles[host] = "app"
    return roles


def _segments(chain_roles: dict, chain: list) -> dict:
    """Place chain hosts: entry dmz, jewel data, middle internal."""
    segments = {}
    for position, host in enumerate(chain):
        if position == 0:
            segments[host] = "dmz"
        elif position == len(chain) - 1:
            segments[host] = "data"
        else:
            segments[host] = "internal"
    return segments


def _fillers(builder: _Builder, names: list, chain: list,
             decoy_hosts: int = 0) -> tuple:
    """Assign roles and segments to non-chain hosts.

    Returns (fillers, decoy_names): the first decoy_hosts picks wear
    data roles in the data segment, so the blue inventory shows them
    as target-looking stores that hold nothing.
    """
    rng = builder.rng
    out = []
    pool = ["workstation", "jump host", "app", "api", "file share",
            "backup", "web portal", "database"]
    for name in names:
        if name in chain:
            continue
        role = rng.choice(pool)
        segment = rng.choice(words.ROLE_SEGMENTS[role])
        if segment == "dmz" and role not in ("web portal", "api"):
            segment = "internal"
        out.append({"name": name, "role": role, "segment": segment})
    decoy_names: list = []
    if decoy_hosts and out:
        for index, pick in enumerate(rng.sample(
                out, min(decoy_hosts, len(out)))):
            pick["role"] = "database" if index % 2 == 0 else "file share"
            pick["segment"] = "data"
            decoy_names.append(pick["name"])
    # Dead ends and decoy pages need requestable hosts: force at
    # least one filler onto an HTTP role.
    if out and not any(item["role"] in words.HTTP_ROLES for item in out):
        pick = rng.choice(out)
        pick["role"] = rng.choice(("web portal", "api"))
    return out, sorted(decoy_names)


def _links(builder: _Builder, chain: list, fillers: list) -> dict:
    """Link chain neighbors, then attach each filler (connected)."""
    rng = builder.rng
    links: dict = {host: set() for host in chain}
    for first, second in zip(chain, chain[1:]):
        links[first].add(second)
        links[second].add(first)
    placed = list(chain)
    for filler in fillers:
        name = filler["name"]
        links[name] = set()
        peer = rng.choice(placed)
        links[name].add(peer)
        links[peer].add(name)
        placed.append(name)
        if rng.random() < 0.5 and len(placed) > 2:
            extra = rng.choice([host for host in placed
                                if host != name and host not in links[name]])
            links[name].add(extra)
            links[extra].add(name)
    return {host: sorted(peers) for host, peers in links.items()}


def _dead_ends(builder: _Builder, fillers: list, count: int,
               pages: list) -> list:
    """Build dead-end mini-chains on filler hosts (lead nowhere).

    Each dead end is a stale-reuse chain: an exposed page names an
    account, a weak sign-in works, and the same weak password opens
    a stale account on a terminal filler host that holds nothing.
    """
    rng = builder.rng
    ends = []
    http_hosts = [entry["name"] for entry in fillers
                  if entry["role"] in words.HTTP_ROLES]
    # Terminals must run a real sign-in gate (no workstations: their
    # gate always fails, so a dead-end sign-in there could never work).
    terminals = [entry["name"] for entry in fillers
                 if entry["role"] not in words.NO_GATE_ROLES]
    for number in range(count):
        if not http_hosts or len(terminals) < 2:
            break
        host = rng.choice(http_hosts)
        terminal = rng.choice([name for name in terminals
                               if name != host])
        ends.append({
            "id": f"dead-{number + 1}",
            "host": host,
            "page_path": pages.pop(0),
            "account": builder.account(),
            "password": builder.weak_password(),
            "terminal_host": terminal,
            "terminal_account": builder.account(),
        })
    return ends


def _noise(builder: _Builder, hosts: list, count: int,
           entry: str, capped: bool = False) -> dict:
    """Background sign-in failures and benign requests (below alerts)."""
    rng = builder.rng
    failures = []
    if capped:
        # Frontier spreads one failure entry per host with at most
        # two lines each, so busier logs still stay below the
        # failed-burst threshold (3) on every host.
        takes = [2] * ((count // 2) // 2)
        takes += [1] * (count // 2 - len(takes))
        rng.shuffle(takes)
        order = rng.sample(list(hosts), len(hosts))
        for host, take in zip(order[:count // 2], takes):
            failures.append({
                "host": host,
                "user": rng.choice(words.NOISE_USERS),
                "count": take,
            })
    else:
        gated = [host for host in hosts]
        for _ in range(count // 2):
            failures.append({
                "host": rng.choice(gated),
                "user": rng.choice(words.NOISE_USERS),
                "count": rng.choice((1, 1, 2)),
            })
    requests = []
    for _ in range(count - count // 2):
        requests.append({
            "host": entry,
            "path": rng.choice(words.BENIGN_PAGES),
        })
    return {"failures": failures, "requests": requests}


def _decoys(builder: _Builder, fillers: list, entry: str,
            count: int) -> dict:
    """Harmless admin-looking pages plus benign status pages."""
    rng = builder.rng
    hosts = [entry] + [item["name"] for item in fillers
                       if item["role"] in words.HTTP_ROLES]
    pages = _sample(rng, words.DECOY_ADMIN_PAGES, min(count, len(words.DECOY_ADMIN_PAGES)))
    admin_pages = [{"host": rng.choice(hosts), "path": path}
                   for path in pages]
    status_pages = [{"host": entry, "path": path}
                    for path in _sample(rng, words.BENIGN_PAGES[1:], 3)]
    return {"admin_pages": admin_pages, "status_pages": status_pages}


def _inventory(host_entries: list, jewel_host: str) -> dict:
    """Blue-visible inventory: roles, links, weights, jewel host."""
    return {
        "hosts": {
            entry["name"]: {
                "role": entry["role"],
                "connected_to": list(entry["links"]),
                "business_weight": entry["weight"],
            }
            for entry in host_entries
        },
        "crown_jewel_host": jewel_host,
    }


def _build_once(seed: int, difficulty: str, attempt: int) -> dict:
    """Build one candidate spec (may still fail validation)."""
    builder = _Builder(seed, difficulty, attempt)
    rng = builder.rng
    shape = DIFFICULTY[difficulty]
    template = rng.choice(catalog.TEMPLATES[difficulty])
    host_count = rng.randint(*shape["hosts"])
    names = _sample(rng, words.HOST_WORDS, host_count)
    dirs = _sample(rng, words.DIR_WORDS, 8)
    configs = _sample(rng, words.CONFIG_FILES, 4)
    backups = _sample(rng, words.BACKUP_FILES, 2)
    pages = _sample(rng, words.ADMIN_PAGES, 4)
    jewel_file = rng.choice(words.JEWEL_FILES)

    # Chain host name count must fit the template's host slots: one
    # per page and known sign-in, plus the entry host.
    slots = 1 + sum(1 for step in template
                    if step in ("exposed_page", "signin_known"))
    chain_names = names[:slots]
    steps, chain = _instantiate(builder, template, chain_names, dirs,
                                configs, backups, pages, jewel_file)
    chain_roles = _roles_for(builder, template, chain)
    chain_segments = _segments(chain_roles, chain)
    fillers, decoy_names = _fillers(builder, names, chain,
                                 shape.get("decoy_hosts", 0))
    links = _links(builder, chain, fillers)

    host_entries = []
    for name in names:
        if name in chain_roles:
            role, segment = chain_roles[name], chain_segments[name]
        else:
            entry = next(item for item in fillers if item["name"] == name)
            role, segment = entry["role"], entry["segment"]
        host_entries.append({
            "name": name,
            "role": role,
            "segment": segment,
            "weight": words.ROLE_WEIGHTS[role],
            "links": links[name],
            "services": list(words.ROLE_SERVICES[role]),
        })

    jewel_host = chain[-1]
    jewel_path = f"{JEWEL_DIR}/{jewel_file}"
    dead = _dead_ends(builder, fillers, shape["dead_ends"], pages)
    decoys = _decoys(builder, fillers, chain[0], shape["decoy_pages"])
    if decoy_names:
        decoys["decoy_hosts"] = list(decoy_names)
    noise = _noise(builder, names, shape["noise"], chain[0],
                   shape.get("noise_capped", False))
    spec = {
        "scenario_id": f"scn-{seed:04d}-{difficulty}-a{attempt}",
        "seed": seed,
        "difficulty": difficulty,
        "hosts": host_entries,
        "entry_host": chain[0],
        "path": steps,
        "dead_ends": dead,
        "decoys": decoys,
        "noise": noise,
        "jewel": {"host": jewel_host, "path": jewel_path},
        "blue_inventory": _inventory(host_entries, jewel_host),
    }
    return spec


def frontier_topology_errors(spec: dict) -> list:
    """Frontier reachability: the jewel hides at least 3 hops out.

    The chain already walks entry -> foothold -> middle -> jewel,
    but a filler occasionally links both ends and shortcuts the
    walk; those candidates re-roll. The first foothold (the weak
    sign-in host) must never link the jewel host directly.
    """
    links = {host["name"]: set(host.get("links", []))
             for host in spec.get("hosts", [])}
    entry, jewel = spec.get("entry_host"), spec["jewel"]["host"]
    foothold = next((step["host"] for step in spec.get("path", [])
                     if step.get("kind") == "weak_creds"), entry)
    errors = []
    if jewel in links.get(foothold, set()):
        errors.append("jewel host links the first foothold directly")
    seen, queue, distance = {entry}, [entry], {entry: 0}
    while queue:
        node = queue.pop(0)
        for peer in sorted(links.get(node, ())):
            if peer not in seen:
                seen.add(peer)
                distance[peer] = distance[node] + 1
                queue.append(peer)
    if distance.get(jewel, 99) < 3:
        errors.append("jewel host is fewer than 3 hops from the entry")
    return errors


def generate(seed: int, difficulty: str) -> dict:
    """Build a valid spec, re-rolling capped failures deterministically."""
    from crucible.scenarios import simulate

    if difficulty not in DIFFICULTY:
        raise ValueError(f"unknown difficulty: {difficulty!r}")
    last_errors: list = []
    want_dead = DIFFICULTY[difficulty]["dead_ends"]
    for attempt in range(MAX_REROLLS + 1):
        spec = _build_once(seed, difficulty, attempt)
        errors = validate_shape(spec)
        if not errors:
            errors = simulate.validate_spec(spec)
        if not errors and len(spec["dead_ends"]) < want_dead:
            errors = [f"only {len(spec['dead_ends'])} dead ends, "
                      f"want {want_dead}"]
        if not errors:
            errors = simulate.validate_simulation(spec)
        if not errors and difficulty == "frontier":
            errors = frontier_topology_errors(spec)
        if not errors:
            return spec
        last_errors = errors
    raise ValueError(f"could not build {difficulty} spec for seed {seed}: "
                     f"{last_errors}")


__all__ = ["DIFFICULTY", "MAX_REROLLS", "frontier_topology_errors", "generate"]
