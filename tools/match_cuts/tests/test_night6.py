"""Night 6: a piece on a RAW shot change keeps the competitor's timing (laptop004's XML FLASH), the take limit of 1.5
RAW frames, a stereo copy of a 5.1 RAW for Premiere, the careful pause cuts of the default, the wrong-input check and
the "not in this RAW" stop, the run's time left, the batch's folders (any names, several competitors per RAW) and the
check-all split (new-style cases thorough and scored, old ones --fast with the hard checks)."""
from __future__ import annotations

import json
import logging
import subprocess
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import batch, check_all, eta, inputs_check, stereo, testcases
from match_cuts import export_xml_edl as ex
from match_cuts import silence
from match_cuts.common import ffmpeg_bin
from match_cuts.keep_speed import keep_speed
from match_cuts.model import Segment

from test_keep_speed import PAN, cutlist


# ---------------------------------------------------------------------------------------------------------------------
# 1. a piece starting on a RAW shot change keeps the competitor's timing (laptop004)
# ---------------------------------------------------------------------------------------------------------------------

def _seg(i, a, b, raw, v=1.25, **kw):
    kw.setdefault("transform", dict(PAN))
    return Segment(id=i, type="raw", comp_in=a, comp_out=b, raw_in_seconds=raw, speed=v, speed_measured=v,
                   confidence=.9, **kw)


def _take_with_a_shot_change():
    """S01 + S02 one take at 125 % (60 fps competitor); S03 starts on the RAW's shot change at 102.0 s, 27 ms after
    the take's line reaches it (laptop004's S03: the line 27 ms early -- 2 frames of the shot before at S03's
    framing)."""
    cl = cutlist()
    line = lambda k: 100.0 + 1.25 * k / 60.0                                       # noqa: E731
    cl.segments = [_seg(1, 0, 40, 100.0), _seg(2, 40, 96, line(40) - 0.021),
                   _seg(3, 96, 400, line(96) + 0.027, transform={**PAN, "tx": -700.0}), _seg(4, 400, 700, 300.0)]
    return cl, line(96) + 0.027


def test_a_piece_starting_on_a_raw_shot_change_keeps_its_own_moment_and_the_take_ends_there():
    cl, change = _take_with_a_shot_change()
    out, take, done = ex.join_takes(cl, [change])
    s = {int(x.id): x for x in out.segments}
    assert s[3].raw_in_seconds == pytest.approx(change)                        # the competitor's own moment
    assert take == {2: 1, 3: 1}                                                # still one take (its sound runs on)
    end2 = float(s[2].raw_in_seconds) + float(s[2].speed) * (96 - 40) / 60.0
    assert end2 == pytest.approx(change, abs=1e-9)                             # S02 ends exactly on the change
    assert {d["segment"]: d["how"] for d in done} == {2: "continues", 3: "on a shot change"}
    kc, _ = keep_speed(out)                                                    # at 100 %: no jump at the cut
    kc = ex.snap_takes(kc, take)
    k = {int(x.id): x for x in kc.segments}
    end2 = float(k[2].raw_in_seconds) + (k[3].comp_in - k[2].comp_in) / 60.0
    assert abs(float(k[3].raw_in_seconds) - change) < 0.5 / 60.0 + 1e-9        # within half a frame of the change
    assert float(k[3].raw_in_seconds) == pytest.approx(end2, abs=1e-6)


def test_without_the_shot_change_the_piece_plays_on_the_line_as_before():
    cl, change = _take_with_a_shot_change()
    out, take, done = ex.join_takes(cl, [change + 0.3])                        # a change elsewhere: no effect
    s = {int(x.id): x for x in out.segments}
    assert s[3].raw_in_seconds == pytest.approx(change - 0.027)
    assert {d["how"] for d in done} == {"continues"}


