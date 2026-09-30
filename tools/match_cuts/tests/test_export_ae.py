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


def place_media(folder: Path, cl: Cutlist) -> None:
    """Placeholder media files where the JSX looks for them (<script dir>/<file_rel>): the mock checks
    File.exists on the real file system."""
    for block in (cl.raw, cl.competitor):
        rel = block.get("file_rel")
        if rel:
            p = folder / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")


def build(tmp_path: Path, cl: Cutlist | None = None, media: bool = True, **cfg_kw):
    cl = cl or make_cutlist()
    cfg = Config(**cfg_kw)
    plan = ea.ae_plan(cl, cfg, meta_for(cl))
    jsx = tmp_path / "build_ae_project.jsx"
    ea.write_jsx(cl, plan, jsx, cfg)
    if media:
        place_media(tmp_path, cl)
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
    nm_main = ea.record_main_comp(nm)
    assert nm_main["markers"] == []
    assert len(nm["warnings"]) == 1 and nm["warnings"][0].startswith("comp markers could not be added")
    # AE-4: the runtime warnings are saved with the project, below the 'mc:main' tag
    assert nm_main["comment"].split("\n") == ["mc:main", nm["warnings"][0]]
    assert rec["warnings"] == [] and main["comment"] == "mc:main"


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
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "raw.mp4").write_bytes(b"")
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


# ---------------------------------------------------------------------------------------------
# AE-1 / D1: per-period layout (fullscreen segments directly in MAIN, split / PiP flagged)
# ---------------------------------------------------------------------------------------------

FULL = {"x": 0.0, "y": 0.0, "w": 1080.0, "h": 1920.0, "corner_radius": 0.0}
PIP = {"x": 100.0, "y": 1500.0, "w": 400.0, "h": 300.0, "corner_radius": 20.0}
_CS = max(1080 / RAW_W, 1920 / RAW_H)
SIM_FULL = {"scale": _CS, "rotation_deg": 0.0, "tx": 540 - _CS * RAW_W / 2, "ty": 960 - _CS * RAW_H / 2}


def fullscreen_cutlist(s3_box=FULL) -> Cutlist:
    """boxed S1 -(6-frame crossfade)-> fullscreen S2, fullscreen S3, boxed S4, a PiP-region S5 with an
    animated framing, a fullscreen NOT-IN-RAW S6."""
    segs = [Segment(id=1, type="raw", comp_in=0, comp_out=45, raw_in_seconds=raw_time(900, 0.5), speed=1.0,
                    transform=dict(SIM1), transition_out=dict(XFADE)),
            Segment(id=2, type="raw", comp_in=39, comp_out=90, raw_in_seconds=raw_time(2000, 0.5), speed=1.0,
                    transform=dict(SIM_FULL), transition_in=dict(XFADE), box=dict(FULL), region=1),
            Segment(id=3, type="raw", comp_in=90, comp_out=120, raw_in_seconds=raw_time(2600, 0.5), speed=1.0,
                    flip_h=True, transform=dict(SIM_FULL), box=dict(s3_box) if s3_box else None,
                    region=1 if s3_box else 0),
            Segment(id=4, type="raw", comp_in=120, comp_out=150, raw_in_seconds=raw_time(3100, 0.5), speed=1.0,
                    transform=dict(SIM1)),
            Segment(id=5, type="raw", comp_in=150, comp_out=180, raw_in_seconds=raw_time(3500, 0.5), speed=1.0,
                    transform={"scale": 0.25, "rotation_deg": 0.0, "tx": 60.0, "ty": 1480.0},
                    transform_keys=[{"comp_frame": 150, "scale": 0.25, "rotation_deg": 0.0, "tx": 60.0, "ty": 1480.0},
                                    {"comp_frame": 179, "scale": 0.3, "rotation_deg": 2.0, "tx": 20.0, "ty": 1450.0}],
                    box=dict(PIP), region=2),
            Segment(id=6, type="not_in_raw", comp_in=180, comp_out=200, label="insert", box=dict(FULL), region=1)]
    cl = make_cutlist(segs, comp_frames=200)
    cl.layout["periods"] = [{"comp_in": 0, "comp_out": 39, "mode": "boxed", "box": cl.layout["box"]},
                            {"comp_in": 39, "comp_out": 120, "mode": "fullscreen", "box": dict(FULL)},
                            {"comp_in": 120, "comp_out": 150, "mode": "boxed", "box": cl.layout["box"]},
                            {"comp_in": 150, "comp_out": 180, "mode": "pip", "box": dict(PIP)},
                            {"comp_in": 180, "comp_out": 200, "mode": "fullscreen", "box": dict(FULL)}]
    return cl


