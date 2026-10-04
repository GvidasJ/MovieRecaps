"""caption_recheck.py: unclear words of the edit's transcript transcribed again from the source and settled.

The transcriptions are stand-ins (a fixed script per source second), so no model is needed: what is tested is which
words count as unsure, how the source is mapped onto the edit's timeline (the edit's own audio map), and how the two
versions and the competitor's caption are weighed -- including the spots left unclear and listed.
"""
from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from match_cuts import caption_recheck as R
from match_cuts import captions as C
from match_cuts.model import Cutlist, Segment

SR = R.SR


def W(text: str, t0: float, t1: float, p: float = 0.95) -> C.Word:
    return C.Word(C.clean_text(text), t0, t1, p, text)


def _cutlist() -> Cutlist:
    """Two shots from the RAW (edit 0-2 s = RAW 10-12 s, edit 2-4 s = RAW 40-42 s), then a NOT-IN-RAW placeholder."""
    comp = {"file": "competitor.mp4", "width": 1080, "height": 1920, "fps": "30/1", "frames": 150}
    raw = {"file": "", "file_abs": "", "width": 1920, "height": 1080, "fps": "30/1", "frames": 3000,
           "has_audio": True, "audio_sample_rate": SR, "audio_channels": 1}
    segs = [Segment(id=1, type="raw", comp_in=0, comp_out=60, raw_in_seconds=10.0, speed=1.0),
            Segment(id=2, type="raw", comp_in=60, comp_out=120, raw_in_seconds=40.0, speed=1.0),
            Segment(id=3, type="not_in_raw", comp_in=120, comp_out=150, label="MISSING")]
    return Cutlist(1, comp, raw, {"mode": "match", "zones": [], "captions": []}, segs)


def test_the_source_map_is_the_edits_own_audio_map():
    cl = _cutlist()
    ps = R.pieces_from_cutlist(cl)
    assert [(round(p.t0, 3), round(p.t1, 3), round(p.src0, 3), p.v) for p in ps] == [(0, 2, 10, 1), (2, 4, 40, 1)]
    assert R.cut_times(ps, 5.0) == [2.0, 4.0]                       # the cut, and the audio stopping at the placeholder
    from match_cuts.render_preview import build_audio
    raw = np.arange(60 * SR, dtype=np.float32) / SR                 # sample value = its RAW second
    y = build_audio(cl, raw, SR)
    for t in (0.5, 1.9, 2.1, 3.5):
        p = R.piece_at(ps, t)
        assert abs(y[int(t * SR)] - p.src(t)) < 1e-3              # the map plays exactly what build_audio plays


def test_unsure_words_low_confidence_music_under_them_or_cut_at_an_edit_point():
    rng = np.random.default_rng(1)
    y = np.zeros(6 * SR, np.float32)
    for a, b in ((0.2, 0.6), (0.8, 1.2), (4.0, 4.4), (4.6, 5.0)):            # four spoken words
        y[int(a * SR):int(b * SR)] = 0.3 * np.sin(np.arange(int(b * SR) - int(a * SR)) * 0.2)
    y[int(2.5 * SR):] += 0.2 * rng.standard_normal(int(3.5 * SR)).astype(np.float32)   # music / noise from 2.5 s
    words = [W("so", 0.2, 0.6), W("what", 0.8, 1.2, 0.3), W("sat", 1.9, 2.1), W("here", 4.0, 4.4),
             W("now", 4.6, 5.0)]
    got = R.unsure_words(words, y, cuts=[2.0])
    assert sorted(got) == [1, 2, 3, 4]
    assert got[1] == ["low confidence (0.30)"] and got[2] == ["an edit point cuts into it"]
    assert "music / noise under it" in got[3][0]
    assert R.unsure_words(words, y, cuts=[2.0], only=[(0.0, 1.5)]) == {1: ["low confidence (0.30)"]}


class Source:
    """A stand-in source: its 'transcription' is a fixed script of (word, source start, source end, confidence);
    transcribing a window returns the script's words inside it, timed from the window's start."""

    def __init__(self, script):
        self.script = script
        self.windows: list[tuple[float, float]] = []
        self._at = 0.0

    def get(self, s0: float, s1: float):
        self.windows.append((s0, s1))
        self._at = s0
        return np.ones(int((s1 - s0) * SR), np.float32), s0

    def transcribe(self, y):
        s0, s1 = self._at, self._at + len(y) / SR
        return [W(t, a - s0, b - s0, p) for t, a, b, p in self.script if a >= s0 and b <= s1]


