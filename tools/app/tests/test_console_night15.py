"""Night 15, task 4 (second part): plain stage names everywhere (the queue row, the console, the report; the
S-number small and grey) and the "What it's doing now" console -- the run's log told in plain words with the time,
newest at the bottom, a live line, "Copy log", open/closed remembered."""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from match_cuts import stage_names as SN  # noqa: E402
from mr_app.core import console as C  # noqa: E402
from mr_app.core import progress as P  # noqa: E402

# lines of a real run (American Dad, work\t31\ad\run2, 22 Oct), shortened
LOG = """\
2026-10-22 03:07:20,100 INFO match_cuts time estimate: the whole run about 41 min (NVIDIA GeForce RTX 5080, --fast, competitor 28 s, RAW 22 min; from 86 earlier run(s) on this machine)
2026-10-22 03:07:21,000 INFO match_cuts == S0 env
2026-10-22 03:07:21,200 INFO match_cuts    S0 env: 0.20s
2026-10-22 03:07:21,300 INFO match_cuts == S1 pair check
2026-10-22 03:07:39,000 INFO match_cuts pair check: sound: 6 of 7 pieces found (86 %); picture: not needed (the sound decided) (1.9 s, from the cache) -> goes on
2026-10-22 03:07:39,600 INFO match_cuts    S1 pair check: 18.33s
2026-10-22 03:07:39,700 INFO match_cuts == S2 probe+conform
2026-10-22 03:07:40,000 INFO match_cuts conform raw: American Dad.mp4 -> raw_ae.mp4 (h264 on the graphics card (NVENC), restamp, 31458 frames)
2026-10-22 03:08:09,237 INFO match_cuts S2 probe+conform: still running (33 s) | S2 probe+conform: 57 % -- under a minute left; whole run: about 41 min left
2026-10-22 03:08:39,248 INFO match_cuts S2 probe+conform: still running (1 min 03 s) | S2 probe+conform: 80 % -- under a minute left; whole run: about 40 min left
2026-10-22 03:10:12,000 INFO match_cuts conform raw: raw_ae.mp4 verified (64 samples, min SSIM 0.9974) in 152.4s (284.2 fps encode)
2026-10-22 03:10:20,000 INFO match_cuts    S2 probe+conform: 160.30s
2026-10-22 03:12:00,000 INFO match_cuts == S5.2 visual search
2026-10-22 03:12:54,297 INFO match_cuts S5.2 visual search: search: 66/278 tasks done (51 s) | S5.2 visual search: 24 % -- about 3 min left; whole run: about 30 min left
2026-10-22 03:13:24,308 INFO match_cuts S5.2 visual search: search: 118/278 tasks done (1 min 21 s) | S5.2 visual search: 42 % -- about 2 min left; whole run: about 29 min left
2026-10-22 03:13:30,000 WARNING match_cuts verification: c5_audio: S04: audio lag +48.36 ms
2026-10-22 03:13:31,000 DEBUG match_cuts segment: comp_duplicate_conflict {"decision": 1}
2026-10-22 03:14:10,000 INFO match_cuts sparse search: 431 anchors on 272/278 searched frames
2026-10-22 03:14:11,000 INFO match_cuts    S5.2 visual search: 130.00s
2026-10-22 03:16:30,000 INFO match_cuts found in the RAW: 100 % of the competitor's frames, 94 % of its sound
2026-10-22 03:19:30,000 INFO match_cuts == S9 verify
2026-10-22 03:19:40,000 INFO match_cuts verify c2_cuts: fail (26.1s)
2026-10-22 03:19:41,000 INFO match_cuts verify s9_1_coverage: pass (0.0s)
2026-10-22 03:19:42,000 INFO match_cuts verify s9_2_plan: not_available (0.0s)
"""


