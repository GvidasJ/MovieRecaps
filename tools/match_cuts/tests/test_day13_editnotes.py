"""Day 13, section 9: small edit notes the tool now fixes in the edit by itself.

9.2  "XML A1 S12: range 650-706 is not a V1 cut range" (Are We There Yet, the laptop's run): snap_to_shots moved the
     V1 cut S11|S12+S13 one frame onto the RAW's shot change, A1 kept the competitor's cut -- under a clip of two
     competitor cuts (S12+S13) a range no V1 clip has. A1 now starts and ends with its V1 clip.
9.1  XML FLASH: (a) the competitor's own last frames show the first frame or two of the RAW's next shot (video12: 2
     frames at 00:00:32:00, you cut them in your fix; Are We There Yet S26: 1 frame at 00:00:24:22): 1_edit.xml ends on
     that shot change; (b) a burst of short shots of the RAW's own (Family Guy S09E11: RAW frames 1 + 2 + 1, played
     straight through in one take) is no flash -- the check had judged its middle shot alone; (c) a flash of 1-2
     frames at a cut inside the edit: the cut moves onto the RAW's shot change (kept only when the check improves).
"""
from __future__ import annotations

import dataclasses
from fractions import Fraction
from types import SimpleNamespace

from match_cuts import export_xml_edl as ex, shots
from match_cuts.config import Config
from match_cuts.geometry import Sim
from match_cuts.model import Cutlist, Segment

R24 = Fraction(24000, 1001)
R30 = Fraction(30000, 1001)
F60 = Fraction(60)


def framing(tx: float) -> dict:
    return {"scale": 0.546, "rotation_deg": 0.0, "tx": tx, "ty": 306.6}


def cutlist(segs: list[Segment], raw_fps: str, comp_frames: int, raw_frames: int = 44142) -> Cutlist:
    comp = {"file": "media/competitor_ref.mp4", "width": 608, "height": 1080, "fps": "60/1", "frames": comp_frames}
    raw = {"file": "media/raw.mp4", "file_abs": "/abs/media/raw.mp4", "width": 1920, "height": 1080,
           "fps": raw_fps, "frames": raw_frames, "has_audio": True, "audio_sample_rate": 48000, "audio_channels": 2}
    layout = {"mode": "match", "layout_kind": "boxed",
              "box": {"x": 30.0, "y": 316.0, "w": 548.0, "h": 569.37, "corner_radius": 48.0},
              "background": "solid", "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000"}
    return Cutlist(1, comp, raw, layout, segs)


def config(tmp_path, changes, **kw) -> Config:
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    cfg.premiere_shots = changes                   # the RAW's shot changes (pipeline.premiere_plan sets them)
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def ranges(x: dict, kind: str) -> list[tuple[int, int, int]]:
    return [(int(c["start"]), int(c["end"]), int(c["in"])) for c in x[kind]]


# ---------------------------------------------------------------------------------------------------------------------
# 9.2: A1 starts and ends with its V1 clip where the V1 cut moved onto the RAW's shot change
# ---------------------------------------------------------------------------------------------------------------------

def awty_like() -> Cutlist:
    """S05 elsewhere in the RAW, then S06 + S07: one take at one framing (one V1 clip of two competitor cuts, like
    Are We There Yet's S12+S13). S06's first tick (44110) still shows RAW 22032, the last frame of the shot before
    RAW 22033 (29.97 fps RAW, 60 fps sequence: video018's numbers) -- snap_to_shots moves the cut a frame later."""
    segs = [Segment(5, "raw", 0, 236, raw_in_seconds=700.0, transform=framing(-96.7)),
            Segment(6, "raw", 236, 330, raw_in_seconds=735.16915, transform=framing(-246.7),
                    raw_in_interval=[735.169133333, 735.169166667]),
            Segment(7, "raw", 330, 437, raw_in_seconds=735.16915 + 94 / 60, transform=framing(-246.7))]
    return cutlist(segs, "30000/1001", 437)


AWTY_SHOTS = shots.seconds([22033], R30)