def test_plan_fullscreen_segments_placed_in_main():
    cl = fullscreen_cutlist()
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    L1, L2, L3, L4, L5 = (layer(plan, f"seg{i}") for i in (1, 2, 3, 4, 5))
    # boxed segments stay in the Video Box pre-comp; fullscreen (and the PiP region) go to MAIN
    assert (L1["comp"], L4["comp"]) == ("box", "box")
    assert (L2["comp"], L3["comp"], L5["comp"]) == ("main", "main", "main")
    # canonical Sim at origin (0, 0), r = 1: no Video Box offset, no mask for the whole canvas
    for L, seg in ((L2, cl.segments[1]), (L3, cl.segments[2])):
        ae = sim_to_ae(Sim.from_dict(seg.transform), seg.flip_h, RAW_W, RAW_H, r=1.0)
        assert L["xf"]["position"] == pytest.approx(list(ae.position), abs=1e-9)
        assert L["xf"]["scale"] == pytest.approx(list(ae.scale), abs=1e-9)
        assert L["maskPath"] is None and L["mask"] is None
    # MAIN stacking: reference > guides > MAIN-level segments (chronological) > Video Box > background
    main_ids = [L["id"] for L in plan["layers"] if L["comp"] == "main"]
    assert main_ids[0] == "ref" and main_ids[-1] == "bg_solid"
    assert max(main_ids.index(g) for g in main_ids if g.startswith("guide")) < main_ids.index("seg2") \
        < main_ids.index("seg3") < main_ids.index("seg5") < main_ids.index("nir6") < main_ids.index("box")
    # the fullscreen NOT-IN-RAW placeholder covers the whole canvas
    P6 = layer(plan, "nir6")
    assert (P6["comp"], P6["w"], P6["h"], P6["xf"]["position"], P6["maskPath"]) == ("main", 1080, 1920, [540.0, 960.0], None)
    # no "exported inside the dominant Video Box" warning for reproduced fullscreen segments; PiP flagged
    assert not any("different layout box" in w or "fullscreen" in w for w in plan["warnings"]), plan["warnings"]
    assert any(w.startswith("S05:") and "picture-in-picture" in w for w in plan["warnings"])
    assert any("picture-in-picture layout in the competitor" in w for w in plan["warnings"])
    assert [(p["mode"], p["reproduced"]) for p in plan["periods"]] == [
        ("boxed", True), ("fullscreen", True), ("boxed", True), ("pip", False), ("fullscreen", True)]
    assert plan["summary"]["mainLevelSegments"] == 4
    assert any(d["decision"] == "fullscreen_period" for d in plan["decisions"])


def test_plan_crossfade_into_a_main_level_layer_keys_the_incoming_layer():
    cl = fullscreen_cutlist()
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    L1, L2 = layer(plan, "seg1"), layer(plan, "seg2")
    # S2 (MAIN, above the whole Video Box) is the upper layer: it rises 0 -> 100 %; S1 stays at 100 %
    assert L1["opacity"] == []
    want = [(39 + i, 100.0 * i / 6) for i in range(6)] + [(45, 100.0)]
    assert [(k["k"], k["v"]) for k in L2["opacity"]] == [(k, pytest.approx(v)) for k, v in want]
    sim = ea.simulate_ae(plan)
    for i in range(6):
        w = {e["layer"]: e["weight"] for e in sim[39 + i]}
        assert w["seg1"] == pytest.approx(1 - i / 6) and w["seg2"] == pytest.approx(i / 6)
    assert [e["layer"] for e in sim[45]] == ["seg2"] and sim[45][0]["weight"] == 1.0
    assert all(sim[K] == [] for K in range(180, 200))                       # fullscreen NOT-IN-RAW
    exp = expected_frames(cl)
    assert ea.raw_frames_by_layer(sim) == exp


