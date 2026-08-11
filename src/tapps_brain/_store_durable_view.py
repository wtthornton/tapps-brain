"""``@durable_pass`` — run a maintenance method over the full durable set (TAP-5648).

Maintenance surfaces reconcile the durable set rather than the capped cache
view, so they hydrate durable overflow into ``_entries``.  Before this they left
it there, and a following ``count()`` or ``snapshot()`` reported above
``max_entries`` until eviction caught up.

The decorator scopes that hydration to the call: ``MemoryStore._durable_view``
merges on entry and trims the cache back to the cap on exit, including on the
exception path.

It lives here rather than in ``_store_base`` because that module is deliberately
runtime-free — annotations and ``TYPE_CHECKING`` stubs only — and a decorator is
executable code.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Concatenate, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from tapps_brain._store_base import _MemoryStoreBase


def durable_pass[S: _MemoryStoreBase, **P, R](
    method: Callable[Concatenate[S, P], R],
) -> Callable[Concatenate[S, P], R]:
    """Run *method* with the full durable set hydrated, then restore the cap.

    Replaces a bare ``self._merge_durable_entries(allow_over_cap=True)`` at the
    top of a maintenance method.  That call hydrated the overflow and never gave
    it back; this both hydrates and restores, so callers no longer have to
    reason about the cache side effects of a read.
    """

    @functools.wraps(method)
    def _wrapped(self: S, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._durable_view():
            return method(self, *args, **kwargs)

    # functools.wraps returns _Wrapped, whose __call__ names its first parameter
    # `self`; Callable cannot express a named parameter, so the two are
    # structurally identical but not assignable. The cast asserts only that.
    return cast("Callable[Concatenate[S, P], R]", _wrapped)
