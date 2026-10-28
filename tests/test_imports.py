"""Import hygiene: the API must not require optional dependencies.

Regression test for a crash where crucible.serve failed to import on
a machine without python-multipart installed. Nothing in crucible may
import it (the audio route reads a raw body), so the import must work
with it blocked, and requirements must still declare it.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Optional third-party packages that must never be import-time
# requirements of crucible.api or crucible.serve.
OPTIONAL_BLOCKED = ["multipart"]


def test_api_imports_without_optional_deps():
    # A sys.modules entry of None makes any import of that name fail,
    # which simulates the package being uninstalled.
    probe = (
        "import sys\n"
        f"for _name in {OPTIONAL_BLOCKED!r}:\n"
        "    sys.modules[_name] = None\n"
        "import crucible.api\n"
        "import crucible.serve\n"
        "print('imports-ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "imports-ok" in proc.stdout


def test_requirements_declares_multipart():
    reqs = (ROOT / "requirements.txt").read_text()
    assert "python-multipart" in reqs
