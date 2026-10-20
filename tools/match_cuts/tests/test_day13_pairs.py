"""Day 13, 1.1: the New video form's WRONG PAIR (Are We There Yet, 2005). The same two files dropped straight into
Match cuts ran; made in Progress -> New video the run "always" stopped with WRONG PAIR. On the PC the New video path
gives the tool the same pair and it passes (the app's test test_day13_newvideo_pair.py) -- but the quick pair check
KEPT its answer per pair (paths, sizes, times): once a project's run was stopped as a WRONG PAIR, every Try again of
that project stopped at once with the same cached answer, while a drop made a new project (new paths) and was checked
fresh. Now a WRONG PAIR is never taken from the cache, and the run's log and the stop message say what each file is
(length, picture, frame rate, codec, size, sound streams), so a stop can be compared with a run that went on.

The test pair (tests/data/are_we_there_yet): two minutes of the movie around the scene (480 px, its 5.1 sound) and the
competitor as the laptop had it (VP9 + Opus in .webm, 360x640 here)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from match_cuts import pair_check as PC

DATA = Path(__file__).resolve().parent / "data" / "are_we_there_yet"
COMP, RAW = DATA / "competitor.webm", DATA / "movie_piece.mp4"


def test_the_real_pair_is_found_by_its_sound_and_by_its_picture(tmp_path):
    res = PC.check(COMP, RAW, tmp_path / "work")
    assert not res.wrong and res.sound.state == PC.FOUND and res.sound.found >= 4, res.summary()
    pic = PC.check(COMP, RAW, None, picture="always").picture
    assert pic is not None and pic.state == PC.FOUND and pic.found >= 8, pic


def test_a_cached_wrong_pair_is_checked_again_and_never_kept(tmp_path):
    work = tmp_path / "work"
    path = PC.cache_path(work, PC.cache_key(COMP, RAW))
    path.parent.mkdir(parents=True, exist_ok=True)
    stale = PC.Result(True, PC.Side(PC.NOT_FOUND, 4, 0), PC.Side(PC.NOT_FOUND, 16, 0), 7.0, False, str(COMP), str(RAW))
    path.write_text(json.dumps(stale.to_dict()), encoding="utf-8")            # the laptop's stop, kept for the pair
    res = PC.check(COMP, RAW, work)
    assert not res.cached and not res.wrong, res.summary()                   # checked again: it goes on
    assert not json.loads(path.read_text(encoding="utf-8"))["wrong"]         # and the good answer replaced it
    assert PC.check(COMP, RAW, work).cached                                  # a pair that goes on: from the cache


def test_a_wrong_pair_is_not_written_to_the_cache(tmp_path):
    other = tmp_path / "other.mp4"                                           # the competitor's own start: not the RAW
    import subprocess
    from match_cuts.common import ffmpeg_bin
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=480x268:rate=24", "-f",
                    "lavfi", "-i", "sine=frequency=330:sample_rate=48000", "-t", "60", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(other)], check=True)
    res = PC.check(COMP, other, tmp_path / "work")
    assert res.wrong, res.summary()
    assert not PC.cache_path(tmp_path / "work", PC.cache_key(COMP, other)).is_file()
    assert not PC.check(COMP, other, tmp_path / "work").cached


def test_the_stop_names_what_each_file_is():
    res = PC.check(COMP, RAW, None)
    c, r = res.inputs["competitor"], res.inputs["raw"]
    assert c["video"] == "vp9" and c["fps"] == "30" and [s["codec"] for s in c["sound"]] == ["opus"]
    assert r["video"] == "h264" and r["fps"] == "23.976" and r["sound"][0]["channels"] == 6
    assert c["size"] == COMP.stat().st_size and abs(r["duration"] - 120.0) < 0.5
    text = PC.facts_text(c)
    assert text.startswith("competitor.webm (0:25, 360x640, 30 fps, vp9, 1.1 MB; sound: opus 2 ch")
    bad = PC.Result(True, PC.Side(PC.NOT_FOUND, 4, 0), PC.Side(PC.NOT_FOUND, 16, 0), 7.0, False, str(COMP), str(RAW),
                    res.inputs)
    msg = bad.message()
    assert msg.startswith("WRONG PAIR: This competitor doesn't seem to come from this RAW (sound: 0 of 4 pieces")
    assert "Competitor: competitor.webm (0:25, 360x640" in msg
    assert "RAW: movie_piece.mp4 (2:00, 480x268, 23.976 fps" in msg
    assert "aac 6 ch" in msg and "--force-inputs" in msg
    assert PC.facts_text(PC.facts(DATA / "not_there.mp4")).startswith("not_there.mp4 (cannot read the file")


def test_the_run_log_says_what_it_was_given(tmp_path, caplog):
    from match_cuts.common import DecisionLog
    ctx = SimpleNamespace(cfg=SimpleNamespace(competitor=str(COMP), raw=str(RAW), work_dir=str(tmp_path),
                                              force_inputs=False),
                          dlog=DecisionLog(tmp_path / "decisions.jsonl", truncate=True))
    with caplog.at_level(logging.INFO, logger="match_cuts"):
        assert PC.stop_if_wrong_pair(ctx) is not None
    lines = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("pair check: the competitor: competitor.webm (0:25, 360x640, 30 fps, vp9") for m in lines)
    assert any(m.startswith("pair check: the RAW: movie_piece.mp4 (2:00, 480x268, 23.976 fps, h264") for m in lines)
    assert any("-> goes on" in m for m in lines)


def test_from_dict_keeps_the_inputs():
    res = PC.check(COMP, RAW, None)
    back = PC.Result.from_dict(json.loads(json.dumps(res.to_dict(), default=float)))
    assert back.inputs["raw"]["name"] == "movie_piece.mp4" and back.cached