def test_plan_masked_main_level_segment_mask_path_is_the_box():
    """A segment with its own non-canvas box is clipped by a layer-space mask whose comp-space image is
    exactly the (rounded) box at every MAIN frame, also with an animated framing (one key per frame)."""
    from match_cuts.geometry import ae_to_matrix
    cl = fullscreen_cutlist()
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    L5 = layer(plan, "seg5")
    mp = L5["maskPath"]
    assert [k["k"] for k in mp["keys"]] == list(range(150, 180))
    want = ea.rounded_rect_shape(PIP["x"], PIP["y"], PIP["w"], PIP["h"], PIP["corner_radius"])
    xk = L5["xf"]["keys"]
    for key in mp["keys"]:
        K = key["k"]
        u = (K - xk[0]["k"]) / (xk[1]["k"] - xk[0]["k"])
        lerp = lambda a, b: a + u * (b - a)       # noqa: E731
        xf = {"anchor": L5["xf"]["anchor"], "scale": [lerp(xk[0]["scale"][i], xk[1]["scale"][i]) for i in range(2)],
              "rotation": lerp(xk[0]["rotation"], xk[1]["rotation"]),
              "position": [lerp(xk[0]["position"][i], xk[1]["position"][i]) for i in range(2)]}
        m = ae_to_matrix(xf)
        for vl, vc in zip(key["vertices"], want["vertices"]):
            assert (m[:2, :2] @ vl + m[:2, 2]).tolist() == pytest.approx(vc, abs=1e-6)
        for nm in ("inTangents", "outTangents"):
            for tl, tc in zip(key[nm], want[nm]):
                assert (m[:2, :2] @ tl).tolist() == pytest.approx(tc, abs=1e-6)
    # a static framing: one shape; comp-size scaling applies r to the box
    cl2 = fullscreen_cutlist()
    cl2.segments[4].transform_keys = []
    plan2 = ea.ae_plan(cl2, Config(comp_size="720x1280"), meta_for(cl2))
    r = 2 / 3
    L5b = layer(plan2, "seg5")
    assert len(L5b["maskPath"]["keys"]) == 1
    m = ae_to_matrix(L5b["xf"])
    want2 = ea.rounded_rect_shape(PIP["x"] * r, PIP["y"] * r, PIP["w"] * r, PIP["h"] * r, PIP["corner_radius"] * r)
    for vl, vc in zip(L5b["maskPath"]["keys"][0]["vertices"], want2["vertices"]):
        assert (m[:2, :2] @ vl + m[:2, 2]).tolist() == pytest.approx(vc, abs=1e-6)
    L2b = layer(plan2, "seg2")
    ae = sim_to_ae(Sim.from_dict(SIM_FULL), False, RAW_W, RAW_H, r=r)
    assert L2b["xf"]["position"] == pytest.approx(list(ae.position)) and L2b["comp"] == "main"
    assert (layer(plan2, "nir6")["w"], layer(plan2, "nir6")["h"]) == (720, 1280)


def test_plan_fullscreen_period_without_segment_box_is_warned():
    cl = fullscreen_cutlist(s3_box=None)
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    assert layer(plan, "seg3")["comp"] == "box"
    assert any("fullscreen in the competitor" in w and "S03" in w for w in plan["warnings"])
    assert plan["periods"][1] == {"comp_in": 39, "comp_out": 120, "mode": "fullscreen", "reproduced": False}
    # a segment box equal to the layout box is the dominant layout (inside the Video Box)
    cl2 = make_cutlist()
    cl2.segments[0].box = dict(cl2.layout["box"])
    assert layer(ea.ae_plan(cl2, Config(), meta_for(cl2)), "seg1")["comp"] == "box"


def test_plan_fill_mode_uses_the_segment_box():
    cl = fullscreen_cutlist()
    plan = ea.ae_plan(cl, Config(layout_mode="fill"), meta_for(cl))
    L2 = layer(plan, "seg2")
    want = ea.fill_transform(Sim.from_dict(SIM_FULL), False, Box.from_dict(FULL), (RAW_W, RAW_H), (1080, 1920))
    ae = sim_to_ae(want, False, RAW_W, RAW_H)
    assert L2["xf"]["position"] == pytest.approx(list(ae.position)) and L2["xf"]["scale"] == pytest.approx(list(ae.scale))
    assert all(L["comp"] == "main" and L["maskPath"] is None for L in plan["layers"])


@needs_node
def test_mock_fullscreen_segments_run_and_match_plan(tmp_path):
    cl, cfg, plan, jsx = build(tmp_path, fullscreen_cutlist())
    res = ea.mock_verify(jsx, plan, meta_for(cl), scenarios=("default",))
    assert res["status"] == "pass", res["failures"]
    rec = res["records"]["default"]
    main = ea.record_main_comp(rec)
    tags = [L["comment"] for L in main["layers"]]
    assert tags.index("mc:seg2") < tags.index("mc:seg3") < tags.index("mc:box")      # above the Video Box
    boxc = next(c for c in rec["comps"] if c["comment"] == "mc:box")
    assert {L["comment"] for L in boxc["layers"]} == {"mc:seg1", "mc:seg4"}
    by = {L["comment"]: L for c in rec["comps"] for L in c["layers"]}
    assert by["mc:seg2"]["masks"] == [] and len(by["mc:seg5"]["masks"][0]["shapeKeys"]) == 30
    assert ea.raw_frames_by_layer(ea.simulate_ae(rec)) == expected_frames(cl)
    sp, sr = ea.simulate_ae(plan), ea.simulate_ae(rec)
    for K in range(39, 46):
        assert [(e["layer"], e["weight"]) for e in sp[K]] == [(e["layer"], pytest.approx(e["weight"])) for e in sr[K]]


# ---------------------------------------------------------------------------------------------
# AE-2: frame-rate conform on any real difference, exact frame count
# ---------------------------------------------------------------------------------------------

