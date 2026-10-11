"""The Zendaya clip (tests/real/zendaya): flash frames, caption stutters, captions timed to the speech of the final
edit, silence across cuts, and every clip of 1_edit.xml named after the RAW.

The real files (competitor.mp4, raw.mp4, and run_edit.xml / run_captions.srt of the run that showed the problems) are
at tests/real/zendaya; the RAW's word timings and shot changes where that run plays it are in
tests/fixtures/zendaya_raw.json. The rules themselves are tested on synthetic pieces.
"""
from __future__ import annotations

import json
import sys
import types
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_export_xml_edl as T  # noqa: E402

from match_cuts import captions as C, caption_rules as R, export_xml_edl as ex, shots, silence as S, speech as SP  # noqa: E402

FPS = Fraction(60)
SR = 16000
ROOT = Path(__file__).resolve().parents[3]
ZEN = ROOT / "tests" / "real" / "zendaya"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "zendaya_raw.json"


def room(dur: float = 6.0) -> np.ndarray:
    return (0.002 * np.random.default_rng(0).standard_normal(int(dur * SR))).astype(np.float32)


def tone(y: np.ndarray, a: float, b: float, amp: float = 0.2) -> np.ndarray:
    n0, n1 = int(a * SR), int(b * SR)
    y[n0:n1] += (amp * np.sin(np.arange(n1 - n0) * 0.3)).astype(np.float32)
    return y


def words(*ws) -> list:
    return [types.SimpleNamespace(text=t, raw=t, start=a, end=b) for t, a, b in ws]


# ---------------------------------------------------------------------------------------------
# 1. flash frames
# ---------------------------------------------------------------------------------------------

def test_shot_changes_are_found_where_the_picture_jumps():
    rng = np.random.default_rng(1)
    a, b = rng.integers(0, 255, (36, 64)).astype(np.float32), rng.integers(0, 255, (36, 64)).astype(np.float32)
    thumbs = [a + rng.normal(0, 1, a.shape) for _ in range(20)] + [b + rng.normal(0, 1, b.shape) for _ in range(20)]
    moving = [np.roll(a, k, axis=1) for k in range(20)]                    # a camera move: no shot change
    assert shots.changes_of(np.array(thumbs)) == [20]
    assert shots.changes_of(np.array(moving)) == []


def test_padding_stops_at_a_shot_change_and_no_sliver_of_a_shot_is_shown():
    y = tone(tone(room(), 1.0, 1.6), 2.4, 3.0)
    sm = SP.speech_map(y, SR, S.Settings(), words(("hello", 1.0, 1.6), ("there", 2.4, 3.0)))
    # the clip ends 0.15 s after "hello" -- but a new shot starts 0.05 s after it: the padding stops there
    assert SP.shot_guard(sm, 0.9, 1.77, [1.68], 0.15, 0.05) == pytest.approx((0.9, 1.68))
    # a clip starting 0.05 s before "there" with a shot change 0.1 s in: the silent sliver goes
    assert SP.shot_guard(sm, 2.35, 3.15, [2.38], 0.15, 0.05) == pytest.approx((2.38, 3.15))
    # the speech runs on into a new shot 0.1 s before the clip's end: that shot is shown 0.25 s
    na, nb = SP.shot_guard(sm, 2.35, 3.15, [2.95], 0.15, 0.05)
    assert nb >= 2.95 + shots.MIN_SHOT_S - 1e-6


def test_the_flash_check_finds_a_sliver_of_another_shot_at_a_cut():
    items = [{"label": "S10", "start": 0, "end": 60, "in": 600, "out": 660, "speed": 1.0},
             {"label": "S11", "start": 60, "end": 116, "in": 171, "out": 227, "speed": 1.0},     # crosses 3.76 s
             {"label": "S12", "start": 116, "end": 184, "in": 9893, "out": 9961, "speed": 1.0}]
    bad = shots.flash_problems(items, FPS, [3.76, 5.0])                  # S12 is another shot (164.9 s)
    assert len(bad) == 1 and bad[0].startswith("S11 at 00:00:01:55: 1 frame(s) of a different RAW shot")
    items[1]["out"], items[1]["end"] = 225, 114                                                # stops before it
    items[2]["start"] -= 2
    assert shots.flash_problems(items, FPS, [3.76, 5.0]) == []
    gap = [dict(items[0]), dict(items[1], start=62, end=114)]                                # two empty frames: black
    assert any("nothing (black)" in r for r in shots.flash_problems(gap, FPS, [3.76, 5.0]))


