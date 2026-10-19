"""Day 12, the Learn tab's three columns on the real widgets: "Waiting for your fix" (a finished edit waits by itself,
amber + the tab's count once rendered, a .prproj dropped on the card opens the check with its run, Skip), "Problems to
fix tonight" (the queue's hook writes them, Open log, Skip (my mistake)) and "Suggestions from last night" (I want
this -> requests.md, No). The hub is a tmp folder -- never D:\\Shared\\Projects.

Run: python -m pytest tools/app/tests/test_learn_columns_ui.py"""
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
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402

from mr_app.core import board as B  # noqa: E402
from mr_app.core import learn_hub as H  # noqa: E402
from mr_app.core import problems as PR  # noqa: E402
from mr_app.core import results as R  # noqa: E402
from mr_app.core import suggestions as SG  # noqa: E402
from mr_app.core import waiting as WT  # noqa: E402
from mr_app.tools import learn_board as LB  # noqa: E402
from mr_app.tools import learn_tool as LT  # noqa: E402
from mr_app.ui import motion as Mo  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump_until(app, cond, timeout_s: float = 8.0) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(cond())


def a_project(root: Path, name: str) -> Path:
    import json
    f = root / name
    (f / "edit" / "extras").mkdir(parents=True)
    (f / "edit" / "extras" / "batch_console.log").write_text("match_cuts result: FAIL\n", encoding="utf-8")
    (f / "project.json").write_text(json.dumps({"version": 1, "name": name, "channel": "LouLaughs",
                                                "created": "2026-10-19T01:00:00+03:00"}), encoding="utf-8")
    return f


@pytest.fixture
def env(qapp, tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(tmp_path / "local"))
    monkeypatch.setattr(LT.learnkit, "default_state_path", lambda: tmp_path / "learn_seen.json")
    toasts, badges = [], []
    ctx = SimpleNamespace(settings=SimpleNamespace(save_to=str(hub)), hub_folder=lambda: hub, pages={},
                          toast=lambda text, kind="ok": toasts.append((text, kind)),
                          set_badge=lambda key, text: badges.append((key, text)))
    pages = []

    def make_page():
        page = LT.LearnPage(ctx)
        page._watch.stop()                                      # noqa: SLF001
        page.resize(1000, 1400)
        page.show()
        ctx.pages["learn"] = page
        pages.append(page)
        return page
    yield SimpleNamespace(hub=hub, toasts=toasts, badges=badges, make_page=make_page, app=qapp, tmp=tmp_path,
                          ctx=ctx)
    for p in pages:
        p.close()
        p.deleteLater()
    Mo.set_enabled(True)
    qapp.processEvents()


def cards(col: LB.LCol) -> list[LB.LCard]:
    out = []
    for i in range(col.body.count()):
        w = col.body.itemAt(i).widget()
        if isinstance(w, LB.LCard) and not w.closing:
            out.append(w)
    return out


def titles(col: LB.LCol) -> list[str]:
    return [c.title.text() for c in cards(col)]


def filled(env):
    """A hub with two waiting edits (one rendered), two problems and two suggestions."""
    hub = env.hub
    p1, p2 = a_project(hub, "my-daddy-was-a-cop"), a_project(hub, "the-part-is-hers")
    WT.add(hub, WT.Entry("my-daddy-was-a-cop", project=str(p1), run=str(p1 / "edit"), channel="LouLaughs",
                         raw="raw.mp4", competitor="comp.mp4", finished="2026-10-19 03:02"))
    WT.add(hub, WT.Entry("the-part-is-hers", project=str(p2), run=str(p2 / "edit"), channel="Flick707",
                         finished="2026-10-19 01:40"))
    B.move(p1, "rendered")
    PR.add(hub, PR.Problem("video-by-darkhumor", check="stopped (exit 3)", run=str(p2 / "edit"), computer="laptop",
                           date="2026-10-19 03:06"))
    PR.add(hub, PR.Problem("video-by-mindculpt", check="FAIL: XML PERSON", run=str(p1 / "edit"), computer="laptop",
                           date="2026-10-19 02:44"))
    SG.add(hub, [SG.Suggestion("Re-frame onto the speaker", "saves 3 min", "half a night", "day 12 (2026-10-19)"),
                 SG.Suggestion("Warn before a full disk", "no failed nights", "small", "day 12 (2026-10-19)")])
    return p1, p2


