"""Day 12, helper "extra": tests for what today's new modules had none for -- the error paths and plain messages of
core.shutdown (never turned off while something blocks it), core.problems / core.waiting (statuses, ids, the prompt,
the fix finder), core.newvideo (remembered choices, torrent rows), core.torrent (sizes, bencode, a broken .torrent,
trackers that never answer), and what the click-through of the real window found (a dropped .torrent is a torrent,
not a video file)."""
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

from mr_app.core import newvideo as NV  # noqa: E402
from mr_app.core import problems as PR  # noqa: E402
from mr_app.core import shutdown as SD  # noqa: E402
from mr_app.core import torrent as T  # noqa: E402
from mr_app.core import waiting as WA  # noqa: E402


# ---------------------------------------------------------------------------------------------------------------------
# core.shutdown: what stops the PC from turning off
# ---------------------------------------------------------------------------------------------------------------------

class _Proc:
    def __init__(self, name, cpu, gone=False):
        self.info = {"name": name}
        self.cpu, self.gone, self.asked = cpu, gone, 0

    def cpu_percent(self, _interval):
        self.asked += 1
        if self.gone and self.asked > 1:
            raise RuntimeError("the process ended")
        return 0.0 if self.asked == 1 else self.cpu


def _fake_psutil(monkeypatch, procs):
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(process_iter=lambda _attrs: list(procs)))


def test_media_encoder_counts_only_when_it_is_encoding(monkeypatch):
    _fake_psutil(monkeypatch, [_Proc("Adobe Media Encoder.exe", 40.0), _Proc("explorer.exe", 90.0)])
    assert SD.media_encoder_busy(sample_s=0) is True
    _fake_psutil(monkeypatch, [_Proc("Adobe Media Encoder.exe", 0.5)])            # open and idle
    assert SD.media_encoder_busy(sample_s=0) is False
    _fake_psutil(monkeypatch, [_Proc("explorer.exe", 90.0)])                       # not open
    assert SD.media_encoder_busy(sample_s=0) is False
    _fake_psutil(monkeypatch, [_Proc("Adobe Media Encoder.exe", 50.0, gone=True)])  # closed while sampled
    assert SD.media_encoder_busy(sample_s=0) is False


def test_media_encoder_without_psutil_is_not_a_blocker(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)                               # import fails
    assert SD.media_encoder_busy(sample_s=0) is False


def test_blockers_say_each_reason_in_plain_words_and_a_broken_check_is_skipped(monkeypatch):
    def broken():
        raise OSError("cannot read the windows")
    monkeypatch.setattr(SD, "CHECKS", [("Premiere has unsaved work -- save it, then the PC turns off", lambda: True),
                                       ("a render is running in Premiere", broken),
                                       ("Adobe Media Encoder is rendering", lambda: True)])
    assert SD.blockers() == ["Premiere has unsaved work -- save it, then the PC turns off",
                             "Adobe Media Encoder is rendering"]
    calls = []
    monkeypatch.setattr(SD, "SHUTDOWN", lambda: calls.append("off"))
    assert SD.turn_off() and calls == []                                           # blocked: never turned off
    monkeypatch.setattr(SD, "CHECKS", [("x", broken)])
    assert SD.blockers() == [] and SD.turn_off() == [] and calls == ["off"]


def test_the_real_shutdown_is_never_the_one_the_tests_see():
    """conftest-free guard: every test that reaches turn_off replaces SHUTDOWN; the default is the real command,
    so this test only checks it is named and not called here."""
    assert SD.SHUTDOWN.__name__ in ("_real_shutdown", "<lambda>")


# ---------------------------------------------------------------------------------------------------------------------
# core.problems: statuses, ids, plain messages, the prompt
# ---------------------------------------------------------------------------------------------------------------------

def test_problem_status_words():
    assert PR.state_of("") == PR.OPEN and PR.state_of("open") == PR.OPEN
    assert PR.state_of("fixed 2026-10-19") == PR.FIXED and PR.state_of("Done") == PR.FIXED
    assert PR.state_of("not fixed — the RAW is gone") == PR.NOT_FIXED
    assert PR.state_of("not-fixed") == PR.NOT_FIXED
    assert PR.state_of("skipped (my mistake) 2026-10-19 10:00") == PR.SKIPPED
    assert PR.state_of("something else") == PR.OPEN                       # unknown words stay open (never lost)


