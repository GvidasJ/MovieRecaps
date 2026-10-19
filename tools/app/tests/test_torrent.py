"""Torrents (day 12, 6c): the core with a fake aria2c (no network) -- the file list and the BIGGEST VIDEO, the free
space refusal, the health bars, pause / resume / remove, the restart, sharing stops, the plain errors -- the picker
and the progress card on the real Qt widgets, and ONE real check: a tiny torrent made here is shared by one local
aria2c and downloaded by another, on 127.0.0.1 only (skipped when aria2c.exe is missing).
Run: python -m pytest tools/app/tests/test_torrent.py"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import machine as Mach  # noqa: E402
from mr_app.core import settings as S  # noqa: E402
from mr_app.core import torrent as T  # noqa: E402

GB = 1024 ** 3


# ---------------------------------------------------------------------------------------------------------------------
# a fake aria2c
# ---------------------------------------------------------------------------------------------------------------------

class FakeAria2:
    """The JSON-RPC calls the session makes, on a dict of downloads. ``metadata[info_hash] = torrent bytes``: what a
    magnet's peers would send (None: nobody shares it)."""

    def __init__(self):
        self.d: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.metadata: dict[str, bytes] = {}
        self.n = 0

    def _gid(self) -> str:
        self.n += 1
        return f"{self.n:016x}"

    def call(self, method: str, *p):
        self.calls.append((method, *p))
        m = method.split(".", 1)[1]
        if m == "addUri":
            uri, opts = p[0][0], p[1]
            ih = T.parse_magnet(uri).info_hash
            gid = self._gid()
            st = {"gid": gid, "status": "active", "totalLength": "0", "completedLength": "0", "connections": "0",
                  "numSeeders": "0", "opts": opts}
            if self.metadata.get(ih):
                Path(opts["dir"]).mkdir(parents=True, exist_ok=True)
                if opts.get("bt-save-metadata") == "true":
                    (Path(opts["dir"]) / f"{ih}.torrent").write_bytes(self.metadata[ih])
                st["status"] = "complete"
            self.d[gid] = st
            return gid
        if m == "addTorrent":
            opts = p[2]
            gid = opts.get("gid") or self._gid()
            if gid in self.d and self.d[gid]["status"] in ("active", "paused", "waiting"):
                raise T.RpcError(1, f"GID {gid} is not unique.")
            meta = T.read_torrent(base64.b64decode(p[0]))
            sel = [int(x) - 1 for x in opts["select-file"].split(",")]
            self.d[gid] = {"gid": gid, "status": "paused" if opts.get("pause") == "true" else "active",
                           "totalLength": str(meta.size_of(sel)), "completedLength": "0", "connections": "0",
                           "numSeeders": "0", "downloadSpeed": "0", "seeder": "false", "opts": opts}
            return gid
        gid = p[0] if p else ""
        if m in ("tellStatus", "getPeers", "forcePause", "unpause", "forceRemove", "removeDownloadResult") and \
                gid not in self.d:
            raise T.RpcError(1, f"GID {gid} is not found")
        if m == "tellStatus":
            return {k: v for k, v in self.d[gid].items() if k != "opts"}
        if m == "getPeers":
            return []
        if m == "forcePause":
            self.d[gid]["status"] = "paused"
            return gid
        if m == "unpause":
            self.d[gid]["status"] = "active"
            return gid
        if m == "forceRemove":
            self.d[gid]["status"] = "removed"
            return gid
        if m == "removeDownloadResult":
            self.d.pop(gid, None)
            return "OK"
        raise AssertionError(f"unexpected call {method}")

    def set(self, gid: str, **kw) -> None:
        self.d[gid].update({k: (str(v) if not isinstance(v, str) else v) for k, v in kw.items()})

    def used(self, method: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == "aria2." + method]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _folder_torrent(tmp: Path, files: dict[str, int], trackers=("http://127.0.0.1:1/announce",)) -> bytes:
    src = tmp / "src" / "Example.Movie.2016"
    for name, size in files.items():
        f = src / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\0" * size)
    return T.make_torrent(src, trackers)