def test_follow_snapped_cuts_moves_the_a1_cut_with_the_picture():
    seg = Segment(1, "raw", 0, 120, raw_in_seconds=10.0)
    ev = SimpleNamespace(seg=seg, rec_in=0, rec_out=120, seg_name="S01", dissolve_in=0)

    def clip(r0, r1, s0):
        return ex.PremiereClip(seg, ev, r0, r1, r0, r1, s0, s0 + (r1 - r0), 1.0, True, 0.0, 1.0, True,
                               [(s0, Sim(0.5, 0.0, 0.0, 0.0))], None, [ev])
    a, b = clip(0, 61, 600), clip(61, 120, 2001)                  # after the snap: the cut moved 60 -> 61
    audio = [{"seg": seg, "start": 0, "end": 60, "in": 600, "out": 660, "speed": 1.0, "what": "picture"},
             {"seg": seg, "start": 60, "end": 120, "in": 2000, "out": 2060, "speed": 1.0, "what": "picture"}]
    got, done = ex.follow_snapped_cuts([a, b], audio, [{"clips": "S01|S02", "from": 60, "to": 61}])
    assert [(g["start"], g["end"], g["in"], g["out"]) for g in got] == [(0, 61, 600, 661), (61, 120, 2001, 2060)]
    assert all(g["followed"] for g in got) and done == [{"clips": "S01|S02", "from": 60, "to": 61, "items": 2}]
    assert audio[0]["end"] == 60                                   # the input is not changed
    # an audio line (another moment of the RAW than the picture) and a sound off its picture's line stay
    line = [dict(audio[0], what="audio line"), dict(audio[1], **{"in": 2500, "out": 2560})]
    got2, done2 = ex.follow_snapped_cuts([a, b], line, [{"clips": "S01|S02", "from": 60, "to": 61}])
    assert [(g["start"], g["end"]) for g in got2] == [(0, 60), (60, 120)] and done2 == []
    # a cut moved a frame EARLIER (the clip before's last frame was the next shot's first)
    a3, b3 = clip(0, 59, 600), clip(59, 120, 1999)
    got3, _ = ex.follow_snapped_cuts([a3, b3], audio, [{"clips": "S01|S02", "from": 60, "to": 59}])
    assert [(g["start"], g["end"], g["in"], g["out"]) for g in got3] == [(0, 59, 600, 659), (59, 120, 1999, 2060)]


def test_a1_of_a_clip_of_two_cuts_starts_with_its_moved_picture_and_validates(tmp_path):
    cl = awty_like()
    cfg = config(tmp_path, AWTY_SHOTS)
    xml = tmp_path / "edit.xml"
    res = ex.write_premiere_xml(cl, xml, cfg)
    x = ex.parse_premiere_xml(xml)
    assert [c["label"] for c in x["clips"]] == ["S05", "S06+S07"]
    assert ranges(x, "clips") == [(0, 237, 42000), (237, 437, 44111)]           # the V1 cut on the shot change
    assert ranges(x, "audio") == [(0, 237, 42000), (237, 437, 44111)]           # ... and A1 with it
    assert res["a1_followed"] == [{"clips": "S05|S06+S07", "from": 236, "to": 237, "items": 2}]
    assert res["edit_fixes"] and "S05|S06+S07 at 00:00:03:57" in res["edit_fixes"][0]
    v = ex.validate_premiere_exports(cl, xml, None, cfg, None, None, AWTY_SHOTS)
    assert not [e for e in v["errors"] if "is not a V1 cut range" in e], v["errors"]
    assert v["ok"], v["errors"]
    # switched off: the old one-frame split edit -- and the note the laptop's run had
    try:
        ex.A1_FOLLOWS_V1 = False
        ex.write_premiere_xml(cl, xml, cfg)
        assert ranges(ex.parse_premiere_xml(xml), "audio") == [(0, 236, 42000), (236, 437, 44110)]
        v0 = ex.validate_premiere_exports(cl, xml, None, cfg, None, None, AWTY_SHOTS)
        assert any("XML A1 S06: range 236-437 is not a V1 cut range" in e for e in v0["errors"]), v0["errors"]
    finally:
        ex.A1_FOLLOWS_V1 = True


