"""export_xml_edl (prompt Stage 8; DESIGN §5 export_xml_edl.py): cutlist.csv, FCP7 XML (xmeml v5) and
CMX3600 EDL for a cutlist with every feature (1.10x, flip + rotation, animated keys, crossfade, NOT-IN-RAW,
dip to black, reverse and freeze remaps); M2 values; round trip through validate_exports (OTIO cmx_3600 /
fcp_xml adapters + own parsers); total duration == competitor frames; tampered files are caught; equal-rate,
NTSC-competitor, mixed-nominal-rate and gap cutlists; fill-mode XML geometry."""
from __future__ import annotations

import copy
import csv
import math
import re
from fractions import Fraction
from pathlib import Path

import pytest

try:
    import opentimelineio as otio
except ImportError:          # optional (no wheel for every Python, e.g. 3.14 on Windows): its re-parse checks skip
    otio = None

from match_cuts import export_xml_edl as ex
from match_cuts.config import Config
from match_cuts.geometry import Sim, sim_to_ae
from match_cuts.model import Box, Cutlist, Segment

need_otio = pytest.mark.skipif(otio is None, reason="OpenTimelineIO not installed")
RF = Fraction(30000, 1001)
N = 300
SIM = {"scale": 0.52, "rotation_deg": 0.0, "tx": -10.0, "ty": 480.0}
XF = {"type": "crossfade", "duration_frames": 6, "alpha": [i / 6 for i in range(6)]}
DIP = {"type": "dip_black", "duration_frames": 4, "alpha": [0, .25, .5, .75]}
BOX = {"x": 60.4, "y": 459.6, "w": 959.3, "h": 1000.5, "corner_radius": 36.0}


def rt(j: int, ph: float = 0.5, rf: Fraction = RF) -> float:
    return float((j + Fraction(ph)) / rf)


def make_segments(rf: Fraction = RF) -> list[Segment]:
    return [
        Segment(id=1, type="raw", comp_in=0, comp_out=45, raw_in_seconds=rt(900, .5, rf), speed=1.0,
                transform=dict(SIM), confidence=.99),
        Segment(id=2, type="raw", comp_in=45, comp_out=90, raw_in_seconds=rt(2000, .37, rf), speed=1.1,
                transform={"scale": .6, "rotation_deg": 0.0, "tx": -80.0, "ty": 450.0}),
        Segment(id=3, type="raw", comp_in=90, comp_out=130, raw_in_seconds=rt(1500, .45, rf), speed=1.0,
                flip_h=True, transform={"scale": .55, "rotation_deg": 1.5, "tx": 20.0, "ty": 470.0}),
        Segment(id=4, type="raw", comp_in=130, comp_out=175, raw_in_seconds=rt(3000, .6, rf), speed=1.0,
                transform=dict(SIM), transition_out=dict(XF),
                transform_keys=[{"comp_frame": 130, **SIM},
                                {"comp_frame": 174, "scale": .6, "rotation_deg": 0.0, "tx": 0.0, "ty": 432.0}]),
        Segment(id=5, type="raw", comp_in=169, comp_out=210, raw_in_seconds=rt(4000, .55, rf), speed=1.0,
                transform=dict(SIM), transition_in=dict(XF)),
        Segment(id=6, type="not_in_raw", comp_in=210, comp_out=240, label="MISSING - not in RAW (00:00:07:00)"),
        Segment(id=7, type="raw", comp_in=240, comp_out=262, raw_in_seconds=rt(1200, .5, rf), speed=1.0,
                transform=dict(SIM), transition_out=dict(DIP)),
        Segment(id=8, type="dip", comp_in=258, comp_out=270, color="#000000", transition_in=dict(DIP),
                transition_out=dict(DIP)),
        Segment(id=9, type="raw", comp_in=266, comp_out=280, raw_in_seconds=rt(1300, .5, rf), speed=1.0,
                transform=dict(SIM), transition_in=dict(DIP)),
        Segment(id=10, type="raw", comp_in=280, comp_out=290, raw_in_seconds=100.0, speed=-1.0, time_mode="remap",
                time_remap_keys=[{"comp_frame": 280, "raw_seconds": 100.0},
                                 {"comp_frame": 290, "raw_seconds": 100.0 - 10 / 30}], transform=dict(SIM)),
        Segment(id=11, type="raw", comp_in=290, comp_out=300, raw_in_seconds=rt(2500, .25, rf), speed=0.0,
                time_mode="remap", transform=dict(SIM),
                time_remap_keys=[{"comp_frame": 290, "raw_seconds": rt(2500, .25, rf)},
                                 {"comp_frame": 300, "raw_seconds": rt(2500, .25, rf)}]),
    ]


def make_cutlist(segments=None, comp_fps: str = "30/1", raw_fps: str = "30000/1001", n: int = N,
                 mode: str = "match") -> Cutlist:
    comp = {"file": "media/competitor_ref.mp4", "width": 1080, "height": 1920, "fps": comp_fps, "frames": n}
    raw = {"file": "media/raw.mp4", "file_abs": "/abs/media/raw clip.mp4", "width": 1920, "height": 1080,
           "fps": raw_fps, "frames": 5400, "has_audio": True, "audio_sample_rate": 48000, "audio_channels": 2}
    layout = {"mode": mode, "layout_kind": "boxed", "box": dict(BOX), "background": "solid",
              "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000"}
    return Cutlist(1, comp, raw, layout, segments if segments is not None else make_segments(Fraction(raw_fps)))


def exact_frame(seg: Segment, k: int, cf: Fraction = Fraction(30), rf: Fraction = RF) -> int:
    """AE rule with exact rationals (independent of the module)."""
    if seg.time_remap_keys:
        a, b = seg.time_remap_keys
        u = Fraction(k - a["comp_frame"], b["comp_frame"] - a["comp_frame"])
        return math.floor(rf * (Fraction(a["raw_seconds"]) + u * (Fraction(b["raw_seconds"]) - Fraction(a["raw_seconds"]))))
    return math.floor(rf * (Fraction(seg.raw_in_seconds) + Fraction(seg.speed) * Fraction(k - seg.comp_in) / cf))


def tc_frames(tc: str, nominal: int) -> int:
    hh, mm, ss, ff = (int(x) for x in re.split(r"[:;]", tc))
    return ((hh * 60 + mm) * 60 + ss) * nominal + ff


@pytest.fixture()
def exported(tmp_path) -> dict:
    cl = make_cutlist()
    cfg = Config(out_dir=str(tmp_path))
    paths = {k: tmp_path / f"recreated_edit.{k}" for k in ("xml", "edl")}
    csv_p = tmp_path / "cutlist.csv"
    ex.write_csv(cl, csv_p)
    ex.write_fcp7_xml(cl, paths["xml"], cfg)
    ex.write_edl(cl, paths["edl"], cfg)
    return {"cl": cl, "cfg": cfg, "csv": csv_p, **paths}


# ---------------------------------------------------------------------------------------------
# Edit model
# ---------------------------------------------------------------------------------------------

def test_edit_events_tile_the_timeline():
    ev = ex.edit_events(make_cutlist())
    assert [(e.seg_name, e.rec_in, e.rec_out, e.dissolve_in, e.tail) for e in ev] == [
        ("S01", 0, 45, 0, 0), ("S02", 45, 90, 0, 0), ("S03", 90, 130, 0, 0), ("S04", 130, 169, 0, 6),
        ("S05", 169, 210, 6, 0), ("S06", 210, 240, 0, 0), ("S07", 240, 258, 0, 4), ("S08", 258, 266, 4, 4),
        ("S09", 266, 280, 4, 0), ("S10", 280, 290, 0, 0), ("S11", 290, 300, 0, 0)]
    assert [e.kind for e in ev].count("black") == 2
    S = {s.id: s for s in make_segments()}
    for e in ev:
        if e.kind == "clip":
            assert e.src_in == exact_frame(S[e.seg.id], e.rec_in)
    assert [round(e.speed, 9) for e in ev if e.kind == "clip"][-2:] == [-1.0, 0.0]


def test_gap_becomes_black_filler(tmp_path):
    segs = make_segments()[:3]
    segs[2].comp_in = 95                                   # frames 90..94 uncovered, 130..299 uncovered
    cl = make_cutlist(segs)
    ev = ex.edit_events(cl)
    assert [(e.kind, e.rec_in, e.rec_out) for e in ev] == [("clip", 0, 45), ("clip", 45, 90), ("black", 90, 95),
                                                            ("clip", 95, 130), ("black", 130, 300)]
    ex.write_fcp7_xml(cl, tmp_path / "g.xml")
    ex.write_edl(cl, tmp_path / "g.edl")
    res = ex.validate_exports(cl, tmp_path / "g.xml", tmp_path / "g.edl")
    assert res["ok"], res["errors"]
    assert any("not covered" in w for w in res["warnings"])


# ---------------------------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------------------------

