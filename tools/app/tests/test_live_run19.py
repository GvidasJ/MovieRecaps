"""Run 19, task 3 -- the live animations, driven by the run's real numbers (core.live: its log, its decision log, its
cut list -- never a cache, never the tool's code): the coverage strip fills as the search finds frames, the line
sweeps the competitor's sound, the dots pop when a step finishes, the status types itself in, the block bar fills
smoothly and never goes back, the counters roll, and at the end ✓ PASS, the finish receipt, OPEN IN PREMIERE and
DONE n. Settings' "Animations" off: every change at once.

Run: python -m pytest tools/app/tests/test_live_run19.py"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import live as L  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")
from test_board_ui import pump_until, qapp  # noqa: E402,F401
from test_look_run18 import mono  # noqa: E402,F401 - the Mono + red look for one test


def _run(tmp_path: Path) -> SimpleNamespace:
    run, work = tmp_path / "run", tmp_path / "work"
    (run / "extras").mkdir(parents=True)
    work.mkdir()
    return SimpleNamespace(run=run, work=work, log=run / "extras" / "match_cuts.log", dec=work / "decisions.jsonl")


def _say(f: Path, *lines: str) -> None:
    with open(f, "a", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(ln + "\n")


def _anchor(frame: int, raw: int, zncc: float = 0.98) -> str:
    return json.dumps({"comp_frame": frame, "decision": "anchor", "stage": "visual_match",
                       "evidence": [{"raw": raw, "zncc": zncc, "inliers": 100}], "rejected": []})


T = "2026-10-26 09:00:00,000 INFO match_cuts "


def test_the_live_numbers_come_from_the_runs_own_files(tmp_path):
    r = _run(tmp_path)
    lv = L.Live(r.run, r.work, None)
    _say(r.dec, _anchor(0, 999))                              # (the run before's record: never counted)
    import os
    os.utime(r.dec, (time.time() - 60, time.time() - 60))
    assert not lv.poll() or lv.snapshot()["found"] == 0
    _say(r.log, T + "== S0 env", T + "probe c.mp4 [competitor]: h264 1080x1920 @ 30/1 fps (CFR), 300 frames, audio",
         T + "probe r.mp4 [raw]: h264 1920x1080 @ 24000/1001 fps (CFR), 9000 frames, audio", T + "   S0 env: 0.10s",
         T + "== S5.2 visual search",
         T + "S5.2 visual search: search 1/2: 50/100 tasks done (30 s) | S5.2 visual search: 25 % -- about 2 min left")
    r.dec.write_text("", encoding="utf-8")                    # the run's own decision log, made anew at its start
    _say(r.dec, *[_anchor(f, 2000 + int(f * 0.8)) for f in range(0, 150, 3)])           # one piece of the RAW ...
    _say(r.dec, _anchor(180, 6000, 0.80), _anchor(183, 6002, 0.80))                     # ... then another, unsure
    assert lv.poll()
    s = lv.snapshot()
    assert s["stage"] == "S5.2 visual search" and s["searching"] and s["scan"] == pytest.approx(0.5)
    assert s["duration"] == pytest.approx(10.0) and s["stages_done"] == ["S0 env"]
    assert s["found"] == 52 and s["shots"] is None and s["frames_found"] == (52, None)    # (no edit yet: frames)
    kinds = [k for _a, _b, k in s["coverage"]]
    assert kinds == ["raw", "unsure"] and s["coverage"][0][0] == 0.0 and s["coverage"][0][1] == pytest.approx(5.0)
    _say(r.log, T + "sparse search: 52 anchors on 52/100 searched frames", T + "   S5.2 visual search: 60.00s",
         T + "== S5.3 refine")
    _say(r.dec, json.dumps({"decision": "track", "stage": "refine", "anchors": [[0, 2000], [147, 2117]]}))
    assert lv.poll()
    s = lv.snapshot()
    assert s["scan"] is None and s["placing"] and s["placed"] == 147 and s["frames_found"] == (52, 100)
    _say(r.log, T + "== S8 captions", T + "captions: 12 captions (competitor) -> x.srt; 1 one-word",
         T + "1_edit.xml: 5 clips on a 23.976 fps sequence (the RAW's rate) -- 9.9 s; 12 captions timed to it; ok")
    (r.run / "extras" / "cutlist.json").write_text(json.dumps({"competitor": {"fps": "30/1", "frames": 300},
        "segments": [{"type": "raw", "comp_in": 0, "comp_out": 150}, {"type": "not_in_raw", "comp_in": 150,
                      "comp_out": 180}, {"type": "uncertain", "comp_in": 180, "comp_out": 300}]}), encoding="utf-8")
    (r.run / "extras" / "report.md").write_text("| `XML FLASH` | no piece of a RAW shot under 0.25 s at a cut | OK |",
                                                encoding="utf-8")
    lv.finish({"label": "PASS", "seconds": 125})
    s = lv.snapshot()
    assert s["captions"] == 12 and s["shots"] == (5, 6)       # the edit's 5 clips, 1 stretch not in the RAW
    assert [k for _a, _b, k in s["coverage"]] == ["raw", "not", "unsure"]
    assert s["final"] == {"clips": 5, "captions": 12, "label": "PASS", "seconds": 125, "raw_pct": 90, "flashes": 0}


def test_the_competitor_is_read_from_a_cached_probe_too(tmp_path):
    r = _run(tmp_path)
    lv = L.Live(r.run, r.work, None)
    rec = {"decision": "input", "fps": "30000/1001", "frames": 1268, "path": "c.mp4", "role": "competitor"}
    _say(r.log, T + "== S0 env", T.replace("INFO", "DEBUG") + "probe: input " + json.dumps(rec),
         T.replace("INFO", "DEBUG") + "probe: input " + json.dumps(dict(rec, fps="24000/1001", role="raw")))
    lv.poll()
    s = lv.snapshot()
    assert s["frames"] == 1268 and s["fps"] == pytest.approx(29.97, abs=0.01) and lv.raw_fps == pytest.approx(23.976, abs=0.01)


def test_a_competitor_wave_and_a_timecode(tmp_path):
    import subprocess
    from match_cuts.common import ffmpeg_bin
    clip = tmp_path / "c.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000",
                    "-af", "volume='if(lt(t,1),0.1,1)':eval=frame", "-t", "2", str(clip)], check=True)
    w = L.wave_of(clip, bins=20)
    assert len(w) == 20 and max(w) == 1.0 and w[0] < 0.3 and w[-1] > 0.9
    assert L.timecode(0, 30) == "00:00:00:00" and L.timecode(421, 30) == "00:00:14:01"
    assert L.timecode(1569, 60) == "00:00:26:09" and L.timecode(10, 0) == "--:--:--:--"


@pytest.fixture
def motion_on():
    from mr_app.ui import motion as Mo
    was = Mo.enabled()
    Mo.set_enabled(True)
    yield Mo
    Mo.set_enabled(was)


def test_the_bar_fills_smoothly_and_never_goes_back(qapp, motion_on):
    from mr_app.ui import mono_parts as MP
    b = MP.BlockBar()
    b.set(0.4, MP.RUN)
    assert b.shown < 0.4                                       # it fills, it does not jump
    assert pump_until(qapp, lambda: abs(b.shown - 0.4) < 1e-6, 3.0)
    b.set(0.3, MP.RUN)                                         # a smaller share while running: kept
    assert b.share == 0.4 and b.text().startswith("■■■■□□□□□□ 40%")
    motion_on.set_enabled(False)
    b.set(0.7, MP.RUN)
    assert b.shown == 0.7                                      # Animations off: at once


def test_a_dot_pops_when_its_step_finishes(qapp, motion_on):
    from mr_app.ui import mono_parts as MP
    d = MP.PipelineDots([("Download", MP.DONE), ("Edit", MP.RUN)])
    d.set([("Download", MP.DONE), ("Edit", MP.DONE)])
    assert pump_until(qapp, lambda: d.scale(1) > 1.2, 1.0)     # the tick
    assert pump_until(qapp, lambda: d.scale(1) == 1.0, 2.0) and d.scale(0) == 1.0
    motion_on.set_enabled(False)
    d.set([("Download", MP.DONE), ("Edit", MP.RUN)])
    d.set([("Download", MP.DONE), ("Edit", MP.DONE)])
    assert d.scale(1) == 1.0


def test_the_status_types_itself_in(qapp, motion_on):
    from PySide6.QtWidgets import QLabel
    from mr_app.ui import mono_parts as MP
    lb = QLabel()
    t = MP.Typer(lb)
    t.set("Finding each competitor shot in the RAW", " · 40 %")
    assert t.typing() and len(lb.text()) < 20
    assert pump_until(qapp, lambda: not t.typing(), 2.0)
    assert lb.text() == "Finding each competitor shot in the RAW · 40 %"
    t.set("Finding each competitor shot in the RAW", " · 45 %")      # the same step: only its % changes
    assert lb.text().endswith("45 %") and not t.typing()
    motion_on.set_enabled(False)
    t.set("Placing every frame exactly", "")
    assert lb.text() == "Placing every frame exactly"


def test_counters_roll_and_the_receipt_and_done_count(qapp, motion_on):
    from mr_app.ui import mono_parts as MP
    c = MP.RollCounters()
    c.set(None, None, (147, 30.0), (52, None))                 # searching: the frames found so far
    assert c.cells() == [("FRAMES FOUND", "52"), ("CAPTIONS", "-"), ("FRAME", "00:00:04:27")]
    c.set(None, None, (147, 30.0), (52, 100))
    assert pump_until(qapp, lambda: c.cells()[0] == ("FRAMES FOUND", "52 / 100"), 2.0)
    c.set((5, 6), 12, (299, 30.0))
    assert pump_until(qapp, lambda: c.cells() == [("SHOTS FOUND", "5 / 6"), ("CAPTIONS", "12"),
                                                  ("FRAME", "00:00:09:29")], 2.0)
    r = MP.Receipt()
    r.set({"label": "PASS", "clips": 5, "captions": 12, "flashes": 0, "raw_pct": 90, "seconds": 125})
    assert r.text() == "✓ PASS · 5 clips · 12 captions · 0 flashes · 90 % found in the RAW · 2 min 05 s  ready for premiere ›"
    seen = []
    r.premiere.connect(lambda: seen.append(1))
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    r.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                                  Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert seen == [1]
    n = MP.DoneCount()
    n.set(2)
    n.set(3)
    assert pump_until(qapp, lambda: n.text() == "DONE 3", 2.0)


def test_the_running_row_shows_the_live_parts_then_the_finish(qapp, mono, tmp_path, monkeypatch, motion_on):
    from mr_app.core import jobs as J
    from mr_app.tools import match_cuts_tool as M
    monkeypatch.setattr(M, "seconds_of", lambda p: 12.0)
    ctx = SimpleNamespace(notify=lambda t, x: None, toast=lambda text, kind="ok": None, selected=[],
                          set_strip_queue=lambda n, running: None)
    q = M.MatchCutsPage(ctx)
    try:
        for f in ("c.mp4", "r.mp4"):
            (tmp_path / f).write_bytes(b"x")
        job = J.Job("x", tmp_path / "c.mp4", tmp_path / "r.mp4", channel="Flick707", title="t")
        q._add_row(job)                                                        # noqa: SLF001
        row = q.rows[job.id]
        job.state, job.percent, job.progress = J.RUNNING, 0.3, "Finding each competitor shot in the RAW · 30 %"
        job.live = {"stage": "S5.2 visual search", "duration": 10.0, "fps": 30.0, "wave": [0.2, 0.9, 0.5] * 20,
                    "scan": 0.4, "coverage": [(0.0, 4.0, "raw")], "shots": None, "frames_found": (40, None),
                    "captions": None, "placed": 0, "final": None}
        row.refresh()
        assert row.counters.isVisibleTo(row) and row.wave.isVisibleTo(row) and row.cov.isVisibleTo(row)
        assert row.counters.cells()[0] == ("FRAMES FOUND", "40") and row.wave.scan == pytest.approx(0.4)
        assert row.cov.live and not row.receipt.isVisibleTo(row)
        assert pump_until(qapp, lambda: "Finding each competitor shot" in row.step.text(), 2.0)
        run = tmp_path / "run"
        (run / "extras").mkdir(parents=True)
        (run / "1_edit.xml").write_text("<xmeml/>", encoding="utf-8")
        job.state, job.run_dir, job.result = J.DONE, run, {"label": "PASS", "seconds": 125}
        job.live = dict(job.live, scan=None, coverage=[(0.0, 5.0, "raw"), (5.0, 6.0, "not"), (6.0, 10.0, "raw")],
                        final={"label": "PASS", "clips": 5, "captions": 12, "flashes": 0, "raw_pct": 90,
                               "seconds": 125})
        row.refresh()
        assert not row.counters.isVisibleTo(row) and not row.wave.isVisibleTo(row)
        assert row.receipt.isVisibleTo(row) and row.receipt.text().startswith("✓ PASS · 5 clips")
        assert row.b_prem.text() == "OPEN IN PREMIERE" and row.b_prem.objectName() == "Primary"
        assert "✓ PASS" in row.step.text()
        assert [k for _a, _b, k in row.cov.spans] == ["raw", "not", "raw"] and not row.cov.live
        row.resize(1000, row.sizeHint().height())
        row.grab()
    finally:
        getattr(q, "poll", SimpleNamespace(stop=lambda: None)).stop()
        q.deleteLater()
        qapp.processEvents()