def test_with_remove_silence_a1_keeps_the_cut_the_speech_safe_step_placed(tmp_path):
    cl = awty_like()
    cfg = config(tmp_path, AWTY_SHOTS, remove_silence=True, competitor="c.mp4")
    res = ex.write_premiere_xml(cl, tmp_path / "edit.xml", cfg)
    assert res["a1_followed"] == [] and res["edit_fixes"] == []


def test_the_a1_cut_follows_onto_the_raws_rate(tmp_path):
    """At the RAW's rate (1_edit.xml) V1 and A1 still cut on the same frame, and the fix is listed there."""
    cl = awty_like()
    cfg = config(tmp_path, AWTY_SHOTS)
    base, final = tmp_path / "edit_60fps.xml", tmp_path / "1_edit.xml"
    ex.write_premiere_xml(cl, base, cfg)
    res = ex.write_premiere_xml(cl, final, cfg, seq_rate=R30)
    x = ex.parse_premiere_xml(final)
    assert [c["start"] for c in x["clips"]] == [a["start"] for a in x["audio"]]
    assert [c["end"] for c in x["clips"]] == [a["end"] for a in x["audio"]]
    assert res["edit_fixes"] and ex._tc(int(res["pos_map"]["237"]), R30) in res["edit_fixes"][0]
    assert ex.final_xml_problems(base, final, 1.0, AWTY_SHOTS, R30) == []


# ---------------------------------------------------------------------------------------------------------------------
# 9.1 (a): the competitor's own last frames show a sliver of the RAW's next shot -- 1_edit.xml ends on the change
# ---------------------------------------------------------------------------------------------------------------------

def _clip24(r0: int, r1: int, s0: int, label: int = 1):
    seg = Segment(label, "raw", 0, 300, raw_in_seconds=10.0)
    ev = SimpleNamespace(seg=seg, rec_in=0, rec_out=300, seg_name=f"S{label:02d}", dissolve_in=0)
    return ex.PremiereClip(seg, ev, r0, r1, r0, r1, s0, s0 + (r1 - r0), 1.0, True, 0.0, 1.0, True,
                           [(s0, Sim(0.5, 0.0, 0.0, 0.0))], None, [ev])


def test_end_flash_frames_finds_only_a_sliver_of_the_next_shot_at_the_very_end():
    # one take at the RAW's rate (a sequence frame = a RAW frame): RAW 1000-1099, a shot change at RAW 1098
    take = [_clip24(0, 100, 1000)]
    assert ex.end_flash_frames(take, 100, shots.seconds([1098], R24), R24) == 2
    assert ex.end_flash_frames(take, 100, shots.seconds([1094], R24), R24) == 0     # 6 frames: 0.25 s, a real shot
    assert ex.end_flash_frames(take, 100, shots.seconds([1095], R24), R24) == 5
    assert ex.end_flash_frames(take, 100, [], R24) == 0 and ex.end_flash_frames(take, 100, None, R24) == 0
    # the change in the middle of the edit: not the end's business
    assert ex.end_flash_frames(take, 100, shots.seconds([1050], R24), R24) == 0
    # the competitor's own last cut: 3 frames of another moment of the RAW (a real cut, not the take running on)
    cut = [_clip24(0, 97, 1000), _clip24(97, 100, 5000, 2)]
    assert ex.end_flash_frames(cut, 100, shots.seconds([5000], R24), R24) == 0
    # run 18 (video35): a last clip of 2 frames (0.08 s) of another shot -- a flash at the end: cut (your fix ends
    # on the clip before it)
    flash = [_clip24(0, 98, 1000), _clip24(98, 100, 5000, 2)]
    assert ex.end_flash_frames(flash, 100, shots.seconds([5000], R24), R24) == 2
    # the take's last pieces split at the change (the XML's Scene Edit Detection): the same sliver
    split = [_clip24(0, 98, 1000), _clip24(98, 100, 1098)]
    assert ex.end_flash_frames(split, 100, shots.seconds([1098], R24), R24) == 2
    # black after the last clip, a reverse: nothing to cut
    assert ex.end_flash_frames(take, 104, shots.seconds([1098], R24), R24) == 0
    rev = [ex.dataclasses.replace(_clip24(0, 100, 1100), speed=-1.0, src_out=1000)]
    assert ex.end_flash_frames(rev, 100, shots.seconds([1002], R24), R24) == 0


