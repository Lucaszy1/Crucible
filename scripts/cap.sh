#!/bin/sh
# Print a bench spend cap: today's recorded Meta spend plus headroom.
# Usage: scripts/cap.sh <headroom-usd>
# Example: python -m crucible.bench ... --spend-cap-usd "$(scripts/cap.sh 6)"
set -eu
python3 - "$1" <<'EOF'
import sys

from crucible import llm

print(f"{llm.spent_today() + float(sys.argv[1]):.2f}")
EOF