def test_every_stage_has_a_plain_name_and_the_s_number_stays_small():
    from match_cuts import eta
    for st in eta.ORDER:
        assert SN.plain(st) != st and not SN.plain(st).startswith("S")
    assert SN.plain("S1 pair check") == "Checking the RAW and the competitor are the same scene"
    assert SN.plain("S2 probe+conform") == "Preparing the RAW (checking / converting)"
    assert SN.plain("S5.2 visual search") == "Finding each competitor shot in the RAW"
    assert SN.plain("S8 exports") == "Making the Premiere files"
    assert SN.plain("S9 verify") == "Double-checking every cut against the competitor"
    assert SN.plain("S5.2 visual search (refined box)").startswith("Finding each competitor shot")
    assert SN.plain("S8 export") == "Making the Premiere files"              # an unknown S8 name: by its number
    assert SN.plain("something else") == "something else"
    assert SN.code_of("S5.4-S6 segments+cutlist") == "S5.4-S6"


def test_the_queue_line_is_plain_with_its_code():
    pr = P.parse("S5.2 visual search: 40 % -- about 2 min left; whole run: about 5 min left")
    assert pr.plain == "Finding each competitor shot in the RAW" and pr.code == "S5.2"
    pr = P.parse("S5.4-S6 segments+cutlist: 42 % -- under a minute left; whole run: about 5 min left")
    assert pr.step == "S5.4-S6 segments+cutlist" and pr.plain == "Deciding the cuts" and pr.step_pct == 42.0


def test_the_console_tells_a_real_run_in_plain_words(tmp_path):
    log = tmp_path / "match_cuts.log"
    log.write_text(LOG, encoding="utf-8")
    now = dt.datetime(2026, 10, 22, 3, 21, 0)
    c = C.Console(log, reading=lambda: (12 * 60.0, 46 * 60.0, 1.8), clock=lambda: now)
    assert c.poll()
    lines = c.lines
    assert lines[0] == "03:07  Expected time: about 41 min (from earlier runs on this PC)"
    assert not any("Getting ready" in ln for ln in lines)                 # a step of a moment: not shown
    assert "03:07  The sound of the competitor was found in the RAW (6 of 7 pieces) -- the same scene, going on" in lines
    assert "03:07  Checking the pair -- done (18 s)" in lines
    assert "03:07  Converting the RAW once (on the graphics card)" in lines
    # the same step's counts: the newer in place of the older
    assert [ln for ln in lines if "Preparing the RAW -- still" in ln] == [
        "03:08  Preparing the RAW -- still working (1 min 03 s), 80 %, under a minute left in this step"]
    assert [ln for ln in lines if "pieces searched" in ln] == [
        "03:13  Finding shots -- 118 of 278 pieces searched, 42 %, about 2 min left in this step"]
    assert "03:16  Found in the RAW: 100 % of the picture, 94 % of the sound" in lines
    assert "03:19  Double-checking cuts -- the cuts: needs a look" in lines
    assert "03:19  Double-checking cuts -- every competitor frame is covered: OK" in lines
    assert not any("c5_audio" in ln or "comp_duplicate" in ln or "not_available" in ln for ln in lines)
    assert c.live == "03:21  now: Double-checking cuts -- 1 min 30 s in this step"      # (no conversion speed here)
    c2 = C.Console(log, reading=lambda: (12 * 60.0, 46 * 60.0, 1.8), clock=lambda: dt.datetime(2026, 10, 22, 3, 9))
    c2.log = tmp_path / "s2.log"
    c2.log.write_text("".join(LOG.splitlines(True)[:9]), encoding="utf-8")
    c2.poll()                                                             # in "Preparing the RAW": its speed
    assert c2.live == ("03:09  now: Preparing the RAW -- converting 1.8x real time, 12 of 46 min done -- "
                       "1 min 21 s in this step")
    assert c.shown()[-1] == c.live and c.text().splitlines()[-1] == c.live
    assert not c.poll()                                                   # nothing new
    with log.open("a", encoding="utf-8") as f:                           # the log grows: only the new part is read
        f.write("2026-10-22 03:21:10,000 INFO match_cuts    S9 verify: 100.00s\n2026-10-22 03:21:1")
    assert c.poll() and c.lines[-1] == "03:21  Double-checking cuts -- done (1 min 40 s)"
    with log.open("a", encoding="utf-8") as f:                           # (the line written in two parts)
        f.write("1,000 INFO match_cuts == S10 report\n")
    c.poll()
    assert c.lines[-1] == "03:21  Writing the report -- started" and "Writing the report" in c.live