def test_cut_end_shortens_v1_a1_markers_and_the_sequence():
    clips = [_clip24(0, 60, 1000), _clip24(60, 98, 1060), _clip24(98, 100, 1098)]
    audio = [{"start": 0, "end": 100, "in": 1000, "out": 1100, "speed": 1.0},
             {"start": 99, "end": 100, "in": 3000, "out": 3001, "speed": 1.0}]
    markers = [{"name": "x", "in": 90, "out": 100}, {"name": "y", "in": 10, "out": -1}]
    c, a, m, n = ex.cut_end(clips, audio, markers, 100, 2)
    assert n == 98 and [(x.rec_start, x.rec_end, x.src_in, x.src_out) for x in c] == [(0, 60, 1000, 1060),
                                                                                     (60, 98, 1060, 1098)]
    assert [(x["start"], x["end"], x["in"], x["out"]) for x in a] == [(0, 98, 1000, 1098)]
    assert [(x["in"], x["out"]) for x in m] == [(90, 98), (10, -1)]


def video12_like() -> Cutlist:
    """video12-fixed's ending (23.976 fps RAW): the last clip plays RAW up to 63.883 s; the RAW's next shot starts on
    frame 1529 (63.772 s) -- the competitor's last 6 ticks show it, 2 RAW frames (1529, 1530) at the RAW's rate."""
    segs = [Segment(1, "raw", 0, 120, raw_in_seconds=55.0, transform=framing(-96.7)),
            Segment(2, "raw", 120, 300, raw_in_seconds=63.883 - 3.0, transform=framing(-246.7))]
    return cutlist(segs, "24000/1001", 300, raw_frames=3000)


V12_SHOTS = shots.seconds([1529], R24)


def test_the_delivered_edit_ends_on_the_raws_shot_change(tmp_path):
    cl = video12_like()
    cfg = config(tmp_path, V12_SHOTS)
    base, final = tmp_path / "edit_60fps.xml", tmp_path / "1_edit.xml"
    r60 = ex.write_premiere_xml(cl, base, cfg)
    assert r60["end_cut"] is None                         # the checked 60 fps edit keeps them (check-all scores it)
    assert ex.premiere_flash_problems(base, V12_SHOTS, R24)               # ... and its note
    res = ex.write_premiere_xml(cl, final, cfg, seq_rate=R24)
    xf = ex.parse_premiere_xml(final)
    n_full = ex.rerate_edit([], [], [], 300, F60, R24)[3]
    assert res["end_cut"] == {"frames": 2, "at": n_full - 2, "label": "S02", "was": n_full}
    assert xf["duration"] == n_full - 2
    last_v, last_a = max(xf["clips"], key=lambda c: c["end"]), max(xf["audio"], key=lambda c: c["end"])
    assert last_v["end"] == last_a["end"] == xf["duration"] and last_v["out"] == last_a["out"] == 1529
    assert ex.premiere_flash_problems(final, V12_SHOTS, R24) == []
    assert ex.final_xml_problems(base, final, 1.0, V12_SHOTS, R24, end_cut=2) == []
    # without the allowance the rate check would call the shorter edit wrong
    assert any("XML RATE" in p for p in ex.rate_problems(base, final, 1.0))
    assert res["edit_fixes"] == [f"2 flash frames removed at {ex._tc(n_full - 2, R24)} (S02): the edit's last 2 "
                                 "frames showed the first frames of the RAW's next shot -- 1_edit.xml now ends on that "
                                 "shot change"]
    # captions follow: a caption ending at the old end (or inside the frames cut) ends on the new end
    pm = {int(k): v for k, v in res["pos_map"].items()}
    assert pm[300] == n_full - 2 and pm[299] == n_full - 2
    # switched off: the old ending (and its flash)
    try:
        ex.END_FLASH_CUT = False
        ex.write_premiere_xml(cl, final, cfg, seq_rate=R24)
        assert ex.parse_premiere_xml(final)["duration"] == n_full
        assert ex.premiere_flash_problems(final, V12_SHOTS, R24)
    finally:
        ex.END_FLASH_CUT = True
    one = ex.edit_fix_lines([], {"frames": 1, "at": 598, "label": "S26"}, R24)
    assert one == ["1 flash frame removed at 00:00:24:22 (S26): the edit's last frame showed the first frame of the "
                   "RAW's next shot -- 1_edit.xml now ends on that shot change"]