PIECES = [R.Piece(0.0, 2.0, 10.0), R.Piece(2.0, 4.0, 40.0)]       # as _cutlist()


def test_a_misheard_word_takes_the_more_confident_source_version_mapped_onto_the_edit():
    words = [W("so", 0.1, 0.3), W("I", 0.35, 0.45), W("was", 0.5, 0.7), W("sad", 0.75, 1.0, 0.31),
             W("at", 1.05, 1.15), W("the", 1.2, 1.3), W("back", 1.35, 1.6)]
    src = Source([("and", 9.5, 9.8, 0.9), ("so", 10.1, 10.3, 0.97), ("I", 10.35, 10.45, 0.99),
                  ("was", 10.5, 10.7, 0.98), ("sat", 10.75, 11.0, 0.93), ("at", 11.05, 11.15, 0.99),
                  ("the", 11.2, 11.3, 0.99), ("back", 11.35, 11.6, 0.97), ("of", 12.1, 12.3, 0.99)])
    out, rep = R.recheck(words, None, PIECES, src.get, src.transcribe)
    assert [w.text for w in out] == ["so", "I", "was", "sat", "at", "the", "back"]  # RAW words outside the piece: no
    assert (out[3].start, out[3].end) == (0.75, 1.0)
    assert src.windows == [(10.75 - R.CONTEXT_S, 11.0 + R.CONTEXT_S)]              # the whole sentence around it
    assert rep["unsure"] == 1 and rep["rechecked"] == 1 and rep["changed"] == 1 and not rep["unclear"]
    assert rep["changes"] == [{"time": 0.75, "end": 1.0, "from": "sad", "to": "sat", "why": "more confident",
                               "conf": [0.31, 0.93]}]


def test_a_word_cut_off_at_the_edit_point_is_heard_whole_in_the_raw_and_keeps_the_part_played():
    words = [W("it", 1.4, 1.5), W("was", 1.55, 1.7), W("comp", 1.75, 2.0, 0.62), W("then", 2.2, 2.4)]
    src = Source([("it", 11.4, 11.5, 0.99), ("was", 11.55, 11.7, 0.99), ("completely", 11.75, 12.4, 0.96),
                  ("a", 12.45, 12.5, 0.99)])
    out, rep = R.recheck(words, np.zeros(5 * SR, np.float32), PIECES, src.get, src.transcribe)
    assert [w.text for w in out] == ["it", "was", "completely", "then"]
    assert (out[2].start, out[2].end) == (1.75, 2.0)                # the RAW word clipped to the part the edit plays
    assert rep["changed"] == 1 and rep["changes"][0]["from"] == "comp"


def test_two_transcriptions_that_agree_confirm_the_word():
    words = [W("Deadpool", 0.1, 0.6, 0.2), W("is", 0.7, 0.8)]
    src = Source([("Deadpool", 10.1, 10.6, 0.9), ("is", 10.7, 10.8, 0.99)])
    out, rep = R.recheck(words, None, PIECES, src.get, src.transcribe)
    assert [w.text for w in out] == ["Deadpool", "is"] and out[0].prob == 0.9
    assert rep["rechecked"] == 1 and rep["changed"] == 0 and not rep["unclear"]


def test_the_competitors_clear_caption_is_the_third_opinion():
    words = [W("Peter", 0.1, 0.4, 0.45), W("Parker", 0.45, 0.8, 0.45)]
    src = Source([("Parker", 10.1, 10.4, 0.6), ("Peter", 10.45, 10.8, 0.6)])         # more confident, but wrong
    spans = [{"comp_in": 0, "comp_out": 30, "ocr": "PETER PARKER", "score": 0.97, "agreement": 1.0}]
    out, rep = R.recheck(words, None, PIECES, src.get, src.transcribe, captions=R.clear_captions(spans, Fraction(30)))
    assert [w.text for w in out] == ["Peter", "Parker"] and rep["changed"] == 0 and not rep["unclear"]
    unclear = [{"comp_in": 0, "comp_out": 30, "ocr": "PETER PARKER", "score": 0.4, "agreement": 1.0}]
    out, rep = R.recheck(words, None, PIECES, src.get, src.transcribe, captions=R.clear_captions(unclear, Fraction(30)))
    assert [w.text for w in out] == ["Parker", "Peter"]             # a caption not read clearly is no opinion