def test_csv_rows(exported):
    with open(exported["csv"], newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ex.CSV_COLUMNS
    body = [dict(zip(rows[0], r)) for r in rows[1:]]
    assert [r["#"] for r in body] == [f"S{i:02d}" for i in range(1, 12)]
    r2 = body[1]
    assert r2["speed"] == "1.1000" and r2["speed_value"] == "1.100000" and r2["comp_in"] == "45"
    assert r2["raw_in_frame"] == "2000" and r2["type"] == "raw"
    assert body[2]["flip"] == "yes" and body[2]["flip_h"] == "1" and body[2]["rotation_deg"] == "1.5000"
    assert body[3]["scale / position"].startswith("animated (2 keys") and body[3]["transition_out"] == "crossfade:6"
    assert body[5]["type"] == "not_in_raw" and "MISSING" in body[5]["RAW in-out (tc)"]
    assert body[9]["speed"].startswith("reverse") and body[10]["speed"].startswith("freeze")
    assert body[0]["comp in-out (tc / frames)"] == "00:00:00:00-00:00:01:15 (0-45)"


# ---------------------------------------------------------------------------------------------
# EDL
# ---------------------------------------------------------------------------------------------

def test_edl_text_m2_and_structure(exported):
    text = exported["edl"].read_text()
    lines = text.splitlines()
    assert lines[0] == "TITLE: Recreated Edit" and lines[1] == "FCM: NON-DROP FRAME"
    assert text.isascii() and not re.search(r"\d\d;\d\d", text)          # NDF timecodes only
    events = ex.parse_edl_text(text)
    assert len(events) == 11
    # M2 = speed * RAW fps (integer competitor rate): 29.970 for 1.0 (29.97 in 30), 32.967 for 1.10x
    m2 = [e["m2"] for e in events]
    assert m2[0] == pytest.approx(29.970) and m2[1] == pytest.approx(32.967)
    assert m2[9] == pytest.approx(-29.970) and m2[10] == 0.0
    assert "M2   AX       032.967" in text and "M2   AX       000.000" in text
    S = {s.id: s for s in make_segments()}
    for e, seg_id in zip(events, range(1, 12)):
        seg = S[seg_id]
        assert tc_frames(e["rec_in"], 30) == ex.edit_events(exported["cl"])[seg_id - 1].rec_in
        if seg.type == "raw":
            assert e["reel"] == "AX" and tc_frames(e["src_in"], 30) == exact_frame(seg, tc_frames(e["rec_in"], 30))
            assert e["m2_tc"] == e["src_in"]
        else:
            assert e["reel"] == "BL" and e["m2"] is None
    # source duration of the 1.10x event = rec frames x 1.1 x 29.97/30, rounded
    e2 = events[1]
    assert tc_frames(e2["src_out"], 30) - tc_frames(e2["src_in"], 30) == round(45 * 1.1 * 30000 / 1001 / 30)
    # dissolves: A-side cut line + D line on the same event number
    diss = [e for e in events if e["trans"] == "D"]
    assert [(e["num"], e["dissolve"]) for e in diss] == [(5, 6), (8, 4), (9, 4)]
    assert diss[0]["a_side"]["rec_in"] == diss[0]["rec_in"] == "00:00:05:19"
    assert sum(1 for ln in lines if ln.startswith("* LOC:")) == 11


@need_otio
def test_edl_otio_parse_total_duration(exported):
    tl = otio.adapters.read_from_file(str(exported["edl"]), adapter_name="cmx_3600", rate=30)
    v = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video][0]
    assert v.duration().rescaled_to(30).value == N
    clips = [c for c in v if isinstance(c, otio.schema.Clip)]
    assert len(clips) == 11 and sum(isinstance(c, otio.schema.Transition) for c in v) == 3
    warps = [fx.time_scalar for fx in clips[1].effects if isinstance(fx, otio.schema.LinearTimeWarp)]
    assert warps and warps[0] * 30 == pytest.approx(32.967)
    assert isinstance(clips[5].media_reference, otio.schema.GeneratorReference)


# ---------------------------------------------------------------------------------------------
# FCP7 XML
# ---------------------------------------------------------------------------------------------

def test_xml_structure_rates_speed_motion(exported):
    text = exported["xml"].read_text()
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5">')
    x = ex.parse_fcp7_xml(exported["xml"])
    assert x["rate"] == Fraction(30) and x["duration"] == N and (x["width"], x["height"]) == (1080, 1920)
    items = x["items"]
    assert [(i["start"], i["end"]) for i in items] == [(e.rec_in, e.rec_out) for e in ex.edit_events(exported["cl"])]
    assert [i["generator"] for i in items].count(True) == 2
    assert all(i["rate"] == RF for i in items if not i["generator"])
    assert items[1]["speed"] == pytest.approx(1.1) and items[0]["speed"] == 1.0
    assert items[9]["speed"] == pytest.approx(-1.0) and items[10]["speed"] == 0.0
    assert items[2]["flip"] and not any(i["flip"] for j, i in enumerate(items) if j != 2)
    assert [(t["start"], t["end"], t["alignment"]) for t in x["transitions"]] == \
        [(169, 175, "start"), (258, 262, "start"), (266, 270, "start")]
    assert len(x["markers"]) == 11 and len(x["audio_items"]) == 9
    # in = the AE-rule RAW frame at the record in; A's out includes the dissolve handle
    S = {s.id: s for s in make_segments()}
    assert items[3]["in"] == exact_frame(S[4], 130) and items[3]["out"] - items[3]["in"] == 45
    assert items[4]["in"] == exact_frame(S[5], 169)
    # Basic Motion of S01 from sim_to_ae (competitor-size sequence): scale 100 s, centre offset / frame size
    import xml.etree.ElementTree as ET
    root = ET.parse(exported["xml"]).getroot()
    clip1 = root.find("sequence/media/video/track/clipitem")
    basic = [e for e in clip1.findall("filter/effect") if e.findtext("effectid") == "basic"][0]
    vals = {p.findtext("parameterid"): p for p in basic.findall("parameter")}
    assert float(vals["scale"].findtext("value")) == pytest.approx(52.0)
    ae = sim_to_ae(Sim.from_dict(SIM), False, 1920, 1080)
    assert float(vals["center"].findtext("value/horiz")) == pytest.approx((ae.position[0] - 540) / 1080, abs=1e-6)
    assert float(vals["center"].findtext("value/vert")) == pytest.approx((ae.position[1] - 960) / 1920, abs=1e-6)
    crop = [e for e in clip1.findall("filter/effect") if e.findtext("effectid") == "crop"][0]
    cv = {p.findtext("parameterid"): float(p.findtext("value")) for p in crop.findall("parameter")}
    assert cv["left"] == pytest.approx(100 * (BOX["x"] - SIM["tx"]) / SIM["scale"] / 1920, abs=1e-3)
    assert cv["top"] == pytest.approx(0.0, abs=1e-3)                    # box top above the RAW top -> 0
    # the file is defined once and referenced afterwards
    files = root.findall(".//file")
    assert sum(1 for f in files if f.find("pathurl") is not None) == 1
    assert files[0].findtext("pathurl") == "file://localhost/abs/media/raw%20clip.mp4"
    # animated S04: keyframes at the media frames of its keys (first key = <in>)
    clip4 = root.findall("sequence/media/video/track/clipitem")[3]
    whens = [int(k.findtext("when")) for k in clip4.findall("filter/effect/parameter/keyframe")[:2]]
    assert whens == [exact_frame(S[4], 130), exact_frame(S[4], 174)]


@need_otio
def test_xml_otio_parse(exported):
    tl = otio.adapters.read_from_file(str(exported["xml"]), adapter_name="fcp_xml")
    v = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video][0]
    assert round(v.duration().rescaled_to(30).value) == N
    clips = [c for c in v if isinstance(c, otio.schema.Clip)]
    assert len(clips) == 11 and clips[0].name == "S01 raw clip.mp4"
    assert round(clips[4].source_range.start_time.rescaled_to(float(RF)).value) == exact_frame(make_segments()[4], 169)


# ---------------------------------------------------------------------------------------------
# validate_exports
# ---------------------------------------------------------------------------------------------

def test_validate_exports_round_trip(exported):
    res = ex.validate_exports(exported["cl"], exported["xml"], exported["edl"])
    assert res["ok"], res["errors"]
    assert res["total_frames"] == N and res["events"] == 11
    if otio is None:
        assert res["edl"]["otio"]["status"] == res["xml"]["otio"]["status"] == "not_available"
    else:
        assert res["edl"]["otio"]["status"] == "ok" and res["edl"]["otio"]["total_frames"] == N
        assert res["xml"]["otio"]["status"] == "ok" and res["xml"]["otio"]["total_frames"] == N
    assert res["edl"]["own"]["total_frames"] == N and res["xml"]["own"]["total_frames"] == N


def test_validate_exports_catches_tampering(exported, tmp_path):
    cl = exported["cl"]
    edl = tmp_path / "bad.edl"
    edl.write_text(exported["edl"].read_text().replace("M2   AX       032.967", "M2   AX       029.970"))
    res = ex.validate_exports(cl, exported["xml"], edl)
    assert not res["ok"] and any("S02" in e and "speed" in e for e in res["errors"])
    xml = tmp_path / "bad.xml"
    xml.write_text(exported["xml"].read_text().replace("<in>2000</in>", "<in>2001</in>", 1))
    res = ex.validate_exports(cl, xml, exported["edl"])
    assert not res["ok"] and any("S02" in e and "in 2001" in e for e in res["errors"])
    short = tmp_path / "short.edl"                             # drop the last event -> duration mismatch
    txt = exported["edl"].read_text()
    short.write_text(txt[:txt.index("\n011  ")] + "\n")
    res = ex.validate_exports(cl, exported["xml"], short)
    assert not res["ok"] and any("ends at frame 290" in e for e in res["errors"])
    res = ex.validate_exports(cl, tmp_path / "missing.xml", exported["edl"])
    assert not res["ok"] and any("does not exist" in e for e in res["errors"])


