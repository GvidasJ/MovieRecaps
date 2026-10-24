"""Day 17, task 5: torrents that stopped "for no reason" -- Windows' 260-character path limit. The laptop's Everybody
Hates Chris season torrent (its folder 95 characters, the season folder, the file ~110) in \\\\192.168.101.11\\Shared\\
Downloads\\Torrents went over it: a file over the limit is now written to a shorter path (aria2c's index-out), and a
stop for that reason says "the path is too long", never "the folder is not there". Settings turns "/" into "\\" and
says whether the torrent folder is reachable; a restarted download uses the current "Save torrents to".

Run: python -m pytest tools/app/tests/test_torrent_day17.py"""
from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import settings as S  # noqa: E402
from mr_app.core import torrent as T  # noqa: E402

TOP = "Everybody Hates Chris (2005) Season 1-4 S01-S04 (1080p WEB-DL x265 HEVC 10bit EAC3 5.1 Silence)"   # 95
SEASON = "Season 3 (AMZN WEB-DL)"
FILE = ("Everybody Hates Chris (2005) - S03E07 - Everybody Hates Houseguests (1080p AMZN WEB-DL x265 Silence).mkv")
LAPTOP = "\\\\192.168.101.11\\Shared\\Downloads\\Torrents"
PC = "D:\\Shared\\Downloads\\Torrents"


def _season_torrent(root: Path, size: int = 40_000, trackers=("http://127.0.0.1:1/announce",)) -> tuple[bytes, Path]:
    src = root / TOP
    for name in (FILE, FILE.replace("S03E07 - Everybody Hates Houseguests", "S03E08 - Everybody Hates Rejection")):
        f = src / SEASON / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(os.urandom(size))
    return T.make_torrent(src, list(trackers), piece_length=16 * 1024), src


def test_the_laptops_path_was_over_the_limit_and_the_pcs_was_not(tmp_path):
    assert len(TOP) == 95 and len(FILE) > 100
    meta = T.read_torrent(_season_torrent(tmp_path / "s")[0])
    i = next(f.index for f in meta.files if "S03E07" in f.path)
    assert len(str(T.natural_path(meta, LAPTOP, i))) > T.WIN_PATH_MAX                   # the laptop's stop
    assert len(str(T.natural_path(meta, PC, i))) <= T.WIN_PATH_MAX                       # the PC: just under
    short = T.short_out(meta, LAPTOP, i)
    assert short is not None and short.endswith("/" + FILE) and SEASON not in short     # the file keeps its name
    p = T.aria2_path(meta, LAPTOP, i)
    assert len(str(p)) <= T.PATH_SAFE and p == meta.disk_path(LAPTOP, i) and p.name == FILE
    assert T.short_out(meta, "D:\\T", i) is None                                         # short enough: as before
    deep = "D:\\" + "\\".join(["a" * 60] * 3)                                           # a folder leaving little room
    s2 = T.short_out(meta, deep, i)
    assert s2 is not None and len(str(Path(deep) / s2)) <= T.PATH_SAFE and s2.endswith(".mkv")
    with pytest.raises(T.TorrentError, match="too long a path"):
        T.short_out(meta, "D:\\" + "b" * 235, i)


def test_aria2c_is_given_the_short_path_for_that_file_only(tmp_path):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_torrent import FakeAria2
    tor, _src = _season_torrent(tmp_path / "s")
    meta = T.read_torrent(tor)
    eng = FakeAria2()
    sess = T.TorrentSession(tmp_path / "state", engine=eng, settings=S.Settings(), free=lambda p: 500 * 1024 ** 3)
    long_dir = tmp_path / ("x" * (T.PATH_SAFE - len(str(tmp_path)) - 60))
    i = next(f.index for f in meta.files if "S03E07" in f.path)
    jid = sess.start(meta, [i], long_dir)
    opts = eng.used("addTorrent")[-1][3]
    # every long file gets its short path, the unticked one too (aria2c writes the piece it shares with the chosen)
    assert opts["index-out"] == [f"{k + 1}={T.short_out(meta, long_dir, k)}" for k in range(len(meta.files))]
    assert opts["select-file"] == str(i + 1)
    assert sess.job(jid).video == str(long_dir / T.short_out(meta, long_dir, i))
    src = tmp_path / "s2" / "Some.Show.S01"
    (src / "Season 1").mkdir(parents=True)
    (src / "Season 1" / "Some.Show.S01E01.mkv").write_bytes(os.urandom(30_000))
    jid2 = sess.start(T.read_torrent(T.make_torrent(src, ["http://127.0.0.1:1/announce"])), [0], tmp_path / "d")
    assert "index-out" not in eng.used("addTorrent")[-1][3]                              # short: nothing changes
    assert sess.job(jid2).video.endswith(os.path.join("Some.Show.S01", "Season 1", "Some.Show.S01E01.mkv"))