def test_still_unclear_keeps_the_best_version_and_lists_the_alternatives():
    words = [W("yes", 0.2, 0.5, 0.14), W("Avengers", 0.55, 1.0)]
    src = Source([("guess", 10.2, 10.5, 0.3), ("Avengers", 10.55, 11.0, 0.98)])
    out, rep = R.recheck(words, None, PIECES, src.get, src.transcribe)
    assert [w.text for w in out] == ["guess", "Avengers"]            # the more confident one, still unsure
    assert rep["changed"] == 1 and len(rep["unclear"]) == 1
    u = rep["unclear"][0]
    assert u["text"] == "guess" and u["time"] == 0.2 and "low confidence (0.14)" in u["why"]
    assert [(a["source"], a["text"], a["conf"]) for a in u["alternatives"]] == [
        ("edit (small.en)", "yes", 0.14), ("RAW (medium.en)", "guess", 0.3)]


def test_words_only_one_version_heard():
    words = [W("so", 0.1, 0.3, 0.4), W("what", 1.0, 1.2)]
    src = Source([("so", 10.1, 10.3, 0.9), ("anyway", 10.4, 10.9, 0.95), ("what", 11.0, 11.2, 0.99)])
    out, rep = R.recheck(words, None, PIECES, src.get, src.transcribe)
    assert [w.text for w in out] == ["so", "anyway", "what"] and rep["changed"] == 1     # clear: added
    src = Source([("so", 10.1, 10.3, 0.9), ("um", 10.4, 10.9, 0.4), ("what", 11.0, 11.2, 0.99)])
    out, rep = R.recheck(words, None, PIECES, src.get, src.transcribe)
    assert [w.text for w in out] == ["so", "what"] and rep["unclear"][0]["alternatives"][1]["text"] == "um"
    words = [W("so", 0.1, 0.3, 0.4), W("mm", 0.4, 0.6, 0.3), W("what", 1.0, 1.2)]
    src = Source([("so", 10.1, 10.3, 0.9), ("what", 11.0, 11.2, 0.99)])
    out, rep = R.recheck(words, None, PIECES, src.get, src.transcribe)
    assert [w.text for w in out] == ["so", "mm", "what"] and rep["unclear"][0]["text"] == "mm"   # never dropped


def test_words_with_no_source_audio_are_listed_not_guessed():
    words = [W("hey", 4.2, 4.5, 0.2)]                               # over the NOT-IN-RAW placeholder
    src = Source([])
    out, rep = R.recheck(words, None, PIECES, src.get, src.transcribe)
    assert out == words and not src.windows and rep["rechecked"] == 0
    assert "not rechecked (no RAW audio there)" in rep["unclear"][0]["why"]


