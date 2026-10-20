"""Day 13, item 1.6: a torrent's files already in the download folder without aria2c's progress file (.aria2) --
aria2c alone stops with "File exists, but a control file (*.aria2) does not exist" (the raw message you saw). Now
the files are checked against the torrent (check-integrity): the right pieces are kept, only the missing ones are
downloaded, a complete file is finished at once (nothing downloaded, nothing written); a file that cannot be this
torrent's (bigger, or none of its pieces right) is renamed "<name> (old)<ext>" and fetched fresh -- said on the card in
plain words; every aria2c error (codes 1-32 and the messages seen) in plain words with what to do.

The real checks run two aria2c on 127.0.0.1 (one shares, the app's session downloads; nothing from the internet),
also into a network folder (\\\\127.0.0.1\\C$\\...: the laptop saves over \\\\192.168.101.11\\Shared). Skipped when
aria2c.exe is missing. Run: python -m pytest tools/app/tests/test_torrent_day13.py"""
from __future__ import annotations

import base64
import hashlib
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import settings as S  # noqa: E402
from mr_app.core import torrent as T  # noqa: E402
from test_torrent import GB, Clock, FakeAria2  # noqa: E402

PIECE = 16 * 1024


def _data(n_pieces: int = 64, seed: int = 7) -> bytes:
    import random
    return random.Random(seed).randbytes(PIECE * n_pieces - 1000)


def _one_file_torrent(tmp_path: Path, data: bytes, name: str = "Movie.2020.1080p.mkv",
                      trackers=("http://127.0.0.1:1/announce",)) -> tuple[bytes, Path]:
    src = tmp_path / "src" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    return T.make_torrent(src, list(trackers), piece_length=PIECE), src


@pytest.fixture()
def sess(tmp_path):
    eng = FakeAria2()
    clock = Clock()
    s = T.TorrentSession(tmp_path / "state", engine=eng, settings=S.Settings(), clock=clock,
                         sleep=lambda _s: setattr(clock, "t", clock.t + 30), free=lambda p: 500 * GB,
                         scrape=lambda ih, tr: 3)
    s.eng, s.clk = eng, clock
    return s


