"""Text normalization for save-path dedup comparison.

This module previously also held a ``BloomFilter`` used as a write-path dedup
fast-path: "definitely not seen" skipped an expensive linear similarity scan
over every entry.  TAP-5615 replaced that value-scan with an O(1) same-key
lookup, because matching on the normalized *value* across all entries
discarded writes under distinct keys — a save returned ``200 {"status":
"saved"}`` for a key that did not exist.

With dedup keyed on the entry key, there is nothing left for a value-membership
filter to accelerate.  The filter became write-only: still added to on every
save and fully rebuilt on cold start, GC, consolidation and undo, but never
queried.  Its ``bloom_saturation`` gauge measured the health of a fast-path
that no longer had a read side, and would have climbed toward 1.0 forever
while nothing consulted it (TAP-5629).  Both are removed rather than left in
place emitting a number that no longer means anything.
"""

from __future__ import annotations

import unicodedata


def normalize_for_dedup(text: str) -> str:
    """Normalize text for dedup comparison — Unicode NFKC, lowercase, collapse whitespace.

    NFKC folds compatibility characters (e.g. fullwidth Latin) so visually
    similar strings compare equal under the same-key dedup check
    (EPIC-044 STORY-044.2).
    """
    if not text:
        return ""
    nfkc = unicodedata.normalize("NFKC", text)
    return " ".join(nfkc.lower().split())
