"""export_ae (prompt Stage 7; DESIGN §2.3-2.5, §5 export_ae): ae_plan numbers, write_jsx ES3/ASCII/NaN
guards, the strict Node AE mock in every scenario, mock record == plan, simulate_ae == the AE floor rule
(computed here with exact Fractions), wrong-order key writes caught, fps-source / source / fill modes,
the +-3 h rule and the frame-exact fallback."""
from __future__ import annotations

import copy
import json
import math
import re
from fractions import Fraction
from pathlib import Path

import pytest

from match_cuts import export_ae as ea
from match_cuts.config import Config
from match_cuts.geometry import Sim, interpolate_keys, sim_to_ae
from match_cuts.model import Box, Cutlist, Segment

RF = Fraction(30000, 1001)
CF = Fraction(30)
RAW_W, RAW_H = 1920, 1080
NODE = ea._find_node()
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js not installed (run_jsx_in_mock -> not_available)")


# ---------------------------------------------------------------------------------------------
# Fixtures: a hand-built cutlist with every feature
# ---------------------------------------------------------------------------------------------

def raw_time(j0: int, phase: float) -> float:
    """RAW time whose floor-rule frame is j0 with the given fractional phase."""
    return float((j0 + Fraction(phase)) / RF)


def exact_stretch(raw_in: float, v: float, c_in: int, c_out: int, cf: Fraction = CF, rf: Fraction = RF,
                  k0: int | None = None) -> list[int]:
    """AE floor rule with exact rationals: floor(raw_fps (raw_in + v (t_k - t_in)))."""
    ri, vv = Fraction(raw_in), Fraction(v)
    k0 = c_in if k0 is None else k0
    return [math.floor(rf * (ri + vv * Fraction(k - k0) / cf)) for k in range(c_in, c_out)]


def exact_remap(keys: list[dict], c_in: int, c_out: int, rf: Fraction = RF) -> list[int]:
    ks = sorted(keys, key=lambda d: d["comp_frame"])
    out = []
    for k in range(c_in, c_out):
        if k <= ks[0]["comp_frame"]:
            val = Fraction(ks[0]["raw_seconds"])
        elif k >= ks[-1]["comp_frame"]:
            val = Fraction(ks[-1]["raw_seconds"])
        else:
            a, b = next((a, b) for a, b in zip(ks, ks[1:]) if a["comp_frame"] <= k <= b["comp_frame"])
            u = Fraction(k - a["comp_frame"], b["comp_frame"] - a["comp_frame"])
            val = Fraction(a["raw_seconds"]) + u * (Fraction(b["raw_seconds"]) - Fraction(a["raw_seconds"]))
        out.append(math.floor(rf * val))
    return out


def min_phase_margin(raw_in: float, v: float, c_in: int, c_out: int) -> float:
    ri, vv = Fraction(raw_in), Fraction(v)
    m = 1.0
    for k in range(c_in, c_out):
        x = RF * (ri + vv * Fraction(k - c_in) / CF)
        m = min(m, float(x - math.floor(x)), float(math.floor(x) + 1 - x))
    return m


def T(k: float, F=(30, 1)) -> float:
    return k * F[1] / F[0]


SIM1 = {"scale": 0.52, "rotation_deg": 0.0, "tx": -10.0, "ty": 480.0}
REV_KEYS = [{"comp_frame": 240, "raw_seconds": raw_time(3500, 0.5)},
            {"comp_frame": 260, "raw_seconds": raw_time(3500, 0.5) - 20 / 30}]
FREEZE_KEYS = [{"comp_frame": 260, "raw_seconds": raw_time(2500, 0.25)},
               {"comp_frame": 280, "raw_seconds": raw_time(2500, 0.25)}]
XFADE = {"type": "crossfade", "duration_frames": 6, "alpha": [i / 6 for i in range(6)]}


def make_segments() -> list[Segment]:
    S = []
    S.append(Segment(id=1, type="raw", comp_in=0, comp_out=45, raw_in_seconds=raw_time(900, 0.5), speed=1.0,
                     transform=dict(SIM1), confidence=0.99))
    S.append(Segment(id=2, type="raw", comp_in=45, comp_out=90, raw_in_seconds=raw_time(2000, 0.37), speed=1.1,
                     transform={"scale": 0.6, "rotation_deg": 0.0, "tx": -80.0, "ty": 450.0}, confidence=0.97))
    S.append(Segment(id=3, type="raw", comp_in=90, comp_out=130, raw_in_seconds=raw_time(1500, 0.45), speed=1.0,
                     flip_h=True, transform={"scale": 0.55, "rotation_deg": 1.5, "tx": 20.0, "ty": 470.0}))
    S.append(Segment(id=4, type="raw", comp_in=130, comp_out=175, raw_in_seconds=raw_time(3000, 0.6), speed=1.0,
                     transform={"scale": 0.5, "rotation_deg": 0.0, "tx": 60.0, "ty": 460.0},
                     transform_keys=[{"comp_frame": 130, "scale": 0.5, "rotation_deg": 0.0, "tx": 60.0, "ty": 460.0},
                                     {"comp_frame": 152, "scale": 0.55, "rotation_deg": 0.0, "tx": 30.0, "ty": 440.0},
                                     {"comp_frame": 174, "scale": 0.6, "rotation_deg": 0.0, "tx": 0.0, "ty": 432.0}],
                     transition_out=dict(XFADE)))
    S.append(Segment(id=5, type="raw", comp_in=169, comp_out=210, raw_in_seconds=raw_time(4000, 0.55), speed=1.0,
                     transform=dict(SIM1), transition_in=dict(XFADE)))
    S.append(Segment(id=6, type="not_in_raw", comp_in=210, comp_out=240, label="stock – clip"))
    S.append(Segment(id=7, type="raw", comp_in=240, comp_out=260, raw_in_seconds=REV_KEYS[0]["raw_seconds"], speed=-1.0,
                     time_mode="remap", time_remap_keys=copy.deepcopy(REV_KEYS), transform=dict(SIM1)))
    S.append(Segment(id=8, type="raw", comp_in=260, comp_out=280, raw_in_seconds=FREEZE_KEYS[0]["raw_seconds"], speed=0.0,
                     time_mode="remap", time_remap_keys=copy.deepcopy(FREEZE_KEYS), transform=dict(SIM1)))
    S.append(Segment(id=9, type="raw", comp_in=280, comp_out=300, raw_in_seconds=raw_time(1200, 0.5), speed=1.0,
                     transform=dict(SIM1),
                     audio={"in_offset_frames": -4, "out_offset_frames": 0, "pitch_preserved": None, "lag_ms": None,
                            "corr": None, "exception": None}))
    return S