@pytest.fixture()
def tor(tmp_path) -> bytes:
    return _folder_torrent(tmp_path, {"Example.Movie.2016.1080p.mkv": 9000, "Sample.mkv": 600,
                                      "Subs/English.srt": 50, "Extras.nfo": 20000})


@pytest.fixture()
def sess(tmp_path):
    eng = FakeAria2()
    clock = Clock()
    s = T.TorrentSession(tmp_path / "state", engine=eng, settings=S.Settings(), clock=clock,
                         sleep=lambda _s: setattr(clock, "t", clock.t + 30), free=lambda p: 500 * GB,
                         scrape=lambda ih, tr: 412)
    s.eng, s.clk = eng, clock
    return s


# ---------------------------------------------------------------------------------------------------------------------
# reading: .torrent, magnet, the BIGGEST VIDEO
# ---------------------------------------------------------------------------------------------------------------------

def test_the_file_list_sizes_and_the_biggest_video(tor, tmp_path):
    m = T.read_torrent(tor)
    assert m.name == "Example.Movie.2016"
    assert [(f.path, f.size) for f in m.files] == [("Example.Movie.2016.1080p.mkv", 9000), ("Extras.nfo", 20000),
                                                   ("Sample.mkv", 600), ("Subs/English.srt", 50)]
    assert m.total == 29650
    # the biggest VIDEO, not the biggest file (the .nfo is bigger)
    assert m.biggest_video() == 0
    assert m.disk_path(tmp_path, 3) == tmp_path / "Example.Movie.2016" / "Subs" / "English.srt"
    assert m.chosen_video([2, 3]) == 2
    assert len(m.info_hash) == 40


