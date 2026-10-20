"""Day 13 (20 October): two caption fixes made after the caption step (post_captions.py, in no cached stage).

* 6.1 -- captions "a little too early": in your video16 (Always Sunny) and mindculpt fixes you dragged stretches of
  captions onto the words as heard. The captions there were timed on the edit's transcript, which ran off the sound
  1_edit.xml plays (video16: 0.25 s early from 4.8 s on; mindculpt: up to 0.47 s late in its second half). Now a
  caption moves by the offset its neighbourhood's words agree on, against the RAW's own words where A1 plays them
  -- only when it is at least post_captions.MIN_SHIFT_S; your earlier fixes kept captions that were on the sound.
* 9.3 -- rule 9 ("kept together"): a determiner split from its word ("still in the" | "water?") or a name split in
  two ("all over Hong" | "Kong") is joined the way you join them, when every caption it makes keeps the caption
  rules (20 characters, 4-5 words, 1.5 s on screen) and no pause, comma or video cut is there.
"""
from __future__ import annotations

import json
from fractions import Fraction
from types import SimpleNamespace

from match_cuts import post_captions as P

F60 = Fraction(60)


def _cap(text, a, b, mode="competitor"):
    return {"text": text, "start": a, "end": b, "mode": mode}


def _words(*rows):
    return [list(r) for r in rows]


# ---------------------------------------------------------------------------------------------------------------------
# 6.1: the captions onto the sound the edit plays
# ---------------------------------------------------------------------------------------------------------------------

def _late_transcript(n=12, late=0.45, t0=10.0, step=0.4):
    """n words heard every ``step`` s from t0 (ref, what A1 plays) and the transcript's copy ``late`` s later."""
    names = [f"w{i}" for i in range(n)]
    ref = [(w, t0 + i * step, t0 + i * step + 0.3) for i, w in enumerate(names)]
    own = [(w, s + late, e + late) for w, s, e in ref]
    return own, ref


def test_pair_words_pairs_the_same_words_in_order_and_near():
    own = [("Hello", 1.0, 1.2), ("there,", 1.3, 1.5), ("hello", 5.0, 5.2)]
    ref = [("hello", 1.1, 1.3), ("there", 1.35, 1.5), ("hello", 9.0, 9.2)]
    assert P.pair_words(own, ref) == [(1.0, 1.1), (1.3, 1.35)]          # the far "hello" is another one
    assert P.pair_words([], ref) == [] and P.pair_words(own, []) == []


def test_local_offset_needs_enough_words_that_agree():
    own, ref = _late_transcript()
    pairs = P.pair_words(own, ref)
    assert abs(P.local_offset(pairs, 12.0) - 0.45) < 1e-9
    assert P.local_offset(pairs[:3], 10.5) is None                        # too few words
    noisy = [(o, r + d) for (o, r), d in zip(pairs, [0.5, -0.4, 0.3, -0.2, 0.6, -0.5, 0.1, -0.3, 0.4, 0.0, 0.2, -0.6])]
    assert P.local_offset(noisy, 12.0) is None                            # the words disagree: no one offset


def test_a_late_stretch_moves_onto_the_sound_start_and_end_back_to_back():
    """mindculpt: the transcript (and so the captions) 0.45 s after the sound -- 27 frames at 60 fps earlier."""
    own, ref = _late_transcript(n=14, late=0.45, t0=10.0)
    caps = [_cap("w0 w1", 627, 675), _cap("w2 w3", 675, 723), _cap("w4 w5", 723, 771), _cap("w6 w7", 771, 819)]
    new, moved = P.retime(caps, P.pair_words(own, ref), F60)
    assert [(c["start"], c["end"]) for c in new] == [(600, 648), (648, 696), (696, 744), (744, 792)]
    assert len(moved) == 4 and all(m["by_frames"] == -27 for m in moved)
    assert all(abs(m["offset_s"] - 0.45) < 1e-9 for m in moved)
    assert caps[0]["start"] == 627                                        # the input is not changed


