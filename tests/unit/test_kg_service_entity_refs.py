"""Unit tests for kg_service.resolve_entity_refs (TAP-3161 / STORY-074.5)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tapps_brain.services import kg_service


def test_resolve_entity_refs_empty() -> None:
    out = kg_service.resolve_entity_refs(MagicMock(), "proj", "brain", refs=[])
    assert out == {"entity_ids": []}


def test_resolve_entity_refs_rejects_non_object() -> None:
    out = kg_service.resolve_entity_refs(MagicMock(), "proj", "brain", refs=["bad"])
    assert out["error"] == "bad_request"


def test_resolve_entity_refs_requires_type_and_name() -> None:
    out = kg_service.resolve_entity_refs(MagicMock(), "proj", "brain", refs=[{"type": "file"}])
    assert out["error"] == "bad_request"


@patch("tapps_brain.services.kg_service.resolve_entity")
def test_resolve_entity_refs_type_id_shorthand(mock_resolve: MagicMock) -> None:
    mock_resolve.return_value = {"entity_id": "uuid-1"}
    refs = [{"type": "file", "id": "src/foo.py"}]
    out = kg_service.resolve_entity_refs(MagicMock(), "proj", "brain", refs=refs)
    assert out == {"entity_ids": ["uuid-1"]}
    mock_resolve.assert_called_once()
    _args, kwargs = mock_resolve.call_args
    assert kwargs["entity_type"] == "file"
    assert kwargs["canonical_name"] == "src/foo.py"


@patch("tapps_brain.services.kg_service.resolve_entity")
def test_resolve_entity_stable_uuid_across_calls(mock_resolve: MagicMock) -> None:
    mock_resolve.return_value = {"entity_id": "stable-uuid"}
    path = "packages/tapps-mcp/checklist.py"
    ref = [{"entity_type": "file", "canonical_name": path}]
    first = kg_service.resolve_entity_refs(MagicMock(), "proj", "brain", refs=ref)
    second = kg_service.resolve_entity_refs(MagicMock(), "proj", "brain", refs=ref)
    assert first["entity_ids"] == second["entity_ids"] == ["stable-uuid"]
