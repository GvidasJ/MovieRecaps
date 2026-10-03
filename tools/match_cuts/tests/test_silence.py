"""silence.py: the silences of my edit cut out of the Premiere export (competitor mode) and the RAW-only edit.

Synthetic speech (tone bursts) and pauses over a quiet room tone: which silences are cut and where (pads, the 0.35 s
minimum, the edit's edges, levels relative to the speech), that a click inside a silence does not break it, that the
cut audio does not click, the Premiere sequence with the silences removed (clips split and moved, A1 faded at every
cut, markers moved, cross dissolves untouched) passing its own validation, copied captions moving with the cuts, and
the whole RAW-only run on a short clip.
"""
from __future__ import annotations

import shutil
import types
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts import silence as S

SR = 16000
FPS = Fraction(60)
SPEECH = ((0.5, 1.5), (1.8, 2.6), (3.6, 5.0))          # the 0.3 s pause at 1.5-1.8 is too short to cut (LOOSE)
LOOSE = S.Settings(db=-20.0, min_s=0.35, pad_before=0.08, pad_after=0.12)   # explicit, loose settings: the mechanics


def speech(dur: float = 6.0, gain: float = 1.0, bursts=SPEECH, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = (0.002 * rng.standard_normal(int(dur * SR))).astype(np.float32)          # room tone, ~-54 dBFS
    for a, b in bursts:
        n0, n1 = int(a * SR), int(b * SR)
        y[n0:n1] += (0.2 * np.sin(np.arange(n1 - n0) * 0.3)).astype(np.float32)
    return y * gain


def test_silences_longer_than_the_minimum_are_cut_keeping_the_pads():
    cuts, lv = S.removal_ranges(speech(), SR, FPS, 360, LOOSE)
    assert lv["how"] == "--silence-db -20" and lv["threshold_db"] == round(lv["speech_db"] - 20, 1)
    # leading silence to 0.5 s minus the 0.08 s pad; 2.6 s + 0.12 .. 3.6 s - 0.08; 5.0 s + 0.12 to the end
    assert [(c.a, c.b) for c in cuts] == [(0, 24), (165, 210), (309, 360)]
    for c in cuts:                                                    # never more than the silence itself
        assert c.a / 60 >= (c.s0 + 0.12 if c.s0 > 0 else 0) - 1e-9 and c.b / 60 <= (c.s1 - 0.08 if c.s1 < 6 else 6) + 1e-9
    quiet, _ = S.removal_ranges(speech(gain=0.05), SR, FPS, 360, LOOSE)     # 26 dB quieter recording
    assert [(c.a, c.b) for c in quiet] == [(c.a, c.b) for c in cuts]                # relative to the speech level
    longer, _ = S.removal_ranges(speech(), SR, FPS, 360, S.Settings(db=-20.0, min_s=0.97, pad_before=0.08, pad_after=0.12))
    assert [(c.a, c.b) for c in longer] == [(309, 360)]                              # --min-silence: only 0.98 s
    padded, _ = S.removal_ranges(speech(), SR, FPS, 360, S.Settings(db=-20.0, min_s=0.35, pad_before=0.2, pad_after=0.3))
    assert [(c.a, c.b) for c in padded] == [(0, 16), (176, 202), (320, 360)]        # --pad-before / --pad-after


def test_loudness_is_short_window_not_single_peaks():
    y = speech()
    y[int(3.1 * SR)] = 0.99                                           # a click inside the 2.6-3.6 s silence
    cuts, _ = S.removal_ranges(y, SR, FPS, 360, LOOSE)
    assert (165, 210) in [(c.a, c.b) for c in cuts]
    loud = speech(bursts=SPEECH + ((3.0, 3.1),))                      # a real 0.1 s sound splits it in two
    cuts, _ = S.removal_ranges(loud, SR, FPS, 360, LOOSE)
    assert (165, 210) not in [(c.a, c.b) for c in cuts]


def test_dissolves_are_protected_and_an_all_silent_edit_is_kept():
    cuts, _ = S.removal_ranges(speech(), SR, FPS, 360, LOOSE, protect=[(180, 190)])
    assert [(c.a, c.b) for c in cuts] == [(0, 24), (165, 180), (190, 210), (309, 360)]
    assert S.removal_ranges(np.zeros(6 * SR, np.float32), SR, FPS, 360, S.Settings())[0] == []


def test_ripple_moves_and_splits():
    rp = S.Ripple([S.Cut(10, 20, 0, 0), S.Cut(40, 45, 0, 0)], 100)
    assert (rp.removed, rp.new_frames) == (15, 85)
    assert [rp.map(f) for f in (0, 10, 15, 20, 39, 40, 45, 99)] == [0, 10, 10, 10, 29, 30, 30, 84]
    assert rp.keep(5, 50) == [(5, 10), (20, 40), (45, 50)] and rp.keep(12, 18) == []


def test_the_cut_audio_does_not_click():
    y = speech()
    y += np.float32(0.01) * np.sin(np.arange(len(y)) * 0.05).astype(np.float32)   # a hum under everything
    cuts, _ = S.removal_ranges(y, SR, FPS, 360, LOOSE)
    rp = S.Ripple(cuts, 360)
    out = S.cut_audio(y, SR, FPS, rp)
    assert len(out) == rp.new_frames * SR // 60
    step = np.abs(np.diff(out))
    typical = np.percentile(np.abs(np.diff(y[int(2.7 * SR):int(3.5 * SR)])), 99)   # inside a silence
    for c in rp.cuts:
        if 0 < c.a and c.b < 360:
            j = rp.map(c.a) * SR // 60
            assert step[j - 3:j + 3].max() <= 1.5 * typical               # no jump at the join
            assert abs(float(out[j - 1])) < 0.002 and abs(float(out[j])) < 0.002   # both sides fade to zero


@pytest.fixture()
def premiere_cl():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_export_xml_edl as T
    return T.premiere_cutlist()


def test_premiere_export_with_silences_removed(premiere_cl, tmp_path):
    from match_cuts import export_xml_edl as ex
    from match_cuts.config import Config
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    rp = S.Ripple([S.Cut(20, 40, 0, 0), S.Cut(130, 150, 0, 0), S.Cut(390, 420, 0, 0)], 600)
    xml, edl = tmp_path / "1_edit.xml", tmp_path / "e.edl"
    ex.write_premiere_xml(premiere_cl, xml, cfg, rp)
    ex.write_edl(premiere_cl, edl, cfg)
    x = ex.parse_premiere_xml(xml)
    assert x["duration"] == 530
    assert [(c["start"], c["end"], c["in"], c["out"]) for c in x["clips"][:2]] == [(0, 20, 396, 416), (20, 60, 436, 476)]
    assert x["audio"][0]["levels"] == [(415, 1.0), (416, 0.0)] and x["audio"][1]["levels"] == [(436, 0.0), (437, 1.0)]
    assert any(c["end"] == -1 for c in x["clips"]) and any(c["start"] == -1 for c in x["clips"])   # dissolve kept
    assert ("NOT IN RAW S07", 340, 370) in [(m["name"], m["in"], m["out"]) for m in x["markers"]]
    v = ex.validate_premiere_exports(premiere_cl, xml, edl, cfg, rp)
    assert v["ok"], v["errors"]
    assert not ex.validate_premiere_exports(premiere_cl, xml, edl, cfg)["ok"]      # it is not the uncut sequence


def test_the_plan_is_measured_on_my_clips_audio(premiere_cl):
    """A1 of the fixture plays RAW 6.6-7.9 s for clip 1 (sequence 0-80): silence the RAW under it and that part goes;
    so do the NOT-IN-RAW spot and the freeze, which play no RAW audio. Speech everywhere else stays."""
    from match_cuts.config import Config
    raw = (0.2 * np.sin(np.arange(200 * SR) * 0.3)).astype(np.float32)          # speech everywhere ...
    raw[int(6.9 * SR):int(7.6 * SR)] = 0.0                                         # ... but under clip 1
    plan = S.plan_premiere(premiere_cl, raw, SR, Config(premiere=True, silence_db=-20.0, min_silence=0.35,
                                                         pad_before=0.08, pad_after=0.12))
    assert plan["cuts"] == [(27, 54), (389, 433), (509, 534)]       # clip 1; NOT-IN-RAW 380-440; freeze 500-540
    assert 18 + 0.12 * 60 <= 27 and 54 <= 60 - 0.08 * 60           # inside RAW 6.9-7.6 s, pads kept
    assert plan["new_s"] == round((600 - 96) / 60, 3) and len(plan["rows"]) == 3


def test_copied_captions_move_with_the_cuts_and_fully_silent_ones_are_dropped():
    from match_cuts import captions as C
    rp = S.Ripple([S.Cut(60, 120, 0, 0)], 300)
    spans = [{"comp_in": 0, "comp_out": 25, "ocr": "SO AS A"}, {"comp_in": 32, "comp_out": 58, "ocr": "*PAUSE*"},
             {"comp_in": 25, "comp_out": 40, "ocr": "JOKE"}, {"comp_in": 58, "comp_out": 90, "ocr": "I"}]
    moved, gone = C.move_spans(sorted(spans, key=lambda d: d["comp_in"]), lambda k: 2 * k, rp)
    assert [(d["ocr"], d["comp_in"], d["comp_out"]) for d in moved] == [("SO AS A", 0, 50), ("JOKE", 50, 60),
                                                                       ("I", 60, 120)]
    assert [d["ocr"] for d in gone] == ["*PAUSE*"]
    caps, _ = C.competitor_copy(moved, [], FPS, int, FPS)
    assert [(c.text, c.start, c.end) for c in caps] == [("SO AS A", 0, 50), ("JOKE", 50, 60), ("I", 60, 120)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not found")
def test_raw_only_run_cuts_the_silences_of_the_raw(tmp_path, monkeypatch, capsys):
    """No --competitor: a 6 s RAW (1920x1080, 30 fps) with three stretches of speech becomes the Premiere sequence
    with its silences cut out, framed to cover the window, and the end summary lists every removed silence."""
    import soundfile as sf

    from match_cuts import cli, export_xml_edl as ex, transcribe
    wav = tmp_path / "speech.wav"
    sf.write(str(wav), speech(), SR)
    raw = tmp_path / "raw.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=6", "-i", str(wav),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(raw)], check=True)
    monkeypatch.setattr(transcribe, "available", lambda: "not in this test")
    code = cli.main(["--raw", str(raw), "--out", str(tmp_path / "out"), "--work", str(tmp_path / "work")])
    out = capsys.readouterr().out
    assert code == 0, out
    run = tmp_path / "out" / "001"
    x = ex.parse_premiere_xml(run / "1_edit.xml")
    assert (x["width"], x["height"], x["timebase"]) == (1080, 1920, 60)
    assert 210 <= x["duration"] <= 228                       # 6 s minus about 2.35 s of silence (tight defaults)
    assert len(x["clips"]) == 3 and not ex.premiere_gaps(run / "1_edit.xml")    # three pieces, the window covered
    assert "Silences: 4 removed" in out and "length 00:06.00 -> " in out
    assert out.count(" s  (cut at ") == 4 and "RAW-only edit" in out
    assert "settings for this video:" in out and "word timings not available" in out
    assert (run / "extras" / "report.md").read_text(encoding="utf-8").count("Silence removal") == 1