def make_cutlist(segments=None, raw_frames=5400, raw_file="media/raw.mp4", box=None, comp_frames=300,
                 background=None) -> Cutlist:
    box = box if box is not None else {"x": 60.4, "y": 459.6, "w": 959.3, "h": 1000.5, "corner_radius": 36.0}
    layout = {"mode": "match", "layout_kind": "boxed", "canvas_bg": "#000000", "box": box,
              "background": (background or {}).get("type", "solid"),
              "background_detail": background or {"type": "solid", "color": "#000000"},
              "zones": [{"type": "title", "x": 90, "y": 260, "w": 900, "h": 140, "comp_in": None, "comp_out": None},
                        {"type": "watermark", "x": 400, "y": 1500, "w": 280, "h": 60, "comp_in": None, "comp_out": None}],
              "captions": [{"comp_in": 12, "comp_out": 40, "x": 200, "y": 900, "w": 600, "h": 90},
                           {"comp_in": 50, "comp_out": 80, "x": 180, "y": 910, "w": 640, "h": 80}]}
    comp = {"file": "media/competitor_ref.mp4", "file_rel": "media/competitor_ref.mp4", "file_abs": "/abs/competitor_ref.mp4",
            "width": 1080, "height": 1920, "fps": "30/1", "frames": comp_frames, "has_audio": True}
    raw = {"file": raw_file, "file_rel": raw_file, "file_abs": "/abs/" + Path(raw_file).name, "width": RAW_W,
           "height": RAW_H, "fps": "30000/1001", "frames": raw_frames, "conformed": False, "has_audio": True}
    return Cutlist(1, comp, raw, layout, segments if segments is not None else make_segments())


def meta_for(cl: Cutlist) -> dict:
    return ea.footage_meta_from_cutlist(cl)


def build(tmp_path: Path, cl: Cutlist | None = None, **cfg_kw):
    cl = cl or make_cutlist()
    cfg = Config(**cfg_kw)
    plan = ea.ae_plan(cl, cfg, meta_for(cl))
    jsx = tmp_path / "build_ae_project.jsx"
    ea.write_jsx(cl, plan, jsx, cfg)
    return cl, cfg, plan, jsx


def layer(plan: dict, lid: str) -> dict:
    return next(L for L in plan["layers"] if L["id"] == lid)


def expected_frames(cl: Cutlist) -> dict[str, dict[int, int]]:
    """Exact floor-rule RAW frame per comp frame for every RAW segment (reference for simulate_ae)."""
    out = {}
    for s in cl.segments:
        if s.type != "raw":
            continue
        if s.time_remap_keys:
            js = exact_remap(s.time_remap_keys, s.comp_in, s.comp_out)
        else:
            js = exact_stretch(s.raw_in_seconds, s.speed, s.comp_in, s.comp_out)
        out[f"seg{s.id}"] = dict(zip(range(s.comp_in, s.comp_out), js))
    return out


# ---------------------------------------------------------------------------------------------
# ae_plan numbers
# ---------------------------------------------------------------------------------------------

def test_test_data_is_well_conditioned():
    for s in make_segments():
        if s.type == "raw" and not s.time_remap_keys:
            assert min_phase_margin(s.raw_in_seconds, s.speed, s.comp_in, s.comp_out) > 1e-4


def test_plan_main_box_and_stretch_layers():
    cl = make_cutlist()
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    assert plan["main"] == {"name": "Recreated Edit", "w": 1080, "h": 1920, "fps": {"num": 30, "den": 1},
                            "frames": 300, "duration": 10.0, "bg": [0.0, 0.0, 0.0]}
    assert plan["rawFps"] == {"num": 30000, "den": 1001}
    # integer Video Box geometry for a fractional box (DESIGN §2.3)
    b = plan["box"]
    assert (b["bx0"], b["by0"], b["bw"], b["bh"]) == (60, 459, 1020 - 60, 1461 - 459)
    assert b["mask"]["x"] == pytest.approx(0.4) and b["mask"]["y"] == pytest.approx(0.6)
    assert b["mask"]["w"] == pytest.approx(959.3) and b["mask"]["r"] == pytest.approx(36.0)
    assert plan["segComp"] == "box"
    exp = expected_frames(cl)
    L1 = layer(plan, "seg1")
    assert (L1["compIn"], L1["compOut"], L1["timeMode"]) == (0, 45, "stretch")
    assert L1["stretch"] == 100.0 and L1["startTime"] == 0.0 - cl.segments[0].raw_in_seconds
    assert L1["expect"] == [exp["seg1"][k] for k in range(0, 45)]
    assert L1["name"].startswith("S01  RAW ") and L1["name"].isascii()
    L2 = layer(plan, "seg2")
    assert L2["stretch"] == 100.0 / 1.1 and L2["timeMode"] == "stretch"
    assert L2["startTime"] == T(45) - cl.segments[1].raw_in_seconds / (100.0 / (100.0 / 1.1))
    assert L2["expect"] == [exp["seg2"][k] for k in range(45, 90)]
    assert L2["inPoint"] == T(45) and L2["outPoint"] == T(90)


def test_plan_transforms_flip_rotation_and_keys():
    cl = make_cutlist()
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    bx0, by0 = plan["box"]["bx0"], plan["box"]["by0"]
    L3 = layer(plan, "seg3")
    s = cl.segments[2].transform
    th = math.radians(s["rotation_deg"])
    cx, cy = RAW_W / 2, RAW_H / 2
    px = s["scale"] * (math.cos(th) * cx - math.sin(th) * cy) + s["tx"] - bx0
    py = s["scale"] * (math.sin(th) * cx + math.cos(th) * cy) + s["ty"] - by0
    assert L3["flip"] is True
    assert L3["xf"]["anchor"] == [cx, cy]
    assert L3["xf"]["scale"] == pytest.approx([-55.0, 55.0])
    assert L3["xf"]["rotation"] == 1.5
    assert L3["xf"]["position"] == pytest.approx([px, py], abs=1e-9)
    # animated push-in: one AE key per measured key, LINEAR AE parameters == geometry.interpolate_keys
    L4 = layer(plan, "seg4")
    keys = L4["xf"]["keys"]
    assert [k["k"] for k in keys] == [130, 152, 174] and L4["xf"]["rotKeys"] is False
    tk = cl.segments[3].transform_keys
    for k in (130, 141, 152, 163.5, 174):
        a, b = next((a, b) for a, b in zip(keys, keys[1:]) if a["k"] <= k <= b["k"])
        u = (k - a["k"]) / (b["k"] - a["k"])
        pos = [a["position"][i] + u * (b["position"][i] - a["position"][i]) for i in range(2)]
        sc = a["scale"][0] + u * (b["scale"][0] - a["scale"][0])
        sim = interpolate_keys(tk, k, RAW_W, RAW_H)
        ref = sim_to_ae(sim, False, RAW_W, RAW_H)
        assert pos == pytest.approx([ref.position[0] - bx0, ref.position[1] - by0], abs=1e-9)
        assert sc == pytest.approx(ref.scale[0], abs=1e-9)


