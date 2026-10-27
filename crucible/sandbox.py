"""Sandbox controller for the exercise range.

Owns the host allowlist and the container lifecycle. Every host lookup
passes through check_host, so red team and blue team code cannot address
anything outside the sandbox, whatever the model was told.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX_DIR = ROOT / "sandbox"
COMPOSE_FILE = SANDBOX_DIR / "docker-compose.yml"
PROJECT = "crucible"

# Rendered stack for one generated spec (see scenarios/render.py).
GEN_COMPOSE_FILE = SANDBOX_DIR / "compose.gen.yml"
GEN_SPEC_FILE = SANDBOX_DIR / "spec.gen.json"

# The only hosts agents may touch. Enforced here in code.
ALLOWED_HOSTS = ("web", "app", "db", "workstation")

# Classic segments for the dashboard layout. Generated specs carry
# their own segments; the classic range predates the field.
CLASSIC_SEGMENTS = {
    "web": "dmz",
    "app": "internal",
    "db": "data",
    "workstation": "internal",
}

# Classic service chain for the dashboard conduits. The inventory
# mesh says every host reaches every other; the dashboard has always
# drawn the service chain instead, so classic keeps emitting it.
CLASSIC_LINKS = {
    "web": ["app"],
    "app": ["web", "db", "workstation"],
    "db": ["app"],
    "workstation": ["app"],
}

# Active generated spec, or None for the classic scenario. Set by the
# CLIs around a scenario's episodes; everything below follows it.
_ACTIVE_SPEC: dict | None = None


def set_active_spec(spec: dict | None) -> None:
    """Activate one generated spec, or None to return to classic."""
    global _ACTIVE_SPEC
    _ACTIVE_SPEC = dict(spec) if spec is not None else None


def active_spec() -> dict | None:
    """Return the active generated spec, or None for classic."""
    return _ACTIVE_SPEC


def active_hosts() -> tuple:
    """Return the hosts agents may touch under the active scenario."""
    if _ACTIVE_SPEC is None:
        return ALLOWED_HOSTS
    return tuple(host["name"] for host in _ACTIVE_SPEC.get("hosts", []))


def active_compose_file() -> Path:
    """Return the compose file for the active scenario."""
    if _ACTIVE_SPEC is None:
        return COMPOSE_FILE
    return GEN_COMPOSE_FILE


def layout_for_spec(spec: dict) -> dict:
    """Return the blue-visible structure of one generated spec.

    Pure helper behind scenario_layout and the lobby preview: host
    ids, roles, segments, links, weights, and the crown jewel host
    only. No vulnerabilities, accounts, passwords, or file paths.
    """
    hosts = []
    for host in (spec or {}).get("hosts", []):
        hosts.append({
            "id": host.get("name", ""),
            "role": host.get("role", ""),
            "segment": host.get("segment", "internal"),
            "links": list(host.get("links", []) or []),
            "weight": host.get("weight", 1),
        })
    return {
        "scenario_id": (spec or {}).get("scenario_id", ""),
        "hosts": hosts,
        "jewel_host": ((spec or {}).get("jewel", {}) or {}).get(
            "host", ""),
    }


def scenario_layout() -> dict:
    """Return the blue-visible range structure for the dashboard.

    Host ids, roles, segments, links, business weights, and the
    crown jewel host only. No vulnerabilities, accounts, passwords,
    or file paths. The classic range emits the same shape.
    """
    if _ACTIVE_SPEC is not None:
        return layout_for_spec(_ACTIVE_SPEC)
    inventory = blue_inventory()
    hosts = []
    for name, entry in (inventory.get("hosts", {}) or {}).items():
        hosts.append({
            "id": name,
            "role": (entry or {}).get("role", ""),
            "segment": CLASSIC_SEGMENTS.get(name, "internal"),
            "links": list(CLASSIC_LINKS.get(name, []) or []),
            "weight": (entry or {}).get("weight", 1),
        })
    return {
        "scenario_id": "classic",
        "hosts": hosts,
        "jewel_host": inventory.get("crown_jewel_host", ""),
    }


class AllowlistError(ValueError):
    """Raised when a host falls outside the sandbox allowlist."""


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot run a requested operation."""


def check_host(host: str) -> str:
    """Allow exactly the sandbox hosts, nothing else.

    Matching is strict on purpose: surrounding spaces, casing tricks, and
    dotted lookalikes all fail closed instead of resolving somewhere odd.
    """
    allowed = active_hosts()
    if not isinstance(host, str) or host not in allowed:
        raise AllowlistError(
            f"host {host!r} is outside the sandbox; "
            f"allowed hosts: {', '.join(allowed)}"
        )
    return host


