"""The Match cuts queue's night-11 behaviour (no screen: Qt's offscreen platform): rows dragged to a new place change
the order (and the slate's scene numbers), "Run again (Thorough)" adds the next take, Del removes the row under the
mouse but never the running one, a finished row shows its result, the question card makes the job you pick, a
WRONG PAIR result is a red pill. Run: python -m pytest tools/app/tests/test_queue_ui.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from mr_app.core import jobs as J  # noqa: E402
from mr_app.tools import match_cuts_tool as M  # noqa: E402
from mr_app.ui import motion as Mo  # noqa: E402


@pytest.fixture()
def page(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)                                   # no fades: the tests look at the end states
    toasts = []
    ctx = SimpleNamespace(set_strip_queue=lambda n, running: None, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": toasts.append((text, kind)))
    monkeypatch.setattr(M, "seconds_of", lambda p: 12.0 if "comp" in Path(p).name else 900.0)
    p = M.MatchCutsPage(ctx)
    p.toasts = toasts
    yield p
    Mo.set_enabled(True)
    p.deleteLater()
    app.processEvents()


def _job(page, tmp_path, name: str) -> J.Job:
    d = tmp_path / name
    d.mkdir()
    (d / "comp.mp4").write_bytes(b"x")
    (d / "raw.mp4").write_bytes(b"x")
    j = J.Job(name, d / "comp.mp4", d / "raw.mp4")
    page._added_jobs(J.DropResult([j], None, []))           # noqa: SLF001 - as a drop does
    return j


def _slate(row) -> str:
    return row.slate.text()


def test_a_row_dropped_higher_changes_the_order_and_the_scene_numbers(page, tmp_path):
    a, b, c = (_job(page, tmp_path, n) for n in ("a", "b", "c"))
    assert "SCENE <span style='color:#ff7a59'>03" in _slate(page.rows[c.id])
    page.move_job(c.id, 0)                                  # c dropped above a
    assert [j.name for j in page.jobs] == ["c", "a", "b"]
    assert [page.list.itemAt(i).widget().job.name for i in range(page.list.count())] == ["c", "a", "b"]
    assert ">01<" in _slate(page.rows[c.id]) and ">03<" in _slate(page.rows[b.id])
    page.move_job(c.id, 3)                                  # ... and back to the end
    assert [j.name for j in page.jobs] == ["a", "b", "c"]
    assert ("Order changed ✓", "ok") in page.toasts


def test_run_again_thorough_is_the_next_take_of_the_same_video(page, tmp_path):
    a = _job(page, tmp_path, "video4")
    a.state = J.DONE
    a.result = {"label": "PASS"}
    page.run_again(a)
    again = page.jobs[-1]
    assert again.mode == J.THOROUGH and again.take == 2 and again.competitor == a.competitor
    assert again.name == "video4-2" and again.state == J.READY
    assert "TAKE <span style='color:#ff7a59'>2" in _slate(page.rows[again.id])


def test_a_wrong_pair_or_input_stop_row_can_run_anyway_with_the_input_checks_skipped(page, tmp_path, monkeypatch):
    from mr_app.core import run as RN
    from mr_app.ui import menu as MN
    a = _job(page, tmp_path, "video5")
    a.state = J.DONE
    a.result = {"label": "WRONG PAIR", "stop": "WRONG PAIR: This competitor doesn't seem to come from this RAW"}
    shown = []

    class Menu:                                                             # the menu's items, not a real popup
        def __init__(self, _parent, items):
            self.items = list(items)

        def popup(self, _pos):
            shown.append(self)
    monkeypatch.setattr(MN, "FadeMenu", Menu)
    row = page.rows[a.id]
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent
    row.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(5, 5)))
    texts = [it[0] for it in shown[0].items if it]
    assert "Run anyway (skip the input checks)" in texts
    page.run_again(a, a.mode, force=True)
    again = page.jobs[-1]
    assert again.force_inputs and again.mode == a.mode and again.take == 2
    assert ("Queued again -- the input checks skipped", "ok") in page.toasts
    cmd = RN.command(Path("py"), Path("c"), Path("r"), Path("o"), Path("w"), True, 100.0,
                     force_inputs=again.force_inputs)
    assert cmd[-1] == "--force-inputs"
    a.result = {"label": "FAIL", "stop": "The inputs look wrong, so I stopped before converting anything: - the "
                "competitor (0:31) is almost as long as the RAW (0:34)"}      # night 6's input checks: the same
    shown.clear()
    row.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(5, 5)))
    assert "Run anyway (skip the input checks)" in [it[0] for it in shown[0].items if it]
    a.result = {"label": "PASS"}                                            # a PASS row: no such item
    shown.clear()
    row.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(5, 5)))
    assert "Run anyway (skip the input checks)" not in [it[0] for it in shown[0].items if it]


def test_remove_never_takes_the_running_row(page, tmp_path):
    a, b = _job(page, tmp_path, "a"), _job(page, tmp_path, "b")
    a.state = J.RUNNING
    page.remove_job(a)
    page.remove_job(b)
    assert [j.name for j in page.jobs] == ["a"]


def test_a_finished_row_shows_its_result_and_a_wrong_pair_is_red(page, tmp_path):
    a = _job(page, tmp_path, "a")
    a.state, a.result = J.DONE, {"label": "WRONG PAIR", "failures": ["WRONG PAIR: This competitor doesn't seem to "
                                                                     "come from this RAW"]}
    row = page.rows[a.id]
    row.refresh()
    assert row.pill.text() == "WRONG PAIR" and row.pill._to[0] == "#e5484d"                  # noqa: SLF001
    assert "doesn't seem to come from this RAW" in row.note.text()
    assert not row.mode.isVisibleTo(row) and not row.tpl.isVisibleTo(row)    # a finished row: no edit buttons


def test_dropped_files_go_into_a_project_and_the_job_waits_for_it(page, tmp_path, monkeypatch):
    """Night 11: a RAW and a competitor dropped from anywhere end up in a project folder (core.jobs.project_for_job,
    in the background); until they are there the job does not start."""
    import time
    proj = tmp_path / "projects" / "2026-10-18_clip"

    def fake_project_for_job(job, root=None, progress=None, cancel=None):
        proj.mkdir(parents=True)
        for f in (job.competitor, job.raw):
            (proj / Path(f).name).write_bytes(b"x")
        job.competitor, job.raw = proj / Path(job.competitor).name, proj / Path(job.raw).name
        job.project, job.out_dir, job.run_name = proj, proj, "edit"
        if progress is not None:
            progress("raw", 1.0, "linked")
        return proj
    monkeypatch.setattr(J, "project_for_job", fake_project_for_job, raising=False)
    started = []
    monkeypatch.setattr(M, "JobThread", lambda *a, **k: started.append(a) or SimpleNamespace(
        progress=SimpleNamespace(connect=lambda f: None), done=SimpleNamespace(connect=lambda f: None),
        start=lambda: None))
    d = tmp_path / "anywhere"
    d.mkdir()
    (d / "comp.mp4").write_bytes(b"x")
    (d / "raw.mp4").write_bytes(b"x")
    j = J.Job("clip", d / "comp.mp4", d / "raw.mp4")
    page._added_jobs(J.DropResult([j], None, []))           # noqa: SLF001
    assert j.preparing                                      # the files are being put into the project ...
    page.running = True
    page._next()                                            # noqa: SLF001
    assert not started and page.running                     # ... so it waits (not "the queue is done")
    t0 = time.perf_counter()
    while j.preparing and time.perf_counter() - t0 < 5:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.01)
    assert j.project == proj and j.raw == proj / "raw.mp4" and j.run_name == "edit"
    assert started                                          # and then it ran


def test_the_question_card_makes_the_job_you_pick(page, tmp_path):
    d = tmp_path / "pair"
    d.mkdir()
    one, two = d / "episode one.mp4", d / "clip two.mp4"
    one.write_bytes(b"x")
    two.write_bytes(b"x")
    page._added_jobs(SimpleNamespace(jobs=[], waiting=None, notes=[], ask=[(one, two)]))   # noqa: SLF001
    card = page.asks.itemAt(0).widget()
    assert isinstance(card, M.AskCard)
    pick = [b for b in card.findChildren(QtWidgets.QPushButton) if b.text().startswith("clip two.mp4")][0]
    pick.click()                                            # "clip two" is the RAW
    assert len(page.jobs) == 1 and page.jobs[0].raw == two and page.jobs[0].competitor == one


def test_a_question_is_on_its_card_and_not_said_twice(page, tmp_path):
    d = tmp_path / "pair"
    d.mkdir()
    one, two = d / "a.mp4", d / "b.mp4"
    one.write_bytes(b"x")
    two.write_bytes(b"x")
    why = "can't tell which one is the RAW: a.mp4 (2:13) and b.mp4 (2:41) are about as long"
    q = SimpleNamespace(videos=[one, two], why=why, name="video7", source="folder video7")
    page._added_jobs(SimpleNamespace(jobs=[], waiting=None, ask=[q],              # noqa: SLF001
                                     notes=["x.txt: not a video", f"video7: {why}"]))
    assert isinstance(page.asks.itemAt(0).widget(), M.AskCard)
    assert page.status.text() == "x.txt: not a video"