def test_the_caption_stage_rechecks_voice_mode_and_says_how_many_changed(tmp_path, monkeypatch):
    import types

    import soundfile as sf

    from match_cuts import pipeline, transcribe
    from match_cuts.common import Cache
    from match_cuts.config import Config
    vo = tmp_path / "vo.wav"                                        # the voice-over is its own source
    sf.write(str(vo), (0.2 * np.sin(np.arange(4 * SR) * 0.05)).astype(np.float32), SR)
    edit = [W("so", 0.1, 0.3), W("I", 0.35, 0.45), W("was", 0.5, 0.7), W("sad", 0.75, 1.0, 0.31),
            W("at", 1.05, 1.15), W("the", 1.2, 1.3), W("back", 1.35, 1.6)]
    src = Source([("so", 0.1, 0.3, 0.97), ("I", 0.35, 0.45, 0.99), ("was", 0.5, 0.7, 0.98), ("sat", 0.75, 1.0, 0.93),
                  ("at", 1.05, 1.15, 0.99), ("the", 1.2, 1.3, 0.99), ("back", 1.35, 1.6, 0.97)])
    models: list[str] = []

    def fake(y, sr, model, language, cache, hints=(), aligned=True):
        models.append(model)
        return list(edit) if model == "small.en" else src.transcribe(y)
    monkeypatch.setattr(transcribe, "available", lambda: None)
    monkeypatch.setattr(transcribe, "transcribe_words", fake)
    monkeypatch.setattr(R, "audio_source", lambda y, sr: src.get)
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"), premiere=True, captions="voice")
    cfg.voiceover = str(vo)
    cfg.caption_model, cfg.caption_recheck_model, cfg.caption_check_model = "small.en", "medium.en", "none"
    info = types.SimpleNamespace(path="", file_hash="x", width=1080, height=1920, display_width=1080,
                                 display_height=1920)
    ctx = types.SimpleNamespace(cfg=cfg, comp_info=info, comp_fps=Fraction(30), n_comp=120, paths={}, broll=None,
                                cutlist=types.SimpleNamespace(layout={}, segments=[]), cache=Cache(cfg.work),
                                raw_audio=None, audio_sr=SR, warn=lambda m: None)
    res = C.run_captions(ctx)
    assert models == ["small.en", "medium.en"]                      # the bigger model once, for the unclear spot
    text = " ".join(b["text"] for b in C.read_srt(res["path"]))
    assert "sat" in text and "sad" not in text
    rc = res["recheck"]
    assert (rc["source"], rc["model"], rc["changed"], rc["unclear"]) == ("voice-over", "medium.en", 1, [])
    assert rc["rechecked"] >= 1 and not [f for f in res["flags"] if f["kind"] == "possible mis-transcription"]
    ctx.captions = res
    assert pipeline.hand_checks(ctx)["caption_recheck"] == [
        f"{rc['rechecked']} words rechecked against the voice-over (medium.en), 1 changed"]
    from match_cuts import report
    md = "\n".join(report._captions(ctx))
    assert "Unclear words rechecked against the voice-over" in md and "| sad | sat | more confident |" in md


def test_competitor_in_capitals_captions_the_words_heard_in_my_style_and_rechecks_them(tmp_path, monkeypatch):
    """A competitor captioning in capitals (not the user's way: caption_style.competitor_style) -- the captions are
    made from the words heard, in the user's style, and the unsure words are rechecked from the source; a third
    reading, the second model's, decides where it hears the reduced form ("gonna")."""
    import types

    import soundfile as sf

    from match_cuts import caption_ocr, transcribe
    from match_cuts.common import Cache
    from match_cuts.config import Config
    vo = tmp_path / "vo.wav"
    sf.write(str(vo), (0.2 * np.sin(np.arange(4 * SR) * 0.05)).astype(np.float32), SR)
    spans = [{"comp_in": 0, "comp_out": 30, "ocr": "SO I WAS", "score": 0.97, "agreement": 1.0},
             {"comp_in": 30, "comp_out": 60, "ocr": "", "score": 0.0, "agreement": 0.0},       # unreadable
             {"comp_in": 60, "comp_out": 90, "ocr": "BACK THERE", "score": 0.97, "agreement": 1.0},
             {"comp_in": 90, "comp_out": 120, "ocr": "GOING TO GO", "score": 0.97, "agreement": 1.0}]
    edit = [W("so", 0.1, 0.3, 0.9), W("I", 0.35, 0.45), W("was", 0.5, 0.7), W("sad", 1.2, 1.5, 0.31),
            W("back", 2.1, 2.4), W("there", 2.5, 2.8), W("going", 3.0, 3.15), W("to", 3.16, 3.25),
            W("go", 3.3, 3.6)]
    second = edit[:6] + [W("gonna", 3.0, 3.25), W("go", 3.3, 3.6)]
    src = Source([("so", 0.1, 0.3, 0.9), ("I", 0.35, 0.45, 0.99), ("was", 0.5, 0.7, 0.98), ("sat", 1.2, 1.5, 0.93),
                  ("back", 2.1, 2.4, 0.95), ("there", 2.5, 2.8, 0.97)])
    models: list[str] = []

    def fake(y, sr, model, language=None, cache=None, hints=(), aligned=True):
        models.append(model)
        return list(edit) if model == "large-v3" else list(second) if model == "large-v3-turbo" else src.transcribe(y)
    monkeypatch.setattr(transcribe, "available", lambda: None)
    monkeypatch.setattr(transcribe, "transcribe_words", fake)
    monkeypatch.setattr(R, "audio_source", lambda y, sr: src.get)
    monkeypatch.setattr(caption_ocr, "available", lambda: None)
    monkeypatch.setattr(caption_ocr, "caption_band", lambda layout, wh: object())
    monkeypatch.setattr(C, "_read_spans", lambda ctx, layout, fps: {"spans": spans, "frames_read": 120})
    from match_cuts import align
    monkeypatch.setattr(align, "available", lambda: "not in this test")
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"), premiere=True,
                 captions="competitor")
    cfg.voiceover = str(vo)
    cfg.caption_recheck_model = "medium.en"                         # the stand-in for the source's transcription
    info = types.SimpleNamespace(path="", file_hash="x", width=1080, height=1920, display_width=1080,
                                 display_height=1920)
    ctx = types.SimpleNamespace(cfg=cfg, comp_info=info, comp_fps=Fraction(30), n_comp=120,
                                cutlist=types.SimpleNamespace(layout={}), cache=Cache(cfg.work), raw_audio=None,
                                audio_sr=SR, warn=lambda m: None)
    res = C.run_captions(ctx)
    assert res["mode"] == "competitor" and not res["competitor_style"]["follow"] and res["styled"]
    text = " ".join(b["text"] for b in C.read_srt(res["path"]))
    assert "sat" in text and "sad" not in text and "gonna go" in text and "going" not in text
    assert res["second_opinion"]["spoken_form"][0]["large-v3-turbo"] == "gonna"
    rc = res["recheck"]
    assert rc["changed"] == 1 and rc["changes"][0]["from"] == "sad"
    assert models[:2] == ["large-v3", "large-v3-turbo"]