def test_plan_crossfade_opacity_and_audio_keys():
    cl = make_cutlist()
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    L4, L5 = layer(plan, "seg4"), layer(plan, "seg5")
    # only the upper (outgoing) layer is keyed: 100 at O-1, 100(1 - alpha) at O..O+D-1, 0 at O+D
    want = [(168, 100.0)] + [(169 + i, 100.0 * (1 - i / 6)) for i in range(6)] + [(175, 0.0)]
    assert [(k["k"], k["v"]) for k in L4["opacity"]] == [(k, pytest.approx(v)) for k, v in want]
    assert L5["opacity"] == []
    # stacking: segment 4 above segment 5 in the Video Box comp
    ids = [L["id"] for L in plan["layers"] if L["comp"] == "box"]
    assert ids.index("seg4") < ids.index("seg5")
    # audio levels on both layers over the overlap (gain 1 - alpha / alpha, -60 dB floor), B back to 0 dB
    a4 = {k["k"]: k["v"] for k in L4["audioKeys"]}
    a5 = {k["k"]: k["v"] for k in L5["audioKeys"]}
    assert sorted(a4) == list(range(169, 175)) and sorted(a5) == list(range(169, 176))
    assert a4[169] == 0.0 and a4[172] == pytest.approx(20 * math.log10(0.5))
    assert a5[169] == pytest.approx(-60.0) and a5[175] == 0.0


def test_plan_placeholder_remap_jcut_markers_and_main_layers():
    cl = make_cutlist()
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    exp = expected_frames(cl)
    P6 = layer(plan, "nir6")
    assert P6["kind"] == "placeholder" and P6["comp"] == "box"
    assert P6["name"].startswith("MISSING - not in RAW (") and "stock - clip" in P6["name"] and P6["name"].isascii()
    assert (P6["w"], P6["h"]) == (plan["box"]["bw"], plan["box"]["bh"])
    L7, L8 = layer(plan, "seg7"), layer(plan, "seg8")
    assert L7["timeMode"] == "remap" and L8["timeMode"] == "remap"
    assert L7["expect"] == [exp["seg7"][k] for k in range(240, 260)] and L7["expect"][0] > L7["expect"][-1]
    assert set(L8["expect"]) == {2500}
    assert L7["startTime"] == T(240) and L7["remap"][0] == {"k": 240, "v": REV_KEYS[0]["raw_seconds"]}
    # J-cut: audio-only duplicate over [comp_in - 4, comp_out), video layer silenced
    L9, A9 = layer(plan, "seg9"), layer(plan, "seg9_audio")
    assert L9["audio"] is False and A9["audio"] is True and A9["enabled"] is False
    assert (A9["compIn"], A9["compOut"], A9["timeMode"]) == (276, 300, "stretch")
    assert A9["startTime"] == pytest.approx(L9["startTime"], abs=1e-9)
    # markers merged per frame; cut + NOT-IN-RAW texts at 210, crossfade at 169
    mk = {m["k"]: m["text"] for m in plan["markers"]}
    assert "Cut 05" in mk[210] and "MISSING" in mk[210]
    assert "Crossfade 6 fr" in mk[169] and "Cut 04" in mk[169]
    assert len(plan["markers"]) == len(mk)
    assert mk[0].startswith("Start | S01 RAW ")
    # MAIN stack: reference on top, guides, the Video Box, background
    main_ids = [L["id"] for L in plan["layers"] if L["comp"] == "main"]
    assert main_ids[0] == "ref" and main_ids[-1] == "bg_solid" and "box" in main_ids
    ref = layer(plan, "ref")
    assert ref["guide"] and not ref["enabled"] and not ref["audio"] and ref["blend"] == "difference"
    assert ref["name"] == "REFERENCE - competitor (turn on: black = match)"
    guides = [L for L in plan["layers"] if L["kind"] == "guide"]
    assert {g["name"].split(" ")[2] for g in guides} == {"title", "watermark", "captions"}
    assert all(g["guide"] and g["comp"] == "main" for g in guides)
    box = layer(plan, "box")
    assert box["xf"]["anchor"] == [0.0, 0.0] and box["xf"]["position"] == [60.0, 459.0] and box["mask"] is not None
    assert plan["summary"]["raw"] == 8 and plan["summary"]["placeholders"] == 1 and plan["summary"]["cuts"] == 8


def test_plan_blurred_background_and_comp_size():
    cl = make_cutlist(background={"type": "blur", "color": "#101010", "sigma": 12})
    plan = ea.ae_plan(cl, Config(comp_size="720x1280"), meta_for(cl))
    r = 2.0 / 3.0
    assert plan["r"] == pytest.approx(r) and (plan["main"]["w"], plan["main"]["h"]) == (720, 1280)
    g = ea.box_geometry(Box.from_dict(cl.layout["box"]), r)
    assert {k: plan["box"][k] for k in ("bx0", "by0", "bw", "bh")} == {k: g[k] for k in ("bx0", "by0", "bw", "bh")}
    L1 = layer(plan, "seg1")
    ae = sim_to_ae(Sim.from_dict(SIM1), False, RAW_W, RAW_H, r=r)
    assert L1["xf"]["position"] == pytest.approx([ae.position[0] - g["bx0"], ae.position[1] - g["by0"]], abs=1e-9)
    assert L1["xf"]["scale"] == pytest.approx([52 * r, 52 * r])
    bg = layer(plan, "bg_blur")
    assert bg["source"] == "box" and bg["blur"] == {"amount": 36.0} and bg["audio"] is False
    ids = [L["id"] for L in plan["layers"] if L["comp"] == "main"]
    assert ids.index("box") < ids.index("bg_blur") < ids.index("bg_solid")
    with pytest.raises(ValueError, match="aspect"):
        ea.ae_plan(cl, Config(comp_size="800x1280"), meta_for(cl))


def test_plan_three_hour_rule_forces_remap():
    segs = [Segment(id=1, type="raw", comp_in=0, comp_out=60, raw_in_seconds=raw_time(345000, 0.5), speed=1.0,
                    transform=dict(SIM1))]
    cl = make_cutlist(segs, raw_frames=400000, comp_frames=60)
    plan = ea.ae_plan(cl, Config(ae_time_mode="stretch"), meta_for(cl))
    L = layer(plan, "seg1")
    assert abs(L["startStretch"]) > 10799 and L["timeMode"] == "remap" and L["startTime"] == 0.0
    assert L["expect"] == exact_stretch(segs[0].raw_in_seconds, 1.0, 0, 60)
    assert any("outside AE's layer limits" in w for w in plan["warnings"])