def test_the_caption_stage_moves_copied_captions_with_the_cuts(tmp_path, monkeypatch):
    import types

    from match_cuts import caption_ocr, captions as C, pipeline
    from match_cuts.common import Cache
    from match_cuts.config import Config
    spans = [{"comp_in": 0, "comp_out": 15, "ocr": "SO AS A", "score": 0.97, "agreement": 1.0},
             {"comp_in": 15, "comp_out": 25, "ocr": "*PAUSE*", "score": 0.97, "agreement": 1.0},
             {"comp_in": 25, "comp_out": 60, "ocr": "JOKE", "score": 0.97, "agreement": 1.0}]
    monkeypatch.setattr(caption_ocr, "available", lambda: None)
    monkeypatch.setattr(caption_ocr, "caption_band", lambda layout, wh: object())
    monkeypatch.setattr(C, "_read_spans", lambda ctx, layout, fps: {"spans": spans, "frames_read": 60})
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"), premiere=True)
    info = types.SimpleNamespace(path="", file_hash="x", width=1080, height=1920, display_width=1080,
                                 display_height=1920)
    plan = S.summarize([S.Cut(28, 52, 0.5, 0.9)], 120, FPS, S.Settings(),                   # 30 fps frames 14-26
                       {"speech_db": -16.0, "noise_db": -50.0, "threshold_db": -38.0, "how": "set", "words": 3})
    ctx = types.SimpleNamespace(cfg=cfg, comp_info=info, comp_fps=Fraction(30), n_comp=60, paths={}, broll=None,
                                cutlist=types.SimpleNamespace(layout={}, segments=[]), cache=Cache(cfg.work),
                                raw_audio=None, audio_sr=SR, warn=lambda m: None, silence=plan)
    res = C.run_captions(ctx)
    got = [(b["text"], round(b["start_ms"] * 60 / 1000), round(b["end_ms"] * 60 / 1000))
           for b in C.read_srt(res["path"])]
    # the copies move with the cuts; my rules on the text ("SO AS A" | "JOKE" regrouped: never end on "a")
    assert got == [("So as a joke", 0, 96)]
    assert res["frames"] == 96 and [d["text"] for d in res["silence_dropped"]] == ["*PAUSE*"]
    ctx.captions = res
    rows = pipeline.hand_checks(ctx)
    assert any("'*PAUSE*': completely inside a removed silence -- dropped" in r for r in rows["captions"])
    assert rows["silence"][0].startswith("1 removed, 0.40 s in all; length 00:02.00 -> 00:01.60")


