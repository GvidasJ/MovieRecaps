"""Stage 10 report (DESIGN.md §5 report.py): ``write_report(ctx, path)`` writes report.md.

Sections: result + acceptance criteria c1..c6 (+ determinism), inputs (codecs, fps, sizes, durations,
VFR/offset issues, conform step and why, MAIN fps and max cut error), detected layout (+ layout.png),
segment table, edit-style breakdown, warnings, verification details, how to open in After Effects,
outputs, environment and timings.

Every section is rendered defensively from whatever the context holds; a section that cannot be
rendered says so in the report (and in the log) instead of aborting the run.
"""
from __future__ import annotations

import math
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
CRITERIA_KEYS = ("c1_coverage", "c2_cuts", "c3_source_frames", "c4_speed_framing", "c5_audio", "c6_after_effects")
KNOWN_STATUSES = ("pass", "pass_with_exceptions", "fail", "not_available")
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
    elif seg.type in ("not_in_raw", "uncertain"):
        raw = seg.label or ("NOT-IN-RAW" if seg.type == "not_in_raw" else "UNCERTAIN")
    else:
        raw = f"{seg.type}" + (f" {seg.color}" if seg.color else "")
    if seg.type != "raw":
        speed = ""
    elif seg.frame_mix:
        speed = f"{seg.speed:.4f} frame blend (Frame Mix)"
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
        notes.append(f"{len(seg.ambiguous_frames)} frames with identical RAW neighbours")
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
    if au.get("line"):
        # FX-14: its audio is one continuous audio line (a video-only retime / placeholder over playing audio)
        notes.append(f"audio line ({au['line'].get('source')})")
    return [f"S{seg.id:02d}", comp, dur, raw, speed, "yes" if seg.flip_h else "", _framing_str(seg),
            _transition_str(seg), f"{seg.confidence:.2f}", "; ".join(notes)]


# ---------------------------------------------------------------------------------------------
# Headline (DESIGN §7 D5)
# ---------------------------------------------------------------------------------------------

def headline(ver: dict | None) -> str:
    """'PASS', 'PASS (criterion 6 not verified: <reason>)' or 'FAIL' ('not verified' without results).

    FAIL when a criterion c1..c6 is missing or failed, any Stage 9 check failed (s9_7 determinism,
    s9_8 deliverables, ...) or a status is unknown; PASS (... not verified ...) when nothing failed but a
    criterion is not_available (exit code 3); else PASS (pass_with_exceptions counts as a pass)."""
    ver = ver or {}
    crit = ver.get("criteria") or {}
    checks = ver.get("checks") or {}
    if not crit and not checks:
        return "not verified"
    statuses = [(crit.get(k) or {}).get("status") for k in CRITERIA_KEYS]
    statuses += [(v or {}).get("status") for v in checks.values()]
    if any(s not in KNOWN_STATUSES or s == "fail" for s in statuses):
        return "FAIL"
    na = [k for k in CRITERIA_KEYS if (crit.get(k) or {}).get("status") == "not_available"]
    if not na:
        return "PASS"
    nums = [k[1:].split("_", 1)[0] for k in na]
    reasons = "; ".join(str((crit.get(k) or {}).get("summary") or "not available") for k in na)
    label = f"criterion {nums[0]}" if len(nums) == 1 else "criteria " + ", ".join(nums)
    return f"PASS ({label} not verified: {reasons})"


# ---------------------------------------------------------------------------------------------
# Plain-language summary (FX-12): for a non-expert user whose English is a second language
# ---------------------------------------------------------------------------------------------

PLAIN_STATUS = {"pass": "OK", "pass_with_exceptions": "OK, with notes", "fail": "NOT OK", "not_available": "not checked"}
PLAIN_WHAT = {
    "c1_coverage": "Every frame of the competitor video is rebuilt or marked.",
    "c2_cuts": "Every cut is on the right frame.",
    "c3_source_frames": "Every frame shows the right frame of your RAW video.",
    "c4_speed_framing": "Speed, zoom, position, flip and rotation are right.",
    "c5_audio": "The sound lines up with the picture.",
    "c6_after_effects": "The After Effects script builds the project.",
}


def raw_only_overlays(ver: dict | None) -> list[dict]:
    """verify's measured RAW-only overlay regions ({segment, raw_rect [x, y, w, h] RAW px, frames [a, b], ...})."""
    return list((((ver or {}).get("raw_only_overlays") or {}).get("regions")) or [])


def raw_only_overlay_text(ver: dict | None) -> list[str]:
    """One line per RAW-only overlay (consecutive segments merged), as verify words it."""
    return list((((ver or {}).get("raw_only_overlays") or {}).get("lines")) or [])


def animated_text(ver: dict | None) -> list[dict]:
    t = (((ver or {}).get("checks") or {}).get("s9_2b_temporal") or {})
    return [z for z in (t.get("animated_text") or []) if z.get("kind", "overlay") == "overlay"]


def headlines(ctx: Any) -> list[str]:
    """One-line headlines (FX-12): the A/V offset and the audio switch baseline, uncertain segments, RAW-only overlays,
    phases pinned by the cadence, moving competitor text."""
    cl = getattr(ctx, "cutlist", None)
    ver = getattr(ctx, "verify", None)
    out = []
    if cl is not None:
        sync = av_offset_line(cl)
        if sync:
            out.append(f"Audio: {sync}.")
        unc = [s for s in cl.segments if s.type == "uncertain"]
        if unc:
            out.append(f"Uncertain: {sum(s.comp_out - s.comp_in for s in unc)} frames in {len(unc)} segment(s) — "
                       + ", ".join(f"{s.comp_in}–{s.comp_out - 1}" for s in unc) + ".")
    ov = raw_only_overlay_text(ver)
    if ov:
        out.append(f"RAW-only overlays: {len(ov)} — " + "; ".join(ov) + ".")
    if cl is not None:
        try:
            pin = [ln for ln in ae_phase_lines(cl, getattr(ctx, "cfg", None)) if "Phase pinned" in ln]
        except Exception:  # noqa: BLE001 - a headline must never break the report
            pin = []
        if pin:
            n = pin[0].split(":", 1)[1].split("segment(s)")[0].strip()
            out.append(f"Phases pinned by the cadence (information, not a risk): {n} segment(s).")
    at = animated_text(ver)
    if at:
        out.append("Moving competitor text (not rebuilt; ignored by the motion checks): "
                   + ", ".join(f"frames {z['comp_in']}–{z['comp_out'] - 1}" for z in at) + ".")
    return out


