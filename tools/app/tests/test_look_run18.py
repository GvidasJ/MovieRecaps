"""Run 18, task 8: the "Mono + red" look -- the palette and one monospace font (theme.apply_look), its own pieces
(the pipeline dots, the block bars, the coverage strip, the Ctrl+K command box), the rows of the queue and New video
drawn as a list in it, Learn's three columns as one strip, and Settings -> "Classic look" to go back. The app's other
tests keep the classic look; every theme value changed here is put back.

Run: python -m pytest tools/app/tests/test_look_run18.py"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import jobs as J  # noqa: E402
from mr_app.ui import theme  # noqa: E402


@pytest.fixture
def mono(monkeypatch):
    """The Mono + red look for one test (every theme value back afterwards)."""
    saved = {k: v for k, v in vars(theme).items() if k.isupper() and not callable(v)}
    theme.apply_look("mono")
    yield theme
    for k, v in saved.items():
        setattr(theme, k, v)


@pytest.fixture
def qt(monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.ui import background as BG
    from mr_app.ui import motion as Mo
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    monkeypatch.setattr(BG, "call", lambda fn, done=None, failed=None, work=False: (done or (lambda _r: None))(fn()))
    yield app
    Mo.set_enabled(True)


def test_the_palette_you_chose_and_classic_changes_nothing(mono):
    assert (theme.BG, theme.TEXT, theme.DIM, theme.RED) == ("#0a0a0a", "#dcdcdc", "#6b6b6b", "#e5484d")
    assert (theme.GREEN, theme.AMBER) == ("#58c470", "#d6a12a") and theme.LINE == "#1a1a1a" and theme.LINE2 == "#333333"
    assert theme.pill_colours("PASS")[0] == theme.GREEN and theme.pill_colours("running")[0] == theme.AMBER
    assert theme.pill_colours("FAIL")[0] == theme.RED and theme.MONO_LOOK and theme.LOOK == "mono"
    assert "#0a0a0a" in theme.qss("x")


def test_classic_keeps_the_old_look():
    saved = {k: v for k, v in vars(theme).items() if k.isupper() and not callable(v)}
    try:
        theme.apply_look("classic")
        assert theme.BG == "#0e0e0e" and not theme.MONO_LOOK and theme.LOOK == "classic"
        assert theme.pill_colours("running")[0] == "#5b9cf5"
    finally:
        for k, v in saved.items():
            setattr(theme, k, v)


def test_the_setting_and_its_switch(tmp_path, monkeypatch):
    from mr_app.core import settings as S
    assert S.Settings().look == "mono"
    assert S.Settings.from_dict({"look": "classic"}).look == "classic"
    assert S.Settings.from_dict({"look": "pink"}).look == "mono"                  # anything else: the default


def test_the_pipeline_dots_and_the_block_bar(qt, mono):
    from mr_app.ui import mono_parts as MP
    d = MP.PipelineDots()
    d.set([("Download", MP.DONE), ("Convert", MP.DONE), ("Edit", MP.RUN), ("Done", MP.WAIT)])
    assert d.text() == "● DOWNLOAD ─── ● CONVERT ─── ◉ EDIT ─── ○ DONE"
    d.set([("Download", MP.DONE), ("Convert", MP.FAIL), ("Edit", MP.WAIT)])
    assert d.text() == "● DOWNLOAD ─── ✕ CONVERT ─── ○ EDIT"
    b = MP.BlockBar()
    b.set(0.68, MP.RUN, MP.eta_of("Placing every frame exactly -- 68 %, about 10 min left"))
    assert b.text() == "■■■■■■■□□□ 68%  ~10 MIN"
    b.set(1.0, MP.DONE)
    assert b.text() == "■■■■■■■■■■ 100%"
    for w in (d, b):
        w.resize(600, 18)
        w.grab()                                                              # it paints


def test_the_coverage_strip_reads_the_runs_cut_list(qt, mono, tmp_path):
    from mr_app.ui import mono_parts as MP
    (tmp_path / "extras").mkdir()
    (tmp_path / "extras" / "cutlist.json").write_text(json.dumps({
        "competitor": {"fps": "30/1", "frames": 300},
        "segments": [{"type": "raw", "comp_in": 0, "comp_out": 150}, {"type": "not_in_raw", "comp_in": 150,
                      "comp_out": 210}, {"type": "uncertain", "comp_in": 210, "comp_out": 300}]}), encoding="utf-8")
    dur, spans = MP.coverage_of(tmp_path)
    assert dur == 10.0 and spans == [(0.0, 5.0, "raw"), (5.0, 7.0, "not"), (7.0, 10.0, "unsure")]
    assert MP.coverage_of(tmp_path / "nothing") == (0.0, [])
    s = MP.CoverageStrip()
    s.set(dur, spans)
    s.resize(600, 22)
    s.grab()


def test_ctrl_k_goes_to_any_page_or_action(qt, mono):
    from PySide6.QtWidgets import QWidget

    from mr_app.ui import mono_parts as MP
    ran = []
    acts = [("New video", "", lambda: ran.append("download")), ("Learn", "", lambda: ran.append("learn")),
            ("Start the queue", "Space", lambda: ran.append("start")), ("Settings", "", lambda: ran.append("set"))]
    parent = QWidget()
    box = MP.CommandBox(parent, acts)
    assert box.shown() == ["New video", "Learn", "Start the queue", "Settings"]
    box.edit.setText("lea")
    assert box.shown() == ["Learn"]
    box.edit.setText("nv")                                                    # the letters in order
    assert box.shown() == ["New video"]
    box._run_current()                                                       # noqa: SLF001 - Enter
    assert ran == ["download"]
    assert "SPACE" not in box.list.item(0).text() if box.list.count() else True
    parent.deleteLater()


def test_the_rows_are_a_list_with_dots_blocks_and_red_failures(qt, mono, tmp_path, monkeypatch):
    from mr_app.tools import download_tool as D
    from mr_app.tools import match_cuts_tool as M
    monkeypatch.setattr(M, "seconds_of", lambda p: 12.0)
    ctx = SimpleNamespace(notify=lambda t, x: None, toast=lambda text, kind="ok": None, selected=[],
                          set_strip_queue=lambda n, running: None)
    ctx.select = ctx.selected.append
    q, dl = M.MatchCutsPage(ctx), D.DownloadPage(ctx)
    ctx.pages = {"match_cuts": q, "download": dl}
    try:
        for f in ("c.mp4", "r.mp4"):
            (tmp_path / f).write_bytes(b"x")
        j = J.Job("phone call", tmp_path / "c.mp4", tmp_path / "r.mp4", channel="Flick707", title="t")
        q._add_row(j)                                                          # noqa: SLF001
        j.state, j.percent, j.progress = J.RUNNING, 0.68, "Placing every frame exactly -- 68 %, about 10 min left"
        row = q.rows[j.id]
        row.refresh()
        assert row.pipe.text() == "● DOWNLOAD ─── ● CONVERT ─── ◉ EDIT ─── ○ DONE"
        assert row.blocks.text() == "■■■■■■■□□□ 68%  ~10 MIN" and row.pill.word() == "68%"
        assert not row.bar.isVisibleTo(row)                                     # the blocks instead of the thin bar
        j.state, j.result = J.DONE, {"label": "FAIL", "failures": ["x"], "reason_line": "the disk is full"}
        row.refresh()
        assert row.pill.word() == "FAILED" and "the disk is full" in row.note.text()
        assert theme.RED in row.note.styleSheet()                               # the reason in red
        it = D.Item("https://www.instagram.com/reel/x/", channel="Flick707", title="t", name="convert stop")
        it.state, it.failed_step, it.error = D.FAILED, "convert", "the sound is 3 s short"
        it.steps["download"] = D.Step(D.S_DONE, 1.0, "")
        it.steps["convert"] = D.Step(D.S_FAIL, 0.0, "failed")
        dl.items.append(it)
        dl.rows[it.id] = D.VideoRow(dl, it)
        r2 = dl.rows[it.id]
        r2.refresh()
        assert r2.list_row and r2.pipe.text().startswith("● DOWNLOAD ─── ✕ CONVERT")
        assert r2.pill.word() == "FAILED" and r2.b_retry.isVisibleTo(r2)        # its reason and its buttons
        for w in (row, r2):
            w.resize(1000, w.sizeHint().height())
            w.grab()
    finally:
        for w in (q, dl):
            getattr(w, "poll", SimpleNamespace(stop=lambda: None)).stop()
            w.deleteLater()
        qt.processEvents()
