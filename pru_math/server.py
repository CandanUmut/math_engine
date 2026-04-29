"""Console-script entry point for the PRU Math Engine HTTP server.

Used by the ``pru-math-server`` script declared in ``pyproject.toml``.
Wraps :mod:`uvicorn` so users can launch the engine without remembering
the import path::

    pru-math-server --host 0.0.0.0 --port 8000

Or, equivalently, with the existing module-level app::

    uvicorn pru_math.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="pru-math-server",
        description="Start the PRU Math Engine HTTP server.",
    )
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind host (default: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8000,
                    help="bind port (default: 8000)")
    ap.add_argument("--reload", action="store_true",
                    help="hot-reload on source changes (development)")
    ap.add_argument("--log-level", default="info",
                    help="uvicorn log level (default: info)")
    args = ap.parse_args(argv)

    # Lazy-import so a `--help` invocation doesn't pay the FastAPI / Ollama
    # / Z3 import cost.
    import uvicorn

    uvicorn.run(
        "pru_math.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