def _summary(ctx: Any) -> list[str]:
    """What passed, what failed and why, and what to check by hand in After Effects -- short sentences, simple words."""
    ver = getattr(ctx, "verify", None) or {}
    crit = ver.get("criteria") or {}
    cl = getattr(ctx, "cutlist", None)
    fps = cl.comp_fps if cl is not None else None
    tc = (lambda k: f" ({timecode(k, fps)})") if fps else (lambda k: "")  # noqa: E731
    out = [f"**Result: {headline(ver)}**", ""]
    if crit:
        out += [md_table(["What was checked", "Result"],
                         [[PLAIN_WHAT[k], PLAIN_STATUS.get((crit.get(k) or {}).get("status"), "not run")]
                          for k in CRITERIA_KEYS]), ""]
    why: list[str] = []
    hand: list[str] = []
    segs = list(cl.segments) if cl is not None else []
    for s in segs:
        if s.type == "uncertain":
            why.append(f"Frames {s.comp_in}–{s.comp_out - 1}{tc(s.comp_in)}: the tool is not sure which RAW frame this is "
                       f"({s.label or 'no clear match'}). It did not guess. These frames count as a failure of check 3.")
            hand.append(f"Frames {s.comp_in}–{s.comp_out - 1}: rebuild them by hand. The 'UNCERTAIN' guide layer in After "
                        "Effects shows the best RAW frames the tool found.")
        elif s.type == "not_in_raw":
            hand.append(f"Frames {s.comp_in}–{s.comp_out - 1}{tc(s.comp_in)}: this part is not in your RAW video. Put your "
                        "own footage on the 'MISSING' solid in After Effects.")
    for r in raw_only_overlays(ver):
        x, y, w, h = r["raw_rect"]
        hand.append(f"Frames {r['frames'][0]}–{r['frames'][1]}: your RAW video shows text or a graphic at x {x:.0f}, "
                    f"y {y:.0f} (size {w:.0f} × {h:.0f} RAW pixels) that the competitor does not show. The After Effects "
                    "project shows it too. If you do not want it, add a mask or a blur there in After Effects.")
    for s in segs:
        if s.frame_mix:
            hand.append(f"S{s.id:02d} (frames {s.comp_in}–{s.comp_out - 1}): slow motion made by mixing two frames. After "
                        "Effects uses Frame Mix here. Look at it once.")
    for z in animated_text(ver):
        hand.append(f"Frames {z['comp_in']}–{z['comp_out'] - 1}: the competitor has moving text on top of the video. The "
                    "tool does not rebuild text. Add your own text in After Effects if you want it.")
    other = [f for f in (ver.get("failures") or []) if "UNCERTAIN segment" not in f]
    for f in other[:8]:
        why.append(f"Other problem: {f}")
    if len(other) > 8:
        why.append(f"... and {len(other) - 8} more (see 'Verification details').")
    out += ["What failed and why:", ""] + ([f"- {w}" for w in why] if why else ["- Nothing failed."]) + [""]
    out += ["Check by hand in After Effects:", ""] + ([f"- {h}" for h in hand] if hand else
                                                    ["- Nothing special. Turn on the 'REFERENCE – competitor' layer to "
                                                     "compare (black = same picture)."]) + [""]
    hl = headlines(ctx)
    if hl:
        out += ["Headlines:", ""] + [f"- {h}" for h in hl]
    return out


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
    dlv = checks.get("s9_8_deliverables", {})
    if dlv or crit:
        rows.append(["9.8 Deliverables", _status(dlv.get("status")), dlv.get("summary", "not run")])
    out = [f"**Overall: {headline(ver)}**", "", md_table(["Criterion", "Status", "Evidence"], rows)]
    settings = (ctx.cutlist.settings if getattr(ctx, "cutlist", None) else {}) or {}
    if settings and not settings.get("criteria_exact", True):
        err_ms = float(settings.get("fps_source_max_error_s") or 0.0) * 1000.0
        out += ["", f"_MAIN runs at {settings.get('main_fps')} (fps mode `{settings.get('fps_mode')}` / layout "
                    f"`{settings.get('layout_mode')}`), not on the competitor's grid: cuts are rounded to the nearest "
                    f"MAIN frame (max error {err_ms:.3f} ms) and criterion 3 accepts, on each MAIN frame, any RAW frame "
                    "between the two competitor frames that bracket its time (listed as 'between competitor frames'). "
                    "Criteria 2, 3 and 6 are frame-exact only with `--fps competitor`._"]
    mock_only = (crit.get("c6_after_effects", {}).get("details") or {}).get("mock_only")
    if mock_only:
        out += ["", "_Criterion 6 was verified with the strict ExtendScript/After Effects mock (After Effects is not "
                    "installed on this machine). Run `build_ae_project.jsx` in After Effects to create "
                    "`recreated_edit.aep`._"]
    return out


_FACTS_MEMO: dict[str, dict] = {}


def container_facts(path: str | os.PathLike | None) -> dict:
    """Input FACTS instead of guesses (FX-12): the per-track MP4 / MOV edit lists (probe.read_edit_lists: every
    entry's media_time and duration), the iTunSMPB tag (encoder gapless info) when present, and the video / audio
    stream durations the container states. Nothing is inferred from them (no AAC-priming or A/V-offset claim): the
    A/V offset is measured from the content (§7 D9). {'elst', 'smpb', 'durations'}: strings, 'n/a' when unreadable."""
    p = str(path or "")
    if p in _FACTS_MEMO:
        return _FACTS_MEMO[p]
    out = {"elst": "n/a", "smpb": "n/a", "durations": "n/a"}
    if p and Path(p).is_file():
        try:
            from .probe import read_edit_lists
            tracks = read_edit_lists(p)
            parts = []
            for t in tracks:
                kind = {"vide": "video", "soun": "audio"}.get(t.handler, t.handler or "?")
                if not t.entries:
                    parts.append(f"{kind} track {t.track_id}: none")
                    continue
                ents = "; ".join(("empty edit" if mt < 0 else
                                  f"media_time {mt}/{t.media_timescale} = {mt / max(1, t.media_timescale):.6f} s")
                                 + f", duration {sd / max(1, t.movie_timescale):.3f} s" + (f", rate {rate:g}" if rate != 1 else "")
                                 for sd, mt, rate in t.entries)
                parts.append(f"{kind} track {t.track_id}: {len(t.entries)} entr{'y' if len(t.entries) == 1 else 'ies'} "
                             f"({ents})")
            out["elst"] = "; ".join(parts) if parts else "none (no MP4 / MOV track boxes)"
        except Exception as e:  # noqa: BLE001 - a fact we cannot read is reported as such
            out["elst"] = f"not read ({type(e).__name__})"
        try:
            from .probe import ffprobe_json
            js = ffprobe_json(p)
            tags = dict((js.get("format") or {}).get("tags") or {})
            for s in js.get("streams") or []:
                tags.update(s.get("tags") or {})
            smpb = next((v for k, v in tags.items() if k.lower() == "itunsmpb"), None)
            out["smpb"] = f"present ({' '.join(str(smpb).split()[:4])} ...)" if smpb else "absent"
            durs = []
            for kind in ("video", "audio"):
                st = next((s for s in js.get("streams") or [] if s.get("codec_type") == kind), None)
                if st is None:
                    durs.append(f"{kind} none")
                    continue
                d = st.get("duration")
                durs.append(f"{kind} {float(d):.3f} s" if d not in (None, "N/A") else f"{kind} unknown")
            out["durations"] = " / ".join(durs)
        except Exception as e:  # noqa: BLE001
            out["smpb"] = out["durations"] = f"not read ({type(e).__name__})"
    if len(_FACTS_MEMO) > 16:
        _FACTS_MEMO.clear()
    _FACTS_MEMO[p] = out
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
        ("audio", (f"{g(o, 'acodec')} {g(o, 'a_sample_rate', 0)} Hz × {g(o, 'a_channels', 0)} ch" if g(o, "has_audio", False)
                   else "none")),
        ("AE issues", ", ".join(g(o, "ae_issues", []) or []) or "none (AE-safe)"),
    ]
    facts = container_facts(g(o, "path", ""))
    i = next((n for n, r in enumerate(rows) if r[0] == "audio"), len(rows))
    rows[i:i] = [("edit lists (elst, per track)", facts["elst"]), ("iTunSMPB (encoder gapless info)", facts["smpb"]),
                 ("stream durations (video / audio)", facts["durations"])]
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
    caps = caption_events(cl) if cl is not None else [c for c in (lb.get("captions") or [])
                                                        if str(c.get("type", "captions")) == "captions"]
    if caps:
        last = max(int(c.get("comp_out", 0)) for c in caps)
        out += ["", f"- Captions: {len(caps)} caption events, frames {caps[0].get('comp_in')}–{last}"
                    f" (masked out of matching; placeholder guides in AE)"]
    others = [c for c in (lb.get("captions") or []) if str(c.get("type", "captions")) != "captions"]
    if others:
        kinds = Counter(str(c.get("type")) for c in others)
        out.append("- Other overlaid text / stickers: " + ", ".join(f"{v} {k} event{'s' if v != 1 else ''}"
                                                                   for k, v in sorted(kinds.items())))
    periods = lb.get("periods") or []
    if periods:
        def _pdesc(p: dict) -> str:
            mode = str(p.get("mode"))
            what = {"fullscreen": " (reproduced: full-canvas layers in MAIN)",
                    "split": " (NOT reproduced: only the dominant region is rebuilt)",
                    "pip": " (NOT reproduced: only the dominant region is rebuilt)"}.get(mode, "")
            return f"{p.get('comp_in')}–{p.get('comp_out')} {mode}{what}"
        out.append("- Layout periods: " + ", ".join(_pdesc(p) for p in periods))
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


