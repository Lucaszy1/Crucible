"""Scenario stack lifecycle for runs over generated specs.

activate(key) renders the compose stack for one scenario key and
makes its spec active (classic deactivates); scenario_info(key)
reports the identity recorded in episode logs; deactivate() returns
to classic. The CLIs call these around each scenario's episodes so
the compose stack restarts as rarely as possible.
"""

from __future__ import annotations

from crucible import sandbox
from crucible.scenarios import generate, render, select


def activate(key) -> dict | None:
    """Render and activate one scenario; None when classic.

    Returns the scenario identity dict recorded in episode logs and
    CSV rows, or None for the classic scenario.
    """
    if select.is_classic(key):
        sandbox.set_active_spec(None)
        return None
    _kind, seed, difficulty = key
    spec = generate.generate(seed, difficulty)
    render.write_stack(spec, sandbox.SANDBOX_DIR)
    sandbox.set_active_spec(spec)
    return {"scenario_id": spec["scenario_id"], "seed": seed,
            "difficulty": difficulty}


def deactivate() -> None:
    """Return the sandbox to the classic scenario."""
    sandbox.set_active_spec(None)