def test_the_defaults_are_tight_and_can_be_overridden():
    from match_cuts import cli
    from match_cuts.config import Config
    assert (S.Settings().db, S.Settings().min_s, S.Settings().pad_before, S.Settings().pad_after) == \
        (None, 0.15, 0.04, 0.06)
    c = Config()
    assert (c.silence_db, c.min_silence, c.pad_before, c.pad_after) == (None, 0.15, 0.04, 0.06)
    a = cli.build_parser().parse_args([])
    assert (a.silence_db, a.min_silence, a.pad_before, a.pad_after) == (None, 0.15, 0.04, 0.06)
    a = cli.build_parser().parse_args(["--silence-db", "-25", "--min-silence", "0.3", "--pad-before", "0.1",
                                       "--pad-after", "0.2"])
    cfg = cli.config_from_args(a, "c.mp4", "r.mp4")
    assert S.Settings.from_cfg(cfg) == S.Settings(-25.0, 0.3, 0.1, 0.2)


def test_the_threshold_adapts_so_a_noisy_videos_pauses_are_cut():
    rng = np.random.default_rng(3)
    y = speech()
    y += (0.03 * rng.standard_normal(len(y))).astype(np.float32)          # loud background noise, about -30 dBFS
    assert S.removal_ranges(y, SR, FPS, 360, LOOSE)[0] == []                # 20 dB under the speech: nothing cut
    cuts, lv = S.removal_ranges(y, SR, FPS, 360, S.Settings())
    assert lv["how"] == "set from the speech level and the background noise"
    assert lv["noise_db"] + 3 <= lv["threshold_db"] <= lv["speech_db"] - 6 and -32 < lv["noise_db"] < -29
    assert [(c.a, c.b) for c in cuts] == [(0, 26), (95, 104), (161, 212), (305, 360)]   # all four pauses
    quiet, lv_q = S.removal_ranges(speech(), SR, FPS, 360, S.Settings())  # the same speech, quiet background
    assert lv_q["threshold_db"] < lv["threshold_db"] - 10 and len(quiet) == 4