def _continuous_cut(a: Any, b: Any, ratio: float) -> bool:
    """Adjacent stretch segments whose cut does not leave the RAW time line: B starts where A's line continues
    (RAW jump floor(u) .. ceil(u), u = RAW frames per comp frame at their common speed) -- a reframe / framing
    step or a phase-only cut: never a jump back, never a re-used RAW moment."""
    if a.comp_out != b.comp_in or a.time_remap_keys or b.time_remap_keys or a.raw_out_frame is None \
            or b.raw_in_frame is None or a.speed is None or b.speed is None \
            or abs(float(a.speed) - float(b.speed)) > 1e-9:
        return False
    u = float(a.speed) * ratio
    if u <= 0:
        return False
    return math.floor(u) <= int(b.raw_in_frame) - int(a.raw_out_frame) <= math.ceil(u)


def edit_breakdown(cutlist: Any, layout: Any = None, cfg: Any = None) -> dict:
    """Numbers for the edit-style breakdown (also usable by tests)."""
    comp_fps = cutlist.comp_fps
    ratio = float(Fraction(cutlist.raw_fps) / Fraction(comp_fps)) if cutlist.raw_fps and comp_fps else 1.0
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
    # re-use: frames covered by >= 2 segments -- except the one RAW frame two adjacent segments on ONE time line
    # both show at their cut (a cut inside the 23.976 -> 30 repeat cadence; FX-04: derived from the final segments)
    used_seg = [s for s in raws if s.raw_in_frame is not None and s.raw_out_frame is not None]
    reuse = []
    for i in range(len(used)):
        for j in range(i + 1, len(used)):
            a, b = max(used[i][0], used[j][0]), min(used[i][1], used[j][1])
            if a <= b and not (a == b and _continuous_cut(used_seg[i], used_seg[j], ratio)):
                reuse.append((a, b, used_seg[i].id, used_seg[j].id))
    # cuts that do not leave the time line (no RAW frame skipped or repeated back): reframes / framing steps
    reframes = [(a.id, b.id) for a, b in zip(raws[:-1], raws[1:]) if _continuous_cut(a, b, ratio)]
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
        "animated": [s.id for s in raws if s.transform_keys], "transitions": dict(trans), "reframes": reframes,
        "not_in_raw": [(s.comp_in, s.comp_out) for s in segs if s.type == "not_in_raw"],
        "uncertain": [(s.comp_in, s.comp_out) for s in segs if s.type == "uncertain"],
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
    if b["reframes"]:
        out.append(f"- Reframes on one time line (cuts without a RAW skip): {len(b['reframes'])} ("
                   + ", ".join(f"S{a:02d}→S{c:02d}" for a, c in b["reframes"]) + ")")
    out.append(f"- Animated zooms/pans: {len(b['animated'])}" + (" (" + ", ".join(f"S{i:02d}" for i in b["animated"]) + ")" if b["animated"] else ""))
    out.append(f"- Horizontal flips: {len(b['flips'])}" + (" (" + ", ".join(f"S{i:02d}" for i in b["flips"]) + ")" if b["flips"] else ""))
    out.append(f"- Rotation: {len(b['rotations'])} segments" + (" (" + ", ".join(f"S{i:02d}" for i in b["rotations"]) + ")" if b["rotations"] else ""))
    out.append("- Transitions: " + (", ".join(f"{k} ×{v}" for k, v in sorted(b["transitions"].items())) or "hard cuts only"))
    caps = caption_events(cl)
    if caps:
        durs = [(int(c["comp_out"]) - int(c["comp_in"])) / float(cl.comp_fps) for c in caps if "comp_in" in c and "comp_out" in c]
        out.append(f"- Captions: {len(caps)} events" + (f", typical duration {statistics.median(durs):.2f}s" if durs else "")
                   + f" ({_caption_style(caps)})")
    statics = [o for o in cl.overlays_detected if _is_zone_entry(o, cl) and o.get("type") != "captions"]
    if statics:
        out.append("- Static overlays: " + ", ".join(f"{o.get('type')}" for o in statics))
    texts = [o for o in cl.overlays_detected if not _is_zone_entry(o, cl) and o.get("type") != "captions"]
    if texts:
        kinds = Counter(str(o.get("type")) for o in texts)
        out.append("- Other overlaid text / stickers: " + ", ".join(f"{k} ×{v}" for k, v in sorted(kinds.items())))
    if cl.added_audio:
        out.append("- Added audio (not recreated): " + ", ".join(
            f"{a.get('type')} {timecode(int(a.get('comp_in', 0)), cl.comp_fps)}–{timecode(int(a.get('comp_out', 0)), cl.comp_fps)}"
            + (f" ({a.get('level_db'):.1f} dB)" if isinstance(a.get("level_db"), (int, float)) else "") for a in cl.added_audio))
    au = cl.audio or {}
    out.append(f"- Audio: status {au.get('status', '?')}" + ("; " + "; ".join(map(str, au.get("notes", []))) if au.get("notes") else ""))
    sync = av_offset_line(cl)
    if sync:
        out.append(f"- Audio sync: {sync}")
    pp = [s.id for s in cl.segments if (s.audio or {}).get("pitch_preserved")]
    if pp:
        out.append("- Pitch preserved on speed-changed segments (AE's stretch changes pitch): " + ", ".join(f"S{i:02d}" for i in pp))
    return out


def av_offset_line(cl: Any) -> str:
    """One line on the competitor's measured A/V offset (DESIGN §7 D9): the offset in words with its
    interval and support, the audio switch baseline and what the export does. '' when nothing was measured."""
    av = (getattr(cl, "audio", None) or {}).get("av_offset") or {}
    if not av:
        return ""
    st = av.get("status")
    mode = av.get("sync_mode") or (getattr(cl, "settings", None) or {}).get("audio_sync") or "raw"
    iv = av.get("lag_ms_interval")
    n = av.get("n_segments")
    cov = av.get("coverage")
    support = (f"{n} segment(s)" + (f", coverage {float(cov):.0%}" if cov is not None else "")) if n else "no segment"
    if st == "measured":
        line = (f"{av.get('text')} (lag {float(av['lag_ms']):+.1f} ms, interval {float(iv[0]):+.1f} … {float(iv[1]):+.1f} ms, "
                f"{support}; a property of the input files, measured)")
    elif st == "zero":
        line = f"{av.get('text')} (0 ms is consistent with {support})"
    else:
        line = f"A/V offset not measured ({av.get('reason') or 'no evidence'}); audio is judged against RAW's own sync"
    b = av.get("switch_baseline_ms")
    if b is not None and st == "measured":
        sw = av.get("switch_baseline") or {}
        over = f" over {sw['n']} {sw.get('tier') or 'strong'} cut(s)" if sw.get("n") else ""
        line += f"; the competitor's audio switches {float(b):+.1f} ms after each picture cut (switch baseline{over})"
    if st == "measured":
        line += ("; export keeps RAW lip-sync (--audio-sync raw)" if mode != "competitor"
                 else "; export reproduces the competitor's offset (--audio-sync competitor)")
    return line


