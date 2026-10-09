"""video018 S15 (run output/018): the hard check 'XML SPEECH A1 S15 starts at 00:00:17:39 inside speech: RAW 745.80 s,
sound 745.67-747.02 s ('minute?')'. Run 018's own numbers: its speech map of the RAW around S13-S16 (large-v3-turbo
words; the sound after 'minute?' has no words, voiced 0.34 s: speech), its plan of S13-S16 (cutlist_no_broll.json).

Both fail on the code before the fix (an audio line under a retimed picture was extended through the shared
ripple, so the picture was extended too, at its speed; speech.MAX_RETIMED_EXT_S).
"""
from __future__ import annotations

import types
from fractions import Fraction

from match_cuts import silence as S, speech as SP

FPS = Fraction(60)
B = "no words: a breath or a noise"
SOUNDS_018 = [(742.74, 743.62, True, "no words, voiced 0.64 s"), (743.64, 743.88, False, B),
              (743.93, 744.52, True, "words: Jose,"), (744.54, 744.66, True, "words: Jose, are you"),
              (744.68, 744.81, True, "words: you going to"), (744.83, 744.84, False, B),
              (744.86, 745.00, True, "words: be all"), (745.02, 745.15, True, "words: right"),
              (745.18, 745.26, True, "words: for"), (745.28, 745.53, True, "words: minute?"),
              (745.67, 747.02, True, "no words, voiced 0.34 s"),          # the laugh over the show's music
              (747.04, 747.22, False, B), (747.24, 747.44, False, B), (747.49, 747.63, False, B),
              (747.69, 747.83, False, B), (747.85, 748.04, False, B), (748.15, 748.24, False, B),
              (748.44, 748.56, False, B), (748.65, 748.79, False, B),
              (749.02, 749.34, True, "words: Joe,"), (749.39, 749.57, True, "words: go"),
              (749.62, 749.67, True, "words: through"), (749.69, 749.82, True, "words: through the"),
              (749.84, 750.02, True, "words: whole"), (750.04, 750.24, True, "words: mundane"),
              (750.26, 750.54, True, "words: mundane")]
WORDS_018 = [("Jose", 744.394, 744.556), ("are", 744.576, 744.637), ("you", 744.637, 744.698),
             ("going", 744.698, 744.799), ("to", 744.799, 744.839), ("be", 744.839, 744.880), ("all", 744.940, 745.021),
             ("right", 745.021, 745.183), ("for", 745.183, 745.264), ("a", 745.264, 745.284),
             ("minute?", 745.325, 745.527), ("Joe", 749.050, 749.332), ("go", 749.392, 749.473),
             ("through", 749.573, 749.713), ("the", 749.713, 749.773), ("whole", 749.813, 750.014),
             ("mundane", 750.054, 750.535)]
SHOTS_018 = [743.476, 746.112, 747.714, 750.183]          # the RAW's shot changes there (s)


def speech_map_018() -> SP.SpeechMap:
    return SP.SpeechMap([SP.Sound(*s) for s in SOUNDS_018], 1472.87, list(WORDS_018))


