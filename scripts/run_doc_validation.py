#!/usr/bin/env python3
"""Run doc validation against a tapps-brain store and write a JSON report.

Usage:
    python scripts/run_doc_validation.py --store .tapps-brain [--strict] [--output report.json]

Exit codes:
    0 — validation complete; no flagged entries (or strict mode not requested)
    1 — strict mode: one or more entries are doc-contradicted
    2 — unexpected error

Intended for CI pipelines on markdown repos that want to catch stale or
contradicted memory entries before merging.  See docs/guides/doc-validation-lookup-engine.md
for how to wire in a third-party lookup engine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run doc validation on a tapps-brain store.")
    parser.add_argument(
        "--store",
        default=".tapps-brain",
        help="Path to the tapps-brain store directory (default: .tapps-brain)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any entries are flagged as doc-contradicted.",
    )
    parser.add_argument(
        "--output",
        default="doc-validation-report.json",
        help="Path for JSON report artifact (default: doc-validation-report.json)",
    )
    parser.add_argument(
        "--keys",
        nargs="*",
        metavar="KEY",
        help="Validate only these entry keys (default: all entries).",
    )
    args = parser.parse_args()

    store_path = Path(args.store)
    if not store_path.exists():
        print(f"error: store path does not exist: {store_path}", file=sys.stderr)
        return 2

    try:
        from tapps_brain.doc_validation import StrictValidationError, ValidationReport
        from tapps_brain.store import MemoryStore

        store = MemoryStore(project_root=store_path)
        try:
            if store._lookup_engine is None:
                print(
                    "warning: no lookup engine configured — validation will return empty report.",
                    file=sys.stderr,
                )
                print(
                    "  Wire a LookupEngineLike via MemoryStore(lookup_engine=...) for real validation.",
                    file=sys.stderr,
                )

            flagged = 0
            report: ValidationReport

            try:
                report = store.validate_entries(
                    keys=args.keys if args.keys else None,
                    strict=args.strict,
                )
            except StrictValidationError as exc:
                report = exc.report
                flagged = report.flagged

            # Write JSON artifact
            output_path = Path(args.output)
            output_data = {
                "validated": report.validated,
                "flagged": report.flagged,
                "inconclusive": report.inconclusive,
                "skipped": report.skipped,
                "elapsed_ms": report.elapsed_ms,
                "entries": [
                    {
                        "key": ev.entry_key,
                        "status": str(ev.overall_status),
                        "reason": ev.reason,
                        "confidence_adjustment": ev.confidence_adjustment,
                    }
                    for ev in report.entries
                ],
            }
            output_path.write_text(json.dumps(output_data, indent=2), encoding="utf-8")

            # Summary
            status_label = "STRICT-FAIL" if (args.strict and flagged > 0) else "OK"
            print(f"doc-validation: {status_label}")
            print(f"  validated:    {report.validated}")
            print(f"  flagged:      {report.flagged}")
            print(f"  inconclusive: {report.inconclusive}")
            print(f"  skipped:      {report.skipped}")
            print(f"  elapsed_ms:   {report.elapsed_ms:.1f}")
            print(f"  artifact:     {output_path}")

            if flagged > 0:
                print("\nFlagged entries:")
                for ev in report.entries:
                    if str(ev.overall_status) == "flagged":
                        print(f"  {ev.entry_key}: {ev.reason}")

            return 1 if (args.strict and flagged > 0) else 0

        finally:
            store.close()

    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
