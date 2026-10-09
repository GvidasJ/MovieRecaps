"""Another video (rare): the competitor uses footage of a second video I did not give. A NOT-IN-RAW stretch whose
competitor audio has speech that is not in my RAW audio is OTHER VIDEO, not B-roll: V1 and A1 stay empty for exactly
its length, in its place, with an "OTHER VIDEO – not in RAW (start–end)" marker; no silence is removed inside it; its
captions are the competitor's (or transcribed from the competitor's audio there), timed to that audio; the checks
allow the stretch and the end summary lists it.

The Zendaya clip (tests/real/zendaya) is such a case: tests/fixtures/zendaya_other_video.json holds what its
competitor's audio says in the stretch.
"""
from __future__ import annotations

import json
import sys
import types
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_export_xml_edl as T  # noqa: E402

from match_cuts import broll, captions as C, caption_rules as R, export_xml_edl as ex, shots, silence as S  # noqa: E402
from match_cuts.config import Config  # noqa: E402

FPS = Fraction(60)
SR = 16000
ZEN = json.loads((Path(__file__).resolve().parent / "fixtures" / "zendaya_other_video.json").read_text())
SAID = [("I", 6.40, 6.50, 0.9), ("haven't", 6.50, 6.90, 0.95), ("got", 6.90, 7.10, 0.95), ("the", 7.10, 7.20, 0.9),
        ("words.", 7.20, 7.30, 0.9)]      # inside the fixture's NOT-IN-RAW S07 (competitor frames 190-220, 30 fps)


# ---------------------------------------------------------------------------------------------
# 1. detection
# ---------------------------------------------------------------------------------------------

def test_speech_is_two_or_more_words_heard_surely():
    assert broll.is_speech(SAID)
    assert broll.is_speech(ZEN["words"])                            # Zendaya: "I can't really explain it. I haven't..."
    assert not broll.is_speech(SAID[:1])                            # a word
    assert not broll.is_speech(ZEN["hallucination"])                # Whisper inventing "See you next time!" (unsure)
    assert not broll.is_speech([("♪", 5.0, 6.0, 0.9)])         # music


def _no_broll(speech_of) -> dict:
    cl = T.premiere_cutlist()
    comp = (0.01 * np.random.default_rng(0).standard_normal(10 * SR)).astype(np.float32)
    raw = (0.01 * np.random.default_rng(1).standard_normal(200 * SR)).astype(np.float32)
    return broll.apply_no_broll(cl, comp, raw, SR, Config(premiere=True), follow_audio=True, hints=None,
                                speech_of=speech_of)


def test_a_not_in_raw_stretch_with_speech_not_in_the_raw_is_another_video():
    asked = []

    def speech_of(t0, t1):
        asked.append((round(t0, 3), round(t1, 3)))
        return [w for w in SAID if t0 <= 0.5 * (w[1] + w[2]) < t1]
    res = _no_broll(speech_of)
    assert asked == [(6.333, 7.333)]          # only the NOT-IN-RAW stretch is asked about (not the uncertain S06)
    assert [(r["segment"], r["comp_in"], r["comp_out"]) for r in res["other_video"]] == [(7, 190, 220)]
    seg = next(s for s in res["cutlist"].segments if s.comp_in == 190)
    assert seg.type == "not_in_raw" and ex.other_video_of(seg)["words"][1][0] == "haven't"
    assert seg.label == "OTHER VIDEO – not in RAW (00:00:06:10–00:00:07:10)"
    assert not any(r["segment"] == 7 for r in res["replaced"])      # never filled with the previous clip
    # no speech there (music, nothing heard) or nothing to transcribe with: B-roll, the previous clip keeps playing
    for quiet in (lambda a, b: [], lambda a, b: None):
        res = _no_broll(quiet)
        assert res["other_video"] == []
        assert any(r["segment"] == 7 and r["how"].startswith("keeps playing") for r in res["replaced"])


