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


# ---------------------------------------------------------------------------------------------------------------------
# 3. / 4. torrents: only this job's files are deleted; the real paths from aria2c; never outside the save folder
# ---------------------------------------------------------------------------------------------------------------------

from mr_app.core import torrent as T  # noqa: E402

from test_torrent import FakeAria2, sess, tor  # noqa: E402,F401 - the fake aria2c and its fixtures


class FilesAria2(FakeAria2):
    """The fake aria2c that also answers aria2.getFiles (``paths[gid] = {0-based index: path}``)."""

    def __init__(self):
        super().__init__()
        self.paths: dict[str, dict[int, str]] = {}

    def call(self, method, *p):
        if method == "aria2.getFiles":
            self.calls.append((method, *p))
            gid = p[0]
            if gid not in self.d:
                raise T.RpcError(1, f"GID {gid} is not found")
            sel = [int(x) - 1 for x in self.d[gid]["opts"]["select-file"].split(",")]
            return [{"index": str(i + 1), "path": path, "selected": "true" if i in sel else "false"}
                    for i, path in sorted(self.paths.get(gid, {}).items())]
        return super().call(method, *p)


def _with_files(sess):  # noqa: F811
    eng = FilesAria2()
    sess.engine = sess.eng = eng
    return eng


def test_aria2_never_removes_unselected_files():
    src = (Path(T.__file__)).read_text(encoding="utf-8")
    assert "bt-remove-unselected-file" not in src


def test_remove_deletes_only_this_jobs_files_never_another_jobs_or_unselected_ones(sess, tor, tmp_path):  # noqa: F811
    m = T.read_torrent(tor)
    dl = tmp_path / "dl"
    first = sess.start(m, [0], dl)                               # the season pack's first episode ...
    sess.eng.set(first, completedLength=9000, status="complete")
    sess.poll(first)
    assert sess.job(first).state == "finished"
    second = sess.start(m, [1], dl)                              # ... then a second job on the same torrent
    files = {i: m.disk_path(dl, i) for i in range(len(m.files))}
    for p in files.values():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    ctl = dl / "Example.Movie.2016.aria2"
    ctl.write_bytes(b"ctl")
    sess.remove(second)
    assert not files[1].exists()                                 # its own part file: gone
    assert files[0].exists()                                     # the first job's finished video: kept
    assert files[2].exists() and files[3].exists()               # files nobody chose: left alone
    assert ctl.exists()                                          # the first job still knows this torrent


def test_remove_skips_a_file_another_running_job_also_chose(sess, tor, tmp_path):  # noqa: F811
    m = T.read_torrent(tor)
    dl = tmp_path / "dl"
    a = sess.start(m, [0, 2], dl)
    sess.job(a).state = "paused"
    with sess._lock:                                             # noqa: SLF001 - a second known job, same folder
        b = T.Job(id="b" * 16, name=m.name, info_hash=m.info_hash, indexes=[2], save_dir=str(dl), video="", size=1,
                  state="paused")
        sess._jobs[b.id] = b                                     # noqa: SLF001
    for i in (0, 2):
        m.disk_path(dl, i).parent.mkdir(parents=True, exist_ok=True)
        m.disk_path(dl, i).write_bytes(b"x")
    sess.remove(a)
    assert not m.disk_path(dl, 0).exists() and m.disk_path(dl, 2).exists()


def test_the_finished_video_is_where_aria2_really_wrote_it(sess, tor, tmp_path):  # noqa: F811
    eng = _with_files(sess)
    m = T.read_torrent(tor)
    dl = tmp_path / "dl"
    jid = sess.start(m, [0, 2], dl)
    real = dl / "Example.Movie.2016" / "Spider-Man%3A Example.1080p.mkv"           # aria2c escaped a ':'
    eng.paths[jid] = {0: str(real), 2: str(dl / "Example.Movie.2016" / "Subs" / "English.srt")}
    eng.set(jid, completedLength=9050, status="complete")
    st = sess.poll(jid)
    assert st["state"] == "finished" and st["video"] == str(real)