def test_an_early_stretch_moves_later():
    """video16 (Always Sunny): the captions 0.27 s before the words are heard -- 16 frames later."""
    own, ref = _late_transcript(n=14, late=-0.27, t0=10.0)
    caps = [_cap("w0 w1", 584, 632), _cap("w2 w3", 632, 680), _cap("w4 w5", 680, 728)]
    new, moved = P.retime(caps, P.pair_words(own, ref), F60)
    assert [(c["start"], c["end"]) for c in new] == [(600, 648), (648, 696), (696, 744)]
    assert [m["by_frames"] for m in moved] == [16, 16, 16]


def test_one_caption_off_alone_is_no_stretch():
    """video021: only the stammer "I've-" had words 0.4 s off around it -- one caption is left where it is."""
    pairs = [(12.0 + 0.4 * i, 12.0 + 0.4 * i - 0.45) for i in range(6)]
    caps = [_cap("a b", 600, 750), _cap("c d", 750, 960), _cap("e f", 960, 1100)]
    assert P.local_offset(pairs, 12.5) is not None and P.local_offset(pairs, 10.0) is None
    assert P.retime(caps, pairs, F60)[1] == []


def test_a_drift_moves_only_when_it_gets_big_somewhere():
    """laptop009: the transcript drifting to 0.22 s early at the end -- you kept those; mindculpt: a drift from 0.16
    to 0.44 s late -- you moved all of it."""
    def drift(lates, t0=10.0, step=0.4):
        ref = [(f"w{i}", t0 + i * step, t0 + i * step + 0.3) for i in range(len(lates))]
        own = [(w, s + d, e + d) for (w, s, e), d in zip(ref, lates)]
        return P.pair_words(own, ref)
    small = drift([-0.16 - 0.06 * i / 15 for i in range(16)])               # -0.16 ... -0.22
    caps = [_cap(f"w{2 * k} w{2 * k + 1}", 600 + 48 * k, 648 + 48 * k) for k in range(8)]
    assert P.retime(caps, small, F60)[1] == []
    big = drift([0.16 + 0.28 * i / 15 for i in range(16)])                  # 0.16 ... 0.44
    new, moved = P.retime([_cap(c["text"], c["start"] + 10, c["end"] + 10) for c in caps], big, F60)
    assert len(moved) == 8 and all(m["by_frames"] < -8 for m in moved)


def test_captions_on_the_sound_or_nearly_stay_where_they_are():
    """Your earlier fixes kept captions within a few frames of the words: nothing under MIN_SHIFT_S moves."""
    for late in (0.0, 0.08, P.MIN_SHIFT_S - 0.02):
        own, ref = _late_transcript(n=14, late=late)
        caps = [_cap("w0 w1", 600, 648), _cap("w2 w3", 648, 696)]
        new, moved = P.retime(caps, P.pair_words(own, ref), F60)
        assert moved == [] and [(c["start"], c["end"]) for c in new] == [(600, 648), (648, 696)]


def test_an_edge_on_a_video_cut_stays_and_nothing_crosses_a_cut():
    own, ref = _late_transcript(n=14, late=0.45, t0=10.0)
    caps = [_cap("w0 w1", 627, 675), _cap("w2 w3", 675, 723), _cap("w4 w5", 723, 771)]
    new, _ = P.retime(caps, P.pair_words(own, ref), F60, cuts=[675])
    assert new[0]["start"] == 600 and new[0]["end"] == 675 and new[1]["start"] == 675   # the cut keeps its edge
    assert [(c["start"], c["end"]) for c in new[1:]] == [(675, 696), (696, 744)]
    new, _ = P.retime(caps, P.pair_words(own, ref), F60, cuts=[610])
    assert new[0]["start"] == 610                                         # up to the cut, not across it


def test_placeholders_actions_and_other_video_do_not_move():
    own, ref = _late_transcript(n=14, late=0.45, t0=10.0)
    caps = [_cap("w0 w1", 627, 675), _cap("*laughs*", 675, 723, "action"), _cap("w4 w5", 723, 771),
            _cap("w6 w7", 771, 819), _cap("w8 w9", 819, 867), _cap("*...*", 867, 900, "placeholder")]
    new, moved = P.retime(caps, P.pair_words(own, ref), F60, stretches=[(819, 900)])
    assert [m["text"] for m in moved] == ["w0 w1", "w4 w5", "w6 w7"]       # w8 w9 is another video's stretch
    assert [(c["start"], c["end"]) for c in new] == [(600, 675), (675, 696), (696, 744), (744, 819), (819, 867),
                                                     (867, 900)]