# ---------------------------------------------------------------------------------------------
# 2. caption stutters
# ---------------------------------------------------------------------------------------------

def test_a_short_word_said_twice_in_one_caption_is_kept_once_and_listed():
    w = [C.Word(t, a, b, 1.0, t) for t, a, b in (("The", 0.0, 0.2), ("the", 0.2, 0.35), ("one", 0.35, 0.5),
                                                  ("that's", 0.5, 0.8))]
    caps = [C.Caption("The the one that's", 0, 48, "voice", w)]
    out, rep = R.enforce(caps, FPS, "voice", w)
    assert [c.text for c in out] == ["The one that's"]
    (st,) = rep["stutters"]
    assert (st["time"], st["start_tc"], st["words"], st["was"].lower(), st["now"].lower()) == (
        0.2, "00:00:00,000", ["the"], "the the one that's", "the one that's")
    for text in ("I I think", "to to go", "a a cat"):
        ws = [C.Word(t, i * 0.2, i * 0.2 + 0.2, 1.0, t) for i, t in enumerate(text.split())]
        got, _ = R.enforce([C.Caption(text, 0, 48, "voice", ws)], FPS, "voice", ws)
        assert " ".join(c.text for c in got).lower() == " ".join(dict.fromkeys(text.split())).lower()
    # a deliberate repeat as separate captions stays
    no = [C.Word("no", i * 0.5, i * 0.5 + 0.3, 1.0, "no") for i in range(3)]
    sep = [C.Caption("No", i * 30, i * 30 + 30, "voice", [no[i]]) for i in range(3)]
    got, rep = R.enforce(sep, FPS, "voice", no)
    assert len(got) == 3 and rep["stutters"] == []
    assert R.enforce([C.Caption("very very good", 0, 60, "voice",
                                [C.Word(t, i * 0.2, i * 0.2 + 0.2, 1.0, t) for i, t in
                                 enumerate("very very good".split())])], FPS, "voice")[0][0].text.lower() == \
        "very very good"                                              # not a short word: a deliberate "very very"


# ---------------------------------------------------------------------------------------------
# 3. captions timed to the speech of the final edit
# ---------------------------------------------------------------------------------------------