def test_the_three_columns_show_their_cards_newest_first_with_red_counts(env):
    Mo.set_enabled(False)
    p1, _p2 = filled(env)
    page = env.make_page()
    page.refresh_columns()
    col = page.columns
    assert pump_until(env.app, lambda: len(cards(col.waiting)) == 2 and len(cards(col.suggestions)) == 2)
    assert titles(col.waiting) == ["my-daddy-was-a-cop", "the-part-is-hers"]            # newest first
    assert titles(col.problems) == ["video-by-darkhumor", "video-by-mindculpt"]
    assert titles(col.suggestions) == ["Re-frame onto the speaker", "Warn before a full disk"]
    for c in (col.waiting, col.problems, col.suggestions):
        assert c.badge.text() == "2" and not c.badge.isHidden()
    amber = cards(col.waiting)[0]
    assert amber.warn and "Rendered — add your project file" in amber.small.text()
    other = cards(col.waiting)[1]
    assert not other.warn and "will be video" in other.small.text()
    assert ("learn", "1") in env.badges                                     # the tab's small count: one rendered
    assert set(cards(col.problems)[0].buttons) == {"Open log", "Skip (my mistake)"}
    assert set(cards(col.suggestions)[0].buttons) == {"I want this", "No"}
    assert set(amber.buttons) == {"Drop .prproj here", "Skip"}
    assert col.waiting.head.text() == "WAITING FOR YOUR FIX"


def test_skip_my_mistake_and_open_log(env, monkeypatch):
    Mo.set_enabled(False)
    p1, _p2 = filled(env)
    page = env.make_page()
    page.refresh_columns()
    col = page.columns.problems
    assert pump_until(env.app, lambda: len(cards(col)) == 2)
    opened = []
    monkeypatch.setattr(os, "startfile", lambda f: opened.append(f), raising=False)
    cards(col)[1].buttons["Open log"].click()
    assert pump_until(env.app, lambda: opened)
    assert opened == [str(p1 / "edit" / "extras" / "batch_console.log")]
    cards(col)[0].buttons["Skip (my mistake)"].click()
    assert titles(col) == ["video-by-mindculpt"]                           # gone at once
    assert pump_until(env.app, lambda: [p.name for p in PR.read(env.hub)] == ["video-by-mindculpt"])
    assert "status: skipped (my mistake)" in (env.hub / PR.FILE).read_text(encoding="utf-8")
    assert pump_until(env.app, lambda: col.badge.text() == "1")


def test_i_want_this_goes_to_requests_and_no_removes(env):
    Mo.set_enabled(False)
    filled(env)
    page = env.make_page()
    page.refresh_columns()
    col = page.columns.suggestions
    assert pump_until(env.app, lambda: len(cards(col)) == 2)
    first = cards(col)[0]
    first.buttons["I want this"].click()
    assert "Added to your wishes" in first.small.text()
    assert pump_until(env.app, lambda: any(w.text.startswith("Re-frame onto the speaker")
                                           for w in H.read_wishes(env.hub)))
    assert pump_until(env.app, lambda: titles(col) == ["Warn before a full disk"])
    cards(col)[0].buttons["No"].click()
    assert pump_until(env.app, lambda: SG.read(env.hub) == [] and titles(col) == [])
    assert not any("full disk" in w.text for w in H.read_wishes(env.hub))
    assert col.badge.isHidden() and not col.empty.isHidden()


def test_a_prproj_dropped_on_a_waiting_card_opens_the_check_with_its_run_and_skip_removes_the_card(env, monkeypatch):
    Mo.set_enabled(False)
    _p1, p2 = filled(env)
    page = env.make_page()
    page.refresh_columns()
    col = page.columns.waiting
    assert pump_until(env.app, lambda: len(cards(col)) == 2)
    taken = []
    monkeypatch.setattr(page, "take", lambda items: taken.append(list(items)))
    fix = env.tmp / "the-part-is-hers_fixed.prproj"
    fix.write_bytes(b"x")
    card = cards(col)[1]
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(fix))])
    ev = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, md, Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier)
    card.dropEvent(ev)
    assert taken == [[fix, p2 / "edit"]]                                    # the run already chosen
    card.buttons["Skip"].click()
    assert titles(col) == ["my-daddy-was-a-cop"]
    assert pump_until(env.app, lambda: [e.name for e in WT.read(env.hub)] == ["my-daddy-was-a-cop"])