@lru_cache(maxsize=1)
def _load_classic_scenario() -> dict:
    """Load the scenario definition shipped with the sandbox."""
    return json.loads((SANDBOX_DIR / "scenario.json").read_text(encoding="utf-8"))


def load_scenario() -> dict:
    """Load the active scenario definition.

    Classic by default; when a generated spec is active, returns its
    classic-shaped adapter instead (see scenarios/compat.py).
    """
    if _ACTIVE_SPEC is not None:
        from crucible.scenarios import compat

        return compat.as_classic_dict(_ACTIVE_SPEC)
    return _load_classic_scenario()


def blue_inventory() -> dict:
    """Return the blue-visible asset inventory for prompts and sensors.

    Each host maps to its role, business weight, and connected hosts,
    plus the host holding the protected data. Roles and links come from
    the scenario's blue_inventory section; weights come from
    business_weights so the two never drift apart. The result never
    carries accounts, passwords, file names, or weaknesses. Falls back
    to generic roles and full-mesh links when the section is missing.
    """
    try:
        scenario = load_scenario()
    except Exception:
        scenario = {}
    hosts = list(scenario.get("hosts", []) or list(active_hosts()))
    weights = scenario.get("business_weights", {}) or {}
    section = scenario.get("blue_inventory", {}) or {}
    entries = section.get("hosts", {}) or {}
    out: dict = {"hosts": {}, "crown_jewel_host": None}
    for host in hosts:
        entry = entries.get(host, {}) or {}
        role = str(entry.get("role", "") or "").strip() or f"{host} host"
        links = entry.get("connected_to", []) or []
        links = [peer for peer in links if peer in hosts and peer != host]
        if not links:
            links = [peer for peer in hosts if peer != host]
        try:
            weight = int(weights.get(host, 1))
        except (TypeError, ValueError):
            weight = 1
        out["hosts"][host] = {
            "role": role,
            "weight": weight,
            "connected_to": sorted(links),
        }
    jewel = section.get("crown_jewel_host") or (scenario.get("secret", {}) or {}).get("host")
    out["crown_jewel_host"] = jewel if jewel in hosts else None
    return out


