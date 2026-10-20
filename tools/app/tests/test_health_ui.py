"""Day 13 (items 1.3 and 1.5) on the real widgets: the top strip's free space (its colours fade, its width never
changes, a click opens the Board's Clean up), the amber version line (glides open, Fix now -> Fixing ... -> Fixed),
the window's wiring (read in the background, Fix now never while a video is made) and the queue that never starts
without room (the numbers, and Clean up). Nothing is installed, nothing reads the real disk's numbers.

Run: python -m pytest tools/app/tests/test_health_ui.py"""
from __future__ import annotations

import json
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
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QMouseEvent  # noqa: E402

from mr_app.core import jobs as J  # noqa: E402
from mr_app.core import space as S  # noqa: E402
from mr_app.core import versions as V  # noqa: E402
from mr_app.ui import health as H  # noqa: E402
from mr_app.ui import motion as M  # noqa: E402
from mr_app.ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


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


def _click(w) -> None:
    pos = QPointF(w.width() / 2, w.height() / 2)
    for kind in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
        QtWidgets.QApplication.sendEvent(w, QMouseEvent(kind, pos, w.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


# ---------------------------------------------------------------------------------------------------------------------
# the strip's free space
# ---------------------------------------------------------------------------------------------------------------------

def test_the_free_space_turns_amber_and_red_with_a_fade_and_never_changes_its_width(qapp):
    w = H.SpaceStrip([("C:", "C:\\"), ("D:", r"D:\Shared\Projects")])
    w.show()
    size0 = w.size()
    assert w.text() == "Free C: ? · D: ?"
    w.set_drives([S.DriveFree("C:", 64 * S.GB, 931 * S.GB), S.DriveFree("D:", int(2.6 * 1024 * S.GB))])
    assert w.text() == "Free C: 64.0 GB · D: 2.6 TB" and w.colour("C:") == QColor(theme.DIM)
    w.set_drives([S.DriveFree("C:", 9 * S.GB, 931 * S.GB), S.DriveFree("D:", 25 * S.GB)])
    mid = w.colour("C:")
    assert mid != QColor(theme.RED)                                   # it fades: not at once
    assert pump_until(qapp, lambda: w.colour("C:") == QColor(theme.RED) and w.colour("D:") == QColor(theme.AMBER))
    assert w.size() == size0 and w.level() == "very low"              # nothing moved
    assert "under 10 GB: almost full" in w.toolTip() and "Clean up" in w.toolTip()
    got = []
    w.clicked.connect(lambda: got.append(1))
    _click(w)
    assert got == [1]


def test_a_network_share_is_named_by_its_share(qapp):
    w = H.SpaceStrip([("C:", "C:\\"), (r"\\192.168.101.11\Shared", r"\\192.168.101.11\Shared\Projects")])
    w.set_drives([S.DriveFree("C:", 48 * S.GB), S.DriveFree(r"\\192.168.101.11\Shared", None)])
    assert w.text() == "Free C: 48.0 GB · Shared ?" and "can't be read now" in w.toolTip()


# ---------------------------------------------------------------------------------------------------------------------
# the version line
# ---------------------------------------------------------------------------------------------------------------------

def _prob(key="scenedetect", text="PySceneDetect 0.6.4 is too old (0.7.1 needed)", fix=V.PIP, **kw):
    return V.Problem(key, key, text, fix, **kw)


def test_the_version_line_glides_open_fixes_and_glides_closed(qapp):
    host = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(host)
    line = H.VersionLine()
    lay.addWidget(line)
    host.resize(1000, 200)
    host.show()
    assert line.height() == 0 and not line.is_open()
    line.show_problems([_prob(), _prob("yt-dlp", "yt-dlp is missing (the YouTube downloader)")])
    assert line.height() < H.OPEN_H                                   # it glides: not at once
    assert pump_until(qapp, lambda: line.height() == H.OPEN_H)
    assert line.text().startswith("This computer differs from what the project expects: PySceneDetect 0.6.4 is "
                                  "too old (0.7.1 needed) · yt-dlp is missing")
    assert line.b_fix.isVisibleTo(line) and line.b_fix.text() == "Fix now" and not line.b_log.isVisibleTo(line)
    line.show_fixing("installing PySceneDetect ...")
    assert line.state == H.FIXING and line.text() == "Fixing ... installing PySceneDetect ..."
    assert not line.b_fix.isVisibleTo(line) and line.b_log.isVisibleTo(line) and line.height() == H.OPEN_H
    line.show_fixed()
    assert pump_until(qapp, lambda: line._now()[1] == QColor(theme.GREEN_SOFT))      # noqa: SLF001
    line.close_line()
    assert pump_until(qapp, lambda: line.height() == 0 and not line.label.isVisibleTo(line))


def test_a_step_by_hand_is_said_without_fix_now(qapp):
    line = H.VersionLine()
    M.set_enabled(False)
    line.show_problems([_prob("ffmpeg", "FFmpeg is missing (reads and converts the videos)", V.HAND,
                              hand="install FFmpeg once (PowerShell: winget install Gyan.FFmpeg)")])
    assert line.height() == H.OPEN_H and not line.b_fix.isVisibleTo(line)
    assert line.text().endswith("-- install FFmpeg once (PowerShell: winget install Gyan.FFmpeg)")


# ---------------------------------------------------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"save_to": str(tmp_path / "shared"), "intro": False}), encoding="utf-8")
    (tmp_path / "shared").mkdir()
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(f))
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(tmp_path / "local"))
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    from mr_app.tools import tools
    from mr_app.ui.main_window import MainWindow
    win = MainWindow(tools())
    win.show()
    yield win
    win.hide()
    win.deleteLater()
    qapp.processEvents()


