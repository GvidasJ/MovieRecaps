"""Night 11, item 2 in the window: a finished project goes to "Save projects to" by itself; when that folder cannot
be reached it is kept here with an amber note; the start-up pass publishes finished projects never published
(core.publish is faked: these tests are about the window's wiring, publish.py has its own)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mr_app.core import projects as P  # noqa: E402
from mr_app.core import publish  # noqa: E402


@pytest.fixture()
def win(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(tmp_path / "local"))
    from mr_app.tools import tools
    from mr_app.ui import main_window as MW
    monkeypatch.setattr(MW, "_save_root", lambda s: tmp_path / "shared")
    w = MW.MainWindow(tools())
    w.toasts = []
    monkeypatch.setattr(w, "toast", lambda text, kind="ok": w.toasts.append((text, kind)))
    yield w
    w.close()
    app.processEvents()


def _wait(cond, s=5.0):
    t0 = time.perf_counter()
    while not cond() and time.perf_counter() - t0 < s:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    return cond()


def test_a_finished_project_is_published_by_itself(win, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(publish, "publish_or_queue",
                        lambda folder, root, local=None: calls.append((Path(folder), Path(root))) or
                        (SimpleNamespace(summary=lambda: "ok"), ""))
    win.project_done(SimpleNamespace(project=tmp_path / "local" / "p1"))
    assert _wait(lambda: win.toasts)
    assert calls == [(tmp_path / "local" / "p1", tmp_path / "shared")]
    assert win.toasts[-1][0].startswith("Saved to") and win.toasts[-1][1] == "ok"


def test_the_shared_folder_away_keeps_it_here_with_an_amber_note(win, tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "publish_or_queue",
                        lambda folder, root, local=None: (None, "saved on this computer for now -- no access"))
    win.project_done(SimpleNamespace(project=tmp_path / "local" / "p2"))
    assert _wait(lambda: win.toasts)
    assert win.toasts[-1] == ("saved on this computer for now -- no access", "note")


def test_the_start_up_pass_publishes_finished_projects_never_published(win, tmp_path, monkeypatch):
    local = tmp_path / "local"
    done = P.create(local, competitor_url="https://youtu.be/abcdefghijk", name="done one")
    done.set_step("edit", P.DONE, result="PASS")
    waiting = P.create(local, competitor_url="https://youtu.be/abcdefghijq", name="still waiting")
    published = P.create(local, competitor_url="https://youtu.be/abcdefghijr", name="published one")
    published.set_step("edit", P.DONE, result="PASS")
    published.update(published={"to": "x"})
    went = []
    monkeypatch.setattr(publish, "reachable", lambda root, t=5.0: True)
    monkeypatch.setattr(publish, "pending", lambda local_root: [])
    monkeypatch.setattr(publish, "publish_or_queue",
                        lambda folder, root, local_root=None: went.append(Path(folder).name) or
                        (SimpleNamespace(summary=lambda: "ok"), ""))
    win.retry_pending()
    assert _wait(lambda: win.toasts)
    assert went == [done.folder.name] and waiting.folder.name not in went
    assert "moved to" in win.toasts[-1][0]
