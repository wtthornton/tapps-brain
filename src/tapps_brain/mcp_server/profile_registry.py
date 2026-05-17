"""MCP tool profile registry — EPIC-073 STORY-073.1.

Maps profile names → frozenset[str] of allowed tool names. Config is loaded
from a YAML file (bundled as package data; overridable via constructor arg).
Validates all listed tool names against the live server registry at startup —
the server refuses to start if the YAML references a tool that no longer
exists (drift detection).

Public API
----------
ProfileRegistry(config_path=None)
    Load profiles from *config_path* (or the bundled default).
ProfileRegistry.get(name) -> frozenset[str]
    Return the tool names for profile *name*.  Raises ``UnknownProfileError``.
ProfileRegistry.get_deferred(name) -> frozenset[str]
    Return the subset of tools in profile *name* marked with
    ``defer_loading: true`` in the YAML (TAP-1985).  Deferred tools remain
    callable; they are hidden from the default ``tools/list`` response so the
    eager catalog stays within the per-server budget (parent epic TAP-1983).
    Returns an empty frozenset for profiles with no deferred entries.  Raises
    ``UnknownProfileError`` for unknown *name*.
ProfileRegistry.profiles -> list[str]
    Sorted list of known profile names.
ProfileRegistry.validate_against(known_tools)
    Check every YAML-listed tool name exists in *known_tools*.  Raise
    ``ValueError`` on drift so the server fails fast at startup.

YAML tool-entry shape (TAP-1985)
--------------------------------
Each entry in ``profiles.<name>.tools`` is either:

* a plain string — eager (visible in ``tools/list``); or
* a mapping ``{name: <str>, defer_loading: <bool>}`` — when
  ``defer_loading: true``, the tool is registered and callable but omitted
  from the default ``tools/list`` response.

The two forms can be mixed within a single profile.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any

import yaml


class UnknownProfileError(KeyError):
    """Raised when *name* is not in the :class:`ProfileRegistry`.

    Attributes
    ----------
    name:
        The requested (unknown) profile name.
    available:
        Sorted list of profile names that *are* registered.
    """

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = sorted(available)
        super().__init__(f"Unknown MCP profile {name!r}. Available profiles: {self.available}")


class ProfileRegistry:
    """Registry mapping profile names to frozensets of allowed tool names.

    Load order
    ----------
    1. Explicit *config_path* constructor argument (highest priority).
    2. Bundled package default at ``tapps_brain.mcp_server/mcp_profiles.yaml``.

    Drift detection
    ---------------
    Call :meth:`validate_against` after all ``@mcp.tool`` decorators have
    been applied in ``create_server()``.  Validation fails fast (``ValueError``)
    if any profile in the YAML references a tool name that is not registered —
    preventing silent misconfiguration from reaching production.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        raw = self._read(config_path)
        data: dict[str, Any] = yaml.safe_load(raw) or {}
        self._profiles: dict[str, frozenset[str]] = {}
        self._deferred: dict[str, frozenset[str]] = {}
        self._descriptions: dict[str, str] = {}
        for name, conf in data.get("profiles", {}).items():
            tool_names, deferred_names = self._parse_tool_entries(name, conf.get("tools") or [])
            self._profiles[name] = frozenset(tool_names)
            self._deferred[name] = frozenset(deferred_names)
            desc = conf.get("description")
            self._descriptions[name] = str(desc).strip() if isinstance(desc, str) else ""

    @staticmethod
    def _parse_tool_entries(profile_name: str, entries: list[Any]) -> tuple[list[str], list[str]]:
        """Split *entries* into (all_tool_names, deferred_tool_names).

        Accepts mixed plain-string and ``{name, defer_loading}`` dict forms
        (TAP-1985). Raises ``ValueError`` on malformed entries so misconfigured
        YAML fails fast at startup rather than producing a silently-wrong
        catalog.
        """
        all_names: list[str] = []
        deferred: list[str] = []
        for entry in entries:
            if isinstance(entry, str):
                all_names.append(entry)
                continue
            if isinstance(entry, dict):
                name = entry.get("name")
                if not isinstance(name, str) or not name:
                    raise ValueError(
                        f"profile {profile_name!r}: tool entry missing 'name': {entry!r}"
                    )
                all_names.append(name)
                if bool(entry.get("defer_loading", False)):
                    deferred.append(name)
                continue
            raise ValueError(
                f"profile {profile_name!r}: tool entry must be a string or "
                f"{{name, defer_loading}} dict, got {type(entry).__name__}: {entry!r}"
            )
        return all_names, deferred

    @staticmethod
    def _read(config_path: Path | None) -> str:
        """Return YAML text from *config_path* or the bundled package default."""
        if config_path is not None:
            return Path(config_path).read_text(encoding="utf-8")
        resource = importlib.resources.files("tapps_brain.mcp_server") / "mcp_profiles.yaml"
        return resource.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, name: str) -> frozenset[str]:
        """Return the frozenset of tool names for profile *name*.

        Parameters
        ----------
        name:
            Profile name, e.g. ``"coder"`` or ``"full"``.

        Raises
        ------
        UnknownProfileError
            If *name* is not in the registry.
        """
        if name not in self._profiles:
            raise UnknownProfileError(name, list(self._profiles))
        return self._profiles[name]

    def get_deferred(self, name: str) -> frozenset[str]:
        """Return the frozenset of *deferred* tool names for profile *name*.

        Deferred tools are still registered and callable; they are hidden from
        the default ``tools/list`` response so the eager catalog stays within
        the per-server budget (TAP-1985, parent epic TAP-1983). Clients adopt
        Anthropic Tool Search BETA (opt-in via the
        ``advanced-tool-use-2025-11-20`` header) to discover deferred tools.

        Returns an empty frozenset when the profile has no deferred entries.

        Parameters
        ----------
        name:
            Profile name, e.g. ``"coder"`` or ``"full"``.

        Raises
        ------
        UnknownProfileError
            If *name* is not in the registry.
        """
        if name not in self._profiles:
            raise UnknownProfileError(name, list(self._profiles))
        return self._deferred.get(name, frozenset())

    def get_description(self, name: str) -> str:
        """Return the human-readable description for profile *name* (TAP-2006).

        Returns an empty string when the YAML did not declare a description.

        Raises
        ------
        UnknownProfileError
            If *name* is not in the registry.
        """
        if name not in self._profiles:
            raise UnknownProfileError(name, list(self._profiles))
        return self._descriptions.get(name, "")

    @property
    def profiles(self) -> list[str]:
        """Sorted list of known profile names."""
        return sorted(self._profiles)

    def validate_against(self, known_tools: frozenset[str]) -> None:
        """Validate every profile against *known_tools* and fail fast on drift.

        Parameters
        ----------
        known_tools:
            The complete set of tool names currently registered in the MCP
            server (typically ``frozenset(t.name for t in mcp._tool_manager.list_tools())``
            called **before** any removal pass).

        Raises
        ------
        ValueError
            If any profile references a tool name not present in *known_tools*.
            The error message lists every offending profile → unknown tool mapping
            so operators can update ``mcp_profiles.yaml`` in one pass.
        """
        errors: list[str] = []
        for profile_name, tools in self._profiles.items():
            unknown = tools - known_tools
            if unknown:
                errors.append(
                    f"  profile {profile_name!r} references unknown tool(s): {sorted(unknown)}"
                )
        if errors:
            raise ValueError(
                "MCP profile drift detected — update mcp_profiles.yaml:\n" + "\n".join(errors)
            )
