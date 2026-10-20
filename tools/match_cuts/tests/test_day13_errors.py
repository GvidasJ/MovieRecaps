"""Day 13, item 1.2: the real reason a run stopped, in plain words (plain_reason.py, batch.stop_message /
stop_reason). The examples are the laptop's three Borat runs of problems_queue.md (their logs are on the laptop:
the key lines quoted there are rebuilt here): exit 2 with only "details in ... match_cuts.log" shown, ffmpeg's exit
code 4294967268 (= -28, ENOSPC: the disk is full) and FileNotFoundError ...\\match_cuts_pool_...\\state-4.pkl (the
worker pool's file in the temp folder: a crash after C: filled up)."""
from __future__ import annotations

from match_cuts import batch
from match_cuts import plain_reason as P

RUN = r"C:\Users\gvida\Desktop\bh\MovieRecaps\input\downloads\2026-10-19_borat-cultural-learnings-of-america-for"
FFMPEG_CMD = (r"ffmpeg -v error -nostdin -y -i " + RUN +
              r"\Borat (2006) (1080p BluRay x265 Silence).mp4 -map 0:v:0 -map 0:a:0 -filter:v settb=1001/24000,"
              r"setpts=N,fps=fps=24000/1001,trim=end_frame=120553,setsar=1,format=yuv420p -filter:a aresample=48000,"
              r"asetpts=PTS-STARTPTS,adelay=delays=960S:all=1 -fps_mode passthrough -c:v libx264 -preset veryfast "
              r"-crf 12 -g 48 -bf 2 -profile:v high -c:a aac -b:a 320k -ar 48000 -map_metadata -1 -map_chapters -1 "
              r"C:\Users\gvida\Desktop\bh\MovieRecaps\work\conform\0a1b2c3d\raw_ae.mp4")
# edit-2 / edit-3: the RuntimeError's message ends with ffmpeg's (empty) stderr -> a blank line before "details in"
BORAT_FFMPEG = ("00:29:58 INFO == S2 probe+conform\n"
                f"match_cuts: ERROR: RuntimeError: conform raw: ffmpeg failed (4294967268): {FFMPEG_CMD}\n\n"
                f"match_cuts: details in {RUN}\\edit-2\\extras\\match_cuts.log\n"
                f"Run folder: {RUN}\\edit-2\n")
POOL_FILE = r"'C:\\Users\\gvida\\AppData\\Local\\Temp\\match_cuts_pool_5mhjfe2b\\state-4.pkl'"
BORAT_POOL = ("00:17:40 INFO == S5.2 visual search\n"
              f"match_cuts: ERROR: FileNotFoundError: [Errno 2] No such file or directory: {POOL_FILE}\n"
              f"match_cuts: details in {RUN}\\edit\\extras\\match_cuts.log\n"
              f"Run folder: {RUN}\\edit\n")
FULL_LOG = ("2026-10-20 00:16:02,114 INFO match_cuts.pipeline == S5.2 visual search\n"
            "2026-10-20 00:16:40,551 ERROR match_cuts.pipeline stereo copy failed: [Errno 28] No space left on device\n"
            "Traceback (most recent call last):\n  File \"x.py\", line 1, in f\n"
            "OSError: [Errno 28] No space left on device\n")


def test_details_in_is_never_the_stop_message():
    msg = batch.stop_message(BORAT_FFMPEG, code=2)
    assert "details in" not in msg and "ffmpeg failed (4294967268)" in msg
    msg = batch.stop_message(BORAT_POOL, code=2)
    assert "details in" not in msg and msg.startswith("ERROR: FileNotFoundError")


def test_ffmpeg_exit_4294967268_is_the_disk_is_full():
    assert P.signed32(4294967268) == -28
    assert P.program_code(4294967268)[0] == P.DISK_FULL
    r = P.explain(BORAT_FFMPEG, code=2)
    assert r.kind == P.DISK_FULL and r.drive == "C:"
    assert r.text == "the disk C: ran out of space: the run stopped while converting the RAW"
    assert r.line() == ("The disk C: ran out of space: the run stopped while converting the RAW. Free space on C: "
                        "(Board → Clean up), then press Try again.")
    assert "ffmpeg failed (4294967268)" in r.detail          # the technical line is kept for the log / the problems
    for bad in ("details in", "4294967268", "match_cuts.log", "Traceback"):
        assert bad not in r.line()


