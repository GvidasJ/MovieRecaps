"""Day 12, helper "fixes": the review's findings, each with its test -- "Turn off the PC" never turns the PC off while
work runs (the warning closes and comes back after it), background jobs count as work, a refused shutdown is said
in plain words, an unreadable Premiere blocks the shutdown, and the rest (torrent files, exit code 3, waiting
projects, folder renames, Clean up, Swap)."""
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

from mr_app.core import shutdown as SD  # noqa: E402
from mr_app.tools import download_tool as D  # noqa: E402

from test_newvideo_ui import page  # noqa: E402,F401 - the Download page fixture


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------------------------------------------------------------
# 1. the PC never turns off while new work runs
# ---------------------------------------------------------------------------------------------------------------------

def test_turn_off_asks_stop_last_and_never_turns_off_when_work_started(monkeypatch):
    calls = []
    monkeypatch.setattr(SD, "SHUTDOWN", lambda: calls.append("off"))
    monkeypatch.setattr(SD, "CHECKS", [])
    assert SD.turn_off(stop=lambda: True) == [SD.STOPPED_BY_WORK] and calls == []
    assert SD.turn_off(stop=lambda: False) == [] and calls == ["off"]


def test_the_warning_closes_without_turning_off_when_work_starts_during_the_countdown(page, monkeypatch):  # noqa: F811
    _app()
    busy = {"on": False}
    w = D.ShutdownWarning(page, seconds=5, working=lambda: busy["on"])
    told = []
    w.stopped_for_work.connect(lambda: told.append(1))
    w.start()
    w.timer.stop()
    w._tick()                                                    # noqa: SLF001
    assert "4 s" in w.text.text() and w.isVisible()
    busy["on"] = True
    w._tick()                                                    # noqa: SLF001
    assert not w.isVisible() and w.stop_now and told == [1] and not w.cancelled
    w.left = 1
    w._tick()                                                    # noqa: SLF001 - closed: never counts on
    assert told == [1]


def test_the_30_s_retry_stops_when_work_started_and_a_late_turn_off_is_not_done(page, monkeypatch):  # noqa: F811
    _app()
    busy = {"on": False}
    w = D.ShutdownWarning(page, seconds=1, working=lambda: busy["on"])
    w.start()
    w.timer.stop()
    w._after(["Premiere has unsaved work -- save it, then the PC turns off"])   # noqa: SLF001
    assert "tries again" in w.note.text()
    busy["on"] = True                                            # a new video added; Premiere saved meanwhile
    w._retry()                                                   # noqa: SLF001
    assert w.for_work and not w.isVisible()
    w2 = D.ShutdownWarning(page, seconds=1, working=lambda: False)
    w2.start()
    w2.timer.stop()
    w2._after([SD.STOPPED_BY_WORK])                              # noqa: SLF001 - the background turn_off's answer
    assert w2.for_work and not w2.isVisible()


def test_the_background_turn_off_reads_the_warnings_stop_flag(page, monkeypatch):  # noqa: F811
    _app()
    from mr_app.ui import background as BG
    calls = []
    monkeypatch.setattr(SD, "SHUTDOWN", lambda: calls.append("off"))
    monkeypatch.setattr(SD, "CHECKS", [])
    ran = []

    def call(fn, done=None, failed=None, **_k):
        w.stop_for_work()                                        # work starts while the checks run
        ran.append(fn())
        done(ran[-1])
    monkeypatch.setattr(BG, "call", call)
    w = D.ShutdownWarning(page, seconds=1, working=lambda: False)
    w.start()
    w.timer.stop()
    w._tick()                                                    # noqa: SLF001
    assert ran == [[SD.STOPPED_BY_WORK]] and calls == []


def test_the_page_closes_the_warning_when_work_starts_and_shows_a_fresh_one_after_it(page, monkeypatch):  # noqa: F811
    _app()
    busy = {"on": False}
    monkeypatch.setattr(page, "anything_working", lambda: busy["on"])
    page.offpc.setChecked(True)
    page._worked = True                                          # noqa: SLF001 - something ran
    page._check_shutdown()                                       # noqa: SLF001
    first = page.warning
    assert first is not None and first.isVisible()
    first.timer.stop()
    busy["on"] = True                                            # a video is added during the 60 s
    page._check_shutdown()                                       # noqa: SLF001
    assert not first.isVisible() and first.stop_now and page.offpc.isChecked()
    busy["on"] = False                                           # its work is done
    page._check_shutdown()                                       # noqa: SLF001
    assert page.warning is not first and page.warning.isVisible() and page.warning.left == 60
    page.warning.timer.stop()
    page.warning.reject()


# ---------------------------------------------------------------------------------------------------------------------
# 6. a refused shutdown is said in plain words, and tried again
# ---------------------------------------------------------------------------------------------------------------------

def test_a_failed_shutdown_command_raises_in_plain_words():
    def refused(*_a, **_k):
        return SimpleNamespace(returncode=5, stderr="Access is denied.(5)\r\n", stdout="")
    with pytest.raises(SD.ShutdownFailed, match="Access is denied"):
        SD._real_shutdown(run=refused)                           # noqa: SLF001

    def missing(*_a, **_k):
        raise FileNotFoundError("shutdown.exe not found")
    with pytest.raises(SD.ShutdownFailed, match="could not be asked"):
        SD._real_shutdown(run=missing)                           # noqa: SLF001
    SD._real_shutdown(run=lambda *_a, **_k: SimpleNamespace(returncode=0, stderr="", stdout=""))  # noqa: SLF001