def test_the_ffmpeg_reading_of_a_conversion(tmp_path, monkeypatch):
    from mr_app.core import run as R
    big = tmp_path / "raw.mkv"
    with big.open("wb") as f:
        f.truncate(100_000_000)

    class FakeFF:
        def name(self):
            return "ffmpeg.exe"

        def cmdline(self):
            return ["ffmpeg", "-y", "-i", str(big), "out.mp4"]

        def io_counters(self):
            return SimpleNamespace(read_bytes=25_000_000)

        def create_time(self):
            return R.time.time() - 100.0

    psutil = pytest.importorskip("psutil")
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(children=lambda recursive=True: [FakeFF()]))
    jr = R.JobRun.__new__(R.JobRun)
    jr.proc = SimpleNamespace(poll=lambda: None, pid=1)
    jr._lengths = {str(big): 2760.0}
    done, total, speed = jr.reading()
    assert (done, total) == (690.0, 2760.0) and abs(speed - 6.9) < 0.1      # 11.5 of 46 min, 6.9x real time


def test_the_report_names_the_steps_plainly():
    from match_cuts import report
    out = "\n".join(report._environment(SimpleNamespace(env={}, timings={"S9 verify": 12.5})))  # noqa: SLF001
    assert "Double-checking every cut against the competitor | S9 verify | 12.50" in out


def test_the_row_shows_the_console_and_remembers_it_closed(tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.core import jobs as J
    from mr_app.core import settings as S
    from mr_app.tools import match_cuts_tool as M
    from mr_app.ui import motion as Mo
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(tmp_path / "settings.json"))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    toasts = []
    ctx = SimpleNamespace(set_strip_queue=lambda n, running: None, notify=lambda t, x: None,
                          toast=lambda text, kind="ok": toasts.append(text))
    monkeypatch.setattr(M, "seconds_of", lambda p: 12.0)
    page = M.MatchCutsPage(ctx)
    try:
        (tmp_path / "c.mp4").write_bytes(b"x")
        (tmp_path / "r.mp4").write_bytes(b"x")
        j = J.Job("a", tmp_path / "c.mp4", tmp_path / "r.mp4")
        page._added_jobs(J.DropResult([j], None, []))                  # noqa: SLF001
        row = page.rows[j.id]
        assert not row.con_box.isVisibleTo(row)                        # no lines yet: no panel
        j.state, j.progress, j.progress_code = J.RUNNING, "Finding each competitor shot in the RAW · 40 %", "S5.2"
        row.refresh()
        assert row.step.text().startswith("Finding each competitor shot in the RAW · 40 %")
        assert "S5.2</span>" in row.step.text() and "font-size:9pt" in row.step.text()
        page._console(j.id, ["04:51  Finding shots -- 23 of 41 pieces searched", "04:52  now: Finding shots"])  # noqa: SLF001
        assert row.con_box.isVisibleTo(row) and row.con.isVisibleTo(row)
        assert row.con.toPlainText().splitlines()[-1] == "04:52  now: Finding shots"
        assert row.con_toggle.text() == "▾ What it's doing now"
        row._copy_console()                                            # noqa: SLF001
        assert QtWidgets.QApplication.clipboard().text().startswith("04:51  Finding shots") and "Log copied ✓" in toasts
        row._toggle_console()                                          # noqa: SLF001 - closed ...
        assert not row.con.isVisibleTo(row) and row.con_toggle.text() == "▸ What it's doing now"
        assert S.load().console_open is False                          # ... and remembered
        assert not M.JobRow(page, j).con.isVisibleTo(row)              # a new row (the app opened again): closed
    finally:
        Mo.set_enabled(True)
        page.deleteLater()
        app.processEvents()
