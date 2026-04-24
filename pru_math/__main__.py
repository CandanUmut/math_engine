"""CLI entry: ``python -m pru_math "integrate x^2 from 0 to 1"``.

Useful for smoke-testing the stack without the FastAPI layer.
"""
from __future__ import annotations

import argparse
import json
import sys

from .reasoner import Reasoner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pru_math", description="PRU math reasoner (CLI)")
    ap.add_argument("text", nargs="+", help="problem in SymPy / LaTeX / natural language")
    ap.add_argument("--json", action="store_true", help="emit the full outcome as JSON")
    args = ap.parse_args(argv)

    reasoner = Reasoner()
    outcome = reasoner.solve(" ".join(args.text))

    if args.json:
        print(json.dumps(outcome.to_dict(), indent=2, default=str))
        return 0 if outcome.ok else 1

    print(f"input      : {' '.join(args.text)}")
    print(f"parsed as  : {outcome.source_format} / {outcome.problem_type}")
    print(f"expression : {outcome.parsed_pretty}")
    if outcome.ok:
        print(f"answer     : {outcome.answer_pretty}")
        print(f"tool       : {outcome.approach}")
        print(f"time       : {outcome.time_ms:.1f} ms")
        print(f"verify     : {outcome.verification_status} — {outcome.verification_detail}")
    else:
        print(f"ERROR      : {outcome.error}")
    print(f"problem_id : {outcome.problem_id}")
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