@pytest.mark.parametrize("comp_fps,raw_fps,m2_1,m2_11", [
    ("30/1", "30/1", None, 33.0),                  # equal rates: normal speed needs no M2
    ("30000/1001", "30000/1001", None, 33.0),      # NTSC competitor: nominal 30.000 = normal speed
    ("30/1", "24000/1001", 23.976, 26.374),        # mixed nominal rates: M2 always, source TC at 24
])
def test_other_rate_combinations(tmp_path, comp_fps, raw_fps, m2_1, m2_11):
    rf = Fraction(raw_fps)
    segs = make_segments(rf)[:3]
    segs[2].comp_out = 120
    cl = make_cutlist(segs, comp_fps=comp_fps, raw_fps=raw_fps, n=120)
    ex.write_edl(cl, tmp_path / "e.edl")
    ex.write_fcp7_xml(cl, tmp_path / "e.xml")
    events = ex.parse_edl_text((tmp_path / "e.edl").read_text())
    if m2_1 is None:
        assert events[0]["m2"] is None
    else:
        assert events[0]["m2"] == pytest.approx(m2_1, abs=5e-4)
    assert events[1]["m2"] == pytest.approx(m2_11, abs=5e-4)
    nominal = round(float(rf))
    for e, seg in zip(events, segs):
        assert tc_frames(e["src_in"], nominal) == exact_frame(seg, seg.comp_in, Fraction(comp_fps), rf)
    res = ex.validate_exports(cl, tmp_path / "e.xml", tmp_path / "e.edl")
    assert res["ok"], res["errors"]
    x = ex.parse_fcp7_xml(tmp_path / "e.xml")
    assert x["rate"] == Fraction(comp_fps) and x["items"][0]["rate"] == rf


def test_fill_and_source_mode_xml_geometry(tmp_path):
    from match_cuts.export_ae import fill_transform
    cl = make_cutlist(make_segments()[:2], n=90, mode="fill")
    ex.write_fcp7_xml(cl, tmp_path / "f.xml", Config(layout_mode="fill", comp_size="720x1280"))
    x = ex.parse_fcp7_xml(tmp_path / "f.xml")
    assert (x["width"], x["height"]) == (720, 1280)
    import xml.etree.ElementTree as ET
    root = ET.parse(tmp_path / "f.xml").getroot()
    clip = root.find("sequence/media/video/track/clipitem")
    basic = [e for e in clip.findall("filter/effect") if e.findtext("effectid") == "basic"][0]
    scale = float([p for p in basic.findall("parameter") if p.findtext("parameterid") == "scale"][0].findtext("value"))
    want = fill_transform(Sim.from_dict(SIM), False, Box.from_dict(BOX), (1920, 1080), (720, 1280))
    assert scale == pytest.approx(100 * want.s, abs=1e-4)
    assert not [e for e in clip.findall("filter/effect") if e.findtext("effectid") == "crop"]
    cl2 = copy.deepcopy(cl)
    cl2.layout["mode"] = "source"
    ex.write_fcp7_xml(cl2, tmp_path / "s.xml", Config(layout_mode="source"))
    x2 = ex.parse_fcp7_xml(tmp_path / "s.xml")
    assert (x2["width"], x2["height"]) == (1920, 1080) and x2["rate"] == Fraction(30)
    res = ex.validate_exports(cl2, tmp_path / "s.xml", tmp_path / "f.xml")      # not an EDL -> errors only there
    assert any(e.startswith("EDL") for e in res["errors"])
    assert not any(e.startswith("XML") for e in res["errors"])


# ---------------------------------------------------------------------------------------------
# Review fixes: CSV timecodes like the report (REQ-8), added-audio placeholders (REQ-4)
# ---------------------------------------------------------------------------------------------

def test_csv_timecodes_are_drop_frame_like_the_report_while_the_edl_stays_ndf(tmp_path):
    """REQ-8: for 29.97 media cutlist.csv shows the same (drop-frame) timecode as report.md and the AE layer
    names (common.timecode); only the EDL, whose FCM header says NON-DROP FRAME, keeps NDF."""
    from match_cuts.common import timecode
    cf = rf = Fraction(30000, 1001)
    j = 17982                                                        # 00:10:00;00 DF == 00:09:59:12 NDF
    seg = Segment(id=1, type="raw", comp_in=17982, comp_out=18027, raw_in_seconds=rt(j, .5, rf), raw_in_frame=j,
                  raw_out_frame=j + 44, speed=1.0, transform=dict(SIM))
    cl = make_cutlist([Segment(id=0, type="raw", comp_in=0, comp_out=17982, raw_in_seconds=rt(0, .5, rf),
                               speed=1.0, transform=dict(SIM)), seg],
                      comp_fps="30000/1001", raw_fps="30000/1001", n=18027)
    cl.raw["frames"] = 60000
    ex.write_csv(cl, tmp_path / "c.csv")
    with open(tmp_path / "c.csv", newline="", encoding="utf-8") as f:
        rows = [dict(zip(ex.CSV_COLUMNS, r)) for r in list(csv.reader(f))[1:]]
    row = rows[1]
    assert timecode(j, rf) == "00:10:00;00"
    assert row["RAW in-out (tc)"].startswith(f"00:10:00;00-{timecode(j + 44, rf)} (raw_in ")
    assert row["comp in-out (tc / frames)"] == f"00:10:00;00-{timecode(18027, cf)} (17982-18027)"
    # the EDL keeps NDF source and record timecodes (FCM: NON-DROP FRAME)
    ex.write_edl(cl, tmp_path / "c.edl")
    text = (tmp_path / "c.edl").read_text()
    assert "FCM: NON-DROP FRAME" in text and not re.search(r"\d\d;\d\d", text)
    ev = ex.parse_edl_text(text)[1]
    assert ev["src_in"] == "00:09:59:12" and ev["rec_in"] == "00:09:59:12"


def _with_added_audio(cl: Cutlist) -> Cutlist:
    cl.added_audio = [{"type": "music", "comp_in": 0, "comp_out": 300, "level_db": -6.5, "level_dbfs": -23.6},
                      {"type": "voice_over", "comp_in": 100, "comp_out": 175, "level_db": None},
                      {"type": "sfx", "comp_in": 212, "comp_out": 220}]
    return cl


def test_added_audio_becomes_labelled_edl_and_xml_placeholders(tmp_path):
    """REQ-4: the music / SFX / voice-over the competitor added get labelled placeholders in the EDL
    (YELLOW locator comments) and the FCP7 XML (sequence range markers), and validation checks them."""
    cl = _with_added_audio(make_cutlist())
    mk = ex.added_audio_markers(cl)
    assert [(m["label"], m["comp_in"], m["comp_out"]) for m in mk] == [
        ("MUSIC placeholder 00:00:00:00-00:00:10:00", 0, 300),
        ("VOICE-OVER placeholder 00:00:03:10-00:00:05:25", 100, 175),
        ("SFX placeholder 00:00:07:02-00:00:07:10", 212, 220)]
    cfg = Config(out_dir=str(tmp_path))
    xml, edl = tmp_path / "a.xml", tmp_path / "a.edl"
    ex.write_fcp7_xml(cl, xml, cfg)
    ex.write_edl(cl, edl, cfg)
    # EDL: a YELLOW locator on the event where each range starts (S01, S03, S06)
    text = edl.read_text()
    assert text.isascii()
    events = ex.parse_edl_text(text)
    locs = {e["num"]: [c for c in e["comments"] if c.startswith("LOC:") and "YELLOW" in c] for e in events}
    assert locs[1] == ["LOC: 00:00:00:00 YELLOW  MUSIC placeholder 00:00:00:00-00:00:10:00 (competitor-added music, "
                       "not recreated - add your own, -6.5 dB re RAW audio)"]
    assert locs[3][0].startswith("LOC: 00:00:03:10 YELLOW  VOICE-OVER placeholder 00:00:03:10-00:00:05:25")
    assert locs[6][0].startswith("LOC: 00:00:07:02 YELLOW  SFX placeholder 00:00:07:02-00:00:07:10")
    assert sum(len(v) for v in locs.values()) == 3
    if otio is not None:
        tl = otio.adapters.read_from_file(str(edl), adapter_name="cmx_3600", rate=30)
        v = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video][0]
        yellow = [m.name for c in v if isinstance(c, otio.schema.Clip) for m in c.markers if m.color == "YELLOW"]
        assert len(yellow) == 3 and yellow[0].startswith("MUSIC placeholder")
    # XML: sequence range markers [comp_in, comp_out)
    x = ex.parse_fcp7_xml(xml)
    got = [(m["name"], m["in"], m["out"]) for m in x["markers"] if "placeholder" in m["name"]]
    assert got == [(m["label"], m["comp_in"], m["comp_out"]) for m in mk]
    if otio is not None:
        tlx = otio.adapters.read_from_file(str(xml), adapter_name="fcp_xml")
        rng = {m.name: (m.marked_range.start_time.value, m.marked_range.duration.value) for m in tlx.tracks.markers}
        assert rng["VOICE-OVER placeholder 00:00:03:10-00:00:05:25"] == (100, 75)
    res = ex.validate_exports(cl, xml, edl)
    assert res["ok"], res["errors"]
    # validation catches a missing placeholder
    bad = tmp_path / "bad.edl"
    bad.write_text("\n".join(ln for ln in text.splitlines() if "SFX placeholder" not in ln) + "\n")
    res = ex.validate_exports(cl, xml, bad)
    assert not res["ok"] and any("SFX placeholder" in e for e in res["errors"])
    badx = tmp_path / "bad.xml"
    badx.write_text(xml.read_text().replace("<out>175</out>", "<out>174</out>"))
    res = ex.validate_exports(cl, badx, edl)
    assert not res["ok"] and any("VOICE-OVER placeholder" in e for e in res["errors"])


