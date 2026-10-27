"""Config-driven toy host: one image plays any generated spec host.

Reads the mounted spec JSON plus TOYHOST_ID, then behaves per the
spec: serves the host's web pages (leaky admin page, decoys, status
pages), plants its files, and writes the credential and gate state
the shell handlers (signin, read-file, list-files, rotate-signin,
reset-signin) enforce. Log lines use the exact classic formats and
print to this process's stdout, which is PID 1's stdout inside the
container. Passwords never reach the logs.

Layout under TOYHOST_STATE (/run/toyhost by default):
  passwd.shipped  pristine user:password lines (only with accounts)
  passwd          live copy the signin handler checks (only with accounts)
  session         touched on any successful sign-in; gates file reads
  gated.txt       file paths whose list/read needs the session flag
  role            the host's role (workstation gates always fail)
"""

from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

PORT = 8000
PATCH_FLAG = "/tmp/patched"
JEWEL_DIR = "/secret"

SPEC_PATH = os.environ.get("TOYHOST_SPEC", "/run/toyhost/spec.json")
STATE_DIR = os.environ.get("TOYHOST_STATE", "/run/toyhost")
HOST_ID = os.environ.get("TOYHOST_ID", "")
NOISE_ON = os.environ.get("TOYHOST_NOISE", "1") == "1"