def test_captions_start_when_their_first_word_is_spoken():
    y = tone(tone(tone(room(), 0.2, 0.8), 1.0, 1.6), 2.0, 2.6)
    sm = SP.speech_map(y, SR, S.Settings(), words(("The", 0.2, 0.5), ("one", 0.5, 0.8), ("my", 1.0, 1.3),
                                                  ("favorite", 1.3, 1.6), ("that", 2.0, 2.6)))
    items = [{"start": 0, "end": 360, "in": 0, "out": 360, "speed": 1.0}]
    onsets, ref = C.speech_starts(items, sm, FPS)
    # each sound's start, and where a word starts inside one (words run together: "The" | "one")
    assert onsets == pytest.approx([0.2, 0.5, 1.0, 1.3, 2.0], abs=0.04)
    on = [round(min(onsets, key=lambda o: abs(o - t)) * 60) for t in (0.2, 1.0, 2.0)]   # the captions' sounds
    # the competitor switched to "my favorite" 0.33 s after she says it, and to "that" early
    caps = [C.Caption("The one", 12, 80, "competitor"), C.Caption("my favorite", 80, 110, "competitor"),
            C.Caption("that", 110, 160, "competitor")]
    off = C.timing_off(caps, FPS, onsets, [ref])
    assert [(r["text"], r["off"]) for r in off] == [("my favorite", 80 - on[1]), ("that", 110 - on[2])]
    assert C.time_to_speech(caps, FPS, onsets, [ref]) == 3
    assert [(c.start, c.end) for c in caps] == [(on[0], on[1]), (on[1], on[2]), (on[2], 160)]
    assert C.timing_off(caps, FPS, onsets, [ref]) == []
    # a caption moved past its own end keeps its length, and back-to-back captions stay back to back (Zendaya's
    # 'explain' at 00:00:06,117 was left one frame long, then nothing until 'it')
    caps = [C.Caption("The one", 12, 40, "competitor"), C.Caption("my favorite", 40, 45, "competitor"),
            C.Caption("that", 45, 160, "competitor")]
    C.time_to_speech(caps, FPS, onsets, [ref])
    assert [(c.start, c.end) for c in caps] == [(on[0], on[1]), (on[1], on[2]), (on[2], 160)]
    # an audio cut between two captions: the one before ends on the cut, never across it
    caps = [C.Caption("The one", 12, 80, "competitor"), C.Caption("my favorite", 80, 110, "competitor")]
    C.time_to_speech(caps, FPS, onsets, [ref], cuts=[on[1] - 4])
    assert (caps[0].end, caps[1].start) == (on[1] - 4, on[1])
    # words my edit does not play: not heard (the caption is left out by the caption stage); untranscribed speech
    # under a caption no other words are said in: heard, timed to that sound
    odd = [C.Caption("The one", 12, 80, "competitor"), C.Caption("I haven't got", 80, 110, "competitor")]
    assert C.unheard(odd, FPS, onsets, [ref]) == [1]
    mumble = [C.Caption("um darling", 115, 150, "competitor")]
    starts, heard = C.spoken_starts(mumble, onsets, [[w for w in ref if w[0] != "that"]], FPS)
    assert heard == [True] and round(starts[0] * 60) == on[2]


# ---------------------------------------------------------------------------------------------
# 4. silence across a cut
# ---------------------------------------------------------------------------------------------

def test_the_silence_across_a_cut_is_trimmed_to_both_pads():
    y = tone(tone(room(), 0.5, 1.5), 1.82, 3.0)                       # 0.32 s of quiet across the cut at 1.6 s
    st = S.Settings()                                                  # --pad-after 0.05 + --pad-before 0.03
    inside, _ = S.removal_ranges(y, SR, FPS, 360, st)
    assert not any(c.s0 <= 1.6 <= c.s1 for c in inside)               # inside a clip it is no pause to cut (< 0.3 s)
    across, _ = S.removal_ranges(y, SR, FPS, 360, st, cuts_at=[96])
    c = next(c for c in across if c.s0 <= 1.6 <= c.s1)
    kept = (c.s1 - c.s0) - (c.b - c.a) / 60.0
    assert kept == pytest.approx(0.08, abs=2.0 / 60) and c.a / 60 >= c.s0 + 0.05 - 1e-9


# ---------------------------------------------------------------------------------------------
# 5. every clip is the RAW
# ---------------------------------------------------------------------------------------------

def test_every_clip_is_named_after_the_raw_with_one_master_clip(tmp_path):
    from match_cuts.config import Config
    cl = T.premiere_cutlist()
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(cl, xml, Config(out_dir=str(tmp_path), premiere=True))
    root = ET.parse(xml).getroot()
    items = list(root.iter("clipitem"))
    assert items and {e.findtext("name") for e in items} == {"raw.mp4"}
    assert {e.findtext("masterclipid") for e in items} == {ex.PREMIERE_MASTERCLIP}
    assert {f.get("id") for f in root.iter("file")} == {"file-raw"}
    assert {e.findtext("file/name") for e in items if e.find("file/name") is not None} == {"raw.mp4"}
    assert "competitor" not in xml.read_text(encoding="utf-8").replace("competitor's", "")
    assert [ex.item_label(e) for e in items if e.find("sourcetrack/mediatype").text == "video"][:2] == ["S01", "S02"]


def test_the_raw_copy_is_always_raw_mp4():
    from match_cuts.conform import _media_name_for_raw
    assert _media_name_for_raw(Path("input/competitor.mp4")) == "raw.mp4"
    assert _media_name_for_raw(Path("input/My Clip.MOV")) == "raw.mov"