def _is_zone_entry(o: dict, cl: Any) -> bool:
    """An overlays_detected entry derived from a layout ZONE (the static / aggregate regions: logo, title,
    the caption band spanning every caption), not a per-event detection."""
    if str(o.get("kind", "")) == "zone" or str(o.get("type", "")).endswith("_zone") or "static" in o:
        return True
    for z in (cl.layout or {}).get("zones") or []:
        if str(z.get("type")) != str(o.get("type")):
            continue
        if str(o.get("type")) != "captions":
            return True                   # logo / title / watermark ...: zone types, never per-event
        if all(abs(float(z.get(q, 0.0)) - float(o.get(q, -1e9))) < 1e-6 for q in ("x", "y", "w", "h")):
            return True                   # the aggregate caption band
    return False


def caption_events(cl: Any) -> list[dict]:
    """Per-event captions (requirements REQ-7): the layout's caption events of type 'captions' (other text
    and sticker events and the aggregate caption ZONE excluded), else the per-event 'captions' entries of
    overlays_detected. Sorted by comp_in."""
    lb = cl.layout or {}
    caps = [c for c in (lb.get("captions") or []) if str(c.get("type", "captions")) == "captions"]
    if not caps:
        caps = [o for o in (cl.overlays_detected or []) if o.get("type") == "captions" and not _is_zone_entry(o, cl)]
    return sorted(caps, key=lambda c: (int(c.get("comp_in", 0)), float(c.get("y", 0.0)), float(c.get("x", 0.0))))


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
        re_rows = reassigned_frames(fm)
        re_set = {r["k"] for r in re_rows}
        pre_low = np.asarray(fm.d["pre_segment_low_margin"], bool) if "pre_segment_low_margin" in fm.d \
            else np.asarray(fm.low_margin, bool)
        lm = [int(k) for k in np.nonzero(pre_low & matched)[0] if int(k) not in re_set]
        if len(lm):
            out.append(f"- Low-margin frames (best RAW frame beats its neighbours by < {getattr(cfg, 'low_margin_eps', 0.001)}): "
                       f"{len(lm)} — {_ranges_str(lm, comp_fps)}")
        vrows = verified_reassigned(getattr(ctx, "verify", None))
        if vrows:
            out += reassigned_lines(vrows, comp_fps)
        elif re_rows:
            ex = "; ".join(f"k {r['k']}: " + (f"measured {r['measured']} → model {r['model']}" if r["measured"] is not None
                                              else f"unmatched → model {r['model']}")
                           + (f" (score gap {r['gap']:.4f})" if r.get("gap") is not None else "") for r in re_rows)
            out.append(f"- Re-assigned by segmentation (the segment model's RAW frame replaced refine's measured best "
                       f"frame; counted against criterion 3): {len(re_rows)} — {_ranges_str(re_set, comp_fps)}"
                       + (f" — {ex}" if ex else ""))
    else:
        amb = [k for s in cl.segments for k in s.ambiguous_frames]
        out.append(f"- Ambiguous-identical frames: {len(amb)} — {_ranges_str(amb, comp_fps)}")
    nir = [s for s in cl.segments if s.type == "not_in_raw"]
    out.append("- NOT-IN-RAW ranges (every hypothesis below none_thresh): "
               + (", ".join(f"{s.comp_in}–{s.comp_out - 1} ({timecode(s.comp_in, comp_fps)}–"
                            f"{timecode(s.comp_out, comp_fps)})" for s in nir) or "none"))
    unc = [s for s in cl.segments if s.type == "uncertain"]
    out.append("- UNCERTAIN ranges (best hypothesis between none_thresh and match_thresh: neither matched nor "
               "NOT-IN-RAW; criterion-3 failures, a guide layer of the best evidence in AE): "
               + ("; ".join(f"{s.comp_in}–{s.comp_out - 1} ({timecode(s.comp_in, comp_fps)}–{timecode(s.comp_out, comp_fps)})"
                            f" {s.label}" for s in unc) or "none"))
    out += ae_phase_lines(cl, cfg)
    nre = not_reproduced(getattr(ctx, "verify", None))
    if nre:
        out.append("- Frames not reproduced exactly (s9_2): " + "; ".join(nre))
    out += [f"- {line}" for line in independent_findings(getattr(ctx, "verify", None))]
    lb = cl.layout or {}
    periods = [p for p in (lb.get("periods") or []) if isinstance(p, dict)]
    full = [p for p in periods if str(p.get("mode")) == "fullscreen"]
    if full:
        out.append("- Full-screen periods (reproduced: full-canvas layers directly in MAIN): " + ", ".join(
            f"{p.get('comp_in')}–{int(p.get('comp_out')) - 1} ({timecode(int(p.get('comp_in')), comp_fps)}–"
            f"{timecode(int(p.get('comp_out')), comp_fps)})" for p in full))
    cant = []
    split = [p for p in periods if str(p.get("mode")) in ("split", "pip")]
    for p in split:
        cant.append(f"frames {p.get('comp_in')}–{int(p.get('comp_out')) - 1}: {p.get('mode')} layout (multiple video regions) "
                    "— only the dominant region is rebuilt")
    if lb.get("regions") and not split:
        cant.append(f"{len(lb['regions'])} extra video region(s) (split-screen / PiP) — only the dominant region is rebuilt")
    # a boxless segment touching a fullscreen period is only a problem for frames that neither a declared
    # dissolve/dip with a fullscreen neighbour nor a 1-2 frame boundary sliver explains (verify's c1 rule)
    try:
        from .verify import boxless_fullscreen_frames
    except Exception:  # noqa: BLE001 - report must render even if verify is unavailable
        boxless_fullscreen_frames = None
    for s in cl.segments:
        if s.type != "raw" or s.box:
            continue
        bad: list[int] = []
        for p in full:
            a, b = int(p.get("comp_in")), int(p.get("comp_out"))
            if not (s.comp_in < b and s.comp_out > a):
                continue
            if boxless_fullscreen_frames is None:
                bad += list(range(max(a, s.comp_in), min(b, s.comp_out)))
            else:
                bad += list(boxless_fullscreen_frames(s, cl.segments, a, b).get("unexplained") or [])
        if bad:
            cant.append(f"S{s.id:02d}: frames {_ranges_str(sorted(set(bad)), comp_fps)} shown full-screen by the "
                        "competitor but rebuilt inside the video box")
    for s in cl.segments:
        if s.frame_mix:
            cant.append(f"S{s.id:02d}: frame-blend retiming (verified path; exported with AE Frame Blending > Frame Mix)")
        elif s.retime and s.retime != "none":
            cant.append(f"S{s.id:02d}: {s.retime} retiming (AE Frame Blending approximates it)")
        if (s.audio or {}).get("pitch_preserved") and s.speed not in (0, 1):
            cant.append(f"S{s.id:02d}: pitch-preserved speed change (AE stretch changes pitch; use Time-Stretch on the audio)")
        if s.uncertain:
            cant.append(f"S{s.id:02d}: uncertain — {s.notes or 'see decisions.jsonl'}")
    for line in raw_only_overlay_text(getattr(ctx, "verify", None)):
        cant.append(f"{line} — the recreation (AE / preview) SHOWS it, the competitor does not: mask or blur it in "
                    "After Effects if you want it hidden")
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