@needs_node
@pytest.mark.parametrize("scenario", ["fps_misread_down", "fps_misread_up"])
def test_mock_fps_misread_is_conformed(tmp_path, scenario):
    """AE reading every clip at rate * 1000/1001 (or 1001/1000) -- the classic NTSC misread, which the old
    1e-3 threshold let through for 30/1 and 24000/1001 -- must be conformed and logged, frame exact."""
    cl, cfg, plan, jsx = build(tmp_path)
    rec = ea.run_jsx_in_mock(jsx, meta_for(cl), scenario)
    assert rec["status"] == "ok" and rec["mock_errors"] == [] and rec["saved"]
    for tag, fps in (("mc:raw", 30000 / 1001), ("mc:ref", 30.0)):
        f = next(x for x in rec["footage"] if x["comment"] == tag)
        assert f["fps_num"] / f["fps_den"] != pytest.approx(fps, rel=1e-6)        # AE misread it ...
        assert f["conformFrameRate"] == pytest.approx(fps, rel=1e-12)             # ... and the JSX conformed
    assert sum("conformed to" in w for w in rec["warnings"]) == 2
    assert any("frame(s) of drift" in w for w in rec["warnings"])
    assert ea.raw_frames_by_layer(ea.simulate_ae(rec)) == expected_frames(cl)
    res = ea.mock_verify(jsx, plan, meta_for(cl), scenarios=(scenario,))
    assert res["status"] == "pass", res["failures"]


@needs_node
def test_mock_one_frame_count_difference_is_warned(tmp_path):
    cl, cfg, plan, jsx = build(tmp_path)
    rec = ea.run_jsx_in_mock(jsx, meta_for(cl), "frame_count_off")
    assert rec["status"] == "ok" and rec["mock_errors"] == []
    offs = [w for w in rec["warnings"] if "may be offset by 1 frame" in w]
    assert len(offs) == 2 and any("5401 frames in AE (expected 5400)" in w for w in offs)
    assert ea.mock_verify(jsx, plan, meta_for(cl), scenarios=("frame_count_off",))["status"] == "pass"


# ---------------------------------------------------------------------------------------------
# AE-3: a failed save over an existing recreated_edit.aep is detected
# ---------------------------------------------------------------------------------------------

@needs_node
@pytest.mark.parametrize("scenario", ["save_fails_existing", "save_silent_fail"])
def test_mock_failed_save_over_existing_aep_is_reported(tmp_path, scenario):
    cl, cfg, plan, jsx = build(tmp_path)
    rec = ea.run_jsx_in_mock(jsx, meta_for(cl), scenario)
    assert rec["status"] == "ok" and rec["saved"] == [] and rec["calls"]["save"] == 1
    assert any("Allow Scripts to Write Files" in a for a in rec["alerts"])
    assert "(NOT saved)" in rec["alerts"][-1] and "and saved" not in rec["alerts"][-1]
    assert ea.mock_verify(jsx, plan, meta_for(cl), scenarios=(scenario,))["status"] == "pass"


@needs_node
def test_mock_save_with_a_real_old_aep_on_disk(tmp_path):
    import os
    cl, cfg, plan, jsx = build(tmp_path)
    old = tmp_path / "recreated_edit.aep"
    old.write_bytes(b"old project")
    os.utime(old, (1_600_000_000, 1_600_000_000))
    ok = ea.run_jsx_in_mock(jsx, meta_for(cl), "default")                  # overwritten: new mtime -> saved
    assert ok["saved"] == [str(old)] and "and saved recreated_edit.aep" in ok["alerts"][-1]
    assert not any("Allow Scripts" in a for a in ok["alerts"])
    bad = ea.run_jsx_in_mock(jsx, meta_for(cl), "save_silent_fail")        # nothing written: old file stays
    assert bad["saved"] == [] and "(NOT saved)" in bad["alerts"][-1]
    assert old.read_bytes() == b"old project"


# ---------------------------------------------------------------------------------------------
# AE-4: warning overflow line, runtime warnings persisted, runtime [frames] tag
# ---------------------------------------------------------------------------------------------

@needs_node
def test_mock_summary_counts_hidden_warnings(tmp_path):
    cl = make_cutlist()
    cfg = Config()
    plan = ea.ae_plan(cl, cfg, meta_for(cl))
    plan["warnings"] = [f"plan warning {i}" for i in range(20)]
    jsx = tmp_path / "build_ae_project.jsx"
    ea.write_jsx(cl, plan, jsx, cfg)
    place_media(tmp_path, cl)
    rec = ea.run_jsx_in_mock(jsx, meta_for(cl))
    lines = rec["alerts"][-1].split("\n")
    assert "Warnings (20):" in lines
    assert sum(1 for x in lines if x.startswith("- plan warning")) == 12
    # only plan warnings overflowed: the pointer names report.md alone (ae-new-jsx:AE2-3)
    assert lines[-1] == "- ... and 8 more (plan warnings: report.md)"


