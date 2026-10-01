"""Shared pytest configuration for match_cuts.

* registers the ``slow`` marker; slow tests run only with ``--runslow`` or ``MATCH_CUTS_SLOW=1``
* session fixtures ``synthetic_mini`` / ``synthetic_full`` / ``synthetic_film24``: the synthetic RAW +
  competitor + truth generated (or reused from cache) by ``tests/synth.py`` into ``work/synthetic/<profile>``
* ``venv_python``: the interpreter the CLI tests must use

Keep this file light: it is imported by every test module (no heavy imports at module level).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]                       # .../MovieRecaps
SYNTH_ROOT = REPO_ROOT / "work" / "synthetic"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

if str(TESTS_DIR) not in sys.path:                     # `import synth` from any test module
    sys.path.insert(0, str(TESTS_DIR))


def _slow_enabled(config) -> bool:
    return bool(config.getoption("--runslow")) or os.environ.get("MATCH_CUTS_SLOW", "") not in ("", "0")


def pytest_addoption(parser):
    parser.addoption("--runslow", action="store_true", default=False,
                     help="run slow end-to-end tests (also enabled by MATCH_CUTS_SLOW=1)")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: end-to-end tests that generate and process synthetic video "
                                       "(run with --runslow or MATCH_CUTS_SLOW=1)")


def pytest_collection_modifyitems(config, items):
    if _slow_enabled(config):
        return
    skip = pytest.mark.skip(reason="slow test: run with --runslow or MATCH_CUTS_SLOW=1")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def venv_python() -> str:
    """Absolute path of the project venv interpreter (falls back to the running interpreter)."""
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _synthetic(profile: str) -> dict:
    import synth
    return synth.make_synthetic(SYNTH_ROOT / profile, profile)


@pytest.fixture(scope="session")
def synthetic_mini() -> dict:
    """{raw, competitor, truth, id, frame_png, out_dir, summary} of the 'mini' synthetic pair (cached)."""
    return _synthetic("mini")


@pytest.fixture(scope="session")
def synthetic_full() -> dict:
    """{raw, competitor, truth, id, frame_png, out_dir, summary} of the 'full' synthetic pair (cached)."""
    return _synthetic("full")


@pytest.fixture(scope="session")
def synthetic_film24() -> dict:
    """{raw, competitor, truth, id, frame_png, out_dir, summary} of the 'film24' synthetic pair (cached): RAW
    24000/1001 on a 30 fps NLE timeline with the real-run regimes (DESIGN §6.1)."""
    return _synthetic("film24")