def test_the_pool_state_file_missing_is_a_crash_after_c_filled_up():
    r = P.explain(BORAT_POOL, code=2)
    assert r.kind == P.DISK_FULL and r.drive == "C:"
    assert "temp folder (C:\\Users\\gvida\\AppData\\Local\\Temp)" in r.text and "C: is full" in r.text
    assert "free space on C:" in r.todo and "Try again" in r.todo
    sure = P.explain(BORAT_POOL, FULL_LOG, code=2)            # the log shows the full disk before it: said as certain
    assert sure.text.startswith("the run crashed after C: filled up: the tool's helper processes could not read")
    assert "state-4.pkl" in sure.detail and "state-4" not in sure.line()


def test_explain_run_reads_the_runs_own_log(tmp_path, monkeypatch):
    run = tmp_path / "edit"
    (run / "extras").mkdir(parents=True)
    (run / "extras" / "match_cuts.log").write_text(FULL_LOG, encoding="utf-8")
    r = batch.stop_reason(BORAT_POOL, 2, run)
    assert r.text.startswith("the run crashed after C: filled up")
    (run / "extras" / "batch_console.log").write_text("python -m match_cuts ...\n\n\n" + BORAT_FFMPEG, encoding="utf-8")
    monkeypatch.setattr(P, "_free_gb", lambda d: 0.4)
    r2 = P.explain_run(run, "", 2)                            # read back later: the console from batch_console.log
    assert r2.kind == P.DISK_FULL and r2.text.startswith("the disk C: ran out of space (only 0.4 GB free now): ")
    monkeypatch.setattr(P, "_free_gb", lambda d: 92.9)        # room again (its half-written file was removed)
    assert P.explain_run(run, "", 2).text == "the disk C: ran out of space: the run stopped while converting the RAW"


def test_negative_errno_and_averror_codes_of_ffmpeg_and_ffprobe():
    for code, kind in ((-28, P.DISK_FULL), (-12, P.MEMORY), (-2, P.FILE_MISSING), (-13, P.FILE_BUSY),
                       (-5, P.DISK_ERROR), (-22, P.BAD_SETTING), (-27, P.FILE_TOO_BIG), (-38, P.PATH_TOO_LONG),
                       (-1094995529, P.BROKEN_VIDEO), (-541478725, P.BROKEN_VIDEO), (69, P.BROKEN_VIDEO),
                       (255, P.CLOSED)):
        assert P.program_code(code)[0] == kind, code
        assert P.program_code(code + (1 << 32) if code < 0 else code)[0] == kind, code     # as Windows reports it
    assert P.program_code(1) == ("", "") and P.program_code(None) == ("", "")
    r = P.explain(r"match_cuts: ERROR: RuntimeError: ffprobe failed (3199971767): ffprobe -v error -show_streams "
                  "C:\\in\\clip.mp4\n", code=2)
    assert r.kind == P.BROKEN_VIDEO and "while reading a video" in r.text and "download or copy" in r.todo
    r = P.explain("match_cuts: ERROR: RuntimeError: conform competitor: ffmpeg failed (4294967284): ffmpeg -i "
                  "C:\\a.webm C:\\w\\comp.mp4\n", code=2)
    assert r.kind == P.MEMORY and "while converting the competitor" in r.text and "close other" in r.todo
    r = P.explain("match_cuts: ERROR: RuntimeError: command failed (4294967283): C:\\ffmpeg\\bin\\ffmpeg.exe -i "
                  "C:\\a.mp4 C:\\b.mp4\n", code=2)
    assert r.kind == P.FILE_BUSY


def test_ffmpeg_exit_1_uses_ffmpegs_own_words():
    err = ("match_cuts: ERROR: RuntimeError: conform raw: ffmpeg failed (1): ffmpeg -i C:\\x\\raw.mp4 C:\\w\\o.mp4\n"
           "[mov,mp4,m4a,3gp,3g2,mj2 @ 0000020] moov atom not found\nC:\\x\\raw.mp4: Invalid data found when "
           "processing input\n\n")
    r = P.explain(err, code=2)
    assert r.kind == P.BROKEN_VIDEO and "raw.mp4" not in r.todo