def test_with_remove_silence_the_end_is_left_as_the_speech_safe_step_made_it(tmp_path):
    cfg = config(tmp_path, V12_SHOTS, remove_silence=True, competitor="c.mp4")
    res = ex.write_premiere_xml(video12_like(), tmp_path / "1_edit.xml", cfg, seq_rate=R24)
    assert res["end_cut"] is None and res["cut_flashes"] == [] and res["edit_fixes"] == []


def test_the_run_writes_and_lists_the_fix(tmp_path):
    """pipeline.apply_final (the S8 'speed + frame' step at the RAW's rate) on the video12-like edit: 1_edit.xml ends
    on the shot change and passes its hard checks (the XML RATE check allows the frames cut on purpose); the SRT's last
    caption ends there too; the fix is in the end summary (hand_checks) and in report.md."""
    from match_cuts import pipeline, report
    from match_cuts.captions import frame_ms, ms_tc, read_srt
    from match_cuts.common import DecisionLog
    cl = video12_like()
    run = tmp_path / "001"
    (run / "extras").mkdir(parents=True)
    cfg = config(tmp_path, V12_SHOTS, out_dir=str(run / "extras"), deliver_dir=str(run), premiere_seq_rate=str(R24))
    xml = run / "1_edit.xml"
    r60 = ex.write_premiere_xml(cl, xml, cfg)
    (run / "2_captions.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\none\n\n2\n00:00:03,000 --> 00:00:05,000\n"
                                        "two\n", encoding="utf-8")
    ctx = SimpleNamespace(cfg=cfg, premiere_cutlist=cl, silence={}, premiere_xml=dict(r60), raw_fps=R24,
                          captions={"path": str(run / "2_captions.srt")}, paths={"xml": str(xml)},
                          dlog=DecisionLog(run / "extras" / "decisions.jsonl"), exports={"ok": True, "errors": []},
                          people=None, cutlist=cl, broll=None, comp_fps=Fraction(60))
    out = pipeline.apply_final(ctx, 1.0, R24)
    assert out["problems"] == [], out["problems"]
    end = ex.parse_premiere_xml(xml)["duration"]
    assert ctx.premiere_xml["end_cut"]["at"] == end and ctx.premiere_xml["edit_fixes"][0].startswith("2 flash frames")
    rows = read_srt(run / "2_captions.srt")
    assert rows[-1]["end_ms"] == frame_ms(end, R24) and ms_tc(frame_ms(end, R24))
    assert (run / "extras" / "edit_60fps.xml").is_file()                 # the checked edit kept (check-all scores it)
    hc = pipeline.hand_checks(ctx)
    assert hc["edit_fixes"] == ctx.premiere_xml["edit_fixes"]
    assert any("Fixed in the edit by itself" in s for s in report._premiere_checks(ctx))


def test_final_srt_ends_the_last_caption_on_the_new_end(tmp_path):
    from match_cuts.pipeline import final_srt
    cl = video12_like()
    cfg = config(tmp_path, V12_SHOTS)
    res = ex.write_premiere_xml(cl, tmp_path / "1_edit.xml", cfg, seq_rate=R24)
    pm = {int(k): v for k, v in res["pos_map"].items()}
    rows = [{"start_ms": 1000.0, "end_ms": 4000.0, "text": "one"},
            {"start_ms": 4000.0, "end_ms": 5000.0, "text": "two"}]
    srt = final_srt(rows, 1.0, F60, R24, pm)
    end = srt.strip().splitlines()[-2].split(" --> ")[1]
    from match_cuts.captions import frame_ms, ms_tc
    assert end == ms_tc(frame_ms(res["end_cut"]["at"], R24))


