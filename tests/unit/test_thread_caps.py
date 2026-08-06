"""Regression guards for the native-thread caps in tests/conftest.py.

The caps stop each pytest-xdist worker from spawning a core-count OpenMP/BLAS
pool when torch loads (via sentence-transformers). If these fail, the caps in
tests/conftest.py were removed, moved below a torch-importing top-level import,
or overridden in the environment.
"""

from __future__ import annotations

import os

import pytest


def test_native_thread_env_caps_are_set() -> None:
    assert os.environ["OMP_NUM_THREADS"] == "1"
    assert os.environ["OPENBLAS_NUM_THREADS"] == "1"
    assert os.environ["MKL_NUM_THREADS"] == "1"
    assert os.environ["TOKENIZERS_PARALLELISM"] == "false"


def test_torch_intra_op_pool_is_capped() -> None:
    torch = pytest.importorskip("torch")
    assert torch.get_num_threads() == 1