def test_problem_id_is_the_same_for_the_same_run_however_it_is_written():
    a = PR.make_id(r"D:\Shared\Projects\x\edit", "Video", "FAIL: XML PERSON  --  at 1 s")
    b = PR.make_id("D:\\Shared\\Projects\\x\\edit\\", "video", "FAIL: XML PERSON -- at 1 s")
    assert a == b and len(a) == 10
    assert PR.make_id(r"D:\Shared\Projects\x\edit", "video", "another check") != a


def test_set_status_says_plainly_when_there_is_no_file_or_no_such_problem(tmp_path):
    assert "there is no problems_queue.md" in PR.set_status(tmp_path, "abc", "fixed")
    PR.add(tmp_path, PR.Problem(name="v", check="FAIL: XML AUDIO", run=str(tmp_path / "v" / "edit")))
    assert PR.skip(tmp_path, "0000000000") == "that problem is not in problems_queue.md any more"
    pid = PR.read(tmp_path)[0].key()
    assert PR.not_fixed(tmp_path, pid, "  ") == ""
    p = PR.read(tmp_path)[0]
    assert p.state == PR.NOT_FIXED and p.status.endswith("no reason given")
    assert PR.fixed(tmp_path, pid, "2026-10-19") == "" and PR.read(tmp_path) == []
    assert PR.read(tmp_path, include_closed=True)[0].status == "fixed 2026-10-19"


def test_check_text_names_the_check_or_the_stop():
    assert PR.check_text({"label": "FAIL", "failures": ["XML PERSON: the person is outside the window"]}) == \
        "FAIL: XML PERSON -- XML PERSON: the person is outside the window"
    assert PR.check_text({"label": "FAIL", "exit": 3, "failures": ["the run stopped (exit 3): out of memory"]}) == \
        "stopped (exit 3) -- out of memory"
    assert PR.check_text({"label": "OK with a note", "notes": ["captions are late"]}) == \
        "OK with a note -- captions are late"
    assert PR.check_text({"label": "FAIL"}) == "FAIL"
    assert len(PR.check_text({"label": "FAIL", "failures": ["x" * 900]})) == 300


def test_open_runs_and_the_prompt_leave_out_closed_problems(tmp_path):
    assert PR.prompt_section(tmp_path, 4) == [] and PR.open_runs(tmp_path) == []
    PR.add(tmp_path, PR.Problem(name="a", check="FAIL: XML SYNC", run=str(tmp_path / "a" / "edit"), computer="laptop",
                                lines=["match_cuts result: FAIL"]))
    PR.add(tmp_path, PR.Problem(name="b", check="FAIL: XML AUDIO", run=str(tmp_path / "b" / "edit")))
    a, b = PR.read(tmp_path)
    PR.not_fixed(tmp_path, a.key(), "the laptop copy is missing")
    PR.skip(tmp_path, b.key())
    assert PR.open_runs(tmp_path) == [str(tmp_path / "a" / "edit")]
    text = "\n".join(PR.prompt_section(tmp_path, 4))
    assert text.startswith("## 4. Problems to fix") and "**a**" in text and "**b**" not in text
    assert "Tried before: not fixed — the laptop copy is missing" in text and "> match_cuts result: FAIL" in text
    assert "on the laptop" in text


# ---------------------------------------------------------------------------------------------------------------------
# core.waiting: a finished job's card, states, which .prproj is a fix
# ---------------------------------------------------------------------------------------------------------------------

def test_from_job_keeps_the_run_inside_the_project_and_the_file_names(tmp_path):
    proj = tmp_path / "2026-10-19_x"
    job = SimpleNamespace(project=proj, name="x", raw=tmp_path / "raw.mp4", competitor=tmp_path / "comp.mp4",
                          channel="LouLaughs", title="Line one\nline two")
    e = WA.from_job(job, {"label": "PASS", "run_dir": str(proj / "edit-2")})
    assert (e.name, e.run_in_project, e.raw, e.competitor, e.result) == \
        ("2026-10-19_x", "edit-2", "raw.mp4", "comp.mp4", "PASS")
    outside = WA.from_job(job, {"label": "PASS", "run_dir": str(tmp_path / "elsewhere" / "edit")})
    assert outside.run_in_project == ""                                    # a run outside the project: no relative
    bare = WA.from_job(SimpleNamespace(name="loose"), {"label": "PASS", "run_dir": str(tmp_path / "r" / "edit")})
    assert bare.name == "loose" and bare.project == "" and bare.raw == ""