def ae_phase_lines(cl: Any, cfg: Any) -> list[str]:
    """AE floor-rule safety (DESIGN §7.3, FX-10), from the exact slack of every frame of each stretch segment
    as written: cadence-pinned phases are INFORMATION ('phase pinned by cadence (±0.083 ms)': the measured
    frames -- or the audio in-point -- fix raw_in inside one breakpoint cell; exported frame-exact while AE's
    time resolution is unverified), real razor edges (``pipeline.ae_rule_sensitive``) one warning line."""
    from .config import Config
    from .pipeline import ae_phase_class, ae_rule_sensitive, phase_slack, time_line_spans
    cfg = cfg if cfg is not None else Config()
    cf, rf = cl.comp_fps, cl.raw_fps
    mode = str(getattr(cfg, "ae_time_mode", "auto") or "auto")
    tol = float(getattr(cfg, "ae_slack_tol_frames", 0.01))
    pinned, risky = [], []
    spans = time_line_spans(list(cl.segments))          # time-tied members: their group's line decides the cell
    for s in cl.segments:
        info = phase_slack(s, cf, rf, spans.get(int(s.id)))
        if info is None:
            continue
        if ae_rule_sensitive(s, cfg, cf, rf, spans.get(int(s.id))):
            risky.append(f"S{s.id:02d} ({info['slack_ms']:.6f} ms at frame {info['k']})")
        elif ae_phase_class(info, cfg) == "pinned":
            by = ("frames" if info["video_pinned"] else
                  "audio in-point" if (s.audio or {}).get("phase_source") == "audio" else "frame-rate lattice")
            pinned.append(f"S{s.id:02d} (±{info['slack_ms']:.3f} ms, {by})")
    out = []
    if pinned:
        out.append(f"- Phase pinned by cadence (information, not a risk): {len(pinned)} segment(s) — "
                   + ", ".join(pinned) + f" — raw_in is fixed inside one breakpoint cell of the {fps_str(rf)}-in-"
                   f"{fps_str(cf)} cadence (maximal information); exported frame-exact (time-remap HOLD keys at "
                   "j + 0.25) because After Effects' time resolution is unverified (s9_6)")
    out.append(f"- AE-rule-sensitive segments (exact floor-rule slack below {tol:g} RAW frame although more was "
               "possible" + (f", or kept in {mode} mode" if mode in ("stretch", "remap") else "") + "): "
               + (", ".join(risky) if risky else "none"))
    return out


def reassigned_frames(fm: Any) -> list[dict]:
    """Matched frames whose RAW frame segmentation changed (verification-honesty F2): refine measured
    ``measured`` (None: refine found no match, the frame was absorbed), the segment model shows ``model``;
    ``gap`` = score(measured) - score(model) from refine's candidate vector when both were evaluated."""
    d = getattr(fm, "d", None) or {}
    if "pre_segment_raw" not in d:
        return []
    pre_raw, raw = np.asarray(d["pre_segment_raw"]), np.asarray(fm.raw)
    pre_st = np.asarray(d.get("pre_segment_status", fm.status))
    st = np.asarray(fm.status)
    out = []
    j0s, cand = d.get("cand_j0"), d.get("cand")
    for k in np.nonzero(st == Status.MATCH)[0]:
        k = int(k)
        if int(pre_st[k]) == Status.MATCH and int(pre_raw[k]) == int(raw[k]):
            continue
        row = {"k": k, "measured": int(pre_raw[k]) if int(pre_st[k]) == Status.MATCH else None, "model": int(raw[k]),
               "gap": None}
        if row["measured"] is not None and j0s is not None and cand is not None:
            j0 = int(j0s[k])
            a, b = row["measured"] - j0, row["model"] - j0
            if j0 >= 0 and 0 <= a < cand.shape[1] and 0 <= b < cand.shape[1] and np.isfinite(cand[k, a]) \
                    and np.isfinite(cand[k, b]):
                row["gap"] = float(cand[k, a] - cand[k, b])
        out.append(row)
    return out


def verified_reassigned(ver: dict | None) -> list[dict]:
    """s9_2's re-assigned and mismatched rows (the plan's; the mock record's only when it differs) with verify's
    evidence (why / class / gap / delta, FX-11)."""
    s92 = ((ver or {}).get("checks") or {}).get("s9_2_ae_sim") or {}
    r = s92.get("plan") or {}
    rows = [dict(x, list="reassigned") for x in (r.get("reassigned") or [])]
    rows += [dict(x, list="mismatch") for x in (r.get("mismatches") or [])]
    return sorted(rows, key=lambda x: int(x.get("k", 0)))


def reassigned_lines(rows: list[dict], comp_fps: Fraction | None = None) -> list[str]:
    """The COMPLETE list of re-assigned / mismatched frames, grouped by reason, with each group's classes and every
    frame's evidence (measured m -> shown, class, gap, delta) -- FX-12; counted against criterion 3, never exempt."""
    if not rows:
        return []
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get("why") or ("mismatch" if r.get("list") == "mismatch" else "model")), []).append(r)
    out = [f"- Frames that do not show refine's measured best frame (counted against criterion 3): {len(rows)} — by reason:"]
    for why, rs in sorted(groups.items(), key=lambda kv: int(kv[1][0]["k"])):
        cls = Counter(str(x.get("class") or "not measured") for x in rs)
        ev = "; ".join(f"k {x['k']}: {('measured ' + str(x['m'])) if x.get('m') is not None else 'unmatched'} → shown "
                       f"{x.get('ae')}" + (f" ({x['class']}, gap {x['gap']:+.4f}, delta {x['delta']:.4f})"
                                           if x.get("gap") is not None and x.get("delta") is not None else
                                           (f" (delta {x['delta']:.4f})" if x.get("delta") is not None else ""))
                       for x in rs)
        out.append(f"  - {why}: {len(rs)} ({', '.join(f'{v} {c}' for c, v in sorted(cls.items()))}) — "
                   f"{_ranges_str([x['k'] for x in rs], comp_fps)} — {ev}")
    return out


def not_reproduced(ver: dict | None) -> list[str]:
    """'Frames not reproduced exactly' lines from s9_2 (plan and mock record): frames differing from the
    cutlist, re-assigned frames and frames whose AE frame differs from the measured m(k)."""
    s92 = ((ver or {}).get("checks") or {}).get("s9_2_ae_sim") or {}
    out = []
    for src_name in ("plan", "mock"):
        r = s92.get(src_name) or {}
        parts = []
        for key, n_key, label in (("plan_mismatches", "n_plan_mismatches", "differ from the cutlist (MAIN frames)"),
                                  ("reassigned", "n_reassigned", "re-assigned by segmentation"),
                                  ("mismatches", "n_mismatches", "AE frame ≠ measured m(k)")):
            lst = r.get(key) or []
            n = int(r.get(n_key) or len(lst))
            if n:
                fr = [int(x.get("k", x.get("K"))) for x in lst if x.get("k", x.get("K")) is not None]
                parts.append(f"{n} {label} ({_ranges_str(fr, None, 8)})")
        if parts:
            out.append(f"{'AE plan' if src_name == 'plan' else 'mock record'}: " + ", ".join(parts))
    return out


CUT_SIDE_LABELS = {"no_cut": "spurious cut", "repeat_pair": "inside a repeat pair", "excursion": "excursion",
                   "A_last": "A's last frame", "B_first": "B's first frame"}


