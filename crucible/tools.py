"""The only actions agents can take.

Red team tools probe the sandbox; blue team tools inspect and contain.
Every host argument passes through the sandbox allowlist first, so an
off-range target raises AllowlistError before anything else runs. Day two
wires these into the orchestrator turn loop with event logging.
"""

from __future__ import annotations

import ipaddress
import json
import posixpath

from crucible import sandbox
from crucible.sandbox import AllowlistError, SandboxError  # noqa: F401 (re-exported)

__all__ = [
    "AllowlistError",
    "SandboxError",
    "scan_host",
    "http_request",
    "try_credential",
    "read_file",
    "report_finding",
    "read_logs",
    "list_processes",
    "block_ip",
    "isolate_host",
    "patch_service",
    "restore_host",
    "reset_state",
]

PATCH_FLAG = "/tmp/patched"
MAX_BODY = 8192
MAX_PATH_LEN = 512

# Interim exercise state. The orchestrator takes ownership of this on day two.
_state = {"authed": set(), "rotated": set(), "blocked": [], "findings": []}


def reset_state() -> None:
    """Clear interim exercise state (signed-in hosts, blocks, findings)."""
    _state["authed"].clear()
    _state["rotated"].clear()
    _state["blocked"].clear()
    _state["findings"].clear()


def _scenario() -> dict:
    return sandbox.load_scenario()


def _valid_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"path must be absolute: {path!r}")
    if len(path) > MAX_PATH_LEN or any(ch.isspace() for ch in path):
        raise ValueError(f"path rejected: {path!r}")
    clean = posixpath.normpath(path)
    roots = tuple(_scenario().get("readable_roots", ["/"]))
    if not clean.startswith(roots):
        raise ValueError(f"path {path!r} is outside readable roots {roots}")
    return clean


# Red team tools.


def scan_host(host: str) -> dict:
    """Report whether a sandbox host is up and which ports it declares."""
    sandbox.check_host(host)
    running = sandbox.is_running(host)
    if not running:
        return {"ok": True, "host": host, "running": False, "reachable": False,
                "ports": [], "note": "host is not up"}
    try:
        isolated = sandbox.is_isolated(host)
    except SandboxError:
        isolated = False
    port = _scenario().get("ports", {}).get(host)
    return {
        "ok": True,
        "host": host,
        "running": True,
        "reachable": not isolated,
        "ports": [port] if port else [],
        "isolated": isolated,
    }


_HTTP_SCRIPT = (
    "import json,urllib.request,urllib.error;"
    "req=urllib.request.Request(URL, data=DATA, method=METHOD);"
    "out={'status':None,'body':'','error':None};"
    "try:\n"
    "  r=urllib.request.urlopen(req,timeout=8);"
    "  out['status']=r.status;"
    "  out['body']=r.read(4096).decode('utf-8','replace');"
    "except urllib.error.HTTPError as e:\n"
    "  out['status']=e.code;"
    "  out['body']=e.read(4096).decode('utf-8','replace');"
    "except Exception as e:\n"
    "  out['error']=str(e)[:300];"
    "print(json.dumps(out))"
)


def http_request(host: str, path: str, method: str = "GET", body: str | None = None) -> dict:
    """Send one HTTP request to a sandbox host through the range network.

    The request runs from the workstation container, so it exercises the
    real internal network path, including isolation cutoffs.
    """
    sandbox.check_host(host)
    if method not in ("GET", "POST"):
        raise ValueError(f"method must be GET or POST: {method!r}")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"path must start with /: {path!r}")
    if len(path) > MAX_PATH_LEN or any(ch.isspace() for ch in path):
        raise ValueError(f"path rejected: {path!r}")
    if "/../" in path or path.endswith("/.."):
        raise ValueError(f"path rejected: {path!r}")
    if body is not None and (not isinstance(body, str) or len(body) > MAX_BODY):
        raise ValueError("body must be a string under 8 KB")
    port = _scenario().get("ports", {}).get(host)
    if not port:
        return {"ok": False, "error": f"no http service declared on {host!r}"}
    if not sandbox.is_running(host):
        return {"ok": False, "error": f"host {host!r} is not up"}
    if not sandbox.is_running("workstation"):
        return {"ok": False, "error": "workstation relay is not up"}
    url = f"http://{host}:{port}{path}"
    data_repr = repr(body.encode("utf-8")) if body is not None else "None"
    script = f"URL={url!r};METHOD={method!r};DATA={data_repr};" + _HTTP_SCRIPT
    try:
        done = sandbox.exec("workstation", ["python", "-c", script])
    except SandboxError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        parsed = json.loads(done["output"].strip().splitlines()[-1])
    except (ValueError, IndexError, KeyError):
        return {"ok": False, "error": "relay returned an unreadable reply"}
    if parsed.get("error") is not None and parsed.get("status") is None:
        return {"ok": False, "error": parsed["error"]}
    return {"ok": True, "host": host, "status": parsed.get("status"),
            "body": parsed.get("body", "")}