def test_record_only_for_good_results_and_never_raises(tmp_path, monkeypatch):
    job = SimpleNamespace(project=tmp_path / "p", name="p", raw=None, competitor=None)
    assert WA.record(tmp_path, job, {"label": "FAIL"}) == "" and WA.read(tmp_path) == []
    monkeypatch.setattr(WA, "add", lambda *_a: (_ for _ in ()).throw(OSError("the hub is offline")))
    msg = WA.record(tmp_path, job, {"label": "PASS", "run_dir": str(tmp_path / "p" / "edit")})
    assert msg.startswith("the waiting card could not be saved") and "the hub is offline" in msg


def test_set_state_changes_only_its_card(tmp_path):
    WA.add(tmp_path, WA.Entry(name="a", project=str(tmp_path / "a"), run=str(tmp_path / "a" / "edit"), result="PASS"))
    WA.add(tmp_path, WA.Entry(name="b", project=str(tmp_path / "b"), run=str(tmp_path / "b" / "edit"), result="PASS"))
    a = next(e for e in WA.read(tmp_path) if e.name == "a")
    assert WA.set_state(tmp_path, "nothing-like-it", WA.SKIPPED) == ""        # unknown: nothing changes
    assert {e.name for e in WA.read(tmp_path)} == {"a", "b"}
    assert WA.set_state(tmp_path, a.key(), WA.SKIPPED) == ""
    assert [e.name for e in WA.read(tmp_path)] == ["b"]


def test_is_fix_waits_until_the_file_is_saved_and_newer_than_the_edit(tmp_path):
    now = time.time()
    f = tmp_path / "video16_fixed.prproj"
    f.write_bytes(b"x")
    os.utime(f, (now - 5, now - 5))
    assert WA.is_fix(f, finished=now - 3600, now=now) is False            # still being written (settle time)
    os.utime(f, (now - 60, now - 60))
    assert WA.is_fix(f, finished=now - 3600, now=now) is True
    assert WA.is_fix(f, finished=now + 3600, now=now + 7200) is False      # older than the edit: not a fix of it
    assert WA.is_fix(tmp_path / "gone.prproj", finished=0, now=now) is False
    own = tmp_path / "2026-10-19_x_edit.prproj"                            # the app's own project, just made
    own.write_bytes(b"x")
    os.utime(own, (now - 60, now - 60))
    assert WA.is_fix(own, finished=now - 3600, now=now) is False


# ---------------------------------------------------------------------------------------------------------------------
# core.newvideo: remembered choices and torrent rows
# ---------------------------------------------------------------------------------------------------------------------

def test_prefs_are_remembered_and_a_broken_settings_file_gives_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(tmp_path / "settings.json"))
    assert NV.get_pref("newvideo_channel", "LouLaughs") == "LouLaughs"
    NV.set_pref("newvideo_channel", "Flick707")
    assert NV.get_pref("newvideo_channel") == "Flick707"
    from mr_app.core import settings as S
    monkeypatch.setattr(S, "load", lambda *a, **k: (_ for _ in ()).throw(ValueError("broken")))
    assert NV.get_pref("newvideo_channel", "x") == "x"
    NV.set_pref("newvideo_channel", "y")                                   # never a crash


def test_torrent_rows_are_kept_and_forgotten_in_the_app_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    assert NV.torrent_rows_file() == tmp_path / "appdata" / "torrent_rows.json"
    assert NV.torrent_rows() == {}
    NV.keep_torrent_row("gid1", {"channel": "Flick707", "comp_file": tmp_path / "c.mp4", "not_a_field": 1})
    NV.keep_torrent_row("", {"channel": "x"})                              # no id: nothing kept
    assert NV.torrent_rows() == {"gid1": {"channel": "Flick707", "comp_file": str(tmp_path / "c.mp4")}}
    NV.forget_torrent_row("nope")
    NV.forget_torrent_row("gid1")
    assert NV.torrent_rows() == {}
    NV.torrent_rows_file().write_text("[1, 2]", encoding="utf-8")         # not a dict: read as none
    assert NV.torrent_rows() == {}


