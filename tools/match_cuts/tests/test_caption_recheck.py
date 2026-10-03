"""caption_recheck.py: unclear words of the edit's transcript transcribed again from the source and settled.

The transcriptions are stand-ins (a fixed script per source second), so no model is needed: what is tested is which
words count as unsure, how the source is mapped onto the edit's timeline (the edit's own audio map), and how the two
versions and the competitor's caption are weighed -- including the spots left unclear and listed.
"""
from __future__ import annotations

from fractions import Fraction

import numpy as np

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

    def fake(y, sr, model, language, cache):
        models.append(model)
        return list(edit) if model == "small.en" else src.transcribe(y)
    monkeypatch.setattr(transcribe, "available", lambda: None)
    monkeypatch.setattr(transcribe, "transcribe_words", fake)
    monkeypatch.setattr(R, "audio_source", lambda y, sr: src.get)
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"), premiere=True, captions="voice")
    cfg.voiceover = str(vo)
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


def test_competitor_mode_rechecks_only_the_words_of_captions_it_could_not_read(tmp_path, monkeypatch):
    import types

    import soundfile as sf

    from match_cuts import caption_ocr, transcribe
    from match_cuts.common import Cache
    from match_cuts.config import Config
    vo = tmp_path / "vo.wav"
    sf.write(str(vo), (0.2 * np.sin(np.arange(4 * SR) * 0.05)).astype(np.float32), SR)
    spans = [{"comp_in": 0, "comp_out": 30, "ocr": "SO I WAS", "score": 0.97, "agreement": 1.0},
             {"comp_in": 30, "comp_out": 60, "ocr": "", "score": 0.0, "agreement": 0.0},       # unreadable
             {"comp_in": 60, "comp_out": 90, "ocr": "BACK THERE", "score": 0.97, "agreement": 1.0}]
    edit = [W("so", 0.1, 0.3, 0.2), W("I", 0.35, 0.45), W("was", 0.5, 0.7), W("sad", 1.2, 1.5, 0.31),
            W("back", 2.1, 2.4, 0.3), W("there", 2.5, 2.8)]
    src = Source([("so", 0.1, 0.3, 0.9), ("I", 0.35, 0.45, 0.99), ("was", 0.5, 0.7, 0.98), ("sat", 1.2, 1.5, 0.93),
                  ("back", 2.1, 2.4, 0.95), ("there", 2.5, 2.8, 0.97)])
    monkeypatch.setattr(transcribe, "available", lambda: None)
    monkeypatch.setattr(transcribe, "transcribe_words",
                        lambda y, sr, model, *a, **k: list(edit) if model == "small.en" else src.transcribe(y))
    monkeypatch.setattr(R, "audio_source", lambda y, sr: src.get)
    monkeypatch.setattr(caption_ocr, "available", lambda: None)
    monkeypatch.setattr(caption_ocr, "caption_band", lambda layout, wh: object())
    monkeypatch.setattr(C, "_read_spans", lambda ctx, layout, fps: {"spans": spans, "frames_read": 120})
    cfg = Config(out_dir=str(tmp_path / "out"), work_dir=str(tmp_path / "work"), premiere=True,
                 captions="competitor")
    cfg.voiceover = str(vo)
    info = types.SimpleNamespace(path="", file_hash="x", width=1080, height=1920, display_width=1080,
                                 display_height=1920)
    ctx = types.SimpleNamespace(cfg=cfg, comp_info=info, comp_fps=Fraction(30), n_comp=120,
                                cutlist=types.SimpleNamespace(layout={}), cache=Cache(cfg.work), raw_audio=None,
                                audio_sr=SR, warn=lambda m: None)
    res = C.run_captions(ctx)
    assert [b["text"] for b in C.read_srt(res["path"])] == ["SO I WAS", "SAT", "BACK THERE"]
    rc = res["recheck"]
    assert rc["unsure"] == 1 and rc["changed"] == 1 and rc["changes"][0]["from"] == "sad"   # not "so" / "back"
    assert res["competitor_notes"]["from_transcript"][0]["text"] == "SAT"