def test_a_path_from_aria2_outside_the_save_folder_is_ignored(sess, tor, tmp_path):  # noqa: F811
    eng = _with_files(sess)
    m = T.read_torrent(tor)
    dl = tmp_path / "dl"
    jid = sess.start(m, [0], dl)
    guess = sess.job(jid).video
    eng.paths[jid] = {0: str(tmp_path / "elsewhere" / "x.mkv")}
    eng.set(jid, completedLength=9000, status="complete")
    assert sess.poll(jid)["video"] == guess
    jid2 = sess.start(m, [1], dl)
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"keep me")
    eng.paths[jid2] = {1: r"..\victim.txt"}
    sess.remove(jid2)
    assert victim.exists()


def test_inside_refuses_dots_drives_and_full_paths(tmp_path):
    base = tmp_path / "dl"
    assert T.inside(base, "a/b.mkv") and T.inside(base, base / "a.mkv")
    assert not T.inside(base, "../a.mkv") and not T.inside(base, "a/../../a.mkv")
    assert not T.inside(base, "C:/Windows/a.mkv") and not T.inside(base, str(tmp_path / "x.mkv"))
    assert not T.inside(base, "") and not T.inside(base, ".")
    if os.name == "nt":
        assert not T.inside(base, "D:a.mkv") or str(base).upper().startswith("D:")


def test_a_torrent_whose_names_leave_the_save_folder_is_refused(sess, tor, tmp_path):  # noqa: F811
    m = T.read_torrent(tor)
    m.files[0].path = "../../evil.mkv"
    with pytest.raises(T.TorrentError, match="outside the save folder"):
        m.disk_path(tmp_path / "dl", 0)
    with pytest.raises(T.TorrentError, match="outside the save folder"):
        sess.start(m, [0], tmp_path / "dl")
    m2 = T.read_torrent(tor)
    m2.name = "C:\\Windows"
    with pytest.raises(T.TorrentError, match="outside the save folder"):
        m2.disk_path(tmp_path / "dl", 0)


# ---------------------------------------------------------------------------------------------------------------------
# 5. exit code 3 without a result: a stop (abort / Fatal Python error), not a finished run
# ---------------------------------------------------------------------------------------------------------------------

def test_exit_3_with_its_result_line_or_verify_json_is_finished_else_a_stop(tmp_path, monkeypatch):
    from match_cuts import batch

    from mr_app.core import results as R
    run = tmp_path / "edit"
    (run / "extras").mkdir(parents=True)
    monkeypatch.setattr(batch, "checks_of", lambda d: {"pass": True, "failures": [], "notes": []})
    monkeypatch.setattr(batch, "captions_of", lambda d: {"written": True, "why": "", "doubtful": []})
    res = R.read_result(run, 3, "match_cuts result: PASS (criterion 6 not verified)\n", "")
    assert res["label"] == R.PASS and res["stop"] == ""
    res = R.read_result(run, 3, "", "Fatal Python error: Aborted\n\nThread 0x01 (most recent call first):\n")
    assert res["label"] == R.FAIL and res["stop"].startswith("Fatal Python error")
    assert res["failures"][0].startswith("the run stopped (exit 3)")
    res = R.read_result(run, 3, "", "")                                       # abort(): nothing said at all
    assert res["label"] == R.FAIL and res["stop"] == batch.ABORT_WORDS
    (run / "extras" / "verify.json").write_text("{}", encoding="utf-8")       # it wrote its result: finished
    res = R.read_result(run, 3, "", "")
    assert res["label"] == R.PASS and res["stop"] == ""
    assert R.label_of(3, "", {"pass": True}, finished=False) == R.FAIL
    assert R.label_of(3, "", {"pass": True, "notes": []}) == R.PASS          # by the code alone, as before
