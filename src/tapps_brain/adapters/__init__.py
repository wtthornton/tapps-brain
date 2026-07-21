"""Optional inbound memory adapters (preserve mode only).

These are migration helpers for foreign export shapes (Mem0, Letta .af).
They are **not** primary interchange formats — prefer native ``tapps-memory``
or MIF v2 for portable backups (TAP-5027 / TAP-5033).
"""

from __future__ import annotations

from tapps_brain.adapters.letta_af import letta_af_to_memory_dicts, looks_like_letta_af
from tapps_brain.adapters.mem0 import looks_like_mem0, mem0_to_memory_dicts

__all__ = [
    "letta_af_to_memory_dicts",
    "looks_like_letta_af",
    "looks_like_mem0",
    "mem0_to_memory_dicts",
]