def test_fullscreen_segment_is_cropped_to_and_framed_from_its_own_box(tmp_path):
    """D1: a segment of a fullscreen period (Segment.box = the whole canvas) is cropped to the canvas, not
    to the Video Box, in match mode, and framed from its own box in fill mode (like export_ae)."""
    from match_cuts.export_ae import fill_transform
    import xml.etree.ElementTree as ET
    full = {"x": 0.0, "y": 0.0, "w": 1080.0, "h": 1920.0, "corner_radius": 0.0}
    cover = {"scale": 1.8, "rotation_deg": 0.0, "tx": 540 - 1.8 * 960, "ty": 0.0}
    segs = make_segments()[:2]
    segs[1] = Segment(id=2, type="raw", comp_in=45, comp_out=90, raw_in_seconds=rt(2000), speed=1.0,
                      transform=dict(cover), box=dict(full), region=1)
    cl = make_cutlist(segs, n=90)

    def crops(path) -> list[dict]:
        root = ET.parse(path).getroot()
        out = []
        for ci in root.findall("sequence/media/video/track/clipitem"):
            crop = [e for e in ci.findall("filter/effect") if e.findtext("effectid") == "crop"]
            out.append({p.findtext("parameterid"): float(p.findtext("value")) for p in crop[0].findall("parameter")}
                       if crop else {})
        return out

    ex.write_fcp7_xml(cl, tmp_path / "m.xml", Config())
    c = crops(tmp_path / "m.xml")
    assert c[0]["top"] == pytest.approx(0.0, abs=1e-3) and c[0]["left"] > 1.0      # S01: the Video Box
    # S02: the RAW (1728 px wide, 1944 high) cropped to the canvas only: 18.75 % left/right, ~1.2 % top/bottom
    assert c[1]["left"] == pytest.approx(100 * (0 - cover["tx"]) / 1.8 / 1920, abs=1e-3)
    assert c[1]["top"] == pytest.approx(0.0, abs=1e-3)
    assert c[1]["bottom"] == pytest.approx(100 * (1080 - 1920 / 1.8) / 1080, abs=1e-3)
    cl.layout["mode"] = "fill"
    ex.write_fcp7_xml(cl, tmp_path / "f.xml", Config(layout_mode="fill", comp_size="720x1280"))
    root = ET.parse(tmp_path / "f.xml").getroot()
    scales = []
    for ci in root.findall("sequence/media/video/track/clipitem"):
        basic = [e for e in ci.findall("filter/effect") if e.findtext("effectid") == "basic"][0]
        scales.append(float([p for p in basic.findall("parameter") if p.findtext("parameterid") == "scale"][0]
                            .findtext("value")))
    want1 = fill_transform(Sim.from_dict(SIM), False, Box.from_dict(BOX), (1920, 1080), (720, 1280))
    want2 = fill_transform(Sim.from_dict(cover), False, Box.from_dict(full), (1920, 1080), (720, 1280))
    assert scales == [pytest.approx(100 * want1.s, abs=1e-4), pytest.approx(100 * want2.s, abs=1e-4)]
    res = ex.validate_exports(cl, tmp_path / "f.xml", tmp_path / "f.xml")
    assert not any(e.startswith("XML") for e in res["errors"])


# ---------------------------------------------------------------------------------------------
# audio sync (DESIGN §7 D9, FX-13) and audio lines (FX-14) in the editorial formats
# ---------------------------------------------------------------------------------------------

def _competitor_sync(cl: Cutlist, lag_ms: float = -86.0, baseline_ms: float = 48.0) -> Cutlist:
    cl.audio = {"status": "ok", "av_offset": {"status": "measured", "lag_ms": lag_ms, "switch_baseline_ms": baseline_ms,
                                              "sync_mode": "competitor"}}
    cl.settings = {"audio_sync": "competitor"}
    return cl


def test_competitor_sync_audio_events_nearest_frame_and_remainder(tmp_path):
    """--audio-sync competitor: every audible segment gets its own audio event, moved by round(48 ms x 30) = 1 frame
    and playing RAW time tau + v g (g = -86 ms): source in = the NEAREST RAW frame, the sub-frame remainder written
    next to it (XML clip comment, EDL '* AUDIO' line); the video events stay exact, both files validate."""
    cl = _competitor_sync(make_cutlist())
    items = ex.audio_items(cl)
    by = {it.seg.id: it for it in items}
    assert set(by) == {1, 2, 3, 4, 5, 7, 9, 10, 11}                       # placeholders / dips play nothing
    s1 = cl.segments[0]
    it = by[1]
    assert (it.rec_in, it.rec_out) == (1, 46)
    tau = s1.raw_in_seconds + 1.0 * (1 / 30 - 0.086)                       # its map at record frame 1, 86 ms earlier
    assert it.src_in == round(tau * float(RF)) and it.remainder_ms == pytest.approx((tau - it.src_in / float(RF)) * 1000)
    assert abs(it.remainder_ms) <= 0.5e3 / float(RF) + 1e-9
    assert by[2].speed == pytest.approx(1.1) and by[11].rec_out == N         # the last one is cut at the end
    xml, edl = tmp_path / "e.xml", tmp_path / "e.edl"
    ex.write_fcp7_xml(cl, xml, Config(out_dir=str(tmp_path)))
    ex.write_edl(cl, edl, Config(out_dir=str(tmp_path)))
    res = ex.validate_exports(cl, xml, edl)
    assert res["ok"], res["errors"]
    own = ex.parse_fcp7_xml(xml)
    assert [(a["start"], a["end"], a["in"]) for a in own["audio_items"]] == [(i.rec_in, i.rec_out, i.src_in) for i in items]
    assert "remainder" in xml.read_text() and any(m["name"] == "Audio sync" for m in own["markers"])
    evs = ex.parse_edl_text(edl.read_text())
    assert {e["chan"] for e in evs} == {"V", "A"} and sum(e["chan"] == "A" for e in evs) == len(items)
    assert "* AUDIO: S01 picture, nearest RAW frame, remainder" in edl.read_text()
    if otio is not None:
        tl = otio.adapters.read_from_file(str(edl), adapter_name="cmx_3600", rate=30.0)
        assert [t.kind for t in tl.tracks].count(otio.schema.TrackKind.Audio) >= 1
    # tampering with an audio event is caught
    a1 = next(e for e in evs if e["chan"] == "A")
    bad = tmp_path / "bad.edl"
    bad.write_text(edl.read_text().replace(f"{a1['src_in']} {a1['src_out']} {a1['rec_in']}",
                                           f"{a1['src_out']} {a1['src_out']} {a1['rec_in']}", 1))
    assert not ex.validate_exports(cl, xml, bad)["ok"]


def test_raw_sync_exports_unchanged_and_audio_lines_get_their_own_events(tmp_path):
    """RAW sync without audio lines: the audio follows the picture (B events, clipitems at the video ranges) exactly
    as before. An audio line (FX-14) -- here under the NOT-IN-RAW placeholder -- makes the audio separate events:
    the placeholder gets the line's audio event, the video events become V."""
    cl = make_cutlist()
    assert ex.audio_items(cl) == [] and not ex.audio_sync_info(cl)["split"]
    segs = make_segments()
    segs[5].audio = {"in_offset_frames": 0, "out_offset_frames": 0, "exception": None, "lag_ms": 0.0, "corr": 0.95,
                     "line": {"id": 175, "raw_in_seconds": rt(4100, .3), "speed": 1.0, "source": "S05 continued",
                              "lag_ms": 0.0, "corr": 0.95}}
    cl = make_cutlist(segments=segs)
    items = {it.seg.id: it for it in ex.audio_items(cl)}
    assert items[6].what == "audio line" and (items[6].rec_in, items[6].rec_out) == (210, 240)
    assert items[6].src_in == round(rt(4100, .3) * float(RF)) and items[1].rec_in == 0
    xml, edl = tmp_path / "l.xml", tmp_path / "l.edl"
    ex.write_fcp7_xml(cl, xml, Config(out_dir=str(tmp_path)))
    ex.write_edl(cl, edl, Config(out_dir=str(tmp_path)))
    assert ex.validate_exports(cl, xml, edl)["ok"]
    assert "S06 audio line, nearest RAW frame" in edl.read_text()


# ---------------------------------------------------------------------------------------------
# Premiere-only export (--premiere): 1080x1920 at 60.00 fps, the template window, A1, markers
# ---------------------------------------------------------------------------------------------

R24 = Fraction(24000, 1001)
PBOX = {"x": 30.0, "y": 316.0, "w": 548.0, "h": 569.37, "corner_radius": 48.0}      # the real run's video box
PAN0 = {"scale": 0.546, "rotation_deg": 0.0, "tx": -246.7, "ty": 306.6}
WIN = (42.0, 555.0, 998.0, 1037.0)