@needs_node
def test_mock_runtime_frames_switch_is_tagged_and_persisted(tmp_path):
    cl, cfg, plan, jsx = build(tmp_path)
    rec = ea.run_jsx_in_mock(jsx, meta_for(cl), "quantize_time")
    assert rec["status"] == "ok" and rec["mock_errors"] == []
    by = {L["comment"]: L for c in rec["comps"] for L in c["layers"]}
    switched = [PL for PL in plan["layers"] if PL["kind"] == "raw" and by["mc:" + PL["id"]]["timeRemapEnabled"]
                and PL["timeMode"] == "stretch"]
    assert switched
    for PL in switched:
        RL = by["mc:" + PL["id"]]
        assert RL["name"] == PL["name"] + "  [frames]" and ea.record_name_matches(PL, RL)
    main = ea.record_main_comp(rec)
    assert main["comment"].split("\n")[0] == "mc:main" and main["comment"].split("\n")[1:] == rec["warnings"]
    assert ea.raw_frames_by_layer(ea.simulate_ae(rec)) == expected_frames(cl)


# ---------------------------------------------------------------------------------------------
# ae-new-jsx:AE2-3: the runtime-warning store in the MAIN comment respects AE's Item.comment limit
# (15,999 bytes); the alert points to the comment only for warnings actually stored there
# ---------------------------------------------------------------------------------------------

_STORE_ANCHOR = "    if (WARN.length > 0) {"          # where the JSX writes the MAIN comment


def _with_runtime_warnings(jsx: Path, n: int, dst: Path, extra: str = "", replace: tuple[str, str] | None = None) -> Path:
    """The generated JSX with ``n`` runtime self-check warnings (the real ~145-byte wording) raised right
    before the MAIN comment is written -- what a long recap under AE time quantisation produces."""
    text = jsx.read_text()
    assert text.count(_STORE_ANCHOR) == 1
    inj = ("    for (var qq = 0; qq < " + str(n) + "; qq++) { warn(\"S\" + (qq < 10 ? \"00\" : (qq < 100 ? \"0\" : \"\")) + qq + "
           "\" RAW 12.345-67.890 s" + extra + ": AE stored stretch/startTime differently from the plan (1 frame(s) off); "
           "switched this layer to frame-exact time remapping\"); }\n")
    text = text.replace(_STORE_ANCHOR, inj + _STORE_ANCHOR)
    if replace:
        assert replace[0] in text
        text = text.replace(replace[0], replace[1])
    dst.write_text(text)
    return dst


@needs_node
def test_mock_item_comment_limit(tmp_path):
    """The strict mock enforces AE's documented Item.comment limit (15,999 bytes after encoding)."""
    snippet = """#target aftereffects
(function () {
    app.newProject();
    var comp = app.project.items.addComp("Recreated Edit", 1080, 1920, 1, 10, 30);
    var s = "", i;
    for (i = 0; i < 15999; i++) { s += "x"; }
    comp.comment = s;
    try { comp.comment = s + "x"; } catch (e) { $.writeln("rejected: " + e.message); }
    var t = "";
    for (i = 0; i < 5334; i++) { t += "\\u4e2d"; }
    try { comp.comment = t; } catch (e2) { $.writeln("rejected utf8: " + e2.message); }
})();
"""
    p = tmp_path / "limit.jsx"
    p.write_text(snippet)
    rec = ea.run_jsx_in_mock(p, {})
    assert rec["status"] == "ok", rec.get("error")
    assert len(rec["comps"][0]["comment"]) == 15999                   # the last accepted value stays
    assert any(x.startswith("rejected: ") for x in rec["logs"])
    assert any(x.startswith("rejected utf8: ") and "16002 bytes" in x for x in rec["logs"])  # 3 bytes per char
    assert len(rec["mock_errors"]) == 2 and all("Item.comment" in e or "CompItem.comment" in e for e in rec["mock_errors"])


@needs_node
def test_mock_many_runtime_warnings_fit_the_main_comment(tmp_path):
    """> 110 runtime warnings of ~145 bytes used to make a ~30 KB comment (200-entry cap): AE rejects it,
    the catch swallowed that and the alert still pointed to the comment. Now the comment stays under the
    budget, ends with the count of the warnings not stored and the alert says so."""
    cl, cfg, plan, jsx = build(tmp_path)
    n = 160
    j2 = _with_runtime_warnings(jsx, n, tmp_path / "many_warnings.jsx")
    rec = ea.run_jsx_in_mock(j2, meta_for(cl))
    assert rec["status"] == "ok" and rec["mock_errors"] == [] and rec["saved"], rec.get("error")
    assert len(rec["warnings"]) == n and 140 <= len(rec["warnings"][0]) <= 160
    main = ea.record_main_comp(rec)
    body = main["comment"]
    assert len(body.encode("utf-8")) <= 15000
    lines = body.split("\n")
    stored = len(lines) - 2
    assert lines[0] == "mc:main" and 90 <= stored < n
    assert lines[1:1 + stored] == rec["warnings"][:stored]
    assert lines[-1] == f"... and {n - stored} more runtime warnings not stored"
    alert = rec["alerts"][-1].split("\n")
    assert f"Warnings ({n}):" in alert
    assert alert[-1] == (f"- ... and {n - 12} more (runtime warnings: the comment of the comp \"Recreated Edit\" "
                         f"({n - stored} of them not stored))")
    # multi-byte warnings (a localised AE error message) are budgeted in bytes, not characters
    j3 = _with_runtime_warnings(jsx, n, tmp_path / "utf8_warnings.jsx", extra=" \\u00e9\\u4e2d\\u6587\\u00fc")
    r3 = ea.run_jsx_in_mock(j3, meta_for(cl))
    assert r3["status"] == "ok" and r3["mock_errors"] == [], r3.get("mock_errors")
    c3 = ea.record_main_comp(r3)["comment"]
    assert len(c3.encode("utf-8")) <= 15000 and c3.split("\n")[-1].endswith("more runtime warnings not stored")
    # few runtime warnings: all stored, no count line, no 'not stored' in the alert
    j4 = _with_runtime_warnings(jsx, 20, tmp_path / "some_warnings.jsx")
    r4 = ea.run_jsx_in_mock(j4, meta_for(cl))
    c4 = ea.record_main_comp(r4)["comment"].split("\n")
    assert c4[1:] == r4["warnings"] and len(c4) == 21
    assert r4["alerts"][-1].split("\n")[-1] == "- ... and 8 more (runtime warnings: the comment of the comp \"Recreated Edit\")"