def independent_findings(ver: dict | None) -> list[str]:
    """Findings of the hypothesis-neutral checks with frame lists: the temporal signature (s9_2b: pairs where the
    recreation's frame-to-frame change disagrees with the competitor's repeat / move labels, motion mismatches),
    the +-1 refit (s9_2c: frames where a neighbouring RAW frame with its own framing matches better) and the
    c2 cuts failed by the no-cut alternative / a repeat pair / an excursion."""
    checks = (ver or {}).get("checks") or {}
    out = []
    t = checks.get("s9_2b_temporal") or {}
    for mm in t.get("motion_mismatch") or []:
        out.append(f"Motion mismatch (s9_2b): S{int(mm['segment']):02d} holds one RAW frame on frames "
                   f"{mm['frames'][0]}–{mm['frames'][1]} while the competitor moves")
    kinds: dict[str, list[int]] = {}
    for d in t.get("disagreements") or []:
        kinds.setdefault(str(d.get("kind")), []).append(int(d["k"]))
    if kinds:
        out.append(f"Temporal signature disagrees with the competitor (s9_2b, {t.get('n_disagreements', 0)} of "
                   f"{t.get('pairs', '?')} frame pairs k|k+1): " + "; ".join(
                       f"{kd.replace('_', ' ')} at {_ranges_str(ks, None, 12)}" for kd, ks in kinds.items()))
    r = checks.get("s9_2c_refit") or {}
    wins = r.get("neighbour_wins") or []
    if wins:
        ex = "; ".join(f"k {w['k']}: shown RAW {w['raw']} {w['z_shown']} < RAW {w['best_neighbour']} {w['z_neighbour']}"
                       for w in wins[:4])
        out.append(f"A neighbouring RAW frame (own refitted framing) matches better (s9_2c, time/framing confound): "
                   f"{r.get('n_neighbour_wins', len(wins))} frames — {_ranges_str([w['k'] for w in wins], None, 12)} — {ex}")
    cuts = ((((ver or {}).get("criteria") or {}).get("c2_cuts") or {}).get("details") or {}).get("cuts") or []
    bad = {}
    for c in cuts:
        for sd in c.get("sides") or []:
            if sd.get("result") == "fail" and sd.get("side") in ("no_cut", "repeat_pair", "excursion"):
                bad.setdefault(CUT_SIDE_LABELS[sd["side"]], []).append(int(c["frame"]))
    for label, frames in bad.items():
        out.append(f"Cuts failed as {label} (c2): frames {', '.join(str(f) for f in frames[:30])}"
                   + (" …" if len(frames) > 30 else ""))
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
    ov_lines = raw_only_overlay_text(ver)
    if ov_lines:
        ex = vis.get("raw_only_overlay_frames") or []
        out += ["", "RAW-only overlays (measured; static in RAW coordinates, a RAW graphic the competitor lacks, small; "
                    "excluded from the visual, temporal and ±1 refit checks, every other pixel still compared): "
                + "; ".join(ov_lines)
                + (f". {len(ex)} matched frames reach the visual threshold only with them excluded: "
                   f"{_ranges_str(ex, None, 12)}." if ex else ".")]
    rej = ((ver.get("raw_only_overlays") or {}).get("rejected")) or []
    if rej:
        out += ["", "Regions that looked like RAW-only overlays but were NOT accepted (still compared): " + "; ".join(
            f"S{int(r['segment']):02d}" + (f" at {r['raw_rect'][0]:.0f},{r['raw_rect'][1]:.0f}" if r.get("raw_rect") else "")
            + f": {r.get('why')}" for r in rej[:8])]
    t = checks.get("s9_2b_temporal") or {}
    lab = (t.get("labels") or {}).get("counts") or {}
    if lab:
        out += ["", f"Temporal signature (competitor-only labels of the frame pairs k|k+1): {lab.get('repeat', 0)} repeat, "
                    f"{lab.get('move', 0)} move, {lab.get('unknown', 0)} unknown, {lab.get('cut', 0)} cut; "
                    f"{t.get('n_disagreements', 0)} pairs where the recreation disagrees, "
                    f"{len(t.get('motion_mismatch') or [])} motion mismatch(es)."]
        at = animated_text(ver)
        if at:
            out.append("Moving competitor text masked from both signatures (it moves over the picture and the recreation "
                       "never shows it; the motion is judged outside it): " + ", ".join(
                           f"frames {z['comp_in']}–{z['comp_out'] - 1} at x {z['x']:.0f}, y {z['y']:.0f} "
                           f"({z['glyphs']} glyphs, {z['step'][0]:+.1f}/{z['step'][1]:+.1f} px per frame)" for z in at) + ".")
    vrows = verified_reassigned(ver)
    if vrows:
        out += ["", "Frames that do not show refine's measured best frame (s9_2; each frame's two candidates re-scored "
                    "with their own per-frame framing):", "",
                md_table(["k", "measured m", "shown (AE)", "reason", "class", "gap", "delta"],
                         [[x["k"], x.get("m"), x.get("ae"), x.get("why"), x.get("class"),
                           "" if x.get("gap") is None else f"{x['gap']:+.4f}",
                           "" if x.get("delta") is None else f"{x['delta']:.4f}"] for x in vrows])]
    found = independent_findings(ver)
    if found:
        out += ["", "Independent checks (they never reuse an analysis decision):", ""] + [f"- {x}" for x in found]
    au = checks.get("s9_5_audio", {})
    if au.get("segments"):
        off = au.get("av_offset") or {}
        if off.get("published_ms") is not None:
            out += ["", f"A/V offset: published {off['published_ms']:+.3f} ms ({off.get('mode')} sync), measured here "
                        f"{off.get('verified_ms', 'n/a')} ms over {off.get('n', 0)} segment(s), tolerance ±{off.get('tolerance_ms')} ms: "
                        + ("confirmed" if off.get("confirmed") else "NOT confirmed")
                        + f"; every segment is judged on its residual after the expected lag {off.get('expected_lag_ms')} ms."]
        out += ["", f"Audio per segment ({au.get('audio_source', '')}; tolerance ±{au.get('tolerance_ms')} ms):", "",
                md_table(["segment", "result", "lag ms", "residual ms", "corr", "code", "checked as"],
                         [[f"S{int(r['id']):02d}", r.get("result"), r.get("lag_ms", ""), r.get("residual_ms", ""),
                           r.get("corr", ""), r.get("code", ""), f"run {r['run']}" if r.get("run") else ""]
                          for r in au["segments"]])]
    crit = ver.get("criteria", {})
    cuts = ((crit.get("c2_cuts") or {}).get("details") or {}).get("cuts") or []
    if cuts:
        out += ["", "Cuts (competitor vs recreation images in `debug/cuts/cut_XX.png`):", "",
                md_table(["cut", "frame", "kind", "status", "failed"],
                         [[f"{i:02d}: S{int(c['from']):02d}|S{int(c['to']):02d}", f"{c['frame']} ({c.get('tc', '')})",
                           c.get("kind"), c.get("status"),
                           ", ".join(CUT_SIDE_LABELS.get(sd.get("side"), str(sd.get("side"))) for sd in c.get("sides") or []
                                     if sd.get("result") == "fail")] for i, c in enumerate(cuts, start=1)])]
    mock = ((crit.get("c6_after_effects") or {}).get("details") or {}).get("mock") or {}
    if mock.get("checks"):
        out += ["", "After Effects mock run:", ""] + [f"- [{'x' if c['ok'] else ' '}] {c['check']}" for c in mock["checks"]]
    if ver.get("failures"):
        out += ["", "Failures:", ""] + [f"- {f}" for f in ver["failures"]]
    return out