def _half(data: bytes) -> bytes:
    b = bytearray(data)
    for k in range(0, len(b) // PIECE + 1, 2):                      # every other piece zero: a part-download
        b[k * PIECE:(k + 1) * PIECE] = bytes(len(b[k * PIECE:(k + 1) * PIECE]))
    return bytes(b)


# ---------------------------------------------------------------------------------------------------------------------
# the pieces
# ---------------------------------------------------------------------------------------------------------------------

def test_aria2c_names_and_the_old_name(tmp_path):
    assert T.aria2_escape('Movie: Part 2? "x" <1>|*') == "Movie%3A Part 2%3F %22x%22 %3C1%3E%7C%2A"
    assert T.aria2_escape("Ok name (2020) [1080p] - ünï") == "Ok name (2020) [1080p] - ünï"
    src = tmp_path / "AB.mkv"
    src.write_bytes(b"x" * 5000)
    m = T.read_torrent(T.make_torrent(src, name="A: B.mkv"))                  # a name Windows cannot have
    assert T.aria2_path(m, tmp_path / "dl", 0) == tmp_path / "dl" / "A%3A B.mkv"
    assert T.control_file(m, tmp_path / "dl") == tmp_path / "dl" / "A%3A B.mkv.aria2"
    f = tmp_path / "Movie.mkv"
    f.write_bytes(b"1")
    assert T.old_name(f) == tmp_path / "Movie (old).mkv"
    (tmp_path / "Movie (old).mkv").write_bytes(b"2")
    assert T.old_name(f) == tmp_path / "Movie (old 2).mkv"


def test_a_file_is_told_apart_by_its_pieces(tmp_path):
    data = _data()
    tor, _src = _one_file_torrent(tmp_path, data)
    m = T.read_torrent(tor)
    f = tmp_path / "f.mkv"
    for content, want in ((data, True), (_half(data), True), (data[:len(data) // 2], True),
                          (os.urandom(len(data)), False), (data[:PIECE - 10], None)):
        f.write_bytes(content)
        assert T.file_matches(m, 0, f) is want


# ---------------------------------------------------------------------------------------------------------------------
# with the fake aria2c: what the session asks for
# ---------------------------------------------------------------------------------------------------------------------

def test_a_finished_file_without_its_aria2_is_checked_not_downloaded(sess, tmp_path):
    data = _data()
    m = T.read_torrent(_one_file_torrent(tmp_path, data)[0])
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "Movie.2020.1080p.mkv").write_bytes(data)
    jid = sess.start(m, [0], dl)
    opts = sess.eng.used("addTorrent")[0][3]
    assert opts["check-integrity"] == "true" and opts["bt-hash-check-seed"] == "false"
    assert opts["allow-overwrite"] == "false" and opts["auto-file-renaming"] == "false"
    st = sess.poll(jid)
    assert "checked against the torrent first" in st["note"]
    sess.eng.set(jid, status="complete", completedLength=len(data))      # the check found every piece
    st = sess.poll(jid)
    assert st["state"] == "finished" and "nothing was downloaded again" in st["note"]
    assert (dl / "Movie.2020.1080p.mkv").read_bytes() == data


def test_the_free_space_check_does_not_count_what_is_already_there(sess, tmp_path):
    """The next Short of the same film on a nearly full disk: the movie is in the folder already -- no refusal."""
    data = _data()
    m = T.read_torrent(_one_file_torrent(tmp_path, data)[0])
    dl = tmp_path / "dl"
    dl.mkdir()
    sess.free = lambda p: 10 * 1024                                            # 10 KB free, the movie is 1 MB
    with pytest.raises(T.TorrentError, match="Not enough free space"):
        sess.check_space(m, [0], dl)
    (dl / "Movie.2020.1080p.mkv").write_bytes(data)
    assert sess.check_space(m, [0], dl) == 10 * 1024                         # already there: fits
    (dl / "Movie.2020.1080p.mkv").write_bytes(data[:len(data) // 2])
    with pytest.raises(T.TorrentError, match=r"the chosen files need 51\d KB"):  # only the missing half counted
        sess.check_space(m, [0], dl)


def test_a_part_downloaded_file_keeps_its_right_pieces(sess, tmp_path):
    data = _data()
    m = T.read_torrent(_one_file_torrent(tmp_path, data)[0])
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "Movie.2020.1080p.mkv").write_bytes(_half(data))
    jid = sess.start(m, [0], dl)
    assert sess.eng.used("addTorrent")[0][3]["check-integrity"] == "true"
    sess.eng.set(jid, completedLength=len(data) // 2, connections=3, downloadSpeed=1000)
    sess.poll(jid)
    sess.eng.set(jid, status="complete", completedLength=len(data))
    st = sess.poll(jid)
    assert st["state"] == "finished" and "only the missing parts were downloaded" in st["note"]


def test_a_file_that_is_not_this_torrents_is_renamed_old_and_fetched_fresh(sess, tmp_path):
    data = _data()
    m = T.read_torrent(_one_file_torrent(tmp_path, data)[0])
    dl = tmp_path / "dl"
    dl.mkdir()
    big = os.urandom(len(data) + 4000)
    (dl / "Movie.2020.1080p.mkv").write_bytes(big)
    jid = sess.start(m, [0], dl)
    assert (dl / "Movie.2020.1080p (old).mkv").read_bytes() == big           # kept, under its new name
    assert not (dl / "Movie.2020.1080p.mkv").exists()
    opts = sess.eng.used("addTorrent")[0][3]
    assert "check-integrity" not in opts                                      # nothing left to check: fresh
    note = sess.poll(jid)["note"]
    assert 'renamed to "Movie.2020.1080p (old).mkv"' in note and "it is bigger" in note
    same = os.urandom(len(data))                                              # the same size, other bytes
    dl2 = tmp_path / "dl2"
    dl2.mkdir()
    (dl2 / "Movie.2020.1080p.mkv").write_bytes(same)
    sess.remove(jid)
    jid2 = sess.start(m, [0], dl2)
    assert (dl2 / "Movie.2020.1080p (old).mkv").read_bytes() == same
    assert "none of the parts checked match" in sess.poll(jid2)["note"]


def test_a_long_check_of_a_file_already_there_is_not_no_one_sharing(sess, tmp_path):
    """A big film checked over the laptop's network takes minutes with no peer and no progress: not "No one is
    sharing" -- aria2c 1.37 says it is checking (verifiedLength), an older one says nothing: time by the size."""
    data = _data()
    m = T.read_torrent(_one_file_torrent(tmp_path, data)[0])
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "Movie.2020.1080p.mkv").write_bytes(data)
    jid = sess.start(m, [0], dl)
    sess.stall_minutes = 10
    sess.eng.set(jid, verifiedLength=1000)
    sess.clk.t += 11 * 60
    assert sess.poll(jid)["state"] == "downloading"
    del sess.eng.d[jid]["verifiedLength"]
    sess.job(jid).size = 40 * GB
    sess.clk.t += 11 * 60
    assert sess.poll(jid)["state"] == "downloading"                       # 40 GB at 20 MB/s: still checking
    sess.clk.t += 40 * GB / T.CHECK_BPS
    st = sess.poll(jid)
    assert st["state"] == "stalled" and st["error"].startswith("No one is sharing")


def test_remove_never_deletes_a_file_that_was_already_there(sess, tmp_path):
    """Remove during the check of a video found in the folder (maybe your earlier finished download): the job goes,
    the file stays. A download this job started itself is still cleaned up as before."""
    data = _data()
    m = T.read_torrent(_one_file_torrent(tmp_path, data)[0])
    dl = tmp_path / "dl"
    dl.mkdir()
    f = dl / "Movie.2020.1080p.mkv"
    f.write_bytes(data)
    jid = sess.start(m, [0], dl)
    assert sess.job(jid).checked
    sess.remove(jid)
    assert f.read_bytes() == data and sess.jobs() == []
    dl2 = tmp_path / "dl2"
    jid2 = sess.start(m, [0], dl2)                                            # nothing there: its own part file
    part = dl2 / "Movie.2020.1080p.mkv"
    part.write_bytes(b"half")
    sess.remove(jid2)
    assert not part.exists()


def test_a_file_with_its_aria2_goes_on_as_before(sess, tmp_path):
    data = _data()
    m = T.read_torrent(_one_file_torrent(tmp_path, data)[0])
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "Movie.2020.1080p.mkv").write_bytes(os.urandom(100))
    (dl / "Movie.2020.1080p.mkv.aria2").write_bytes(b"ctl")
    jid = sess.start(m, [0], dl)
    opts = sess.eng.used("addTorrent")[0][3]
    assert "check-integrity" not in opts and (dl / "Movie.2020.1080p.mkv").stat().st_size == 100
    assert sess.poll(jid)["note"] == ""


def test_error_13_at_runtime_is_checked_once_then_said_plainly(sess, tmp_path):
    data = _data()
    m = T.read_torrent(_one_file_torrent(tmp_path, data)[0])
    jid = sess.start(m, [0], tmp_path / "dl")
    raw = ("File C:/x/Movie.2020.1080p.mkv exists, but a control file(*.aria2) does not exist. Download was canceled "
           "in order to prevent your file from being truncated to 0. If you are sure to download the file all over "
           "again, then delete it or add --allow-overwrite=true option and restart aria2.")
    sess.eng.set(jid, status="error", errorCode=13, errorMessage=raw)
    st = sess.poll(jid)
    assert st["state"] == "downloading" and st["error"] == ""
    again = sess.eng.used("addTorrent")
    assert len(again) == 2 and again[1][3]["check-integrity"] == "true" and again[1][3]["gid"] == jid
    sess.eng.set(jid, status="error", errorCode=13, errorMessage=raw)        # again: no endless loop
    st = sess.poll(jid)
    assert st["state"] == "failed" and len(sess.eng.used("addTorrent")) == 2
    for bad in ("control file(*.aria2)", "--allow-overwrite", "truncated to 0"):
        assert bad not in st["error"]
    assert st["error"].startswith("The video is already in the download folder") and "Press Resume" in st["error"]


def test_every_aria2_error_code_in_plain_words(tmp_path):
    seen = set()
    for code in range(1, 33):
        s = T.plain_aria2(code, "", tmp_path)
        assert s[0].isupper() and s.endswith(".") and "{" not in s and "}" not in s, (code, s)
        assert ". " in s, (code, s)                                          # what happened. What to do.
        seen.add(s)
    assert len(seen) >= 25
    assert T.plain_aria2(9, "", tmp_path).startswith("The disk is full (only ") and "Clean up" in \
        T.plain_aria2(9, "", tmp_path)
    busy = ("Failed to open the file C:/x/a.mkv, cause: The process cannot access the file because it is being used "
            "by another process.")
    assert T.plain_aria2(16, busy, tmp_path).startswith("Windows did not let the download open its file")
    assert T.plain_aria2(17, "cause: The specified network name is no longer available.",
                         r"\\192.168.101.11\Shared\Downloads\Torrents").startswith(
        r"The shared folder \\192.168.101.11\Shared\Downloads\Torrents could not be reached")
    assert T.plain_aria2(1, "There is not enough space on the disk.", tmp_path).startswith("The disk is full")
    assert T.plain_aria2(None, "", tmp_path).startswith("The download stopped")
    assert T.plain_aria2(13, "File x exists, but a control file(*.aria2) does not exist.", tmp_path).startswith(
        "The video is already in the download folder")


def test_a_failed_job_saved_with_aria2cs_own_sentence_is_said_plainly_after_a_restart(tmp_path):
    """jobs.json of day 12 kept "The download stopped: File ... exists, but a control file(*.aria2) does not exist.
    ..." -- the raw message you saw; loaded again it is plain words (and Resume checks the file)."""
    import json
    state = tmp_path / "state"
    state.mkdir()
    raw = ("The download stopped: File D:/Shared/Downloads/Torrents/Movie.mkv exists, but a control file(*.aria2) "
           "does not exist. Download was canceled in order to prevent your file from being truncated to 0. If you "
           "are sure to download the file all over again, then delete it or add --allow-overwrite=true option and "
           "restart aria2.. Press Resume to try again.")
    (state / "jobs.json").write_text(json.dumps({"jobs": [{"id": "ab" * 8, "name": "Movie", "info_hash": "cd" * 20,
                                                           "indexes": [0], "save_dir": str(tmp_path), "video": "",
                                                           "size": 10, "state": "failed", "error": raw}]}),
                                     encoding="utf-8")
    s = T.TorrentSession(state, engine=FakeAria2(), settings=S.Settings(), free=lambda p: 500 * GB)
    err = s.job("ab" * 8).error
    assert err.startswith("The video is already in the download folder") and "control file(" not in err


def test_the_card_shows_the_note(sess, tmp_path):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.ui import motion as Mo
    from mr_app.ui import torrent_panel as TP
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    try:
        data = _data()
        m = T.read_torrent(_one_file_torrent(tmp_path, data)[0])
        dl = tmp_path / "dl"
        dl.mkdir()
        (dl / "Movie.2020.1080p.mkv").write_bytes(os.urandom(len(data) + 10))

        def run(fn, done=None, failed=None):
            try:
                r = fn()
            except Exception as e:  # noqa: BLE001
                if failed:
                    failed(f"{type(e).__name__}: {e}")
                return
            if done:
                done(r)
        job = TP.TorrentJob.begin(m, [0], dl, sess=sess, run=run)
        job.timer.stop()
        card = TP.TorrentCard(job)
        job.poll()
        assert card.note.isVisibleTo(card) and 'renamed to "Movie.2020.1080p (old).mkv"' in card.note.text()
        sess.eng.set(job.id, status="complete", completedLength=len(data))
        job.poll()
        assert card.pill.word() == "DOWNLOADED" and "renamed to" in card.note.text()
        card.deleteLater()
        app.processEvents()
    finally:
        Mo.set_enabled(True)


# ---------------------------------------------------------------------------------------------------------------------
# the real engine, on this computer only (127.0.0.1)
# ---------------------------------------------------------------------------------------------------------------------

needs_aria2 = pytest.mark.skipif(T.find_aria2c() is None, reason="aria2c.exe is not installed (run setup_app.py)")
from mr_app.core.torrent_selftest import LOCAL_ONLY  # noqa: E402 - 127.0.0.1 only: no Windows firewall question

EXTRA = LOCAL_ONLY + ["--bt-tracker-interval=2"]


class Real:
    """One local tracker, one sharing aria2c and the app's session with its own aria2c."""

    def __init__(self, tmp: Path, getter_args: list[str] | None = None):
        from mr_app.core.torrent_selftest import LocalTracker
        self.tmp = tmp
        self.tracker = LocalTracker()
        self.seeder = T.Aria2(tmp / "seed_state", listen_port=T._free_port(), extra_args=EXTRA)
        self.getter = T.Aria2(tmp / "get_state", listen_port=T._free_port(), extra_args=EXTRA + (getter_args or []))
        self.sess = T.TorrentSession(tmp / "get_state", engine=self.getter, settings=S.Settings(), probe_minutes=1)
        self.gid = ""

    def share(self, data: bytes, limit: str = "") -> T.Meta:
        tor, src = _one_file_torrent(self.tmp, data, trackers=[self.tracker.url])
        self.seeder.start()
        o = {"dir": str(src.parent), "check-integrity": "true", "seed-ratio": "0.0", "bt-tracker-interval": "2"}
        if limit:
            o["max-upload-limit"] = limit
        self.gid = self.seeder.call("aria2.addTorrent", base64.b64encode(tor).decode(), [], o)
        return T.read_torrent(tor)

    def uploaded(self) -> int:
        return int(self.seeder.call("aria2.tellStatus", self.gid, ["uploadLength"])["uploadLength"]) if self.gid else 0

    def wait(self, jid: str, timeout: float = 60.0, every=None) -> dict:
        t0 = time.monotonic()
        st = self.sess.poll(jid)
        while st["state"] not in ("finished", "failed", "stalled"):
            if every is not None:
                every(st)
            assert time.monotonic() - t0 < timeout, f"not finished in {timeout:.0f} s: {st}"
            time.sleep(0.2)
            st = self.sess.poll(jid)
        return st

    def close(self) -> None:
        for a in (self.getter, self.seeder):                 # at once: no goodbye to the trackers (seconds)
            if a.running():
                try:
                    a.proc.kill()
                    a.proc.wait(timeout=5)
                except Exception:  # noqa: BLE001 - stop() tries again
                    pass
            a.stop()
        self.tracker.close()


def test_the_tests_aria2c_command_lines_are_127_0_0_1_only():
    from conftest import _aria2c_on_this_computer_only as local
    cmd = local([r"C:\x\aria2c.exe", "--enable-rpc=true", "--enable-dht=true", "--disable-ipv6=false"])
    assert cmd[-4:] == LOCAL_ONLY                                         # added last: they win over the app's own
    assert local(["aria2c", "--version"]) == ["aria2c", "--version"]      # not a daemon: as it is
    assert local(["ffmpeg", "-i", "x"]) == ["ffmpeg", "-i", "x"] and local("aria2c --enable-rpc=true") == \
        "aria2c --enable-rpc=true"


@needs_aria2
def test_every_aria2c_a_test_starts_listens_on_127_0_0_1_only(tmp_path):
    """The lead saw Windows' firewall question about aria2c.exe during the tests: an aria2c started with the app's own
    command line (no test options at all) still binds to 127.0.0.1 only, without DHT / IPv6 (conftest)."""
    a = T.Aria2(tmp_path / "state", listen_port=T._free_port())
    try:
        a.start()
        g = a.call("aria2.getGlobalOption")
        assert (g.get("interface"), g.get("disable-ipv6"), g.get("enable-dht"), g.get("bt-enable-lpd")) == \
            ("127.0.0.1", "true", "false", "false")
    finally:
        if a.running():
            a.proc.kill()
        a.stop()


@pytest.fixture()
def real(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "margin", lambda _n: 0)        # tiny files: no 512 MB to spare (C: may be nearly full)
    r = Real(tmp_path)
    yield r
    r.close()


@needs_aria2
def test_real_a_finished_file_is_finished_at_once_without_downloading(real, tmp_path):
    data = _data()
    tor, _src = _one_file_torrent(tmp_path, data, trackers=[real.tracker.url])
    m = T.read_torrent(tor)                                  # nobody shares it: it must not need anyone
    dl = tmp_path / "dl"
    dl.mkdir()
    f = dl / "Movie.2020.1080p.mkv"
    f.write_bytes(data)
    os.utime(f, (1_700_000_000, 1_700_000_000))
    jid = real.sess.start(m, [0], dl)
    st = real.wait(jid, 30)
    assert st["state"] == "finished", st
    assert "nothing was downloaded again" in st["note"]
    assert f.read_bytes() == data and f.stat().st_mtime_ns == 1_700_000_000 * 10 ** 9      # not written at all
    assert not (dl / "Movie.2020.1080p.mkv.aria2").exists() and Path(st["video"]).name == f.name


@needs_aria2
def test_real_the_next_short_of_the_same_film_needs_no_download(real, tmp_path):
    """The real case: a movie torrent (a folder: the film, a sample, a note) downloaded once for one Short; the next
    Short of the same film adds the same torrent again -- aria2c deleted its .aria2 when it finished. With nobody
    sharing any more, it is finished at once from the file already there (checked, not written)."""
    src = tmp_path / "src" / "Borat.2006.1080p"
    src.mkdir(parents=True)
    film = _data(80, seed=11)
    (src / "Borat.2006.1080p.mkv").write_bytes(film)
    (src / "Sample.mkv").write_bytes(_data(9, seed=12))
    (src / "info.nfo").write_bytes(b"x" * 3000)
    tor = T.make_torrent(src, [real.tracker.url], piece_length=PIECE)
    m = T.read_torrent(tor)
    real.seeder.start()
    real.gid = real.seeder.call("aria2.addTorrent", base64.b64encode(tor).decode(), [],
                                {"dir": str(src.parent), "check-integrity": "true", "seed-ratio": "0.0",
                                 "bt-tracker-interval": "2"})
    i = m.biggest_video()
    dl = tmp_path / "dl"
    jid = real.sess.start(m, [i], dl)
    st = real.wait(jid)
    assert st["state"] == "finished" and Path(st["video"]).read_bytes() == film
    real.sess.remove(jid)                                    # a finished download is never deleted
    f = Path(st["video"])
    before = f.stat().st_mtime_ns
    real.seeder.proc.kill()                                  # nobody shares it any more
    real.seeder.proc.wait(timeout=5)
    ctl = T.control_file(m, dl)
    assert ctl.is_file()                 # one file of a folder's: aria2c keeps its .aria2 when it has finished
    jid2 = real.sess.start(m, [i], dl)                       # 1. the .aria2 is there: it goes on from it
    st2 = real.wait(jid2, 40)
    assert st2["state"] == "finished" and "nothing was downloaded again" in st2["note"], st2
    assert f.read_bytes() == film and f.stat().st_mtime_ns == before
    real.sess.remove(jid2)
    ctl.unlink()                                             # 2. the .aria2 lost: checked against the torrent
    jid3 = real.sess.start(m, [i], dl)
    assert real.sess.job(jid3).checked
    st3 = real.wait(jid3, 40)
    assert st3["state"] == "finished" and "every part is right, so nothing was downloaded again" in st3["note"], st3
    assert f.read_bytes() == film and f.stat().st_mtime_ns == before


@needs_aria2
def test_real_a_half_file_fetches_only_the_missing_pieces(real, tmp_path):
    data = _data()
    m = real.share(data)
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "Movie.2020.1080p.mkv").write_bytes(_half(data))
    jid = real.sess.start(m, [0], dl)
    st = real.wait(jid)
    assert st["state"] == "finished", st
    assert hashlib.sha1((dl / "Movie.2020.1080p.mkv").read_bytes()).digest() == hashlib.sha1(data).digest()
    up = real.uploaded()
    assert 0 < up <= 0.6 * len(data), f"the sharer sent {up} of {len(data)} bytes: more than the missing half"
    assert "only the missing parts were downloaded" in st["note"]


@needs_aria2
def test_real_a_wrong_size_file_is_renamed_old_and_fetched_fresh(real, tmp_path):
    data = _data()
    m = real.share(data)
    dl = tmp_path / "dl"
    dl.mkdir()
    big = os.urandom(len(data) + 5000)
    (dl / "Movie.2020.1080p.mkv").write_bytes(big)
    jid = real.sess.start(m, [0], dl)
    st = real.wait(jid)
    assert st["state"] == "finished", st
    assert (dl / "Movie.2020.1080p (old).mkv").read_bytes() == big                 # never truncated
    assert (dl / "Movie.2020.1080p.mkv").read_bytes() == data
    assert 'renamed to "Movie.2020.1080p (old).mkv"' in st["note"]
    assert real.uploaded() >= len(data)


@needs_aria2
def test_real_error_13_from_aria2c_is_checked_by_itself(real, tmp_path, monkeypatch):
    """A file the app's own check did not see (here: its path guessed wrong on purpose) -- aria2c stops with
    "File exists, but a control file (*.aria2) does not exist" (error 13); the session asks aria2c for its real path,
    checks the file and adds the download again with check-integrity: finished, the raw message never shown."""
    data = _data()
    tor, _src = _one_file_torrent(tmp_path, data, trackers=[real.tracker.url])
    m = T.read_torrent(tor)
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "Movie.2020.1080p.mkv").write_bytes(data)
    monkeypatch.setattr(T, "aria2_path", lambda meta, save, i: Path(save) / "not-the-name.mkv")
    jid = real.sess.start(m, [0], dl)
    st = real.wait(jid, 30)
    assert st["state"] == "finished", st
    assert real.sess.job(jid).rechecked and "control file" not in st["error"]
    assert (dl / "Movie.2020.1080p.mkv").read_bytes() == data


