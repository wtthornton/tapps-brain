"""Lossless bundle helpers: relations + embeddings (TAP-5030)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

EMBEDDINGS_SIDECAR_FORMAT = "tapps-brain-embeddings-v1"

# ---------------------------------------------------------------------------


def collect_relations(store: MemoryStore) -> list[dict[str, Any]]:
    """Collect private SPO relations for the store's tenant."""
    persistence = getattr(store, "_persistence", None)
    list_rels = getattr(persistence, "list_relations", None) if persistence is not None else None
    if callable(list_rels):
        try:
            return list(list_rels())
        except Exception:
            logger.warning("memory_export_list_relations_failed", exc_info=True)
    query = getattr(store, "query_relations", None)
    if callable(query):
        try:
            rebuild = getattr(store, "_rebuild_relations_cache_from_durable", None)
            if callable(rebuild):
                rebuild()
            return list(query())
        except Exception:
            logger.warning("memory_export_query_relations_failed", exc_info=True)
    return []


def build_embeddings_sidecar(
    vectors: dict[str, list[float]],
    *,
    embedding_model_id: str,
    dimension: int | None = None,
) -> dict[str, Any]:
    """Build optional embeddings sidecar keyed by memory key."""
    dim = dimension
    if dim is None and vectors:
        first = next(iter(vectors.values()))
        dim = len(first)
    return {
        "format": EMBEDDINGS_SIDECAR_FORMAT,
        "embedding_model_id": embedding_model_id,
        "dimension": dim,
        "vectors": vectors,
        "entry_count": len(vectors),
    }


def _active_embedding_model_id(store: MemoryStore) -> str | None:
    provider = getattr(store, "_embedding_provider", None)
    if provider is None:
        return None
    mid = getattr(provider, "model_id", None)
    if isinstance(mid, str) and mid.strip():
        return mid.strip()
    return None


def collect_embeddings(store: MemoryStore) -> dict[str, Any] | None:
    """Load durable embeddings into a sidecar dict, or None if unavailable."""
    load = getattr(store, "load_embeddings", None)
    if not callable(load):
        return None
    try:
        loaded = load()
    except Exception:
        logger.warning("memory_export_load_embeddings_failed", exc_info=True)
        return None
    if not isinstance(loaded, dict) or not loaded:
        return None
    # loaded: key -> {"vector": [...], "embedding_model_id": "..."}
    model_ids = {
        v.get("embedding_model_id")
        for v in loaded.values()
        if isinstance(v, dict) and v.get("embedding_model_id")
    }
    model_id = next(iter(model_ids), None) or _active_embedding_model_id(store) or "unknown"
    vectors = {
        k: v["vector"]
        for k, v in loaded.items()
        if isinstance(v, dict) and isinstance(v.get("vector"), list)
    }
    if not vectors:
        return None
    return build_embeddings_sidecar(vectors, embedding_model_id=str(model_id))


def restore_relations(store: MemoryStore, relations: list[dict[str, Any]]) -> int:
    """Restore SPO relations via ``save_relations``; returns restored count."""
    from tapps_brain.relations import RelationEntry

    save = getattr(store, "save_relations", None)
    if not callable(save):
        return 0
    restored = 0
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        keys = rel.get("source_entry_keys") or []
        anchor = str(keys[0]) if keys else ""
        if not anchor:
            continue
        try:
            entry = RelationEntry.model_validate(
                {
                    "subject": rel["subject"],
                    "predicate": rel["predicate"],
                    "object_entity": rel["object_entity"],
                    "source_entry_keys": list(keys),
                    "confidence": float(rel.get("confidence", 0.8)),
                    "created_at": rel.get("created_at") or datetime.now(tz=UTC).isoformat(),
                }
            )
            save(anchor, [entry])
            restored += 1
        except Exception:
            logger.warning("memory_import_relation_failed", relation=rel, exc_info=True)
    return restored


def restore_embeddings(
    store: MemoryStore,
    embeddings: dict[str, Any],
) -> dict[str, int]:
    """Restore embeddings when model ids match; skip on mismatch."""
    empty = {"restored": 0, "skipped_mismatch": 0, "skipped_no_api": 0}
    sidecar_model = embeddings.get("embedding_model_id")
    active = _active_embedding_model_id(store)
    vectors_raw = embeddings.get("vectors")
    if not isinstance(vectors_raw, dict) or not vectors_raw:
        return empty

    if active is not None and sidecar_model is not None and str(sidecar_model) != str(active):
        logger.warning(
            "memory_import_embeddings_model_mismatch",
            sidecar_model=sidecar_model,
            active_model=active,
        )
        return {"restored": 0, "skipped_mismatch": len(vectors_raw), "skipped_no_api": 0}

    setter = getattr(store, "set_embeddings", None)
    if not callable(setter):
        return {"restored": 0, "skipped_mismatch": 0, "skipped_no_api": len(vectors_raw)}

    clean: dict[str, list[float]] = {}
    for key, vec in vectors_raw.items():
        if isinstance(vec, list) and all(isinstance(x, (int, float)) for x in vec):
            clean[str(key)] = [float(x) for x in vec]
    if not clean:
        return empty

    model_id = str(sidecar_model or active or "imported")
    try:
        result = setter(clean, model_id=model_id)
    except Exception:
        logger.warning("memory_import_set_embeddings_failed", exc_info=True)
        return {"restored": 0, "skipped_mismatch": 0, "skipped_no_api": len(clean)}

    restored = (
        int(result.get("restored", result.get("set", 0)))
        if isinstance(result, dict)
        else (int(result) if isinstance(result, int) else len(clean))
    )
    skipped_mismatch = int(result.get("skipped_mismatch", 0)) if isinstance(result, dict) else 0
    return {"restored": restored, "skipped_mismatch": skipped_mismatch, "skipped_no_api": 0}


# ---------------------------------------------------------------------------
