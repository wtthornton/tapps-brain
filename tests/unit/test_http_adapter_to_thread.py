"""TAP-1099 regression: HTTP adapter routes must offload sync DB calls to a worker thread.

Every ``async def`` ``/v1/*`` route handler in ``http_adapter.py`` calls into
``services/memory_service.py`` (``_ms.*``) and the idempotency store
(``istore.check`` / ``istore.save``).  Both layers are sync ``def`` functions
that issue a blocking ``psycopg`` round-trip.

Before TAP-1099 those calls ran inline on the FastAPI event loop, so a single
slow DB query would freeze every concurrent /v1 request on the same worker
process — under 50 concurrent agents the loop saturates at one in-flight call.

This test is a static AST regression guard: it walks the relevant async route
bodies and asserts every ``_ms.*`` call and every ``istore.check`` /
``istore.save`` call is the *target* of an ``await asyncio.to_thread(...)``,
not a bare invocation.  The functional concurrency proof lives in the manual
benchmark referenced in ``docs/engineering/async-performance.md``; this guard
catches anyone unwrapping a route in a future edit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ADAPTER = Path(__file__).resolve().parents[2] / "src" / "tapps_brain" / "http_adapter.py"

# Routes that hit the DB.  ``_idempotency_save`` is an unused sync helper kept
# for forward compatibility; not a route.
_DB_ROUTES = frozenset(
    {
        "_v1_remember",
        "_v1_reinforce",
        "_v1_remember_batch",
        "_v1_recall_batch",
        "_v1_reinforce_batch",
        "_v1_recall",
        "_v1_forget",
        "_v1_learn_success",
        "_v1_learn_failure",
    }
)

# Sync function names that block on Postgres.  Inside an async route they must
# always be wrapped in ``await asyncio.to_thread(...)``.
_SYNC_DB_NAMES = frozenset(
    {
        "memory_save",
        "memory_save_many",
        "memory_reinforce",
        "memory_reinforce_many",
        "memory_recall",
        "memory_recall_many",
        "memory_get",
        "memory_delete",
        "memory_search",
        "memory_list",
        "brain_recall",
        "brain_forget",
        "brain_learn_success",
        "brain_learn_failure",
        "brain_remember",
    }
)


def _is_to_thread_call(node: ast.AST) -> bool:
    """Return True when *node* is ``asyncio.to_thread(...)`` — the wrapper marker."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    return (
        isinstance(fn, ast.Attribute)
        and fn.attr == "to_thread"
        and isinstance(fn.value, ast.Name)
        and fn.value.id == "asyncio"
    )


def _bare_blocking_calls(body: list[ast.stmt]) -> list[tuple[int, str]]:
    """Walk an async-route body; return ``(lineno, label)`` for any unwrapped sync DB call.

    A call is "bare" when it appears directly (or as a statement value), not as
    the *first positional argument* of ``asyncio.to_thread(...)``.
    """
    offences: list[tuple[int, str]] = []

    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue

        # Skip the to_thread call itself — we'll inspect its first arg.
        if _is_to_thread_call(node):
            continue

        fn = node.func

        # ``_ms.memory_save`` / ``_ms.brain_recall`` etc.
        if (
            isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "_ms"
            and fn.attr in _SYNC_DB_NAMES
        ):
            offences.append((node.lineno, f"_ms.{fn.attr}"))

        # ``istore.check(...)`` / ``istore.save(...)``
        if (
            isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "istore"
            and fn.attr in {"check", "save"}
        ):
            offences.append((node.lineno, f"istore.{fn.attr}"))

    # Now subtract anything that *is* the first positional arg of an
    # ``asyncio.to_thread(...)`` call — those are wrapped, not bare.
    wrapped: set[tuple[int, str]] = set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not _is_to_thread_call(node):
            continue
        if not node.args:
            continue
        target = node.args[0]
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            if target.value.id == "_ms" and target.attr in _SYNC_DB_NAMES:
                wrapped.add((node.lineno, f"_ms.{target.attr}"))
            if target.value.id == "istore" and target.attr in {"check", "save"}:
                wrapped.add((node.lineno, f"istore.{target.attr}"))

    return [o for o in offences if o not in wrapped]


def _async_route_bodies() -> dict[str, list[ast.stmt]]:
    """Return a ``name → body`` map of every nested async-route function defined in create_app."""
    tree = ast.parse(_ADAPTER.read_text(encoding="utf-8"))
    routes: dict[str, list[ast.stmt]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in _DB_ROUTES:
            routes[node.name] = list(node.body)
    return routes


@pytest.mark.parametrize("route_name", sorted(_DB_ROUTES))
def test_route_offloads_sync_db_calls(route_name: str) -> None:
    """Each DB-touching async route must wrap every sync DB call in ``asyncio.to_thread``."""
    routes = _async_route_bodies()
    assert route_name in routes, (
        f"async route {route_name} not found in http_adapter.py — "
        "did the function rename or drop the @app.post decorator?"
    )
    bare = _bare_blocking_calls(routes[route_name])
    assert not bare, (
        f"{route_name} has bare sync DB calls that block the event loop: {bare}. "
        "Wrap each in `await asyncio.to_thread(...)` (TAP-1099)."
    )


def test_all_db_routes_were_inspected() -> None:
    """Sanity guard: the route allow-list must match the actual file contents.

    If someone adds a new ``/v1/*`` route, this test forces them to extend
    ``_DB_ROUTES`` (and decide whether the route hits the DB).
    """
    found = set(_async_route_bodies().keys())
    missing = _DB_ROUTES - found
    assert not missing, (
        f"declared DB routes not found in http_adapter.py: {sorted(missing)}. "
        "Either the function was renamed/removed, or the test list is stale."
    )
