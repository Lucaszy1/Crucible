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

# The only hosts agents may touch. Enforced here in code.
ALLOWED_HOSTS = ("web", "app", "db", "workstation")


class AllowlistError(ValueError):
    """Raised when a host falls outside the sandbox allowlist."""


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot run a requested operation."""


def check_host(host: str) -> str:
    """Allow exactly the sandbox hosts, nothing else.

    Matching is strict on purpose: surrounding spaces, casing tricks, and
    dotted lookalikes all fail closed instead of resolving somewhere odd.
    """
    if not isinstance(host, str) or host not in ALLOWED_HOSTS:
        raise AllowlistError(
            f"host {host!r} is outside the sandbox; "
            f"allowed hosts: {', '.join(ALLOWED_HOSTS)}"
        )
    return host


@lru_cache(maxsize=1)
def load_scenario() -> dict:
    """Load the scenario definition shipped with the sandbox."""
    return json.loads((SANDBOX_DIR / "scenario.json").read_text(encoding="utf-8"))


def _compose(*args: str, timeout: int) -> str:
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", PROJECT, *args]
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


def start() -> dict:
    """Build the images and bring the four hosts up.

    Raises SandboxError with the missing host names when any host is
    still down after compose reports success, instead of handing back
    a silent all-False map.
    """
    _compose("up", "-d", "--build", timeout=300)
    state = status()
    missing = [host for host, up in state.items() if not up]
    if missing:
        raise SandboxError(
            f"sandbox failed to start: {', '.join(missing)} not running "
            "after compose up; check the docker daemon and image build output"
        )
    return state


def stop() -> dict:
    """Stop the range and remove its containers."""
    _compose("down", timeout=120)
    return {"web": False, "app": False, "db": False, "workstation": False}


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
    return {host: is_running(host) for host in ALLOWED_HOSTS}


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


def container_logs(host: str, tail: int | None = 100) -> list:
    """Return stdout lines from one sandbox host.

    tail=None returns every line. read_logs uses that to number ids from
    the first line logged, so ids stay stable as logs grow.
    """
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