def test_plan_frames_mode_and_time_mode_override():
    cl = make_cutlist()
    plan = ea.ae_plan(cl, Config(ae_time_mode="frames"), meta_for(cl))
    raws = [L for L in plan["layers"] if L["kind"] == "raw"]
    assert all(L["timeMode"] == "frames" and L["startTime"] == T(L["compIn"]) for L in raws)
    assert all(not L["audio"] for L in raws)                         # audio from audio-only twins
    twins = {L["id"] for L in plan["layers"] if L["kind"] == "raw_audio"}
    assert twins == {f"{L['id']}_audio" for L in raws}
    exp = expected_frames(cl)
    for mode in (None, "frames"):
        sim = ea.raw_frames_by_layer(ea.simulate_ae(plan, mode))
        assert {k: sim[k] for k in exp} == exp
    plan_auto = ea.ae_plan(cl, Config(), meta_for(cl))
    for mode in (None, "stretch", "remap", "frames"):
        sim = ea.raw_frames_by_layer(ea.simulate_ae(plan_auto, mode))
        assert {k: sim[k] for k in exp} == exp, mode


def test_plan_fps_source_mode():
    segs = [Segment(id=1, type="raw", comp_in=0, comp_out=1000, raw_in_seconds=raw_time(100, 0.5), speed=1.0,
                    transform=dict(SIM1)),
            Segment(id=2, type="raw", comp_in=1000, comp_out=2000, raw_in_seconds=raw_time(3000, 0.5), speed=1.0,
                    transform=dict(SIM1))]
    cl = make_cutlist(segs, comp_frames=2000)
    plan = ea.ae_plan(cl, Config(fps_mode="source"), meta_for(cl))
    assert plan["main"]["fps"] == {"num": 30000, "den": 1001} and plan["main"]["frames"] == 1998
    L2 = layer(plan, "seg2")
    assert (L2["compIn"], L2["compOut"]) == (999, 1998)
    err = float(Fraction(999) / RF - Fraction(1000, 30))
    assert plan["fpsSource"]["active"] and plan["fpsSource"]["perSegment"]["2"] == pytest.approx(err)
    errs = [float(Fraction(k) / RF - Fraction(c, 30)) for k, c in ((0, 0), (999, 1000), (1998, 2000))]
    assert plan["fpsSource"]["perSegmentOut"]["2"] == pytest.approx(errs[2])
    assert plan["fpsSource"]["maxErrorS"] == pytest.approx(max(abs(e) for e in errs))
    assert L2["rawIn"] == pytest.approx(segs[1].raw_in_seconds + err, abs=1e-12)
    # on the RAW grid a speed-1 segment shows consecutive RAW frames
    assert L2["expect"] == list(range(L2["expect"][0], L2["expect"][0] + 999))
    ea.apply_fps_source_notes(cl, plan)
    assert cl.settings["fps_source_max_error_s"] == pytest.approx(max(abs(e) for e in errs), abs=1e-9)
    assert "MAIN fps 30000/1001" in cl.segments[1].notes


def test_plan_source_and_fill_layouts():
    cl = make_cutlist()
    src = ea.ae_plan(cl, Config(layout_mode="source"), meta_for(cl))
    assert (src["main"]["w"], src["main"]["h"], src["main"]["fps"]) == (RAW_W, RAW_H, {"num": 30000, "den": 1001})
    assert src["box"] is None and src["segComp"] == "main"
    L = layer(src, "seg3")
    assert L["xf"]["anchor"] == L["xf"]["position"] == [RAW_W / 2, RAW_H / 2] and L["xf"]["scale"] == [100.0, 100.0]
    assert not any(x["kind"] in ("guide", "bg_solid") for x in src["layers"])
    fill = ea.ae_plan(cl, Config(layout_mode="fill"), meta_for(cl))
    assert (fill["main"]["w"], fill["main"]["h"]) == (1080, 1920) and fill["box"] is None
    assert not any(x["kind"] == "guide" for x in fill["layers"])
    L1 = layer(fill, "seg1")
    want = ea.fill_transform(Sim.from_dict(SIM1), False, Box.from_dict(cl.layout["box"]), (RAW_W, RAW_H), (1080, 1920))
    ae = sim_to_ae(want, False, RAW_W, RAW_H)
    assert L1["xf"]["position"] == pytest.approx(list(ae.position)) and L1["xf"]["scale"] == pytest.approx(list(ae.scale))


def _corners_inside(sim: Sim, raw_wh, target_wh, tol=1e-6) -> bool:
    inv = sim.inverse()
    W, H = raw_wh
    pts = inv.apply([[0, 0], [target_wh[0], 0], [0, target_wh[1]], [target_wh[0], target_wh[1]]])
    return bool((pts[:, 0] >= -tol).all() and (pts[:, 0] <= W + tol).all() and (pts[:, 1] >= -tol).all()
                and (pts[:, 1] <= H + tol).all())


def test_fill_transform():
    box = Box(60, 460, 960, 1000, 36)
    raw, tgt = (RAW_W, RAW_H), (1080, 1920)
    # unclamped: box-centre RAW point -> frame centre, zoom = s * cover_frame / cover_box
    sim = Sim(1.2, 0.0, 540 - 1.2 * 1000, 960 - 1.2 * 560)       # RAW (1000, 560) at the box centre (540, 960)
    f = ea.fill_transform(sim, False, box, raw, tgt)
    cover_box, cover_frame = max(960 / RAW_W, 1000 / RAW_H), max(1080 / RAW_W, 1920 / RAW_H)
    assert f.s == pytest.approx(1.2 * cover_frame / cover_box)
    assert f.apply([[1000, 560]])[0] == pytest.approx([540, 960])
    assert _corners_inside(f, raw, tgt)
    # zoomed-out competitor -> clamped to the cover scale, no empty edges
    f2 = ea.fill_transform(Sim(0.4, 0.0, 100, 700), True, box, raw, tgt)
    assert f2.s == pytest.approx(cover_frame) and _corners_inside(f2, raw, tgt)
    # rotation: still covers the frame; centre point near the RAW edge gets clamped inside
    f3 = ea.fill_transform(Sim(1.1, 3.0, 540 - 1.1 * 1900, 960 - 1.1 * 540), False, box, raw, tgt)
    assert f3.theta_deg == 3.0 and _corners_inside(f3, raw, tgt)
    with pytest.raises(ValueError):
        ea.fill_transform(sim, False, None, raw, tgt)


def test_plan_rejects_nan_none_and_bad_modes():
    for bad in (float("nan"), None, float("inf")):
        segs = make_segments()
        segs[0].raw_in_seconds = bad
        with pytest.raises(ValueError):
            ea.ae_plan(make_cutlist(segs), Config(), None)
    segs = make_segments()
    segs[1].transform = {"scale": float("nan"), "rotation_deg": 0.0, "tx": 0.0, "ty": 0.0}
    with pytest.raises(ValueError, match="scale"):
        ea.ae_plan(make_cutlist(segs), Config(), None)
    segs = make_segments()
    segs[1].speed = None
    with pytest.raises(ValueError, match="speed"):
        ea.ae_plan(make_cutlist(segs), Config(), None)
    with pytest.raises(ValueError, match="ae_time_mode"):
        ea.ae_plan(make_cutlist(), Config(ae_time_mode="bogus"), None)


