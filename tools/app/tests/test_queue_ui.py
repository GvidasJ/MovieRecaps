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