# ---------------------------------------------------------------------------------------------------------------------
# 9.1 (c): a flash of 1-2 frames at a cut inside the edit -- the cut moves onto the RAW's shot change
# ---------------------------------------------------------------------------------------------------------------------

def _framed(c, tx: float):
    return ex.dataclasses.replace(c, keys=[(c.src_in, Sim(0.5, 0.0, tx, 0.0))])


def _a1(clips):
    return [{"start": c.rec_start, "end": c.rec_end, "in": c.src_in, "out": c.src_out, "speed": 1.0,
             "what": "picture"} for c in clips]


def _shape(clips):
    return [(c.rec_start, c.rec_end, c.src_in, c.src_out) for c in clips]


def test_a_take_running_two_frames_into_its_next_shot_ends_on_the_change():
    # S01 plays RAW 1000-1049; its next shot starts on RAW 1048: its last 2 frames are a flash before S02 (RAW 5000-)
    x, y = _framed(_clip24(0, 50, 1000, 1), 0.0), _framed(_clip24(50, 100, 5000, 2), 40.0)
    changes = shots.seconds([900, 1048, 4900, 6000], R24)
    assert ex._flash_lines([x, y], changes, R24, R24)                       # the hard check's note
    clips, audio, done = ex.fix_cut_flashes([x, y], _a1([x, y]), changes, R24, R24)
    assert _shape(clips) == [(0, 48, 1000, 1048), (48, 100, 4998, 5050)]   # S02 starts 2 frames earlier, on its line
    assert [(a["start"], a["end"], a["in"]) for a in audio] == [(0, 48, 1000), (48, 100, 4998)]
    assert done == [{"clips": "S01|S02", "from": 50, "to": 48, "frames": 2}]
    assert ex._flash_lines(clips, changes, R24, R24) == []
    line = ex.edit_fix_lines([], None, R24, None, done)[0]
    assert line.startswith("2 flash frames removed at 00:00:02:00 (S01|S02): the cut moved 2 frames earlier")


def test_a_clip_starting_two_frames_before_its_own_shot_starts_on_it():
    # S02 starts on RAW 4998, its shot starts on 5000: its first 2 frames are the shot before's last
    x, y = _framed(_clip24(0, 50, 1000, 1), 0.0), _framed(_clip24(50, 100, 4998, 2), 40.0)
    changes = shots.seconds([900, 3000, 5000, 6000], R24)   # (shots.raw_shot_changes adds each window's edges too)
    pm = {125: 50}
    clips, audio, done = ex.fix_cut_flashes([x, y], _a1([x, y]), changes, R24, R24, pm)
    assert _shape(clips) == [(0, 52, 1000, 1052), (52, 100, 5000, 5048)]   # S01 plays 2 more of its own shot
    assert [(a["start"], a["end"], a["in"]) for a in audio] == [(0, 52, 1000), (52, 100, 5000)]
    assert pm == {125: 52} and done[0]["to"] == 52                          # a caption on that cut moves with it


def test_a_flash_no_short_move_can_fix_is_left_for_a_look():
    x, y = _framed(_clip24(0, 50, 1000, 1), 0.0), _framed(_clip24(50, 100, 5000, 2), 40.0)
    # S02's own shot starts on its first frame: it cannot start earlier without a sliver of the shot before
    tight = shots.seconds([900, 1048, 5000, 6000], R24)
    clips, _, done = ex.fix_cut_flashes([x, y], _a1([x, y]), tight, R24, R24)
    assert done == [] and _shape(clips) == _shape([x, y])
    # 3 frames: more than a flash frame or two -- the competitor's own short shot, not moved
    three = shots.seconds([900, 1047, 4900, 6000], R24)
    assert ex.fix_cut_flashes([x, y], _a1([x, y]), three, R24, R24)[2] == []
    # a sound cut there that is not the picture's own sound (an audio line): the picture cut stays with it
    line = [dict(a, what="audio line") for a in _a1([x, y])]
    assert ex.fix_cut_flashes([x, y], line, shots.seconds([900, 1048, 4900, 6000], R24), R24, R24)[2] == []