@needs_node
def test_mock_main_comment_fallback_when_ae_rejects_it(tmp_path):
    """An AE that rejects the comment (e.g. a lower limit than documented): the JSX retries with a small
    budget, then keeps the bare 'mc:main' tag; the alert never points to warnings that are not there."""
    cl, cfg, plan, jsx = build(tmp_path)
    n = 300
    # first budget too large for AE -> rejected, the small one is stored
    j1 = _with_runtime_warnings(jsx, n, tmp_path / "retry.jsx",
                                replace=("var COMMENT_BUDGETS = [15000, 2000];", "var COMMENT_BUDGETS = [40000, 2000];"))
    r1 = ea.run_jsx_in_mock(j1, meta_for(cl))
    assert r1["status"] == "ok" and r1["saved"] and len(r1["mock_errors"]) == 1
    c1 = ea.record_main_comp(r1)["comment"].split("\n")
    stored = len(c1) - 2
    assert c1[0] == "mc:main" and 1 <= stored <= 14 and c1[1:1 + stored] == r1["warnings"][:stored]
    a1 = r1["alerts"][-1].split("\n")[-1]
    if stored > 12:
        assert f"({n - stored} of them not stored)" in a1
    else:
        assert a1 == f"- ... and {n - 12} more (runtime warnings could not be stored)"
    # every attempt rejected -> the bare tag stays, the alert says the runtime warnings could not be stored
    j2 = _with_runtime_warnings(jsx, n, tmp_path / "rejected.jsx",
                                replace=("var COMMENT_BUDGETS = [15000, 2000];", "var COMMENT_BUDGETS = [40000, 30000];"))
    r2 = ea.run_jsx_in_mock(j2, meta_for(cl))
    assert r2["status"] == "ok" and r2["saved"] and len(r2["mock_errors"]) == 2
    main = ea.record_main_comp(r2)
    assert main is not None and main["comment"] == "mc:main"
    assert r2["alerts"][-1].split("\n")[-1] == f"- ... and {n - 12} more (runtime warnings could not be stored)"
    assert "comment of the comp" not in r2["alerts"][-1]
    # plan AND runtime warnings hidden: both pointers, each where the warnings really are
    plan2 = dict(plan)
    plan2["warnings"] = [f"plan warning {i}" for i in range(15)]
    jsx5 = tmp_path / "with_plan_warnings.jsx"
    ea.write_jsx(cl, plan2, jsx5, cfg)
    j5 = _with_runtime_warnings(jsx5, 30, tmp_path / "both.jsx")
    r5 = ea.run_jsx_in_mock(j5, meta_for(cl))
    assert r5["alerts"][-1].split("\n")[-1] == ("- ... and 21 more (plan warnings: report.md; runtime warnings: the "
                                                "comment of the comp \"Recreated Edit\")")


# ---------------------------------------------------------------------------------------------
# AE-5: the mock checks paths on the real file system
# ---------------------------------------------------------------------------------------------

@needs_node
def test_mock_media_must_exist_where_the_jsx_looks(tmp_path):
    cl, cfg, plan, jsx = build(tmp_path, media=False)                      # no media/ next to the script
    rec = ea.run_jsx_in_mock(jsx, meta_for(cl))
    assert rec["calls"]["openDialog"] == 1 and rec["saved"] == [] and rec["alerts"][0].startswith("Cancelled")
    # a wrong relative path in the plan is caught although media/raw.mp4 exists
    d2 = tmp_path / "wrong_rel"
    d2.mkdir()
    cl2 = make_cutlist()
    plan2 = ea.ae_plan(cl2, Config(), meta_for(cl2))
    plan2["footage"]["raw"]["rel"] = "no/such/dir/raw.mp4"
    jsx2 = d2 / "build_ae_project.jsx"
    ea.write_jsx(cl2, plan2, jsx2, Config())
    place_media(d2, cl2)
    res = ea.mock_verify(jsx2, plan2, meta_for(cl2), scenarios=("default",))
    assert res["status"] == "fail" and res["records"]["default"]["calls"]["openDialog"] == 1
    # the default run records where each clip was imported from: <script dir>/<rel>
    (tmp_path / "ok").mkdir()
    cl3, _, plan3, jsx3 = build(tmp_path / "ok", make_cutlist())
    rec3 = ea.run_jsx_in_mock(jsx3, meta_for(cl3))
    raw = next(f for f in rec3["footage"] if f["comment"] == "mc:raw")
    assert raw["fsName"] == str(tmp_path / "ok" / "media" / "raw.mp4")