def test_windows_crash_codes_in_plain_words():
    assert "memory access" in P.crash_words(3221225477) and "memory access" in P.crash_words(-1073741819)
    assert "stack" in P.crash_words(3221225725) and "0xC0000409" in P.crash_words(3221226505)
    assert P.crash_words(3) == "" and P.crash_words(None) == "" and batch.crash_words(2) == ""
    assert batch.CRASH_CODES is P.CRASH_CODES and batch.ABORT_WORDS == P.ABORT_WORDS
    r = P.explain("", code=3221225477)
    assert r.kind == P.CRASH and "memory access" in r.text and "Open log" in r.todo
    r = P.explain("", code=3221225781)                        # 0xC0000135: a DLL missing
    assert r.kind == P.SETUP and "setup_app.py" in r.todo
    r = P.explain("", code=0x40010004)
    assert r.kind == P.CLOSED and "closed from outside" in r.text
    assert P.explain("", code=3).text == P.ABORT_WORDS
    dump = "Fatal Python error: Aborted\n\nThread 0x0001 (most recent call first):\n  File \"x.py\", line 1 in f\n"
    r = P.explain(dump, code=3)
    assert r.kind == P.CRASH and "Thread" not in r.line() and "File" not in r.line()
    assert P.explain("match_cuts: interrupted\n", code=130).kind == P.CLOSED


def test_python_and_windows_errors_in_plain_words():
    cases = [
        ("match_cuts: ERROR: OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB", P.GPU_MEMORY),
        ("match_cuts: ERROR: OSError: [WinError 1455] The paging file is too small for this operation to complete",
         P.MEMORY),
        ("match_cuts: ERROR: MemoryError: Unable to allocate 3.2 GiB for an array", P.MEMORY),
        (r"match_cuts: ERROR: PermissionError: [WinError 32] The process cannot access the file because it is "
         r"being used by another process: 'C:\\x\\raw_ae.mp4'", P.FILE_BUSY),
        ("match_cuts: ERROR: OSError: [WinError 64] The specified network name is no longer available", P.NETWORK),
        ("match_cuts: ERROR: ModuleNotFoundError: No module named 'scenedetect.output'", P.SETUP),
        ("match_cuts: ERROR: FileNotFoundError: [WinError 2] The system cannot find the file specified", P.SETUP),
        (r"match_cuts: ERROR: FileNotFoundError: [Errno 2] No such file or directory: 'D:\\in\\comp.mp4'",
         P.FILE_MISSING),
        ("match_cuts: ERROR: OSError: [Errno 5] Input/output error", P.DISK_ERROR),
        ("match_cuts: ERROR: OSError: [WinError 206] The filename or extension is too long", P.PATH_TOO_LONG),
        ("match_cuts: ERROR: NoSpaceError: not enough free space to copy raw.mp4 (14.2 GB) into the run folder's "
         r"media (D:\out\x\extras\media): 3.1 GB free -- another drive (C: -> D:): x. Free some space", P.DISK_FULL),
    ]
    for err, kind in cases:
        r = P.explain(err + "\nmatch_cuts: details in C:\\x\\extras\\match_cuts.log\nRun folder: C:\\x\n", code=2)
        assert r.kind == kind, (err, r)
        assert r.todo and "details in" not in r.line() and "match_cuts:" not in r.line(), r
    r = P.explain(cases[3][0], code=2)
    assert "raw_ae.mp4" in r.text and "another program" in r.text
    assert P.explain(cases[7][0], code=2).text == "a file the run needs is not there any more (comp.mp4)"
    assert P.explain(cases[10][0], code=2).drive == "D:"


def test_never_a_traceback_never_a_library_warning():
    tb = ("[ WARN:0@720.058] global net_impl_backend.cpp:345 cv::dnn::Net::Impl::setPreferableTarget Targets\n"
          "Traceback (most recent call last):\n  File \"C:\\x\\pipeline.py\", line 9, in run\n    boom()\n"
          "ValueError: the frame map is empty\n"
          "C:\\x\\align.py:124: UserWarning: The given NumPy array is not writable\n  wav = torch.from_numpy(y)\n")
    r = P.explain(tb, code=2)
    assert r.kind == P.UNKNOWN and r.text == ("the run stopped on a problem the app does not know yet: the frame map "
                                              "is empty (ValueError)")
    assert "the full log: Open log" in r.todo
    for bad in ("Traceback", "File ", "WARN", "UserWarning", "boom()"):
        assert bad not in r.line()
    warn_only = "[ WARN:0@1.0] global foo.cpp:1 x\nC:\\a.py:1: FutureWarning: y\n  z = 1\n"
    r = P.explain(warn_only, code=2)
    assert r.text == "the run stopped without saying why (exit code 2)" and "WARN" not in r.line()