def test_cuts_only_fall_between_words_and_keep_the_padding_around_each_word():
    W = lambda text, a, b: types.SimpleNamespace(text=text, start=a, end=b)        # noqa: E731
    y = speech()
    y[int(3.0 * SR):int(3.2 * SR)] += (0.01 * np.sin(np.arange(int(0.2 * SR)) * 0.3)).astype(np.float32)  # soft "uh"
    words = [W("so", 0.5, 1.0), W("what", 1.05, 1.5), W("about", 1.8, 2.95),         # "about" timed into the pause
             W("uh", 3.0, 3.2), W("then", 3.6, 5.0)]
    cuts, lv = S.removal_ranges(y, SR, FPS, 360, S.Settings(), words=words)
    assert lv["words"] == 5
    t, db = S.loudness(y, SR)
    cores = S.word_cores(words, t, db, lv["noise_db"])
    assert abs(cores[2][1] - 2.6) < 0.05                    # "about": only its audible part, not the pause after
    assert abs(cores[3][0] - 3.0) < 0.03 and abs(cores[3][1] - 3.2) < 0.03          # the soft word is a word
    for c in cuts:                                          # never inside a word, the padding kept around each
        for a, b in cores:
            assert c.b / 60 <= a - 0.04 + 1e-9 or c.a / 60 >= b + 0.06 - 1e-9, (c, a, b)
    assert [(c.a, c.b) for c in cuts] == [(0, 26), (95, 104), (163, 177), (197, 212), (305, 360)]
    no_words, _ = S.removal_ranges(y, SR, FPS, 360, S.Settings())
    assert (161, 212) in [(c.a, c.b) for c in no_words]     # by loudness alone the soft "uh" would have gone


def test_the_summary_shows_the_settings_used_and_the_time_removed():
    from match_cuts import pipeline
    lv = {"speech_db": -16.2, "noise_db": -42.2, "threshold_db": -33.1,
          "how": "set from the speech level and the background noise", "words": 632}
    plan = S.summarize([S.Cut(60, 90, 1.0, 1.5)], 600, FPS, S.Settings(), lv)
    lines = pipeline.silence_lines(plan)
    assert lines[0] == "1 removed, 0.50 s in all; length 00:10.00 -> 00:09.50"
    assert lines[1] == ("settings for this video: speech -16.2 dBFS, background -42.2 dBFS -> silence below -33.1 dBFS "
                        "(set from the speech level and the background noise), longer than 0.15 s; kept 0.04 s before "
                        "/ 0.06 s after each word; cuts only between words (632 words timed)")
    plan = S.summarize([], 600, FPS, S.Settings(), dict(lv, words=None))
    assert pipeline.silence_lines(plan)[0] == "none found; length 00:10.00"
    assert "word timings not available: cuts from loudness alone" in pipeline.silence_lines(plan)[1]