def test_overlapping_raw_windows_of_different_pieces_are_transcribed_once():
    pieces = [R.Piece(0.0, 2.0, 10.0), R.Piece(2.0, 4.0, 12.5)]    # two shots half a second apart in the RAW
    words = [W("I", 0.5, 0.6), W("sad", 0.7, 1.0, 0.3), W("then", 2.6, 2.9, 0.3), W("went", 3.0, 3.3)]
    src = Source([("I", 10.5, 10.6, 0.99), ("sat", 10.7, 11.0, 0.95), ("then", 13.1, 13.4, 0.9),
                  ("went", 13.5, 13.8, 0.99)])
    out, rep = R.recheck(words, None, pieces, src.get, src.transcribe)
    assert src.windows == [(10.7 - R.CONTEXT_S, 13.4 + R.CONTEXT_S)]                # one transcription for both
    assert [w.text for w in out] == ["I", "sat", "then", "went"] and rep["rechecked"] == 2 and rep["changed"] == 1


def test_a_source_version_missing_a_word_never_wins_on_a_common_word_or_a_small_margin():
    """Seen on a real clip: the RAW piece ends inside 'going', so the RAW's version lacks it ('Brad Pitt's to do
    this'); the caption agrees only with the edit's version in context. And 'gonna' / 'going to' are the same."""
    cap = R.clear_captions([{"comp_in": 0, "comp_out": 60, "ocr": "BACK UP BRAD PITT'S GOING TO DO THIS?",
                             "score": 0.97, "agreement": 1.0},
                            {"comp_in": 60, "comp_out": 120, "ocr": "HE'S GOING TO PLAY VANISHER", "score": 0.97,
                             "agreement": 1.0}], Fraction(30))
    words = [W("Brad", 0.2, 0.4), W("Pitt's", 0.45, 0.7), W("gonna", 0.75, 0.95, 0.45), W("do", 1.0, 1.1),
             W("this", 1.15, 1.4), W("he's", 2.1, 2.3), W("gonna", 2.35, 2.5, 0.45), W("play", 2.55, 2.8, 0.45),
             W("Vanisher", 2.85, 3.4)]
    src = Source([("Brad", 10.2, 10.4, 0.99), ("Pitt's", 10.45, 10.7, 0.99), ("to", 10.85, 10.95, 0.99),
                  ("do", 11.0, 11.1, 0.99), ("this", 11.15, 11.4, 0.99),
                  ("he's", 40.1, 40.3, 0.99), ("going", 40.35, 40.42, 0.99), ("to", 40.43, 40.5, 0.99),
                  ("Vanisher", 40.85, 41.4, 0.99)])
    out, rep = R.recheck(words, None, PIECES, src.get, src.transcribe, captions=cap)
    assert [w.text for w in out] == [w.text for w in words] and rep["changed"] == 0
    src = Source([("Brad", 10.2, 10.4, 0.9), ("Pitt's", 10.45, 10.7, 0.9), ("going", 10.75, 10.85, 0.9),
                  ("to", 10.86, 10.95, 0.9), ("do", 11.0, 11.1, 0.9), ("this", 11.15, 11.4, 0.9)])
    out, rep = R.recheck(words[:5], None, PIECES, src.get, src.transcribe)
    assert [w.text for w in out] == ["Brad", "Pitt's", "gonna", "do", "this"] and rep["changed"] == 0
    assert out[2].prob == 0.9 and not rep["unclear"]                 # the same words: confirmed