def test_a_framing_change_one_frame_before_the_raws_shot_change_moves_onto_it():
    # one take (S02 continues S01's source) reframed at the competitor's cut, one frame before the RAW's shot change:
    # the new shot's first frame at S01's framing
    x, y = _framed(_clip24(0, 50, 1000, 1), 0.0), _framed(_clip24(50, 100, 1050, 2), 40.0)
    changes = shots.seconds([900, 1049, 2000], R24)
    assert any("at the framing of another clip" in p for p in ex._flash_lines([x, y], changes, R24, R24))
    clips, audio, done = ex.fix_cut_flashes([x, y], _a1([x, y]), changes, R24, R24)
    assert _shape(clips) == [(0, 49, 1000, 1049), (49, 100, 1049, 1100)] and done[0]["frames"] == 1
    assert [(a["start"], a["end"], a["in"]) for a in audio] == [(0, 49, 1000), (49, 100, 1049)]


# ---------------------------------------------------------------------------------------------------------------------
# 9.1 (b): a burst of the RAW's own short shots, played straight through, is no flash
# ---------------------------------------------------------------------------------------------------------------------

FG_SHOTS = shots.seconds([14520, 14521, 14523, 14524], R24)          # 1 + 2 + 1 RAW frames (Family Guy S09E11)


def test_a_burst_of_short_raw_shots_played_straight_through_is_no_flash():
    a, b = (115.935, 0.0, (0.13221, 0.15465), False), (116.066, 0.0, (0.02466, 0.15471), False)
    # the laptop's 1_edit.xml at 00:00:07:12: S16 then S17 (one take, another framing) from the burst's first frame
    items = [{"label": "S16", "start": 164, "end": 180, "in": 14504, "speed": 1.0, "framing": a},
             {"label": "S17+S18+S19+S20", "start": 180, "end": 241, "in": 14520, "speed": 1.0, "framing": b}]
    old = shots.flash_problems(items, R24, [14520 / float(R24), 14524 / float(R24)] + FG_SHOTS[1:3])
    assert ex.played_through(items, R24, FG_SHOTS) == [0.5 * (FG_SHOTS[0] + FG_SHOTS[-1])]
    assert shots.flash_problems(items, R24, ex.played_through(items, R24, FG_SHOTS)) == []
    assert old                                            # (judged shot by shot it had looked like two flashes)
    # the same at 60 fps (the checked edit): S16 to tick 451, S17 from the burst's first tick
    i60 = [{"label": "S16", "start": 411, "end": 451, "in": 36297, "speed": 1.0, "framing": a},
           {"label": "S17+S18+S19+S20", "start": 451, "end": 603, "in": 36337, "speed": 1.0, "framing": b}]
    assert shots.flash_problems(i60, F60, ex.played_through(i60, F60, FG_SHOTS)) == []
    # a cut INTO the burst (another moment follows inside it): still a flash, as before
    cut = [{"label": "S16", "start": 164, "end": 182, "in": 14504, "speed": 1.0, "framing": a},
           {"label": "S30", "start": 182, "end": 241, "in": 20000, "speed": 1.0, "framing": b}]
    assert ex.played_through(cut, R24, FG_SHOTS) != [0.5 * (FG_SHOTS[0] + FG_SHOTS[-1])]
    assert shots.flash_problems(cut, R24, ex.played_through(cut, R24, FG_SHOTS))
    # a cut from another moment of the RAW straight INTO the burst's first frame: not played through -- as before
    into = [{"label": "S30", "start": 100, "end": 180, "in": 20000, "speed": 1.0, "framing": a},
            {"label": "S17", "start": 180, "end": 241, "in": 14520, "speed": 1.0, "framing": b}]
    assert FG_SHOTS[0] in ex.played_through(into, R24, FG_SHOTS)          # the change at the cut stays
    assert shots.flash_problems(into, R24, ex.played_through(into, R24, FG_SHOTS))


