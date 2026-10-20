"""Day 13, item 1.2: the app always says the real reason a run stopped, in plain words, with what to do -- never just
"details in ... match_cuts.log" (the laptop's three Borat runs in problems_queue.md: ffmpeg's 4294967268 = -28 = the
disk is full; FileNotFoundError ...\\match_cuts_pool_...\\state-4.pkl = a crash after C: filled up). One shared
function (match_cuts.plain_reason) behind results.read_result, the queue row and its report, the New video row,
History and problems_queue.md's entry.

Run: python -m pytest tools/app/tests/test_errors_day13.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import history as HI  # noqa: E402
from mr_app.core import newvideo as NV  # noqa: E402
from mr_app.core import problems as PR  # noqa: E402
from mr_app.core import results as R  # noqa: E402

RUN = r"C:\Users\gvida\Desktop\bh\MovieRecaps\input\downloads\2026-10-19_borat-cultural-learnings-of-america-for"
CMD = (r"ffmpeg -v error -nostdin -y -i " + RUN + r"\Borat (2006) (1080p BluRay x265 Silence).mp4 -map 0:v:0 "
       r"-map 0:a:0 -filter:v settb=1001/24000,setpts=N,fps=fps=24000/1001,trim=end_frame=120553,setsar=1,"
       r"format=yuv420p -c:v libx264 -preset veryfast -crf 12 -map_metadata -1 -map_chapters -1 "
       r"C:\Users\gvida\Desktop\bh\MovieRecaps\work\conform\ab12\raw_ae.mp4")
BORAT_FFMPEG = (f"match_cuts: ERROR: RuntimeError: conform raw: ffmpeg failed (4294967268): {CMD}\n\n"
                f"match_cuts: details in {RUN}\\edit-2\\extras\\match_cuts.log\nRun folder: {RUN}\\edit-2\n")
BORAT_POOL = ("match_cuts: ERROR: FileNotFoundError: [Errno 2] No such file or directory: "
              r"'C:\\Users\\gvida\\AppData\\Local\\Temp\\match_cuts_pool_5mhjfe2b\\state-4.pkl'" "\n"
              f"match_cuts: details in {RUN}\\edit\\extras\\match_cuts.log\nRun folder: {RUN}\\edit\n")
ENOSPC_LOG = ("2026-10-20 00:16:40,551 ERROR match_cuts.pipeline stereo copy failed: [Errno 28] No space left on "
              "device\nTraceback (most recent call last):\n  File \"x.py\", line 1, in f\n"
              "OSError: [Errno 28] No space left on device\n")
WRONG = ("12:00:01 INFO == S1 pair check\nmatch_cuts: WRONG PAIR: This competitor doesn't seem to come from this RAW "
         "(sound: 0 of 4, picture: 0 of 16)\nRun folder: C:\\x\n")


def _run(tmp_path, log: str = "") -> Path:
    run = tmp_path / "edit"
    (run / "extras").mkdir(parents=True)
    if log:
        (run / "extras" / "match_cuts.log").write_text(log, encoding="utf-8")
    return run


# ---------------------------------------------------------------------------------------------------------------------
# results.read_result: the reason, what to do, the row's line
# ---------------------------------------------------------------------------------------------------------------------

def test_a_full_disk_is_said_with_what_to_do_never_details_in(tmp_path):
    res = R.read_result(_run(tmp_path), 2, "", BORAT_FFMPEG)
    assert res["label"] == R.FAIL and res["reason_kind"] == "disk full"
    assert res["reason"].startswith("the disk C: ran out of space") and "while converting the RAW" in res["reason"]
    assert res["todo"] == "free space on C: (Board → Clean up), then press Try again"
    assert res["failures"][0].startswith("the run stopped (exit 2): The disk C: ran out of space")
    line = R.short(res)
    assert line == res["reason_line"] and line.startswith("The disk C: ran out of space")
    assert line.endswith("Free space on C: (Board → Clean up), then press Try again.")
    for bad in ("details in", "match_cuts.log", "4294967268", "exit 2", "RuntimeError"):
        assert bad not in line
    assert "ffmpeg failed (4294967268)" in res["reason_detail"]          # the technical line, for the report
    assert R.stopped_by_itself(res)


def test_the_lost_pool_file_is_a_crash_after_c_filled_up(tmp_path):
    res = R.read_result(_run(tmp_path), 2, "", BORAT_POOL)
    assert res["reason_kind"] == "disk full" and "temp folder" in res["reason"] and "C: is full" in res["reason"]
    res = R.read_result(_run(tmp_path / "b", ENOSPC_LOG), 2, "", BORAT_POOL)     # the log shows the disk full first
    assert res["reason"].startswith("the run crashed after C: filled up")
    assert "state-4.pkl" not in R.short(res) and "details in" not in R.short(res)


def test_wrong_pair_and_the_input_checks_keep_their_own_words(tmp_path):
    res = R.read_result(_run(tmp_path), 2, "", WRONG)
    assert res["label"] == R.WRONG_PAIR and res["reason_kind"] == "tool stop" and not R.stopped_by_itself(res)
    assert R.short(res).startswith("This competitor doesn't seem to come from this RAW (sound: 0 of 4")
    pic = "match_cuts: PICTURE NOT FOUND (sound found) -- the RAW may be a different cut of the film\n"
    res = R.read_result(_run(tmp_path / "p"), 2, "", pic)
    assert res["label"] == R.PICTURE_NOT_FOUND and R.short(res).startswith("PICTURE NOT FOUND (sound found)")
    assert res["failures"][0].startswith("the run stopped (exit 2): PICTURE NOT FOUND")


def test_an_unknown_stop_shows_the_last_error_line_of_the_log_not_a_traceback(tmp_path):
    log = ("2026-10-20 00:11:00,123 ERROR match_cuts.pipeline people analysis failed: shape mismatch\n"
           "Traceback (most recent call last):\n  File \"x.py\", line 1, in f\nValueError: shape mismatch\n")
    res = R.read_result(_run(tmp_path, log), 2, "", "match_cuts: details in C:\\x\\extras\\match_cuts.log\n")
    assert res["reason"] == "the run stopped on a problem the app does not know yet: people analysis failed: " \
                            "shape mismatch"
    assert "the full log: Open log" in R.short(res) and "Traceback" not in R.short(res)


def test_a_crash_code_of_the_tool_in_plain_words(tmp_path):
    res = R.read_result(_run(tmp_path), 3221225477, "", "")
    assert res["reason_kind"] == "crash" and "memory access" in R.short(res) and R.stopped_by_itself(res)
    res = R.read_result(_run(tmp_path / "s"), 3221225725, "", "")
    assert "stack" in R.short(res)


# ---------------------------------------------------------------------------------------------------------------------
# problems_queue.md, project.json, History, the New video row's words
# ---------------------------------------------------------------------------------------------------------------------

def test_the_problems_entry_title_carries_the_plain_reason(tmp_path):
    run = _run(tmp_path)
    (run / "extras" / "batch_console.log").write_text("python -m match_cuts ...\n\n\n" + BORAT_FFMPEG,
                                                      encoding="utf-8")
    res = R.read_result(run, 2, "", BORAT_FFMPEG)
    res.update(exit=2, log=str(run / "extras" / "batch_console.log"))
    assert PR.is_tool_problem(res)
    p = PR.from_result("2026-10-19_borat-cultural-learnings-of-america-for", res, computer="laptop")
    assert p.check.startswith("stopped (exit 2) -- The disk C: ran out of space")
    assert "details in" not in p.check and "Board → Clean up" in p.check
    assert any("ffmpeg failed (4294967268)" in ln for ln in p.lines)        # the key line for the night run stays
    assert not any(ln.startswith("match_cuts: details in") for ln in p.lines)
    hub = tmp_path / "hub"
    hub.mkdir()
    assert PR.add(hub, p) == ""
    text = (hub / PR.FILE).read_text(encoding="utf-8")
    assert "· stopped (exit 2) -- The disk C: ran out of space" in text


def test_project_json_keeps_the_plain_reason_and_history_says_it(tmp_path):
    from mr_app.core import flow
    from mr_app.core import projects as PJ
    p = PJ.create(tmp_path / "projects", name="borat")
    res = R.read_result(_run(tmp_path), 2, "", BORAT_FFMPEG)
    flow.record_edit(p, res)
    step = PJ.load(p.folder).data["steps"]["edit"]
    assert step["state"] == PJ.FAILED and step["error"] == res["reason_line"]
    ev = [e for e in HI.project_events(PJ.load(p.folder).data) if e.what.startswith("Edit failed")][0]
    assert ev.detail.startswith("The disk C: ran out of space") and "details in" not in ev.detail


def test_history_says_an_older_runs_technical_line_in_plain_words():
    data = {"name": "borat", "created": "2026-10-19T23:00:00+03:00",
            "steps": {"edit": {"state": "failed", "result": "FAIL", "finished": "2026-10-20T00:30:00+03:00",
                               "failures": [f"the run stopped (exit 2): ERROR: RuntimeError: conform raw: ffmpeg "
                                            f"failed (4294967268): {CMD}"]}}}
    ev = [e for e in HI.project_events(data) if e.what.startswith("Edit failed")][0]
    assert ev.detail.startswith("The disk C: ran out of space: the run stopped while converting the RAW")
    data["steps"]["edit"]["failures"] = [f"the run stopped (exit 2): details in {RUN}\\edit-2\\extras\\match_cuts.log"]
    ev = [e for e in HI.project_events(data) if e.what.startswith("Edit failed")][0]
    assert ev.detail.startswith("The run stopped; the reason is in its log") and "match_cuts.log" not in ev.detail


def test_the_new_video_rows_words(tmp_path):
    assert NV.plain_error(f"conform raw: ffmpeg failed (4294967268): {CMD}").startswith("The disk C: ran out of space")
    pool = BORAT_POOL.splitlines()[0]
    assert "temp folder" in NV.plain_error(pool) and "state-4" not in NV.plain_error(pool)
    assert NV.plain_error("RAW: this video is private") == "RAW: this video is private"      # plain stays plain
    res = R.read_result(_run(tmp_path), 2, "", BORAT_FFMPEG)
    assert NV.plain_error(res["reason_line"]) == res["reason_line"]
    ytdlp = ("the download did not work: unable to open for writing: [Errno 2] No such file or directory: "
             "'C:\\\\p\\\\x.mp4.part'")                       # a download's words: never the edit run's advice
    assert "the RAW and the competitor" not in NV.plain_error(ytdlp)


def test_just_cuts_says_a_full_disk_plainly(tmp_path):
    from mr_app.core import flow
    run = _run(tmp_path)
    err = (f"match_cuts: the cuts could not be made: RuntimeError: conform raw: ffmpeg failed (4294967268): {CMD}\n")
    res = flow.just_cuts_result(run, 2, err)
    assert res["label"] == "FAIL" and res["failures"][0].startswith("The disk C: ran out of space")
    assert res["reason_kind"] == "disk full" and "RuntimeError" not in res["failures"][0]
    res = flow.just_cuts_result(run, 2, "match_cuts: the template x.png cannot be used: no transparent hole\n")
    assert res["failures"][0] == "the template x.png cannot be used: no transparent hole"     # its own words stay


def test_the_apps_own_conversion_reads_ffmpegs_exit_code():
    from mr_app.core import convert as C
    assert C._ffmpeg_problem("", 4294967268) == "the disk is full: free some space and try again"     # noqa: SLF001
    assert C._ffmpeg_problem("", -12).startswith("FFmpeg stopped: out of memory")                     # noqa: SLF001
    assert C._ffmpeg_problem("x\nConversion failed!\n", 1) == "FFmpeg failed: Conversion failed!"     # noqa: SLF001


# ---------------------------------------------------------------------------------------------------------------------
# the real queue row and its report
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def page(tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.tools import match_cuts_tool as M
    from mr_app.ui import motion as Mo
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    toasts = []
    ctx = SimpleNamespace(set_strip_queue=lambda n, running: None, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": toasts.append((text, kind)))
    monkeypatch.setattr(M, "seconds_of", lambda p: 46.0)
    p = M.MatchCutsPage(ctx)
    p.toasts = toasts
    p.resize(900, 1200)
    yield p
    Mo.set_enabled(True)
    p.deleteLater()
    app.processEvents()


def _done_job(page, tmp_path, res: dict):
    from mr_app.core import jobs as J
    d = tmp_path / "v"
    d.mkdir(parents=True, exist_ok=True)
    (d / "comp.mp4").write_bytes(b"c")
    (d / "raw.mp4").write_bytes(b"r")
    j = J.Job("borat", d / "comp.mp4", d / "raw.mp4", source="folder borat", mode=J.FAST)
    j.comp_s, j.raw_s = 45.0, 5600.0
    page._added_jobs(J.DropResult([j], None, []))                           # noqa: SLF001
    j.state, j.result, j.run_dir = J.DONE, dict(res), tmp_path
    page.rows[j.id].refresh()
    return j


def test_the_queue_row_says_why_and_offers_try_again(page, tmp_path):
    from mr_app.core import jobs as J
    res = R.read_result(_run(tmp_path), 2, "", BORAT_FFMPEG)
    j = _done_job(page, tmp_path, res)
    row = page.rows[j.id]
    assert row.note.text() == res["reason_line"] and "details in" not in row.note.text()
    assert row.b_retry.isVisibleTo(row) and row.b_retry.text() == "Try again"
    n = len(page.jobs)
    row.b_retry.click()
    assert len(page.jobs) == n + 1
    again = page.jobs[-1]
    assert again.mode == J.FAST and again.take == 2 and again.state == J.READY
    assert ("Queued again (Fast)", "ok") in page.toasts


def test_try_again_on_a_row_you_ran_anyway_runs_it_anyway_again(page, tmp_path):
    res = R.read_result(_run(tmp_path), 2, "", BORAT_FFMPEG)
    j = _done_job(page, tmp_path, res)
    j.force_inputs = True                                     # you had pressed Run anyway after a WRONG PAIR
    page.rows[j.id].b_retry.click()
    assert page.jobs[-1].force_inputs and page.jobs[-1].take == 2


def test_no_try_again_for_wrong_pair_or_a_pass(page, tmp_path):
    res = R.read_result(_run(tmp_path), 2, "", WRONG)
    j = _done_job(page, tmp_path, res)
    assert not page.rows[j.id].b_retry.isVisibleTo(page.rows[j.id])
    j2 = _done_job(page, tmp_path / "b", {"label": R.PASS, "seconds": 3.0})
    assert not page.rows[j2.id].b_retry.isVisibleTo(page.rows[j2.id])


def test_the_report_says_why_it_stopped_and_what_to_do(page, tmp_path):
    from mr_app.tools import match_cuts_tool as M
    res = R.read_result(_run(tmp_path), 2, "", BORAT_FFMPEG)
    j = _done_job(page, tmp_path, res)
    d = M.ReportDialog(j)
    from PySide6.QtWidgets import QPushButton, QTextBrowser
    html = d.findChild(QTextBrowser).toPlainText()
    assert "Why it stopped" in html and "What to do" in html and "Board → Clean up" in html
    assert "ffmpeg failed (4294967268)" in html                              # "From the log": the technical line
    assert "Open log" in [b.text() for b in d.findChildren(QPushButton)]
    d.deleteLater()
