"""The Learn tab on the real widgets (night 11, item 3): a drop fills the check card and "Learn this one" puts the fix
in the learn queue and moves its project's card to Fixed (never backwards); every drop starts a new form; a run that
cannot be found is said plainly with "Pick the run here"; a new fix found in a project is offered ("Learn from
this?"); the standing rules and the wishes go to the shared files; "Copy tonight's prompt" copies it.
learnkit's own searching and copying is tested in test_learn_core.py -- here it is stood in for where it would need
real Premiere projects.

Run: python -m pytest tools/app/tests/test_learn_ui.py"""
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

from mr_app.core import board as B  # noqa: E402
from mr_app.core import learn_hub as H  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from mr_app.tools import learn_tool as LT  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def pump_until(app, cond, timeout_s: float = 8.0) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(cond())


@pytest.fixture
def env(qapp, tmp_path, monkeypatch):
    local, shared, hub = tmp_path / "local", tmp_path / "shared", tmp_path / "hub"
    for d in (local, shared, hub):
        d.mkdir()
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(local))
    monkeypatch.setattr(LT.learnkit, "default_state_path", lambda: tmp_path / "learn_seen.json")
    toasts, badges = [], []
    ctx = SimpleNamespace(settings=SimpleNamespace(save_to=str(shared)), hub_folder=lambda: hub, pages={},
                          toast=lambda text, kind="ok": toasts.append((text, kind)),
                          set_badge=lambda key, text: badges.append((key, text)))
    pages = []

    def make_page():
        page = LT.LearnPage(ctx)
        page._watch.stop()                                      # noqa: SLF001 - the tests call watch() themselves
        page.resize(1000, 1200)
        page.show()
        pages.append(page)
        return page
    yield SimpleNamespace(local=local, shared=shared, hub=hub, toasts=toasts, badges=badges, make_page=make_page,
                          app=qapp, tmp=tmp_path, ctx=ctx)
    for p in pages:
        p.close()
        p.deleteLater()
    qapp.processEvents()


def a_form(tmp: Path, project: Path | None, run: Path | None = None, name: str = "video13", ready: bool = True,
           problems=()):
    prproj = tmp / "video13_fixed.prproj"
    prproj.write_bytes(b"x")
    card = SimpleNamespace(raw="raw.mp4 · 12:03 · 1920x1080", competitor="competitor.mp4 · 0:41",
                           captions="12 of 22 changed (4 words, 4 groupings, 4 timings)",
                           cuts="0 moved, 0 added, 2 removed", position="0 of 7 clips moved or zoomed",
                           fixed_version="video13_fixed.prproj, saved 18 Oct 02:00",
                           tool_version="output\\laptop_video13, 17 Oct 23:10, laptop, Fast", notes=[],
                           problems=[], ignored=["all audio", "the headline PNG"]) if run is not None else None
    plan = SimpleNamespace(name=name, problems=lambda: [])
    return SimpleNamespace(prproj=prproj, run=run, run_how="your project plays its media" if run else "",
                           project_folder=project, name=name, card=card, notes=["the run was found by itself"],
                           problems=list(problems), ready=ready, plan=plan)


def test_a_drop_fills_the_check_card_and_learn_queues_it_and_moves_the_card_to_fixed(env, monkeypatch):
    proj = PJ.create(env.local, name="clip one")
    proj.set_step("download", PJ.DONE)
    run = env.tmp / "run"
    run.mkdir()
    form = a_form(env.tmp, proj.folder, run)
    calls = []
    monkeypatch.setattr(LT.learnkit, "new_form", lambda items, roots, queue_folder=None: form)
    monkeypatch.setattr(LT.learnkit, "queue_item", lambda plan, card=None: "the queue line")
    monkeypatch.setattr(LT.learnkit, "copy_and_queue",
                        lambda plan, progress=None, hub=None, item=None: (calls.append((plan.name, hub, item)), "")[1])
    page = env.make_page()
    page.take([form.prproj])
    assert pump_until(env.app, lambda: page.form is form)
    assert "12 of 22 changed" in page.rows["captions"].text() and "0 moved" in page.rows["cuts"].text()
    assert "raw.mp4" in page.rows["raw"].text() and "laptop_video13" in page.rows["run"].text()
    assert "Not compared (you do these by hand): all audio; the headline PNG" in page.note.text()
    assert page.name.text() == "video13" and page.b_learn.isEnabled() and page.problem.text() == ""
    page.learn()
    assert pump_until(env.app, lambda: calls and ("video13 is in the learn queue ✓", "ok") in env.toasts)
    assert calls == [("video13", env.hub, "the queue line")]
    assert pump_until(env.app, lambda: B.card_of(proj.folder).column == "fixed")