# ---- the two best models (task 4): where they disagree -----------------------------------------------------------

def test_two_models_the_spoken_form_the_caption_and_what_stays_open():
    a = [W("we're", 0.0, 0.2), W("going", 0.2, 0.4), W("to", 0.4, 0.5), W("get", 0.5, 0.7),
         W("It's", 1.0, 1.2), W("going", 1.2, 1.4), W("to", 1.4, 1.5), W("be", 1.5, 1.6), W("great", 1.6, 2.0),
         W("Brad", 2.5, 2.7), W("Pitt", 2.7, 2.9), W("doing", 2.9, 3.1), W("it", 3.1, 3.2)]
    b = [W("we're", 0.0, 0.2), W("gonna", 0.2, 0.5), W("get", 0.5, 0.7),
         W("This", 1.0, 1.1), W("is", 1.1, 1.2), W("gonna", 1.2, 1.5), W("be", 1.5, 1.6), W("great", 1.6, 2.0),
         W("Brad", 2.5, 2.7), W("Pitt", 2.7, 2.9), W("one", 2.9, 3.2)]
    caps = R.clear_captions([{"comp_in": 27, "comp_out": 63, "ocr": "this is going to be great", "score": 0.97,
                              "agreement": 1.0}], Fraction(30))
    out, rep, unsure = R.resolve_two(a, b, "large-v3", "large-v3-turbo", caps)
    text = " ".join(w.text for w in out)
    assert text.startswith("we're gonna get")                       # the reduced form one model heard
    assert "This is gonna be great" in text                         # the caption agrees with the second model
    assert text.endswith("Brad Pitt doing it")                      # no caption there: the best model's, unsure
    assert [r["large-v3-turbo"] for r in rep["spoken_form"]] == ["gonna"]
    assert rep["caption"][0]["large-v3-turbo"] == "This is gonna"
    assert [r["large-v3"] for r in rep["open"]] == ["doing it"]
    assert {out[i].text for i in unsure} == {"doing", "it"} and "large-v3-turbo heard 'one'" in unsure[len(out) - 1]
    assert out == sorted(out, key=lambda w: w.start)


def test_the_screen_gives_the_reduced_form():
    words = [W("do", 0.0, 0.1), W("you", 0.1, 0.2), W("want", 0.2, 0.4), W("to", 0.4, 0.5), W("know?", 0.5, 0.8),
             W("I", 2.0, 2.1), W("want", 2.1, 2.3), W("to", 2.3, 2.4), W("go", 2.4, 2.6)]
    caps = R.clear_captions([{"comp_in": 0, "comp_out": 24, "ocr": "DO YOU WANNA KNOW", "score": 0.97,
                              "agreement": 1.0}], Fraction(30))
    out, rows = R.screen_spoken_forms(words, caps)
    assert [w.raw for w in out[:4]] == ["do", "you", "wanna", "know?"]
    assert [w.text for w in out[4:]] == ["I", "want", "to", "go"]        # nothing on screen there: as heard
    assert rows[0]["to"] == "wanna" and out[2].end == pytest.approx(0.5)


def test_a_word_the_edit_has_just_outside_a_recheck_window_is_not_added_again():
    # the source's "And" falls inside the window, the edit's own "And" just before it (their timings differ)
    words = [W("And", 15.375, 15.495), W("they're", 15.515, 15.655), W("like,", 15.655, 15.775)]
    heard = [W("And", 15.45, 15.53, 1.0), W("they're", 15.53, 15.66, 1.0), W("like", 15.66, 15.78, 1.0)]
    swaps: list = []
    rep = {"changed": 0, "changes": [], "unclear": []}
    R._decide(words, [1, 2], heard, {1: ["an edit point cuts into it"]}, {1: 0.4}, swaps, rep, None, "RAW",
              "large-v3", "large-v3")
    assert swaps == [] and rep["changes"] == []
    assert R._said_beside(W("and", 15.45, 15.53), words)
    assert not R._said_beside(W("and", 16.2, 16.3), words)            # the same word said again later: a new word


