"""The window's chrome on the real widgets (night 11, items 2 and 9): Settings are saved and applied at once
(Animations off, goals, "Save projects to" checked for reachability), the motivation strip counts today's Shorts and
says "goal hit" only when it happens while the app runs, the quote line fades to a new quote and a click asks for the
next one, and the keys (Space, Del, Ctrl+V) do nothing while you type.

Run: python -m pytest tools/app/tests/test_chrome_ui.py"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402

from mr_app.ui import chrome  # noqa: E402
from mr_app.ui import motion as M  # noqa: E402
from mr_app.ui.motivation_strip import MotivationStrip  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture(autouse=True)
def animations_on():
    M.set_enabled(True)
    yield
    M.set_enabled(True)


def pump_until(app, cond, timeout_s: float = 6.0) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(cond())


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"save_to": str(tmp_path / "shared"), "intro": False}), encoding="utf-8")
    (tmp_path / "shared").mkdir()
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(f))
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(tmp_path / "local"))
    from mr_app.tools import tools
    from mr_app.ui.main_window import MainWindow
    win = MainWindow(tools())
    win.show()
    yield win, f
    win.hide()
    win.deleteLater()
    qapp.processEvents()


def test_settings_are_saved_and_applied_at_once(qapp, window, tmp_path, monkeypatch):
    win, f = window
    from mr_app.ui import settings_dialog as SD
    other = tmp_path / "elsewhere"
    other.mkdir()
    notes = []

    def fake_exec(d):
        d.save.setText(str(other))
        assert pump_until(qapp, lambda: "reachable" in d.root_note.text())      # checked in the background
        notes.append(d.root_note.text())
        d.anim.set("off")
        d.daily.setValue(4)
        d.monthly.setValue(100)
        d.every.setValue(45)
        d._ok()                                                                  # noqa: SLF001 - "Save"
        return d.result()
    monkeypatch.setattr(SD.SettingsDialog, "exec", fake_exec)
    win.open_settings()
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert saved["save_to"] == str(other) and saved["animations"] is False
    assert (saved["daily_goal"], saved["monthly_goal"], saved["upload_check_minutes"]) == (4, 100, 45)
    assert notes and notes[0].endswith("-- reachable")
    assert not M.enabled()                                                       # applied at once
    assert win.settings.daily_goal == 4


def test_a_folder_that_is_not_there_is_said_to_be_unreachable(qapp, window, tmp_path, monkeypatch):
    win, _f = window
    from mr_app.ui import settings_dialog as SD
    d = SD.SettingsDialog(win, win.settings)
    d.save.setText(str(tmp_path / "no" / "such" / "folder"))
    assert pump_until(qapp, lambda: "not reachable now" in d.root_note.text())
    d.reject()


def test_the_strip_counts_today_and_says_goal_hit_only_when_it_happens_while_the_app_runs(qapp):
    now = datetime(2026, 10, 18, 12, 0)
    ups = [(datetime(2026, 10, 18, h, 0), ch) for h, ch in ((8, "LouLaughs"), (9, "LouLaughs"),
                                                             (10, "MikeMemesYou"), (11, "Flick707"))]
    s = MotivationStrip()
    hits = []
    s.goal_hit.connect(hits.append)
    s.refresh(ups, 6, 150, now=now)
    assert s.today.count == 4 and s.today.text == "2 more to hit today's goal"
    assert dict(s.today.split) == {"LouLaughs": 2, "MikeMemesYou": 1, "Flick707": 1}
    assert s.month.count == 4 and s.month.label
    more = ups + [(datetime(2026, 10, 18, 11, 30), "LouLaughs"), (datetime(2026, 10, 18, 11, 45), "Flick707")]
    s.refresh(more, 6, 150, now=now)
    assert hits == [6] and s.today.text == "Goal hit. Want a bonus one?"
    s.refresh(more, 6, 150, now=now)
    assert hits == [6]                                                           # said once
    fresh = MotivationStrip()                                                    # a start with the goal already hit
    fresh.goal_hit.connect(hits.append)
    fresh.refresh(more, 6, 150, now=now)
    assert hits == [6]


def test_the_quote_line_fades_to_the_new_quote_and_a_click_asks_for_the_next(qapp):
    q = chrome.QuoteLine()
    q.resize(800, 40)
    q.show()
    q.show_quote("Morning", "I just never thought I couldn't.", "McQueen")
    assert q.text == "I just never thought I couldn't." and q.op.value == 1.0         # the first one: at once
    q.show_quote("Afternoon", "Small daily improvements.", "")
    assert q.text == "I just never thought I couldn't."                             # fading out first ...
    assert pump_until(qapp, lambda: q.text == "Small daily improvements." and q.op.value == pytest.approx(1.0), 3)
    clicks = []
    q.clicked.connect(lambda: clicks.append(1))
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(10, 10), QPointF(10, 10), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(q, ev)
    assert clicks == [1]
    q.close()


def test_the_keys_do_nothing_while_you_type(qapp, window, monkeypatch):
    win, _f = window
    mc = win.pages["match_cuts"]
    started, removed = [], []
    monkeypatch.setattr(mc, "start", lambda: started.append(1))
    monkeypatch.setattr(mc, "remove_row_under_mouse", lambda: removed.append(1))
    win.activateWindow()
    win._key_start()                                                             # noqa: SLF001
    win._key_delete()                                                            # noqa: SLF001
    assert started == [1] and removed == [1]
    field = QtWidgets.QLineEdit(win)
    field.show()
    field.setFocus()
    monkeypatch.setattr(QtWidgets.QApplication, "focusWidget", staticmethod(lambda: field))
    win._key_start()                                                             # noqa: SLF001
    win._key_delete()                                                            # noqa: SLF001
    assert started == [1] and removed == [1]                                     # typing: the keys are text
