"""VAL-10 (TAP-6696): manifest records per-tool MCP profile membership.

``docs/generated/mcp-tools-manifest.json`` previously had no per-profile
breakdown at all (a flat {name, description} list). The generator now sources
per-tool profile membership from the runtime ``ProfileRegistry``
(mcp_profiles.yaml) so the manifest can prove which named profiles (full,
operator, coder, ...) may call a given tool — enough to diff-prove
brain_promote_learning/brain_demote_learning sit in full+operator, absent
from coder (TAP-5542: a coding agent must not approve its own learnings).
"""

from __future__ import annotations

import json

from scripts.generate_mcp_tool_manifest import OUT_PATH, main
from tapps_brain.mcp_server.profile_registry import ProfileRegistry


class TestManifestProfileMembership:
    def test_main_regenerates_manifest_with_profiles_field(self) -> None:
        assert main() == 0
        payload = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        assert "profile_names" in payload
        assert "tools_by_profile" in payload
        assert payload["tools"], "manifest must list at least one default tool"
        for tool in payload["tools"] + payload["operator_tools"]:
            assert "profiles" in tool
            assert isinstance(tool["profiles"], list)

    def test_promote_demote_are_full_and_operator_not_coder(self) -> None:
        assert main() == 0
        payload = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        by_name = {t["name"]: t for t in payload["tools"] + payload["operator_tools"]}
        for tool_name in ("brain_promote_learning", "brain_demote_learning"):
            profiles = by_name[tool_name]["profiles"]
            assert "full" in profiles
            assert "operator" in profiles
            assert "coder" not in profiles

    def test_profile_membership_matches_live_registry(self) -> None:
        """Manifest membership isn't hand-maintained drift — it's read straight
        from the same ProfileRegistry the MCP server gates calls against."""
        assert main() == 0
        payload = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        registry = ProfileRegistry()
        by_name = {t["name"]: t for t in payload["tools"] + payload["operator_tools"]}
        for tool_name in ("brain_promote_learning", "brain_demote_learning", "brain_recall"):
            expected = sorted(p for p in registry.profiles if tool_name in registry.get(p))
            assert by_name[tool_name]["profiles"] == expected