def test_an_unknown_stop_shows_the_last_error_line_of_match_cuts_log():
    log = ("2026-10-20 00:10:00,000 INFO match_cuts.pipeline == S5.4-S6 segments+cutlist\n"
           "2026-10-20 00:11:00,123 ERROR match_cuts.pipeline people analysis failed: shape mismatch 3 vs 4\n"
           "Traceback (most recent call last):\n  File \"x.py\", line 1, in f\nValueError: shape mismatch 3 vs 4\n"
           "2026-10-20 00:12:00,000 ERROR match_cuts.pipeline segments: the cut list is empty\n"
           "Traceback (most recent call last):\n  File \"y.py\", line 2, in g\nIndexError: list index out of range\n")
    r = P.explain("", log, code=2)
    assert r.text == "the run stopped on a problem the app does not know yet: segments: the cut list is empty"
    assert r.todo == "press Try again; if it stops the same way again, the full log: Open log"
    assert r.line().endswith("Press Try again; if it stops the same way again, the full log: Open log.")


def test_the_exception_that_stopped_the_run_wins_over_an_older_error_in_the_log():
    """cli prints the exception that stopped the run only to the console; the log's ERROR lines are older soft
    errors of steps that went on."""
    log = ("2026-10-20 00:11:00,123 ERROR match_cuts.pipeline people analysis failed: shape mismatch 3 vs 4\n"
           "2026-10-20 00:11:02,000 ERROR match_cuts.pipeline speech map failed: [WinError 32] in use\n")
    err = "match_cuts: ERROR: ValueError: the frame map is empty\nmatch_cuts: details in C:\\x\\match_cuts.log\n"
    r = P.explain(err, log, code=2)
    assert r.text == "the run stopped on a problem the app does not know yet: the frame map is empty (ValueError)"


def test_an_older_library_line_in_the_console_does_not_win_over_the_stop():
    err = ("CUDA out of memory. Tried to allocate 2.00 GiB (falling back to the CPU)\n00:10:00 INFO == S5\n"
           "match_cuts: ERROR: ValueError: the frame map is empty\nRun folder: C:\\x\n")
    assert P.explain(err, code=2).text == ("the run stopped on a problem the app does not know yet: the frame map is "
                                           "empty (ValueError)")
    ff = ("match_cuts: ERROR: RuntimeError: conform raw: ffmpeg failed (1): ffmpeg -i C:\\x\\raw.mp4 C:\\w\\o.mp4\n"
          "[mov @ 01] moov atom not found\nC:\\x\\raw.mp4: Invalid data found when processing input\n\n"
          "match_cuts: details in C:\\x\\match_cuts.log\n")
    r = P.explain(ff, code=2)                                 # FFmpeg's own words under its command line count
    assert r.kind == P.BROKEN_VIDEO and r.text == ("the video raw.mp4 is damaged or not complete, so FFmpeg could not "
                                                   "read it while converting the RAW")


def test_the_words_have_no_technical_brackets_and_ffmpeg_is_spelled_right():
    r = P.explain("match_cuts: ERROR: RuntimeError: conform raw: ffmpeg failed (4294967284): ffmpeg -i C:\\a.mp4 "
                  "C:\\w\\b.mp4\n", code=2)
    assert r.text == "the computer ran out of memory (RAM) while converting the RAW"
    assert "ENOMEM" not in r.line() and "ENOMEM" in r.detail
    r = P.explain("match_cuts: ERROR: RuntimeError: ffprobe failed (3199971767): ffprobe C:\\in\\clip.mp4\n", code=2)
    assert r.text == ("a video file is damaged or not complete, so FFmpeg could not read it while reading a video "
                      "(invalid data in the video)")
    r = P.explain("match_cuts: ERROR: RuntimeError: conform raw: ffmpeg failed (3221225477): ffmpeg -i C:\\a.mp4 "
                  "C:\\w\\b.mp4\n", code=2)
    assert r.kind == P.CRASH and r.text.startswith("FFmpeg crashed (a memory access error") and \
        r.text.endswith("while converting the RAW")
    r = P.explain("match_cuts: ERROR: RuntimeError: conform raw: ffmpeg failed (3165764104): ffmpeg -c:v libx264 "
                  "-i C:\\a.mp4 C:\\w\\b.mp4\n", code=2)
    assert r.kind == P.SETUP and r.text == ("a part of the tool is missing or broken on this computer (an encoder "
                                            "missing in this FFmpeg)")