def test_one_short_shot_is_judged_as_before():
    """The night-4 rule for a single short shot is unchanged (test_night4 has the video020 case)."""
    fps = F60
    changes = [10.4, 10.48]
    take = [{"label": "S04", "start": 0, "end": 60, "speed": 1.0, "in": 600.0, "framing": None}]
    assert ex.played_through(take, fps, changes) == [0.5 * (10.4 + 10.48)]
    three = [10.4, 10.48, 11.5]                     # a short shot, then a long one: not a burst
    assert ex.played_through(take, fps, three) == [0.5 * (10.4 + 10.48), 11.5]


# ---------------------------------------------------------------------------------------------------------------------
# the fixes are listed in report.md
# ---------------------------------------------------------------------------------------------------------------------

def test_report_lists_the_fixes():
    from match_cuts import report
    ctx = SimpleNamespace(cfg=SimpleNamespace(premiere=True), exports={"ok": True, "flash_problems": []},
                          premiere_xml={"edit_fixes": ["1 flash frame removed at 00:00:24:22 (S26): ..."]}, people=None)
    text = "\n".join(report._premiere_checks(ctx))
    assert "**Fixed in the edit by itself** (1" in text and "1 flash frame removed at 00:00:24:22 (S26)" in text


def test_run18_one_take_left_in_two_clips_is_one_clip_again():
    """video14 (run 18): a repeat trimmed at a cut and the move to the RAW's rate left S09..S21 / S22 playing one
    continuous take with the same framing as two clips (XML: "one continuous RAW take ... but two clips"): one clip,
    its A1 items one too. A seam where A1 does not continue stays a cut."""
    clips = [_clip24(0, 60, 1000), _clip24(60, 100, 1060, 2)]
    audio = [{"start": 0, "end": 60, "in": 1000, "out": 1060, "speed": 1.0, "what": "picture"},
             {"start": 60, "end": 100, "in": 1060, "out": 1100, "speed": 1.0, "what": "picture"}]
    c2, a2 = ex.join_seams(clips, audio)
    assert [(c.rec_start, c.rec_end, c.src_in, c.src_out) for c in c2] == [(0, 100, 1000, 1100)]
    assert [(a["start"], a["end"], a["in"], a["out"]) for a in a2] == [(0, 100, 1000, 1100)]
    jump = [_clip24(0, 60, 1000), _clip24(60, 100, 1061, 2)]                 # a real cut (a frame skipped): kept
    assert len(ex.join_seams(jump, audio)[0]) == 2
    line = [dict(audio[0]), dict(audio[1], **{"in": 2000, "out": 2040})]     # A1 jumps there: the cut stays
    c3, a3 = ex.join_seams(clips, line)
    assert len(c3) == 2 and len(a3) == 2
    # the real video14: the clip before is the last piece of a clip with a framing per RAW shot (its list kept, the
    # change before its own range), and S22's framing is S21's to the 7th digit -- as the XML's check reads them: one
    piece = dataclasses.replace(_clip24(0, 60, 1000), shot_framing=[(900, _clip24(0, 1, 0).keys[0][1])])
    s = piece.keys[0][1]
    s22 = dataclasses.replace(_clip24(60, 100, 1060, 2), keys=[(1060, Sim(s.s, 0.0, s.tx + 1e-4, s.ty))])
    assert len(ex.join_seams([piece, s22], audio)[0]) == 2                  # (exact framing: two)
    c4, _ = ex.join_seams([piece, s22], audio, (1920.0, 1080.0))
    assert len(c4) == 1 and not c4[0].shot_framing
    inside = dataclasses.replace(piece, shot_framing=[(1030, s)])            # a change inside its own range: kept
    assert len(ex.join_seams([inside, s22], audio, (1920.0, 1080.0))[0]) == 2