def test_a_clip_played_on_into_the_next_raw_shot_shows_it_a_whole_quarter_second():
    """--remove-silence: a clip ending inside a word that runs on into the RAW's next shot plays on until that shot
    has shown shots.MIN_SHOT_S, on whole frames -- the change lies between two frames (video020 --fast S02+S03: 0.25 s
    from it rounded down to 14 frames at 60 fps, an XML FLASH)."""
    import math
    from match_cuts import speech as SP
    from test_speech import FPS, PA, PB, three
    _, sm = three()                                    # "there": 2.4-3.0 s
    c = 177.4 / 60.0                                   # a RAW shot change between two sequence frames (2.957 s)
    a = SP.Piece("A", 0, 120, 0.9 * 60, 1.0)           # RAW 0.9-2.9: ends inside "there", which runs over the change
    _, inserts, _, _ = SP.snap_edits([a], sm, FPS, PA, PB, shots=[c])
    end = 2.9 * 60 + sum(n for _, n, side, _ in inserts if side == "end")      # the clip's last RAW frame + 1
    assert round(end) - math.ceil(c * 60) >= 15        # frames of the new shot: 0.25 s (it was 14)


def test_a_jump_of_two_raw_frames_is_a_cut_now():
    cl = cutlist()
    line = lambda k: 100.0 + 1.25 * k / 60.0                                       # noqa: E731
    two = 2.0 * 1001 / 24000                                                   # 83 ms at 23.976 fps
    cl.segments = [_seg(1, 0, 120, 100.0), _seg(2, 120, 400, line(120) + two), _seg(3, 400, 700, 300.0)]
    _, take, _ = ex.join_takes(cl)
    assert take == {}                                                          # video3's 85 ms: the competitor's cut
    cl.segments[1] = _seg(2, 120, 400, line(120) + 0.04)                       # one RAW frame: the same take
    _, take, _ = ex.join_takes(cl)
    assert take == {2: 1}


# ---------------------------------------------------------------------------------------------------------------------
# 3. a stereo copy of a 5.1 RAW
# ---------------------------------------------------------------------------------------------------------------------

def test_the_stereo_mix_takes_the_front_and_the_centre_and_leaves_out_the_surround():
    assert stereo.pan_filter(6, "5.1") == "pan=stereo|c0=FL+0.7071*FC|c1=FR+0.7071*FC"
    assert stereo.pan_filter(6, "5.1(side)") == "pan=stereo|c0=FL+0.7071*FC|c1=FR+0.7071*FC"
    assert stereo.pan_filter(8, "7.1") == "pan=stereo|c0=FL+0.7071*FC|c1=FR+0.7071*FC"
    assert stereo.pan_filter(6, "") == "pan=stereo|c0=c0+0.7071*c2|c1=c1+0.7071*c2"      # unnamed: FL FR FC ...
    assert stereo.pan_filter(4, "quad") == "pan=stereo|c0=FL|c1=FR"


def test_a_stereo_copy_of_a_six_channel_video_keeps_every_frame(tmp_path):
    src = tmp_path / "raw.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24000/1001",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-filter_complex",
                    "[1:a]pan=5.1|FL=c0|FR=c0|FC=c0|LFE=c0|BL=c0|BR=c0[a]", "-map", "0:v", "-map", "[a]", "-t", "2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    assert stereo.audio_layout(src)["channels"] == 6
    res = stereo.stereo_copy(src, tmp_path / "raw_stereo.mp4")
    assert stereo.audio_layout(res["path"])["channels"] == 2
    assert stereo._frames(res["path"])[0] == stereo._frames(src)[0]
    assert res["peak_db"] is not None and res["gain_db"] <= 0.0
    blk = stereo.stereo_block({"file": "media/raw_ae.mp4", "frames": 48, "audio_channels": 6}, res["path"], tmp_path)
    assert blk["audio_channels"] == 2 and blk["file_rel"] == "raw_stereo.mp4" and blk["frames"] == 48


# ---------------------------------------------------------------------------------------------------------------------
# 4. the careful pause cuts of the default
# ---------------------------------------------------------------------------------------------------------------------

