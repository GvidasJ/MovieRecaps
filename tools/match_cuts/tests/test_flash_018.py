"""video018 (output/018, 29.97 fps RAW in the 60 fps sequence): the 1-frame flash at the RAW's own shot changes.

Premiere shows at sequence frame r the RAW frame floor(t x 29.97) of the clip's source time t, so the RAW's shot change
at frame k first shows on tick ceil(k x 2.002). The run placed two cuts on the NEAREST tick instead:
  S05|S06 (00:00:09:20): S06 in 44110 = round(735.16915 x 60); RAW 22033 starts at tick 44110.066 -> S06's first
          frame is 22032, the last frame of S05's shot (same framing: a held frame; the check called it a flash
          because it dropped S05, which starts inside a 2-frame cross dissolve, and read black before S06);
  S16|S17 (00:00:20:06): S16 plays on S17's time line (in 44941 = round(749.010266 x 60), out 45012); RAW 22483 starts
          at tick 45010.966 -> S16's last frame is 22483, the next shot's first, at S16's framing (a real flash the
          check missed: it joined that frame with S17's run of the same shot, framing ignored).
The user moved both cuts onto the change: 735.1833 (tick 44111) and 750.1833 (tick 45011).
"""
from __future__ import annotations

from fractions import Fraction

from match_cuts import export_xml_edl as ex, shots
from match_cuts.config import Config
from match_cuts.model import Cutlist, Segment

RF = Fraction(30000, 1001)
FPS = Fraction(60)
CHANGES = shots.seconds([21884, 21941, 22033, 22133, 22409, 22483, 22572], RF)     # the run's RAW shot changes
BOX = {"x": 30.0, "y": 316.0, "w": 548.0, "h": 569.37, "corner_radius": 48.0}
XF = {"type": "crossfade", "duration_frames": 2, "alpha": [0.0, 0.5]}


def framing(tx: float) -> dict:
    return {"scale": 0.546, "rotation_deg": 0.0, "tx": tx, "ty": 306.6}


def cutlist() -> Cutlist:
    """video018 S04-S06 and S16-S17 at their RAW times (competitor frames from 0; 60 fps like its competitor)."""
    segs = [
        Segment(4, "raw", 0, 52, raw_in_seconds=731.25, transform=framing(-96.7), transition_out=dict(XF)),
        Segment(5, "raw", 50, 236, raw_in_seconds=732.082480882, speed=0.994429, transform=framing(-246.7),
                raw_in_interval=[732.082407162, 732.082554602], transition_in=dict(XF)),
        Segment(6, "raw", 236, 437, raw_in_seconds=735.16915, transform=framing(-246.7),
                raw_in_interval=[735.169133333, 735.169166667]),
        Segment(16, "raw", 437, 508, raw_in_seconds=749.010266, transform=framing(-246.7)),    # on S17's line
        Segment(17, "raw", 508, 674, raw_in_seconds=750.193599, transform=framing(-46.7),
                raw_in_interval=[750.1855, 750.199466667]),
    ]
    comp = {"file": "media/competitor_ref.mp4", "width": 608, "height": 1080, "fps": "60/1", "frames": 674}
    raw = {"file": "media/raw.mp4", "file_abs": "/abs/media/raw.mp4", "width": 1920, "height": 1080,
           "fps": "30000/1001", "frames": 44142, "has_audio": True, "audio_sample_rate": 48000, "audio_channels": 2}
    layout = {"mode": "match", "layout_kind": "boxed", "box": dict(BOX), "background": "solid",
              "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000"}
    return Cutlist(1, comp, raw, layout, segs)


def export(tmp_path, name: str, **cfg_kw):
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    for k, v in cfg_kw.items():
        setattr(cfg, k, v)
    xml = tmp_path / name
    ex.write_premiere_xml(cutlist(), xml, cfg)
    return cfg, xml, ex.parse_premiere_xml(xml)


