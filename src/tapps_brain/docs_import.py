"""Import legacy per-repo ``.tapps-mcp-cache`` entries into brain doc storage."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from tapps_brain.docs_lookup import (
    DocsConfig,
    _persist_doc_entry,
    doc_memory_key,
)
from tapps_brain.services import memory_service

logger = structlog.get_logger(__name__)


def _safe_name(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_").replace(":", "_").replace(" ", "_").lower()


@dataclass
class ImportDirReport:
    """Summary of a legacy cache import run."""

    imported: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": self.errors,
        }


def _entry_exists(store: Any, cfg: DocsConfig, library: str, topic: str) -> bool:
    key = doc_memory_key(library, topic)
    row = memory_service.memory_get(store, cfg.project_id, cfg.agent_id, key=key)
    return isinstance(row, dict) and bool(row.get("value")) and not row.get("error")


def import_cache_dir(
    store: Any,
    cache_dir: Path,
    *,
    config: DocsConfig | None = None,
    skip_existing: bool = True,
) -> ImportDirReport:
    """Ingest ``{library}/{topic}.md`` + ``.meta.json`` sidecars from *cache_dir*."""
    report = ImportDirReport()
    cfg = config or DocsConfig.from_env()
    root = cache_dir.expanduser().resolve()
    if not root.is_dir():
        report.failed += 1
        report.errors.append(f"not a directory: {root}")
        return report

    for lib_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        dir_library = lib_dir.name
        for md_path in sorted(lib_dir.glob("*.md")):
            # Per-file locals — do not mutate dir_library across siblings when one
            # meta.json remaps ``library`` (otherwise later files inherit the remap).
            library = dir_library
            topic = md_path.stem
            meta_path = lib_dir / f"{topic}.meta.json"
            try:
                content = md_path.read_text(encoding="utf-8")
                context7_id: str | None = None
                provider_source = "import"
                mode = "code"
                if meta_path.is_file():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if isinstance(meta, dict):
                        context7_id = meta.get("context7_id") or None
                        provider_source = str(meta.get("provider_source") or provider_source)
                        library = str(meta.get("library") or library)
                        topic = str(meta.get("topic") or topic)
                        mode = str(meta.get("mode") or mode)
                lib_key = _safe_name(library)
                if skip_existing and _entry_exists(store, cfg, lib_key, topic):
                    report.skipped += 1
                    continue
                _persist_doc_entry(
                    store,
                    cfg,
                    library=lib_key,
                    topic=topic,
                    mode=mode,
                    content=content,
                    context7_id=context7_id,
                    provider_source=provider_source,
                )
                report.imported += 1
            except Exception as exc:
                report.failed += 1
                report.errors.append(f"{library}/{topic}: {exc}")
                logger.warning("docs_import_failed", library=library, topic=topic, error=str(exc))
    return report


def list_import_candidates(cache_dir: Path) -> list[tuple[str, str]]:
    """Return ``(library, topic)`` pairs discoverable under *cache_dir*."""
    root = cache_dir.expanduser().resolve()
    if not root.is_dir():
        return []
    pairs: list[tuple[str, str]] = []
    for lib_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        pairs.extend((lib_dir.name, md_path.stem) for md_path in sorted(lib_dir.glob("*.md")))
    return pairs