def test_the_window_reads_the_free_space_in_the_background(window, qapp, monkeypatch):
    seen = []

    def fake(folders, timeout_s=8.0):
        seen.append(list(folders))
        return [S.DriveFree(n, 8 * S.GB, 100 * S.GB) for n, _f in folders]
    monkeypatch.setattr(S, "read_strip", fake)
    window.refresh_space()
    assert pump_until(qapp, lambda: window.strip_space.level() == "very low")
    assert seen and seen[-1][0][0] == S.drive_of(os.environ.get("SystemDrive", "C:") + "\\")
    n = len(seen)
    window.space_changed()                                   # a run ended: read again, soon
    assert pump_until(qapp, lambda: len(seen) > n)


def test_the_version_check_opens_the_line_and_fix_now_fixes(window, qapp, monkeypatch):
    calls = []
    probs = [[_prob()], []]                                  # before Fix now, after it
    monkeypatch.setattr(V, "check_in_process", lambda python=None, timeout_s=0: probs.pop(0) if probs else [])

    def run_setup(python, log, say=None, popen=None):
        calls.append(str(log))
        say("installing PySceneDetect ...")
        return 0, ""
    monkeypatch.setattr(V, "run_setup", run_setup)
    window.check_versions()
    assert pump_until(qapp, lambda: window.version_line.state == H.PROBLEM)
    assert "PySceneDetect 0.6.4 is too old" in window.version_line.text()
    window.version_line.b_fix.click()
    assert pump_until(qapp, lambda: window.version_line.state == H.FIXED)
    assert calls and calls[0].endswith("setup_app.log")
    assert window.version_line.text().startswith("Fixed ✓")


def test_fix_now_waits_while_a_video_is_being_made(window, qapp, monkeypatch):
    monkeypatch.setattr(V, "run_setup", lambda *a, **k: pytest.fail("pip must not run during a run"))
    mc = window.pages["match_cuts"]
    monkeypatch.setattr(mc, "busy", lambda: True)
    window.version_line.show_problems([_prob()])
    window.fix_versions()
    assert window.version_line.state == H.FAILED and "Fix now waits" in window.version_line.text()