def test_an_audio_line_under_a_retimed_picture_is_not_extended_video018_s15():
    """S15's picture plays at 115 % (RAW 746.783 on); its sound is an audio line at 100 % from RAW 746.85, inside the
    laugh after 'minute?' (745.67-747.02), 0.17 s before its end, with no speech after it in the clip. Today its start
    moves 74 frames earlier, before the whole laugh (745.617) -- and the extension is the ripple's, so V1 goes back
    round(74 x 1.15) = 85 frames (to 745.367: across the shot change at 746.11, into the RAW S13+S14 plays); the repeat
    removal then cuts 11 frames off both tracks and A1 starts at 745.80, inside the laugh. An extension of a retimed
    picture adds at most 0.1 s of new picture: the start moves into the clip instead, and S15, with no speech left in
    it, goes -- as in my finished edit (nothing from RAW 745.600 to 749.100)."""
    f = float(FPS)
    ev = types.SimpleNamespace(dissolve_in=0)
    clips = [types.SimpleNamespace(rec_start=r0, rec_end=r1, start=r0, end=r1, src_in=int(round(s * f)), speed=v, ev=ev)
             for r0, r1, s, v in ((994, 1115, 744.2667, 1.0), (1115, 1150, 746.7833, 1.15), (1150, 1221, 749.0167, 1.0))]
    seg = {k: types.SimpleNamespace(id=k) for k in (13, 15, 16)}
    audio = [{"seg": seg[13], "start": 994, "end": 1073, "in": 44656, "speed": 1.0, "what": "picture"},
             {"seg": seg[15], "start": 1115, "end": 1150, "in": 44811, "speed": 1.0, "what": "audio line"},
             {"seg": seg[16], "start": 1150, "end": 1221, "in": 44941, "speed": 1.0, "what": "picture"}]
    st = types.SimpleNamespace(pad_after=0.05, pad_before=0.05)
    rp, rows = SP.plan_cuts(clips, audio, speech_map_018(), FPS, st, 1812, None, 1, SHOTS_018)
    moved = {(r["clip"], r["edge"]): r["frames"] for r in rows}
    assert not any(i.at == 1115 and i.side == "start" for i in rp.inserts), moved      # today: 74 frames earlier
    assert moved.get(("S15", "whole")) == -35 and any((c.a, c.b) == (1115, 1150) for c in rp.cuts)
    assert moved[("S13", "start")] == -23 and moved[("S16", "start")] == -3          # the clips at 100 %: as before
    # a picture that barely moves (zendaya-age S06: 7 % over its own audio line) is still extended before the word
    slow = [types.SimpleNamespace(**dict(vars(c), speed=0.0734)) if c.rec_start == 1115 else c for c in clips]
    rp2, rows2 = SP.plan_cuts(slow, audio, speech_map_018(), FPS, st, 1812, None, 1, SHOTS_018)
    assert any(i.at == 1115 and i.side == "start" and i.frames == 74 for i in rp2.inserts)


def test_the_speech_check_of_video018_s15_passes(tmp_path):
    """The same four segments through the export (plan_premiere, the repeat removal, the XML, its hard speech check):
    today S15's 115 % picture runs back into what S13+S14 shows, the repeat removal cuts both tracks, and A1 starts
    inside the laugh ('XML SPEECH A1 S15 starts ... inside speech'). With the fix nothing is left inside speech."""
    from match_cuts import export_xml_edl as ex, repeats
    from match_cuts.config import Config
    from match_cuts.model import Cutlist, Segment
    pan = {"scale": 0.546, "rotation_deg": 0.0, "tx": -246.7, "ty": 306.6}
    box = {"x": 30.0, "y": 316.0, "w": 548.0, "h": 569.37, "corner_radius": 48.0}

    def seg(i, a, b, raw_s, v=1.0, **kw):
        return Segment(id=i, type="raw", comp_in=a, comp_out=b, raw_in_seconds=raw_s,
                       raw_in_interval=[raw_s - 8e-5, raw_s + 8e-5], speed=v, confidence=.97, transform=dict(pan), **kw)
    segs = [seg(13, 0, 79, 744.260683333), seg(14, 79, 121, 745.57735, audio={"mute": True}),
            seg(15, 121, 156, 746.78755, 1.15, audio={"line": {"id": 121, "raw_in_seconds": 746.843599778, "speed": 1.0,
                                                               "source": "own in-point at speed 1"}}),
            seg(16, 156, 227, 749.010266)]
    cl = Cutlist(1, {"file": "media/competitor_ref.mp4", "width": 608, "height": 1080, "fps": "60/1", "frames": 227},
                 {"file": "media/raw.mp4", "file_abs": "/abs/media/raw.mp4", "width": 1920, "height": 1080,
                  "fps": "30000/1001", "frames": 44142, "has_audio": True, "audio_sample_rate": 48000,
                  "audio_channels": 2},
                 {"mode": "match", "layout_kind": "boxed", "box": box, "background": "solid",
                  "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000"}, segs)
    sm = speech_map_018()
    cfg = Config(out_dir=str(tmp_path), premiere=True, premiere_normal_audio=False)   # S14 muted, S15's line
    plan = repeats.add_to_plan(S.plan_premiere(cl, None, 16000, cfg, None, sm, shots=SHOTS_018), cl, cfg)
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(cl, xml, cfg, plan["ripple"])
    assert ex.premiere_speech_problems(xml, sm) == [], (plan["repeats"]["rows"], plan["speech"]["rows"])
    assert ex.premiere_repeat_problems(xml) == []
    assert [r["edge"] for r in plan["speech"]["rows"] if r["clip"] == "S15"] == ["whole"]   # S15 goes, as in my edit
