"""Default-fill heuristic for an omitted ``temporal_sensitivity`` (TAP-6696 / VAL-11).

Runs only when the caller does not pass ``temporal_sensitivity`` explicitly —
:meth:`~tapps_brain.store.MemoryStore.save` only consults this when the
parameter is ``None`` (the caller-omitted default), so it never overrides an
explicit value, including an explicit ``None``/``"medium"``.
"""

from __future__ import annotations

import re
from typing import Literal

# Port, URL, or pinned version — content likely to go stale quickly.
# Checked first: a value mentioning both this and an ADR citation is still
# operationally volatile (the pinned detail, not the doc reference, is what
# decays), so this pattern wins on overlap.
_HIGH_SENSITIVITY_PATTERN = re.compile(
    r"https?://"  # URL
    r"|:\d{2,5}\b"  # port number
    r"|\bv?\d+\.\d+(?:\.\d+)?\b"  # version pin: v1.2, 1.2.3, v1.2.3
)

# ADR citation — a versioned design decision, stable until formally superseded.
_ADR_CITATION_PATTERN = re.compile(r"\bADR-\d+\b", re.IGNORECASE)


def infer_temporal_sensitivity(value: str) -> Literal["high", "low"] | None:
    """Classify *value* as ``"high"``/``"low"`` sensitivity, or ``None`` (medium/unset).

    ``None`` is a real result, not "not computed yet" — it means the existing
    default (``null``/``"medium"``) applies because neither pattern matched.
    """
    if _HIGH_SENSITIVITY_PATTERN.search(value):
        return "high"
    if _ADR_CITATION_PATTERN.search(value):
        return "low"
    return None
