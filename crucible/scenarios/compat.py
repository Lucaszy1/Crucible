"""Classic-shaped view of a generated spec.

The classic tooling (tools.py gates, scripted agents, scoring,
inventory) reads the scenario through sandbox.load_scenario. When a
generated spec is active, sandbox serves this adapter instead, so
every consumer follows the spec with no per-consumer branching.
Keys mirror sandbox/scenario.json; spec-only extras (entry_host,
gated_paths, scenario identity) ride along for the few callers that
need them.
"""

from __future__ import annotations

from crucible.scenarios import words


def _spec_accounts(spec: dict) -> dict:
    """Map each host to its {user: password} accounts."""
    accounts: dict = {}
    for step in spec.get("path", []):
        kind = step.get("kind")
        if kind in ("weak_creds", "stale_reuse", "signin_known"):
            accounts.setdefault(step["host"], {})[step["account"]] = \
                step["password"]
        elif kind in ("config_creds", "backup_creds"):
            accounts.setdefault(step["reveals_host"], {})[
                step["reveals_account"]] = step["reveals_password"]
    for end in spec.get("dead_ends", []):
        accounts.setdefault(end["host"], {})[end["account"]] = \
            end["password"]
        accounts.setdefault(end["terminal_host"], {})[
            end["terminal_account"]] = end["password"]
    return accounts


def _gated_paths(spec: dict) -> dict:
    """Map each host to the file paths whose access needs a session.

    Exactly the simulator's rule: planted files plus their parent
    dirs. The /secret tree is always gated on top of this.
    """
    gated: dict = {}
    for step in spec.get("path", []):
        if step.get("kind") not in ("config_creds", "backup_creds",
                                    "jewel_read"):
            continue
        host, path = step.get("host"), step.get("path")
        if not host or not path:
            continue
        parent = path.rsplit("/", 1)[0] or "/"
        gated.setdefault(host, set()).update((path, parent))
    return {host: sorted(paths) for host, paths in gated.items()}


def as_classic_dict(spec: dict) -> dict:
    """Render one spec as a classic-shaped scenario dict."""
    hosts = [host["name"] for host in spec.get("hosts", [])]
    roles = {host["name"]: host for host in spec.get("hosts", [])}
    accounts = _spec_accounts(spec)
    http_hosts = [name for name in hosts
                  if roles[name].get("role") in words.HTTP_ROLES]
    first_page = next((step for step in spec.get("path", [])
                       if step.get("kind") == "exposed_page"), {})
    first_file = next((step for step in spec.get("path", [])
                       if step.get("kind") in ("config_creds",
                                              "backup_creds")), {})
    shipped = {}
    for host in hosts:
        users = accounts.get(host, {})
        if not users:
            continue
        first = sorted(users)[0]
        shipped[host] = {"user": first, "pass": users[first],
                         "users": sorted(users)}
    return {
        "hosts": hosts,
        "ports": {host: 8000 for host in http_hosts},
        "services": {
            host: {"service": (roles[host].get("services")
                               or ["toyhost"])[0]}
            for host in hosts
        },
        "shipped_defaults": shipped,
        "secret": {"host": spec["jewel"]["host"],
                   "path": spec["jewel"]["path"]},
        "app_config": {"host": first_file.get("host", ""),
                       "path": first_file.get("path", "")},
        "readable_roots": ["/"],
        "admin_endpoint": {"host": first_page.get("host", ""),
                           "path": first_page.get("page_path", "")},
        "business_weights": {host["name"]: host.get("weight", 1)
                              for host in spec.get("hosts", [])},
        "blue_inventory": spec.get("blue_inventory", {}),
        # Spec-only extras below this line.
        "entry_host": spec.get("entry_host", ""),
        "gated_paths": _gated_paths(spec),
        "scenario_id": spec.get("scenario_id", ""),
        "seed": spec.get("seed"),
        "difficulty": spec.get("difficulty", ""),
    }
