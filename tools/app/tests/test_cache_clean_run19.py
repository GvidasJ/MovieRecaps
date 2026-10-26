"""Run 19, task 4 -- the tool's cache cleans itself (the laptop: no D: drive, work\\cache grew to 91 GB, C: had 12 GB
free). Without a D: drive the cache files not used for 7 days go; with the cache's drive under 30 GB free, the
oldest not used for 2 days go too until it has room; the newest always stay; nothing outside the cache folder is ever
touched. On the PC (a D: drive with room) nothing changes. Fake files with fake dates: no real cache is touched.

Run: python -m pytest tools/app/tests/test_cache_clean_run19.py"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import cache_clean as CC  # noqa: E402

DAY = 86400.0


def _f(root: Path, rel: str, days_old: float, size: int = 1000, now: float | None = None) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    t = (now or time.time()) - days_old * DAY
    os.utime(p, (t, t))
    return p


@pytest.fixture
def cache(tmp_path):
    root = tmp_path / "work" / "cache"
    now = time.time()
    files = {"old_proxy": _f(root, "proxy/a.npz", 9, now=now), "old_index": _f(root, "raw_index/b.npy", 30, now=now),
             "week": _f(root, "sparse_search/c.json", 6, now=now), "two": _f(root, "frame_map/d.npz", 3, now=now),
             "today": _f(root, "captions_asr/e.json", 0.2, now=now)}
    outside = _f(tmp_path, "input/downloads/project/raw.mp4", 40, now=now)       # a RAW: never
    return root, files, outside, now


def test_without_a_d_drive_the_files_not_used_for_7_days_go(cache):
    root, files, outside, now = cache
    rep = CC.clean(root, now=now, d_drive=False, save=False)
    assert rep.files == 2 and rep.freed == 2000 and "no D: drive" in rep.why
    assert not files["old_proxy"].exists() and not files["old_index"].exists()
    assert all(files[k].exists() for k in ("week", "two", "today")) and outside.exists()


def test_a_low_drive_takes_the_oldest_of_2_days_first_until_there_is_room(cache, monkeypatch):
    root, files, outside, now = cache
    free = {"v": CC.LOW_FREE - 2500}                         # 2.5 kB short of 30 GB
    monkeypatch.setattr(CC, "_free", lambda p: free["v"])
    why, todo = CC.plan(root, now=now, d_drive=True)
    assert [p for _t, _s, p in todo] == [files["old_index"], files["old_proxy"], files["week"]]   # the oldest first
    assert "below 30 GB" in why                               # ... and the 3-day one only if still short
    free["v"] = CC.LOW_FREE - 10_000                          # far short: every file older than 2 days, never today's
    _why, todo = CC.plan(root, now=now, d_drive=True)
    assert {p for _t, _s, p in todo} == {files["old_index"], files["old_proxy"], files["week"], files["two"]}
    assert files["today"] not in {p for _t, _s, p in todo}


def test_on_the_pc_with_room_on_d_nothing_changes(cache, monkeypatch):
    root, files, outside, now = cache
    monkeypatch.setattr(CC, "_free", lambda p: 1500 * 1024 ** 3)
    rep = CC.clean(root, now=now, d_drive=True, save=False)
    assert rep.files == 0 and all(f.exists() for f in files.values()) and "D: has room" in rep.why


def test_a_file_used_since_the_plan_or_a_link_out_of_the_cache_stays(cache, monkeypatch):
    root, files, outside, now = cache
    why, todo = CC.plan(root, now=now, d_drive=False)
    os.utime(files["old_proxy"], (time.time(), time.time()))   # read again meanwhile
    real_plan = CC.plan
    monkeypatch.setattr(CC, "plan", lambda *a, **k: (why, todo))
    rep = CC.clean(root, now=now, d_drive=False, save=False)
    monkeypatch.setattr(CC, "plan", real_plan)
    assert files["old_proxy"].exists() and not files["old_index"].exists() and rep.files == 1
    link = root / "linked"                                     # a folder link inside the cache: never followed
    try:
        os.symlink(outside.parent, link, target_is_directory=True)
    except OSError:
        pytest.skip("no symlink right here")
    assert all(p != outside for _t, _s, p in CC._files(root))  # noqa: SLF001


def test_what_it_did_is_kept_for_settings(cache, tmp_path, monkeypatch):
    root, files, outside, now = cache
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    assert CC.last().line() == "has not run yet"
    CC.clean(root, now=now, d_drive=False)
    line = CC.last().line()
    assert "freed" in line and "2 cache file(s)" in line and "no D: drive" in line


def test_the_real_cache_on_this_pc_is_left_alone_when_d_has_room(monkeypatch):
    """On this PC the cache is D:\\mr_cache (work\\cache, a junction): with room on D:, the plan is empty."""
    from mr_app.core import cache_move as CM
    real = CM.real_dir()
    if not str(real).lower().startswith("d:") or not CC.has_d_drive():
        pytest.skip("not the PC's layout")
    if CC._free(real) < CC.LOW_FREE:                           # noqa: SLF001
        pytest.skip("D: is low right now: the clean would act")
    monkeypatch.setattr(CC, "_files", lambda r: pytest.fail("no file is even listed when D: has room"))
    why, todo = CC.plan(real)
    assert todo == [] and why == ""


def test_settings_has_the_switch_and_says_what_it_freed(tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.core import settings as S
    from mr_app.ui import motion as Mo
    from mr_app.ui.settings_dialog import SettingsDialog
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(tmp_path / "settings.json"))
    root = tmp_path / "cache"
    _f(root, "proxy/a.npz", 9)
    CC.clean(root, d_drive=False)
    Mo.set_enabled(False)
    s = S.Settings()
    assert s.cache_clean is True and S.Settings.from_dict({"cache_clean": "no"}).cache_clean is True
    win = QtWidgets.QWidget()
    win.settings = s
    d = SettingsDialog(win, s)
    try:
        assert "freed" in d.cclean_line.text() and "no D: drive" in d.cclean_line.text()
        d.cclean.set("off")
        d._ok()                                                    # noqa: SLF001
        assert S.load().cache_clean is False
    finally:
        Mo.set_enabled(True)
        d.deleteLater()
        win.deleteLater()
        app.processEvents()