def test_the_queue_hook_adds_a_problem_for_a_fail_and_a_waiting_card_for_a_pass(env):
    Mo.set_enabled(False)
    page = env.make_page()
    hub = env.hub
    p = a_project(hub, "2026-10-19_video-by-x")
    job = SimpleNamespace(project=p, name="x", run_dir=p / "edit", raw=Path("raw.mp4"), competitor=Path("c.mp4"),
                          channel="MikeMemesYou", title="")
    LT.after_edit(env.ctx, job, {"label": R.FAIL, "failures": ["coverage: fail"], "run_dir": str(p / "edit"),
                                 "log": str(p / "edit" / "extras" / "batch_console.log"), "exit": 1})
    assert pump_until(env.app, lambda: titles(page.columns.problems) == ["2026-10-19_video-by-x"])
    LT.after_edit(env.ctx, job, {"label": R.WRONG_PAIR, "stop": "WRONG PAIR: no", "run_dir": str(p / "edit-2"),
                                 "exit": 2})
    LT.after_edit(env.ctx, job, {"label": R.PASS, "run_dir": str(p / "edit"), "exit": 0})
    assert pump_until(env.app, lambda: titles(page.columns.waiting) == ["2026-10-19_video-by-x"])
    assert len(PR.read(hub)) == 1


def test_the_match_cuts_queue_calls_the_hook_when_a_result_is_decided(qapp, tmp_path, monkeypatch):
    from mr_app.core import jobs as J
    from mr_app.tools import match_cuts_tool as M
    Mo.set_enabled(False)
    got = []
    monkeypatch.setattr(LT, "after_edit", lambda ctx, job, res: got.append((job.name, res["label"])))
    ctx = SimpleNamespace(set_strip_queue=lambda n, running: None, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": None)
    monkeypatch.setattr(M, "seconds_of", lambda p: 12.0 if "comp" in Path(p).name else 900.0)
    page = M.MatchCutsPage(ctx)
    d = tmp_path / "v"
    d.mkdir()
    (d / "comp.mp4").write_bytes(b"x")
    (d / "raw.mp4").write_bytes(b"x")
    j = J.Job("v", d / "comp.mp4", d / "raw.mp4")
    page._added_jobs(J.DropResult([j], None, []))                         # noqa: SLF001
    page._done(j.id, {"label": R.FAIL, "failures": ["x"]})                # noqa: SLF001
    assert got == [("v", R.FAIL)]
    page.deleteLater()
    qapp.processEvents()


def test_cards_open_and_close_by_fading_and_folding(env):
    Mo.set_enabled(True)
    col = LB.LCol("Test", "nothing")
    col.resize(300, 600)
    col.show()
    col.show_cards([dict(key="a", title="A", lines=["one"]), dict(key="b", title="B", lines=["two"])])
    a = col.cards["a"]
    assert a.maximumHeight() == 0 or a.reveal.value < 1.0                # opens from nothing
    assert pump_until(env.app, lambda: a.reveal.value >= 1.0, 3.0)
    assert a.maximumHeight() == LB.NO_MAX
    col.show_cards([dict(key="b", title="B2", lines=["two"])])
    assert a.closing and col.cards.keys() == {"b"} and col.cards["b"].title.text() == "B2"
    assert pump_until(env.app, lambda: col.body.indexOf(a) < 0, 3.0)      # folded away, then removed
    assert titles(col) == ["B2"] and col.badge.text() == "1"
    b = col.cards["b"]
    b.hover.to(1.0)                                                       # hover: only the border fades
    h0 = b.geometry()
    assert pump_until(env.app, lambda: b.hover.value >= 1.0, 2.0)
    assert b.geometry() == h0
    col.close()


def test_the_columns_stack_on_a_narrow_page(env):
    Mo.set_enabled(False)
    g = LB.LearnColumns()
    g.resize(1000, 400)
    g.show()
    env.app.processEvents()
    pos = [g.grid.getItemPosition(g.grid.indexOf(c)) for c in g.cols]
    assert [(r, c) for r, c, _rs, _cs in pos] == [(0, 0), (0, 1), (0, 2)]
    g.resize(500, 400)
    env.app.processEvents()
    pos = [g.grid.getItemPosition(g.grid.indexOf(c)) for c in g.cols]
    assert [(r, c) for r, c, _rs, _cs in pos] == [(0, 0), (1, 0), (2, 0)]
    g.close()
