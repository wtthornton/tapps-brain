#!/usr/bin/env python3
# upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched.
# BEGIN: tapps-skill-asset measure-script/scripts/measure.py v3.12.83
"""Extract a metric from many JSON records, with the probe discipline enforced rather than remembered.

    scripts/measure.py --repo <path> --files '<glob>' --key clipped_fraction --expect 0.982345
    scripts/measure.py --repo <path> --files '<glob>' --key gate_state --expect failed --group 3

Why this exists
---------------
On 2026-09-01, nine ad-hoc probes across two sessions returned an empty or wrong result that
would have read as a finding. Not one was a careless mistake; each was a default being wrong:

* wrong nesting        - assumed auto_qa.checks, the data was at curation.auto_qa.metrics
* wrong repo           - a subprocess inherited a cwd that was not the repo being asked about
* glob too wide        - '*/*/manifest.json' swept template dirs that ship to nobody, and the
                         superset was reported as the set
* naming seam          - an exact-identifier grep found nothing because producer and consumer
                         spell the same thing differently

Every one returned EMPTY, and empty reads as "not there" when it means "I did not look where it is".

Two rules are enforced here so they cannot be skipped:

1. ``--expect`` is MANDATORY. You must name a value you already know is in the data. If the probe
   cannot find it, you get a diagnosis instead of results. This is the known-positive assertion.
2. The DENOMINATOR is always printed: files scanned, records found, distinct groups. A number
   without its population is not a measurement -- "16 lines", "16 shown heroes" and "89 candidate
   records" are three different answers to what sounds like one question.

Known limit, stated because it bit us: a known-positive assertion tests RECALL (can the probe find
the truth?) and never PRECISION (is it also finding things it should not?). A probe can hold a
green assertion while measuring a superset. That is what ``--group`` and the printed denominator
are for -- look at them before believing the count.
"""
from __future__ import annotations

import argparse
import glob as globlib
import json
import os
import sys


def walk(obj, key, path=""):
    """Yield (path, value) for every occurrence of `key`, at any depth."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}/{k}"
            if k == key:
                yield here, v
            # a list of {"metric": "<key>", "value": ...} records is the common shape
            if k == "metric" and v == key and "value" in obj:
                yield path, obj["value"]
            yield from walk(v, key, here)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, key, f"{path}[{i}]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="repo root; all globs are relative to it")
    ap.add_argument("--files", required=True, help="glob, e.g. 'assets/**/manifest.json'")
    ap.add_argument("--key", required=True, help="metric/field name to extract")
    ap.add_argument("--expect", required=True,
                    help="a value you KNOW is present (the known-positive assertion)")
    ap.add_argument("--group", type=int, default=None,
                    help="path segment index to group by, e.g. 3 for .../merch-lines/<LINE>/...")
    ap.add_argument("--fail-only", action="store_true", help="only show records whose sibling ok is false")
    args = ap.parse_args()

    root = os.path.abspath(args.repo)
    if not os.path.isdir(os.path.join(root, ".git")) and not os.path.isdir(root):
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    paths = sorted(globlib.glob(os.path.join(root, args.files), recursive=True))
    rows: list[tuple[str, str, object]] = []
    parse_errors = 0
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            parse_errors += 1
            continue
        rel = os.path.relpath(p, root)
        for jpath, val in walk(doc, args.key):
            rows.append((rel, jpath, val))

    print(f"glob            : {args.files}")
    print(f"files matched   : {len(paths)}" + (f"  ({parse_errors} unparseable)" if parse_errors else ""))
    print(f"records found   : {len(rows)}   <-- this is your DENOMINATOR; is it the population your claim is about?")

    if not rows:
        print(f"\nPROBE FAILED: key '{args.key}' not found in any matched file.")
        if paths:
            with open(paths[0], encoding="utf-8") as fh:
                doc = json.load(fh)
            found = list(walk(doc, args.key))
            if found:
                print(f"  The key DOES exist in {os.path.relpath(paths[0], root)} at: {found[0][0]}")
                print("  Your glob matched the right files; something else dropped the rows.")
            else:
                print(f"  Not present in {os.path.relpath(paths[0], root)} either.")
                print("  Check: is the key spelled as the PRODUCER spells it? Is the glob the shipped path?")
        else:
            print("  The glob matched NO files. Empty here means 'I did not look where it is'.")
        return 1

    # ---- known-positive assertion, before any result is believed -------------
    def matches(v) -> bool:
        if str(v) == str(args.expect):
            return True
        try:
            return abs(float(v) - float(args.expect)) < 1e-6
        except (TypeError, ValueError):
            return False

    if not any(matches(v) for _, _, v in rows):
        print(f"\nPROBE INVALID: expected value {args.expect!r} not present among {len(rows)} records.")
        sample = sorted({str(v) for _, _, v in rows})[:8]
        print(f"  Sample of what WAS found: {sample}")
        print("  Do not use these results. Either the expectation is wrong or the probe is looking")
        print("  in the wrong place -- and an unvalidated probe's output is not evidence.")
        return 1

    print(f"assertion       : PASSED, {args.expect} present  (recall proven; precision is NOT)")

    groups: dict[str, list[object]] = {}
    for rel, _jpath, val in rows:
        gk = rel.split(os.sep)[args.group] if args.group is not None and len(rel.split(os.sep)) > args.group else rel
        groups.setdefault(gk, []).append(val)
    print(f"distinct groups : {len(groups)}")

    numeric = []
    for g, vals in sorted(groups.items()):
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums:
            numeric.append((g, max(nums)))
        else:
            uniq = sorted({str(v) for v in vals})
            print(f"  {g:<42} {','.join(uniq)}")
    for g, mx in sorted(numeric, key=lambda x: -x[1]):
        print(f"  {g:<42} max={mx}")

    print("\nBefore quoting any count above: does 'distinct groups' name the population your")
    print("sentence is about? A superset reported as the set is a green assertion and a wrong claim.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# END: tapps-skill-asset
