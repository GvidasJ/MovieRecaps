"""Stage 10 report (DESIGN.md §5 report.py): ``write_report(ctx, path)`` writes report.md.

Sections: result + acceptance criteria c1..c6 (+ determinism), inputs (codecs, fps, sizes, durations,
VFR/offset issues, conform step and why, MAIN fps and max cut error), detected layout (+ layout.png),
segment table, edit-style breakdown, warnings, verification details, how to open in After Effects,
outputs, environment and timings.

Every section is rendered defensively from whatever the context holds; a section that cannot be
rendered says so in the report (and in the log) instead of aborting the run.
"""
from __future__ import annotations

import os
import statistics
import traceback
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from .common import atomic_write_text, fps_str, hms, log, parse_fps, timecode
from .model import Segment, Status

STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "pass_with_exceptions": "PASS (with exceptions)",
                "not_available": "N/A"}
CRITERIA_TITLES = {
    "c1_coverage": "1. Full coverage",
    "c2_cuts": "2. Frame-exact cuts",
    "c3_source_frames": "3. Frame-exact source frames",
    "c4_speed_framing": "4. Speed / framing / flip / rotation",
    "c5_audio": "5. Audio",
    "c6_after_effects": "6. After Effects",
}


# ---------------------------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------------------------

def _esc(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ")


def md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    rows = list(rows)
    out = ["| " + " | ".join(_esc(h) for h in headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _status(s: str | None) -> str:
    return STATUS_LABEL.get(s or "", (s or "not run").upper())


def _fps_label(fps: Any) -> str:
    try:
        f = parse_fps(fps)
    except (TypeError, ValueError):
        return str(fps)
    return f"{fps_str(f)} ({float(f):.3f})"


def _ranges(frames: Iterable[int]) -> list[tuple[int, int]]:
    fr = sorted(set(int(f) for f in frames))
    out: list[tuple[int, int]] = []
    for f in fr:
        if out and f == out[-1][1] + 1:
            out[-1] = (out[-1][0], f)
        else:
            out.append((f, f))
    return out


def _ranges_str(frames: Iterable[int], fps: Fraction | None = None, limit: int = 20) -> str:
    rs = _ranges(frames)
    parts = []
    for a, b in rs[:limit]:
        p = f"{a}" if a == b else f"{a}-{b}"
        if fps:
            p += f" ({timecode(a, fps)})"
        parts.append(p)
    if len(rs) > limit:
        parts.append(f"... +{len(rs) - limit} more runs")
    return ", ".join(parts) if parts else "none"


def _rel(path: str | os.PathLike | None, base: Path) -> str:
    if not path:
        return ""
    try:
        return os.path.relpath(str(path), str(base))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------------------------
# Segment rows (shared with the CSV column order in export_xml_edl)
# ---------------------------------------------------------------------------------------------

SEGMENT_COLUMNS = ["#", "comp in–out (tc / frames)", "duration", "RAW in–out (tc)", "speed", "flip",
                   "scale / position", "transition", "confidence", "notes"]


def _transition_str(seg: Segment) -> str:
    parts = []
    for label, t in (("in", seg.transition_in), ("out", seg.transition_out)):
        if t:
            t = t if isinstance(t, dict) else vars(t)
            parts.append(f"{label}: {t.get('type')} {t.get('duration_frames')}f")
    return "; ".join(parts)


def _framing_str(seg: Segment) -> str:
    if seg.type != "raw":
        return ""
    if seg.transform_keys:
        return f"animated ({len(seg.transform_keys)} keys, {seg.easing})"
    t = seg.transform
    if not t:
        return "?"
    s = f"s {float(t['scale']):.4f} · ({float(t['tx']):.1f}, {float(t['ty']):.1f})"
    if abs(float(t.get("rotation_deg", 0.0))) > 1e-9:
        s += f" · rot {float(t['rotation_deg']):.2f}°"
    return s


def segment_row(seg: Segment, comp_fps: Fraction, raw_fps: Fraction) -> list[str]:
    n = seg.comp_out - seg.comp_in
    comp = (f"{timecode(seg.comp_in, comp_fps)}–{timecode(seg.comp_out, comp_fps)} "
            f"({seg.comp_in}–{seg.comp_out})")
    dur = f"{n}f / {n / float(comp_fps):.3f}s"
    if seg.type == "raw" and seg.raw_in_frame is not None:
        raw = f"{timecode(seg.raw_in_frame, raw_fps)}–{timecode(seg.raw_out_frame if seg.raw_out_frame is not None else seg.raw_in_frame, raw_fps)}"
        if seg.raw_in_seconds is not None:
            raw += f" (raw_in {seg.raw_in_seconds:.6f}s)"
    elif seg.type == "not_in_raw":
        raw = seg.label or "NOT-IN-RAW"
    else:
        raw = f"{seg.type}" + (f" {seg.color}" if seg.color else "")
    if seg.type != "raw":
        speed = ""
    elif seg.time_remap_keys:
        speed = "freeze" if seg.speed == 0 else ("reverse" if seg.speed < 0 else "ramp") + f" ({len(seg.time_remap_keys)} remap keys)"
    else:
        speed = f"{seg.speed:.4f}" + (" (unsnapped)" if seg.unsnapped else "")
    notes = []
    if seg.notes:
        notes.append(seg.notes)
    if seg.uncertain:
        notes.append("UNCERTAIN")
    if seg.ambiguous_frames:
        notes.append(f"{len(seg.ambiguous_frames)} ambiguous-identical")
    if seg.tie_frames:
        notes.append(f"{len(seg.tie_frames)} timing-tie")
    if seg.retime and seg.retime != "none":
        notes.append(f"retime {seg.retime}")
    if seg.cut_ambiguity:
        notes.append(f"cut ambiguous {list(seg.cut_ambiguity)}")
    au = seg.audio or {}
    if au.get("exception"):
        notes.append(f"audio: {au['exception']}")
    if au.get("in_offset_frames") or au.get("out_offset_frames"):
        notes.append(f"J/L audio {au.get('in_offset_frames')}/{au.get('out_offset_frames')}f")
    return [f"S{seg.id:02d}", comp, dur, raw, speed, "yes" if seg.flip_h else "", _framing_str(seg),
            _transition_str(seg), f"{seg.confidence:.2f}", "; ".join(notes)]


# ---------------------------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------------------------

def _criteria(ctx: Any) -> list[str]:
    ver = getattr(ctx, "verify", None) or {}
    crit = ver.get("criteria", {})
    checks = ver.get("checks", {})
    rows = []
    for key, title in CRITERIA_TITLES.items():
        c = crit.get(key, {})
        rows.append([title, _status(c.get("status")), c.get("summary", "not run")])
    det = checks.get("s9_7_determinism", {})
    rows.append(["9.7 Determinism", _status(det.get("status")), det.get("summary", "not run")])
    overall = "not verified"
    if crit:
        overall = "FAIL" if any(c.get("status") == "fail" for c in crit.values()) or det.get("status") == "fail" else "PASS"
    out = [f"**Overall: {overall}**", "", md_table(["Criterion", "Status", "Evidence"], rows)]
    settings = (ctx.cutlist.settings if getattr(ctx, "cutlist", None) else {}) or {}
    if settings and not settings.get("criteria_exact", True):
        out += ["", f"_MAIN runs at {settings.get('main_fps')} (fps mode `{settings.get('fps_mode')}` / layout "
                    f"`{settings.get('layout_mode')}`): criteria 2 and 6 are exact only with `--fps competitor`._"]
    mock_only = (crit.get("c6_after_effects", {}).get("details") or {}).get("mock_only")
    if mock_only:
        out += ["", "_Criterion 6 was verified with the strict ExtendScript/After Effects mock (After Effects is not "
                    "installed on this machine). Run `build_ae_project.jsx` in After Effects to create "
                    "`recreated_edit.aep`._"]
    return out


def _stream_rows(info: Any, src: Any, conf: Any) -> list[tuple[str, str]]:
    def g(o, a, d=""):
        return getattr(o, a, d) if o is not None else d
    o = src or info
    rows = [
        ("file", f"{g(o, 'path')}"),
        ("container", g(o, "container")),
        ("video codec / profile", f"{g(o, 'vcodec')} {g(o, 'vprofile')}".strip()),
        ("pixel format / range", f"{g(o, 'pix_fmt')} {g(o, 'color_range')}".strip()),
        ("coded size", f"{g(o, 'width')}×{g(o, 'height')}"),
        ("display size", f"{g(o, 'display_width')}×{g(o, 'display_height')}"),
        ("SAR / DAR", f"{fps_str(g(o, 'sar', Fraction(1)))} / {fps_str(g(o, 'dar', Fraction(0)))}"),
        ("rotation", f"{g(o, 'rotation', 0)}°"),
        ("r_frame_rate / avg_frame_rate", f"{fps_str(g(o, 'r_frame_rate', Fraction(0)))} / {fps_str(g(o, 'avg_frame_rate', Fraction(0)))}"),
        ("nominal fps", _fps_label(g(o, "fps", Fraction(0)))),
        ("decoded frames", f"{g(o, 'nb_frames', 0)}"),
        ("duration", f"{hms(float(g(o, 'duration', 0.0)))} ({float(g(o, 'duration', 0.0)):.3f}s)"),
        ("CFR / VFR", ("VFR" if g(o, "vfr", False) else "CFR") + f" (PTS jitter {float(g(o, 'pts_jitter', 0.0)):.3f} frames)"),
        ("start times (v / a) / A-V offset", f"{float(g(o, 'v_start_time', 0.0)):.6f}s / {float(g(o, 'a_start_time', 0.0)):.6f}s / "
                                             f"{float(g(o, 'av_offset', 0.0)) * 1000:.3f} ms"),
        ("edit list", "yes" if g(o, "edit_list", False) else "no"),
        ("audio", (f"{g(o, 'acodec')} {g(o, 'a_sample_rate', 0)} Hz × {g(o, 'a_channels', 0)} ch" if g(o, "has_audio", False)
                   else "none")),
        ("AE issues", ", ".join(g(o, "ae_issues", []) or []) or "none (AE-safe)"),
    ]
    if conf is not None:
        rows.append(("imported by AE", f"{g(conf, 'file_rel') or g(conf, 'file_abs') or g(conf, 'path')}"))
        rows.append(("conform", ("transcoded — " if g(conf, "conformed", False) else "not needed — ") + str(g(conf, "reason", ""))))
        ver = g(conf, "verification", None)
        if ver:
            rows.append(("conform verification", ", ".join(f"{k}={v}" for k, v in sorted(ver.items()) if not isinstance(v, (list, dict)))))
    return rows


def _inputs(ctx: Any) -> list[str]:
    out = []
    for title, info, src, conf in (("Competitor", ctx.comp_info, getattr(ctx, "comp_input", None), getattr(ctx, "comp_conform", None)),
                                   ("RAW", ctx.raw_info, getattr(ctx, "raw_input", None), getattr(ctx, "raw_conform", None))):
        if info is None and src is None:
            out += [f"### {title}", "", "not probed", ""]
            continue
        out += [f"### {title}", "", md_table(["property", "value"], _stream_rows(info, src, conf)), ""]
    st = (ctx.cutlist.settings if getattr(ctx, "cutlist", None) else {}) or {}
    if st:
        out += [f"Timeline: MAIN comp {st.get('main_size', ['?', '?'])[0]}×{st.get('main_size', ['?', '?'])[1]} at "
                f"{_fps_label(st.get('main_fps'))} (layout `{st.get('layout_mode')}`, comp size `{st.get('comp_size')}`, "
                f"fps mode `{st.get('fps_mode')}`, AE time mode `{st.get('ae_time_mode')}`). "
                f"Max cut error from the fps mode: {float(st.get('fps_source_max_error_s') or 0.0) * 1000:.3f} ms."]
    return out


def _layout(ctx: Any) -> list[str]:
    cl = getattr(ctx, "cutlist", None)
    lb = (cl.layout if cl else None) or {}
    lay = getattr(ctx, "layout", None)
    if not lb and lay is None:
        return ["not analysed"]
    box = lb.get("box") or (lay.box.to_dict() if lay is not None and lay.box is not None else None)
    out = [f"- Layout kind: **{lb.get('layout_kind', getattr(lay, 'mode', '?'))}** (recreated in `{lb.get('mode')}` mode)",
           f"- Canvas: {lb.get('canvas_bg', getattr(lay, 'canvas_bg', '?'))}"]
    if box:
        out.append(f"- Video box: x {float(box['x']):.2f}, y {float(box['y']):.2f}, w {float(box['w']):.2f}, "
                   f"h {float(box['h']):.2f} (competitor px, CORNER convention), corner radius "
                   f"{float(box.get('corner_radius', 0.0)):.2f} px")
    else:
        out.append("- Video box: none (full-frame video)")
    bg = lb.get("background_detail") or lb.get("background")
    if isinstance(bg, dict):
        extra = ", ".join(f"{k} {v}" for k, v in sorted(bg.items()) if k != "type")
        bg = f"{bg.get('type', '?')}" + (f" ({extra})" if extra else "")
    out.append(f"- Background: {bg}")
    zones = lb.get("zones") or []
    if zones:
        out += ["", md_table(["zone", "x", "y", "w", "h", "frames", "notes"],
                             [[z.get("type"), f"{float(z.get('x', 0)):.0f}", f"{float(z.get('y', 0)):.0f}",
                               f"{float(z.get('w', 0)):.0f}", f"{float(z.get('h', 0)):.0f}",
                               "all" if z.get("comp_in") is None else f"{z.get('comp_in')}–{z.get('comp_out')}",
                               (z.get("text") or "") + (" " + z.get("notes") if z.get("notes") else "")] for z in zones])]
    caps = lb.get("captions") or []
    if caps:
        out += ["", f"- Captions: {len(caps)} caption events, frames {caps[0].get('comp_in')}–{caps[-1].get('comp_out')}"
                    f" (masked out of matching; placeholder guides in AE)"]
    periods = lb.get("periods") or []
    if periods:
        out.append("- Layout periods: " + ", ".join(f"{p.get('comp_in')}–{p.get('comp_out')} {p.get('mode')}" for p in periods))
    if lb.get("regions"):
        out.append(f"- Extra video regions (not recreated in v1): {lb['regions']}")
    for n in lb.get("notes") or []:
        out.append(f"- Note: {n}")
    out += ["", "![layout](debug/layout.png)"]
    return out


def _segments(ctx: Any) -> list[str]:
    cl = ctx.cutlist
    rows = [segment_row(s, cl.comp_fps, cl.raw_fps) for s in sorted(cl.segments, key=lambda s: (s.comp_in, s.id))]
    return [md_table(SEGMENT_COLUMNS, rows), "", "Mapping plot (competitor time → RAW time): ![mapping](debug/mapping.png)",
            "", "Scores: ![scores](debug/scores.png)"]


def _union_len(ranges: list[tuple[int, int]]) -> tuple[int, list[tuple[int, int]]]:
    rs = sorted(ranges)
    merged: list[list[int]] = []
    for a, b in rs:
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return sum(b - a + 1 for a, b in merged), [(a, b) for a, b in merged]


def edit_breakdown(cutlist: Any, layout: Any = None, cfg: Any = None) -> dict:
    """Numbers for the edit-style breakdown (also usable by tests)."""
    comp_fps = cutlist.comp_fps
    segs = sorted(cutlist.segments, key=lambda s: (s.comp_in, s.id))
    raws = [s for s in segs if s.type == "raw"]
    lens = [(s.comp_out - s.comp_in) / float(comp_fps) for s in raws]
    used = [(int(s.raw_in_frame), int(s.raw_out_frame)) if s.raw_out_frame >= s.raw_in_frame
            else (int(s.raw_out_frame), int(s.raw_in_frame))
            for s in raws if s.raw_in_frame is not None and s.raw_out_frame is not None]
    n_raw = int(cutlist.raw.get("frames") or 0)
    used_len, merged = _union_len(used)
    cut_out = []
    prev = 0
    for a, b in merged:
        if a > prev:
            cut_out.append((prev, a - 1))
        prev = b + 1
    if n_raw and prev < n_raw:
        cut_out.append((prev, n_raw - 1))
    # re-use: frames covered by >= 2 segments
    reuse = []
    for i in range(len(used)):
        for j in range(i + 1, len(used)):
            a, b = max(used[i][0], used[j][0]), min(used[i][1], used[j][1])
            if a <= b:
                reuse.append((a, b, raws[i].id, raws[j].id))
    ordered = [s for s in raws if s.raw_in_frame is not None]
    hook = len(ordered) >= 2 and ordered[0].raw_in_frame > ordered[1].raw_in_frame
    non_chrono = []        # segments that start earlier in RAW than where the previous segment ended
    prev_out = None
    for s in ordered:
        if prev_out is not None and s.raw_in_frame < prev_out:
            non_chrono.append(s.id)
        prev_out = s.raw_out_frame if s.raw_out_frame is not None else s.raw_in_frame
    punch = []
    step = float(getattr(cfg, "punch_scale_step", 0.01)) if cfg is not None else 0.01
    for a, b in zip(raws[:-1], raws[1:]):
        if (a.comp_out == b.comp_in and a.raw_out_frame is not None and b.raw_in_frame is not None
                and abs(b.raw_in_frame - a.raw_out_frame - 1) <= 2 and a.transform and b.transform
                and a.flip_h == b.flip_h and abs(float(b.transform["scale"]) / float(a.transform["scale"]) - 1) > step):
            punch.append((a.id, b.id, float(b.transform["scale"]) / float(a.transform["scale"])))
    speeds = Counter(round(float(s.speed), 3) for s in raws if not s.time_remap_keys)
    trans = Counter()
    for s in segs:
        t = s.transition_in if isinstance(s.transition_in, dict) or s.transition_in is None else vars(s.transition_in)
        if t:
            trans[t.get("type")] += 1
        if s.type in ("dip", "flash"):
            trans[s.type] += 1
    rot = [s.id for s in raws if s.transform and abs(float(s.transform.get("rotation_deg", 0.0))) > 1e-9]
    return {
        "segments": len(segs), "raw_segments": len(raws), "cuts": max(0, len(segs) - 1),
        "shot_mean_s": statistics.mean(lens) if lens else 0.0, "shot_median_s": statistics.median(lens) if lens else 0.0,
        "shot_min_s": min(lens) if lens else 0.0, "shot_max_s": max(lens) if lens else 0.0,
        "raw_used_frames": used_len, "raw_frames": n_raw, "raw_used_pct": 100.0 * used_len / n_raw if n_raw else 0.0,
        "raw_cut_out": cut_out, "reuse": reuse, "non_chronological": non_chrono, "hook": bool(hook), "punch_ins": punch,
        "speeds": dict(sorted(speeds.items())), "flips": [s.id for s in raws if s.flip_h], "rotations": rot,
        "animated": [s.id for s in raws if s.transform_keys], "transitions": dict(trans),
        "not_in_raw": [(s.comp_in, s.comp_out) for s in segs if s.type == "not_in_raw"],
        "freeze_reverse_ramp": [s.id for s in raws if s.time_remap_keys],
    }


def _breakdown(ctx: Any) -> list[str]:
    cl = ctx.cutlist
    b = edit_breakdown(cl, getattr(ctx, "layout", None), getattr(ctx, "cfg", None))
    raw_fps = cl.raw_fps
    out = [f"- Segments: {b['segments']} ({b['raw_segments']} from RAW), cuts: {b['cuts']}",
           f"- Shot length: mean {b['shot_mean_s']:.2f}s, median {b['shot_median_s']:.2f}s "
           f"(min {b['shot_min_s']:.2f}s, max {b['shot_max_s']:.2f}s)",
           f"- RAW used: {b['raw_used_frames']} of {b['raw_frames']} frames ({b['raw_used_pct']:.1f} %)"]
    if b["raw_cut_out"]:
        big = sorted(b["raw_cut_out"], key=lambda r: r[1] - r[0], reverse=True)[:12]
        out.append("- RAW ranges cut out (largest first): " + ", ".join(
            f"{timecode(a, raw_fps)}–{timecode(e + 1, raw_fps)} ({e - a + 1}f, {(e - a + 1) / float(raw_fps):.2f}s)"
            for a, e in big))
    if not b["non_chronological"]:
        out.append("- Order: chronological")
    else:
        out.append("- Order: non-chronological — " + ", ".join(f"S{i:02d}" for i in b["non_chronological"])
                   + " jump back in RAW time" + (" (the opening is a hook taken from later in RAW)" if b["hook"] else ""))
    if b["reuse"]:
        out.append("- Re-used RAW moments: " + ", ".join(f"frames {a}–{e} in S{i:02d} and S{j:02d}" for a, e, i, j in b["reuse"]))
    out.append("- Speed factors: " + (", ".join(f"{v:.3f}× ({n} segment{'s' if n != 1 else ''})"
                                                for v, n in b["speeds"].items()) or "n/a"))
    if b["freeze_reverse_ramp"]:
        out.append("- Freeze / reverse / ramp (time-remapped): " + ", ".join(f"S{i:02d}" for i in b["freeze_reverse_ramp"]))
    out.append(f"- Zoom punch-ins: {len(b['punch_ins'])}" + (" (" + ", ".join(f"S{a:02d}→S{c:02d} ×{r:.3f}" for a, c, r in b["punch_ins"]) + ")"
                                                         if b["punch_ins"] else ""))
    out.append(f"- Animated zooms/pans: {len(b['animated'])}" + (" (" + ", ".join(f"S{i:02d}" for i in b["animated"]) + ")" if b["animated"] else ""))
    out.append(f"- Horizontal flips: {len(b['flips'])}" + (" (" + ", ".join(f"S{i:02d}" for i in b["flips"]) + ")" if b["flips"] else ""))
    out.append(f"- Rotation: {len(b['rotations'])} segments" + (" (" + ", ".join(f"S{i:02d}" for i in b["rotations"]) + ")" if b["rotations"] else ""))
    out.append("- Transitions: " + (", ".join(f"{k} ×{v}" for k, v in sorted(b["transitions"].items())) or "hard cuts only"))
    caps = [o for o in cl.overlays_detected if o.get("type") == "captions"]
    if caps:
        durs = [(int(c["comp_out"]) - int(c["comp_in"])) / float(cl.comp_fps) for c in caps if "comp_in" in c and "comp_out" in c]
        out.append(f"- Captions: {len(caps)} events" + (f", typical duration {statistics.median(durs):.2f}s" if durs else "")
                   + f" ({_caption_style(caps)})")
    statics = [o for o in cl.overlays_detected if o.get("type") != "captions"]
    if statics:
        out.append("- Static overlays: " + ", ".join(f"{o.get('type')}" for o in statics))
    if cl.added_audio:
        out.append("- Added audio (not recreated): " + ", ".join(
            f"{a.get('type')} {timecode(int(a.get('comp_in', 0)), cl.comp_fps)}–{timecode(int(a.get('comp_out', 0)), cl.comp_fps)}"
            + (f" ({a.get('level_db'):.1f} dB)" if isinstance(a.get("level_db"), (int, float)) else "") for a in cl.added_audio))
    au = cl.audio or {}
    out.append(f"- Audio: status {au.get('status', '?')}" + ("; " + "; ".join(map(str, au.get("notes", []))) if au.get("notes") else ""))
    pp = [s.id for s in cl.segments if (s.audio or {}).get("pitch_preserved")]
    if pp:
        out.append("- Pitch preserved on speed-changed segments (AE's stretch changes pitch): " + ", ".join(f"S{i:02d}" for i in pp))
    return out


def _caption_style(caps: list[dict]) -> str:
    ys = [float(c["y"]) for c in caps if "y" in c]
    hs = [float(c["h"]) for c in caps if "h" in c]
    if not ys:
        return "position unknown"
    return f"band y≈{statistics.median(ys):.0f}px, height≈{statistics.median(hs):.0f}px" if hs else f"y≈{statistics.median(ys):.0f}px"


def _warnings(ctx: Any) -> list[str]:
    cl = ctx.cutlist
    fm = getattr(ctx, "fm", None)
    cfg = getattr(ctx, "cfg", None)
    comp_fps = cl.comp_fps
    out: list[str] = []
    if fm is not None:
        thr = float(getattr(cfg, "low_conf_thresh", 0.5))
        matched = fm.status == Status.MATCH
        low = np.nonzero((fm.conf < thr) & (fm.status != Status.NONE))[0]
        out.append(f"- Low-confidence frames (conf < {thr}): {len(low)} — {_ranges_str(low, comp_fps)}"
                   + (" — see `debug/low_confidence/`" if len(low) else ""))
        amb = np.nonzero(matched & (fm.raw_hi > fm.raw_lo) & (fm.raw_lo >= 0))[0]
        out.append(f"- Ambiguous-identical frames (neighbouring RAW frames identical): {len(amb)} — {_ranges_str(amb, comp_fps)}")
        ties = sorted(set(np.nonzero(fm.tie)[0].tolist()) | {k for s in cl.segments for k in s.tie_frames})
        out.append(f"- Timing-tie frames (AE floor/round may differ by one frame): {len(ties)} — {_ranges_str(ties, comp_fps)}")
        lm = np.nonzero(fm.low_margin & matched)[0]
        if len(lm):
            out.append(f"- Low-margin frames (best RAW frame beats its neighbours by < {getattr(cfg, 'low_margin_eps', 0.001)}): "
                       f"{len(lm)} — {_ranges_str(lm, comp_fps)}")
    else:
        amb = [k for s in cl.segments for k in s.ambiguous_frames]
        out.append(f"- Ambiguous-identical frames: {len(amb)} — {_ranges_str(amb, comp_fps)}")
    nir = [s for s in cl.segments if s.type == "not_in_raw"]
    out.append("- NOT-IN-RAW ranges: " + (", ".join(f"{s.comp_in}–{s.comp_out - 1} ({timecode(s.comp_in, comp_fps)}–"
                                                     f"{timecode(s.comp_out, comp_fps)})" for s in nir) or "none"))
    sens = [s for s in cl.segments if s.type == "raw" and s.time_mode != "remap" and (
        (s.ae_margin_ms is not None and s.ae_margin_ms < float(getattr(cfg, "ae_min_margin_ms", 1.0)))
        or (s.raw_in_seconds is not None and s.raw_in_interval_both is None))]
    out.append("- AE-rule-sensitive segments (tiny phase margin; use `--ae-time-mode frames` if AE is off by a frame): "
               + (", ".join(f"S{s.id:02d} ({s.ae_margin_ms if s.ae_margin_ms is not None else '?'} ms)" for s in sens) or "none"))
    cant = []
    lb = cl.layout or {}
    if lb.get("regions"):
        cant.append(f"{len(lb['regions'])} extra video region(s) (split-screen / PiP) — only the dominant region is rebuilt")
    for s in cl.segments:
        if s.retime and s.retime != "none":
            cant.append(f"S{s.id:02d}: {s.retime} retiming (AE Frame Blending approximates it)")
        if (s.audio or {}).get("pitch_preserved") and s.speed not in (0, 1):
            cant.append(f"S{s.id:02d}: pitch-preserved speed change (AE stretch changes pitch; use Time-Stretch on the audio)")
        if s.uncertain:
            cant.append(f"S{s.id:02d}: uncertain — {s.notes or 'see decisions.jsonl'}")
    out.append("- Anything AE can't reproduce: " + ("; ".join(cant) if cant else "nothing detected"))
    seen = set()
    extra = []
    for w in list(cl.warnings) + list(getattr(ctx, "warnings", []) or []):
        if w not in seen:
            seen.add(w)
            extra.append(w)
    if extra:
        out += ["", "All warnings:", ""] + [f"- {w}" for w in extra]
    errs = getattr(ctx, "errors", None) or []
    if errs:
        out += ["", "Stage errors:", ""] + [f"- {e.get('stage')}: {e.get('error')}" for e in errs]
    return out


def _verification(ctx: Any) -> list[str]:
    ver = getattr(ctx, "verify", None) or {}
    checks = ver.get("checks", {})
    if not checks:
        return ["Verification did not run."]
    out = [md_table(["Check", "Status", "Summary"],
                    [[k, _status(v.get("status")), v.get("summary", "")] for k, v in checks.items()])]
    vis = checks.get("s9_3_visual", {})
    dist = vis.get("distribution") or {}
    if dist:
        out += ["", f"Visual ZNCC over matched frames ({vis.get('source', '')}): min {dist.get('min')}, p1 {dist.get('p1')}, "
                    f"p5 {dist.get('p5')}, median {dist.get('median')}, mean {dist.get('mean')}; threshold {vis.get('threshold')}.",
                "", md_table(["ZNCC bin", "frames"], [[k, v] for k, v in (dist.get("hist") or {}).items()])]
        if vis.get("failed_frames"):
            out.append(f"Failure thumbnails: `debug/verify_failures/` ({len(vis['failed_frames'])} frames).")
    au = checks.get("s9_5_audio", {})
    if au.get("segments"):
        out += ["", f"Audio per segment ({au.get('audio_source', '')}; tolerance ±{au.get('tolerance_ms')} ms):", "",
                md_table(["segment", "result", "lag ms", "corr", "code"],
                         [[f"S{int(r['id']):02d}", r.get("result"), r.get("lag_ms", ""), r.get("corr", ""), r.get("code", "")]
                          for r in au["segments"]])]
    crit = ver.get("criteria", {})
    cuts = ((crit.get("c2_cuts") or {}).get("details") or {}).get("cuts") or []
    if cuts:
        out += ["", "Cuts (competitor vs recreation images in `debug/cuts/cut_XX.png`):", "",
                md_table(["cut", "frame", "kind", "status"],
                         [[f"{i:02d}: S{int(c['from']):02d}|S{int(c['to']):02d}", f"{c['frame']} ({c.get('tc', '')})",
                           c.get("kind"), c.get("status")] for i, c in enumerate(cuts, start=1)])]
    mock = ((crit.get("c6_after_effects") or {}).get("details") or {}).get("mock") or {}
    if mock.get("checks"):
        out += ["", "After Effects mock run:", ""] + [f"- [{'x' if c['ok'] else ' '}] {c['check']}" for c in mock["checks"]]
    if ver.get("failures"):
        out += ["", "Failures:", ""] + [f"- {f}" for f in ver["failures"]]
    return out


def _how_to_open(ctx: Any) -> list[str]:
    cl = getattr(ctx, "cutlist", None)
    raw_file = (cl.raw.get("file") if cl else "") or "media/…"
    return [
        "1. Copy the whole output folder (the `.jsx` finds `media/` next to itself; keep them together).",
        "2. After Effects → **File → Scripts → Run Script File…** → `build_ae_project.jsx`.",
        "3. The script creates a new project with the `Recreated Edit` comp and saves `recreated_edit.aep` next to "
        "the script. If saving fails, enable **Preferences → Scripting & Expressions → Allow Scripts to Write Files "
        "and Access Network** (in versions before 16.1: Preferences → General) and run it again.",
        f"4. If the RAW media is not found next to the script (`{raw_file}`), the script tries the absolute path and "
        "then opens a *Locate the RAW video* dialog (relink).",
        "5. Reference layer: `REFERENCE – competitor` sits on top as a guide layer in *Difference* mode, switched off. "
        "Turn its video switch on: black means the recreation matches the competitor exactly. Guide layers never render.",
        "6. Guide layers outline the header / title / caption / watermark zones — drop your own assets there. "
        "`MISSING – not in RAW` solids mark the ranges that have to be filled with your own footage.",
        "7. Alternative route: import `recreated_edit.xml` (FCP7 XML) in Premiere Pro or DaVinci Resolve.",
    ]


def _outputs(ctx: Any) -> list[str]:
    out_dir = Path(getattr(ctx.cfg, "out_dir", "."))
    paths = getattr(ctx, "paths", {}) or {}
    rows = []
    desc = {"jsx": "After Effects build script", "aep": "After Effects project", "cutlist": "cut list (source of truth)",
            "csv": "cut list, one row per segment", "xml": "FCP7 XML (Premiere / Resolve)", "edl": "CMX3600 EDL",
            "preview": "frame-exact preview render", "compare": "competitor | recreation | difference",
            "report": "this report", "verify": "verification results", "media": "AE-imported media",
            "debug": "debug plots, cut images, failure thumbnails", "decisions": "decision log (evidence)",
            "log": "run log", "frame_map": "per-frame mapping m(k)"}
    for k, p in paths.items():
        rows.append([k, _rel(p, out_dir), desc.get(k, "")])
    return [md_table(["output", "path", "what"], rows)] if rows else ["no outputs recorded"]


def _environment(ctx: Any) -> list[str]:
    env = getattr(ctx, "env", {}) or {}
    t = getattr(ctx, "timings", {}) or {}
    out = [f"- OS: {env.get('platform', env.get('os', '?'))}, Python {env.get('python', '?')}, "
           f"ffmpeg {env.get('ffmpeg_version', '?')}, Node {env.get('node_version') or 'not found'}",
           f"- After Effects: {env.get('ae_app') or 'not installed'}; aerender: {env.get('aerender') or 'not installed'}",
           f"- match_cuts {env.get('tool_version', '')}"]
    if t:
        out += ["", md_table(["stage", "seconds"], [[k, f"{float(v):.2f}"] for k, v in t.items()])]
    return out


SECTIONS: list[tuple[str, Callable[[Any], list[str]]]] = [
    ("Acceptance criteria", _criteria),
    ("Inputs", _inputs),
    ("Detected layout", _layout),
    ("Segments", _segments),
    ("Edit-style breakdown", _breakdown),
    ("Warnings", _warnings),
    ("Verification details", _verification),
    ("How to open in After Effects", _how_to_open),
    ("Outputs", _outputs),
    ("Environment and timings", _environment),
]


def render_report(ctx: Any) -> str:
    cfg = ctx.cfg
    comp = Path(getattr(cfg, "competitor", "competitor")).name
    raw = Path(getattr(cfg, "raw", "raw")).name
    lines = [f"# Match cuts report: {comp} rebuilt from {raw}", ""]
    for i, (title, fn) in enumerate(SECTIONS, start=1):
        lines += [f"## {i}. {title}", ""]
        try:
            lines += fn(ctx)
        except Exception as e:  # noqa: BLE001 - one broken section must not lose the rest of the report
            log.error("report section %r failed: %s\n%s", title, e, traceback.format_exc())
            lines.append(f"_Section could not be rendered: {type(e).__name__}: {e}_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(ctx: Any, path: str | os.PathLike) -> None:
    """Write report.md (prompt Stage 10) for a pipeline.Context."""
    atomic_write_text(path, render_report(ctx))