def test_only_the_part_with_no_raw_audio_and_speech_is_another_video(monkeypatch):
    # S07: 190-200 speech not in the RAW, 200-210 RAW audio (the alignment found it), 210-220 no speech
    monkeypatch.setattr(broll, "audio_runs", lambda s, *a, **k: [(200, 210, 50.0)] if s.id == 7 else None)
    monkeypatch.setattr(broll, "_check_run", lambda *a, **k: (True, 0.9, 0.0))
    early = [("I", 6.34, 6.44, 0.9), ("haven't", 6.44, 6.66, 0.9)]
    res = _no_broll(lambda t0, t1: [w for w in early if t0 <= 0.5 * (w[1] + w[2]) < t1])
    assert [(r["comp_in"], r["comp_out"]) for r in res["other_video"]] == [(190, 200)]
    row = next(r for r in res["replaced"] if r["segment"] == 7)
    assert [(p["comp_in"], p["comp_out"], p["how"]) for p in row["parts"]] == [
        (190, 200, "other video"), (200, 210, "audio"), (210, 220, "keeps playing (short)")]
    got = [(s.type, s.comp_in, s.comp_out) for s in res["cutlist"].segments if 190 <= s.comp_in < 220]
    assert got == [("not_in_raw", 190, 200), ("raw", 200, 220)]     # B-roll follows the audio, then plays on


# ---------------------------------------------------------------------------------------------
# 2. the export: V1 and A1 empty for exactly its length, marked, no silence cut inside, the checks allow it
# ---------------------------------------------------------------------------------------------

def _other_video_cutlist():
    cl = T.premiere_cutlist()
    s7 = next(s for s in cl.segments if s.id == 7)
    s7.audio = dict(s7.audio or {}, line=None, other_video={"comp_in": 190, "comp_out": 220, "segment": 7,
                                                           "words": [list(w) for w in SAID]})
    return cl


def _export(tmp_path, cl):
    raw = (0.2 * np.sin(np.arange(200 * SR) * 0.3)).astype(np.float32)        # speech everywhere ...
    raw[int(6.9 * SR):int(7.6 * SR)] = 0.0                                     # ... but under clip 1
    cfg = Config(out_dir=str(tmp_path), premiere=True, silence_db=-20.0, min_silence=0.35, pad_before=0.08,
                 pad_after=0.12)
    plan = S.plan_premiere(cl, raw, SR, cfg)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(cl, xml, cfg, plan["ripple"])
    return plan, xml, cfg


def test_v1_and_a1_stay_empty_for_exactly_its_length_marked_and_never_silence_cut(tmp_path):
    cl = _other_video_cutlist()
    plan, xml, cfg = _export(tmp_path, cl)
    # A1 plays nothing there: a plain NOT-IN-RAW spot's silence is cut away (test_silence: 389-433); this stays whole
    assert plan["cuts"] == [(27, 54)]                               # the freeze plays its own sound: no silence
    x = ex.parse_premiere_xml(xml)
    (a, b), = ex.other_video_ranges(x)
    assert b - a == 60                                              # 30 competitor frames at 60 fps, exactly
    assert not any(it["start"] < b and it["end"] > a for it in x["clips"] + x["audio"]
                   if it["start"] >= 0 and it["end"] >= 0)
    m = next(m for m in x["markers"] if m["name"].startswith("OTHER VIDEO"))
    assert m["name"] == "OTHER VIDEO – not in RAW (00:00:05:53–00:00:06:53)"   # where it is in the edit
    assert "Competitor 00:00:06:10-00:00:07:10" in m["comment"] and "I haven't got the words." in m["comment"]
    v = ex.validate_premiere_exports(cl, xml, None, cfg, plan["ripple"])
    assert v["ok"], v["errors"]
    assert v["other_video"] == [{"segment": "S07", "in": a, "out": b, "name": m["name"]}]
    # the gap, audio and link checks allow the stretch and list it (task 3); the person check does too (test_speakers)
    tag = "OTHER VIDEO 00:00:05:53-00:00:06:53 (1.00 s)"
    assert len(v["gap_exceptions"]) == 1 and v["gap_exceptions"][0].startswith(tag)
    assert sum(e.startswith(tag) for e in v["audio_exceptions"]) == 1
    assert sum(e.startswith(tag) for e in v["link_exceptions"]) == 1 and v["link_problems"] == []