def test_the_screens_reading_replaces_the_heard_one_only_when_every_judge_prefers_it():
    words = [W("There", 0.0, 0.2), W("was", 0.2, 0.4), W("a", 0.4, 0.5), W("joke.", 0.5, 0.8), W("I", 1.0, 1.1),
             W("suggested", 1.1, 1.6)]
    spans = [{"comp_in": 0, "comp_out": 15, "ocr": "SO AS A", "score": 0.98, "agreement": 1.0},
             {"comp_in": 15, "comp_out": 27, "ocr": "JOKE", "score": 1.0, "agreement": 1.0},
             {"comp_in": 30, "comp_out": 48, "ocr": "I SUGGESTED", "score": 1.0, "agreement": 1.0},
             {"comp_in": 48, "comp_out": 60, "ocr": "*LAUGHS*", "score": 1.0, "agreement": 1.0}]
    shown = R.screen_words(spans, Fraction(30))
    assert [w.raw for w in shown] == ["SO", "AS", "A", "JOKE", "I", "SUGGESTED"]      # no action caption
    y = np.zeros(16000 * 3, np.float32)
    asked: list = []

    def judge(screen_likelier: bool):
        def run(jobs):
            asked.extend(texts for _y, texts in jobs)
            return [[0.0, 1.0 if screen_likelier else -1.0] for _ in jobs]
        return run
    out, rows = R.screen_readings(words, shown, y, [judge(True), judge(True)])
    assert [C.norm(w.text) for w in out[:4]] == ["so", "as", "a", "joke"]
    assert (out[0].start, out[1].end) == pytest.approx((0.0, 0.4))          # where the words heard were
    assert rows == [{"time": 0.0, "heard": "There was", "screen": "SO AS", "scores": [[0.0, 1.0], [0.0, 1.0]],
                     "taken": True}]
    assert asked[0] == ["there was a joke i", "so as a joke i"]                # the same words heard around them
    out, rows = R.screen_readings(words, shown, y, [judge(True), judge(False)])     # one judge against: as heard
    assert [w.text for w in out[:2]] == ["There", "was"] and rows[0]["taken"] is False


def test_the_screen_judge_hears_the_raw_where_the_edit_plays_the_phrase_in_order():
    raw = np.arange(16000 * 10, dtype=np.float32) / 16000 / 10      # sample value: its second / 10
    src = R.audio_source(raw, 16000)
    ctx = [W("a", 0.2, 0.4), W("b", 0.5, 0.9), W("c", 1.1, 1.3)]
    one_take = [R.Piece(0.0, 1.0, 5.0), R.Piece(1.0, 2.0, 6.0)]      # one take, cut in two pieces
    y = R._source_window(ctx, one_take, src)
    assert y is not None and len(y) == pytest.approx(16000 * 1.7, abs=2)       # RAW 4.9 - 6.6 s
    assert y[0] * 10 == pytest.approx(4.9, abs=1e-3)
    jump = [R.Piece(0.0, 1.0, 5.0), R.Piece(1.0, 2.0, 9.0)]                    # another place in the RAW
    assert R._source_window(ctx, jump, src) is None
    left_out = [R.Piece(0.0, 1.0, 5.0), R.Piece(1.0, 2.0, 6.5)]               # 0.5 s of the take left out
    assert R._source_window(ctx, left_out, src) is None


def test_a_stock_phrase_only_one_model_heard_is_dropped():
    a = [W("to", 24.6, 24.7), W("death.", 24.7, 25.0), W("THANKS", 25.0, 25.6), W("FOR", 25.6, 25.9),
         W("JOINING", 25.9, 26.4), W("US.", 26.4, 26.7)]
    b = [W("to", 24.6, 24.7), W("death.", 24.7, 25.0)]
    out, rep, unsure = R.resolve_two(a, b, "large-v3", "large-v3-turbo")
    assert [C.norm(w.text) for w in out] == ["to", "death"] and not unsure
    assert rep["hallucination"][0]["large-v3"].lower().split() == ["thanks", "for", "joining", "us"]
    out, rep, unsure = R.resolve_two(a, a, "large-v3", "large-v3-turbo")        # both heard it: said
    assert len(out) == 6