def _how_to_open(ctx: Any) -> list[str]:
    cl = getattr(ctx, "cutlist", None)
    raw_file = (cl.raw.get("file") if cl else "") or "media/…"
    if getattr(getattr(ctx, "cfg", None), "premiere", False):
        xv = ((getattr(ctx, "exports", None) or {}).get("xml") or {})
        framing = (f" {xv['clips']} V1 clips with {xv['framing_changes']} framing changes: the framing changes only "
                   f"where the competitor's moves {xv['min_move']:g} px or more (--min-move); {xv['framing_kept']} "
                   f"clip(s) keep the framing of the clip before, {xv.get('face_centred', 0)} clip(s) are face-centred "
                   "(a replaced B-roll / uncertain spot: the main face at the window centre, zoom kept), and "
                   f"{xv['merged']} piece(s) of one continuous RAW take are joined to the clip before (no cut). Every "
                   "clip's picture edges are checked from the XML's own values: none leaves the window uncovered."
                   if "framing_changes" in xv else "")
        return [
            "1. Premiere Pro → **File → Import…** → `recreated_edit.xml` (keep the output folder together: the XML "
            f"points at `{raw_file}`; relink if Premiere asks).",
            "2. The sequence `Recreated Edit (Premiere)` is 1080×1920 at 60.00 fps: the edit on V1 (each clip framed into "
            "your template window), the RAW audio on A1 with the same cuts, V2 and above empty — put your overlay template "
            "and captions there." + framing,
            "3. Sequence markers name the UNCERTAIN and NOT-IN-RAW spots (and RETIME spots Premiere's XML cannot carry). "
            "Each clip's comment lists the Motion values to expect (Position, Scale) — check one clip after import.",
            "4. Captions: **File → Import…** → `captions.srt`, then drag it onto the sequence at 00:00:00:00 (Premiere "
            "puts it on a caption track). The Captions section of this report lists what to check.",
        ]
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


def _tc(c: dict) -> str:
    return f"{c.get('start_tc', '?')} → {c.get('end_tc', '?')}"


def _captions(ctx: Any) -> list[str]:
    """captions.srt (captions.py): the mode of each part, the caption-generator-prompt.md checks (24-character cap,
    *...* placeholders, possible mis-transcriptions / doubled / missing words) and every OCR / transcript
    disagreement. Flagged, never corrected."""
    cap = getattr(ctx, "captions", None) or {}
    if not cap:
        return ["Captions were not made in this run."]
    if cap.get("error"):
        return [f"captions.srt was not written: {cap['error']}"]
    fps = cap.get("fps", "60")
    out = []
    mode = cap.get("mode")
    by = cap.get("by_mode") or {}
    cn = cap.get("competitor_notes")
    if mode == "competitor" and cn is not None:
        o = cap.get("ocr") or {}
        out.append(f"- **Mode: competitor** ({cap.get('reason')}) — {by.get('competitor', 0)} captions with the "
                   f"competitor's on-screen timing and splits (caption band read on all {o.get('frames_read', 0)} "
                   "frames: a caption starts when new text appears and ends when it disappears or changes to different "
                   "words; blips under 0.15 s merged), their text the words spoken during each caption (transcript); "
                   f"OCR ({o.get('engine', 'OCR')}) only for the word split, names and non-speech captions; "
                   f"{by.get('fill', 0)} voice captions fill speech the competitor left uncaptioned.")
    elif mode == "competitor":
        o = cap.get("ocr") or {}
        out.append(f"- **Mode: competitor** ({cap.get('reason')}) — {by.get('competitor', 0)} captions copied exactly "
                   f"from the competitor's burned-in captions ({o.get('engine', 'OCR')}, {o.get('frames_read', 0)} frames "
                   f"read around {o.get('events', 0)} caption events; text, splits, frames, capitalisation and punctuation "
                   f"unchanged, no style rules applied), {by.get('fill', 0)} voice captions filling speech the competitor "
                   "left uncaptioned.")
    else:
        out.append(f"- **Mode: voice** ({cap.get('reason')}) — {by.get('voice', 0)} captions made from the voice-over by "
                   f"caption-generator-prompt.md, {by.get('placeholder', 0)} `*...*` placeholders.")
    tr = cap.get("transcriber") or {}
    out.append(f"- Transcript: {cap.get('source', '?')} — {tr.get('engine', 'faster-whisper')} `{tr.get('model', '')}`, "
               f"{tr.get('words', 0)} words with word timestamps" + (f" (**not available**: {tr['error']})"
                                                                     if tr.get("error") else "") + ".")
    if cap.get("path"):
        out.append(f"- File: `captions.srt` — {cap.get('count', 0)} captions on the {fps} fps sequence "
                   f"({cap.get('frames', 0)} frames), frame-exact (competitor frame k = sequence frame "
                   f"{'2k' if str(fps) == '60' else 'k x ratio'} for a 30 fps competitor). Premiere: File → Import → "
                   "captions.srt, then drag it onto the sequence (a caption track above V2).")
    for w in cap.get("warnings") or []:
        out.append(f"- Warning: {w}")
    for n in cap.get("notes") or []:
        out.append(f"- Note: {n}")
    out.append("- Speaker changes are not detected (the transcriber has no speaker diarisation): the "
               "speaker-change break of the grouping rules is not applied.")
    parts = cap.get("parts") or []
    if parts:
        from .captions import frame_ms, ms_tc
        from fractions import Fraction
        f = Fraction(str(fps))
        out += ["", "**Mode of each part**", "",
                md_table(["from", "to", "mode", "captions"],
                         [[ms_tc(frame_ms(p["start"], f)), ms_tc(frame_ms(p["end"], f)),
                           {"competitor": "competitor (copied)", "fill": "voice (fills uncaptioned speech)",
                            "voice": "voice"}.get(p["mode"], p["mode"]), p["count"]] for p in parts])]
    st = cap.get("stats") or {}
    if st.get("captions"):
        wp = st.get("words_pct") or {}
        rows = [["words per caption", "1–4 (1: 23%, 2: 39%, 3: 28%, 4: 9%), never more than 5",
                 f"1: {wp.get('1', 0)}%, 2: {wp.get('2', 0)}%, 3: {wp.get('3', 0)}%, 4: {wp.get('4', 0)}%, "
                 f"5+: {wp.get('5+', 0)}%"],
                ["characters", "median 11, 90th percentile 17, cap 24",
                 f"median {st.get('chars_median')}, p90 {st.get('chars_p90')}, max {st.get('chars_max')}"],
                ["on screen", "median 0.57 s", f"median {st.get('duration_median_s')} s"],
                ["reading rate", "about 18 characters per second", f"median {st.get('cps_median')}"],
                ["full stops and commas", "none", str(st.get("stops_commas"))],
                ["lower-case starts", "52%", f"{st.get('lower_start_pct')}%"],
                ["back to back", "100%", f"{st.get('back_to_back_pct')}%"],
                ["ending on a weak word", "none", str(st.get("weak_endings"))]]
        out += ["", "**Style check** (all captions" + (", including the copied competitor ones" if mode == "competitor"
                                                       else "") + ")", "", md_table(["", "my style", "this file"], rows)]
    oc = cap.get("over_cap") or []
    out += ["", f"**Captions at the 24-character cap**: {len(oc) if oc else 'none'}"]
    out += [f"- {_tc(c)} `{c['text']}` ({len(c['text'])} characters, {c['mode']})" for c in oc]
    ph = cap.get("placeholders") or []
    out += ["", f"**`*...*` placeholders** (silences over ~1 s — write the action there): {len(ph) if ph else 'none'}"]
    out += [f"- {_tc(c)}" for c in ph]
    wk = cap.get("weak_kept") or []
    if wk:
        out += ["", f"**Weak endings kept** ({len(wk)}; the rule could not move the word):"]
        out += [f"- {_seconds(w['time'])} `{w['text']}` — {w['reason']}" for w in wk]
    fl = cap.get("flags") or []
    out += ["", "**Possible mis-transcriptions, doubled or missing words** (flagged, not corrected"
            + ("; voice captions only" if mode == "competitor" else "") + f"): {len(fl) if fl else 'none'}"]
    out += [f"- {_seconds(x['time'])} {x['kind']}: {x['detail']}" for x in fl]
    if mode == "competitor" and cn is not None:
        sh = cap.get("short") or []
        out += ["", f"**Captions shorter than 0.1 s**: {len(sh) if sh else 'none'}"]
        out += [f"- {_tc(c)} `{c['text']}` (the competitor's own caption is this short)" for c in sh]
        for title, key, fmt in (
                ("Names spelt as the competitor writes them (OCR over the transcript)", "names",
                 lambda r: f"`{r['heard']}` → `{r['written']}`"),
                ("Non-speech captions (read from the picture)", "non_speech", lambda r: f"`{r['text']}`"),
                ("Captions with no words heard (text read from the picture)", "from_ocr", lambda r: f"`{r['text']}`"),
                ("Captions that could not be read and have no words heard: written `*...*`", "unreadable",
                 lambda r: f"OCR read `{r.get('ocr') or ''}`"),
                ("Where the competitor's caption reads differently from what is said (the spoken words are used)",
                 "differs", lambda r: f"caption `{r['ocr']}` · spoken `{r['text']}`")):
            rows = cn.get(key) or []
            out += ["", f"**{title}**: {len(rows) if rows else 'none'}"]
            out += [f"- {r['start_tc']} → {r['end_tc']} {fmt(r)}" for r in rows]
    elif mode == "competitor":
        dis = cap.get("disagreements") or []
        out += ["", f"**OCR / transcript disagreements** (every one; the caption text is never changed): "
                f"{len(dis) if dis else 'none'}"]
        if dis:
            out += ["", md_table(["time", "caption (OCR, kept)", "heard in the audio", "kind", "likely OCR mistake"],
                                 [[_tc(d), d["ocr"], d["heard"] or "—", d["kind"],
                                   "yes" if d.get("likely_ocr_mistake") else "no"] for d in dis])]
        ocr = cap.get("ocr") or {}
        for s_ in (ocr.get("notes") or {}).get("static") or []:
            out.append(f"- Static text in the caption band ignored: `{s_['text']}` (frames {s_['comp_in']}–{s_['comp_out']})")
        for u in (ocr.get("notes") or {}).get("unreadable") or []:
            out.append(f"- Unreadable caption-band text left out: competitor frames {u['comp_in']}–{u['comp_out']}")
        unsure = [c for c in cap.get("captions") or [] if c.get("mode") == "competitor"
                  and (float(c.get("agreement") or 1) < 0.6 or float(c.get("score") or 1) < 0.8)]
        if unsure:
            out += ["", f"**Copied captions the OCR was unsure of** ({len(unsure)}):"]
            out += [f"- {_tc(c)} `{c['text']}` — {c.get('reads')} frames read, agreement {c.get('agreement')}, "
                    f"score {c.get('score')}; readings {c.get('variants')}" for c in unsure]
    return out


