"""Night 15, task 4 -- honest progress: the time left as a smoothed range, the % never backwards, "seems stuck"."""
from __future__ import annotations

from mr_app.core import progress as P


def test_the_range_reads_like_about_25_35_min():
    assert P.range_text(30 * 60) == "about 25-35 min left"
    assert P.range_text(4 * 60) == "about 3-5 min left"
    assert P.range_text(70) == "about 1 min left"
    assert P.range_text(20) == "under a minute left"
    assert P.range_text(2 * 3600) == "about 1 h 40 min - 2 h 20 min left"


def test_the_whole_run_time_moves_at_most_20_percent_per_new_estimate():
    h = P.Honest()
    a = h.update(0, 30 * 60)
    assert a["left_s"] == 30 * 60
    b = h.update(10, 5 * 60)                       # the tool jumps to 5 min: the run's time falls 20 % only
    assert abs(h.total - 0.8 * 30 * 60) < 1e-6 and b["left_s"] == 0.8 * 30 * 60 - 10
    c = h.update(20, 3 * 3600)                     # and up: 20 % only
    assert abs(h.total - 1.2 * 0.8 * 30 * 60) < 1e-6
    assert c["share"] >= b["share"]


def test_the_share_never_goes_backwards():
    h = P.Honest()
    s = [h.update(t, left)["share"] for t, left in ((0, 100), (50, 50), (60, 400), (61, 900), (62, 4000))]
    assert s == sorted(s) and s[-1] < 1.0


def test_a_countdown_keeps_the_share_and_the_range_steady():
    h = P.Honest()
    out = [h.update(t, 600 - t) for t in range(0, 300, 30)]
    assert all(abs(o["left_s"] - (600 - t)) < 1e-6 for o, t in zip(out, range(0, 300, 30)))


def test_past_the_estimate_it_never_says_0_min_for_long():
    h = P.Honest(estimate_s=60)
    r = h.update(120, None)
    assert r["left_s"] >= 30 and r["share"] < 1.0


def test_no_output_and_no_cpu_for_5_minutes_seems_stuck():
    h = P.Honest()
    assert not h.update(0, 600, sig="a", cpu_s=10.0)["stuck"]
    assert not h.update(200, 600, sig="a", cpu_s=10.2)["stuck"]
    assert h.update(301, 600, sig="a", cpu_s=10.3)["stuck"]
    assert not h.update(302, 600, sig="b", cpu_s=10.3)["stuck"]      # new output: working again


def test_cpu_alone_keeps_it_from_being_stuck():
    h = P.Honest()
    h.update(0, 600, sig="a", cpu_s=0.0)
    for t in range(60, 700, 60):                                      # the log quiet, the CPU busy (a long ffmpeg)
        r = h.update(t, 600, sig="a", cpu_s=float(t))
    assert not r["stuck"]


def test_the_queue_line_says_stuck(monkeypatch):
    assert "Try again" in P.STUCK_TEXT and "5 min" in P.STUCK_TEXT


def test_job_run_activity_reads_sizes_and_cpu(tmp_path):
    import subprocess
    import sys
    from types import SimpleNamespace
    from mr_app.core.run import JobRun
    r = JobRun.__new__(JobRun)
    r.run_dir = tmp_path / "run"
    (r.run_dir / "extras").mkdir(parents=True)
    (r.run_dir / "extras" / "match_cuts.log").write_text("x" * 10, encoding="utf-8")
    r._out_path = tmp_path / "out.txt"
    r.proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        sig, cpu = r.activity()
        assert sig == (10, -1) and (cpu is None or cpu >= 0.0)
    finally:
        r.proc.kill()
        r.proc.wait()
    r.proc = SimpleNamespace(poll=lambda: 0, pid=0)
    assert r.activity()[1] is None


def test_a_run_that_went_on_after_a_pause_does_not_stand_at_99_percent():
    """17 Nov: its saved steps end in no time, so the tool's first estimates are tiny; once the time spent has passed
    that total and the tool says "about 13 min left", the total is that -- the bar goes back from 99 %."""
    h = P.Honest()
    h.update(0, 40)                                # "whole run: under a minute left"
    a = h.update(60, None)                         # past it: the end seems near
    assert a["share"] > 0.6
    b = h.update(90, 13 * 60)                      # the search really starts: 13 minutes left
    assert abs(h.total - (90 + 13 * 60)) < 1e-6 and b["share"] < 0.15 and abs(b["left_s"] - 13 * 60) < 1e-6
    c = h.update(120, 12.5 * 60)
    assert b["share"] <= c["share"] < 0.2