def try_credential(host: str, user: str, password: str) -> dict:
    """Try a toy sign-in against one sandbox host.

    Credentials here are planted defaults from the scenario file, never
    real secrets. A hit marks the host signed-in for later file reads.
    """
    sandbox.check_host(host)
    if not user or not isinstance(user, str):
        raise ValueError("user must be a non-empty string")
    if not password or not isinstance(password, str):
        raise ValueError("password must be a non-empty string")
    if host in _state["rotated"]:
        return {"ok": True, "host": host, "authenticated": False,
                "note": "default sign-in was rotated by a patch"}
    expected = _scenario().get("toy_credentials", {}).get(host)
    good = bool(expected) and user == expected.get("user") and password == expected.get("pass")
    if good:
        _state["authed"].add(host)
    return {"ok": True, "host": host, "authenticated": good}


def read_file(host: str, path: str) -> dict:
    """Read a file from inside one sandbox host.

    Secret roots need a prior successful sign-in on that host. Paths stay
    pinned under the scenario's readable roots.
    """
    sandbox.check_host(host)
    clean = _valid_path(path)
    if clean.startswith("/secret/") and host not in _state["authed"]:
        return {"ok": False, "error": f"sign-in on {host!r} is required first"}
    try:
        done = sandbox.exec(host, ["cat", clean])
    except SandboxError as exc:
        return {"ok": False, "error": str(exc)}
    if done["exit_code"] != 0:
        return {"ok": False, "error": f"cannot read {clean} on {host!r}"}
    return {"ok": True, "host": host, "path": clean,
            "content": done["output"][:MAX_BODY]}


def report_finding(text: str) -> dict:
    """Record a note from either team and hand back its id."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("finding text must be a non-empty string")
    finding_id = f"finding-{len(_state['findings']) + 1}"
    _state["findings"].append({"id": finding_id, "text": text.strip()[:2000]})
    return {"ok": True, "id": finding_id}


# Blue team tools.


def read_logs(host: str, n: int = 50) -> dict:
    """Return recent log lines from one sandbox host with line ids."""
    sandbox.check_host(host)
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError("n must be an integer")
    n = max(1, min(200, n))
    try:
        lines = sandbox.container_logs(host, tail=n)
    except SandboxError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "host": host,
            "lines": [{"id": f"{host}:{i}", "line": line}
                      for i, line in enumerate(lines)]}


def list_processes(host: str) -> dict:
    """List processes running inside one sandbox host."""
    sandbox.check_host(host)
    try:
        done = sandbox.exec(host, ["ps"])
    except SandboxError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "host": host,
            "processes": [line for line in done["output"].splitlines() if line.strip()]}


def block_ip(ip: str) -> dict:
    """Record a block for one IPv4 address in the exercise firewall state."""
    if not isinstance(ip, str):
        raise ValueError(f"not an IPv4 address: {ip!r}")
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        raise ValueError(f"not an IPv4 address: {ip!r}") from None
    if parsed.version != 4:
        raise ValueError(f"not an IPv4 address: {ip!r}")
    if ip not in _state["blocked"]:
        _state["blocked"].append(ip)
    return {"ok": True, "ip": ip, "blocked": list(_state["blocked"])}


def isolate_host(host: str) -> dict:
    """Detach one sandbox host from the range network."""
    sandbox.check_host(host)
    try:
        done = sandbox.isolate(host)
    except SandboxError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **done}


def patch_service(host: str, service: str) -> dict:
    """Apply the toy fix for one service on one sandbox host.

    On the web host this closes the exposed admin page through a flag the
    toy app honors. Where a default sign-in exists, the patch also rotates
    it so the planted default stops working.
    """
    sandbox.check_host(host)
    expected = (_scenario().get("services", {}).get(host) or {}).get("service")
    if service != expected:
        raise ValueError(f"unknown service {service!r} on {host!r}")
    if not sandbox.is_running(host):
        return {"ok": False, "error": f"host {host!r} is not up"}
    try:
        done = sandbox.exec(host, ["touch", PATCH_FLAG])
    except SandboxError as exc:
        return {"ok": False, "error": str(exc)}
    if done["exit_code"] != 0:
        return {"ok": False, "error": f"patch on {host!r} did not apply"}
    detail = "patch flag applied"
    if host in _scenario().get("toy_credentials", {}):
        _state["rotated"].add(host)
        _state["authed"].discard(host)
        detail = "patch flag applied; default sign-in rotated"
    return {"ok": True, "host": host, "service": service, "detail": detail}


def restore_host(host: str) -> dict:
    """Bring one sandbox host back to a clean attached state.

    Reattaches the range network, clears the patch flag, and drops any
    signed-in session. A rotated sign-in stays rotated.
    """
    sandbox.check_host(host)
    try:
        net = sandbox.reattach(host)
        done = sandbox.exec(host, ["rm", "-f", PATCH_FLAG])
    except SandboxError as exc:
        return {"ok": False, "error": str(exc)}
    _state["authed"].discard(host)
    cleaned = done["exit_code"] == 0
    return {"ok": True, "host": host, "reattached": not net.get("isolated", True),
            "patch_cleared": cleaned}