@needs_node
def test_mock_rel_missing_abs_present(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "raw.mp4").write_bytes(b"")
    cl = make_cutlist()
    cl.raw["file_abs"] = str(elsewhere / "raw.mp4")
    out = tmp_path / "out"
    out.mkdir()
    _, _, plan, jsx = build(out, cl)
    res = ea.mock_verify(jsx, plan, meta_for(cl), scenarios=("rel_missing_abs_present",))
    assert res["status"] == "pass", res["failures"]
    assert res["details"]["rel_missing_abs_present"] == "abs"
    rec = res["records"]["rel_missing_abs_present"]
    raw = next(f for f in rec["footage"] if f["comment"] == "mc:raw")
    assert raw["fsName"] == str(elsewhere / "raw.mp4") and rec["calls"]["openDialog"] == 0 and rec["saved"]
    # the reference has no reachable absolute path: skipped with a warning, not fatal
    assert any("reference video" in w for w in rec["warnings"])


# ---------------------------------------------------------------------------------------------
# REQ-4: labelled placeholders for the competitor's music / SFX / VO and in-box overlays
# ---------------------------------------------------------------------------------------------

def placeholder_cutlist() -> Cutlist:
    cl = make_cutlist()
    cl.added_audio = [{"type": "voice_over", "comp_in": 50, "comp_out": 120, "level_db": None},
                      {"type": "music", "comp_in": 0, "comp_out": 300, "level_db": -6.5, "level_dbfs": -23.6}]
    cl.overlays_detected = [
        {"type": "logo", "comp_in": 0, "comp_out": 300, "x": 30, "y": 30, "w": 50, "h": 50, "static": True},
        {"type": "captions", "comp_in": 12, "comp_out": 40, "x": 200, "y": 900, "w": 600, "h": 90},
        {"type": "text", "comp_in": 50, "comp_out": 66, "x": 520.0, "y": 940.0, "w": 56.0, "h": 44.0},
        {"type": "text", "comp_in": 50, "comp_out": 66, "x": 520.0, "y": 940.0, "w": 56.0, "h": 44.0},
        {"type": "sticker", "comp_in": 100, "comp_out": 130, "x": 500.0, "y": 1000.0, "w": 100.0, "h": 100.0}]
    return cl


def test_plan_audio_placeholders_and_overlay_guides_match_mode():
    cl = placeholder_cutlist()
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    aph = [L for L in plan["layers"] if L["kind"] == "audio_placeholder"]
    assert [(L["compIn"], L["compOut"]) for L in aph] == [(0, 300), (50, 120)]
    assert all(L["comp"] == "main" and L["guide"] and not L["enabled"] and not L["audio"] for L in aph)
    assert aph[0]["name"].startswith("PLACEHOLDER - MUSIC 00:00:00:00-00:00:10:00") and "-6.5 dB" in aph[0]["name"]
    assert aph[1]["name"].startswith("PLACEHOLDER - VOICE OVER ") and aph[1]["name"].isascii()
    mk = {m["k"]: m["text"] for m in plan["markers"]}
    assert mk[0].startswith("Start | S01 RAW ") and "MUSIC placeholder 00:00:00:00-00:00:10:00" in mk[0]
    assert "VOICE OVER placeholder" in mk[50]
    ovl = [L for L in plan["layers"] if L["id"].startswith("ovl")]
    assert len(ovl) == 2                                                   # duplicate event merged
    t, st = ovl
    assert t["name"].startswith("GUIDE - text overlay (520,940 56x44) 00:00:01:20-00:00:02:06")
    assert (t["compIn"], t["compOut"], t["w"], t["h"], t["xf"]["position"]) == (50, 66, 56, 44, [548.0, 962.0])
    assert st["name"].startswith("GUIDE - sticker overlay") and (st["compIn"], st["compOut"]) == (100, 130)
    assert all(L["guide"] and L["enabled"] and L["comp"] == "main" for L in ovl)
    assert plan["summary"]["audioPlaceholders"] == 2 and plan["summary"]["overlayGuides"] == 2
    main_ids = [L["id"] for L in plan["layers"] if L["comp"] == "main"]
    assert main_ids.index("ref") < main_ids.index("ovl0") < main_ids.index("audio_ph0") < main_ids.index("box")
    # the added layers never change what AE shows
    assert ea.raw_frames_by_layer(ea.simulate_ae(plan)) == expected_frames(cl)