def _pauses(quiet, points=(), n=600, fps=60):
    y = np.zeros(48000 * n // fps, np.float32)
    st = silence.Settings()
    cuts, lv = silence.pause_ranges(y, 48000, Fraction(fps), n, st, points, quiet=quiet,
                                    lv={"speech_db": -20.0, "noise_db": -60.0, "threshold_db": -45.0, "how": "test"})
    return [(c.a, c.b) for c in cuts], lv


def test_a_real_pause_inside_a_take_is_cut_with_a_gap_kept_before_and_after_the_words():
    cuts, lv = _pauses([(2.0, 2.4)])
    assert cuts == [(123, 142)]                  # 2.05 s (0.05 after the word) .. 2.37 s (0.03 before the next)
    assert lv["pauses"]["cut"] == 1


def test_a_short_pause_a_few_frames_a_cut_point_the_start_and_the_ending_stay():
    assert _pauses([(2.0, 2.14)])[0] == []       # under 0.15 s: not a real pause
    assert _pauses([(2.0, 2.17)])[0] == []       # 0.17 s: only 0.09 s would go -- a few frames, no cut
    assert _pauses([(2.0, 2.4)], points=[130])[0] == []        # the competitor cuts in this pause: it stays
    assert _pauses([(2.0, 2.4)], points=[120])[0] == []        # ... or right at its edge
    assert _pauses([(0.0, 0.5)])[0] == []        # the edit's start
    assert _pauses([(9.5, 10.0)])[0] == []       # the ending (XML ENDING)


def test_the_default_cuts_pauses_and_the_options_turn_it_off_or_back_to_the_full_removal():
    from match_cuts.config import Config, cuts_pauses, removes_silence
    c = Config(premiere=True, competitor="c.mp4")
    assert cuts_pauses(c) and not removes_silence(c)
    assert not cuts_pauses(Config(premiere=True, competitor="c.mp4", keep_silence=True))
    r = Config(premiere=True, competitor="c.mp4", remove_silence=True)
    assert removes_silence(r) and not cuts_pauses(r)


# ---------------------------------------------------------------------------------------------------------------------
# 6c. wrong inputs
# ---------------------------------------------------------------------------------------------------------------------

def _p(path, w, h, d):
    return {"path": str(path), "width": w, "height": h, "duration": d}


def test_the_wrong_input_check_names_a_movie_given_as_the_competitor(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"a" * 10)
    b.write_bytes(b"b" * 20)
    assert inputs_check.problems(_p(a, 1080, 1920, 45.0), _p(b, 1920, 1080, 1300.0)) == []
    movie = inputs_check.problems(_p(a, 1920, 1080, 6540.0), _p(b, 1920, 1080, 6560.0))
    assert any("much longer than a Short" in x for x in movie) and any("almost as long" in x for x in movie)
    assert any("longer than the RAW" in x for x in inputs_check.problems(_p(a, 1080, 1920, 120.0),
                                                                       _p(b, 1920, 1080, 60.0)))
    assert any("swapped" in x for x in inputs_check.problems(_p(a, 1920, 1080, 1300.0), _p(b, 1080, 1920, 45.0)))
    assert any("same video" in x for x in inputs_check.problems(_p(a, 1080, 1920, 45.0), _p(a, 1080, 1920, 45.0)))
    msg = inputs_check.message(_p(a, 1920, 1080, 6540.0), _p(b, 1920, 1080, 6560.0), movie)
    assert "--force-inputs" in msg and "1:49:00" in msg


def test_every_test_case_passes_the_wrong_input_check():
    from match_cuts.cli import quick_probe
    for c in testcases.cases():
        mine = "--allow-short-raw" in [str(o) for o in c.options]    # day 13: Liquid Death, your pick of the RAW
        assert inputs_check.problems(quick_probe(c.competitor), quick_probe(c.raw), mine) == [], c.name


def test_almost_nothing_found_in_the_raw_stops_the_run():
    from match_cuts import pipeline
    from match_cuts.model import FrameMap, Status
    fm = FrameMap(100)
    fm.status = np.full(100, Status.NONE)
    hints = SimpleNamespace(comp_t=np.arange(40.0), confident=lambda c: np.zeros(40, bool))
    ctx = SimpleNamespace(cfg=SimpleNamespace(force_inputs=False, audio_min_conf=1.3, competitor="c.mp4",
                                              raw="r.mp4"), fm_pre=fm, hints=hints,
                          dlog=SimpleNamespace(record=lambda *a, **k: None))
    with pytest.raises(pipeline.NotInRaw, match="not in this RAW"):
        pipeline.stop_if_not_in_raw(ctx)
    ctx.cfg.force_inputs = True
    pipeline.stop_if_not_in_raw(ctx)                                             # --force-inputs goes on
    ctx.cfg.force_inputs = False
    ctx.hints = SimpleNamespace(comp_t=np.arange(40.0), confident=lambda c: np.arange(40) < 20)
    pipeline.stop_if_not_in_raw(ctx)                                             # its sound is there: a version issue
    st = np.full(100, Status.NONE)
    st[:30] = Status.MATCH
    fm.status = st
    ctx.hints = hints
    pipeline.stop_if_not_in_raw(ctx)                                             # 30 % of its frames: goes on


# ---------------------------------------------------------------------------------------------------------------------
# 6d. time left
# ---------------------------------------------------------------------------------------------------------------------

def test_the_run_clock_adds_the_time_left_to_the_progress_lines():
    lg = logging.getLogger("match_cuts.test_night6")
    lg.setLevel(logging.INFO)
    seen = []
    h = logging.Handler()
    h.emit = lambda r: seen.append(r.getMessage())
    lg.addHandler(h)
    past = [{"machine": "GPU X", "fast": True, "comp_s": 20.0, "raw_s": 1000.0, "cached": [],
             "stages": {"S2 probe+conform": 20.0, "S5.2 visual search": 40.0, "S8 captions": 30.0}}]
    clock = eta.RunClock(20.0, 1000.0, True, mach="GPU X", past=past, every_s=0.0)
    lg.addFilter(clock)
    try:
        assert clock.pred["S5.2 visual search"] == pytest.approx(40.0)          # from the earlier run on this machine
        lg.info("== S5.2 visual search")
        lg.info("S5.2 visual search: search 2/2: 50/100 tasks done (31 s)")
        assert "S5.2 visual search: 75 %" in seen[-1] and "whole run:" in seen[-1]
        lg.info("   S5.2 visual search: 40.00s")
        assert clock.done == {"S5.2 visual search": 40.0}
    finally:
        lg.removeFilter(clock)
        lg.removeHandler(h)
    assert eta.minutes(30) == "under a minute" and eta.minutes(600) == "about 10 min"
    assert batch.left_s("x | S8 captions: 40 % -- under a minute left; whole run: about 7 min left") == 420.0


# ---------------------------------------------------------------------------------------------------------------------
# 6e. batch folders
# ---------------------------------------------------------------------------------------------------------------------

def _clip(path: Path, seconds: float, w: int = 64, h: int = 36) -> Path:
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:size={w}x{h}:rate=10",
                    "-t", f"{seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_a_batch_folder_with_one_raw_and_several_competitors_of_any_name(tmp_path):
    d = tmp_path / "todo" / "show"
    d.mkdir(parents=True)
    raw = _clip(d / "Episode 12.mkv", 6.0)
    c1, c2 = _clip(d / "short one.mp4", 2.0, 36, 64), _clip(d / "short two.mov", 1.5, 36, 64)
    one = tmp_path / "todo" / "single"
    one.mkdir()
    _clip(one / "competitor.mp4", 1.5, 36, 64)
    _clip(one / "raw.mp4", 0.5)                                    # named raw: the RAW, even shorter (inputs check)
    jobs, skipped = batch.find_videos(tmp_path / "todo")
    assert [(n, c.name, r.name) for n, c, r in jobs] == [("show_short one", "short one.mp4", "Episode 12.mkv"),
                                                        ("show_short two", "short two.mov", "Episode 12.mkv"),
                                                        ("single", "competitor.mp4", "raw.mp4")]
    assert skipped == []
    wrong = batch.input_problems(jobs)
    assert list(wrong) == ["single"] and "longer than the RAW" in wrong["single"][0]


def test_the_batch_finds_a_runs_progress_far_back_in_its_log_and_counts_it_down(tmp_path):
    import datetime as dt
    log = tmp_path / "extras" / "match_cuts.log"
    log.parent.mkdir()
    t0 = dt.datetime(2026, 10, 13, 7, 50, 34)
    line = (f"{t0:%Y-%m-%d %H:%M:%S},694 INFO match_cuts S5.3 refine: final: 539/733 tasks done (30 s) | S5.3 refine: "
            "74 % -- under a minute left; whole run: about 4 min left\n")
    log.write_text(line + "2026-10-13 07:50:35,000 DEBUG match_cuts a frame's scores\n" * 5000, encoding="utf-8")
    assert log.stat().st_size > 65536                          # the demo batch read 64 KB: "running" every time
    now = t0.timestamp()
    assert batch.latest_progress(tmp_path, now + 10) == "S5.3 refine: 74 % -- under a minute left; whole run: about " \
                                                        "4 min left"
    assert batch.latest_progress(tmp_path, now + 130) == "whole run: about 2 min left"        # 240 s - 130 s
    assert batch.left_s(batch.latest_progress(tmp_path, now + 130)) == 120.0
    with log.open("a", encoding="utf-8") as f:                 # the next stage began: it, with the time counted down
        f.write("2026-10-13 07:50:50,000 INFO match_cuts == S8 export\n")
    assert batch.latest_progress(tmp_path, now + 30) == "S8 export -- whole run: about 4 min left"   # 240 - 30 s


def test_a_short_run_with_no_progress_line_shows_its_stage(tmp_path):
    log = tmp_path / "extras" / "match_cuts.log"
    log.parent.mkdir()
    assert batch.latest_progress(tmp_path) == ""
    log.write_text("2026-10-13 09:19:12,264 INFO match_cuts time estimate: the whole run about 6 min\n"
                   "2026-10-13 09:19:12,300 INFO match_cuts == S2 probe+conform\n"
                   "2026-10-13 09:19:14,000 INFO match_cuts    S2 probe+conform: 1.54s\n"
                   "2026-10-13 09:19:14,100 INFO match_cuts == S5.2 visual search\n", encoding="utf-8")
    assert batch.latest_progress(tmp_path) == "S5.2 visual search"      # the demo batch: "running" every time


def test_a_stopped_runs_reason_in_the_batch_summary_is_its_plain_message():
    stopped = ("07:56:35 INFO match_cuts 0.1.0: competitor=clip.mp4 raw=Anchorman.mp4\n07:56:44 INFO found in the RAW: 0 %\n"
               "07:56:44 ERROR STOPPED: almost nothing of the competitor is in this RAW\n"
               "the competitor's scenes are not in this RAW: only 0 % of its frames and 2 % of its sound were found.\n"
               "Run folder: out\\wrong episode\n")
    assert batch.stop_message(stopped) == ("the competitor's scenes are not in this RAW: only 0 % of its frames and 2 % "
                                           "of its sound were found.")
    crash = "07:00:00 INFO start\nTraceback (most recent call last):\n  File \"x.py\", line 1\nValueError: boom\n"
    assert batch.stop_message(crash) == "ValueError: boom"
    assert batch.stop_message("07:00:00 INFO start\n07:00:01 ERROR the RAW has no video stream\n") == \
        "the RAW has no video stream"
    wrong = ("match_cuts: The inputs look wrong, so I stopped before converting anything:\n"
             "  - the competitor is 1:49:00 long\nWhat to do: give the Short as the competitor.\n")
    assert batch.stop_message(wrong).startswith("The inputs look wrong, so I stopped") and "What to do" in \
        batch.stop_message(wrong)
    assert batch.stop_message("") == "see the log"


# ---------------------------------------------------------------------------------------------------------------------
# 7. check-all: new-style cases thorough and scored, old ones --fast with the hard checks
# ---------------------------------------------------------------------------------------------------------------------

def test_check_all_runs_new_style_cases_thorough_and_old_ones_fast_with_the_hard_checks_only():
    new = SimpleNamespace(style="new")
    old = SimpleNamespace(style="old")
    assert check_all.case_args(new) == (["--check-determinism", "--quick-checks", "--skip-compare"], True)
    assert check_all.case_args(old) == (["--fast", "--check-determinism", "--quick-checks", "--skip-compare"], False)
    assert check_all.case_args(old, old_way=True) == (["--compare-fast", "--check-determinism"], True)
    styles = {c.name: c.style for c in testcases.cases()}
    assert {n for n, s in styles.items() if s == "new"} == {"mike005-fixed", "laptop009-fixed", "laptop010-fixed",
                                                           "laptop013-fixed", "video021-fixed", "deadpool", "zendaya",
                                                           "video6-fixed",     # night 7: your video6 fix
                                                           "video7-fixed", "video8-fixed", "video9-fixed",   # night 8
                                                           "video10-fixed",                                  # night 9
                                                           "video11-fixed", "video12-fixed", "chocolate-fixed",  # 11
                                                           "video14-fixed", "video15-fixed",  # day 12
                                                           "video16-fixed", "video17-fixed", "video18-fixed",
                                                           "video19-fixed", "video20-fixed",
                                                           "liquid-death-fixed",  # day 13
                                                           "video21-fixed", "video22-fixed", "video23-fixed",
                                                           "video24-fixed", "video25-fixed", "video26-fixed",
                                                           "video27-fixed",  # night 15 (the learn queue)
                                                           "video28-fixed", "video29-fixed", "video30-fixed",
                                                           "video31-fixed", "video32-fixed",  # day 16
                                                           "video33-fixed", "video34-fixed", "video35-fixed",
                                                           "video36-fixed", "video37-fixed", "video38-fixed",  # day 17
                                                           "mik1-fixed", "video39-fixed", "flkoick1-fixed",
                                                           "video40-fixed", "video41-fixed", "video42-fixed",
                                                           "video43-fixed",  # run 18 (the learn queue)
                                                           "video44-fixed", "video45-fixed", "video46-fixed",
                                                           "e1-fixed", "sc22-fixed"}  # run 19 (the learn queue)
    card = check_all.scorecard([{"case": "video1", "seconds": 60.0, "deliverables": {"status": "pass"},
                                 "determinism": {"status": "pass"}, "coverage": {"status": "pass"},
                                 "scored": False, "mode": "fast"}])
    assert "hard checks only" in card and "PASS" in card


# ---------------------------------------------------------------------------------------------------------------------
# 6. captions: stammers left out, grouped by what is said
# ---------------------------------------------------------------------------------------------------------------------

def _words(text: str, step: float = 0.2):
    from match_cuts.captions import Word
    return [Word(t.lower().strip(",.?!"), step * i, step * i + 0.15, 1.0, t) for i, t in enumerate(text.split())]


@pytest.mark.parametrize("said, written", [
    ("from the the captain on the on the subject", "from the captain on the subject"),     # video2
    ("in here? Yeah, yeah, success.", "in here? Yeah, success."),                         # mike005
    ("thank God. And and then I", "thank God. And then I"),                               # video017
    ("Harley rider. This this is making", "Harley rider. This this is making"),           # laptop009: you keep it
    ("still urinating. It's a lot. It's a lot of pig", "still urinating. It's a lot. It's a lot of pig"),   # 021
    ("know. I'm very, very lonely!", "know. I'm very, very lonely!"),                     # video3: emphasis
    ("nah nah nah okay", "nah nah nah okay"),                                             # three times: deliberate
])
def test_a_false_start_said_again_is_left_out_of_the_captions(said, written):
    from match_cuts.captions import drop_stammers
    out, rows = drop_stammers(_words(said))
    assert " ".join(w.raw for w in out) == written
    assert bool(rows) == (said != written)


def test_a_slow_repeat_stays():
    from match_cuts.captions import drop_stammers
    ws = _words("and and then", step=0.6)                                   # 0.45 s between the two: deliberate
    assert drop_stammers(ws)[0] == ws


def test_a_one_word_competitor_gives_no_break_hints_and_one_like_yours_is_still_followed():
    from match_cuts import captions, caption_style
    mike005 = caption_style.competitor_style([{"ocr": w} for w in "is there a black person here what".split()])
    assert mike005["mean_words"] < captions.ONE_WORD_MEAN and not mike005["follow"]
    deadpool = caption_style.competitor_style([{"ocr": t} for t in ("I need", "a favor from you", "who are you",
                                                                    "never mind", "get in the car")])
    assert deadpool["follow"]


# ---------------------------------------------------------------------------------------------------------------------
# 6b. the person a too-wide competitor window shows (laptop010 / laptop013)
# ---------------------------------------------------------------------------------------------------------------------

def test_the_fixed_crop_re_centring_is_off_by_default():
    assert ex.CENTRE_FIXED_CROP is False             # laptop013: 1 of your 5 moves reproduced, your wide shot moved


def test_a_narrow_template_box_centres_the_person_the_competitor_window_shows():
    from match_cuts import speakers
    from match_cuts.geometry import Sim
    from match_cuts.model import Box
    W, H = 1920.0, 1040.0
    win = (22.0, 527.0, 1036.0, 1210.0)
    comp = {"scale": 0.693, "rotation_deg": 0.0, "tx": -305.6, "ty": 300.0}       # laptop013: 23-77 % of the width
    seg = Segment(id=1, type="raw", comp_in=0, comp_out=300, raw_in_seconds=10.0, speed=1.0, transform=comp,
                  confidence=.9)
    box = Box(0.0, 300.0, 720.0, 720.0)
    sim = ex._window_map(Sim.from_dict(comp), box, win)
    ev = SimpleNamespace(seg=seg, rec_in=0, rec_out=300, seg_name="S01", dissolve_in=0)
    clip = ex.PremiereClip(seg, ev, 0, 600, 0, 600, 600, 1200, 1.0, True, 0.0, 1.0, True, [(600, sim)], None, [ev])
    seg0 = Segment(id=9, type="raw", comp_in=300, comp_out=600, raw_in_seconds=50.0, speed=1.0, transform=dict(comp),
                   confidence=.9)                                # an earlier shot, the same fixed crop
    ev0 = SimpleNamespace(seg=seg0, rec_in=300, rec_out=600, seg_name="S09", dissolve_in=0)
    other = ex.PremiereClip(seg0, ev0, 600, 1200, 600, 1200, 3000, 3600, 1.0, True, 0.0, 1.0, True, [(3000, sim)],
                            None, [ev0])
    assert not ex._fixed_competitor_crop([clip])                 # (one clip says nothing)
    assert ex._fixed_competitor_crop([clip, other])
    man = (1050.0, 200.0, 1390.0, 640.0)                                          # inside the competitor's window
    faces = speakers.Faces("speaker", True, man, [man], others=[man])
    sp = SimpleNamespace(same_shot=lambda a, b: True, faces=lambda a, b: faces)
    sp.same_shot = lambda a, b: abs(a - b) < 5.0                  # the two clips: two shots
    moved = ex._subject_in_narrow_window([clip, other], sp, (W, H), win, Fraction(60), box, 250.0)
    assert moved == 2
    new = clip.keys[0][1]
    cx = new.s * 0.5 * (man[0] + man[2]) + new.tx
    assert abs(cx - (win[0] + win[2] / 2.0)) < 1.0 or new.tx == pytest.approx(win[0] + win[2] - new.s * W)
    assert new.s == sim.s and "centred on the person" in clip.person_note
    clip.keys, other.keys = [(600, sim)], [(3000, sim)]                          # a face outside its window: no move
    far = (1700.0, 200.0, 1900.0, 640.0)
    sp.faces = lambda a, b: speakers.Faces("speaker", True, far, [far], others=[far])
    assert ex._subject_in_narrow_window([clip, other], sp, (W, H), win, Fraction(60), box, 250.0) == 0
    sp.faces = lambda a, b: faces                                                # a competitor that frames each shot
    other.seg.transform = {**comp, "tx": -200.0}                                 # itself (laptop010): no move
    assert ex._subject_in_narrow_window([clip, other], sp, (W, H), win, Fraction(60), box, 250.0) == 0


def test_one_take_across_the_raws_own_shot_change_is_framed_shot_by_shot():
    # laptop013: the competitor plays one take over several shots of the movie through one fixed crop; your fix frames
    # each shot on its own person (-361 px on the man, +228 px on the woman)
    from match_cuts import speakers
    from match_cuts.geometry import Sim
    from match_cuts.model import Box
    W, H = 1920.0, 1040.0
    win = (22.0, 527.0, 1036.0, 1210.0)
    comp = {"scale": 0.693, "rotation_deg": 0.0, "tx": -305.6, "ty": 300.0}
    box = Box(0.0, 300.0, 720.0, 720.0)
    sim = ex._window_map(Sim.from_dict(comp), box, win)
    segs = [Segment(id=i + 1, type="raw", comp_in=300 * i, comp_out=300 * (i + 1), raw_in_seconds=10.0 + 5 * i,
                    speed=1.0, transform=dict(comp), confidence=.9) for i in range(2)]
    evs = [SimpleNamespace(seg=s, rec_in=s.comp_in, rec_out=s.comp_out, seg_name=f"S0{s.id}", dissolve_in=0)
           for s in segs]
    # one clip: RAW 10-20 s at 60 fps sequence ticks (src 600..1200), a shot change near RAW 15 s
    take = ex.PremiereClip(segs[0], evs[0], 0, 600, 0, 600, 600, 1200, 1.0, True, 0.0, 1.0, True, [(600, sim)], None,
                           list(evs))
    man, woman = (1050.0, 200.0, 1390.0, 640.0), (560.0, 220.0, 820.0, 600.0)
    cut = 360 * 1001 / 24000                                 # the shot change: RAW frame 360 (15.015 s)
    sp = SimpleNamespace(shots=[cut], same_shot=lambda a, b: (a < cut) == (b < cut),
                         faces=lambda a, b: speakers.Faces("speaker", True, man if a < cut else woman,
                                                           [man if a < cut else woman],
                                                           others=[man if a < cut else woman]))
    clips = [take]
    moved = ex._subject_in_narrow_window(clips, sp, (W, H), win, Fraction(60), box, 250.0, "24000/1001", [cut])
    assert moved == 2 and len(clips) == 1                   # the clip keeps the competitor's cut points ...
    assert len(clips[0].shot_framing) == 1                  # ... and carries the second shot's framing
    first, second = ex.split_at_shots(clips, [cut], Fraction(24000, 1001), Fraction(60))[0]   # as the XML splits it
    assert first.rec_end == second.rec_start and first.src_out == second.src_in   # seamless
    centre = win[0] + win[2] / 2.0
    for c, face in ((first, man), (second, woman)):
        s_ = c.keys[0][1]
        assert abs(s_.s * 0.5 * (face[0] + face[2]) + s_.tx - centre) < 1.0