def test_a_caption_left_too_short_on_a_cut_joins_the_one_before():
    """mindculpt: "by" | "himself" split on a video cut, "himself" heard before the cut; after the move "himself"
    would show for 2 frames -- it joins "by": "by himself", as you wrote it."""
    own, ref = _late_transcript(n=14, late=0.3, t0=9.7)
    caps = [_cap("by", 600, 648), _cap("himself", 648, 667), _cap("my daddy", 667, 720), _cap("once caught", 720, 770)]
    new, moved = P.retime(caps, P.pair_words(own, ref), F60, cuts=[648])
    assert [(c["text"], c["start"], c["end"]) for c in new] == [("by himself", 582, 650), ("my daddy", 650, 702),
                                                                ("once caught", 702, 752)]
    assert [m["now"] for m in moved if m.get("joined") is not None] == ["by himself"]
    from match_cuts.report import _post_captions
    lines = _post_captions({"post": {"pairs": [], "retimed": moved}})
    assert any("`himself` joined into `by himself`" in x for x in lines)


def test_a_caption_pushed_onto_its_end_cut_starts_there_and_keeps_going():
    """video16: "bread" ended on a video cut but is heard after it; moved later it starts on the cut (where you put
    it) and the caption after it, which started on that cut, moves with the stretch."""
    own, ref = _late_transcript(n=14, late=-0.27, t0=10.27)
    caps = [_cap("don't have", 600, 648), _cap("bread", 648, 660), _cap("and no", 660, 690),
            _cap("speakers", 690, 720)]
    new, _ = P.retime(caps, P.pair_words(own, ref), F60, cuts=[660])
    assert [(c["text"], c["start"], c["end"]) for c in new] == [("don't have", 616, 660), ("bread", 660, 676),
                                                                ("and no", 676, 706), ("speakers", 706, 736)]


def test_a_caption_moved_earlier_leaves_the_one_before_two_frames():
    own, ref = _late_transcript(n=14, late=0.45, t0=10.0)
    caps = [_cap("*...*", 600, 627, "placeholder"), _cap("w0 w1", 627, 675), _cap("w2 w3", 675, 723),
            _cap("w4 w5", 723, 771)]
    new, _ = P.retime(caps, P.pair_words(own, ref), F60)
    assert [(c["start"], c["end"]) for c in new] == [(600, 602), (602, 648), (648, 696), (696, 744)]


# ---------------------------------------------------------------------------------------------------------------------
# 9.3: pairs kept together (rule 9)
# ---------------------------------------------------------------------------------------------------------------------

def test_a_determiner_moves_to_its_word():
    """video021: "still in the" | "water?" -> "still in" | "the water?" (the break where "the" is heard)."""
    caps = [_cap("still in the", 600, 642), _cap("water?", 642, 700)]
    words = _words(("still", 10.0, 10.3), ("in", 10.35, 10.5), ("the", 10.55, 10.65), ("water?", 10.7, 11.1))
    new, done = P.pairs_fix(caps, words, F60)
    assert [(c["text"], c["start"], c["end"]) for c in new] == [("still in", 600, 633), ("the water?", 633, 700)]
    assert done == [{"was": "still in the | water?", "now": "still in | the water?", "start": 600, "end": 700}]


def test_a_lone_determiner_takes_its_word():
    """mindculpt: "my" | "daddy saved" -> "my daddy" | "saved"; laptop013: "My" | "sister's" -> one caption."""
    caps = [_cap("my", 600, 624), _cap("daddy saved", 624, 680)]
    words = _words(("my", 10.0, 10.2), ("daddy", 10.4, 10.7), ("saved", 10.8, 11.2))
    new, _ = P.pairs_fix(caps, words, F60)
    assert [(c["text"], c["start"], c["end"]) for c in new] == [("my daddy", 600, 648), ("saved", 648, 680)]
    new, _ = P.pairs_fix([_cap("My", 600, 624), _cap("sister's", 624, 680)], [], F60)
    assert [(c["text"], c["start"], c["end"]) for c in new] == [("My sister's", 600, 680)]


