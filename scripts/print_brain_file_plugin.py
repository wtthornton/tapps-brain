"""Pytest plugin: print tapps_brain.__file__ from inside the test process.

TAP-7398 evidence requirement — the editable-install trap means the primary
checkout's .venv can import tapps_brain from the wrong tree, so the resolved
module path must be printed from inside the same process that ran the suite,
not from a separate command run afterward.
"""

from __future__ import annotations


def pytest_configure(config: object) -> None:
    import tapps_brain

    print(f"\nTAPPS_BRAIN_FILE={tapps_brain.__file__}\n")
