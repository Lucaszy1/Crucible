"""Dry-run simulator: prove a spec solvable with no containers.

The simulator derives an in-memory range from the spec (accounts,
files, pages), executes tool calls with the real tools' semantics
(sign-in gates, readable roots, guessable weak pool), and prints log
lines in the exact formats the real hosts print. validate_spec runs
the static checks; validate_simulation runs the dynamic ones: the
chain completes, the length matches, every step's lines match real
formats, ordered leave-one-out proves no shortcut, dead ends lead
nowhere, and the generic sensors fire at least one alert per step.
"""

from __future__ import annotations

import re

from crucible import sensors
from crucible.scenarios import words
from crucible.scenarios.schema import JEWEL_DIR, PATH_LENGTHS

# Fixed request source: the real range relays HTTP through one
# workstation container address, so every request line shares it.
REQUEST_SOURCE = "172.18.0.5"

# Real line formats, mirrored from the sandbox host scripts
# (signin.sh, read-file.sh, list-files.sh, web app.py). The format
# test pins these against the script sources.
LINE_FORMATS = {
    "signin_ok": re.compile(r"^signin ok user=\S+ from=\S+$"),
    "signin_fail": re.compile(r"^signin fail user=\S+ from=\S+$"),
    "signin_fail_nogate": re.compile(
        r"^signin fail user=\S+ from=\S+ note=no-gate$"),
    "file_read": re.compile(r"^file read path=\S+ bytes=\d+ from=\S+$"),
    "dir_list": re.compile(r"^dir list path=\S+ entries=\d+ from=\S+$"),
    "request": re.compile(
        r"^request from=\S+ method=\S+ path=\S+ status=\d+$"),
}

# Toy leaked-password shape: guess-proof, obviously fake.
LEAKED_PASSWORD_RE = re.compile(r"^toy-[a-z]+-\d{4}$")

# Words that must never appear in the blue inventory (same rule as
# the classic scenario: roles, links, weights, jewel host only).
INVENTORY_FORBIDDEN = (
    "vulnerab", "weak", "exposed", "leak", "hint", "exploit",
    "backdoor", "misconfig", "unpatched", "default sign-in",
    "stale", "decoy",
)