# ---------------------------------------------------------------------------------------------
# write_jsx: ASCII / NaN / ES3 guards
# ---------------------------------------------------------------------------------------------

def test_write_jsx_ascii_nan_and_static_es3(tmp_path):
    cl = make_cutlist(raw_file="média/räw clip.mp4")
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    jsx = tmp_path / "build_ae_project.jsx"
    ea.write_jsx(cl, plan, jsx, Config())
    text = jsx.read_text(encoding="ascii")
    assert text.isascii() and text.startswith("#target aftereffects\n")
    assert "r\\u00e4w clip.mp4" in text and "stock - clip" in text
    assert ea.es3_static_check(text) == []
    assert "NaN" not in re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)
    bad = copy.deepcopy(plan)
    bad["layers"][0]["xf"]["position"][0] = float("nan")
    with pytest.raises(ValueError, match="not finite"):
        ea.write_jsx(cl, bad, tmp_path / "bad.jsx", Config())
    assert not (tmp_path / "bad.jsx").exists()


def test_es3_static_check_flags_code_not_strings():
    ok = '#target aftereffects\nvar s = "JSON.parse() [].forEach( let x NaN";\n// a.indexOf(b) in a comment\nvar n = 1;\n'
    assert ea.es3_static_check(ok) == []
    for code in ("var a = [1].indexOf(1);", "var j = JSON;", "let x = 1;", "var y = NaN;", "var f = function () {}.bind(this);",
                 "var k = Object.keys(o);", "var z = Array.isArray(o);", "var t = Date.now();", "var q = `x`;",
                 "var s = 'café';"):
        assert ea.es3_static_check(code), code


# ---------------------------------------------------------------------------------------------
# Strict Node mock
# ---------------------------------------------------------------------------------------------

@needs_node
def test_mock_all_scenarios_and_record_equals_plan(tmp_path):
    cl, cfg, plan, jsx = build(tmp_path)
    meta = meta_for(cl)
    res = ea.mock_verify(jsx, plan, meta)
    assert res["status"] == "pass", res["failures"]
    rec = res["records"]["default"]
    assert rec["status"] == "ok" and rec["mock_errors"] == [] and rec["clamps"] == []
    assert rec["saved"] == [str(tmp_path / "recreated_edit.aep")]
    assert len(rec["alerts"]) == 1 and "Error" not in rec["alerts"][0]
    main = next(c for c in rec["comps"] if c["comment"] == "mc:main")
    assert main["frameRate"] == 30 and main["duration"] == 10 and main["workAreaDuration"] == 10 and main["opened"]
    assert main["width"] == 1080 and main["height"] == 1920
    boxc = next(c for c in rec["comps"] if c["comment"] == "mc:box")
    assert (boxc["width"], boxc["height"]) == (plan["box"]["bw"], plan["box"]["bh"])
    # one layer per segment, chronological below the dips, with exactly the plan's values
    tags = [L["comment"] for L in boxc["layers"]]
    assert tags[:9] == ["mc:seg1", "mc:seg2", "mc:seg3", "mc:seg4", "mc:seg5", "mc:nir6", "mc:seg7", "mc:seg8", "mc:seg9"]
    by = {L["comment"]: L for c in rec["comps"] for L in c["layers"]}
    for PL in plan["layers"]:
        RL = by["mc:" + PL["id"]]
        assert RL["name"] == PL["name"]
        assert (RL["startTime"], RL["inPoint"], RL["outPoint"]) == (PL["startTime"], PL["inPoint"], PL["outPoint"])
        if PL["timeMode"] == "stretch":
            assert RL["stretch"] == PL["stretch"] and not RL["timeRemapEnabled"]
    # crossfade keys only on the upper layer; position keys linear in time and space
    assert "ADBE Opacity" in by["mc:seg4"]["props"] and not by["mc:seg5"]["props"]["ADBE Opacity"]["keys"]
    pos = by["mc:seg4"]["props"]["ADBE Position"]["keys"]
    assert [k["time"] for k in pos] == pytest.approx([T(130), T(152), T(174)], abs=1e-9)
    assert all(k["inInterp"] == k["outInterp"] == "LINEAR" and k["autoBezier"] is False and k["continuous"] is False
               and k["inTangent"] == [0, 0, 0] and k["outTangent"] == [0, 0, 0] for k in pos)
    # the rounded box mask: Bezier corners on the pre-comp layer, feather 0
    m = by["mc:box"]["masks"][0]
    assert m["mode"] == "ADD" and len(m["shape"]["vertices"]) == 8 and m["feather"] == [0, 0]
    assert m["shape"]["outTangents"][1][0] == pytest.approx(0.5522847498 * 36.0)
    # reference layer and render switches
    ref = by["mc:ref"]
    assert ref["guideLayer"] and not ref["enabled"] and not ref["audioEnabled"] and ref["blendingMode"] == "DIFFERENCE"
    for tag in ("mc:seg1", "mc:seg7", "mc:box"):
        L = by[tag]
        assert (L["quality"], L["frameBlendingType"], L["samplingQuality"], L["motionBlur"]) == \
            ("BEST", "NO_FRAME_BLEND", "BILINEAR", False)
    # J-cut twin and silenced video layer
    assert by["mc:seg9_audio"]["audioEnabled"] and not by["mc:seg9_audio"]["enabled"] and not by["mc:seg9"]["audioEnabled"]
    # comp markers merged per frame
    assert [mk["time"] for mk in main["markers"]] == [T(m["k"]) for m in plan["markers"]]   # comp time: exact
    # the other scenarios
    mm = res["records"]["media_missing"]
    assert mm["calls"]["openDialog"] == 1 and mm["saved"] == [] and mm["calls"]["newProject"] == 0
    assert mm["alerts"][0].startswith("Cancelled") and mm["dialogs"] == ["Locate the RAW video"]
    npn = res["records"]["new_project_null"]
    assert npn["calls"]["newProject"] == 1 and npn["saved"] == [] and npn["alerts"][0].startswith("Cancelled")
    nm = res["records"]["no_marker_property"]
    assert nm["saved"] and "comp markers could not be added" in nm["alerts"][-1]
    assert next(c for c in nm["comps"] if c["comment"] == "mc:main")["markers"] == []
    assert len(nm["warnings"]) == 1 and nm["warnings"][0].startswith("comp markers could not be added")
    assert rec["warnings"] == []