def test_no_lone_weak_word_is_left_behind():
    """video18: "on the" | "island together" -> "on the island" | "together" (not "on" alone)."""
    caps = [_cap("on the", 600, 640), _cap("island together", 640, 700)]
    words = _words(("on", 10.0, 10.2), ("the", 10.3, 10.6), ("island", 10.7, 10.95), ("together", 11.0, 11.4))
    new, _ = P.pairs_fix(caps, words, F60)
    assert [c["text"] for c in new] == ["on the island", "together"] and new[1]["start"] == 660
    # video021 / video15: when the noun was all the next caption held, the three go together
    caps = [_cap("from the", 600, 640), _cap("children", 640, 680)]
    words = _words(("from", 10.0, 10.2), ("the", 10.3, 10.6), ("children", 10.7, 11.2))
    new, _ = P.pairs_fix(caps, words, F60)
    assert [(c["text"], c["start"], c["end"]) for c in new] == [("from the children", 600, 680)]


def test_a_name_split_in_two_is_joined():
    """mindculpt: "all over Hong" | "Kong" -> "all over" | "Hong Kong"; two one-word names (chocolate's sung
    "Nestle" | "Crunch") and a sentence end heard between ("Oh God." | "No") stay."""
    words = _words(("all", 10.0, 10.1), ("over", 10.15, 10.3), ("Hong", 10.35, 10.6), ("Kong", 10.65, 10.9))
    new, _ = P.pairs_fix([_cap("all over Hong", 600, 640), _cap("Kong", 640, 660)], words, F60)
    assert [c["text"] for c in new] == ["all over", "Hong Kong"] and new[1]["start"] == 621
    two = [_cap("Nestle", 600, 640), _cap("Crunch", 640, 660)]
    assert P.pairs_fix(two, _words(("Nestle", 10.0, 10.5), ("Crunch", 10.6, 11.0)), F60)[1] == []
    god = [_cap("Oh God", 600, 640), _cap("No we're not", 640, 700)]
    assert P.pairs_fix(god, _words(("Oh", 10.0, 10.2), ("God.", 10.25, 10.6), ("No,", 10.65, 10.8)), F60)[1] == []


def test_pairs_stay_split_where_the_rules_or_the_speaker_say_so():
    words = _words(("look", 10.0, 10.2), ("at", 10.25, 10.4), ("the", 10.45, 10.55), ("thing", 10.6, 10.9))
    base = [_cap("look at the", 600, 640), _cap("thing", 640, 660)]
    assert P.pairs_fix(base, words, F60)[1] != []
    paused = _words(("look", 10.0, 10.2), ("at", 10.25, 10.4), ("the", 10.45, 10.55), ("thing", 11.0, 11.3))
    assert P.pairs_fix(base, paused, F60)[1] == []                          # a pause after "the": a break may go there
    assert P.pairs_fix(base, words, F60, cuts=[641])[1] == []               # a video cut there: rule 10 decides
    gap = [_cap("look at the", 600, 640), _cap("thing", 660, 700)]
    assert P.pairs_fix(gap, words, F60)[1] == []                            # a gap between the captions
    long = [_cap("look at the", 600, 640), _cap("extraordinary things", 640, 700)]
    assert P.pairs_fix(long, words, F60)[1] == []                           # "the extraordinary things": 24 characters
    stammer = [_cap("I'm a-", 600, 640), _cap("I'm allergic", 640, 700)]
    assert P.pairs_fix(stammer, [], F60)[1] == []                           # "a-" is a cut-off word, not "a"
    action = [_cap("look at the", 600, 640), _cap("*points*", 640, 700, "action")]
    assert P.pairs_fix(action, words, F60)[1] == []
    on_screen = [_cap("look at the", 600, 640), _cap("thing over there", 640, 730)]
    assert P.pairs_fix(on_screen, words, F60)[1] == []                      # "the thing over there" 1.6 s on screen


