#!/usr/bin/env python3
"""Run the lexical golden evaluation suite and write results as a JSON artifact.

Usage:
    python scripts/run_eval_golden.py [--output eval-report.json]

Exit codes:
    0 — evaluation passed (MRR and nDCG thresholds met)
    1 — evaluation failed (regression detected or unexpected error)

Intended for CI (eval-golden job) and local spot-checks.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

# Ensure repo src is on the path when run without uv run / activated venv.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run lexical golden eval suite.")
    parser.add_argument(
        "--output",
        default="eval-report.json",
        help="Path for JSON artifact (default: eval-report.json)",
    )
    parser.add_argument(
        "--min-mrr",
        type=float,
        default=0.8,
        help="Minimum MRR threshold (default: 0.8)",
    )
    parser.add_argument(
        "--min-ndcg",
        type=float,
        default=0.8,
        help="Minimum mean nDCG@k threshold (default: 0.8)",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=5,
        help="Rank cutoff k (default: 5)",
    )
    args = parser.parse_args()

    from tapps_brain.evaluation import (
        EvalThresholds,
        evaluate,
        lexical_golden_eval_suite,
        load_eval_suite_into_store,
    )
    from tapps_brain.store import MemoryStore

    suite = lexical_golden_eval_suite()
    thresholds = EvalThresholds(min_mrr=args.min_mrr, min_ndcg_at_k=args.min_ndcg, k=args.k)

    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(project_root=Path(tmpdir), embedding_provider=None)
        load_eval_suite_into_store(store, suite)
        report = evaluate(store, suite, k=args.k, thresholds=thresholds)

    output_path = Path(args.output)
    output_path.write_text(
        json.dumps(report.model_dump(), indent=2),
        encoding="utf-8",
    )

    status = "PASSED" if report.passed else "FAILED"
    print(f"eval-golden: {status}")
    print(f"  suite:        {report.suite_name}")
    print(f"  queries:      {len(report.per_query)}")
    print(f"  MRR:          {report.mrr:.4f}  (min {thresholds.min_mrr})")
    print(f"  nDCG@{report.k}:      {report.mean_ndcg_at_k:.4f}  (min {thresholds.min_ndcg_at_k})")
    print(f"  Recall@{report.k}:    {report.mean_recall_at_k:.4f}")
    print(f"  artifact:     {output_path}")

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