def test_plan_placeholders_fill_and_source_modes():
    cl = placeholder_cutlist()
    fill = ea.ae_plan(cl, Config(layout_mode="fill"), meta_for(cl))
    # fill: guides mapped through fill_transform (box centre -> frame centre, zoom cover_frame / cover_box)
    bx = cl.layout["box"]
    q = max(1080 / RAW_W, 1920 / RAW_H) / max(bx["w"] / RAW_W, bx["h"] / RAW_H)
    bcx, bcy = bx["x"] + bx["w"] / 2, bx["y"] + bx["h"] / 2
    t = next(L for L in fill["layers"] if L["id"] == "ovl0")
    cx, cy = 540 + q * (548 - bcx), 960 + q * (962 - bcy)
    assert t["xf"]["position"] == pytest.approx([cx, cy], abs=0.51) and t["w"] == round(56 * q)
    zones = {L["name"].split(" ")[2]: L for L in fill["layers"] if L["id"].startswith("guide")}
    assert set(zones) == {"title", "watermark", "captions"}
    for L in zones.values():                                               # clamped inside the frame
        x, y = L["xf"]["position"]
        assert L["w"] / 2 - 0.51 <= x <= 1080 - L["w"] / 2 + 0.51 and L["h"] / 2 - 0.51 <= y <= 1920 - L["h"] / 2 + 0.51
    assert len([L for L in fill["layers"] if L["kind"] == "audio_placeholder"]) == 2
    # source: no spatial guides, but the audio placeholders on the RAW grid
    src = ea.ae_plan(cl, Config(layout_mode="source"), meta_for(cl))
    assert not any(L["kind"] == "guide" for L in src["layers"])
    aph = [L for L in src["layers"] if L["kind"] == "audio_placeholder"]
    to_main = lambda k: math.floor(Fraction(k) * RF / CF + Fraction(1, 2))   # noqa: E731
    assert [(L["compIn"], L["compOut"]) for L in aph] == [(0, to_main(300)), (to_main(50), to_main(120))]


@needs_node
def test_mock_placeholders_build_cleanly(tmp_path):
    for mode in ("match", "fill", "source"):
        d = tmp_path / mode
        d.mkdir()
        cl, cfg, plan, jsx = build(d, placeholder_cutlist(), layout_mode=mode)
        res = ea.mock_verify(jsx, plan, meta_for(cl), scenarios=("default",))
        assert res["status"] == "pass", (mode, res["failures"])
        rec = res["records"]["default"]
        by = {L["comment"]: L for c in rec["comps"] for L in c["layers"]}
        assert by["mc:audio_ph0"]["guideLayer"] and not by["mc:audio_ph0"]["enabled"]
        main = ea.record_main_comp(rec)
        assert any("MUSIC placeholder" in m["comment"] for m in main["markers"])
        assert "2 audio placeholder(s)" in rec["alerts"][-1]


@needs_node
def test_mock_display_rounded_rate_is_conformed_quietly(tmp_path):
    """AE reporting 29.97 for a 30000/1001 clip (9.6e-7 relative) is conformed to the exact rate without a
    user warning (float32 noise alone, < 6e-8, never triggers a conform)."""
    cl, cfg, plan, jsx = build(tmp_path)
    rec = ea.run_jsx_in_mock(jsx, meta_for(cl), "fps_display_rounded")
    assert rec["status"] == "ok" and rec["warnings"] == [] and rec["saved"]
    raw = next(f for f in rec["footage"] if f["comment"] == "mc:raw")
    ref = next(f for f in rec["footage"] if f["comment"] == "mc:ref")
    assert (raw["fps_num"], raw["fps_den"]) == (2997, 100) and raw["conformFrameRate"] == 30000 / 1001
    assert ref["conformFrameRate"] == 0                                    # 30/1 is read exactly
    assert any(re.search(r"raw\.mp4: AE reported 29\.9699\d* fps; conformed to 30000/1001", x) for x in rec["logs"])
    assert ea.raw_frames_by_layer(ea.simulate_ae(rec)) == expected_frames(cl)


@needs_node
def test_write_jsx_accepts_a_plan_without_the_new_fields(tmp_path):
    cl = make_cutlist()
    plan = ea.ae_plan(cl, Config(), meta_for(cl))
    for L in plan["layers"]:
        del L["maskPath"]
    del plan["summary"]["audioPlaceholders"], plan["summary"]["overlayGuides"]
    jsx = tmp_path / "build_ae_project.jsx"
    ea.write_jsx(cl, plan, jsx, Config())
    place_media(tmp_path, cl)
    rec = ea.run_jsx_in_mock(jsx, meta_for(cl))
    assert rec["status"] == "ok" and rec["mock_errors"] == [] and rec["saved"], rec.get("error")
    assert "maskPath" not in plan["layers"][0]                            # the caller's plan is not mutated
