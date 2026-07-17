"""Tests for ``brain_export`` — TAP-2099 one-shot Managed Agents exporter."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier
from tapps_brain.services import memory_service


class _FakeStore:
    """Duck-typed stand-in for ``MemoryStore.iter_active_entries``."""

    def __init__(self, entries: list[MemoryEntry]) -> None:
        self._entries = list(entries)

    def iter_active_entries(self) -> Any:
        yield from self._entries


def _entry(
    key: str,
    *,
    tier: MemoryTier = MemoryTier.pattern,
    value: str = "body",
    confidence: float = 0.7,
    tags: list[str] | None = None,
    last_accessed_days_ago: float = 1.0,
    source: MemorySource = MemorySource.agent,
) -> MemoryEntry:
    ts = (datetime.now(tz=UTC) - timedelta(days=last_accessed_days_ago)).isoformat()
    return MemoryEntry(
        key=key,
        value=value,
        tier=tier,
        confidence=confidence,
        source=source,
        tags=tags or [],
        created_at=ts,
        updated_at=ts,
        last_accessed=ts,
    )


def _export(store: _FakeStore, output_dir: Path, **overrides: Any) -> dict[str, Any]:
    return memory_service.brain_export(
        store=store,
        project_id="test-project",
        agent_id="test-agent",
        output_dir=str(output_dir),
        **overrides,
    )


def test_empty_store_writes_manifest_with_zero_counts(tmp_path: Path) -> None:
    out = tmp_path / "export"
    report = _export(_FakeStore([]), out)

    assert report["files_written"] == 0
    assert report["tier_counts"] == {}
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["tier_counts"] == {}
    assert manifest["project_id"] == "test-project"


def test_populated_store_writes_per_tier_files_with_frontmatter(tmp_path: Path) -> None:
    entries = [
        _entry("alpha", tier=MemoryTier.architectural, value="alpha body"),
        _entry("beta", tier=MemoryTier.pattern, value="beta body"),
    ]
    out = tmp_path / "export"
    report = _export(_FakeStore(entries), out)

    assert report["files_written"] == 2
    assert report["tier_counts"] == {"architectural": 1, "pattern": 1}

    alpha_md = (out / "architectural" / "alpha.md").read_text()
    assert "READ-ONLY managed by tapps-brain" in alpha_md
    assert "key: alpha" in alpha_md
    assert "tier: architectural" in alpha_md
    assert "confidence: 0.7000" in alpha_md
    assert "source: agent" in alpha_md
    assert "alpha body" in alpha_md


def test_secret_tag_entries_are_skipped_wholesale(tmp_path: Path) -> None:
    entries = [
        _entry("a", value="public"),
        _entry("b", value="hidden", tags=["secret"]),
        _entry("c", value="also hidden", tags=["secret", "architectural"]),
    ]
    out = tmp_path / "export"
    report = _export(_FakeStore(entries), out, redact=False)

    assert report["skipped_secret_tag"] == 2
    assert report["files_written"] == 1
    assert (out / "pattern" / "a.md").exists()
    assert not (out / "pattern" / "b.md").exists()


def test_redaction_scrubs_aws_keys_github_tokens_jwts_and_emails(tmp_path: Path) -> None:
    secrets = (
        "AKIAIOSFODNN7EXAMPLE ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123_def-ghi user@example.com"
    )
    out = tmp_path / "export"
    _export(_FakeStore([_entry("dirty", value=secrets)]), out)

    body = (out / "pattern" / "dirty.md").read_text()
    assert "AKIAIOSFODNN7EXAMPLE" not in body
    assert "ghp_aaaaa" not in body
    assert "user@example.com" not in body
    assert "eyJhbGciOi" not in body
    assert "[REDACTED:aws-key]" in body
    assert "[REDACTED:gh-token]" in body
    assert "[REDACTED:jwt]" in body
    assert "[REDACTED:email]" in body


def test_redaction_can_be_disabled(tmp_path: Path) -> None:
    out = tmp_path / "export"
    _export(
        _FakeStore([_entry("clear", value="AKIAIOSFODNN7EXAMPLE")]),
        out,
        redact=False,
    )
    body = (out / "pattern" / "clear.md").read_text()
    assert "AKIAIOSFODNN7EXAMPLE" in body
    assert "[REDACTED" not in body


def test_top_n_per_tier_truncates_lowest_ranked(tmp_path: Path) -> None:
    # Three pattern entries — ranking is max(confidence, recency).
    # high_conf wins on confidence; recent_low wins on recency over old_low.
    entries = [
        _entry("high_conf", confidence=0.95, last_accessed_days_ago=400.0),
        _entry("recent_low", confidence=0.10, last_accessed_days_ago=0.5),
        _entry("old_low", confidence=0.10, last_accessed_days_ago=400.0),
    ]
    out = tmp_path / "export"
    report = _export(_FakeStore(entries), out, top_n_per_tier=2)

    assert report["tier_counts"] == {"pattern": 2}
    assert (out / "pattern" / "high_conf.md").exists()
    assert (out / "pattern" / "recent_low.md").exists()
    assert not (out / "pattern" / "old_low.md").exists()


def test_manifest_schema_records_required_fields(tmp_path: Path) -> None:
    out = tmp_path / "export"
    _export(_FakeStore([_entry("a")]), out)
    manifest = json.loads((out / "manifest.json").read_text())
    for key in (
        "schema_version",
        "project_id",
        "layout",
        "exported_at",
        "tier_counts",
        "files_written",
        "top_n_per_tier",
        "redact",
        "skipped_secret_tag",
        "redacted_fields",
    ):
        assert key in manifest, f"missing manifest key: {key}"
    assert manifest["schema_version"] == 1
    assert manifest["layout"] == "managed-agents"
    assert manifest["redact"] is True


def test_invalid_layout_returns_error(tmp_path: Path) -> None:
    report = _export(_FakeStore([]), tmp_path / "export", layout="cocoa")
    assert report["error"] == "invalid_layout"
    assert "managed-agents" in report["message"]


def test_invalid_top_n_returns_error(tmp_path: Path) -> None:
    report = _export(_FakeStore([]), tmp_path / "export", top_n_per_tier=0)
    assert report["error"] == "invalid_top_n"


def test_store_required_when_none_passed(tmp_path: Path) -> None:
    report = memory_service.brain_export(
        store=None,
        project_id="p",
        agent_id="a",
        output_dir=str(tmp_path / "export"),
    )
    assert report["error"] == "store_required"


def test_refuses_to_overwrite_non_empty_output_dir(tmp_path: Path) -> None:
    out = tmp_path / "existing"
    out.mkdir()
    (out / "leftover.md").write_text("not mine")

    report = _export(_FakeStore([_entry("a")]), out)
    assert report["error"] == "output_exists_not_empty"
    # Existing file untouched.
    assert (out / "leftover.md").read_text() == "not mine"


def test_target_project_id_overrides_caller_project(tmp_path: Path) -> None:
    out = tmp_path / "export"
    report = _export(_FakeStore([]), out, target_project_id="other-proj")
    assert report["project_id"] == "other-proj"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["project_id"] == "other-proj"


def test_confidence_minus_one_resolves_via_source_default(tmp_path: Path) -> None:
    entry = _entry("h", confidence=-1.0, source=MemorySource.human)
    out = tmp_path / "export"
    _export(_FakeStore([entry]), out)
    body = (out / "pattern" / "h.md").read_text()
    # Human source default is 0.95 per _SOURCE_CONFIDENCE_DEFAULTS.
    assert "confidence: 0.9500" in body


@pytest.mark.parametrize(
    "tier",
    [
        MemoryTier.architectural,
        MemoryTier.pattern,
        MemoryTier.procedural,
        MemoryTier.context,
        MemoryTier.ephemeral,
        MemoryTier.session,
    ],
)
def test_every_tier_round_trips(tmp_path: Path, tier: MemoryTier) -> None:
    out = tmp_path / "export"
    _export(_FakeStore([_entry("k", tier=tier)]), out)
    assert (out / tier.value / "k.md").exists()


def test_recency_score_naive_timestamp_does_not_raise() -> None:
    """tz-naive last_accessed must not TypeError against aware ``now``."""
    from tapps_brain.services.memory_service import _recency_score

    naive = (datetime.now(tz=UTC) - timedelta(days=3)).replace(tzinfo=None).isoformat()
    score = _recency_score(naive)
    assert 0.0 < score < 1.0
    # ~3 days → 1/(1+3/30) ≈ 0.909
    assert 0.88 < score < 0.93


# ---------------------------------------------------------------------------
# OKF layout (Open Knowledge Format v0.1)
# ---------------------------------------------------------------------------


def _parse_okf_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    """Split an OKF concept doc into (frontmatter, body); assert frontmatter-first."""
    assert md_text.startswith("---\n"), "OKF frontmatter must be the first thing in the file"
    _, front, body = md_text.split("---\n", 2)
    return yaml.safe_load(front), body


def test_okf_layout_writes_frontmatter_first_conformant_docs(tmp_path: Path) -> None:
    entries = [
        _entry("alpha", tier=MemoryTier.architectural, value="alpha body: with colons"),
        _entry("beta", tier=MemoryTier.pattern, value='body "quoted"'),
    ]
    out = tmp_path / "export"
    report = _export(_FakeStore(entries), out, layout="okf")
    assert report["files_written"] == 2

    fm, body = _parse_okf_frontmatter((out / "architectural" / "alpha.md").read_text())
    # Conformance rule 2: non-empty `type`.
    assert fm["type"] == "architectural"
    assert fm["key"] == "alpha"
    assert fm["tier"] == "architectural"
    assert isinstance(fm["tags"], list)
    assert "timestamp" in fm
    # Banner lives in the body, not before the frontmatter.
    assert "READ-ONLY managed by tapps-brain" in body
    assert "alpha body: with colons" in body

    # Special characters in the value must not break YAML parsing.
    fm_beta, body_beta = _parse_okf_frontmatter((out / "pattern" / "beta.md").read_text())
    assert fm_beta["type"] == "pattern"
    assert 'body "quoted"' in body_beta


def test_okf_layout_writes_reserved_index_and_log(tmp_path: Path) -> None:
    entries = [
        _entry("alpha", tier=MemoryTier.architectural, value="alpha body"),
        _entry("beta", tier=MemoryTier.pattern, value="beta body"),
    ]
    out = tmp_path / "export"
    _export(_FakeStore(entries), out, layout="okf")

    # index.md is reserved → no frontmatter; groups by tier with relative links.
    index = (out / "index.md").read_text()
    assert not index.startswith("---")
    assert "## architectural" in index
    assert "[alpha](architectural/alpha.md)" in index
    assert "[beta](pattern/beta.md)" in index
    assert "okf_version: 0.1" in index

    # log.md is reserved → ISO date headings, newest first.
    log = (out / "log.md").read_text()
    assert log.startswith("# Log")
    assert "**Creation**" in log
    import re

    assert re.search(r"## \d{4}-\d{2}-\d{2}", log), "log.md needs an ISO date heading"


def test_okf_manifest_declares_version_and_layout(tmp_path: Path) -> None:
    out = tmp_path / "export"
    _export(_FakeStore([_entry("a")]), out, layout="okf")
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["layout"] == "okf"
    assert manifest["okf_version"] == "0.1"


def test_okf_layout_redacts_and_skips_secret_tags(tmp_path: Path) -> None:
    entries = [
        _entry("public", value="key AKIAIOSFODNN7EXAMPLE here"),
        _entry("hidden", value="nope", tags=["secret"]),
    ]
    out = tmp_path / "export"
    report = _export(_FakeStore(entries), out, layout="okf")
    assert report["skipped_secret_tag"] == 1
    assert not (out / "pattern" / "hidden.md").exists()

    _fm, body = _parse_okf_frontmatter((out / "pattern" / "public.md").read_text())
    assert "AKIAIOSFODNN7EXAMPLE" not in body
    assert "[REDACTED:aws-key]" in body


def test_managed_agents_layout_emits_no_okf_reserved_files(tmp_path: Path) -> None:
    out = tmp_path / "export"
    _export(_FakeStore([_entry("a")]), out)  # default layout
    assert not (out / "index.md").exists()
    assert not (out / "log.md").exists()
    manifest = json.loads((out / "manifest.json").read_text())
    assert "okf_version" not in manifest