def test_the_warning_says_a_refused_shutdown_and_tries_again(page, monkeypatch):  # noqa: F811
    _app()
    from mr_app.ui import background as BG
    monkeypatch.setattr(SD, "CHECKS", [])

    def refuse():
        raise SD.ShutdownFailed("Windows did not turn off (code 1190: a shutdown is already under way)")
    monkeypatch.setattr(SD, "SHUTDOWN", refuse)

    def call(fn, done=None, failed=None, **_k):
        try:
            r = fn()
        except Exception as e:                                   # noqa: BLE001 - as background.call words it
            failed(f"{type(e).__name__}: {e}")
            return
        done(r)
    monkeypatch.setattr(BG, "call", call)
    retries = []
    monkeypatch.setattr(D.QTimer, "singleShot", lambda ms, fn: retries.append(ms))
    w = D.ShutdownWarning(page, seconds=1, working=lambda: False)
    w.start()
    w.timer.stop()
    w._tick()                                                    # noqa: SLF001
    assert w.text.text() == "Not turned off yet"
    assert "could not be turned off: Windows did not turn off (code 1190" in w.note.text()
    assert "ShutdownFailed" not in w.note.text() and retries == [30000]
    w.reject()


# ---------------------------------------------------------------------------------------------------------------------
# 7. Premiere running but not readable: a blocker
# ---------------------------------------------------------------------------------------------------------------------

def test_premiere_running_but_not_readable_blocks_the_shutdown(monkeypatch):
    from mr_app.core import premiere

    def broken():
        raise OSError("the windows could not be read")
    monkeypatch.setattr(premiere, "state", broken)
    monkeypatch.setattr(premiere, "_pids", lambda: [1234])
    assert SD.premiere_unreadable() is True
    monkeypatch.setattr(premiere, "_pids", lambda: [])
    assert SD.premiere_unreadable() is False                     # not running: nothing to lose
    monkeypatch.setattr(premiere, "state", lambda: premiere.State(running=True, title=None))   # no main window read
    assert SD.premiere_unreadable() is True
    monkeypatch.setattr(premiere, "state", lambda: premiere.State(running=True, title="Adobe Premiere Pro 2024 - "
                                                                  r"C:\x\edit.prproj"))
    assert SD.premiere_unreadable() is False
    monkeypatch.setattr(premiere, "state", lambda: premiere.State())
    assert SD.premiere_unreadable() is False
    words = [w for w, fn in SD.CHECKS if fn is SD.premiere_unreadable]
    assert words and words[0].startswith("can't tell whether Premiere has unsaved work")


# ---------------------------------------------------------------------------------------------------------------------
# 2. background jobs flagged as work count for "Turn off the PC"
# ---------------------------------------------------------------------------------------------------------------------

def test_a_background_job_flagged_as_work_is_counted_until_it_returns():
    import threading

    from PySide6.QtCore import QThreadPool

    from mr_app.ui import background as BG
    _app()
    go = threading.Event()
    before = BG.working()
    BG.call(lambda: go.wait(10), None, None, work=True)
    assert BG.working() == before + 1
    BG.call(lambda: None)                                        # not flagged: never counted
    assert BG.working() == before + 1
    go.set()
    QThreadPool.globalInstance().waitForDone(10000)
    assert BG.working() == before

    def broken():
        raise OSError("the shared folder went away")
    BG.call(broken, None, None, work=True)                       # a failed job is not counted for ever
    QThreadPool.globalInstance().waitForDone(10000)
    assert BG.working() == before


def test_anything_working_counts_background_work_and_a_running_torrent(page, monkeypatch):  # noqa: F811
    from mr_app.ui import background as BG
    assert page.anything_working() is False
    monkeypatch.setattr(BG, "working", lambda: 1)                # e.g. a project being copied to the shared folder
    assert page.anything_working() is True
    monkeypatch.setattr(BG, "working", lambda: 0)
    it = SimpleNamespace(state=D.READY, torrent_job=None)
    page.items.append(it)
    for state, busy in (("", True), ("downloading", True), ("stalled", True), ("paused", False),
                        ("finished", False), ("failed", False), ("removed", False)):
        it.torrent_job = SimpleNamespace(status={"state": state} if state else {})
        assert page.anything_working() is busy, state
    page.items.remove(it)


def test_publishing_clean_up_and_change_headline_are_flagged_as_work():
    """The calls that copy or change project files for a while pass work=True (read from the source: the windows
    that make them need a whole app)."""
    root = Path(__file__).resolve().parents[1] / "mr_app"
    mw = (root / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert mw.count("BG.call(work, done, failed, work=True)") == 1                       # project_done -> publish
    assert mw.count("BG.call(work, done, lambda _e: None, work=True)") == 1              # retry_pending -> publish
    board = (root / "tools" / "board_tool.py").read_text(encoding="utf-8")
    assert board.count("work=True)") == 3                         # Clean up, Change headline, Set headline
    mc = (root / "tools" / "match_cuts_tool.py").read_text(encoding="utf-8")
    assert mc.count("work=True)") == 2
    dl = (root / "tools" / "download_tool.py").read_text(encoding="utf-8")
    assert dl.count("\"fail\"), work=True)") == 1                  # the row's Change headline
