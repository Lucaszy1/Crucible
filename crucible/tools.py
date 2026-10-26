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
    "list_files",
    "report_finding",
    "read_logs",
    "list_processes",
    "wait",
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
_state = {"authed": set(), "blocked": [], "findings": []}


def reset_state() -> None:
    """Clear interim exercise state (signed-in hosts, blocks, findings)."""
    _state["authed"].clear()
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


def _valid_dir_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"path must be absolute: {path!r}")
    if len(path) > MAX_PATH_LEN or any(ch.isspace() for ch in path):
        raise ValueError(f"path rejected: {path!r}")
    clean = posixpath.normpath(path)
    roots = tuple(_scenario().get("readable_roots", ["/"]))
    for root in roots:
        bare = root.rstrip("/") or "/"
        if clean == bare or clean.startswith(root):
            return clean
    raise ValueError(f"path {path!r} is outside readable roots {roots}")


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


def _relay_script(url: str, method: str, data_repr: str) -> str:
    """Build the python snippet the workstation relay runs.

    Kept as real lines, not one semicolon chain: compound statements such
    as try and except cannot follow a semicolon, and joining them that way
    made every relay call die with a syntax error.
    """
    lines = [
        f"URL={url!r}",
        f"METHOD={method!r}",
        f"DATA={data_repr}",
        "import json,urllib.request,urllib.error",
        "req=urllib.request.Request(URL, data=DATA, method=METHOD)",
        "opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))",
        "out={'status':None,'body':'','error':None}",
        "try:",
        "    r=opener.open(req,timeout=8)",
        "    out['status']=r.status",
        "    out['body']=r.read(4096).decode('utf-8','replace')",
        "except urllib.error.HTTPError as e:",
        "    out['status']=e.code",
        "    out['body']=e.read(4096).decode('utf-8','replace')",
        "except Exception as e:",
        "    out['error']=str(e)[:300]",
        "print(json.dumps(out))",
    ]
    return "\n".join(lines) + "\n"


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
    script = _relay_script(url, method, data_repr)
    try:
        done = sandbox.exec("workstation", ["python", "-c", script])
    except SandboxError as exc:
        return {"ok": False, "error": str(exc)}
    stdout = done["output"]
    stderr = done["stderr"]
    try:
        parsed = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": (
            "relay returned an unreadable reply "
            f"(exit={done['exit_code']} "
            f"stdout={stdout[:300]!r} stderr={stderr[:300]!r})")}
    if parsed.get("error") is not None and parsed.get("status") is None:
        return {"ok": False, "error": parsed["error"]}
    return {"ok": True, "host": host, "status": parsed.get("status"),
            "body": parsed.get("body", "")}


def try_credential(host: str, user: str, password: str) -> dict:
    """Try a toy sign-in against the live gate inside one sandbox host.

    The verdict comes from the container itself, which holds the current
    credential files. The scenario file only documents the shipped default;
    nothing here compares against it. A hit marks the host signed-in for
    later file reads.
    """
    sandbox.check_host(host)
    if not user or not isinstance(user, str):
        raise ValueError("user must be a non-empty string")
    if not password or not isinstance(password, str):
        raise ValueError("password must be a non-empty string")
    if not sandbox.is_running(host):
        return {"ok": False, "error": f"host {host!r} is not up"}
    try:
        done = sandbox.exec(host, ["signin", user, password])
    except SandboxError as exc:
        return {"ok": False, "error": str(exc)}
    if "AUTH_OK" in done["output"]:
        _state["authed"].add(host)
        return {"ok": True, "host": host, "authenticated": True}
    if "AUTH_FAIL" in done["output"]:
        return {"ok": True, "host": host, "authenticated": False}
    return {"ok": True, "host": host, "authenticated": False,
            "note": f"no sign-in gate on {host!r}"}


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


def list_files(host: str, path: str) -> dict:
    """List names under a directory inside one sandbox host.

    Paths stay pinned under the scenario's readable roots, including the
    roots themselves. Secret roots need a prior successful sign-in on that
    host, the same as read_file. Read-only: runs a plain directory listing.
    """
    sandbox.check_host(host)
    clean = _valid_dir_path(path)
    if (clean == "/secret" or clean.startswith("/secret/")) and host not in _state["authed"]:
        return {"ok": False, "error": f"sign-in on {host!r} is required first"}
    try:
        done = sandbox.exec(host, ["ls", "-1", clean])
    except SandboxError as exc:
        return {"ok": False, "error": str(exc)}
    if done["exit_code"] != 0:
        return {"ok": False, "error": f"cannot list {clean} on {host!r}"}
    files = [line for line in done["output"].splitlines() if line.strip()]
    return {"ok": True, "host": host, "path": clean, "files": files}


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


def wait(reason: str | None = None, **_ignored) -> dict:
    """Do nothing for one turn so blue is not forced to poll."""
    out = {"ok": True, "waited": True}
    if reason:
        out["reason"] = str(reason)[:200]
    return out


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
    toy app honors. Where a sign-in gate exists, the patch also rotates the
    live container-side default so the shipped one stops working.
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
    try:
        gate = sandbox.exec(host, ["rotate-signin"])
    except SandboxError:
        gate = None
    if gate is not None and "ROTATED_OK" in gate["output"]:
        _state["authed"].discard(host)
        detail = "patch flag applied; sign-in rotated on the host"
    return {"ok": True, "host": host, "service": service, "detail": detail}


def restore_host(host: str) -> dict:
    """Bring one sandbox host back to a clean attached state.

    Reattaches the range network, clears the patch flag, drops any
    signed-in session, and puts a rotated sign-in back to the shipped
    default so the host matches a fresh one.
    """
    sandbox.check_host(host)
    try:
        net = sandbox.reattach(host)
        done = sandbox.exec(host, ["rm", "-f", PATCH_FLAG])
    except SandboxError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        gate = sandbox.exec(host, ["reset-signin"])
        gate_reset = "RESET_OK" in gate["output"]
    except SandboxError:
        gate_reset = False
    _state["authed"].discard(host)
    cleaned = done["exit_code"] == 0
    return {"ok": True, "host": host, "reattached": not net.get("isolated", True),
            "patch_cleared": cleaned, "sign_in_reset": gate_reset}