class Range:
    """In-memory range derived from one spec."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.hosts = {host["name"]: host for host in spec["hosts"]}
        self.roles = {name: host["role"] for name, host in self.hosts.items()}
        self.creds: dict = {}
        self.files: dict = {}
        self.dirs: dict = {}
        self.pages: dict = {}
        self._load_path()
        self._load_dead_ends()
        self._load_decoys()
        self.authed: set = set()
        self.logs: dict = {name: [] for name in self.hosts}
        self.knowledge: dict = {}  # (host, user) -> password learned

    # -- spec loading ------------------------------------------------

    def _add_account(self, host: str, user: str, password: str) -> None:
        self.creds[(host, user)] = password

    def _add_file(self, host: str, path: str, content: str,
                  reveals: dict | None = None, jewel: bool = False) -> None:
        self.files[(host, path)] = {"content": content, "reveals": reveals,
                                    "jewel": jewel}
        parent = path.rsplit("/", 1)[0] or "/"
        self.dirs.setdefault((host, parent), []).append(path)

    def _add_page(self, host: str, path: str, leak=None,
                  decoy: bool = False) -> None:
        self.pages[(host, path)] = {"leak": leak, "decoy": decoy}

    def _load_path(self) -> None:
        for step in self.spec["path"]:
            kind, host = step["kind"], step["host"]
            if kind == "exposed_page":
                self._add_page(host, step["page_path"], leak={
                    "host": step["leaks_host"],
                    "account": step["leaks_account"]})
            elif kind in ("weak_creds", "stale_reuse", "signin_known"):
                self._add_account(host, step["account"], step["password"])
            elif kind in ("config_creds", "backup_creds"):
                content = (f"host={step['reveals_host']} "
                           f"user={step['reveals_account']} "
                           f"password={step['reveals_password']}")
                self._add_file(host, step["path"], content, reveals={
                    "host": step["reveals_host"],
                    "account": step["reveals_account"],
                    "password": step["reveals_password"]})
            elif kind == "jewel_read":
                content = f"TOY-SECRET-{self.spec['scenario_id']}"
                self._add_file(host, step["path"], content, jewel=True)

    def _load_dead_ends(self) -> None:
        for end in self.spec.get("dead_ends", []):
            self._add_page(end["host"], end["page_path"], leak={
                "host": end["host"], "account": end["account"]})
            self._add_account(end["host"], end["account"], end["password"])
            self._add_account(end["terminal_host"], end["terminal_account"],
                              end["password"])

    def _load_decoys(self) -> None:
        decoys = self.spec.get("decoys", {})
        for entry in decoys.get("admin_pages", []):
            self._add_page(entry["host"], entry["path"], decoy=True)
        for entry in decoys.get("status_pages", []):
            self._add_page(entry["host"], entry["path"])

    # -- logging -----------------------------------------------------

    def _log(self, host: str, line: str) -> str:
        ref = f"{host}:{len(self.logs[host])}"
        self.logs[host].append((ref, line))
        return ref

    # -- tool execution (real tools' semantics) ----------------------

    def _gated(self, host: str) -> bool:
        return self.roles[host] not in words.NO_GATE_ROLES

    def _http(self, host: str) -> bool:
        return self.roles[host] in words.HTTP_ROLES

    def scan_host(self, host: str) -> dict:
        up = host in self.hosts
        return {"ok": True, "host": host, "running": up, "reachable": up,
                "ports": [8000] if up and self._http(host) else [],
                "isolated": False}

    def _known_host(self, host: str) -> bool:
        if host == self.spec["entry_host"]:
            return True
        return any(known == host for known, _ in self.knowledge)

    def http_request(self, host: str, path: str,
                     method: str = "GET", known_only: bool = False) -> dict:
        if not self._http(host):
            return {"ok": False,
                    "error": f"no http service declared on {host!r}"}
        if known_only and not self._known_host(host):
            return {"ok": False, "error": f"unknown host {host!r}"}
        page = self.pages.get((host, path))
        status = 200 if page is not None else 404
        self._log(host, f"request from={REQUEST_SOURCE} method={method} "
                        f"path={path} status={status}")
        if page is None:
            return {"ok": True, "host": host, "path": path, "status": 404}
        body: dict = {"ok": True, "host": host, "path": path, "status": 200}
        if page["leak"] is not None:
            leak = page["leak"]
            body["hint"] = (f"internal host {leak['host']!r} runs reports "
                            f"as {leak['account']!r}")
            self.knowledge[(leak["host"], leak["account"])] = None
        return body

    def try_credential(self, host: str, user: str, password: str,
                       known_only: bool = False) -> dict:
        """Attempt a sign-in, logging ok/fail exactly like the gate.

        With known_only, red may only try targets it learned (the
        page and file leaks) and passwords it learned or can guess
        from the weak pool; anything else fails without logging, as
        red would never attempt an unknown target.
        """
        real = self.creds.get((host, user))
        if known_only:
            learned = set(self.knowledge.values())
            if (host, user) not in self.knowledge or \
                    (password not in words.WEAK_PASSWORDS
                     and password not in learned):
                return {"ok": True, "host": host, "authenticated": False,
                        "user": user}
        ok = self._gated(host) and real is not None and password == real
        line = f"signin {'ok' if ok else 'fail'} user={user} from=controller"
        if not self._gated(host):
            line += " note=no-gate"
        self._log(host, line)
        if ok:
            self.authed.add(host)
            self.knowledge[(host, user)] = password
            return {"ok": True, "host": host, "authenticated": True,
                    "user": user}
        return {"ok": True, "host": host, "authenticated": False,
                "user": user}

    def _needs_session(self, host: str, path: str) -> bool:
        """Check the sign-in gate for one file operation.

        The jewel directory gates exactly like the classic /secret/
        roots. Config and backup roots additionally require a session:
        a Stage 2 image requirement so the sign-in step stays
        load-bearing (classic roots are world-readable, which would
        let red skip signing in before reading them).
        """
        if path == JEWEL_DIR or path.startswith(JEWEL_DIR + "/"):
            return True
        return (host, path) in self.dirs or any(
            key == (host, path) for key in self.files)

    def list_files(self, host: str, path: str,
                   known_only: bool = False) -> dict:
        if known_only and not self._known_host(host):
            return {"ok": False, "error": f"unknown host {host!r}"}
        if self._needs_session(host, path) and host not in self.authed:
            return {"ok": False,
                    "error": f"sign-in on {host!r} is required first"}
        entries = self.dirs.get((host, path))
        if entries is None:
            return {"ok": False, "error": f"cannot list {path} on {host!r}"}
        names = [entry.rsplit("/", 1)[-1] for entry in entries]
        self._log(host, f"dir list path={path} entries={len(names)} "
                        f"from=controller")
        return {"ok": True, "host": host, "path": path, "files": names}

    def read_file(self, host: str, path: str,
                  known_only: bool = False) -> dict:
        if known_only and not self._known_host(host):
            return {"ok": False, "error": f"unknown host {host!r}"}
        if self._needs_session(host, path) and host not in self.authed:
            return {"ok": False,
                    "error": f"sign-in on {host!r} is required first"}
        entry = self.files.get((host, path))
        if entry is None:
            return {"ok": False, "error": f"cannot read {path} on {host!r}"}
        content = entry["content"]
        self._log(host, f"file read path={path} bytes={len(content)} "
                        f"from=controller")
        reveals = entry.get("reveals")
        if reveals is not None:
            self.knowledge[(reveals["host"], reveals["account"])] = \
                reveals["password"]
        return {"ok": True, "host": host, "path": path, "content": content}

    def run_action(self, action: dict, known_only: bool = False) -> dict:
        """Execute one tool-call dict, returning its result."""
        tool, args = action["tool"], dict(action.get("args", {}))
        if tool == "scan_host":
            return self.scan_host(args["host"])
        if tool == "http_request":
            return self.http_request(args["host"], args["path"],
                                     args.get("method", "GET"),
                                     known_only=known_only)
        if tool == "try_credential":
            return self.try_credential(args["host"], args["user"],
                                       args["password"],
                                       known_only=known_only)
        if tool == "list_files":
            return self.list_files(args["host"], args["path"],
                                   known_only=known_only)
        if tool == "read_file":
            return self.read_file(args["host"], args["path"],
                                  known_only=known_only)
        if tool == "report_finding":
            return {"ok": True, "id": "finding-sim"}
        raise ValueError(f"simulator cannot run tool: {tool!r}")


def _collect_accounts(spec: dict) -> list:
    """List every (host, user, password) triple in path and dead ends."""
    triples = []
    for step in spec.get("path", []):
        kind = step.get("kind")
        # signin_known replays a credential the preceding file step
        # revealed; it is not a new account, so skip it here.
        if kind in ("weak_creds", "stale_reuse"):
            triples.append((step.get("host"), step.get("account"),
                            step.get("password")))
        elif kind in ("config_creds", "backup_creds"):
            triples.append((step.get("reveals_host"),
                            step.get("reveals_account"),
                            step.get("reveals_password")))
    for end in spec.get("dead_ends", []):
        triples.append((end.get("host"), end.get("account"),
                        end.get("password")))
        triples.append((end.get("terminal_host"), end.get("terminal_account"),
                        end.get("password")))
    return triples


def _collect_paths(spec: dict) -> list:
    """List every planted file path in the spec."""
    paths = []
    for step in spec.get("path", []):
        if step.get("kind") in ("config_creds", "backup_creds"):
            paths.append((step.get("host"), step.get("path")))
        elif step.get("kind") == "jewel_read":
            paths.append((step.get("host"), step.get("path")))
    return paths


def validate_spec(spec: dict) -> list:
    """Static semantic checks beyond the JSON shape."""
    from crucible.scenarios.schema import validate_shape

    errors = list(validate_shape(spec))
    if errors:
        return errors
    names = [host["name"] for host in spec["hosts"]]
    by_name = {host["name"]: host for host in spec["hosts"]}

    # Links are symmetric and the graph is connected.
    for host in spec["hosts"]:
        for peer in host.get("links", []):
            if peer not in names:
                errors.append(f"{host['name']} links unknown host {peer!r}")
            elif host["name"] not in by_name[peer].get("links", []):
                errors.append(f"link {host['name']}-{peer} is one-sided")
    seen, stack = set(), [spec["entry_host"]]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(by_name[node].get("links", []))
    if set(names) != seen:
        errors.append("host graph is not connected")

    # Entry and jewel placement.
    if by_name[spec["entry_host"]].get("segment") != "dmz":
        errors.append("entry host must sit in dmz")
    if by_name[spec["entry_host"]].get("role") not in words.HTTP_ROLES:
        errors.append("entry host must answer HTTP")
    jewel_host = spec["jewel"]["host"]
    if by_name[jewel_host].get("segment") != "data":
        errors.append("crown jewel host must sit in data")

    # Name pools never collide inside one scenario.
    accounts = [(host, user) for host, user, _ in _collect_accounts(spec)]
    if len({user for _, user in accounts}) != len(accounts):
        errors.append("account names must be unique in one scenario")
    file_paths = _collect_paths(spec)
    if len(set(file_paths)) != len(file_paths):
        errors.append("planted file paths must be unique in one scenario")
    basenames = [path.rsplit("/", 1)[-1] for _, path in file_paths]
    if len(set(basenames)) != len(basenames):
        errors.append("planted file names must be unique in one scenario")

    # Passwords: weak pool for guessable steps, toy-leaked otherwise.
    seen_passwords: set = set()
    for step in spec.get("path", []):
        kind = step.get("kind")
        if kind in ("weak_creds", "stale_reuse"):
            if step.get("password") not in words.WEAK_PASSWORDS:
                errors.append(f"{kind} password must be weak-pool")
        elif kind == "signin_known":
            password = step.get("password", "")
            if not LEAKED_PASSWORD_RE.match(str(password)):
                errors.append("signin_known password must be toy-leaked")
    for host, user, password in _collect_accounts(spec):
        if password in words.WEAK_PASSWORDS or \
                LEAKED_PASSWORD_RE.match(str(password or "")):
            seen_passwords.add(password)
        else:
            errors.append(f"non-toy password for {user} on {host}")
    if len(seen_passwords) != len(_collect_accounts(spec)):
        # Weak passwords may repeat (one pool); leaked must be unique.
        leaked = [password for _, _, password in _collect_accounts(spec)
                  if password not in words.WEAK_PASSWORDS]
        if len(set(leaked)) != len(leaked):
            errors.append("leaked passwords must be unique in one scenario")

    # Noise users never equal real accounts (coverage stays clean).
    real_users = {user for _, user in accounts}
    for failure in spec.get("noise", {}).get("failures", []):
        if failure.get("user") in real_users:
            errors.append("noise user collides with a real account")

    # Chain integrity: each step's output unlocks the next step.
    path = spec.get("path", [])
    by_kind = {}
    for step in path:
        by_kind.setdefault(step.get("kind"), []).append(step)
    pages = by_kind.get("exposed_page", [])
    weak = (by_kind.get("weak_creds") or [{}])[0]
    for first, second in zip(pages, pages[1:]):
        if second.get("host") != first.get("leaks_host"):
            errors.append("chained page must sit on the leaked host")
    if not pages or weak.get("host") != pages[-1].get("leaks_host") or \
            weak.get("account") != pages[-1].get("leaks_account"):
        errors.append("final page leak must name the weak sign-in target")
    leak = None
    for kind in ("config_creds", "backup_creds"):
        for step in by_kind.get(kind, []):
            leak = step
    for known in by_kind.get("signin_known", []):
        if leak is None or known.get("host") != leak.get("reveals_host") \
                or known.get("account") != leak.get("reveals_account") \
                or known.get("password") != leak.get("reveals_password"):
            errors.append("known sign-in must use the file leak")
    if path and path[-1].get("host") != jewel_host:
        errors.append("path must end on the jewel host")
    if by_name[jewel_host].get("role") not in ("database", "backup",
                                               "file share"):
        errors.append("crown jewel host must hold a data role")

    # Dead ends stay off the path and away from the jewel.
    path_hosts = {step.get("host") for step in path}
    for end in spec.get("dead_ends", []):
        if end.get("host") in path_hosts:
            errors.append(f"{end.get('id')} sits on the path")
        if end.get("terminal_host") in path_hosts \
                or end.get("terminal_host") == jewel_host:
            errors.append(f"{end.get('id')} terminal reaches the path")
        host_entry = by_name.get(end.get("host"), {})
        if host_entry.get("role") not in words.HTTP_ROLES:
            errors.append(f"{end.get('id')} page host must answer HTTP")

    # Decoy and status pages sit on real HTTP hosts.
    decoys = spec.get("decoys", {})
    for entry in decoys.get("admin_pages", []) + \
            decoys.get("status_pages", []):
        host_entry = by_name.get(entry.get("host"))
        if host_entry is None:
            errors.append("decoy page names an unknown host")
        elif host_entry.get("role") not in words.HTTP_ROLES:
            errors.append("decoy page host must answer HTTP")

    # Noise references real hosts.
    for failure in spec.get("noise", {}).get("failures", []):
        if failure.get("host") not in names:
            errors.append("noise failure names an unknown host")
    for request in spec.get("noise", {}).get("requests", []):
        if request.get("host") not in names:
            errors.append("noise request names an unknown host")

    # Page paths are unique per host.
    seen_pages: set = set()
    page_entries = [(entry.get("host"), entry.get("page_path"))
                    for entry in pages]
    for end in spec.get("dead_ends", []):
        page_entries.append((end.get("host"), end.get("page_path")))
    for entry in spec.get("decoys", {}).get("admin_pages", []):
        page_entries.append((entry.get("host"), entry.get("path")))
    for entry in spec.get("decoys", {}).get("status_pages", []):
        page_entries.append((entry.get("host"), entry.get("path")))
    for host, path_value in page_entries:
        if (host, path_value) in seen_pages:
            errors.append(f"page {path_value} repeats on {host}")
        seen_pages.add((host, path_value))

    errors.extend(inventory_errors(spec))
    return errors


def inventory_errors(spec: dict) -> list:
    """Blue inventory purity: roles, links, jewel host only."""
    import json as _json

    errors = []
    blob = _json.dumps(spec.get("blue_inventory", {}), sort_keys=True)
    lowered = blob.lower()
    for _, user, password in _collect_accounts(spec):
        if user and user in blob:
            errors.append(f"inventory leaks account {user!r}")
        if password and str(password) in blob:
            errors.append("inventory leaks a password")
    for _, path in _collect_paths(spec):
        if path and path in blob:
            errors.append(f"inventory leaks path {path!r}")
    for word in INVENTORY_FORBIDDEN:
        if word in lowered:
            errors.append(f"inventory leaks weakness word {word!r}")
    return errors


def _step_lines(runner: Range, before: dict) -> set:
    """Refs logged since a per-host line-count snapshot."""
    refs = set()
    for host, lines in runner.logs.items():
        for ref, _ in lines[before.get(host, 0):]:
            refs.add(ref)
    return refs


def _snapshot(runner: Range) -> dict:
    return {host: len(lines) for host, lines in runner.logs.items()}


def run_plan(spec: dict, steps: list, known_only: bool = False,
             skip: int = -1) -> tuple:
    """Execute chain steps, optionally skipping one.

    Returns (ok, runner, step_lines) where ok means every action
    succeeded, every sign-in authenticated, and the final jewel read
    returned the secret. skip drops one step's actions
    (leave-one-out); known_only restricts red to learned knowledge
    plus weak guesses, failing anything else without logging.
    """
    runner = Range(spec)
    emit_noise(runner, spec)
    step_lines: list = []
    for index, step in enumerate(steps):
        before = _snapshot(runner)
        if index == skip:
            step_lines.append(set())
            continue
        for action in step["actions"]:
            result = runner.run_action(action, known_only=known_only)
            if not result.get("ok"):
                return False, runner, step_lines
            if action["tool"] == "try_credential" and \
                    not result.get("authenticated"):
                return False, runner, step_lines
        step_lines.append(_step_lines(runner, before))
    last = steps[-1]["actions"][-1] if steps and steps[-1]["actions"] \
        else {}
    if last.get("tool") != "read_file" or \
            last.get("args", {}).get("path") != spec["jewel"]["path"] or \
            last.get("args", {}).get("host") != spec["jewel"]["host"]:
        return False, runner, step_lines
    return True, runner, step_lines


def emit_noise(runner: Range, spec: dict) -> None:
    """Print background failures and requests before the intrusion."""
    noise = spec.get("noise", {})
    for failure in noise.get("failures", []):
        for _ in range(int(failure.get("count", 1))):
            runner.try_credential(failure["host"], failure["user"],
                                  "wrongpw")
    for request in noise.get("requests", []):
        runner.http_request(request["host"], request["path"])


def _formats_hold(runner: Range) -> list:
    """Check every emitted line against a real host line format."""
    errors = []
    for host, lines in runner.logs.items():
        for ref, line in lines:
            if line.startswith("signin ok"):
                pattern = LINE_FORMATS["signin_ok"]
            elif "note=no-gate" in line:
                pattern = LINE_FORMATS["signin_fail_nogate"]
            elif line.startswith("signin fail"):
                pattern = LINE_FORMATS["signin_fail"]
            elif line.startswith("file read"):
                pattern = LINE_FORMATS["file_read"]
            elif line.startswith("dir list"):
                pattern = LINE_FORMATS["dir_list"]
            elif line.startswith("request"):
                pattern = LINE_FORMATS["request"]
            else:
                errors.append(f"{ref} has unknown line shape: {line!r}")
                continue
            if not pattern.match(line):
                errors.append(f"{ref} breaks real format: {line!r}")
    return errors


def coverage(spec: dict, runner: Range, step_lines: list) -> dict:
    """Map each path step to the sensor rules firing on its lines."""
    connections = {host["name"]: set(host.get("links", []))
                   for host in spec["hosts"]}
    alerts = sensors.build_alerts(runner.logs, connections=connections,
                                  crown_jewel_host=spec["jewel"]["host"])
    table: dict = {}
    for index in range(len(step_lines)):
        refs = step_lines[index]
        rules = sorted({alert["rule"] for alert in alerts
                        if refs & set(alert.get("refs", []))})
        table[index] = rules
    return table


def validate_simulation(spec: dict) -> list:
    """Dynamic checks: solvable, ordered, shortcut-free, covered."""
    from crucible.scenarios import chain as chain_mod

    errors: list = []
    steps = chain_mod.chain_steps(spec)
    want = PATH_LENGTHS.get(spec.get("difficulty", ""), 0)
    if len(steps) != want or len(spec.get("path", [])) != want:
        errors.append(f"path must hold {want} executed steps")
        return errors

    ok, runner, step_lines = run_plan(spec, steps, known_only=True)
    if not ok:
        errors.append("chain does not reach the crown jewel")
        return errors
    errors.extend(_formats_hold(runner))
    errors.extend(shortcut_errors(spec, steps))

    # Every path step trips at least one generic sensor rule.
    table = coverage(spec, runner, step_lines)
    for index, rules in table.items():
        if not rules:
            errors.append(f"step {index} ({steps[index]['kind']}) "
                          f"trips no sensor rule")
    return errors


def shortcut_errors(spec: dict, steps: list | None = None) -> list:
    """Prove the crown jewel is reachable only through the full path.

    Leave-one-out (dropping any single step breaks the chain),
    direct reads and weak guesses with no knowledge fail, and dead
    ends cannot reach the jewel.
    """
    from crucible.scenarios import chain as chain_mod

    errors: list = []
    if steps is None:
        steps = chain_mod.chain_steps(spec)

    # Leave-one-out: dropping any single step must break the chain.
    for index in range(len(steps) - 1):
        skip_ok, _, _ = run_plan(spec, steps, known_only=True, skip=index)
        if skip_ok:
            errors.append(f"step {index} ({steps[index]['kind']}) is "
                          f"skippable: shortcut exists")

    # Direct attempts with no knowledge must fail.
    bare = Range(spec)
    direct = bare.read_file(spec["jewel"]["host"], spec["jewel"]["path"])
    if direct.get("ok"):
        errors.append("crown jewel readable without a session")
    for weak in words.WEAK_PASSWORDS:
        attempt = bare.try_credential(spec["jewel"]["host"], "anyone", weak)
        if attempt.get("authenticated"):
            errors.append("crown jewel falls to a weak guess")

    # Dead ends lead nowhere.
    for end in spec.get("dead_ends", []):
        stray = Range(spec)
        stray.http_request(end["host"], end["page_path"])
        signin = stray.try_credential(end["host"], end["account"],
                                      end["password"])
        if not signin.get("authenticated"):
            errors.append(f"{end['id']} cannot even start")
            continue
        terminal = stray.try_credential(
            end["terminal_host"], end["terminal_account"],
            end["password"])
        if not terminal.get("authenticated"):
            errors.append(f"{end['id']} terminal unreachable")
            continue
        jewel_try = stray.read_file(spec["jewel"]["host"],
                                    spec["jewel"]["path"])
        if jewel_try.get("ok"):
            errors.append(f"{end['id']} reaches the crown jewel")
    return errors