def _edit_marker(xml: Path, dest: Path, **vals) -> Path:
    tree = ET.parse(str(xml))
    mk = next(m for m in tree.getroot().iter("marker") if (m.findtext("name") or "").startswith("OTHER VIDEO"))
    for k, v in vals.items():
        mk.find(k).text = str(v)
    tree.write(str(dest), encoding="utf-8", xml_declaration=True)
    return dest


def test_the_check_fails_a_stretch_cut_shorter_or_played_over(tmp_path):
    cl = _other_video_cutlist()
    plan, xml, cfg = _export(tmp_path, cl)
    assert ex.premiere_other_video_problems(xml, [("S07", 60)]) == []
    short = ex.premiere_other_video_problems(_edit_marker(xml, tmp_path / "short.xml", out=400), [("S07", 60)])
    assert short == ["S07 at 00:00:05:53: 47 frame(s) left for the other video, the competitor's stretch is 60"]
    over = ex.premiere_other_video_problems(_edit_marker(xml, tmp_path / "over.xml", **{"in": 343}), [("S07", 70)])
    assert over and all("plays inside the other video's stretch" in p for p in over)     # A1 S06's audio line
    assert ex.premiere_other_video_problems(xml, []) == ["1 OTHER VIDEO marker(s), 0 stretch(es) of another video "
                                                         "in the plan"]


def test_the_flash_check_allows_a_short_stretch_of_another_video():
    items = [{"label": "S01", "start": 0, "end": 100, "in": 0, "speed": 1.0},
             {"label": "S02", "start": 105, "end": 200, "in": 105, "speed": 1.0}]
    assert shots.flash_problems(items, FPS, []) != []                 # 5 empty frames on V1: black, a flash
    gap = {"label": "OTHER VIDEO", "start": 100, "end": 105, "speed": None, "allowed": True}
    assert shots.flash_problems(items + [gap], FPS, []) == []         # left empty on purpose, filled by hand


# ---------------------------------------------------------------------------------------------
# 3. captions: the competitor's (or transcribed from its audio), timed to its audio there
# ---------------------------------------------------------------------------------------------

def test_the_stretch_s_words_and_speech_starts_are_the_competitor_s_audio(tmp_path):
    cl = _other_video_cutlist()
    plan, xml, cfg = _export(tmp_path, cl)
    comp = (0.002 * np.random.default_rng(2).standard_normal(10 * SR)).astype(np.float32)
    for _, s0, s1, _ in SAID[:1] + SAID[1:2]:
        n0, n1 = int(s0 * SR), int(s1 * SR)
        comp[n0:n1] += (0.3 * np.sin(np.arange(n1 - n0) * 0.3)).astype(np.float32)
    ctx = types.SimpleNamespace(broll={"cutlist": cl}, cutlist=cl, comp_fps=cl.comp_fps, comp_audio=comp,
                                audio_sr=SR, cfg=cfg)
    (st,) = C.other_video_stretches(ctx, xml, FPS)
    a = st["a"] / 60.0                                              # the stretch in the edit (competitor 6.333 s)
    assert (st["segment"], st["b"] - st["a"]) == (7, 60)
    assert [w[0] for w in st["words"]] == ["I", "haven't", "got", "the", "words."]
    assert abs(st["words"][0][1] - (a + 6.40 - 190 / 30)) < 1e-6    # competitor time -> edit time, one for one
    assert min(abs(o - st["words"][0][1]) for o in st["onsets"]) <= 0.03     # where its sound starts