def test_the_screen_judge_hears_over_a_pause_the_edit_cut_out_but_not_over_speech():
    rng = np.random.default_rng(0)
    raw = np.zeros(16000 * 10, np.float32)
    for a, b in ((5.2, 5.4), (5.5, 5.9), (6.6, 6.8)):                       # the words; 5.9 - 6.6: a pause
        raw[int(a * 16000):int(b * 16000)] = 0.3 * rng.standard_normal(int((b - a) * 16000))
    src = R.audio_source(raw, 16000)
    ctx = [W("a", 0.2, 0.4), W("b", 0.5, 0.9), W("c", 1.0, 1.2)]
    pause_cut = [R.Piece(0.0, 0.95, 5.0), R.Piece(0.95, 2.0, 6.55)]         # the pause cut out
    assert R._source_window(ctx, pause_cut, src) is not None
    raw[int(6.1 * 16000):int(6.3 * 16000)] = 0.3 * rng.standard_normal(int(0.2 * 16000))   # a word in it
    assert R._source_window(ctx, pause_cut, src) is None


# ---------------------------------------------------------------------------------------------------------------------
# the learned glossary (Task 6: match_cuts learn writes it from the words the user corrected)
# ---------------------------------------------------------------------------------------------------------------------

def _scored(written_minus_heard: float):
    """A judge that finds the written reading this much more (or less) likely than the heard one."""
    def run(jobs):
        return [[0.0, written_minus_heard] for _ in jobs]
    return run


def test_a_learned_word_replaces_the_heard_one_only_where_the_audio_fits():
    words = [W("I", 0.0, 0.1), W("went", 0.1, 0.3), W("to", 0.3, 0.4), W("the", 0.4, 0.5), W("zendeya", 0.5, 1.0),
             W("interview.", 1.0, 1.6)]
    y = np.zeros(16000 * 2, np.float32)
    learned = [("zendeya", "Zendaya")]
    out, rows = R.glossary_readings(words, learned, y, [_scored(-0.5), _scored(0.2)])     # within the slack: fits
    assert [w.text for w in out] == ["I", "went", "to", "the", "Zendaya", "interview"] and rows[0]["taken"] is True
    assert (out[4].start, out[4].end) == pytest.approx((0.5, 1.0))                      # where the heard word was
    out, rows = R.glossary_readings(words, learned, y, [_scored(-3.0), _scored(0.2)])     # one model: the audio says
    assert out[4].text == "zendeya" and rows[0]["taken"] is False                       # otherwise -- never blindly
    out, rows = R.glossary_readings(words, learned, None, [_scored(1.0)])                 # no audio: no replacement
    assert out[4].text == "zendeya" and rows[0]["why"] == "no audio to check it against"


def test_a_learned_capital_is_written_that_way_without_asking_the_audio():
    words = [W("with", 0.0, 0.2), W("tom", 0.2, 0.4), W("holland,", 0.4, 0.8), W("today", 0.9, 1.2)]
    out, rows = R.glossary_readings(words, [("tom holland", "Tom Holland")], None, [])
    assert [w.text for w in out] == ["with", "Tom", "Holland", "today"] and out[2].raw == "Holland,"
    assert rows == [{"time": 0.2, "heard": "tom holland", "written": "Tom Holland", "kind": "capitals", "taken": True}]


def test_the_screen_showing_a_learned_correction_needs_only_to_fit_the_audio():
    words = [W("I", 0.0, 0.1), W("met", 0.1, 0.3), W("zendeya", 0.3, 0.8), W("today", 0.9, 1.3)]
    spans = [{"comp_in": 0, "comp_out": 40, "ocr": "I MET ZENDAYA TODAY", "score": 0.99, "agreement": 1.0}]
    shown = R.screen_words(spans, Fraction(30))
    y = np.zeros(16000 * 2, np.float32)
    out, rows = R.screen_readings(words, shown, y, [_scored(-0.4), _scored(-0.4)])        # a little less likely
    assert out[2].text == "zendeya" and rows[0]["taken"] is False                       # unknown: as heard
    out, rows = R.screen_readings(words, shown, y, [_scored(-0.4), _scored(-0.4)], glossary=[("zendeya", "Zendaya")])
    assert C.norm(out[2].text) == "zendaya" and rows[0]["taken"] is True and rows[0]["glossary"] is True
