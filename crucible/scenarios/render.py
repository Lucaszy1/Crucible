"""Render a generated spec into a Docker compose stack.

render(spec) returns the compose file text: one toyhost service per
spec host on an internal-only network, no published ports, CPU and
memory limits per container, container names from the spec, and the
spec JSON mounted read-only into every container. write_stack(spec)
writes the compose file plus the spec JSON next to the classic stack;
containers are capped at 10.
"""

from __future__ import annotations

import json
import re

from crucible.scenarios.schema import MAX_HOSTS

COMPOSE_NAME = "compose.gen.yml"
SPEC_NAME = "spec.gen.json"
IMAGE = "crucible-toyhost:stage2"
PORT = 8000

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9_.-]*[a-z0-9])?$")


def render(spec: dict) -> str:
    """Render the compose file text for one spec."""
    hosts = [host["name"] for host in spec.get("hosts", [])]
    if len(hosts) > MAX_HOSTS:
        raise ValueError(f"spec has {len(hosts)} hosts, cap is {MAX_HOSTS}")
    if not hosts:
        raise ValueError("spec has no hosts")
    for name in hosts:
        if not _NAME_RE.match(name):
            raise ValueError(f"host {name!r} is not a valid service name")
    scenario_id = spec.get("scenario_id", "")
    lines = [
        f"# Generated stack for {scenario_id}. Internal-only network,",
        "# no published ports. Written by crucible/scenarios/render.py;",
        "# do not edit by hand.",
        "services:",
    ]
    # One image plays every host, so only the first service builds it:
    # a build stanza on every service makes compose build the same tag
    # concurrently, and the parallel retags race (AlreadyExists / no
    # such image). The rest reuse the local image by name.
    for index, name in enumerate(hosts):
        lines.extend([
            f"  {name}:",
        ])
        if index == 0:
            lines.append("    build: ./toyhost")
        lines.extend([
            f"    image: {IMAGE}",
            f"    container_name: crucible-{name}",
            f"    hostname: {name}",
            "    environment:",
            f"      TOYHOST_ID: {name}",
            "    volumes:",
            f"      - ./{SPEC_NAME}:/run/toyhost/spec.json:ro",
            "    networks:",
            "      - range",
            "    deploy:",
            "      resources:",
            "        limits:",
            "          cpus: '0.50'",
            "          memory: 256M",
        ])
    lines.extend([
        "",
        "networks:",
        "  range:",
        "    internal: true",
        "",
    ])
    return "\n".join(lines)


def write_stack(spec: dict, directory, noise: bool = True) -> dict:
    """Write the compose file and spec JSON, returning their paths.

    noise=False renders the compose file with the background noise
    generator switched off, for the parity harness. The spec JSON is
    always the full spec; noise only changes container env.
    """
    from pathlib import Path

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    spec_path = target / SPEC_NAME
    spec_path.write_text(json.dumps(spec, indent=1) + "\n",
                         encoding="utf-8")
    text = render(spec)
    if not noise:
        text = text.replace("      TOYHOST_ID: ",
                            "      TOYHOST_NOISE: '0'\n      TOYHOST_ID: ")
    compose_path = target / COMPOSE_NAME
    compose_path.write_text(text, encoding="utf-8")
    return {"compose": compose_path, "spec": spec_path}