def test_a_missing_input_file_says_which_and_what_to_do():
    r = P.explain("match_cuts: raw file not found: D:\\in\\Borat.mp4\n", code=2)
    assert r.kind == P.FILE_MISSING and r.text == "the RAW file is not there any more (D:\\in\\Borat.mp4)"
    assert r.todo.startswith("put it back where it was")
    r = P.explain("match_cuts: competitor file not found: C:\\c\\short.mp4\n", code=2)
    assert r.text.startswith("the competitor's file is not there any more")


def test_the_tools_own_stops_stay_as_they_are():
    for stop in ("WRONG PAIR: the competitor's pictures are not in this RAW (0 of 16 frames found)",
                 "PICTURE NOT FOUND (sound found) -- the RAW may be a different cut",
                 "the competitor's scenes are not in this RAW: only 0 % of its frames",
                 "The inputs look wrong, so I stopped before converting anything: What to do: ..."):
        r = P.explain(f"12:00:00 INFO x\nmatch_cuts: {stop}\nRun folder: C:\\x\n", code=2)
        assert r.kind == P.TOOL_STOP and r.text == stop and r.todo == "" and r.line().startswith(stop[:20])


def test_a_stored_technical_failure_becomes_plain_text():
    old = f"the run stopped (exit 2): ERROR: RuntimeError: conform raw: ffmpeg failed (4294967268): {FFMPEG_CMD}"
    assert P.plain_text(old).startswith("The disk C: ran out of space: the run stopped while converting the RAW. "
                                        "Free space")
    old_pool = (f"the run stopped (exit 2): ERROR: FileNotFoundError: [Errno 2] No such file or directory: "
                f"{POOL_FILE} match_cuts: details in {RUN}\\edit\\extras\\match_cuts.log")
    assert "temp folder" in P.plain_text(old_pool) and "details in" not in P.plain_text(old_pool)
    only = f"the run stopped (exit 2): details in {RUN}\\edit-2\\extras\\match_cuts.log"
    assert P.plain_text(only) == "The run stopped; the reason is in its log. Press Try again; the full log: Open log."
    r = P.explain(r"match_cuts: ERROR: RuntimeError: ffprobe failed (3199971767): ffprobe C:\in\clip.mp4" "\n", code=2)
    assert ") Download" not in r.line() and "(invalid data in the video). Download" in r.line()   # one stop each
    for plain in ("every check passed", "the disk is full: free some space and press Try again",
                  "the run stopped (exit 2): PICTURE NOT FOUND (sound found) -- the RAW may be a different cut"):
        assert P.plain_text(plain) == plain


def test_the_batch_summary_says_the_plain_reason(tmp_path, monkeypatch):
    """batch.main's row for a stopped run: its failure is the plain line (the stop message kept for WRONG PAIR)."""
    import subprocess as sp
    import sys
    folder = tmp_path / "videos" / "borat"
    folder.mkdir(parents=True)
    (folder / "raw.mp4").write_bytes(b"r")
    (folder / "comp.mp4").write_bytes(b"c")

    class FakePopen:
        def __init__(self, cmd, stdout=None, stderr=None, **_kw):
            self.returncode, self.stderr = 2, __import__("io").StringIO(BORAT_FFMPEG)

        def wait(self, timeout=None):
            return 2
    monkeypatch.setattr(batch, "input_problems", lambda jobs, allow=(): {})
    monkeypatch.setattr(batch, "estimate_s", lambda c, r, f: 0.0)
    monkeypatch.setattr(sp, "Popen", FakePopen)
    out = tmp_path / "out"
    assert batch.main([str(tmp_path / "videos"), "--out", str(out), "--work", str(tmp_path / "w"),
                       "--python", sys.executable]) == 1
    md = (out / "batch_summary.md").read_text(encoding="utf-8")
    import re
    assert re.search(r"FAILED: the run stopped \(exit 2\): The disk C: ran out of space(?: \(only [\d.]+ GB free "
                     r"now\))?: the run stopped while converting the RAW\. Free space on C: \(Board → Clean up\), "
                     r"then press Try again\.", md), md
    assert "details in" not in md