def _unc(p: Path) -> Path | None:
    """The same folder over the network: \\\\127.0.0.1\\C$\\... (the admin share; the laptop saves over
    \\\\192.168.101.11\\Shared the same way). None when it cannot be reached."""
    s = str(p)
    if len(s) < 3 or s[1] != ":":
        return None
    u = Path(f"\\\\127.0.0.1\\{s[0]}$" + s[2:])
    try:
        return u if u.exists() else None
    except OSError:
        return None


@needs_aria2
def test_real_over_a_network_folder_the_aria2_file_stays_next_to_the_download(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "margin", lambda _n: 0)
    dl = tmp_path / "net"
    dl.mkdir()
    unc = _unc(dl)
    if unc is None:
        pytest.skip("the admin share \\\\127.0.0.1\\C$ cannot be reached on this computer")
    real = Real(tmp_path, getter_args=["--auto-save-interval=1"])
    try:
        data = _data(64)
        m = real.share(data, limit="64K")                    # slow (~10 s): the .aria2 file can be seen meanwhile
        (unc / "Movie.2020.1080p.mkv").write_bytes(data[:len(data) // 3])     # a third there, no .aria2
        jid = real.sess.start(m, [0], unc)
        ctl = unc / "Movie.2020.1080p.mkv.aria2"
        seen = []
        st = real.wait(jid, 120, every=lambda s: seen.append(ctl.exists()))
        assert st["state"] == "finished", st
        assert any(seen), "the .aria2 progress file was never next to the download while it ran"
        assert not ctl.exists()                                                    # gone once finished
        assert (dl / "Movie.2020.1080p.mkv").read_bytes() == data
        assert real.uploaded() <= 0.75 * len(data)                                 # the third that was there kept
    finally:
        real.close()
