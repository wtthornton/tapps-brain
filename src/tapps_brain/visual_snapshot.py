"""Versioned JSON snapshot for brain visual surfaces (dashboard / hero / demos).

Contract: aggregated metadata only — no memory bodies, tag lists, or keys.
See ``docs/planning/brain-visual-implementation-plan.md``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

_THEME_SEED_BYTE_LEN = 8

VISUAL_SNAPSHOT_SCHEMA_VERSION: Literal[1] = 1

PRIVACY_NOTICE = "Aggregated counts and health metadata only; excludes memory text, keys, and tags."


class DiagnosticsSummary(BaseModel):
    """Subset of diagnostics safe for visual telemetry."""

    composite_score: float = Field(ge=0.0, le=1.0)
    circuit_state: str
    recorded_at: str


class VisualThemeTokens(BaseModel):
    """Deterministic HSL-oriented tokens for CSS (dark-first, OLED-friendly)."""

    hue_primary: int = Field(ge=0, le=359, description="Base hue for surfaces and accents.")
    hue_accent: int = Field(ge=0, le=359)
    accent_chroma: float = Field(ge=0.0, le=1.0, description="Relative saturation scale.")
    surface_lightness: int = Field(
        ge=6,
        le=18,
        description="Primary panel background lightness scale (dark-first).",
    )
    text_lightness: int = Field(ge=88, le=98, description="Primary text on dark.")
    flow_angle_deg: int = Field(
        ge=0,
        le=359,
        description="Background gradient direction seed (degrees).",
    )


class VisualSnapshot(BaseModel):
    """``brain-visual.json`` contract (schema version 1)."""

    schema_version: Literal[1] = Field(default=VISUAL_SNAPSHOT_SCHEMA_VERSION)
    generated_at: str = Field(description="ISO-8601 UTC when the snapshot was built.")
    fingerprint_sha256: str = Field(description="SHA-256 of canonical identity payload (hex).")
    privacy: str = Field(default=PRIVACY_NOTICE)
    health: dict[str, Any]
    agent_scope_counts: dict[str, int]
    hive_attached: bool
    diagnostics: DiagnosticsSummary | None = None
    theme: VisualThemeTokens


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_fingerprint_hex(identity: dict[str, object]) -> str:
    """Return hex SHA-256 of the canonical identity object."""
    body = _canonical_json(identity).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def theme_from_fingerprint(fingerprint_hex: str) -> VisualThemeTokens:
    """Derive theme tokens deterministically from fingerprint bytes."""
    digest = bytes.fromhex(fingerprint_hex)
    if len(digest) < _THEME_SEED_BYTE_LEN:
        digest = digest.ljust(_THEME_SEED_BYTE_LEN, b"0")
    b = digest
    hue_p = int.from_bytes(b[0:2], "big") % 360
    hue_a = (hue_p + 24 + (b[2] % 48)) % 360
    chroma = 0.45 + (b[3] % 56) / 100.0
    surf = 6 + (b[4] % 9)
    text = 90 + (b[5] % 9)
    flow = int.from_bytes(b[6:8], "big") % 360
    return VisualThemeTokens(
        hue_primary=hue_p,
        hue_accent=hue_a,
        accent_chroma=round(chroma, 2),
        surface_lightness=surf,
        text_lightness=text,
        flow_angle_deg=flow,
    )


def _hive_attached(store: MemoryStore) -> bool:
    return getattr(store, "_hive_store", None) is not None


def _agent_scope_counts(store: MemoryStore) -> dict[str, int]:
    entries = store.list_all()
    counts: dict[str, int] = {}
    for e in entries:
        scope = getattr(e, "agent_scope", None) or "private"
        counts[scope] = counts.get(scope, 0) + 1
    return dict(sorted(counts.items()))


def build_visual_snapshot(
    store: MemoryStore,
    *,
    skip_diagnostics: bool = False,
) -> VisualSnapshot:
    """Build a versioned visual snapshot from an open store."""
    report = store.health()
    hdump = report.model_dump(mode="json")
    agent_scopes = _agent_scope_counts(store)
    hive_on = _hive_attached(store)

    identity: dict[str, object] = {
        "agent_scope_counts": agent_scopes,
        "entry_count": report.entry_count,
        "federation_enabled": report.federation_enabled,
        "hive_attached": hive_on,
        "profile_name": report.profile_name,
        "schema_version": report.schema_version,
        "store_path": report.store_path,
        "tier_distribution": dict(sorted(report.tier_distribution.items())),
    }
    fingerprint = compute_fingerprint_hex(identity)
    theme = theme_from_fingerprint(fingerprint)

    diagnostics: DiagnosticsSummary | None = None
    if not skip_diagnostics:
        diag = store.diagnostics(record_history=False)
        diagnostics = DiagnosticsSummary(
            composite_score=diag.composite_score,
            circuit_state=diag.circuit_state,
            recorded_at=diag.recorded_at,
        )

    now = datetime.now(tz=UTC).isoformat()
    return VisualSnapshot(
        generated_at=now,
        fingerprint_sha256=fingerprint,
        health=hdump,
        agent_scope_counts=agent_scopes,
        hive_attached=hive_on,
        diagnostics=diagnostics,
        theme=theme,
    )


def snapshot_to_json(snapshot: VisualSnapshot) -> str:
    """Serialize snapshot with stable key order for diff-friendly exports."""
    data = snapshot.model_dump(mode="json")
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