def _grid_seg(id_, a, b, n30, **kw) -> Segment:
    """A RAW segment whose raw_in is on the competitor's n/30 grid (as in the real run) with a 4/1001-frame feasible
    interval around it (a cadence-pinned phase): the interval holds exactly one 1/60 s tick."""
    raw_in = float(Fraction(n30, 30))
    iv = [raw_in - 0.00008, raw_in + 0.00008]
    kw.setdefault("transform", dict(PAN0))
    return Segment(id=id_, type="raw", comp_in=a, comp_out=b, raw_in_seconds=raw_in, raw_in_interval=iv,
                   speed=kw.pop("speed", 1.0), confidence=.97, **kw)


def premiere_cutlist() -> Cutlist:
    segs = [
        _grid_seg(1, 0, 40, 198),                                            # plain shot
        _grid_seg(2, 40, 70, 420, transform_keys=[                           # editor pan: 2 keys
            {"comp_frame": 40, **PAN0}, {"comp_frame": 69, "scale": 0.556, "rotation_deg": 0.0, "tx": -300.0,
                                          "ty": 300.0}]),
        _grid_seg(3, 70, 100, 900, transform=dict(PAN0, ty=322.0)),          # top edge 6 px inside the box: zoom < 5 %
        _grid_seg(4, 100, 130, 1200, speed=1.1, transition_out=dict(XF)),
        _grid_seg(5, 124, 160, 1500, transition_in=dict(XF)),
        Segment(id=6, type="uncertain", comp_in=160, comp_out=190, label="UNCERTAIN - best RAW 2000-2024, ZNCC 0.80-0.83",
                audio={"line": {"id": "L1", "raw_in_seconds": 51.2, "speed": 1.0, "source": "S05 continued"}}),
        Segment(id=7, type="not_in_raw", comp_in=190, comp_out=220, label="MISSING - not in RAW (00:00:06:10)"),
        _grid_seg(8, 220, 250, 2100, transform={"scale": 0.40, "rotation_deg": 0.0, "tx": 0.0, "ty": 380.0}),  # small
        Segment(id=9, type="raw", comp_in=250, comp_out=270, raw_in_seconds=90.0, speed=0.0, time_mode="remap",
                transform=dict(PAN0), time_remap_keys=[{"comp_frame": 250, "raw_seconds": 90.0},
                                                       {"comp_frame": 270, "raw_seconds": 90.0}]),
        _grid_seg(10, 270, 300, 2400),
    ]
    comp = {"file": "media/competitor_ref.mp4", "width": 608, "height": 1080, "fps": "30/1", "frames": 300}
    raw = {"file": "media/raw.mp4", "file_abs": "/abs/media/raw.mp4", "width": 1920, "height": 1080,
           "fps": "24000/1001", "frames": 3445, "has_audio": True, "audio_sample_rate": 48000, "audio_channels": 2}
    layout = {"mode": "match", "layout_kind": "boxed", "box": dict(PBOX), "background": "solid",
              "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000"}
    return Cutlist(1, comp, raw, layout, segs)


def _premiere_export(tmp_path, **cfg_kw) -> dict:
    cl = premiere_cutlist()
    cfg = Config(out_dir=str(tmp_path), premiere=True, **cfg_kw)
    xml, edl = tmp_path / "recreated_edit.xml", tmp_path / "recreated_edit.edl"
    res = ex.write_premiere_xml(cl, xml, cfg)
    ex.write_edl(cl, edl, cfg)
    return {"cl": cl, "cfg": cfg, "xml": xml, "edl": edl, "res": res, "x": ex.parse_premiere_xml(xml)}


@pytest.fixture()
def premiere(tmp_path) -> dict:
    return _premiere_export(tmp_path)


@pytest.fixture()
def premiere_keyframed(tmp_path) -> dict:
    """The earlier Premiere framing (premiere_static_framing=False): the competitor's pans / zooms as keyframes."""
    return _premiere_export(tmp_path, premiere_static_framing=False)


def _motion_sims(c: dict, raw_wh=(1920.0, 1080.0)) -> list[Sim]:
    m = c["motion"]
    if m["keys"]:
        return [ex._sim_from_motion(s[1], r[1], ce[1], 1080, 1920, raw_wh)
                for s, r, ce in zip(m["keys"]["scale"], m["keys"]["rotation"], m["keys"]["center"])]
    return [ex._sim_from_motion(m["scale"], m["rotation"], m["center"], 1080, 1920, raw_wh)]


def test_premiere_sequence_is_1080x1920_at_exactly_60fps_with_v1_and_a1_only(premiere):
    x = premiere["x"]
    assert (x["timebase"], x["ntsc"]) == (60, "FALSE")                      # 60.00, not 59.94
    assert (x["width"], x["height"]) == (1080, 1920)
    assert x["duration"] == 600 and x["video_tracks"] == 1 and x["audio_tracks"] == 1
    assert not x.get("generators") and not x["transitions"] or all(t["end"] > t["start"] for t in x["transitions"])
    assert all((c["timebase"], c["ntsc"]) == (60, "FALSE") for c in x["clips"])
    text = premiere["xml"].read_text(encoding="utf-8")
    assert "<timebase>60</timebase>" in text and "<ntsc>TRUE</ntsc>" not in text.split("<file")[0]
    v = ex.validate_premiere_exports(premiere["cl"], premiere["xml"], premiere["edl"], premiere["cfg"])
    assert v["ok"], v["errors"]


def test_premiere_cuts_land_on_the_30fps_moments_and_a1_has_the_same_cuts(premiere):
    cl, x = premiere["cl"], premiere["x"]
    events = ex.edit_events(cl)
    clip_events = [ev for ev in events if ev.kind == "clip"]
    clips, _, _ = ex.premiere_clips(cl, premiere["cfg"])
    assert [(c.rec_start, c.rec_end) for c in clips] == [(2 * ev.rec_in, 2 * ev.rec_out) for ev in clip_events]
    # the crossfade S04 -> S05 is a cross dissolve starting at the edit point, 2 x 6 frames
    assert x["transitions"] == [{"start": 248, "end": 260}]
    # A1: one item per V1 clip at its record range (the freeze is silent) + the uncertain spot's audio line
    a = {(i["start"], i["end"]) for i in x["audio"]}
    v_ranges = {(c.rec_start, c.rec_end) for c in clips if c.seg.id != 9}
    assert v_ranges <= a and (320, 380) in a and (380, 440) not in a and (500, 540) not in a
    assert {(i["start"], i["end"]) for i in x["audio"]} <= {(2 * ev.rec_in, 2 * ev.rec_out) for ev in events}
    pic = {c.rec_start: c for c in clips}
    for i in x["audio"]:
        if i["start"] in pic and pic[i["start"]].seg.type == "raw":
            assert i["in"] == pic[i["start"]].src_in                       # A1 locked to V1's source in-point
    line = next(i for i in x["audio"] if i["start"] == 320)
    assert line["in"] == round(51.2 * 60)                                  # the audio line's RAW time, 1/60 s


def test_premiere_markers_on_uncertain_and_not_in_raw_spots(premiere):
    ms = {(m["in"], m["out"]): m for m in premiere["x"]["markers"]}
    assert ms[(320, 380)]["name"] == "UNCERTAIN S06" and "best RAW 2000-2024" in ms[(320, 380)]["comment"]
    assert ms[(380, 440)]["name"] == "NOT IN RAW S07"
    assert ms[(500, 540)]["name"] == "RETIME S09" and "freeze" in ms[(500, 540)]["comment"]
    assert not any(m["name"].startswith("Cut") for m in premiere["x"]["markers"])
    assert not any(c["label"].startswith(("S06", "S07")) for c in premiere["x"]["clips"])  # V1 empty there


def test_premiere_framing_fills_the_window_keeps_the_competitor_view_and_zooms_at_most_5_percent(premiere_keyframed):
    premiere = premiere_keyframed
    cl, x = premiere["cl"], premiere["x"]
    clips, _, warnings = ex.premiere_clips(cl, premiere["cfg"])
    box = Box.from_dict(PBOX)
    k = max(WIN[2] / box.w, WIN[3] / box.h)
    wc, bc = (WIN[0] + WIN[2] / 2, WIN[1] + WIN[3] / 2), (box.x + box.w / 2, box.y + box.h / 2)
    for c, got in zip(clips, x["clips"]):
        comp = [Sim.from_dict(t) for t in (c.seg.transform_keys or [c.seg.transform])]
        for ps, cs in zip(_motion_sims(got), comp):
            a, b = ex._inv(ps, wc), ex._inv(cs, bc)
            assert math.hypot(a[0] - b[0], a[1] - b[1]) < 0.05               # same RAW point at the window centre
            z = ps.s / (cs.s * k)
            assert 1.0 - 1e-6 <= z <= 1.05 + 1e-6                         # 6-decimal XML values
            if c.covered:
                assert ex._covers(ps, (1920.0, 1080.0), WIN, tol=0.01)
    z = {c.seg.id: c.zoom for c in clips}
    assert z[1] == 1.0 and 1.0 < z[3] < 1.05                              # S03 needs a small zoom to cover the window
    assert z[8] == 1.05 and not next(c for c in clips if c.seg.id == 8).covered
    assert any("S08: the RAW does not cover the template window" in w for w in warnings)
    # the clip comment states the Premiere Effect Controls values to check after import
    text = premiere["xml"].read_text(encoding="utf-8")
    assert "Premiere Motion (first key): Position" in text


