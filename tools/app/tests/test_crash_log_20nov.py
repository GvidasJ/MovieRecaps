"""20 Nov: the app suite no longer dies in Qt, and the tests no longer write into this PC's real crash.log.

* app._crash_log writes into the app's own folder (settings.app_dir: MOVIERECAPS_APPDATA, else %APPDATA%\\MovieRecaps)
  -- it read %APPDATA% itself, so the tests that start the app wrote "the app started / closed normally" into the
  real crash.log;
* the suite runs on ONE QApplication (conftest.py): a bare QCoreApplication made by one file made the next file's
  first window abort the whole process (0xC0000409, "QWidget: Cannot create a QWidget without QApplication");
* the torrent picker's Esc filter does not raise when Qt calls it while the picker is taken down (two errors in the
  real crash.log on 19 Nov).

Run: python -m pytest tools/app/tests/test_crash_log_20nov.py"""
from __future__ import annotations

import atexit
import faulthandler
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_the_crash_log_goes_to_the_apps_own_folder_not_the_real_appdata(tmp_path, monkeypatch):
    from mr_app import app as A
    real, own = tmp_path / "Roaming", tmp_path / "own"
    monkeypatch.setenv("APPDATA", str(real))
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(own))
    files = []
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)                 # (put back after the test)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(faulthandler, "enable", lambda file=None, **_k: files.append(file))
    monkeypatch.setattr(atexit, "register", lambda *_a, **_k: None)
    A._crash_log()                                                         # noqa: SLF001
    for f in files:
        f.close()
    assert (own / "crash.log").read_text(encoding="utf-8").strip().startswith("=== ")
    assert "the app started" in (own / "crash.log").read_text(encoding="utf-8")
    assert not (real / "MovieRecaps" / "crash.log").exists()


def test_without_the_tests_folder_it_is_still_appdata_movierecaps(tmp_path, monkeypatch):
    from mr_app.core import settings as S
    monkeypatch.delenv("MOVIERECAPS_APPDATA", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert S.app_dir() == tmp_path / "Roaming" / "MovieRecaps"             # (where the app has always written it)


def test_the_whole_suite_has_one_real_qapplication():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance()
    assert isinstance(app, QtWidgets.QApplication)                         # never a bare QCoreApplication
    import conftest
    assert app is conftest._QAPP                                           # noqa: SLF001 - held to the end


def test_the_torrent_pickers_esc_filter_says_nothing_while_it_is_taken_down(tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from mr_app.ui import torrent_panel as TP
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    w = TP.TorrentPicker("magnet:?xt=urn:btih:" + "a" * 40, tmp_path / "dl", run=lambda *a, **k: None)
    box = w.find_box
    box.setText("x")
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(box, esc)
    assert box.text() == "" and not errors                                 # Esc clears the search box
    del w.find_box                                                         # (as when the picker is taken down)
    QtWidgets.QApplication.sendEvent(box, QEvent(QEvent.Type.Leave))     # (an event that only meets the filter)
    QtWidgets.QApplication.sendEvent(box, esc)
    assert not errors


def test_pythons_garbage_collector_runs_only_between_tests_on_the_main_thread():
    """(conftest.py: an access violation in pyside6.abi3.dll when it ran in a worker thread at a random moment)"""
    import gc
    assert not gc.isenabled()
    assert threading.current_thread() is threading.main_thread()