# ---------------------------------------------------------------------------------------------
# the real run
# ---------------------------------------------------------------------------------------------

def _run_clips() -> list[tuple[str, int, int, int]]:
    """V1 of the run (run_edit.xml) up to S12: the clips at 100 % (label, start, end, RAW in at 60 fps)."""
    seq = ET.parse(ZEN / "run_edit.xml").getroot().find(".//sequence")
    out = []
    for e in seq.find("media/video/track").findall("clipitem"):
        s, t, a, b = (int(e.findtext(k)) for k in ("start", "end", "in", "out"))
        if t - s == b - a and s < 483:
            out.append((e.findtext("name").split()[0], s, t, a))
    return out


def _cutlist(clips):
    from match_cuts.model import Cutlist, Segment
    segs = [Segment(id=i + 1, type="raw", comp_in=s, comp_out=t, raw_in_seconds=a / 60.0,
                    raw_in_interval=[a / 60.0 - 0.00008, a / 60.0 + 0.00008], speed=1.0, confidence=.97,
                    transform=dict(T.PAN0), label=name) for i, (name, s, t, a) in enumerate(clips)]
    comp = {"file": "media/competitor_ref.mp4", "width": 720, "height": 1280, "fps": "60/1",
            "frames": max(t for _, _, t, _ in clips)}
    raw = {"file": "media/raw.mp4", "file_abs": "/abs/media/raw.mp4", "width": 1920, "height": 1080, "fps": "25/1",
           "frames": 6939, "has_audio": True, "audio_sample_rate": 48000, "audio_channels": 2}
    layout = {"mode": "match", "layout_kind": "boxed", "box": dict(T.PBOX), "background": "solid",
              "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000"}
    return Cutlist(1, comp, raw, layout, segs)


@pytest.mark.skipif(not (ZEN / "raw.mp4").is_file() or not (ZEN / "run_edit.xml").is_file(),
                    reason="tests/real/zendaya not in this checkout")
def test_zendaya_no_flash_frame_and_no_long_silence_at_a_cut(tmp_path):
    from match_cuts import media, repeats
    from match_cuts.config import Config
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    w = {m: [types.SimpleNamespace(text=t, raw=t, start=a, end=b) for t, a, b in v] for m, v in fx["words"].items()}
    changes = shots.seconds(fx["shot_changes"], Fraction(25))
    assert 3.76 in changes                                            # the shot change S11 ran over by one frame
    # the run's own export: the flash at 00:00:06:54, between S11 and S12
    flash = ex.premiere_flash_problems(ZEN / "run_edit.xml", changes)
    assert any(r.startswith("S11 at 00:00:06:54: 1 frame(s) of a different RAW shot") for r in flash), flash
    y = media.extract_audio(ZEN / "raw.mp4", sr=48000, mono=True)
    sm = SP.speech_map(y, 48000, S.Settings(), w["medium.en"], w["small.en"], [tuple(h) for h in fx["heard"]])
    cl = _cutlist(_run_clips())
    cfg = Config(out_dir=str(tmp_path), premiere=True, remove_silence=True)     # the speech-safe cuts and silences
    plan = repeats.add_to_plan(S.plan_premiere(cl, y, 48000, cfg, None, sm, shots=changes), cl, cfg)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(cl, xml, cfg, plan["ripple"])
    v = ex.validate_premiere_exports(cl, xml, None, cfg, plan["ripple"], sm, changes)
    assert v["ok"], v["errors"]
    assert v["flash_problems"] == [] and v["silence_problems"] == [] and v["speech_problems"] == []
    x = ex.parse_premiere_xml(xml)
    s11 = [c for c in x["clips"] if c["in"] < 3.76 * 60 < c["out"] + 30 and c["in"] < 400]
    assert s11 and all(c["out"] <= 3.76 * 60 + 0.5 or c["out"] - 3.76 * 60 >= 15 for c in s11)   # no sliver
    assert {c["name"] for c in x["clips"]} == {"raw.mp4"}
