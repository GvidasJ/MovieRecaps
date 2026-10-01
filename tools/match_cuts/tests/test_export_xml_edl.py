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

import opentimelineio as otio
import pytest

from match_cuts import export_xml_edl as ex
from match_cuts.config import Config
from match_cuts.geometry import Sim, sim_to_ae
from match_cuts.model import Box, Cutlist, Segment

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
    tl = otio.adapters.read_from_file(str(edl), adapter_name="cmx_3600", rate=30)
    v = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video][0]
    yellow = [m.name for c in v if isinstance(c, otio.schema.Clip) for m in c.markers if m.color == "YELLOW"]
    assert len(yellow) == 3 and yellow[0].startswith("MUSIC placeholder")
    # XML: sequence range markers [comp_in, comp_out)
    x = ex.parse_fcp7_xml(xml)
    got = [(m["name"], m["in"], m["out"]) for m in x["markers"] if "placeholder" in m["name"]]
    assert got == [(m["label"], m["comp_in"], m["comp_out"]) for m in mk]
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