def test_not_enough_room_offers_the_board_s_clean_up(window, qapp, monkeypatch):
    asked, cleaned = [], []
    monkeypatch.setattr(H, "ask_cleanup", lambda parent, text, title="": (asked.append(text), True)[1])
    board = window.pages["board"]
    monkeypatch.setattr(board, "clean_up", lambda: cleaned.append(1))
    window.offer_cleanup("not enough free space on C: -- about 26.7 GB is needed there, only 9.2 GB is free")
    assert asked and "only 9.2 GB is free" in asked[0] and cleaned == [1]
    assert window.current() == "board"
    window.strip_space.clicked.emit()                        # the strip's click: the same Clean up
    assert cleaned == [1, 1]


# ---------------------------------------------------------------------------------------------------------------------
# the queue: never started without room
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def page(qapp, monkeypatch):
    from mr_app.tools import match_cuts_tool as MC
    offered, toasts = [], []
    ctx = SimpleNamespace(set_strip_queue=lambda n, running: None, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": toasts.append((text, kind)), offer_cleanup=offered.append,
                          set_progress=lambda share, state="normal": None)
    monkeypatch.setattr(MC, "seconds_of", lambda p: 12.0 if "comp" in Path(p).name else 900.0)
    p = MC.MatchCutsPage(ctx)
    p.offered, p.toasts = offered, toasts
    yield p
    p.deleteLater()
    qapp.processEvents()


def _job(page, tmp_path, name: str) -> J.Job:
    d = tmp_path / name
    d.mkdir()
    (d / "comp.mp4").write_bytes(b"x")
    (d / "raw.mp4").write_bytes(b"x")
    j = J.Job(name, d / "comp.mp4", d / "raw.mp4")
    page._added_jobs(J.DropResult([j], None, []))           # noqa: SLF001 - as a drop does
    return j


def test_the_queue_does_not_start_without_room_and_offers_clean_up(page, tmp_path, monkeypatch):
    from mr_app.tools import match_cuts_tool as MC
    monkeypatch.setattr(MC, "QMessageBox", lambda *a, **k: pytest.fail("no 'Start anyway' question for space"))
    _job(page, tmp_path, "borat")
    room = ["not enough free space on C: -- about 26.7 GB is needed there, only 9.2 GB is free (borat: ...) -- free "
            "some space (Board -> Clean up), then press Start again"]
    page._checked(["borat: the competitor looks long"], {}, room)          # noqa: SLF001
    assert not page.running and page.status.text().startswith("not started -- not enough free space on C:")
    assert page.offered and "only 9.2 GB is free" in page.offered[0] and "Also worth a look" in page.offered[0]
    assert ("Not started: not enough free space", "fail") in page.toasts


def test_a_run_without_room_just_before_its_start_waits_again_and_the_queue_stops(page, tmp_path, monkeypatch):
    from mr_app.tools import match_cuts_tool as MC
    a, b = _job(page, tmp_path, "borat"), _job(page, tmp_path, "rush-hour")
    why = "not enough free space on C: -- about 26.7 GB is needed there, only 9.2 GB is free -- free some space"
    monkeypatch.setattr(MC.space, "run_problem", lambda *args, **kw: why)
    th = MC.JobThread(a, {}, tmp_path / "out")
    got = []
    th.done.connect(lambda i, res: got.append((i, res)))
    th.run()                                                  # (in this thread: the check before the tool starts)
    assert got and got[0][1]["no_space"] and got[0][1]["failures"] == [why]
    page.running = True
    a.state = J.RUNNING
    page._done(a.id, got[0][1])                               # noqa: SLF001
    assert a.state == J.READY and b.state == J.READY and not page.running
    assert page.status.text().startswith("stopped before borat -- not enough free space")
    assert page.offered and page.offered[0].startswith("borat: not enough free space")