def test_a_one_file_torrent_is_saved_without_a_folder(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x" * 1000)
    m = T.read_torrent(T.make_torrent(f))
    assert m.single and m.disk_path(tmp_path / "out", 0) == tmp_path / "out" / "clip.mp4"


def test_magnet_links_and_their_plain_errors():
    ih = "0123456789abcdef0123456789abcdef01234567"
    mg = T.parse_magnet(f"magnet:?xt=urn:btih:{ih.upper()}&dn=Example&tr=udp%3A%2F%2Ft.example%3A80")
    assert (mg.info_hash, mg.name, mg.trackers) == (ih, "Example", ["udp://t.example:80"])
    b32 = base64.b32encode(bytes.fromhex(ih)).decode()
    assert T.parse_magnet(f"magnet:?xt=urn:btih:{b32}").info_hash == ih
    with pytest.raises(T.TorrentError, match="magnet link is broken"):
        T.parse_magnet("magnet:?xt=urn:btih:12345&dn=cut")
    with pytest.raises(T.TorrentError, match="not a magnet link"):
        T.parse_magnet("https://example.com/x")
    assert T.kind_of("magnet:?xt=...") == "magnet" and T.kind_of('"C:\\a\\b.torrent"') == "torrent"
    assert T.kind_of("https://youtu.be/x") == ""


def test_a_broken_torrent_file_is_said_plainly(tmp_path):
    with pytest.raises(T.TorrentError, match="not a working .torrent"):
        T.read_torrent(b"d4:infoi3e")


def test_health_bars_from_the_seeders():
    assert [T.health_bars(n) for n in (0, 1, 2, 4, 5, 14, 15, 49, 50, 412)] == [0, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    assert T.human_eta(None) == "—" and T.human_eta(45) == "45 s" and T.human_eta(600) == "10 min"
    assert T.human_eta(3 * 3600 + 120) == "3 h 02 min"


def test_probe_a_magnet_gets_the_file_list_and_the_seeders(sess, tor):
    m0 = T.read_torrent(tor)
    sess.eng.metadata[m0.info_hash] = tor
    m = sess.probe(f"magnet:?xt=urn:btih:{m0.info_hash}&dn=x&tr=http%3A%2F%2F127.0.0.1%3A1%2Fannounce")
    assert m.info_hash == m0.info_hash and len(m.files) == 4 and m.seeders == 412
    assert m.torrent == tor
    add = sess.eng.used("addUri")[0]
    assert add[2]["bt-metadata-only"] == "true"
    assert sess.eng.used("forceRemove")                        # the probe itself is not left in aria2c


def test_a_magnet_nobody_shares_says_so_after_the_wait(sess):
    sess.probe_minutes = 5
    with pytest.raises(T.TorrentError) as e:
        sess.probe("magnet:?xt=urn:btih:" + "ab" * 20)
    assert str(e.value).startswith("No one is sharing this file right now — tried for 5 minutes")


def test_probe_a_torrent_file(sess, tor, tmp_path):
    p = tmp_path / "x.torrent"
    p.write_bytes(tor)
    m = sess.probe(str(p))
    assert len(m.files) == 4 and m.seeders == 412 and not sess.eng.calls


# ---------------------------------------------------------------------------------------------------------------------
# the download
# ---------------------------------------------------------------------------------------------------------------------

def test_free_space_is_checked_before_starting(sess, tor, tmp_path):
    m = T.read_torrent(tor)
    sess.free = lambda p: 5000                                # 5 KB free, the movie is 9 KB
    with pytest.raises(T.TorrentError) as e:
        sess.start(m, [0], tmp_path / "dl")
    assert "Not enough free space" in str(e.value) and "only 5 KB is free" in str(e.value)
    assert not sess.eng.used("addTorrent") and sess.jobs() == []
    with pytest.raises(T.TorrentError, match="Tick at least one file"):
        sess.check_space(m, [], tmp_path)
    sess.free = lambda p: None
    with pytest.raises(T.TorrentError, match="cannot be reached"):
        sess.check_space(m, [0], tmp_path)


def test_start_downloads_only_the_chosen_files_and_reports_live_numbers(sess, tor, tmp_path):
    m = T.read_torrent(tor)
    jid = sess.start(m, [0, 3], tmp_path / "dl")
    opts = sess.eng.used("addTorrent")[0][3]
    assert opts["select-file"] == "1,4" and opts["seed-time"] == "0" and opts["gid"] == jid
    sess.eng.set(jid, completedLength=4525, downloadSpeed=1024, numSeeders=7, connections=10)
    st = sess.poll(jid)
    assert st["state"] == "downloading" and st["percent"] == 50 and st["seeders"] == 7 and st["peers"] == 3
    assert st["health"] == 3 and st["eta"] == pytest.approx(4525 / 1024) and st["speed_text"] == "1 KB/s"
    assert st["video"] == str(tmp_path / "dl" / "Example.Movie.2016" / "Example.Movie.2016.1080p.mkv")
    with pytest.raises(T.TorrentError, match="already downloading"):
        sess.start(m, [0], tmp_path / "dl")


def test_pause_resume_and_remove(sess, tor, tmp_path):
    m = T.read_torrent(tor)
    dl = tmp_path / "dl"
    jid = sess.start(m, [0], dl)
    sess.pause(jid)
    assert sess.eng.d[jid]["status"] == "paused" and sess.poll(jid)["state"] == "paused"
    assert sess.poll(jid)["speed"] == 0
    sess.resume(jid)
    assert sess.eng.d[jid]["status"] == "active" and sess.poll(jid)["state"] == "downloading"
    part = m.disk_path(dl, 0)
    part.parent.mkdir(parents=True)
    part.write_bytes(b"half")
    (dl / "Example.Movie.2016.aria2").write_bytes(b"ctl")
    sess.remove(jid)
    assert not part.exists() and not (dl / "Example.Movie.2016.aria2").exists()
    assert jid not in sess.eng.d and sess.jobs() == []
    assert not (sess.folder / f"{m.info_hash}.torrent").exists()


def test_finished_stops_sharing_and_gives_the_video(sess, tor, tmp_path):
    m = T.read_torrent(tor)
    jid = sess.start(m, [0], tmp_path / "dl")
    sess.eng.set(jid, completedLength=9000, status="complete", numSeeders=3)
    st = sess.poll(jid)
    assert st["state"] == "finished" and st["percent"] == 100 and st["video"].endswith("1080p.mkv")
    assert not st["sharing"] and st["health"] == 2
    assert jid not in sess.eng.d                                 # removed from aria2c: nothing is shared
    sess.remove(jid)                                             # a finished download is never deleted
    assert sess.jobs() == []


def test_keep_sharing_switch_keeps_it_seeding(sess, tor, tmp_path):
    sess.settings = S.Settings(torrent_keep_sharing=True)
    m = T.read_torrent(tor)
    jid = sess.start(m, [0], tmp_path / "dl")
    opts = sess.eng.used("addTorrent")[0][3]
    assert "seed-time" not in opts and opts["seed-ratio"] == "1.0"
    sess.eng.set(jid, completedLength=9000, seeder="true")
    st = sess.poll(jid)
    assert st["state"] == "finished" and st["sharing"] and sess.eng.d[jid]["status"] == "active"


def test_no_one_to_download_from_for_a_long_time(sess, tor, tmp_path):
    m = T.read_torrent(tor)
    jid = sess.start(m, [0], tmp_path / "dl")
    sess.stall_minutes = 10
    sess.clk.t += 9 * 60
    assert sess.poll(jid)["state"] == "downloading"
    sess.clk.t += 2 * 60
    st = sess.poll(jid)
    assert st["state"] == "stalled" and st["error"].startswith("No one is sharing this file right now — tried for "
                                                                "10 minutes")
    assert sess.eng.d[jid]["status"] == "paused"
    sess.resume(jid)
    assert sess.poll(jid)["state"] == "downloading"


def test_a_full_disk_is_said_plainly(sess, tor, tmp_path):
    m = T.read_torrent(tor)
    jid = sess.start(m, [0], tmp_path / "dl")
    sess.eng.set(jid, status="error", errorCode=9, errorMessage="There is not enough disk space")
    st = sess.poll(jid)
    assert st["state"] == "failed" and st["error"].startswith("The disk is full")
    assert "press Resume" in st["error"]


def test_it_continues_after_an_app_restart(sess, tor, tmp_path):
    m = T.read_torrent(tor)
    a = sess.start(m, [0], tmp_path / "dl")
    other = T.read_torrent(_folder_torrent(tmp_path / "b", {"Other.mp4": 3000}))
    b = sess.start(other, [0], tmp_path / "dl")
    sess.pause(b)
    # the app closes; aria2c stops with it. A new app, a new aria2c:
    eng2 = FakeAria2()
    s2 = T.TorrentSession(sess.folder, engine=eng2, settings=S.Settings(), clock=sess.clk,
                          free=lambda p: 500 * GB)
    assert {j.id for j in s2.jobs()} == {a, b}
    assert set(s2.restore()) == {a, b}
    assert eng2.d[a]["status"] == "active" and eng2.d[b]["status"] == "paused"   # same ids, paused stays paused
    assert eng2.d[a]["opts"]["select-file"] == "1"
    assert s2.poll(a)["state"] == "downloading" and s2.poll(b)["state"] == "paused"


def test_settings_keep_the_torrent_folder_and_the_switch(tmp_path, monkeypatch):
    s = S.Settings()
    assert s.torrent_dir == "" and s.torrent_keep_sharing is False
    d = S.Settings.from_dict({"torrent_dir": "  E:\\T  ", "torrent_keep_sharing": "yes"})
    assert d.torrent_dir == "E:\\T" and d.torrent_keep_sharing is False
    assert T.default_dir(Mach.PC, settings=S.Settings()) == Path(r"D:\Shared\Downloads\Torrents")
    assert T.default_dir(Mach.LAPTOP, settings=S.Settings()) == Path(r"\\192.168.101.11\Shared\Downloads\Torrents")
    assert T.default_dir(Mach.LAPTOP, settings=S.Settings(torrent_dir=str(tmp_path))) == tmp_path


# ---------------------------------------------------------------------------------------------------------------------
# the widgets
# ---------------------------------------------------------------------------------------------------------------------

def _sync(fn, done=None, failed=None):
    try:
        r = fn()
    except Exception as e:  # noqa: BLE001
        if failed:
            failed(f"{type(e).__name__}: {e}")
        return
    if done:
        done(r)


@pytest.fixture()
def qt():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.ui import motion as Mo
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    yield app
    Mo.set_enabled(True)


def test_the_picker_card(qt, sess, tor, tmp_path):
    from mr_app.ui import torrent_panel as TP
    p = tmp_path / "x.torrent"
    p.write_bytes(tor)
    got = []
    w = TP.TorrentPicker(str(p), tmp_path / "dl", sess=sess, run=_sync)
    w.chosen.connect(got.append)
    assert w.head.text() == "Example.Movie.2016"
    assert "4 files · 28.96 KB" not in w.info.text()            # sizes are rounded as Windows shows them
    assert "4 files · 29 KB" in w.info.text() and "412 seeders" in w.info.text() and "free on" in w.info.text()
    assert [r.checked() for r in w.rows] == [True, False, False, False]
    assert w.rows[0].big is not None and all(r.big is None for r in w.rows[1:])
    assert w.save_label.text() == f"Save to {tmp_path / 'dl'}"
    assert w.sel_label.text() == "9 KB selected"
    w.rows[3].box.setChecked(True)
    assert w.sel_label.text() == "9 KB selected" and w.indexes() == [0, 3]
    w.rows[0].box.setChecked(False)
    w.rows[3].box.setChecked(False)
    assert not w.start_btn.isEnabled() and w.sel_label.text() == "nothing selected"
    w.rows[0].box.setChecked(True)
    w.start_btn.click()
    assert got == [[0]]
    job = w.start_job(got[0])
    assert job.id and sess.job(job.id).indexes == [0]
    job.timer.stop()


def test_the_picker_refuses_when_the_disk_is_too_small(qt, sess, tor, tmp_path):
    from mr_app.ui import torrent_panel as TP
    p = tmp_path / "x.torrent"
    p.write_bytes(tor)
    sess.free = lambda d: 1000
    got, cancelled = [], []
    w = TP.TorrentPicker(str(p), tmp_path / "dl", sess=sess, run=_sync)
    w.chosen.connect(got.append)
    w.cancelled.connect(lambda: cancelled.append(1))
    assert "Not enough free space" in w.note.text()
    w.start_btn.click()
    assert got == [] and "Not enough free space" in w.note.text()
    w.cancel_btn.click()
    assert cancelled == [1]


def test_the_picker_says_a_broken_magnet_plainly(qt, sess):
    from mr_app.ui import torrent_panel as TP
    w = TP.TorrentPicker("magnet:?xt=urn:btih:zz", None, sess=sess, run=_sync)
    assert "magnet link is broken" in w.note.text() and w.head.text() == "This torrent could not be read"
    assert not w.start_btn.isEnabled() and not sess.eng.calls


def test_the_progress_card(qt, sess, tor, tmp_path):
    from mr_app.ui import theme
    from mr_app.ui import torrent_panel as TP
    m = T.read_torrent(tor)
    job = TP.TorrentJob.begin(m, [0], tmp_path / "dl", sess=sess, run=_sync)
    job.timer.stop()
    card = TP.TorrentCard(job)
    fin, fail = [], []
    job.finished.connect(fin.append)
    job.failed.connect(fail.append)
    sess.eng.set(job.id, completedLength=2250, downloadSpeed=3 * 1024 ** 2, numSeeders=60, connections=75)
    job.poll()
    assert card.head.text() == "Example.Movie.2016.1080p.mkv"                          # the chosen video's name
    assert card.seeders.value.text() == "60" and card.peers.value.text() == "15"
    assert card.done.value.text() == "25 %" and card.speed.value.text() == "3.0 MB/s"
    assert card.health.value == 5 and card.bar.target == pytest.approx(0.25)
    assert card.pill.word() == "DOWNLOADING" and "stops sharing when finished" in card.foot_right.text()
    card.pause_btn.click()
    assert sess.eng.d[job.id]["status"] == "paused" and card.pause_btn.text() == "Resume"
    assert card.pill.word() == "PAUSED"
    card.pause_btn.click()
    assert sess.eng.d[job.id]["status"] == "active" and card.pause_btn.text() == "Pause"
    sess.eng.set(job.id, status="error", errorCode=9)
    job.poll()
    assert fail and fail[-1].startswith("The disk is full") and card.pill.word() == "FAILED"
    assert card.note.text().startswith("The disk is full")
    sess.resume(job.id)
    sess.eng.set(job.id, completedLength=9000, status="complete")
    job.poll()
    assert fin == [str(m.disk_path(tmp_path / "dl", 0))]
    assert card.pill.word() == "DOWNLOADED" and card._line == theme.GREEN_LINE      # noqa: SLF001
    assert card.stats.isHidden() and card.open_btn.isVisibleTo(card) and "sharing stopped" in card.foot_left.text()
    job.poll()
    assert fin == [str(m.disk_path(tmp_path / "dl", 0))]                              # told once


def test_remove_from_the_card(qt, sess, tor, tmp_path):
    from mr_app.ui import torrent_panel as TP
    job = TP.TorrentJob.begin(T.read_torrent(tor), [0], tmp_path / "dl", sess=sess, run=_sync)
    job.timer.stop()
    card = TP.TorrentCard(job)
    gone = []
    job.removed.connect(lambda: gone.append(1))
    card.remove_btn.click()
    assert gone == [1] and sess.jobs() == []


def test_a_start_with_no_space_fails_on_the_card(qt, sess, tor, tmp_path):
    from mr_app.ui import torrent_panel as TP
    sess.free = lambda d: 10
    job = TP.TorrentJob.begin(T.read_torrent(tor), [0], tmp_path / "dl", sess=sess, run=_sync)
    assert job.id == "" and not job.timer.isActive() and sess.jobs() == []
    assert job._said.startswith("failed:Not enough free space")                       # noqa: SLF001
    card = TP.TorrentCard(job)
    card._failed("Not enough free space on D:")                                        # noqa: SLF001
    assert card.pill.word() == "FAILED" and card.pause_btn.isHidden()


def test_restore_jobs_gives_back_the_unfinished(qt, sess, tor, tmp_path):
    from mr_app.ui import torrent_panel as TP
    jid = sess.start(T.read_torrent(tor), [0], tmp_path / "dl")
    got = []
    TP.restore_jobs(got.extend, sess=sess, run=_sync)
    assert [j.id for j in got] == [jid] and got[0].name == "Example.Movie.2016"
    for j in got:
        j.timer.stop()


def test_settings_dialog_saves_the_torrent_folder_and_the_switch(qt, tmp_path):
    from mr_app.ui.settings_dialog import SettingsDialog
    d = SettingsDialog(None, S.Settings(save_to=str(tmp_path / "hub")))
    assert d.tdir.text() == "" and d.share.value == "off"
    assert d.tdir.placeholderText().endswith(r"Shared\Downloads\Torrents")
    d.tdir.setText(str(tmp_path / "tor"))
    d.share.set("on")
    d._ok()                                                                             # noqa: SLF001
    s = S.load()
    assert s.torrent_dir == str(tmp_path / "tor") and s.torrent_keep_sharing is True
    assert T.default_dir(settings=s) == tmp_path / "tor" and T.keep_sharing(s)
    S.save(S.Settings(save_to=s.save_to, intro=False))                                  # back as conftest left it
    d.deleteLater()


# ---------------------------------------------------------------------------------------------------------------------
# the real engine, on this computer only
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(T.find_aria2c() is None, reason="aria2c.exe is not installed (run setup_app.py)")
def test_real_aria2c_shares_and_downloads_a_tiny_torrent_on_localhost(tmp_path):
    from mr_app.core import torrent_selftest
    out = torrent_selftest.run(tmp_path, size=200 * 1024, timeout=90, log=lambda *_a: None)
    assert out.is_file() and out.stat().st_size == 200 * 1024
