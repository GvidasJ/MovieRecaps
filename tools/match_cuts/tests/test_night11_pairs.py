"""Night 11: which video is the RAW -- names win over length (pairing.py, used by the batch's folders, the input
folder of the command line and the app) -- and the quick wrong-pair check before the long run (pair_check.py:
seconds, not minutes; pipeline.WrongPair, exit 2, the batch's WRONG PAIR)."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import batch, cli, pair_check as PC, pairing, pipeline
from match_cuts.common import ffmpeg_bin

REPO = Path(__file__).resolve().parents[3]
REAL = REPO / "tests" / "real"


def _secs(table):
    return lambda p: table.get(Path(p).name)


def _size(table):
    return lambda p: table.get(Path(p).name)


P = Path


# ---------------------------------------------------------------------------------------------------------------------
# 1. names win over length
# ---------------------------------------------------------------------------------------------------------------------

def test_a_video_named_raw_is_the_raw_even_when_it_is_shorter():
    p = pairing.pick_pair([P("x/comp.mp4"), P("x/RAW episode.MKV")], _secs({"comp.mp4": 60.0, "RAW episode.MKV": 5.0}))
    assert p.raw == P("x/RAW episode.MKV") and p.competitors == [P("x/comp.mp4")] and p.rule == "name" and not p.ask


def test_one_comp_video_and_one_other_the_other_is_the_raw_whatever_the_lengths():
    for comp in ("comp.mp4", "Competitor 2.mov", "competitor.mp4"):
        p = pairing.pick_pair([P(comp), P("episode.mp4")], _secs({comp: 30.0, "episode.mp4": 31.0}))
        assert p.raw == P("episode.mp4") and p.competitors == [P(comp)] and p.rule == "name"
    # three: two comp* and one other
    p = pairing.pick_pair([P("comp1.mp4"), P("comp2.mp4"), P("movie.mkv")], _secs({}))
    assert p.raw == P("movie.mkv") and sorted(p.competitors) == [P("comp1.mp4"), P("comp2.mp4")]


def test_raw_next_to_other_raw_named_files_the_exact_name_wins_the_others_are_no_competitors():
    p = pairing.pick_pair([P("raw.mp4"), P("raw_stereo.mp4"), P("clip.mp4")], _secs({}))
    assert p.raw == P("raw.mp4") and p.competitors == [P("clip.mp4")] and "raw_stereo.mp4" in p.why
    p = pairing.pick_pair([P("raw1.mp4"), P("raw2.mp4"), P("clip.mp4")], _secs({}))
    assert p.raw is None and p.ask and set(p.candidates) == {P("raw1.mp4"), P("raw2.mp4")}


def test_the_names_dont_say_the_longer_one_when_more_than_20_percent_apart():
    p = pairing.pick_pair([P("a.mp4"), P("b.mp4")], _secs({"a.mp4": 79.9, "b.mp4": 100.0}))
    assert p.raw == P("b.mp4") and p.competitors == [P("a.mp4")] and p.rule == "length"
    assert p.why == "the longer video is the RAW: b.mp4 (1:40) against a.mp4 (1:20)"
    # exactly 80 %: about as long -- not decided by the lengths
    p = pairing.pick_pair([P("a.mp4"), P("b.mp4")], _secs({"a.mp4": 80.0, "b.mp4": 100.0}))
    assert p.raw is None and p.ask


def test_about_as_long_a_portrait_one_next_to_a_landscape_one_is_the_competitor():
    p = pairing.pick_pair([P("tall.mp4"), P("wide.mp4")], _secs({"tall.mp4": 133.0, "wide.mp4": 161.0}),
                          _size({"tall.mp4": (1080, 1920), "wide.mp4": (1920, 1080)}))
    assert p.raw == P("wide.mp4") and p.competitors == [P("tall.mp4")] and p.rule == "portrait"
    assert "the portrait one (tall.mp4) is the competitor" in p.why
    # the portrait one longer: still the competitor
    p = pairing.pick_pair([P("tall.mp4"), P("wide.mp4")], _secs({"tall.mp4": 161.0, "wide.mp4": 133.0}),
                          _size({"tall.mp4": (1080, 1920), "wide.mp4": (1920, 1080)}))
    assert p.raw == P("wide.mp4")


def test_still_undecided_asks_with_the_two_candidates_and_says_what_to_do():
    p = pairing.pick_pair([P("b.mp4"), P("a.mp4")], _secs({"a.mp4": 133.0, "b.mp4": 161.0}),
                          _size({"a.mp4": (1920, 1080), "b.mp4": (1920, 1080)}))
    assert p.raw is None and p.ask and p.candidates == [P("a.mp4"), P("b.mp4")] and p.rule == ""
    assert p.why == ("can't tell which one is the RAW: a.mp4 (2:13) and b.mp4 (2:41) are about as long and the names "
                     "don't say -- name the RAW raw.mp4")


def test_unreadable_lengths_the_names_still_decide_else_it_says_so():
    p = pairing.pick_pair([P("competitor.mp4"), P("x.mp4")], _secs({}))
    assert p.raw == P("x.mp4")
    p = pairing.pick_pair([P("a.mp4"), P("b.mp4")], _secs({"a.mp4": 10.0}))
    assert p.raw is None and "cannot read the length of b.mp4" in p.why and "raw.mp4" in p.why


def test_three_videos_the_longest_when_clearly_longest_else_ask():
    p = pairing.pick_pair([P("a.mp4"), P("b.mp4"), P("ep.mkv")], _secs({"a.mp4": 20.0, "b.mp4": 30.0,
                                                                         "ep.mkv": 1300.0}))
    assert p.raw == P("ep.mkv") and sorted(p.competitors) == [P("a.mp4"), P("b.mp4")]
    p = pairing.pick_pair([P("a.mp4"), P("b.mp4"), P("c.mp4")], _secs({"a.mp4": 20.0, "b.mp4": 1250.0,
                                                                        "c.mp4": 1300.0}))
    assert p.raw is None and p.ask and len(p.candidates) == 3


def test_one_video_or_none_is_no_question():
    assert pairing.pick_pair([], _secs({})).why == "no video in it"
    p = pairing.pick_pair([P("raw.mp4")], _secs({}))
    assert p.raw is None and not p.ask and "a raw video but no competitor" in p.why
    p = pairing.pick_pair([P("clip.mp4")], _secs({}))
    assert "only one video (clip.mp4)" in p.why


def _clip(path: Path, seconds: float, w: int = 64, h: int = 36) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=black:size={w}x{h}:rate=10",
                    "-t", f"{seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_the_batch_folders_follow_the_rule_an_undecided_folder_is_skipped_with_why(tmp_path, capsys):
    root = tmp_path / "todo"
    _clip(root / "named" / "raw.mp4", 1.0)                       # named raw: the RAW although shorter
    _clip(root / "named" / "short.mp4", 2.0, 36, 64)
    _clip(root / "portrait" / "a.mp4", 2.0, 36, 64)               # about as long: the portrait one is the competitor
    _clip(root / "portrait" / "b.mp4", 2.2)
    _clip(root / "same" / "x.mp4", 2.0)                           # about as long, both landscape: skipped
    _clip(root / "same" / "y.mp4", 2.1)
    notes: list[str] = []
    jobs, skipped = batch.find_videos(root, notes)
    assert [(n, c.name, r.name) for n, c, r in jobs] == [("named", "short.mp4", "raw.mp4"),
                                                         ("portrait", "a.mp4", "b.mp4")]
    assert len(skipped) == 1 and skipped[0].startswith("same: can't tell which one is the RAW: x.mp4 (0:02) and "
                                                       "y.mp4 (0:02)") and "name the RAW raw.mp4" in skipped[0]
    assert len(notes) == 1 and notes[0].startswith("portrait: ") and "the portrait one (a.mp4)" in notes[0]
    # the app's way: the undecided folder asked, not skipped
    asks: list = []
    jobs2, skipped2 = batch.find_videos(root, asks=asks)
    assert skipped2 == [] and asks[0][0] == "same" and [v.name for v in asks[0][1]] == ["x.mp4", "y.mp4"]
    assert batch.split_folder([root / "same" / "x.mp4", root / "same" / "y.mp4"])[0] is None


def test_the_input_folder_of_the_command_line_names_first(tmp_path):
    d = tmp_path / "input"
    _clip(d / "raw.mp4", 1.0)
    _clip(d / "their short.mp4", 3.0, 36, 64)
    c, r, notes = cli.resolve_inputs(None, None, d)
    assert Path(c).name == "their short.mp4" and Path(r).name == "raw.mp4" and "named raw" in notes[0]
    amb = tmp_path / "amb"
    _clip(amb / "one.mp4", 2.0)
    _clip(amb / "two.mp4", 2.1)
    with pytest.raises(cli.InputError, match="can't tell which one is the RAW: one.mp4 .* name the RAW raw.mp4"):
        cli.resolve_inputs(None, None, amb)


# ---------------------------------------------------------------------------------------------------------------------
# 2. the quick wrong-pair check: the sound
# ---------------------------------------------------------------------------------------------------------------------

def _speechy(seconds: float, seed: int) -> np.ndarray:
    """Speech-like sound at 8 kHz: short syllables -- voiced ones (harmonics of a gliding pitch under two formants)
    and consonants (bursts of high noise) -- with short pauses, each with its own loudness: every place of it
    different from every other."""
    rng = np.random.default_rng(seed)
    n = int(seconds * PC.SR)
    y = np.zeros(n, np.float32)
    t = 0
    while t < n:
        ln = int(rng.uniform(0.04, 0.16) * PC.SR)
        gap = int(rng.exponential(0.06) * PC.SR) + int(0.01 * PC.SR)
        tt = np.arange(ln) / PC.SR
        if rng.random() < 0.6:
            f0 = rng.uniform(90, 260) * (1 + 0.15 * np.sin(2 * np.pi * rng.uniform(2, 6) * tt))
            ph = 2 * np.pi * np.cumsum(f0) / PC.SR
            seg = np.zeros(ln, np.float32)
            f1, f2 = rng.uniform(300, 900), rng.uniform(900, 2800)
            for k in range(1, 25):
                fk = k * f0.mean()
                if fk > 3800:
                    break
                seg += (np.exp(-((fk - f1) / 250) ** 2) + 0.7 * np.exp(-((fk - f2) / 400) ** 2) + 0.05) * np.sin(k * ph)
        else:
            spec = np.fft.rfft(rng.standard_normal(ln))
            spec[np.fft.rfftfreq(ln, 1 / PC.SR) < rng.uniform(1500, 3000)] *= 0.1
            seg = np.fft.irfft(spec, ln).astype(np.float32)
        seg = seg / max(1e-6, float(np.abs(seg).max()))
        seg *= np.hanning(ln).astype(np.float32) * rng.uniform(0.05, 0.5)
        y[t:t + ln] += seg[:max(0, min(ln, n - t))]
        t += ln + gap
    return y


STARTS = [20.0, 61.0, 95.0, 130.0, 170.0, 200.0, 240.0, 270.0]


def _cut(raw: np.ndarray, starts: list[float] = STARTS, piece_s: float = 4.0) -> np.ndarray:
    return np.concatenate([raw[int(s * PC.SR):int((s + piece_s) * PC.SR)] for s in starts])


def test_the_competitors_sound_is_found_in_its_raw_even_with_music_on_top():
    raw = _speechy(300.0, 3)
    comp = _cut(raw)
    tt = np.arange(len(comp)) / PC.SR
    music = 0.04 * (np.sin(2 * np.pi * 220 * tt) + np.sin(2 * np.pi * 277 * tt) + np.sin(2 * np.pi * 330 * tt)) * \
        (0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * tt))
    for c in (comp, comp + music):
        s = PC.sound_check(c.astype(np.float32), raw)
        assert s.state == PC.FOUND and s.found >= 3, s
    where = PC.sound_check(comp, raw).where                                      # where in the RAW they were found:
    assert where and all(any(st - PC.WIN_S <= w <= st + 4.0 for st in STARTS) for w in where)   # where they came
    #                                                                     from (a piece across a cut: its larger part)
    assert all(w["at"] >= 0.5 for st in s.details["tried"].values() for w in st)  # never the file's first half second


def test_another_sound_is_not_found_and_silence_is_no_evidence():
    raw = _speechy(300.0, 3)
    other = _cut(_speechy(300.0, 103))
    s = PC.sound_check(other, raw)
    assert s.state == PC.NOT_FOUND and s.found == 0 and s.weak == 0 and s.tested >= 4, s
    assert PC.sound_check(np.zeros(20 * PC.SR, np.float32), raw).state == PC.ABSENT
    assert PC.sound_check(None, raw).state == PC.ABSENT and PC.sound_check(other, None).state == PC.ABSENT


def test_a_competitor_sped_up_125_percent_pitch_up_is_found_as_sped_up():
    from scipy.signal import resample_poly
    raw = _speechy(300.0, 3)
    comp = resample_poly(_cut(raw), 4, 5).astype(np.float32)                    # 1.25 x faster, pitch up
    s = PC.sound_check(comp, raw)
    assert s.state == PC.FOUND and "1.25" in s.details["speed"], s


def test_the_correlation_finds_a_piece_at_its_place():
    rng = np.random.default_rng(5)
    raw = rng.standard_normal((20000, PC.BANDS)).astype(np.float32)
    c = PC.Correlator(raw, 150)
    ncc = c.ncc(raw[12345:12345 + 150])
    assert int(np.argmax(ncc)) == 12345 and ncc[12345] == pytest.approx(1.0, abs=1e-4)
    assert len(ncc) == 20000 - 150 + 1 and float(np.abs(ncc).max()) <= 1.0 + 1e-4


# ---------------------------------------------------------------------------------------------------------------------
# 3. the picture, the decision, the cache
# ---------------------------------------------------------------------------------------------------------------------

def test_the_video_window_is_the_part_that_moves():
    rng = np.random.default_rng(0)
    st = np.full((12, 640, 360), 200, np.uint8)                     # a still white card ...
    st[:, 220:420, 10:350] = rng.integers(0, 255, (12, 200, 340), dtype=np.uint8)   # ... a moving window in it
    x0, y0, x1, y1 = PC.moving_box(st)
    assert abs(x0 - 10) <= 4 and abs(x1 - 350) <= 4 and abs(y0 - 220) <= 4 and abs(y1 - 420) <= 4
    assert PC.moving_box(np.full((12, 64, 36), 9, np.uint8)) == (0, 0, 36, 64)      # nothing moves: the whole frame


def test_wrong_pair_only_when_the_evidence_clearly_says_so():
    S = PC.Side
    snf, sf, sab = S(PC.NOT_FOUND, 10, 0), S(PC.FOUND, 10, 8), S(PC.ABSENT)
    s_weak = S(PC.UNCLEAR, 10, 0, weak=2)                   # pieces almost found, none clearly
    s_jingle = S(PC.UNCLEAR, 13, 1, weak=1)                 # one piece of 13: another episode's jingle
    s_one_of_five = S(PC.UNCLEAR, 5, 1, weak=1)             # one piece of 5: too few to call it nothing
    pnf, pun, pf, pab = S(PC.NOT_FOUND, 16, 0), S(PC.UNCLEAR, 16, 1, weak=2), S(PC.FOUND, 16, 9), S(PC.ABSENT)
    assert PC.decide(snf, pnf) and PC.decide(sab, pnf) and PC.decide(s_weak, pnf) and PC.decide(s_jingle, pnf)
    assert PC.decide(snf, pab)                              # the picture not there: the sound shows nothing at all
    assert not PC.decide(s_one_of_five, pnf) and not PC.decide(sf, pnf) and not PC.decide(snf, pun)
    assert not PC.decide(snf, pf) and not PC.decide(s_weak, pab) and not PC.decide(sab, pab)
    assert not PC.decide(snf, None)                         # the picture not looked at: never on the sound alone


def _real(name: str) -> tuple[Path, Path]:
    d = REAL / name
    if not (d / "competitor.mp4").is_file() or not (d / "raw.mp4").is_file():
        pytest.skip(f"tests/real/{name} is not in this checkout")
    return d / "competitor.mp4", d / "raw.mp4"


def test_a_true_pair_passes_in_seconds_and_a_crossed_pair_is_a_wrong_pair(tmp_path):
    comp, raw = _real("video10-fixed")
    _, other_raw = _real("laptop009-fixed")
    t = time.perf_counter()
    ok = PC.check(comp, raw, tmp_path / "work")
    assert not ok.wrong and ok.sound.state == PC.FOUND and time.perf_counter() - t < 60
    bad = PC.check(comp, other_raw, tmp_path / "work")
    assert bad.wrong and bad.sound.state == PC.NOT_FOUND and bad.picture.state == PC.NOT_FOUND, bad.summary()
    msg = bad.message()
    assert msg.startswith("WRONG PAIR: This competitor doesn't seem to come from this RAW (sound: 0 of ")
    assert "picture: 0 of " in msg and "--force-inputs" in msg and "raw.mp4" in msg
    # the picture of the true pair is found too (the sound decided above, so it was not looked at)
    pic = PC.check(comp, raw, None, picture="always").picture
    assert pic.state == PC.FOUND and pic.found >= 6, pic
    # cached per pair: instant, the same answer
    t = time.perf_counter()
    again = PC.check(comp, other_raw, tmp_path / "work")
    assert again.cached and again.wrong and time.perf_counter() - t < 2.0
    assert list((tmp_path / "work" / "cache" / "pair_check").glob("*.json"))


def test_an_all_keyframe_raw_gives_at_most_one_keyframe_a_second(tmp_path):
    """ProRes, MJPEG ... every frame is a keyframe: thinned, or a 10-minute RAW would be 18 000 pictures."""
    p = tmp_path / "intra.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-t", "8",
                    "-c:v", "libx264", "-g", "1", "-pix_fmt", "yuv420p", str(p)], check=True)
    kf, kt = PC.keyframes(p, 8.0, 160, 90)
    assert 7 <= len(kf) <= 9 and kf.shape[1:] == (90, 160)
    assert np.all(np.diff(kt) >= 0.99) and kt[0] < 0.05


def test_tiny_or_very_short_videos_are_not_judged(tmp_path):
    a = _clip(tmp_path / "a.mp4", 1.0, 36, 64)
    b = _clip(tmp_path / "b.mp4", 3.0)
    r = PC.check(a, b, None)
    assert not r.wrong and r.picture is None and "not checked" in r.sound.why


def _ctx(comp: Path, raw: Path, work: Path, force: bool = False):
    from match_cuts.common import DecisionLog
    return SimpleNamespace(cfg=SimpleNamespace(competitor=str(comp), raw=str(raw), work_dir=str(work),
                                               force_inputs=force),
                           dlog=DecisionLog(work / "decisions.jsonl", truncate=True))


def test_the_run_stops_with_wrong_pair_and_force_inputs_goes_on(tmp_path, caplog):
    comp, _ = _real("video10-fixed")
    _, other_raw = _real("laptop009-fixed")
    (tmp_path / "w").mkdir()
    with caplog.at_level("INFO", logger="match_cuts"):
        with pytest.raises(pipeline.WrongPair) as e:
            PC.stop_if_wrong_pair(_ctx(comp, other_raw, tmp_path / "w"))
    assert str(e.value).startswith("WRONG PAIR: This competitor doesn't seem to come from this RAW")
    assert isinstance(e.value, pipeline.NotInRaw)                    # cli prints it and exits 2, as NOT IN THIS RAW
    assert any("pair check: sound: 0 of" in r.getMessage() for r in caplog.records)   # the numbers in the run's log
    assert PC.stop_if_wrong_pair(_ctx(comp, other_raw, tmp_path / "w", force=True)) is None


def test_pipeline_run_checks_the_pair_before_anything_is_converted(tmp_path, monkeypatch):
    from match_cuts.config import Config
    calls = []
    monkeypatch.setattr(pipeline, "stage_env", lambda ctx: calls.append("env"))
    monkeypatch.setattr(pipeline, "stage_probe_conform", lambda ctx: calls.append("conform"))

    def wrong(ctx):
        calls.append("pair")
        raise pipeline.WrongPair("WRONG PAIR: This competitor doesn't seem to come from this RAW (test)")
    monkeypatch.setattr(PC, "stop_if_wrong_pair", wrong)
    run = tmp_path / "run"
    (tmp_path / "c.mp4").write_bytes(b"c")
    (tmp_path / "r.mp4").write_bytes(b"r")
    cfg = Config(competitor=str(tmp_path / "c.mp4"), raw=str(tmp_path / "r.mp4"), out_dir=str(run / "extras"),
                 work_dir=str(tmp_path / "work"), deliver_dir=str(run))
    with pytest.raises(pipeline.WrongPair):
        pipeline.run(cfg)
    assert calls == ["env", "pair"]                                  # S2 never started


def test_cli_prints_the_wrong_pair_stop_and_exits_2(tmp_path, monkeypatch, capsys):
    comp, raw = _real("video10-fixed")

    def wrong(cfg):
        raise pipeline.WrongPair("WRONG PAIR: This competitor doesn't seem to come from this RAW (sound: 0 of 9 pieces "
                                 "found (0 %); picture: 0 of 16 frames found (0 %); checked in 6 s).")
    monkeypatch.setattr(pipeline, "run", wrong)
    code = cli.main(["--competitor", str(comp), "--raw", str(raw), "--out", str(tmp_path / "o")])
    err = capsys.readouterr().err
    assert code == 2 and "match_cuts: WRONG PAIR: This competitor doesn't seem to come from this RAW" in err
    assert batch.is_wrong_pair(batch.stop_message(err))


STUB = r'''
import sys
from pathlib import Path
a = sys.argv[1:]
run = Path(a[a.index("--run-dir") + 1])
(run / "extras").mkdir(parents=True)
print("12:00:00 INFO match_cuts pair check: sound: 0 of 9 pieces found (0 %)", file=sys.stderr)
print("match_cuts: WRONG PAIR: This competitor doesn't seem to come from this RAW (sound: 0 of 9 pieces found (0 %); "
      "picture: 0 of 16 frames found (0 %); checked in 7 s). To run it anyway: --force-inputs.", file=sys.stderr)
print("Run folder: " + str(run), file=sys.stderr)
sys.exit(2)
'''


def test_the_batch_summary_says_wrong_pair(tmp_path):
    vids, out = tmp_path / "videos", tmp_path / "out"
    (vids / "show").mkdir(parents=True)
    (vids / "show" / "competitor.mp4").write_bytes(b"c")
    (vids / "show" / "raw.mp4").write_bytes(b"r")
    stub = tmp_path / "stub.py"
    stub.write_text(STUB, encoding="utf-8")
    pyw = tmp_path / "py.cmd" if sys.platform == "win32" else tmp_path / "py.sh"
    if sys.platform == "win32":
        pyw.write_text(f'@"{sys.executable}" "{stub}" %*\n', encoding="utf-8")
    else:
        pyw.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n', encoding="utf-8")
        pyw.chmod(0o755)
    assert batch.main([str(vids), "--out", str(out), "--work", str(tmp_path / "w"), "--python", str(pyw)]) == 1
    s = json.loads((out / "batch_summary.json").read_text(encoding="utf-8"))
    row = s["rows"][0]
    assert row["wrong_pair"] and batch.verdict(row) == "WRONG PAIR" and not row["pass"]
    md = (out / "batch_summary.md").read_text(encoding="utf-8")
    assert "**show** -- **WRONG PAIR**: This competitor doesn't seem to come from this RAW" in md
    assert "| WRONG PAIR (exit 2) |" in md and "(1 WRONG PAIR)" in md and "## show: WRONG PAIR" in md
    assert "| -- (not run) |" in md and "NO CAPTIONS" not in md          # not run: no captions to miss