@needs_node
def test_simulate_plan_and_record_equal_floor_rule(tmp_path):
    cl, cfg, plan, jsx = build(tmp_path)
    rec = ea.run_jsx_in_mock(jsx, meta_for(cl))
    assert rec["status"] == "ok"
    sp, sr = ea.simulate_ae(plan), ea.simulate_ae(rec)
    assert set(sp) == set(sr) == set(range(300))
    for K in range(300):
        assert [(e["layer"], e["raw_frame"]) for e in sp[K]] == [(e["layer"], e["raw_frame"]) for e in sr[K]], K
        assert [e["weight"] for e in sp[K]] == pytest.approx([e["weight"] for e in sr[K]])
    assert ea.raw_frames_by_layer(sp) == expected_frames(cl)
    # crossfade frames: both layers visible with the xfade weights (1 - alpha, alpha)
    for i in range(6):
        e = {x["layer"]: x["weight"] for x in sp[169 + i]}
        assert e["seg4"] == pytest.approx(1 - i / 6) and e["seg5"] == pytest.approx(i / 6)
    assert sp[168][0]["weight"] == 1.0 and [x["layer"] for x in sp[175]] == ["seg5"]
    assert all(sp[K] == [] for K in range(210, 240))                 # NOT-IN-RAW placeholder
    # the AE rule module (if present) agrees with the plan on stretch layers
    try:
        from match_cuts import phase_solve
    except ImportError:
        return
    for s in cl.segments:
        if s.type == "raw" and not s.time_remap_keys:
            js = [phase_solve.ae_frame(s.raw_in_seconds, s.speed, k, s.comp_in, CF, RF) for k in range(s.comp_in, s.comp_out)]
            assert js == layer(plan, f"seg{s.id}")["expect"]


@needs_node
def test_mock_catches_keys_written_before_timing(tmp_path):
    """Keys live in LAYER time: a JSX that writes Time Remap keys and THEN sets startTime shows the wrong
    frames. The mock must model this so the simulation fails (a comp-time mock would pass it)."""
    cl, cfg, plan, jsx = build(tmp_path)
    text = jsx.read_text()
    good_block = ("        L.stretch = 100;\n        L.startTime = tIn;\n        L.inPoint = tIn;\n"
                  "        L.outPoint = tOut;\n        if (!L.canSetTimeRemapEnabled)")
    assert good_block in text
    wrong = text.replace(good_block, "        L.stretch = 100;\n        L.inPoint = tIn;\n        L.outPoint = tOut;\n"
                                     "        if (!L.canSetTimeRemapEnabled)")
    wrong = wrong.replace("        setKeys(P, times, vals, frameExact);\n",
                          "        setKeys(P, times, vals, frameExact);\n        L.startTime = tIn;\n")
    bad_jsx = tmp_path / "wrong_order.jsx"
    bad_jsx.write_text(wrong)
    rec = ea.run_jsx_in_mock(bad_jsx, meta_for(cl))
    assert rec["status"] == "ok"
    exp = expected_frames(cl)
    got = ea.raw_frames_by_layer(ea.simulate_ae(rec))
    assert got.get("seg7") != exp["seg7"] and got.get("seg8") != exp["seg8"]
    assert got["seg1"] == exp["seg1"]                                  # stretch layers unaffected
    # transform keys written before the timing move too (caught by the key-time comparison)
    wrong2 = text.replace("        // timing FIRST", "        applyXf(L, s.xf);\n        // timing FIRST", 1)
    wrong2 = wrong2.replace("        renderSwitches(L, s);\n        applyXf(L, s.xf);\n", "        renderSwitches(L, s);\n")
    bad2 = tmp_path / "wrong_order2.jsx"
    bad2.write_text(wrong2)
    res = ea.mock_verify(bad2, plan, meta_for(cl), scenarios=("default",))
    assert res["status"] == "fail" and any("ADBE Position: key at" in f for f in res["failures"])
    # and the hand-written minimal snippet: identical calls, only the order differs
    snippet = """#target aftereffects
(function () {
    var here = new File($.fileName).parent;
    var io = new ImportOptions(new File(here.fsName + "/media/raw.mp4"));
    io.importAs = ImportAsType.FOOTAGE;
    app.newProject();
    var it = app.project.importFile(io);
    it.comment = "mc:raw";
    var comp = app.project.items.addComp("Recreated Edit", 1080, 1920, 1, 10, 30);
    comp.comment = "mc:main";
    var L = comp.layers.add(it);
    L.comment = "mc:seg1";
    L.stretch = 100;
    __ORDER__
    app.project.save(new File(here.fsName + "/recreated_edit.aep"));
})();
"""
    timing = "L.startTime = 2; L.inPoint = 2; L.outPoint = 4;"
    keys = ("L.timeRemapEnabled = true; var P = L.property(\"ADBE Time Remapping\");"
            "while (P.numKeys > 0) { P.removeKey(P.numKeys); }"
            "P.setValuesAtTimes([2, 4], [100.5 / 29.97002997002997, 160.5 / 29.97002997002997]);")
    results = {}
    for name, body in (("right", timing + keys), ("wrong", keys + timing)):
        p = tmp_path / f"snippet_{name}.jsx"
        p.write_text(snippet.replace("__ORDER__", body))
        r = ea.run_jsx_in_mock(p, meta_for(cl))
        assert r["status"] == "ok", r.get("error")
        results[name] = ea.raw_frames_by_layer(ea.simulate_ae(r))["seg1"]
    assert results["right"][60] == 100 and results["right"][119] == 159
    assert results["wrong"] != results["right"]


@needs_node
def test_mock_quantized_time_triggers_frame_exact_fallback(tmp_path):
    """If AE stored startTime/stretch less precisely, the JSX self-check switches the affected layers to
    frame-exact HOLD remapping (+ an audio twin) and the result is still frame exact."""
    cl, cfg, plan, jsx = build(tmp_path)
    rec = ea.run_jsx_in_mock(jsx, meta_for(cl), "quantize_time")
    assert rec["status"] == "ok" and rec["mock_errors"] == []
    by = {L["comment"]: L for c in rec["comps"] for L in c["layers"]}
    fell_back = [lid for lid in ("seg1", "seg2", "seg3", "seg4", "seg5", "seg9") if by["mc:" + lid]["timeRemapEnabled"]]
    assert fell_back, "quantisation must break at least one stretch layer"
    assert "switched this layer to frame-exact time remapping" in rec["alerts"][-1]
    for lid in fell_back:
        if lid != "seg9":
            assert by[f"mc:{lid}_audio"]["audioEnabled"] and not by[f"mc:{lid}"]["audioEnabled"]
    assert ea.raw_frames_by_layer(ea.simulate_ae(rec)) == expected_frames(cl)