def _compose(*args: str, timeout: int) -> str:
    cmd = ["docker", "compose", "-f", str(active_compose_file()),
           "-p", PROJECT, *args]
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=SANDBOX_DIR
        )
    except FileNotFoundError as exc:
        raise SandboxError("docker CLI not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"docker compose timed out: {' '.join(args)}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()[-500:]
        raise SandboxError(f"docker compose {' '.join(args)} failed: {detail}")
    return done.stdout


def _check_started(state: dict) -> dict:
    """Raise naming hosts still down after compose reports success."""
    missing = [host for host, up in state.items() if not up]
    if missing:
        raise SandboxError(
            f"sandbox failed to start: {', '.join(missing)} not running "
            "after compose up; check the docker daemon and image build output"
        )
    return state


def start() -> dict:
    """Build the images and bring the active scenario's hosts up.

    Raises SandboxError with the missing host names when any host is
    still down after compose reports success, instead of handing back
    a silent all-False map.
    """
    _compose("up", "-d", "--build", timeout=300)
    return _check_started(status())


def start_warm() -> dict:
    """Bring the active scenario's hosts up without rebuilding.

    Used when switching between generated scenarios after the first
    build, so warm switches stay fast. The toyhost image must already
    exist from an earlier start().
    """
    _compose("up", "-d", timeout=300)
    return _check_started(status())


def stop() -> dict:
    """Stop the range and remove its containers."""
    _compose("down", timeout=120)
    return {host: False for host in active_hosts()}


def reset() -> dict:
    """Return the range to a fresh state by cycling it fully."""
    stop()
    return start()


def _docker_client():
    try:
        import docker
    except ImportError as exc:
        raise SandboxError(
            "the docker package is missing; run pip install -r requirements.txt"
        ) from exc
    try:
        return docker.from_env()
    except Exception as exc:
        raise SandboxError(f"cannot reach the docker daemon: {exc}") from exc


@contextlib.contextmanager
def _client_scope(client=None):
    """Yield a docker client, closing it when this scope created it.

    Every docker.from_env() holds pool sockets, so a client created
    for one operation must close before returning. Callers that pass
    their own client keep owning it.
    """
    owned = client is None
    client = client if client is not None else _docker_client()
    try:
        yield client
    finally:
        if owned:
            close = getattr(client, "close", None)
            if callable(close):
                close()


def _container(host: str, client):
    check_host(host)
    found = client.containers.list(
        filters={
            "label": [
                f"com.docker.compose.project={PROJECT}",
                f"com.docker.compose.service={host}",
            ]
        }
    )
    if not found:
        raise SandboxError(f"sandbox host {host!r} is not running")
    return found[0]


def is_running(host: str) -> bool:
    """Report whether a host's container is up. Never raises for docker
    trouble; anything uncertain reads as not running."""
    try:
        with _client_scope() as client:
            _container(host, client)
    except (AllowlistError, SandboxError):
        return False
    return True


def status() -> dict:
    """Map each sandbox host to its running state."""
    return {host: is_running(host) for host in active_hosts()}


def exec(host: str, cmd: list, timeout: int = 30) -> dict:
    """Run a command inside one sandbox host and capture both streams.

    Returns exit_code plus output, stdout, and stderr as text. Output
    mirrors stdout so existing callers keep working; stderr stays separate
    so failures can show what the command actually printed.
    """
    check_host(host)
    with _client_scope() as client:
        container = _container(host, client)
        try:
            exit_code, output = container.exec_run(cmd, demux=True)
        except Exception as exc:
            raise SandboxError(f"exec on {host!r} failed: {exc}") from exc
    if isinstance(output, tuple):
        out_bytes, err_bytes = output
    else:  # tolerate clients that ignore demux and merge the streams
        out_bytes, err_bytes = output, None
    stdout = out_bytes.decode("utf-8", errors="replace") if out_bytes else ""
    stderr = err_bytes.decode("utf-8", errors="replace") if err_bytes else ""
    return {"exit_code": exit_code, "output": stdout, "stdout": stdout,
            "stderr": stderr}


# Episode log scope: per-host line counts recorded when an episode
# starts. While set, container_logs returns only lines logged after
# the offset, so one exercise never sees another's log lines.
_LOG_OFFSETS: dict = {}


def note_log_offsets(hosts=None) -> dict:
    """Record each host's current log length as this episode's scope start.

    Later container_logs calls return only newer lines. Returns a copy
    of the recorded offsets.
    """
    targets = list(hosts) if hosts else list(active_hosts())
    offsets = {}
    for host in targets:
        offsets[check_host(host)] = len(_raw_logs(host, tail=None))
    _LOG_OFFSETS.update(offsets)
    return dict(offsets)


def clear_log_offsets() -> None:
    """Drop any recorded episode scope, restoring full-stream reads."""
    _LOG_OFFSETS.clear()


def log_offsets() -> dict:
    """Return a copy of the recorded episode log offsets."""
    return dict(_LOG_OFFSETS)


def _raw_logs(host: str, tail: int | None) -> list:
    """Return stdout lines from one sandbox host, ignoring any scope."""
    check_host(host)
    with _client_scope() as client:
        container = _container(host, client)
        try:
            if tail is None:
                raw = container.logs()
            else:
                raw = container.logs(tail=max(1, tail))
        except Exception as exc:
            raise SandboxError(f"logs for {host!r} failed: {exc}") from exc
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return [line for line in raw.splitlines() if line.strip()]


def container_logs(host: str, tail: int | None = 100) -> list:
    """Return stdout lines from one sandbox host.

    tail=None returns every line. While an episode scope is recorded
    (see note_log_offsets), only lines logged after the scope start
    are returned, so ids computed from this list restart at 0 per
    episode and stay stable as logs grow. With no scope recorded,
    tail=None returns the full stream.
    """
    check_host(host)
    if host in _LOG_OFFSETS:
        scoped = _raw_logs(host, tail=None)[_LOG_OFFSETS[host]:]
        if tail is None:
            return scoped
        return scoped[-max(1, tail):]
    return _raw_logs(host, tail)


def _range_network_name(container) -> str | None:
    container.reload()
    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    for name in networks:
        if name.endswith("_range") or name == "range":
            return name
    return None


def is_isolated(host: str) -> bool:
    """Report whether a host is detached from the range network."""
    with _client_scope() as client:
        container = _container(host, client)
        return _range_network_name(container) is None


def isolate(host: str) -> dict:
    """Detach one host from the range network so peers cannot reach it."""
    with _client_scope() as client:
        container = _container(host, client)
        network_name = _range_network_name(container)
        if network_name is None:
            return {"host": host, "isolated": True, "note": "already detached"}
        try:
            client.networks.get(network_name).disconnect(container)
        except Exception as exc:
            raise SandboxError(f"isolate {host!r} failed: {exc}") from exc
    return {"host": host, "isolated": True}


def reattach(host: str) -> dict:
    """Attach one host back to the range network."""
    with _client_scope() as client:
        container = _container(host, client)
        if _range_network_name(container) is not None:
            return {"host": host, "isolated": False, "note": "already attached"}
        try:
            client.networks.get(f"{PROJECT}_range").connect(container)
        except Exception as exc:
            raise SandboxError(f"reattach {host!r} failed: {exc}") from exc
    return {"host": host, "isolated": False}