def test_competitor_captions_stay_on_their_side_and_keep_the_stretch_s_sentences():
    ov = [{"a": 289, "b": 619}]
    caps = [C.Caption("The Billy Elliot one", 230, 295, "competitor"), C.Caption("I can't", 610, 640, "competitor")]
    assert C.clip_to_stretches(caps, ov) == 2
    assert [(c.start, c.end) for c in caps] == [(230, 289), (619, 640)]       # its screen lagged the cut a little
    # Zendaya: the words as said keep their sentence ends -- "it." is no lone weak word to join "I haven't got"
    base = 289 / 60 - ZEN["stretch"][0] / ZEN["fps"]
    words = [C.Word(C.clean_text(t), a + base, b + base, p, t) for t, a, b, p in ZEN["words"]]
    caps = [C.Caption(t, (a - ZEN["stretch"][0]) * 2 + 289, (b - ZEN["stretch"][0]) * 2 + 289, "competitor")
            for t, a, b in ZEN["captions"]]
    out, _ = R.enforce(caps, FPS, "competitor", words)
    assert [c.text for c in out] == ["I can't really", "explain it", "I haven't got", "the words"]


def test_my_audio_is_transcribed_without_the_stretch_and_put_back_in_place():
    y = np.arange(10 * 16000, dtype=np.float32)
    mine, held = C.without_stretches(y, [{"a": 120, "b": 240}], FPS)          # 2 s - 4 s taken out
    assert len(mine) == 8 * 16000 and held == [(2.0, 2.0)] and mine[2 * 16000] == y[4 * 16000]
    ws = C.restore_times([C.Word("one", 1.0, 1.5), C.Word("I", 2.5, 2.7)], held)
    assert [(w.start, w.end) for w in ws] == [(1.0, 1.5), (4.5, 4.7)]


def test_a_caption_is_heard_only_when_half_its_content_words_are():
    ref = [("explain.", 11.24, 11.82), ("I", 12.24, 12.24), ("just", 12.24, 12.44), ("I", 12.44, 12.72),
           ("look", 12.72, 12.88), ("that,", 15.64, 15.92), ("I", 16.2, 16.24), ("thought", 16.24, 16.52)]
    caps = [C.Caption("explain", 674, 734, "competitor"), C.Caption("it I haven't got", 734, 852, "competitor"),
            C.Caption("that I'thought", 944, 1001, "competitor")]
    _, heard = C.spoken_starts(caps, [], [ref], FPS)
    # the competitor's own wrong caption over "it, I just": its "it" / "I" are said everywhere, "haven't got" is not;
    # one garbled content word ("I'thought") does not make a caption unheard
    assert heard == [True, False, True]


# ---------------------------------------------------------------------------------------------
# 4. the end summary
# ---------------------------------------------------------------------------------------------

def test_the_end_summary_lists_each_stretch():
    from match_cuts import cli, pipeline
    ctx = types.SimpleNamespace(comp_fps=Fraction(30), captions={"other_video": [
        {"a": 289, "b": 619, "t0": 151 / 30, "t1": 316 / 30, "how": {"copied from the competitor": 4}}]})
    m = {"name": "OTHER VIDEO – not in RAW (00:00:04:49–00:00:10:19)", "in": 289, "out": 619}
    row = pipeline.other_video_row(ctx, m, FPS)
    assert row == ("OTHER VIDEO – not in RAW (00:00:04:49–00:00:10:19): 5.50 s; competitor 00:00:05:01-"
                   "00:00:10:16 -- V1 and A1 left empty, put the other video there; captions: 4 copied from the "
                   "competitor")
    text = cli.format_summary({"ok": True, "checklist": {"other_video": [row]}}, "/tmp/run")
    assert "Other video (not in RAW), left empty on purpose: 1" in text and row in text