@needs_node
def test_mock_frames_mode_three_hour_rule_and_range_checks(tmp_path):
    cl, cfg, plan, jsx = build(tmp_path, ae_time_mode="frames")
    res = ea.mock_verify(jsx, plan, meta_for(cl), scenarios=("default",))
    assert res["status"] == "pass", res["failures"]
    assert ea.raw_frames_by_layer(ea.simulate_ae(res["records"]["default"])) == expected_frames(cl)
    # +-3 h: stretch would need startTime ~ -11511 s; the plan uses remap and the mock accepts it
    segs = [Segment(id=1, type="raw", comp_in=0, comp_out=60, raw_in_seconds=raw_time(345000, 0.5), speed=1.0,
                    transform=dict(SIM1))]
    cl3 = make_cutlist(segs, raw_frames=400000, comp_frames=60)
    d3 = tmp_path / "long"
    d3.mkdir()
    _, _, plan3, jsx3 = build(d3, cl3)
    res3 = ea.mock_verify(jsx3, plan3, meta_for(cl3), scenarios=("default",))
    assert res3["status"] == "pass", res3["failures"]
    # forcing stretch in that plan makes the strict mock reject the layer time (AE's +-10800 s limit)
    forced = copy.deepcopy(plan3)
    L = layer(forced, "seg1")
    L["timeMode"], L["startTime"] = "stretch", L["startStretch"]
    jsx_f = d3 / "forced.jsx"
    ea.write_jsx(cl3, forced, jsx_f, Config())
    rec = ea.run_jsx_in_mock(jsx_f, meta_for(cl3))
    assert any("failed" in a for a in rec["alerts"]) and any("AVLayer.startTime" in e for e in rec["mock_errors"])
    assert rec["saved"] == []


@needs_node
def test_mock_source_fill_fps_source_run_clean(tmp_path):
    for i, kw in enumerate(({"layout_mode": "source"}, {"layout_mode": "fill"}, {"fps_mode": "source"})):
        d = tmp_path / f"m{i}"
        d.mkdir()
        cl, cfg, plan, jsx = build(d, **kw)
        res = ea.mock_verify(jsx, plan, meta_for(cl), scenarios=("default",))
        assert res["status"] == "pass", (kw, res["failures"])
        sp = ea.raw_frames_by_layer(ea.simulate_ae(plan))
        assert sp == ea.raw_frames_by_layer(ea.simulate_ae(res["records"]["default"]))


@needs_node
def test_mock_non_ascii_media_name_round_trip(tmp_path):
    cl = make_cutlist(raw_file="média/räw clip.mp4")
    _, _, plan, jsx = build(tmp_path, cl)
    rec = ea.run_jsx_in_mock(jsx, meta_for(cl))
    assert rec["status"] == "ok" and rec["saved"], rec["alerts"]
    raw = next(f for f in rec["footage"] if f["comment"] == "mc:raw")
    assert raw["file"].endswith("média/räw clip.mp4")


@needs_node
@pytest.mark.parametrize("src, needle", [
    ("var s = 'café';", "non-ASCII"),
    ("var a = seg.in;", "not ECMAScript 3"),
    ("let a = 1;", "not ECMAScript 3"),
    ("var f = function (x) { return x; }; var o = {a: 1,};", "not ECMAScript 3"),
    ("var a = [1, 2]; a.forEach(function () {});", "forbidden ES5+ call .forEach("),
    ("var t = \" x \"; t.trim();", "forbidden ES5+ call .trim("),
    ("var j = JSON.stringify({});", "forbidden identifier JSON"),
    ("var k = Object.keys({});", "forbidden ES5+ API Object.keys"),
    ("var n = NaN;", "forbidden identifier NaN"),
])
def test_mock_es3_gate_rejects(tmp_path, src, needle):
    p = tmp_path / "gate.jsx"
    p.write_bytes(("#target aftereffects\n" + src + "\n").encode("utf-8"))
    rec = ea.run_jsx_in_mock(p, {})
    assert rec["status"] == "gate_failed" and needle in rec["gate_error"], rec.get("gate_error")


STRICT_SNIPPET = """#target aftereffects
(function () {
    var out = [];
    function probe(name, fn) { try { out.push(name + "=" + fn()); } catch (e) { out.push(name + "!" + e.message); } }
    var here = new File($.fileName).parent;
    var io = new ImportOptions(new File(here.fsName + "/media/raw.mp4"));
    var it = app.project.importFile(io);
    var comp = app.project.items.addComp("C", 100, 100, 1, 10, 30000 / 1001);
    var A = comp.layers.addSolid([1, 0, 0], "first", 10, 10, 1);
    var B = comp.layers.addSolid([0, 1, 0], "second", 10, 10, 1);
    var L = comp.layers.add(it);
    probe("order", function () { return comp.layer(1).name + "," + comp.layer(2).name + "," + comp.layer(3).name; });
    probe("index", function () { return L.index + "," + B.index + "," + A.index; });
    probe("f32", function () { return (comp.frameRate === 30000 / 1001) + "," + (Math.abs(comp.frameRate - 30000 / 1001) < 1e-5); });
    probe("unknownSet", function () { L.guidelayer = true; return "no"; });
    probe("unknownGet", function () { return L.fooBar; });
    probe("displayName", function () { return L.property("Transform").name; });
    probe("readOnly", function () { it.duration = 3; return "no"; });
    probe("enum", function () { L.blendingMode = 5; return "no"; });
    probe("wrongEnum", function () { L.blendingMode = MaskMode.ADD; return "no"; });
    probe("addCompInt", function () { app.project.items.addComp("x", 100.5, 100, 1, 10, 30); return "no"; });
    probe("audioOnSolid", function () { A.audioEnabled = false; return "no"; });
    probe("noIndexOf", function () { var v = L.property("ADBE Transform Group").property("ADBE Position").value; return v.length + "," + typeof v["index" + "Of"] + "," + typeof v.slice; });
    probe("clamp", function () { L.startTime = 0; L.outPoint = 5000; return L.outPoint; });
    probe("keysMove", function () {
        var P = L.property("ADBE Transform Group").property("ADBE Opacity");
        P.setValueAtTime(2, 50);
        var before = P.keyTime(1);
        L.startTime = 1;
        return before + "," + P.keyTime(1);
    });
    probe("setValueKeyed", function () { L.property("ADBE Transform Group").property("ADBE Opacity").setValue(20); return "no"; });
    probe("tangentDims", function () {
        var P = L.property("ADBE Transform Group").property("ADBE Position");
        P.setValueAtTime(1, [1, 2]);
        P.setSpatialTangentsAtKey(1, [0, 0], [0, 0]);
        return "no";
    });
    probe("remapOff", function () { L.property("ADBE Time Remapping").setValueAtTime(1, 1); return "no"; });
    probe("defaultLayerStart", function () { return comp.layers.add(it).startTime; });
    alert(out.join("\\n"));
})();
"""