def test_is_link_only_for_web_links():
    assert NV.is_link(NV.YOUTUBE) and NV.is_link(NV.LINK)
    assert not NV.is_link(NV.TORRENT) and not NV.is_link(NV.FILE) and not NV.is_link(NV.NONE)


# ---------------------------------------------------------------------------------------------------------------------
# core.torrent: sizes, bencode, broken files, trackers that never answer
# ---------------------------------------------------------------------------------------------------------------------

def test_sizes_and_speeds_read_like_windows():
    assert T.human_size(None) == "?" and T.human_size(512) == "512 B" and T.human_size(2048) == "2 KB"
    assert T.human_size(3 * 1024 ** 3) == "3.00 GB" and T.human_size(640 * 1024 ** 2) == "640 MB"
    assert T.human_speed(0) == T.human_speed(None) == "0 MB/s"
    assert T.human_speed(500 * 1024) == "500 KB/s" and T.human_speed(38 * 1024 ** 2) == "38.0 MB/s"
    assert T.no_one_sharing(1).startswith("No one is sharing this file right now — tried for 1 minute.")
    assert "tried for 5 minutes" in T.no_one_sharing(5)


def test_bencode_round_trip_and_what_it_refuses():
    v = {"name": "x", "n": 3, "flag": True, "l": [b"a", 1], "d": {"k": "v"}}
    assert T.bdecode(T.bencode(v)) == {b"name": b"x", b"n": 3, b"flag": 1, b"l": [b"a", 1], b"d": {b"k": b"v"}}
    with pytest.raises(TypeError):
        T.bencode(1.5)
    for bad in (b"5:abc", b"i12", b"x", b"", b"l1:a"):
        with pytest.raises((ValueError, IndexError)):
            T.bdecode(bad)


@pytest.mark.parametrize("info", [
    {"name": "x", "files": [b"not a dict"], "piece length": 16384, "pieces": b"0" * 20},
    {"name": "x", "files": [{"length": "big", "path": ["a.mp4"]}], "piece length": 16384, "pieces": b"0" * 20},
    {"name": "x", "length": "big", "piece length": 16384, "pieces": b"0" * 20},
])
def test_a_torrent_with_a_broken_file_list_is_refused_in_plain_words(info):
    with pytest.raises(T.TorrentError) as e:
        T.read_torrent(T.bencode({"info": info}))
    assert "torrent" in str(e.value).lower() and "Error" not in str(e.value)


def test_a_missing_torrent_file_is_said_plainly(tmp_path):
    with pytest.raises(T.TorrentError) as e:
        T.read_torrent(tmp_path / "gone.torrent")
    assert str(e.value).startswith("The .torrent file could not be read")


def test_trackers_that_never_answer_give_no_seeders_quickly():
    t0 = time.monotonic()
    got = T.scrape_seeders("ab" * 20, ["wss://example.invalid/announce", "ftp://x", "udp://127.0.0.1:9/announce",
                                       "http://127.0.0.1:9/announce"], timeout=0.5)
    assert got is None and time.monotonic() - t0 < 5


# ---------------------------------------------------------------------------------------------------------------------
# found in the real window: a .torrent dropped (or chosen) on the New video form is a torrent, not a video file
# ---------------------------------------------------------------------------------------------------------------------

from test_newvideo_ui import COMP, page  # noqa: E402,F401 - the New video page fixture


def _drop(widget, files):
    from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QApplication
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(f)) for f in files])
    acts, btn, mods = Qt.DropAction.CopyAction, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    enter = QDragEnterEvent(QPoint(5, 5), acts, md, btn, mods)
    QApplication.sendEvent(widget, enter)
    drop = QDropEvent(QPointF(5, 5), acts, md, btn, mods)
    QApplication.sendEvent(widget, drop)
    return enter.isAccepted() and drop.isAccepted()


def test_a_dropped_torrent_file_starts_the_torrent_not_a_video_copy(page, tmp_path, monkeypatch):
    from mr_app.tools import download_tool as D
    tor = tmp_path / "Some.Show.torrent"
    tor.write_bytes(T.bencode({"info": {"name": "Some.Show.mp4", "length": 10, "piece length": 16384,
                                        "pieces": b"0" * 20}}))
    comp = tmp_path / "funny_short.mp4"
    comp.write_bytes(b"x")
    started = []
    monkeypatch.setattr(D.DownloadPage, "start_torrent", lambda self, it: started.append(it))
    assert _drop(page.drop, [tor, comp])
    assert page.d_raw.text() == "TORRENT" and page.comp.text() == str(comp)
    assert page.raw_file is None                              # not a video file: the torrent's own path
    it = page.add()
    assert it is not None and it.raw_torrent == str(tor) and it.raw_file is None and started == [it]