# ---------------------------------------------------------------------------------------------------------------------
# the run's step: 2_captions.srt and captions.json rewritten, the report says what was fixed
# ---------------------------------------------------------------------------------------------------------------------

def _ctx(tmp_path, captions, words, styled=True, speech=None):
    srt = tmp_path / "2_captions.srt"
    srt.write_text("old", encoding="utf-8")
    (tmp_path / "debug").mkdir(exist_ok=True)
    cap = {"path": str(srt), "fps": "60", "frames": 2000, "captions": captions, "words_final": words,
           "styled": styled, "source": "the cut edit", "other_video": [], "count": len(captions)}
    cfg = SimpleNamespace(deliver=tmp_path, debug_dir=tmp_path / "debug")
    return SimpleNamespace(cfg=cfg, captions=cap, speech=speech), srt


def test_the_step_rewrites_the_srt_and_captions_json(tmp_path, monkeypatch):
    from match_cuts import captions as C
    from match_cuts import export_xml_edl as X
    own, ref = _late_transcript(n=14, late=0.45, t0=10.0)
    caps = [_cap("w0 w1", 627, 675), _cap("w2 w3", 675, 723), _cap("w4 w5", 723, 771)]
    (tmp_path / "1_edit.xml").write_text("<xmeml/>", encoding="utf-8")
    monkeypatch.setattr(C, "edit_cuts", lambda xml, fps: [])
    monkeypatch.setattr(X, "parse_premiere_xml", lambda xml: {"audio": []})
    monkeypatch.setattr(C, "speech_starts", lambda items, sm, fps: ([], ref))
    ctx, srt = _ctx(tmp_path, caps, own, speech=object())
    info = P.run(ctx)
    assert len(info["retimed"]) == 3 and info["offset_s"]["words"] == 14
    blocks = srt.read_text(encoding="utf-8").strip().split("\n\n")
    assert blocks[0].splitlines() == ["1", "00:00:10,000 --> 00:00:10,800", "w0 w1"]
    saved = json.loads((tmp_path / "debug" / "captions.json").read_text(encoding="utf-8"))
    assert [(c["start"], c["end"], c["start_tc"]) for c in saved["captions"]] == \
        [(600, 648, "00:00:10,000"), (648, 696, "00:00:10,800"), (696, 744, "00:00:11,600")]
    assert saved["post"]["retimed"][0]["by_frames"] == -27
    from match_cuts.report import _post_captions
    lines = _post_captions(ctx.captions)
    assert any("`w0 w1` moved 0.45 s earlier" in x for x in lines) and any("kept together" in x for x in lines)


def test_the_step_leaves_copied_captions_and_a_missing_speech_map_alone(tmp_path):
    own, _ref = _late_transcript(n=14, late=0.45, t0=10.0)
    caps = [_cap("w0 w1", 627, 675), _cap("w2 w3", 675, 723), _cap("w4 w5", 723, 771)]
    ctx, srt = _ctx(tmp_path, caps, own, styled=False, speech=object())
    assert P.run(ctx) == {"pairs": [], "retimed": []} and srt.read_text(encoding="utf-8") == "old"
    ctx, srt = _ctx(tmp_path, caps, own, styled=True, speech=None)
    assert P.run(ctx)["retimed"] == [] and srt.read_text(encoding="utf-8") == "old"


def test_the_pipeline_step_never_fails_the_run(tmp_path, monkeypatch):
    from match_cuts import pipeline
    ctx, _srt = _ctx(tmp_path, [_cap("a", 0, 10)], [])

    def boom(_ctx):
        raise RuntimeError("no")
    monkeypatch.setattr(P, "run", boom)
    pipeline.post_captions_step(ctx)
    assert ctx.captions["post"]["error"] == "RuntimeError: no"


def test_post_captions_is_in_no_cached_stage():
    """Its fixes cost no re-transcription: no stage module imports it (common.stage_modules)."""
    from match_cuts import common
    for stage in common.STAGE_CODE:
        assert "post_captions" not in common.stage_modules(stage)
