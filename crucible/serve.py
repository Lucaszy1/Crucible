#!/usr/bin/env python3
"""Start the Crucible Operations Center server on localhost only.

Example:
  python -m crucible.serve
  python -m crucible.serve --port 8123
"""

from __future__ import annotations

import argparse


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve the Crucible Operations Center dashboard."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Local port to bind (default 8000)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    import uvicorn

    url = f"http://127.0.0.1:{args.port}"
    print(f"Crucible Operations Center: {url}")
    uvicorn.run("crucible.api:app", host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