@needs_node
def test_mock_strictness(tmp_path):
    p = tmp_path / "strict.jsx"
    p.write_text(STRICT_SNIPPET)
    meta = {"raw.mp4": {"width": 1920, "height": 1080, "fps_num": 30000, "fps_den": 1001, "frames": 5400,
                        "has_audio": True}}
    rec = ea.run_jsx_in_mock(p, meta)
    assert rec["status"] == "ok", rec.get("error")
    parsed = [re.match(r"(\w+)([=!])(.*)", line).groups() for line in rec["alerts"][0].split("\n")]
    res = {name: value for name, _, value in parsed}              # probe name -> result or error message
    kinds = {name: sep for name, sep, _ in parsed}                 # '=' returned, '!' threw
    assert res["order"] == "raw.mp4,second,first" and res["index"] == "1,2,3"
    assert res["f32"] == "false,true"
    for k in ("unknownSet", "unknownGet", "displayName", "readOnly", "enum", "wrongEnum", "addCompInt",
              "audioOnSolid", "setValueKeyed", "tangentDims", "remapOff"):
        assert kinds[k] == "!", (k, res[k])
    assert "unknown member" in res["unknownSet"] and "matchName" in res["displayName"]
    assert "read-only" in res["readOnly"] and "BlendingMode" in res["enum"] and "integer" in res["addCompInt"]
    assert res["noIndexOf"] == "3,undefined,function"            # context-realm array, ES5 API deleted
    assert float(res["clamp"]) == pytest.approx(5400 * 1001 / 30000)
    assert rec["clamps"] and rec["clamps"][0]["member"] == "outPoint"
    before, after = (float(x) for x in res["keysMove"].split(","))
    assert (before, after) == (2.0, 3.0)                          # keys live in layer time
    assert float(res["defaultLayerStart"]) == pytest.approx(7 * 1001 / 30000)   # CTI, not 0
    assert len(rec["mock_errors"]) >= 11


def test_run_jsx_in_mock_not_available(monkeypatch, tmp_path):
    monkeypatch.setattr(ea, "_find_node", lambda: None)
    p = tmp_path / "x.jsx"
    p.write_text("#target aftereffects\n")
    rec = ea.run_jsx_in_mock(p, {})
    assert rec["status"] == "not_available"
    cl, cfg, plan, jsx = build(tmp_path)
    assert ea.mock_verify(jsx, plan, meta_for(cl))["status"] == "not_available"
    with pytest.raises(ValueError):
        ea.run_jsx_in_mock(p, {}, scenario="nope")


def test_plan_is_deterministic_and_json_clean():
    cl = make_cutlist()
    a = json.dumps(ea.ae_plan(cl, Config(), meta_for(cl)), sort_keys=True, allow_nan=False)
    b = json.dumps(ea.ae_plan(make_cutlist(), Config(), meta_for(cl)), sort_keys=True, allow_nan=False)
    assert a == b and a.isascii()


def dip_cutlist() -> Cutlist:
    """A -> dip to black -> B (DESIGN §3 dip convention: the dip overlaps its neighbours like a crossfade),
    then a 2-frame white flash and C; A carries an L-cut (audio runs 5 frames past its picture)."""
    dipA = {"type": "dip_black", "duration_frames": 4, "alpha": [0.0, 0.25, 0.5, 0.75]}
    dipB = {"type": "dip_black", "duration_frames": 4, "alpha": [0.0, 0.25, 0.5, 0.75]}
    segs = [Segment(id=1, type="raw", comp_in=0, comp_out=30, raw_in_seconds=raw_time(500, 0.5), speed=1.0,
                    transform=dict(SIM1), transition_out=dict(dipA),
                    audio={"in_offset_frames": 0, "out_offset_frames": 5, "pitch_preserved": None, "lag_ms": None,
                           "corr": None, "exception": None}),
            Segment(id=2, type="dip", comp_in=26, comp_out=40, color="#000000", transition_in=dict(dipA),
                    transition_out=dict(dipB)),
            Segment(id=3, type="raw", comp_in=36, comp_out=60, raw_in_seconds=raw_time(800, 0.5), speed=1.0,
                    transform=dict(SIM1), transition_in=dict(dipB)),
            Segment(id=4, type="flash", comp_in=60, comp_out=62, color="#ffffff"),
            Segment(id=5, type="raw", comp_in=62, comp_out=90, raw_in_seconds=raw_time(1500, 0.5), speed=1.0,
                    transform=dict(SIM1))]
    return make_cutlist(segs, comp_frames=90)


def test_plan_dip_flash_and_lcut():
    cl = dip_cutlist()
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    box_ids = [L["id"] for L in plan["layers"] if L["comp"] == "box"]
    assert box_ids[:2] == ["dip2", "flash4"]                         # solids above both neighbours
    dip = layer(plan, "dip2")
    assert dip["color"] == [0.0, 0.0, 0.0] and (dip["compIn"], dip["compOut"]) == (26, 40)
    want = ([(26 + i, 100 * a) for i, a in enumerate([0, 0.25, 0.5, 0.75])] + [(30, 100.0), (35, 100.0)]
            + [(36 + i, 100 * (1 - a)) for i, a in enumerate([0, 0.25, 0.5, 0.75])] + [(40, 0.0)])
    assert [(k["k"], k["v"]) for k in dip["opacity"]] == [(k, pytest.approx(v)) for k, v in want]
    assert layer(plan, "seg1")["opacity"] == [] and layer(plan, "seg3")["opacity"] == []
    assert layer(plan, "seg1")["audioKeys"] == []                     # no audio ramps for dips
    assert layer(plan, "flash4")["color"] == [1.0, 1.0, 1.0]
    sim = ea.simulate_ae(plan)
    for i, a in enumerate([0, 0.25, 0.5, 0.75]):
        assert sim[26 + i][0]["layer"] == "seg1" and sim[26 + i][0]["weight"] == pytest.approx(1 - a)
        assert sim[36 + i][0]["layer"] == "seg3" and sim[36 + i][0]["weight"] == pytest.approx(a)
    assert all(sim[K] == [] for K in range(30, 36)) and sim[60] == [] and sim[61] == []
    lc = layer(plan, "seg1_audio")
    assert (lc["compIn"], lc["compOut"], lc["enabled"], lc["audio"]) == (0, 35, False, True)
    assert layer(plan, "seg1")["audio"] is False
    assert ea.raw_frames_by_layer(sim) == expected_frames(cl)


@needs_node
def test_mock_dip_flash_lcut_and_blur_background(tmp_path):
    cl = dip_cutlist()
    cl.layout["background"] = "blur"
    cl.layout["background_detail"] = {"type": "blur", "color": "#000000", "blurriness": 80}
    _, _, plan, jsx = build(tmp_path, cl)
    res = ea.mock_verify(jsx, plan, meta_for(cl))
    assert res["status"] == "pass", res["failures"]
    rec = res["records"]["default"]
    by = {L["comment"]: L for c in rec["comps"] for L in c["layers"]}
    fx = by["mc:bg_blur"]["effects"]
    assert fx == [{"matchName": "ADBE Gaussian Blur 2", "enabled": True,
                   "params": {"ADBE Gaussian Blur 2-0001": 80.0, "ADBE Gaussian Blur 2-0003": 1}}]
    assert by["mc:bg_blur"]["sourceType"] == "comp" and not by["mc:bg_blur"]["audioEnabled"]
    assert len(by["mc:dip2"]["props"]["ADBE Opacity"]["keys"]) == len(layer(plan, "dip2")["opacity"]) == 11
    assert ea.raw_frames_by_layer(ea.simulate_ae(rec)) == expected_frames(cl)
