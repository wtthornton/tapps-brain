"""Flywheel service functions (EPIC-070 STORY-070.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def flywheel_process(
    store: Any, project_id: str, agent_id: str, *, since: str = ""
) -> dict[str, Any]:
    from tapps_brain.flywheel import FeedbackProcessor, FlywheelConfig

    return FeedbackProcessor(FlywheelConfig()).process_feedback(
        store,
        since=since.strip() or None,
    )


def flywheel_gaps(
    store: Any, project_id: str, agent_id: str, *, limit: int = 10, semantic: bool = False
) -> dict[str, Any]:
    gaps = store.knowledge_gaps(limit=limit, semantic=semantic)
    return {"gaps": [g.model_dump(mode="json") for g in gaps], "count": len(gaps)}


def flywheel_report(
    store: Any, project_id: str, agent_id: str, *, period_days: int = 7
) -> dict[str, Any]:
    rep = store.generate_report(period_days=period_days)
    return {
        "rendered_text": rep.rendered_text,
        "structured_data": rep.structured_data,
    }


def flywheel_evaluate(
    store: Any, project_id: str, agent_id: str, *, suite_path: str, k: int = 5
) -> dict[str, Any]:
    from tapps_brain.evaluation import EvalSuite, evaluate

    p = Path(suite_path).expanduser().resolve()
    if not p.exists():
        return {"error": "not_found", "path": str(p)}
    if p.is_dir():
        suite = EvalSuite.load_beir_dir(p)
    elif p.suffix.lower() in (".yaml", ".yml"):
        suite = EvalSuite.load_yaml(p)
    else:
        return {"error": "invalid_suite", "message": "Expected directory or YAML"}
    report = evaluate(store, suite, k=k)
    return report.model_dump(mode="json")


def flywheel_hive_feedback(
    store: Any, project_id: str, agent_id: str, *, threshold: int = 3
) -> dict[str, Any]:
    from tapps_brain.flywheel import aggregate_hive_feedback, process_hive_feedback

    hs = getattr(store, "_hive_store", None)
    agg = aggregate_hive_feedback(hs)
    proc = process_hive_feedback(hs, threshold=threshold)
    return {
        "aggregate": None if agg is None else agg.model_dump(mode="json"),
        "process": proc,
    }