def test_a_dropped_torrent_alone_asks_for_the_competitor(page, tmp_path):
    tor = tmp_path / "Some.Show.torrent"
    tor.write_bytes(b"d4:infod4:name1:xee")
    page._dropped([tor])                                      # noqa: SLF001 - the drop zone's handler
    assert page.add() is None and "needs a competitor" in page.msg.text()


def test_typing_a_link_after_a_drop_clears_the_dropped_files_note(page, tmp_path):
    f = tmp_path / "episode.mp4"
    f.write_bytes(b"x")
    page._dropped([f])                                        # noqa: SLF001
    assert page.msg.text() == "RAW: episode.mp4"
    from PySide6.QtTest import QTest
    page.raw.selectAll()
    QTest.keyClicks(page.raw, "https://youtu.be/AbCdEf12345")                 # typed over it (textEdited)
    assert page.raw_file is None and page.msg.text() == ""
    page.comp.setText(COMP)
    assert page.add().raw_url


# ---------------------------------------------------------------------------------------------------------------------
# 12.5: Send to Match cuts writes project.json / title.txt off the window's thread
# ---------------------------------------------------------------------------------------------------------------------

def _ready_item(page, tmp_path):
    from mr_app.core import projects as P
    from mr_app.tools import download_tool as D
    p = P.create(tmp_path / "projects", competitor_url=COMP, raw_url="https://youtu.be/AbCdEf12345")
    it = D.Item(COMP, raw_url="https://youtu.be/AbCdEf12345", channel="LouLaughs", title="line one\nline two",
                project=p.folder, name=p.folder.name, state=D.GATED)
    page.items.append(it)
    page.rows[it.id] = D.VideoRow(page, it)
    return it, p


def test_send_to_match_cuts_writes_the_project_in_the_background(page, tmp_path, monkeypatch):
    from mr_app.core import projects as P
    from mr_app.tools import download_tool as D
    pending = []
    monkeypatch.setattr(D, "off_thread", lambda fn, done, failed=None: pending.append((fn, done)))
    it, p = _ready_item(page, tmp_path)
    page.send_to_edit(it, start=True)
    assert it.state == D.SENT and it.job is None and page.queue_page.jobs == [] and len(pending) == 1
    assert not P.load(p.folder).data.get("title")                          # nothing written on the window thread
    assert page.anything_working()                                         # "Turn off the PC" waits for it
    page.send_to_edit(it, start=True)                                      # pressed again meanwhile: nothing more
    assert len(pending) == 1
    fn, done = pending[0]
    done(fn())
    job, autostart = page.queue_page.jobs[-1]
    assert job is it.job and autostart and job.title == "line one\nline two" and job.channel == "LouLaughs"
    assert P.load(p.folder).data["title"] == "line one\nline two"
    assert (p.folder / "title.txt").read_text(encoding="utf-8").strip() == "line one\nline two"


def test_send_to_match_cuts_failure_is_red_in_plain_words_and_a_removed_row_is_not_queued(page, tmp_path,
                                                                                          monkeypatch):
    from mr_app.core import flow
    from mr_app.tools import download_tool as D
    pending = []
    monkeypatch.setattr(D, "off_thread", lambda fn, done, failed=None: pending.append((fn, done)))

    def no_files(*_a):
        raise flow.StepError("the competitor and the RAW are not both in the folder (download and convert first)")
    monkeypatch.setattr(flow, "edit_job", no_files)
    it, _p = _ready_item(page, tmp_path)
    page.send_to_edit(it)
    fn, done = pending.pop()
    done(fn())
    assert it.state == D.FAILED and page.queue_page.jobs == []
    assert page.now_text(it) == ("Stopped at edit: could not make the edit's job: the competitor and the RAW are not "
                                 "both in the folder (download and convert first)")
    it2, _p2 = _ready_item(page, tmp_path / "b")
    page.send_to_edit(it2)
    page.remove_item(it2)                                                  # removed before the write finished
    fn, done = pending.pop()
    done(fn())
    assert page.queue_page.jobs == []