def test_a_learned_fix_never_moves_a_card_backwards(env):
    proj = PJ.create(env.local, name="clip one")
    B.move(proj.folder, "uploaded")
    assert LT._to_fixed(proj.folder) is False                                       # noqa: SLF001
    assert B.card_of(proj.folder).column == "uploaded"
    other = PJ.create(env.local, name="clip two")
    other.set_step("download", PJ.DONE)
    assert LT._to_fixed(other.folder) is True and B.card_of(other.folder).column == "fixed"   # noqa: SLF001


def test_every_drop_starts_a_new_form_and_a_missing_run_is_said_plainly(env, monkeypatch):
    first = a_form(env.tmp, None, env.tmp, name="video13")
    second = a_form(env.tmp, None, None, name="video14", ready=False)
    forms = [first, second]
    monkeypatch.setattr(LT.learnkit, "new_form", lambda items, roots, queue_folder=None: forms.pop(0))
    page = env.make_page()
    page.take([first.prproj])
    assert pump_until(env.app, lambda: page.form is first)
    page.take([second.prproj])
    assert page.name.text() == "" and not page.b_learn.isEnabled()                 # the old form is gone at once
    assert pump_until(env.app, lambda: page.form is second)
    assert "I can't find the run for this project — pick it here" in page.problem.text()
    assert page.pick_run.isVisibleTo(page) and page.pick_run.text() == "Pick the run here..."
    assert not page.b_learn.isEnabled() and page.rows["captions"].text() == ""


def test_a_new_fix_in_a_project_is_offered_and_not_now_is_remembered(env, monkeypatch):
    proj = env.local / "2026-10-18_clip"
    proj.mkdir()
    fixed = proj / "fixed.prproj"
    fixed.write_bytes(b"x")
    seen = []
    monkeypatch.setattr(LT.learnkit, "new_fixed", lambda roots, state, queue_folder=None: [(proj, fixed)])
    monkeypatch.setattr(LT.learnkit, "mark_seen", lambda state, prproj: seen.append(Path(prproj)) or "")
    page = env.make_page()
    page.watch()
    assert pump_until(env.app, lambda: page.offers.count() == 1)
    offer = page.offers.itemAt(0).widget()
    assert "learn from this?" in offer.findChildren(QtWidgets.QLabel)[0].text()
    assert ("learn", "1") in env.badges and any("New fix found" in t for t, _k in env.toasts)
    page.watch()                                                                    # found again: offered once
    assert pump_until(env.app, lambda: True, 0.3) and page.offers.count() == 1
    no = [b for b in offer.findChildren(QtWidgets.QPushButton) if b.text() == "Not now"][0]
    no.click()
    assert seen == [fixed] and page.offers.count() == 0 and env.badges[-1] == ("learn", "")


def test_learn_from_this_opens_the_form_for_that_fix(env, monkeypatch):
    proj = env.local / "2026-10-18_clip"
    proj.mkdir()
    form = a_form(env.tmp, proj, env.tmp)
    taken = []
    monkeypatch.setattr(LT.learnkit, "new_fixed", lambda roots, state, queue_folder=None: [(proj, form.prproj)])
    monkeypatch.setattr(LT.learnkit, "new_form",
                        lambda items, roots, queue_folder=None: (taken.append(list(items)), form)[1])
    page = env.make_page()
    page.watch()
    assert pump_until(env.app, lambda: page.offers.count() == 1)
    yes = [b for b in page.offers.itemAt(0).widget().findChildren(QtWidgets.QPushButton)
           if b.text() == "Learn from this"][0]
    yes.click()
    assert pump_until(env.app, lambda: page.form is form)
    assert taken == [[form.prproj]]


def test_the_standing_rules_and_the_wishes_go_to_the_shared_files(env):
    page = env.make_page()
    page.refresh_hub()
    assert pump_until(env.app, lambda: "requests.md" in page.where.text())
    assert (env.hub / H.WISHES_FILE).is_file()                                      # made once, with the Later items
    page.rules.setPlainText("keep the competitor's cut points")
    page.save_rules()
    assert pump_until(env.app, lambda: ("Standing rules saved ✓", "ok") in env.toasts)
    assert "keep the competitor's cut points" in H.read_rules(env.hub)
    page.wish.setPlainText("a Stats tab with the channels' views")
    page.add_wish()
    assert pump_until(env.app, lambda: "a Stats tab with the channels' views" in page.wishes.text())
    assert page.wish.toPlainText() == ""
    assert any("a Stats tab with the channels' views" in w.text for w in H.read_wishes(env.hub))


def test_copy_tonights_prompt_puts_it_on_the_clipboard(env):
    H.add_wish(env.hub, "the board in a smaller font")
    page = env.make_page()
    QtWidgets.QApplication.clipboard().setText("")
    page.copy_prompt()
    assert pump_until(env.app, lambda: "the board in a smaller font" in QtWidgets.QApplication.clipboard().text())
    assert any("Tonight's prompt copied" in t for t, _k in env.toasts)
