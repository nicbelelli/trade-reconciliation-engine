"""Command line entry point.

    python -m recon evaluate --trades 500 --break-rate 0.08
    python -m recon reconcile --audit-out data/audit.csv
"""

from __future__ import annotations

import argparse
import sys

from .audit import AuditTrail
from .generator import alias_table, generate
from .metrics import evaluate
from .stages import WATERFALL, MatchConfig
from .waterfall import reconcile


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recon", description="Trade reconciliation engine"
    )
    parser.add_argument("command", choices=["reconcile", "evaluate"])
    parser.add_argument("--trades", type=int, default=500)
    parser.add_argument("--break-rate", type=float, default=0.08)
    parser.add_argument("--benign-rate", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--audit-out", default=None,
                        help="write the audit trail to this CSV path")
    parser.add_argument("--stages", type=int, default=4, choices=[1, 2, 3, 4],
                        help="run only the first N stages of the waterfall")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    internal, external, truth = generate(
        n_trades=args.trades,
        break_rate=args.break_rate,
        benign_rate=args.benign_rate,
        seed=args.seed,
    )
    config = MatchConfig(instrument_aliases=alias_table())
    audit = AuditTrail()

    result, audit = reconcile(
        internal, external, config, audit, stages=WATERFALL[: args.stages]
    )

    print(f"internal records   {len(internal)}")
    print(f"external records   {len(external)}")
    print(f"injected breaks    {truth.break_count}")
    print(f"benign variations  {truth.benign_count}")
    print()
    for key, value in result.summary().items():
        print(f"  {key:<28} {value}")

    if args.command == "evaluate":
        print()
        print(evaluate(result, truth, internal, external).report())

    if args.audit_out:
        path = audit.to_csv(args.audit_out)
        print(f"\naudit trail: {len(audit)} entries -> {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