def test_premiere_pan_stays_motion_keyframes_at_source_times(premiere_keyframed):
    cl, x = premiere_keyframed["cl"], premiere_keyframed["x"]
    got = next(c for c in x["clips"] if c["label"].startswith("S02"))
    keys = got["motion"]["keys"]
    assert len(keys["scale"]) == len(keys["center"]) == 2
    whens = [w for w, _ in keys["center"]]
    assert whens[0] == got["in"] and whens[1] == got["in"] + 58               # comp frames 40 -> 69 = 29 x 2 ticks
    (_, c0), (_, c1) = keys["center"]
    assert c0 != c1 and keys["scale"][0][1] != keys["scale"][1][1]


def test_premiere_source_in_is_a_60fps_tick_inside_the_frame_exact_interval(premiere):
    cl = premiere["cl"]
    clips, _, _ = ex.premiere_clips(cl, premiere["cfg"])
    for c in clips:
        if c.seg.time_remap_keys:
            continue
        assert c.in_exact, c.seg.id
        # every competitor frame k shows the plan's RAW frame at its first 60 fps frame (floor sampling)
        for k in range(c.ev.rec_in, c.ev.rec_out):
            t = Fraction(c.src_in, 60) + Fraction(2 * (k - c.ev.rec_in), 60) * Fraction(c.speed).limit_denominator(1000)
            assert math.floor(t * R24) == ex.seg_raw_frame(c.seg, k, Fraction(30), R24), (c.seg.id, k)


def test_premiere_validation_catches_a_wrong_rate_an_extra_track_and_a_moved_cut(premiere, tmp_path):
    cl, cfg = premiere["cl"], premiere["cfg"]
    text = premiere["xml"].read_text(encoding="utf-8")
    cases = {
        "ntsc": text.replace("<ntsc>FALSE</ntsc>", "<ntsc>TRUE</ntsc>", 1),
        "v2": text.replace("</track>", "</track>\n<track></track>", 1),
        "cut": text.replace("<end>80</end>", "<end>82</end>", 1),
    }
    for name, t in cases.items():
        p = tmp_path / f"bad_{name}.xml"
        p.write_text(t, encoding="utf-8")
        v = ex.validate_premiere_exports(cl, p, premiere["edl"], cfg)
        assert not v["ok"], name


def test_premiere_needs_a_whole_number_of_sequence_frames_per_competitor_frame():
    assert ex.premiere_factor(Fraction(30), Fraction(60)) == 2
    with pytest.raises(ValueError, match="cannot be placed frame-exactly"):
        ex.premiere_factor(Fraction(30000, 1001), Fraction(60))


def test_premiere_flag_skips_after_effects():
    from match_cuts import cli, pipeline
    cfg = cli.config_from_args(cli.build_parser().parse_args(["--premiere"]))
    assert cfg.premiere is True and "premiere" not in cfg.analysis_params()
    assert cli.config_from_args(cli.build_parser().parse_args([])).premiere is False

    class Ctx:
        pass
    ctx = Ctx()
    ctx.cfg, ctx.ae_run, ctx.plan = cfg, {}, None
    pipeline.stage_ae(ctx)                                                   # no plan, no JSX, no After Effects
    assert ctx.plan is None and ctx.ae_run["status"] == "not_available" and "--premiere" in ctx.ae_run["reason"]


# ---- --premiere default: no camera movement ------------------------------------------------------------------------

def test_premiere_default_one_fixed_framing_per_clip_covering_the_window(tmp_path):
    premiere = _premiere_export(tmp_path, premiere_min_move=0)       # each clip's own framing (no --min-move hold)
    cl, x, cfg = premiere["cl"], premiere["x"], premiere["cfg"]
    clips, _, _ = ex.premiere_clips(cl, cfg)
    raw_wh = (1920.0, 1080.0)
    for c, got in zip(clips, x["clips"]):
        m = got["motion"]
        assert not m["keys"], got["name"]                                  # no keyframes at all
        assert float(m["rotation"]) == 0.0
        ps = ex._sim_from_motion(m["scale"], m["rotation"], m["center"], 1080, 1920, raw_wh)
        assert ex._covers(ps, raw_wh, WIN, tol=0.01), got["name"]          # every clip fully covers the window
        assert c.covered
    box = Box.from_dict(PBOX)
    by = {c.seg.id: c for c in clips}
    # S01 already covers the window: exactly the competitor's framing
    s01 = by[1].keys[0][1]
    want = ex._window_map(Sim.from_dict(PAN0), box, WIN)
    assert abs(s01.s - want.s) < 1e-9 and abs(s01.tx - want.tx) < 1e-6 and abs(s01.ty - want.ty) < 1e-6
    # S02 pans and zooms: one framing, the average of what the competitor shows over the clip
    s02 = by[2].keys[0][1]
    k0 = ex._window_map(Sim.from_dict(PAN0), box, WIN)
    k1 = ex._window_map(Sim(0.556, 0.0, -300.0, 300.0), box, WIN)
    assert min(k0.s, k1.s) < s02.s < max(k0.s, k1.s)
    # S08 is a small shot: scaled up as far as needed to cover (more than 5 %), no more
    s08 = by[8].keys[0][1]
    assert abs(s08.s - max(WIN[2] / 1920.0, WIN[3] / 1080.0)) < 1e-6 and by[8].zoom > 1.05
    v = ex.validate_premiere_exports(cl, premiere["xml"], premiere["edl"], cfg)
    assert v["ok"], v["errors"]


def test_static_framing_moves_the_least_and_zooms_only_when_needed():
    raw_wh = (1920.0, 1080.0)
    win = WIN
    s_min = max(win[2] / 1920.0, win[3] / 1080.0)
    # big enough but shifted off the window: same scale, moved just enough to cover
    sim = Sim(1.2, 0.0, win[0] + 10.0, win[1] - 50.0)
    one, z = ex._static_framing([(0.0, sim)], 0, 10, raw_wh, win)
    assert abs(one.s - 1.2) < 1e-6 and abs(z - 1.0) < 1e-6
    assert one.tx == pytest.approx(win[0], abs=1e-6) and one.ty == pytest.approx(win[1] - 50.0, abs=1e-6)
    assert ex._covers(one, raw_wh, win)
    # too small: scaled to the smallest cover, the centre as close as possible to the competitor's
    sim = Sim(0.5, 0.0, 100.0, 700.0)
    one, z = ex._static_framing([(0.0, sim)], 0, 10, raw_wh, win)
    assert one.s == pytest.approx(s_min, rel=1e-6) and z == pytest.approx(s_min / 0.5, rel=1e-6)
    assert ex._covers(one, raw_wh, win) and one.theta_deg == 0.0
    # a rotated competitor framing is held level
    one, _ = ex._static_framing([(0.0, Sim(1.2, 3.0, 0.0, 400.0))], 0, 10, raw_wh, win)
    assert one.theta_deg == 0.0 and ex._covers(one, raw_wh, win)


# ---- --min-move: reframe only for a move of 250 px or more, one clip per take and framing -----------------------------

K_BOX = max(WIN[2] / PBOX["w"], WIN[3] / PBOX["h"])                   # competitor px -> sequence px (the box -> window)
C0 = (-246.7 + 0.546 * 960, 306.6 + 0.546 * 540)                       # PAN0's RAW centre in competitor px


def _framed(dx: float = 0.0, zoom: float = 1.0) -> dict:
    """PAN0 moved dx SEQUENCE px sideways and zoomed about its centre."""
    s = 0.546 * zoom
    cx, cy = C0[0] + dx / K_BOX, C0[1]
    return {"scale": s, "rotation_deg": 0.0, "tx": cx - s * 960, "ty": cy - s * 540}


def min_move_cutlist() -> Cutlist:
    segs = [
        _grid_seg(1, 0, 40, 300, transform=_framed(0)),                    # framing A
        _grid_seg(2, 40, 70, 340, transform=_framed(100)),                 # same take, 100 px: keeps A -> one clip
        _grid_seg(3, 70, 100, 900, transform=_framed(-150)),               # real cut, 150 px from A: keeps A, a cut
        _grid_seg(4, 100, 130, 930, transform=_framed(300)),               # same take, 300 px: reframes
        _grid_seg(5, 130, 160, 960, transform=_framed(400)),               # same take, 100 px from S04: one clip
        _grid_seg(6, 160, 200, 990, transform=_framed(300, zoom=1.3)),     # same take, a 30 % zoom: edges move 286 px
    ]
    base = premiere_cutlist()
    return Cutlist(1, dict(base.competitor, frames=200), base.raw, base.layout, segs)


def _min_move_export(tmp_path, **cfg_kw) -> dict:
    cl = min_move_cutlist()
    cfg = Config(out_dir=str(tmp_path), premiere=True, **cfg_kw)
    xml, edl = tmp_path / "recreated_edit.xml", tmp_path / "recreated_edit.edl"
    ex.write_premiere_xml(cl, xml, cfg)
    ex.write_edl(cl, edl, cfg)
    return {"cl": cl, "cfg": cfg, "xml": xml, "edl": edl, "x": ex.parse_premiere_xml(xml),
            "v": ex.validate_premiere_exports(cl, xml, edl, cfg)}


