"""STORY-065.3 / STORY-078.9 — brain-visual dashboard purge regression tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_VISUAL = _REPO / "examples" / "brain-visual"
_INDEX = _VISUAL / "index.html"
_JSON = _VISUAL / "brain-visual.json"
_SCORECARD_DERIVE = _VISUAL / "scorecard-derive.js"

_REMOVED_IDS = (
    "tags-body",
    "groups-body",
    "tag-cloud-body",
    "memory-groups-body",
    "memory-groups",
    "tag-cloud",
    "rstep-query",
    "rstep-bm25",
    "rstep-vec",
    "rstep-rrf",
    "rstep-result",
    "retrieval-insight-panel",
)

_REMOVED_JS = (
    "renderTags",
    "renderGroups",
    "renderMemoryGroups",
    "renderTagCloud",
    "scorecard-derive",
)

_NGINX_VISUAL = _REPO / "docker" / "nginx-visual.conf"


def test_brain_visual_json_empty_stub() -> None:
    data = json.loads(_JSON.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["health"]["entry_count"] == 0
    assert data["hive_health"]["connected"] is False
    assert data["scorecard"] == []


def test_scorecard_derive_js_removed() -> None:
    assert not _SCORECARD_DERIVE.exists()


def test_brain_visual_index_purged_stale_sections() -> None:
    html = _INDEX.read_text(encoding="utf-8")
    for element_id in _REMOVED_IDS:
        assert f'id="{element_id}"' not in html, f"stale element id={element_id!r} still present"
        assert f"id='{element_id}'" not in html
    for pattern in (r"\brstep-", r"retrieval-insight-panel", r"privacy-bullets"):
        assert not re.search(pattern, html), f"stale pattern {pattern!r} still present"
    assert 'id="privacy-tier-badge"' in html
    for fn in _REMOVED_JS:
        assert fn not in html, f"stale JS reference {fn!r} still present"


def test_brain_visual_index_uses_nltweb_lockup() -> None:
    """Header uses NLTWeb logo pack lockups — no HTML-reconstructed wordmark."""
    html = _INDEX.read_text(encoding="utf-8")
    assert "nltlabs-lockup-fullcolor.svg" in html
    assert "nltlabs-lockup-fullcolor-on-dark.svg" in html
    assert "logo-lockup-img--light" in html
    assert 'class="brand-wordmark"' not in html
    assert "nlt-an-mark-sm.svg" not in html


def test_nginx_visual_healthz_location() -> None:
    """STORY-078.5: /healthz is served by nginx without proxying to brain-http."""
    conf = _NGINX_VISUAL.read_text(encoding="utf-8")
    match = re.search(r"location = /healthz \{[^}]+\}", conf, re.DOTALL)
    assert match is not None, "missing location = /healthz block"
    block = match.group(0)
    assert '"service":"tapps-visual"' in block
    assert "return 200" in block
    assert "proxy_pass" not in block


def test_nginx_visual_upstream_error_codes_distinguish_timeout() -> None:
    """502/503 = unavailable; only 504 is a timeout (dashboard classifies by body)."""
    conf = _NGINX_VISUAL.read_text(encoding="utf-8")
    assert 'return 502 \'{"error":"upstream_unavailable"' in conf
    assert 'return 503 \'{"error":"upstream_unavailable"' in conf
    assert 'return 504 \'{"error":"upstream_timeout"' in conf
    assert 'return 502 \'{"error":"upstream_timeout"' not in conf
    assert 'return 503 \'{"error":"upstream_timeout"' not in conf


def test_brain_visual_empty_state_remediation_targets_hive_stack() -> None:
    """Empty-state copy must not point operators at retired tapps-brain-mcp."""
    html = _INDEX.read_text(encoding="utf-8")
    assert "tapps-brain-mcp" not in html
    assert "tapps-brain mcp start --http" not in html
    assert "docker-compose.hive.yaml" in html
    assert "classifySnapshotFailure" in html
    assert "upstream_unavailable" in html