def clip(x: dict, label: str) -> dict:
    return next(c for c in x["clips"] if c["label"] == label)


def test_the_flash_check_sees_the_next_shots_first_frame_at_the_clip_befores_framing():
    """S16 ends on RAW 22483 (tick 45011 >= 22483 x 2.002 = 45010.966) at its own framing; S17 shows the same shot at
    another: one frame of the next shot at the wrong framing. Today the two join into one run of that shot."""
    s16, s17 = (100.0, 0.0, (-0.21, 0.105), False), (100.0, 0.0, (0.046, 0.105), False)
    items = [{"label": "S16", "start": 1133, "end": 1207, "in": 44938, "speed": 1.0, "framing": s16},
             {"label": "S17+S18+S19", "start": 1207, "end": 1385, "in": 45012, "speed": 1.0, "framing": s17}]
    bad = shots.flash_problems(items, FPS, CHANGES)
    assert len(bad) == 1 and bad[0].startswith("S16 at 00:00:20:06: 1 frame(s) of a RAW shot at the framing")
    # the user's cut: one frame earlier, on the change (750.1833 s)
    items[0]["end"], items[1]["start"], items[1]["in"] = 1206, 1206, 45011
    assert shots.flash_problems(items, FPS, CHANGES) == []
    # one framing on both sides (S05|S06: a held frame of the shot before, not a flash)
    items[0]["framing"] = s17
    items[0]["end"], items[1]["start"], items[1]["in"] = 1207, 1207, 45012
    assert shots.flash_problems(items, FPS, CHANGES) == []


def test_the_flash_check_reads_the_clips_of_a_cross_dissolve_and_the_framing(tmp_path):
    """The XML the run wrote (no cut moved): S05 starts inside the S04|S05 dissolve; it shows RAW 22032 up to S06,
    whose first frame is 22032 again at the same framing -- no flash (today: S05 dropped, black up to S06, a
    '1 frame of a different RAW shot' at S06). S16's last frame is the next shot's first at S16's framing -- a flash
    (today: missed)."""
    cfg, xml, x = export(tmp_path, "run.xml")
    assert (clip(x, "S06")["in"], clip(x, "S16")["out"], clip(x, "S17")["in"]) == (44110, 45012, 45012)
    assert any(t["start"] == 50 for t in x["transitions"]) and clip(x, "S05")["start"] == -1
    bad = ex.premiere_flash_problems(xml, CHANGES)
    assert [b.split(":")[0] for b in bad] == ["S16 at 00"], bad
    assert "at the framing of another clip" in bad[0] and "00:00:08:27" in bad[0]       # sequence frame 507


def test_premiere_cuts_at_a_raw_shot_change_land_on_its_first_frame(tmp_path):
    """With the RAW's shot changes known (cfg.premiere_shots) the two cuts move one frame onto the change, as the user
    moved them: S05|S06 at tick 44111 (735.1833 s), S16|S17 at tick 45011 (750.1833 s). A1 keeps its cuts, the
    sequence its length, and the export validates (flash check included)."""
    cfg, xml, x = export(tmp_path, "edit.xml", premiere_shots=CHANGES)
    s05, s06, s16, s17 = (clip(x, s) for s in ("S05", "S06", "S16", "S17"))
    assert (s05["end"], s05["out"], s06["start"], s06["in"]) == (237, 44111, 237, 44111)
    assert (s16["end"], s16["out"], s17["start"], s17["in"]) == (507, 45011, 507, 45011)
    assert x["duration"] == 674
    # A1 keeps the competitor's cuts and in-points (the takes run on under the moved picture cuts)
    assert [(a["start"], a["end"], a["in"]) for a in x["audio"]] == [
        (0, 50, 43875), (50, 236, 43925), (236, 437, 44110), (437, 508, 44941), (508, 674, 45012)]
    v = ex.validate_premiere_exports(cutlist(), xml, None, cfg, None, None, CHANGES)
    assert v["flash_problems"] == [] and v["ok"], v["errors"]
