"""Run 19: a stretch the best speech model skipped is heard again, without the hints
(caption_recheck.skipped / fill_skips).

Whisper large-v3 can jump over half a minute after a repeated word (e1's check-all run: "a 911 2012 2012", then
nothing until "5 4 3 2 1" -- 60 words the second model heard, the captions there left as *...*); the caption hints
(Whisper's prompt, "AI, MJ, MCU") trigger it: on e1's edit large-v3 heard 69 words with them, 136 without. The second
model's words only show where to listen: the sound is transcribed again by the best model without the hints, and
its words inside the stretch go in. Where nothing was skipped, the words stay exactly as they were. The transcriptions are
stand-ins (no model needed).
"""
from __future__ import annotations

import numpy as np

from match_cuts import caption_recheck as R
from match_cuts import captions as C

SR = R.SR


def W(text: str, t0: float, t1: float, p: float = 0.95) -> C.Word:
    return C.Word(C.clean_text(text), t0, t1, p, text)


def _talk(t0: float, t1: float, words: str) -> list[C.Word]:
    ws = words.split()
    step = (t1 - t0) / len(ws)
    return [W(w, t0 + i * step, t0 + (i + 0.8) * step) for i, w in enumerate(ws)]


BEFORE = _talk(0.0, 2.0, "you want a Porsche")
AFTER = _talk(15.0, 17.0, "five four three two one")
GAP = _talk(4.0, 12.0, "well if you don't want it you can go to the Empire State Building")       # 14 words
SECOND = BEFORE + _talk(4.0, 12.0, "well if you do not want it you can go to the Empire State building") + AFTER


def test_a_stretch_only_the_second_model_heard_is_a_skip():
    a = BEFORE + AFTER
    (t0, t1, n), = R.skipped(a, SECOND)
    assert (round(t0, 2), n) == (4.0, 15) and 11.0 < t1 <= 12.0
    assert R.skipped(a, BEFORE + SECOND[4:11] + AFTER) == []             # too few words: no skip
    assert R.skipped(a, BEFORE + _talk(4.0, 8.0, "a b c d e f g h i j") + AFTER) == []   # too short: no skip
    assert R.skipped(BEFORE + GAP + AFTER, SECOND) == []                 # both heard it


def test_the_skipped_stretch_is_heard_again_by_the_best_model_alone():
    a = BEFORE + AFTER
    y = np.zeros(20 * SR, np.float32)
    asked = []

    def hear(whole):                                   # the best model on the whole sound again, without the hints
        asked.append(len(whole) / SR)
        return BEFORE + GAP + [W("one", 12.5, 12.7)] + AFTER        # "one" at 12.5 s: outside the stretch
    out, rows = R.fill_skips(a, SECOND, y, SR, hear)
    assert asked == [20.0]                                              # once, the whole sound
    text = " ".join(w.text for w in out)
    assert text == ("you want a Porsche well if you don't want it you can go to the Empire State Building "
                    "five four three two one")                          # the best model's words, not the second's
    assert out == sorted(out, key=lambda w: w.start)
    assert [round(w.start, 2) for w in out[4:6]] == [4.0, round(4.0 + 8.0 / 14, 2)]   # on the edit's own time
    (r,) = rows
    assert (r["second_heard"], r["heard_again"]) == (15, 14) and r["text"].startswith("well if you")


def test_nothing_skipped_nothing_changes_and_nothing_is_asked():
    a = BEFORE + GAP + AFTER

    def hear(whole):
        raise AssertionError("not asked")
    out, rows = R.fill_skips(a, SECOND, np.zeros(20 * SR, np.float32), SR, hear)
    assert rows == [] and out == a


def test_heard_nothing_again_or_a_made_up_phrase_adds_nothing():
    a = BEFORE + AFTER
    y = np.zeros(20 * SR, np.float32)
    out, rows = R.fill_skips(a, SECOND, y, SR, lambda whole: [])
    assert out == a and rows[0]["heard_again"] == 0                     # the second model's words never taken
    out, rows = R.fill_skips(a, SECOND, y, SR, lambda whole: BEFORE + _talk(5.0, 7.0, "thanks for watching") + AFTER)
    assert out == a and rows[0]["heard_again"] == 0


def test_the_caption_stage_hears_the_skipped_stretch_again_without_the_hints(tmp_path, monkeypatch):
    """The whole caption step (voice mode, a 20 s voice-over): large-v3 with the hints skips 3-14 s, large-v3-turbo
    hears it; heard again by large-v3 WITHOUT the hints, the captions hold it, and the run says so."""
    import types
    from fractions import Fraction

    import soundfile as sf

    from match_cuts import align, transcribe
    from match_cuts.common import Cache
    from match_cuts.config import Config
    vo = tmp_path / "vo.wav"
    sf.write(str(vo), (0.2 * np.sin(np.arange(20 * SR) * 0.05)).astype(np.float32), SR)
    calls: list[tuple[str, float, tuple]] = []

    def fake(y, sr, model, language=None, cache=None, hints=(), aligned=True):
        calls.append((model, round(len(y) / sr, 1), tuple(hints)))
        if model == "large-v3-turbo":
            return list(SECOND)
        return BEFORE + AFTER if hints else BEFORE + GAP + AFTER    # large-v3: the hints make it skip
    monkeypatch.setattr(transcribe, "available", lambda: None)
    monkeypatch.setattr(transcribe, "transcribe_words", fake)
    monkeypatch.setattr(C, "caption_hints", lambda: ["AI", "MJ", "MCU"])
    monkeypatch.setattr(align, "available", lambda: "not in this test")
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"), premiere=True, captions="voice")
    cfg.voiceover = str(vo)
    cfg.caption_model, cfg.caption_check_model, cfg.caption_recheck_model = "large-v3", "large-v3-turbo", "none"
    info = types.SimpleNamespace(path="", file_hash="x", width=1080, height=1920, display_width=1080,
                                 display_height=1920)
    ctx = types.SimpleNamespace(cfg=cfg, comp_info=info, comp_fps=Fraction(30), n_comp=600, paths={}, broll=None,
                                cutlist=types.SimpleNamespace(layout={}, segments=[]), cache=Cache(cfg.work),
                                raw_audio=None, audio_sr=SR, warn=lambda m: None)
    res = C.run_captions(ctx)
    assert [(m, h) for m, _s, h in calls[:3]] == [("large-v3", ("AI", "MJ", "MCU")),
                                                 ("large-v3-turbo", ("AI", "MJ", "MCU")), ("large-v3", ())]
    assert calls[2][1] == 20.0                                           # the whole sound again
    text = " ".join(b["text"] for b in C.read_srt(res["path"])).lower()
    assert "empire state building" in text and "porsche" in text and "two one" in text
    (row,) = res["heard_again"]
    assert row["heard_again"] == 14 and res["transcriber"]["words"] == 4 + 14 + 5
