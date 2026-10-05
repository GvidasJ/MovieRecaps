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
VENV_PYTHON = next((p for p in (REPO_ROOT / ".venv" / "Scripts" / "python.exe", REPO_ROOT / ".venv" / "bin" / "python")
                    if p.exists()), REPO_ROOT / ".venv" / "bin" / "python")       # Windows, then Linux / macOS

if str(TESTS_DIR) not in sys.path:                     # `import synth` from any test module
    sys.path.insert(0, str(TESTS_DIR))
# never the After Effects of this machine: also for the CLI runs the end-to-end tests start (they inherit it)
os.environ["MATCH_CUTS_NO_AE"] = "1"


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


@pytest.fixture(autouse=True)
def _no_real_after_effects(monkeypatch):
    """Tests never use the After Effects installed on this machine: a test that runs the whole CLI would otherwise
    send its build_ae_project.jsx to it (``AfterFX.exe -r``, into the user's open After Effects) and wait up to
    10 minutes for a project that never comes. The search with explicit ``roots`` (its own tests) still runs."""
    from match_cuts import pipeline
    real = pipeline.find_after_effects

    def no_installed_ae(system=None, roots=None):
        if roots is None:
            return {"ae_app": None, "aerender": None, "ae_version": None, "ae_app_name": None}
        return real(system, roots)
    no_installed_ae.real = real                          # the search itself, for its own tests
    monkeypatch.setattr(pipeline, "find_after_effects", no_installed_ae)


@pytest.fixture(autouse=True)
def _match_cuts_logger_as_found():
    """A test that runs the CLI in this process (cli.main -> common.setup_logging) leaves handlers on the
    ``match_cuts`` logger: one writing to that test's captured stderr -- closed when the test ends, so every later
    log line became a 'Logging error ... I/O operation on closed file' -- and one holding its run's log file open.
    Every test gets the logger back as it found it."""
    import logging
    lg = logging.getLogger("match_cuts")
    handlers, level, propagate = list(lg.handlers), lg.level, lg.propagate
    yield
    for h in list(lg.handlers):
        if h not in handlers:
            lg.removeHandler(h)
            try:
                h.close()
            except Exception:  # noqa: BLE001 - a handler that cannot close is still gone
                pass
    for h in handlers:
        if h not in lg.handlers:
            lg.addHandler(h)
    lg.setLevel(level)
    lg.propagate = propagate


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