def _broll(ctx: Any) -> list[str]:
    """--no-broll (broll.py): every replaced cutaway and every cutaway kept because the RAW audio does not continue
    under it, with competitor and sequence timecodes."""
    cfg = getattr(ctx, "cfg", None)
    br = getattr(ctx, "broll", None) or {}
    follow = bool(br.get("follow_audio"))
    if not (getattr(cfg, "no_broll", False) or follow):
        return ["Not used: the export shows the competitor's cutaways as they are. Run with `--no-broll` to let the "
                "main clip play through cutaways over continuous RAW audio."]
    if br.get("error"):
        return [f"`--no-broll` was not applied ({br['error']}): the export is the faithful edit."]
    cl = getattr(ctx, "cutlist", None)
    fps = cl.comp_fps if cl is not None else Fraction(30)
    from .export_xml_edl import premiere_settings
    seq = Fraction(premiere_settings(cfg)["fps"])

    def tcs(a: int, b: int) -> tuple[str, str]:
        comp = f"{timecode(a, fps)}–{timecode(b, fps)}"
        sa, sb = int(round(Fraction(a) * seq / fps)), int(round(Fraction(b) * seq / fps))
        return comp, f"{timecode(sa, seq)}–{timecode(sb, seq)}"

    rep, kept = br.get("replaced") or [], br.get("kept") or []
    lead = ("- Premiere default (B-roll follows the audio): every NOT-IN-RAW / uncertain / B-roll spot shows the RAW "
            "video of the audio playing there; where that audio is not from the RAW (music / voice-over) the previous "
            "RAW clip keeps playing with no RAW audio under it. V1 is never left empty. "
            if follow else "- `--no-broll`: ")
    out = [f"{lead}**{len(rep)} spot(s) replaced**, **{len(kept)} kept** as the competitor has them.",
           "- Changed: `recreated_edit.xml` (a `B-ROLL REPLACED` marker on each spot), `recreated_edit.edl` and "
           "`cutlist.csv`; the A1 audio follows the picture. `cutlist.json`, the preview / compare renders and the "
           "verification above still describe the competitor's own edit."]
    for n in br.get("notes") or []:
        out.append(f"- {n}")
    if rep:
        rows = []
        for r in rep:
            comp, sq = tcs(r["comp_in"], r["comp_out"])
            parts = r.get("parts") or [{"comp_in": r["comp_in"], "comp_out": r["comp_out"],
                                         "raw_in_seconds": r["raw_in_seconds"], "how": r.get("how") or "audio",
                                         "corr": r.get("corr")}]
            now, evs = [], []
            for p_ in parts:
                h = p_.get("how") or "audio"
                what = ("previous clip keeps playing" if h.startswith("keeps playing") else "RAW of the audio")
                now.append(f"{p_['comp_in']}–{p_['comp_out']}: {what} from RAW {float(p_['raw_in_seconds']):.3f}s")
                evs.append("too short to hear; between two shots of the same line" if r.get("bridged") else
                           "audio not from the RAW (music / voice-over): no RAW audio under it" if h == "keeps playing"
                           else "a frame or two: too short to check its audio" if h == "keeps playing (short)"
                           else f"corr {p_.get('corr')}" if p_.get("corr") is not None
                           else "found by the audio alignment")
            rows.append([f"S{int(r['segment']):02d}", f"{r['comp_in']}–{r['comp_out']}", comp, sq, r["showed"],
                         "<br>".join(now), "<br>".join(evs)])
        out += ["", "**Replaced spots** (a `B-ROLL REPLACED` marker on each in the XML)", "",
                md_table(["segment", "competitor frames", "competitor timecode", "sequence timecode (60 fps)",
                          "competitor showed", "now shows", "evidence"], rows)]
    if kept:
        rows = []
        for r in kept:
            comp, sq = tcs(r["comp_in"], r["comp_out"])
            rows.append([f"S{int(r['segment']):02d}", f"{r['comp_in']}–{r['comp_out']}", comp, sq, r["showed"],
                         r.get("why", "")])
        out += ["", "**Kept cutaways** (the RAW audio does not continue under them -- left as the competitor has "
                "them)", "", md_table(["segment", "competitor frames", "competitor timecode",
                                       "sequence timecode (60 fps)", "competitor showed", "why kept"], rows)]
    if not rep and not kept:
        out.append("- No cutaways found: every piece of the edit shows the clip its audio belongs to.")
    return out


def _seconds(t: float) -> str:
    from .captions import ms_tc
    return ms_tc(int(round(float(t) * 1000)))


def _outputs(ctx: Any) -> list[str]:
    out_dir = Path(getattr(ctx.cfg, "out_dir", "."))
    paths = getattr(ctx, "paths", {}) or {}
    rows = []
    desc = {"jsx": "After Effects build script", "aep": "After Effects project", "cutlist": "cut list (source of truth)",
            "csv": "cut list, one row per segment", "xml": "FCP7 XML (Premiere / Resolve)", "edl": "CMX3600 EDL",
            "preview": "frame-exact preview render", "compare": "competitor | recreation | difference",
            "captions": "captions (SRT) on the 60 fps sequence", "report": "this report",
            "broll": "--no-broll export cut list (cutaways replaced)",
            "verify": "verification results", "media": "AE-imported media",
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
    ("Summary", _summary),
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
    ("Captions", _captions),
    ("B-roll cutaways", _broll),
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