def test_framing_move_is_the_biggest_movement_of_the_centre_or_an_edge():
    raw_wh = (1920.0, 1080.0)
    a = Sim(1.0, 0.0, -400.0, 400.0)
    assert ex.framing_move(a, Sim(1.0, 0.0, -300.0, 400.0), raw_wh) == pytest.approx(100.0)       # a pan
    assert ex.framing_move(a, Sim(1.0, 0.0, -200.0, 600.0), raw_wh) == pytest.approx(math.hypot(200, 200))  # diagonal
    z = Sim(1.2, 0.0, -400.0 - 0.1 * 1920, 400.0 - 0.1 * 1080)        # 20 % zoom about the centre: centre still
    assert ex.framing_move(a, z, raw_wh) == pytest.approx(0.1 * 1920)                               # edges 192 px
    assert ex.framing_move(a, a, raw_wh) == 0.0


def test_min_move_keeps_the_framing_under_250_px_and_merges_one_take(tmp_path):
    r = _min_move_export(tmp_path)
    assert r["v"]["ok"], r["v"]["errors"]
    clips, _, _ = ex.premiere_clips(r["cl"], r["cfg"])
    assert [c.label for c in clips] == ["S01+S02", "S03", "S04+S05", "S06"]
    assert [(c.rec_start, c.rec_end) for c in clips] == [(0, 140), (140, 200), (200, 320), (320, 400)]
    x = r["x"]
    assert [(c["start"], c["end"], c["in"], c["out"]) for c in x["clips"]] == [
        (0, 140, 600, 740), (140, 200, 1800, 1860), (200, 320, 1860, 1980), (320, 400, 1980, 2060)]
    own = {c.seg.id: c.keys[0][1] for c in ex.premiere_clips(r["cl"], Config(premiere=True, premiere_min_move=0))[0]}
    f = [_motion_sims(c)[0] for c in x["clips"]]
    same = lambda a, b: abs(a.s - b.s) < 1e-6 and abs(a.tx - b.tx) < 0.01 and abs(a.ty - b.ty) < 0.01  # noqa: E731
    assert same(f[0], own[1])                        # S01's own framing for the whole take S01+S02
    assert same(f[1], own[1])                        # a real cut stays a cut but keeps the framing (150 px < 250)
    assert same(f[2], own[4]) and same(f[3], own[6])  # reframed at 300 px and at a 30 % zoom
    raw_wh = (1920.0, 1080.0)
    assert ex.framing_move(own[1], own[2], raw_wh) == pytest.approx(100.0, abs=0.5)
    assert ex.framing_move(own[4], own[6], raw_wh) == pytest.approx(0.3 * 0.546 * K_BOX * 960, abs=0.5)
    assert all(ex._covers(s, raw_wh, WIN, tol=0.01) and not c["motion"]["keys"] for s, c in zip(f, x["clips"]))
    xv = r["v"]["xml"]
    assert (xv["clips"], xv["framing_changes"], xv["merged"], xv["min_move"]) == (4, 2, 2, 250.0)
    # A1: one clip per V1 clip, the same cuts and source in-points
    assert [(a["start"], a["end"], a["in"]) for a in x["audio"]] == [(c["start"], c["end"], c["in"]) for c in x["clips"]]
    text = r["xml"].read_text(encoding="utf-8")
    assert "framing kept from S01: the competitor's moves 150 px here, under --min-move 250" in text
    assert "one clip for S01+S02 (one continuous RAW take, same framing)" in text


def test_min_move_zero_gives_every_piece_its_own_framing(tmp_path):
    r = _min_move_export(tmp_path, premiere_min_move=0)
    assert r["v"]["ok"], r["v"]["errors"]
    assert [c["label"] for c in r["x"]["clips"]] == ["S01", "S02", "S03", "S04", "S05", "S06"]
    assert (r["v"]["xml"]["framing_changes"], r["v"]["xml"]["merged"]) == (5, 0)


def test_min_move_validation_catches_a_small_reframe(tmp_path):
    r = _min_move_export(tmp_path)
    text = r["xml"].read_text(encoding="utf-8")
    i = _clip_item(text, "S03")
    j = text.index("<horiz>", i)
    k = text.index("</horiz>", j)
    bad = text[:j + 7] + f"{float(text[j + 7:k]) + 0.05:.6f}" + text[k:]   # S03 moved 0.05 x 1920 (source) = 96 px
    p = tmp_path / "bad.xml"
    p.write_text(bad, encoding="utf-8")
    v = ex.validate_premiere_exports(r["cl"], p, r["edl"], r["cfg"])
    assert any("S03: the framing changes by 96 px after S01+S02 (under --min-move 250)" in e for e in v["errors"])


def test_min_move_held_framing_that_would_not_cover_changes_the_least():
    raw_wh = (1920.0, 1080.0)
    seg1, seg2 = Segment(id=1, type="raw", comp_in=0, comp_out=10), Segment(id=2, type="raw", comp_in=10, comp_out=20)
    gap = Sim(1.0, 0.0, 100.0, 530.0)                       # leaves the window's left edge (x 42-100) empty
    near = Sim(1.0, 0.0, 30.0, 530.0)                       # this clip's own framing, 70 px away: under 250
    a = ex.PremiereClip(seg1, None, 0, 20, 0, 20, 0, 20, 1.0, True, 0.0, 1.0, False, [(0, gap)], None)
    b = ex.PremiereClip(seg2, None, 20, 40, 20, 40, 100, 120, 1.0, True, 0.0, 1.0, True, [(100, near)], None)
    ex._hold_framing([a, b], raw_wh, WIN, 250.0)
    got = b.keys[0][1]
    assert got.s == gap.s and got.ty == gap.ty and got.tx == pytest.approx(WIN[0])   # moved just to the window edge
    assert b.covered and "changed the least to cover the window" in b.framing_note
    small = Sim(0.5, 0.0, 300.0, 800.0)                     # too small to cover at all: scaled to the least cover
    fixed = ex._least_cover(small, raw_wh, WIN)
    assert fixed.s == pytest.approx(max(WIN[2] / 1920.0, WIN[3] / 1080.0), rel=1e-6) and ex._covers(fixed, raw_wh, WIN)


def test_min_move_option():
    from match_cuts import cli
    parse = cli.build_parser().parse_args
    assert cli.config_from_args(parse(["--premiere"])).premiere_min_move == 250.0
    cfg = cli.config_from_args(parse(["--premiere", "--min-move", "120"]))
    assert cfg.premiere_min_move == 120.0 and "premiere_min_move" not in cfg.analysis_params()
    assert ex.premiere_settings(cfg)["min_move"] == 120.0 and ex.premiere_settings(None)["min_move"] == 250.0
    with pytest.raises(SystemExit):
        parse(["--min-move", "-5"])


# ---- Premiere reads <center> in SOURCE pixels; the hard gap check; face-centred stretches ---------------------------

SEQ, SRC = (1080, 1920), (1920, 1080)


def test_premiere_center_is_in_source_pixels_as_premiere_reads_it():
    # the user's S21: <center> (0.622738, 0.064497) at Scale 135.88 showed at Position 1735.8, 1029.7 in Premiere
    px, py = ex.premiere_position((0.622738, 0.064497), SEQ, SRC)
    assert (round(px, 1), round(py, 1)) == (1735.7, 1029.7)
    assert px - 1.358766 * 1920 / 2 == pytest.approx(431.2, abs=0.1)              # its left edge: x 42-431 black
    h, v = ex.premiere_center((1083.0, 1029.7), SEQ, SRC)                           # the user's fix, written back
    assert ex.premiere_position((h, v), SEQ, SRC) == pytest.approx((1083.0, 1029.7))
    assert h == pytest.approx((1083.0 - 540) / 1920) and v == pytest.approx((1029.7 - 960) / 1080)


def test_premiere_xml_positions_are_the_planned_ones_in_premiere_units(premiere):
    clips, _, _ = ex.premiere_clips(premiere["cl"], premiere["cfg"])
    for c, got in zip(clips, premiere["x"]["clips"]):
        want = c.keys[0][1]
        cx, cy = want.tx + want.s * 1920 / 2, want.ty + want.s * 1080 / 2              # the RAW centre (Position)
        h, v = got["motion"]["center"]
        assert ex.premiere_position((h, v), SEQ, SRC) == pytest.approx((cx, cy), abs=0.01)
        assert h == pytest.approx((cx - 540) / 1920, abs=1e-6) and v == pytest.approx((cy - 960) / 1080, abs=1e-6)


def _clip_item(text: str, label: str) -> int:
    """Where the V1 clip item of segment ``label`` starts in the XML text (every clip is named raw.mp4; the segment
    id is in its comment)."""
    return text.rindex("<clipitem", 0, text.index(f"<mastercomment1>{label} "))


def _set_motion(text: str, clip_name: str, horiz: float, vert: float, scale: float) -> str:
    i = _clip_item(text, clip_name)
    j = text.index("<parameterid>scale</parameterid>", i)
    a, b = text.index("<value>", j) + 7, text.index("</value>", j)
    text = text[:a] + f"{scale:.6f}" + text[b:]
    j = text.index("<parameterid>center</parameterid>", i)
    a, b = text.index("<horiz>", j) + 7, text.index("</horiz>", j)
    text = text[:a] + f"{horiz:.6f}" + text[b:]
    a, b = text.index("<vert>", j) + 6, text.index("</vert>", j)
    return text[:a] + f"{vert:.6f}" + text[b:]