def load_spec(path: str = SPEC_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def host_config(spec: dict, host: str) -> dict:
    """Collect everything this host serves, holds, or gates."""
    entry = next(item for item in spec["hosts"] if item["name"] == host)
    pages: dict = {}
    accounts: dict = {}
    files: dict = {}
    for step in spec.get("path", []):
        kind = step.get("kind")
        if kind == "exposed_page" and step.get("host") == host:
            pages[step["page_path"]] = {
                "leak_host": step["leaks_host"],
                "leak_account": step["leaks_account"],
            }
        elif kind in ("weak_creds", "stale_reuse", "signin_known") \
                and step.get("host") == host:
            accounts[step["account"]] = step["password"]
        elif kind in ("config_creds", "backup_creds"):
            if step.get("reveals_host") == host:
                accounts[step["reveals_account"]] = \
                    step["reveals_password"]
            if step.get("host") == host:
                files[step["path"]] = (
                    f"host={step['reveals_host']} "
                    f"user={step['reveals_account']} "
                    f"password={step['reveals_password']}")
        elif kind == "jewel_read" and step.get("host") == host:
            files[step["path"]] = f"TOY-SECRET-{spec['scenario_id']}"
    for end in spec.get("dead_ends", []):
        if end.get("host") == host:
            pages[end["page_path"]] = {
                "leak_host": end["host"],
                "leak_account": end["account"],
            }
            accounts[end["account"]] = end["password"]
        if end.get("terminal_host") == host:
            accounts[end["terminal_account"]] = end["password"]
    decoys = spec.get("decoys", {}) or {}
    for item in decoys.get("admin_pages", []) or []:
        if item.get("host") == host:
            pages.setdefault(item["path"], {"decoy": True})
    for item in decoys.get("status_pages", []) or []:
        if item.get("host") == host:
            pages.setdefault(item["path"], {"status": True})
    gated = set()
    for path in files:
        gated.add(path)
        parent = path.rsplit("/", 1)[0] or "/"
        gated.add(parent)
    noise = spec.get("noise", {}) or {}
    mine = {
        "failures": [entry for entry in noise.get("failures", []) or []
                     if entry.get("host") == host],
        "requests": [entry for entry in noise.get("requests", []) or []
                     if entry.get("host") == host],
    }
    return {
        "host": host,
        "role": entry.get("role", ""),
        "service": (entry.get("services") or ["toyhost"])[0],
        "http": entry.get("role") in ("web portal", "api"),
        "pages": pages,
        "accounts": accounts,
        "files": files,
        "gated": sorted(gated),
        "noise": mine,
        "scenario_id": spec.get("scenario_id", ""),
    }


def route_request(method: str, raw_path: str, config: dict,
                  patched: bool) -> tuple:
    """Return (status, body) for one request, without logging.

    Pure function of its inputs so tests can drive it in-process.
    """
    path = urlsplit(raw_path).path or "/"
    page = (config.get("pages") or {}).get(path)
    service = config.get("service", "toyhost")
    if method == "POST":
        return 404, {"error": "not found"}
    if page is not None and ("leak_host" in page or "decoy" in page) \
            and patched:
        return 404, {"error": "not found"}
    if page is not None and "leak_host" in page:
        hint = (f"internal host {page['leak_host']!r} runs reports "
                f"as {page['leak_account']!r}")
        return 200, {"admin": True,
                     "note": "toy misconfiguration: admin page left open",
                     "hint": hint}
    if page is not None and "decoy" in page:
        return 200, {"admin": True,
                     "note": "moved; nothing to see here"}
    if path == "/" or (page is not None and "status" in page):
        return 200, {"service": service, "state": "up"}
    return 404, {"error": "not found"}


class Handler(BaseHTTPRequestHandler):
    """Serve this host's pages; every request logs one line."""

    config: dict = {}

    def log_message(self, *args):
        pass  # keep stdout tidy; request lines below use our own format

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _log_request(self, code, path):
        print(
            f"request from={self.client_address[0]} "
            f"method={self.command} path={path} status={code}",
            flush=True,
        )

    def _handle(self):
        path = urlsplit(self.path).path or "/"
        patched = os.path.exists(PATCH_FLAG)
        code, payload = route_request(self.command, path, self.config,
                                      patched)
        if self.command == "POST":
            length = int(self.headers.get("Content-Length", 0) or 0)
            self.rfile.read(min(length, 8192))
        self._send(code, payload)
        self._log_request(code, path)

    do_GET = _handle
    do_POST = _handle


def serve_forever(config: dict) -> None:
    """Run the HTTP server on 8000 until the process dies."""
    Handler.config = config
    print(f"toyhost {config['host']} listening on {PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


def noise_lines_for_host(spec: dict, host: str, seed: str,
                         clients: int = 4) -> list:
    """Build this host's background noise lines (pure, seeded).

    One signin-fail line per background failure and one request line
    per background request, attributed to fake client addresses so
    they never collide with the controller's lines. The live loop
    paces these out; the parity harness disables noise instead.
    """
    rng = random.Random(seed)
    config = host_config(spec, host)
    ips = [f"10.9.0.{number}"
           for number in rng.sample(range(2, 250), clients)]
    lines = []
    for failure in config["noise"]["failures"]:
        user = failure.get("user", "guest")
        for _ in range(int(failure.get("count", 1))):
            lines.append(f"signin fail user={user} "
                         f"from={rng.choice(ips)}")
    for request in config["noise"]["requests"]:
        lines.append(f"request from={rng.choice(ips)} method=GET "
                     f"path={request.get('path', '/')} status=200")
    rng.shuffle(lines)
    return lines


def noise_loop(spec: dict, host: str, seed: str) -> None:
    """Print background noise lines forever, a few seconds apart."""
    rng = random.Random(f"{seed}:pace")
    lines = noise_lines_for_host(spec, host, seed)
    if not lines:
        return
    while True:
        for line in lines:
            time.sleep(rng.uniform(4.0, 12.0))
            print(line, flush=True)


def write_state(config: dict, state_dir: str = STATE_DIR) -> None:
    """Write the credential, gate, and role files handlers read.

    Hosts with no accounts get no passwd files at all: the
    clean-start check treats any passwd file there as an unexpected
    gate. The handlers already tolerate the absence (NO_GATE on
    workstations, AUTH_FAIL elsewhere, ROTATE/RESET_NONE).
    """
    os.makedirs(state_dir, exist_ok=True)
    accounts = config.get("accounts", {}) or {}
    if accounts:
        shipped = "".join(f"{user}:{password}\n"
                          for user, password in sorted(accounts.items()))
        with open(os.path.join(state_dir, "passwd.shipped"), "w",
                  encoding="utf-8") as handle:
            handle.write(shipped)
        with open(os.path.join(state_dir, "passwd"), "w",
                  encoding="utf-8") as handle:
            handle.write(shipped)
    with open(os.path.join(state_dir, "role"), "w",
              encoding="utf-8") as handle:
        handle.write(str(config.get("role", "")))
    with open(os.path.join(state_dir, "gated.txt"), "w",
              encoding="utf-8") as handle:
        handle.write("".join(f"{path}\n" for path in config["gated"]))


def plant_files(config: dict) -> None:
    """Write this host's spec files at their absolute paths."""
    for path, content in (config.get("files", {}) or {}).items():
        parent = path.rsplit("/", 1)[0] or "/"
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)


def main() -> int:
    if not HOST_ID:
        print("TOYHOST_ID is required", file=sys.stderr)
        return 1
    try:
        spec = load_spec()
    except (OSError, ValueError) as exc:
        print(f"cannot load spec: {exc}", file=sys.stderr)
        return 1
    names = [item["name"] for item in spec.get("hosts", [])]
    if HOST_ID not in names:
        print(f"host {HOST_ID!r} is not in the spec", file=sys.stderr)
        return 1
    config = host_config(spec, HOST_ID)
    write_state(config)
    plant_files(config)
    seed = os.environ.get("TOYHOST_SEED",
                          f"{config['scenario_id']}:{HOST_ID}")
    print(f"toyhost {HOST_ID} ready ({config['role']})", flush=True)
    if config["http"]:
        thread = threading.Thread(target=serve_forever, args=(config,),
                                  daemon=True)
        thread.start()
    if NOISE_ON:
        thread = threading.Thread(target=noise_loop,
                                  args=(spec, HOST_ID, seed), daemon=True)
        thread.start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    raise SystemExit(main())