def test_a_stop_for_that_reason_says_the_path_is_too_long():
    long_path = LAPTOP + "\\" + TOP + "\\" + SEASON + "\\" + FILE
    text = T.plain_aria2(16, "Failed to open the file " + long_path + ", cause: The system cannot find the path "
                         "specified.", LAPTOP, long_path)
    assert text.startswith("The path is too long for Windows") and "not there" not in text
    st = {"errorCode": "16", "errorMessage": "cannot find the path specified",
          "files": [{"path": long_path, "selected": "true"}]}
    assert T._plain_status(st, Path(LAPTOP)).startswith("The path is too long")        # noqa: SLF001
    # the real stop: aria2c failed on the NEXT episode's file (not ticked: the piece the two share)
    st2 = {"errorCode": "16", "errorMessage": "Failed to open the file x, cause: The system cannot find the path "
           "specified.", "files": [{"path": LAPTOP + "\\" + TOP + "\\" + FILE, "selected": "true"},
                                  {"path": long_path, "selected": "false"}]}
    assert T._plain_status(st2, Path(LAPTOP)).startswith("The path is too long")       # noqa: SLF001
    short = T.plain_aria2(16, "The system cannot find the path specified.", PC, PC + "\\x.mkv")
    assert "is not there any more" in short                                              # a really missing folder


def test_settings_turn_slashes_round_and_keep_the_default():
    assert S.normalize_folder("//192.168.101.11/Shared/Downloads/Torrents") == LAPTOP
    assert S.normalize_folder(' "D:/Shared/Downloads/Torrents/" ') == PC
    assert S.normalize_folder("") == "" and S.normalize_folder("D:/") == "D:\\"
    assert S._valid("torrent_dir", "//pc/Shared/T", "") == "\\\\pc\\Shared\\T"          # noqa: SLF001


def test_the_settings_dialog_says_whether_the_torrent_folder_is_reachable(tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.ui import background as BG
    from mr_app.ui import settings_dialog as SD
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(BG, "call", lambda fn, done=None, failed=None, work=False: (done or (lambda _r: None))(fn()))
    d = SD.SettingsDialog(None, S.Settings(torrent_dir=str(tmp_path).replace("\\", "/")))
    try:
        d._tidy_tdir()                                                                    # noqa: SLF001
        assert d.tdir.text() == str(tmp_path) and "reachable and can be written" in d.tdir_note.text()
        d.tdir.setText(str(tmp_path / "no" / "such" / "drive").replace(str(tmp_path)[:2], "Q:"))
        d._check_tdir()                                                                   # noqa: SLF001
        assert "not reachable" in d.tdir_note.text()
    finally:
        d.deleteLater()


def test_a_restarted_download_uses_the_current_save_folder(tmp_path, monkeypatch):
    from mr_app.tools import download_tool as D
    monkeypatch.setattr(S, "load", lambda: S.Settings(torrent_dir=str(tmp_path / "old")))
    assert D.DownloadPage._torrent_dir() == tmp_path / "old"
    monkeypatch.setattr(S, "load", lambda: S.Settings(torrent_dir=str(tmp_path / "new")))   # Settings changed
    assert D.DownloadPage._torrent_dir() == tmp_path / "new"                                # Remove + Try again


def _unc(p: Path) -> Path | None:
    s = str(p)
    u = Path(f"\\\\127.0.0.1\\{s[0]}$" + s[2:])
    try:
        return u if u.exists() else None
    except OSError:
        return None


@pytest.mark.skipif(T.find_aria2c() is None, reason="aria2c.exe is not installed (run setup_app.py)")
def test_real_the_season_torrent_downloads_over_a_network_folder(tmp_path, monkeypatch):
    """The laptop's case, for real: the season torrent's episode into a network folder whose path takes it over 260
    characters (\\\\127.0.0.1\\C$\\...: this PC's own disk over the network, as the laptop reaches the PC's)."""
    sys.path.insert(0, str(Path(__file__).parent))
    from test_torrent_day13 import Real
    monkeypatch.setattr(T, "margin", lambda _n: 0)
    short_root = Path(os.environ.get("SystemDrive", "C:") + "\\mr17t")     # the seeder's copy: under the limit
    import shutil
    shutil.rmtree(short_root, ignore_errors=True)
    real = Real(tmp_path)
    try:
        tor, src = _season_torrent(short_root, trackers=[real.tracker.url])
        real.seeder.start()
        real.gid = real.seeder.call("aria2.addTorrent", base64.b64encode(tor).decode(), [],
                                    {"dir": str(src.parent), "check-integrity": "true", "seed-ratio": "0.0",
                                     "bt-tracker-interval": "2"})
        meta = T.read_torrent(tor)
        dl = tmp_path / "Downloads" / "Torrents"
        dl.mkdir(parents=True)
        unc = _unc(dl)
        if unc is None:
            pytest.skip("the admin share \\\\127.0.0.1\\C$ can't be reached here")
        i = next(f.index for f in meta.files if "S03E07" in f.path)
        assert len(str(T.natural_path(meta, unc, i))) > T.WIN_PATH_MAX                 # over the limit, as there
        jid = real.sess.start(meta, [i], unc)
        st = real.wait(jid, 90)
        if st["state"] != "finished":
            full = real.getter.call("aria2.tellStatus", jid)
            pytest.fail(f"{full.get('errorCode')} {full.get('errorMessage')} | dir {full.get('dir')} | "
                        + " | ".join(f"{f.get('selected')} {f.get('path')}" for f in full.get("files") or []))
        got = Path(st["video"])
        assert got.name == FILE and len(str(got)) <= T.PATH_SAFE and got.is_file()
        assert got.read_bytes() == (src / SEASON / FILE).read_bytes()
    finally:
        real.close()
        time.sleep(0.5)
        shutil.rmtree(short_root, ignore_errors=True)