def test_gap_check_reads_the_xml_values_and_fails_the_s21_framing(tmp_path, premiere):
    assert ex.premiere_gaps(premiere["xml"], premiere["cfg"]) == []                  # every clip covers the window
    v = ex.validate_premiere_exports(premiere["cl"], premiere["xml"], premiere["edl"], premiere["cfg"])
    assert v["ok"] and v["gaps"] == [], v["errors"]
    bad = _set_motion(premiere["xml"].read_text(encoding="utf-8"), "S03", 0.622738, 0.064497, 135.8766)
    p = tmp_path / "s21.xml"
    p.write_text(bad, encoding="utf-8")
    gaps = ex.premiere_gaps(p, premiere["cfg"])
    assert len(gaps) == 1 and gaps[0].startswith("S03 at 00:00:02:20: Position 1735.7, 1029.7 Scale 135.9")
    assert "the window is uncovered at x 42-431" in gaps[0]
    v = ex.validate_premiere_exports(premiere["cl"], p, premiere["edl"], premiere["cfg"])
    assert not v["ok"] and any(e.startswith("XML GAP S03") for e in v["errors"])
    fixed = _set_motion(premiere["xml"].read_text(encoding="utf-8"), "S03", (1083.0 - 540) / 1920,
                        (1029.7 - 960) / 1080, 135.8766)                              # the user's hand fix covers
    p.write_text(fixed, encoding="utf-8")
    assert ex.premiere_gaps(p, premiere["cfg"]) == []
    # a picture that sits too low is caught at the window's top edge
    p.write_text(_set_motion(premiere["xml"].read_text(encoding="utf-8"), "S03", 0.0, 0.2, 100.0), encoding="utf-8")
    assert ex.premiere_gaps(p, premiere["cfg"])[0].endswith("picture x -420-1500, y 636-1716; the window is "
                                                              "uncovered at y 555-636")


def _broll(seg_id: int, a: int, b: int, what: str = "NOT-IN-RAW insert") -> dict:
    return {"broll": {"replaced": what, "how": "audio", "ranges": [[a, b, seg_id, "audio"]]}}


def test_a_stretch_with_a_replaced_spot_is_face_centred_keeping_its_zoom(tmp_path, monkeypatch):
    from match_cuts import faces
    calls = []

    def fake(video, raw_fps, times, view=None):
        calls.append((len(times), view))
        return 700.0, len(times)
    monkeypatch.setattr(faces, "main_face_x", fake)
    cl = min_move_cutlist()
    cl.segments[3].audio = _broll(4, 100, 130)                     # S04 is a B-roll replacement (its framing copied)
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    clips, _, _ = ex.premiere_clips(cl, cfg)
    own = {c.seg.id: c.keys[0][1] for c in ex.premiere_clips(min_move_cutlist(), cfg)[0]}
    by = {c.label: c for c in clips}
    assert list(by) == ["S01+S02", "S03", "S04+S05", "S06"]
    f = by["S04+S05"].keys[0][1]
    assert f.s == own[4].s and f.ty == own[4].ty                                 # zoom and height kept
    assert f.tx + f.s * 700.0 == pytest.approx(WIN[0] + WIN[2] / 2)               # the face at the window centre
    assert "S04 NOT-IN-RAW insert replaced: face-centred" in by["S04+S05"].framing_note
    assert by["S01+S02"].keys[0][1] is not f and _same(by["S01+S02"].keys[0][1], own[1])   # reliable: untouched
    assert calls == [(10, calls[0][1])]                                           # 5 frames from each of S04, S05
    r = _min_move_export(tmp_path)                                                # (unpatched cutlist) still valid
    assert r["v"]["ok"]


def _same(a, b) -> bool:
    return abs(a.s - b.s) < 1e-9 and abs(a.tx - b.tx) < 1e-6 and abs(a.ty - b.ty) < 1e-6


def test_face_centred_stretch_under_min_move_of_the_one_before_keeps_that_framing(tmp_path, monkeypatch):
    from match_cuts import faces
    cl = min_move_cutlist()
    cl.segments[3].audio = _broll(4, 100, 130)
    s4 = ex.premiere_clips(min_move_cutlist(), Config(premiere=True, premiere_min_move=0))[0][3].keys[0][1]
    s1 = ex.premiere_clips(min_move_cutlist(), Config(premiere=True, premiere_min_move=0))[0][0].keys[0][1]
    fx = (WIN[0] + WIN[2] / 2 - s1.tx - 100.0) / s4.s                            # face-centring lands 100 px from S01
    monkeypatch.setattr(faces, "main_face_x", lambda *a, **k: (fx, 10))
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    xml, edl = tmp_path / "recreated_edit.xml", tmp_path / "recreated_edit.edl"
    ex.write_premiere_xml(cl, xml, cfg)
    ex.write_edl(cl, edl, cfg)
    clips, _, _ = ex.premiere_clips(cl, cfg)
    # S04's face-centred framing is under 250 px from the framing on screen (S01's): it keeps that one, so the
    # take S03-S05 is one clip with one framing
    assert [c.label for c in clips] == ["S01+S02", "S03+S04+S05", "S06"]
    assert _same(clips[1].keys[0][1], s1)
    v = ex.validate_premiere_exports(cl, xml, edl, cfg)
    assert v["ok"] and v["gaps"] == [], v["errors"]


def test_no_face_found_keeps_the_framing_and_still_covers(tmp_path, monkeypatch):
    from match_cuts import faces
    monkeypatch.setattr(faces, "main_face_x", lambda *a, **k: (None, 0))
    cl = min_move_cutlist()
    cl.segments[3].audio = _broll(4, 100, 130)
    clips, _, _ = ex.premiere_clips(cl, Config(premiere=True))
    c = next(c for c in clips if c.label == "S04+S05")
    assert "no face found, framing kept" in c.framing_note and c.covered


# ---- S21 on the real RAW: tests/real/deadpool/raw.mp4 (input/raw_test.mp4) with the export cutlist of the run on it --------------------------------

RAW_TEST = Path(__file__).resolve().parents[3] / "tests" / "real" / "deadpool" / "raw.mp4"
RAW_TEST_CUTLIST = Path(__file__).resolve().parent / "data" / "raw_test_export_cutlist.json"
need_raw_test = pytest.mark.skipif(not RAW_TEST.is_file(), reason="tests/real/deadpool not in this checkout")


@need_raw_test
def test_faces_finds_the_guest_in_the_raw():
    from match_cuts import faces
    times = [t / 60 for t in range(5926, 6400, 24)]                    # S21-S27 of the run (RAW 98.8-106.7 s)
    x, n = faces.main_face_x(RAW_TEST, 30000 / 1001, times, (98.0, 833.0))
    assert n >= 15 and 500 <= x <= 570                                  # the guest (left), not the host (x ~1450)


@need_raw_test
def test_s21_comes_out_face_centred_near_the_users_fix_and_nothing_leaves_a_gap(tmp_path):
    import json
    d = json.loads(RAW_TEST_CUTLIST.read_text(encoding="utf-8"))
    d["raw"]["file_abs"] = str(RAW_TEST)
    cl = Cutlist.from_dict(d)
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    xml, edl = tmp_path / "recreated_edit.xml", tmp_path / "recreated_edit.edl"
    from match_cuts import repeats
    plan = repeats.add_to_plan({}, cl, cfg)          # as the pipeline: S26 starts on S25's last 6 RAW frames (a stutter)
    assert [(r["removed"], r["kept"], r["a"], r["b"], r["kind"]) for r in plan["repeats"]["rows"]] == [
        ("S26", "S25", 1192, 1198, "stutter")]
    ex.write_premiere_xml(cl, xml, cfg, plan["ripple"])
    ex.write_edl(cl, edl, cfg)
    v = ex.validate_premiere_exports(cl, xml, edl, cfg, plan["ripple"])
    assert v["ok"] and v["gaps"] == [] and v["repeat_problems"] == [] and v["item_problems"] == [], v["errors"]
    ex.write_premiere_xml(cl, tmp_path / "uncut.xml", cfg)             # without the trim: the hard check fails
    assert ex.premiere_repeat_problems(tmp_path / "uncut.xml") == [
        "V1 S25 at 00:00:19:46 and S26 at 00:00:19:52 both play RAW 103.27-103.37 s: a stutter at a cut"]
    x = ex.parse_premiere_xml(xml)

    def position(c: dict) -> tuple[float, float]:
        return ex.premiere_position(c["motion"]["center"], SEQ, SRC)
    s21 = next(c for c in x["clips"] if c["label"].startswith("S21"))
    at = next(c for c in x["clips"] if c["start"] <= 1153 < c["end"])   # 00:00:19:13 in the 60 fps sequence
    for c in (s21, at):
        px, py = position(c)
        # the user's hand fix: 1083.0. YuNet (since task 2) boxes the guest's face tightly and centres it at 1147; the
        # Haar cascades' boxes leaned ~55 RAW px right (onto his ear) and landed nearer the fix. Both show the guest
        assert abs(px - 1083.0) <= 75.0, (c["name"], px)
        assert c["motion"]["scale"] == pytest.approx(135.88, abs=0.05)  # the zoom is kept
        assert px - 1.3588 * 960 <= 42 and px + 1.3588 * 960 >= 1040    # covers x 42-1039
        assert py - 1.3588 * 540 <= 555 and py + 1.3588 * 540 >= 1592   # and y 555-1591
    text = xml.read_text(encoding="utf-8")
    assert "S26 NOT-IN-RAW replaced" in text and "face-centred -- the main face" in text
