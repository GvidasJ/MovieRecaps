"""Stage 8 editorial exports (prompt Stage 8; DESIGN.md §5 export_xml_edl.py).

``write_csv``         cutlist.csv -- one human-readable row per segment (the report's segment-table columns,
                      then machine-readable columns). The human-readable timecodes are ``common.timecode``
                      exactly like report.md and the AE layer names / markers (drop-frame ';' for 29.97 /
                      59.94, else NDF), so a CSV timecode lands on the same frame in a DF-displaying AE
                      timeline; the EDL alone stays NDF (its FCM header declares it).
``write_fcp7_xml``    recreated_edit.xml -- FCP7 XML (xmeml v5) for Premiere Pro / DaVinci Resolve.
``write_edl``         recreated_edit.edl -- CMX3600, NON-DROP FRAME, cuts + M2 speed lines + dissolves.
``validate_exports``  re-parses both files (OTIO ``cmx_3600`` / ``fcp_xml`` adapters AND own parsers, because
                      the fcp adapter ignores time remapping) and compares FRAME NUMBERS with the cutlist.

Edit model shared by the XML and the EDL (:func:`edit_events`)
---------------------------------------------------------------
Added audio (``cutlist.added_audio``: music / SFX / voice-over the competitor mixed in, never recreated)
becomes labelled placeholders: EDL ``* LOC:`` locator comments (YELLOW) on the event where the range
starts and FCP7 XML range markers on the sequence, both named ``'<TYPE> placeholder <tc in>-<tc out>'``
(record TC, exclusive end) -- see :func:`added_audio_markers`.

Segments are sorted by ``comp_in``; each one becomes an event whose RECORD range is its own range trimmed
at the next segment's ``comp_in`` (``rec_out = min(comp_out, next.comp_in)``), so events tile
``[0, competitor frames)`` exactly. A crossfade (DESIGN §3: ``B.comp_in = O``, ``A.comp_out = O + D``)
becomes a dissolve of ``D`` frames that STARTS at the edit point ``O`` (EDL: ``D`` event on B; XML: a
``transitionitem`` with ``alignment=start``), i.e. A continues for ``D`` frames under the dissolve using its
handle -- exactly the linear AE opacity ramp. NOT-IN-RAW placeholders, dips and flashes are black events
(EDL ``BL`` reel, XML slug generator); a dip is a black event with dissolves on both sides.

Time conventions
----------------
* Record timecode: competitor frame index counted at the nominal competitor rate, NDF, from 00:00:00:00.
* Source timecode: RAW frame index counted at the nominal RAW rate ``round(raw_fps)``, NDF (RAW frame 0 =
  00:00:00:00). The source-in frame of an event is the AE-rule frame (DESIGN §2.1) at its record-in.
* EDL speed: ``M2 = v * raw_fps * nominal(comp) / comp_fps`` source-TC frames per record-TC second -- equal
  to DESIGN's ``speed * raw_fps`` for integer competitor rates (the synthetic test and every TikTok/Shorts
  edit); for NTSC competitor rates it keeps same-rate normal speed at the nominal value (30.000, no M2).
  Written with 3 decimals; negative = reverse, 0 = freeze. Emitted when it differs from the nominal
  record rate or when the RAW and record TC rates differ (CMX needs it to accept different durations).
* XML speed: ``Time Remap`` filter, ``speed`` = 100 * |v| percent (real-time relative), ``reverse`` flag.
  Sequence rate = competitor fps (``ntsc`` TRUE for x/1001), clipitem/file rate = RAW rate.
"""
from __future__ import annotations

import csv
import math
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from .common import atomic_write_text, fps_str, log, timecode
from .geometry import Sim, sim_to_ae
from .model import Box, Cutlist, Segment

__all__ = ["EditEvent", "edit_events", "write_csv", "write_fcp7_xml", "write_edl", "validate_exports",
           "edl_m2", "parse_edl_text", "parse_fcp7_xml", "CSV_COLUMNS", "added_audio_markers"]

_AE_EPS = 1e-9
SPEED_TOL = 0.002                    # validate_exports: relative speed tolerance (0.2 %)
EDL_REEL = "AX"
BLACK_REEL = "BL"
SEQUENCE_NAME = "Recreated Edit"

CSV_COLUMNS = ["#", "comp in-out (tc / frames)", "duration", "RAW in-out (tc)", "speed", "flip",
               "scale / position", "transition", "confidence", "notes",
               # machine-readable columns
               "id", "type", "comp_in", "comp_out", "frames", "raw_in_frame", "raw_out_frame", "raw_in_seconds",
               "speed_value", "speed_measured", "flip_h", "scale", "rotation_deg", "tx", "ty", "transform_keys",
               "time_mode", "transition_in", "transition_out", "audio_in_offset_frames", "audio_out_offset_frames",
               "audio_exception", "label"]


# ---------------------------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------------------------

def _nominal(fps: Fraction) -> int:
    return int(round(float(fps)))


def _ae_frame(raw_in: float, v: float, k: int, comp_in: int, comp_fps: Fraction, raw_fps: Fraction) -> int:
    """AE sampling rule (DESIGN §2.1) -- phase_solve.ae_frame when available, else the identical expression."""
    try:
        from .phase_solve import ae_frame
    except ImportError:  # pragma: no cover - phase_solve is a sibling module
        rf, cf = float(Fraction(raw_fps)), float(Fraction(comp_fps))
        return int(math.floor(rf * (float(raw_in) + float(v) * ((float(k) - float(comp_in)) / cf)) + _AE_EPS))
    return int(ae_frame(float(raw_in), float(v), int(k), int(comp_in), comp_fps, raw_fps))


def _remap_seconds(keys: list[dict], k: float) -> float:
    """time_remap_keys [{comp_frame, raw_seconds}] linearly interpolated at comp frame k (held outside)."""
    ks = sorted(keys, key=lambda d: float(d["comp_frame"]))
    if k <= float(ks[0]["comp_frame"]):
        return float(ks[0]["raw_seconds"])
    for a, b in zip(ks[:-1], ks[1:]):
        ka, kb = float(a["comp_frame"]), float(b["comp_frame"])
        if ka <= k <= kb:
            u = 0.0 if kb == ka else (k - ka) / (kb - ka)
            return float(a["raw_seconds"]) + u * (float(b["raw_seconds"]) - float(a["raw_seconds"]))
    return float(ks[-1]["raw_seconds"])


def _raw_in_seconds(seg: Segment, raw_fps: Fraction) -> float:
    if seg.raw_in_seconds is not None:
        return float(seg.raw_in_seconds)
    if seg.time_remap_keys:
        return _remap_seconds(seg.time_remap_keys, float(seg.comp_in))
    if seg.raw_in_frame is not None:
        return float((Fraction(int(seg.raw_in_frame)) + Fraction(1, 2)) / raw_fps)
    raise ValueError(f"segment {seg.id}: no raw_in_seconds / raw_in_frame")


def seg_raw_frame(seg: Segment, k: int, comp_fps: Fraction, raw_fps: Fraction) -> int:
    """RAW frame the segment's AE time model shows at competitor frame k (extrapolated past the ends)."""
    if seg.time_remap_keys:
        return int(math.floor(_remap_seconds(seg.time_remap_keys, float(k)) * float(raw_fps) + _AE_EPS))
    return _ae_frame(_raw_in_seconds(seg, raw_fps), float(seg.speed), int(k), int(seg.comp_in), comp_fps, raw_fps)


def seg_speed(seg: Segment, comp_fps: Fraction) -> float:
    """Playback speed used in the exports: seg.speed, or the average slope of the time-remap keys."""
    if seg.time_remap_keys and len(seg.time_remap_keys) >= 2:
        ks = sorted(seg.time_remap_keys, key=lambda d: float(d["comp_frame"]))
        dk = float(ks[-1]["comp_frame"]) - float(ks[0]["comp_frame"])
        if dk > 0:
            return (float(ks[-1]["raw_seconds"]) - float(ks[0]["raw_seconds"])) / (dk / float(comp_fps))
    return float(seg.speed)


def edl_m2(speed: float, raw_fps: Fraction, comp_fps: Fraction) -> float:
    """CMX3600 M2 field: source-TC frames per record-TC second (see module docstring)."""
    return float(speed) * float(raw_fps) * _nominal(comp_fps) / float(comp_fps)


def _speed_from_m2(m2: float, raw_fps: Fraction, comp_fps: Fraction) -> float:
    return float(m2) * float(comp_fps) / (float(raw_fps) * _nominal(comp_fps))


def _src_advance(speed: float, n_rec: int, raw_fps: Fraction, comp_fps: Fraction) -> int:
    """RAW frames advanced over n_rec record frames (rounded; negative for reverse)."""
    return int(round(float(speed) * n_rec * float(raw_fps) / float(comp_fps)))


def _tc_to_frames(tc: str, nominal: int) -> int:
    """HH:MM:SS:FF (NDF, ':' or ';' separators) -> frame count at an integer nominal rate."""
    m = re.fullmatch(r"(-?)(\d+):(\d\d):(\d\d)[:;.](\d\d)", tc.strip())
    if not m:
        raise ValueError(f"bad timecode {tc!r}")
    sign = -1 if m.group(1) else 1
    hh, mm, ss, ff = (int(g) for g in m.groups()[1:])
    return sign * (((hh * 60 + mm) * 60 + ss) * nominal + ff)


def _tc(frame: int, fps: Fraction) -> str:
    """NDF timecode (EDL / XML, whose headers declare NON-DROP FRAME)."""
    return timecode(int(frame), Fraction(fps), drop_frame=False)


def _tc_display(frame: int, fps: Fraction) -> str:
    """Human-readable timecode of cutlist.csv: common.timecode's default (DF for 30000/1001 and 60000/1001),
    the same string report.md and the AE layer names / markers show for that frame."""
    return timecode(int(frame), Fraction(fps))


# ---------------------------------------------------------------------------------------------
# Added audio (music / SFX / voice-over) placeholders
# ---------------------------------------------------------------------------------------------

def _added_audio_kind(t: Any) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(t or "audio")).strip("-").upper()
    return {"VO": "VOICE-OVER", "VOICEOVER": "VOICE-OVER"}.get(s, s or "AUDIO")


def added_audio_markers(cutlist: Cutlist) -> list[dict]:
    """Labelled placeholders for the audio the competitor ADDED (cutlist.added_audio: music bed, SFX,
    voice-over -- detected, logged, never recreated): [{type, comp_in, comp_out, name, label}] sorted by
    comp_in, clamped to [0, competitor frames); label = '<TYPE> placeholder <tc in>-<tc out>' in record TC
    (NDF, as the EDL / XML declare; end exclusive like the report), e.g. 'MUSIC placeholder
    00:00:00:00-00:00:18:06'. Entries without a valid range are skipped (logged)."""
    comp_fps = cutlist.comp_fps
    n_total = int(cutlist.competitor["frames"])
    out: list[dict] = []
    for a in cutlist.added_audio or []:
        try:
            k0 = max(0, int(a.get("comp_in", 0)))
            k1 = min(n_total, int(a.get("comp_out", n_total)))
        except (TypeError, ValueError, AttributeError):
            log.warning("export_xml_edl: added_audio entry %r has no valid range; no placeholder marker", a)
            continue
        if k1 <= k0:
            continue
        kind = _added_audio_kind(a.get("type"))
        name = f"{kind} placeholder"
        label = f"{name} {_tc(k0, comp_fps)}-{_tc(k1, comp_fps)}"
        extra = []
        if isinstance(a.get("level_db"), (int, float)) and math.isfinite(float(a["level_db"])):
            extra.append(f"{float(a['level_db']):+.1f} dB re RAW audio")
        what = str(a.get("type") or "audio").replace("_", "-")
        comment = f"{label} (competitor-added {what}, not recreated - add your own{', ' + extra[0] if extra else ''})"
        out.append({"type": str(a.get("type") or "audio"), "comp_in": k0, "comp_out": k1, "name": name,
                    "label": label, "comment": comment})
    return sorted(out, key=lambda m: (m["comp_in"], m["comp_out"], m["name"]))


# ---------------------------------------------------------------------------------------------
# Edit model
# ---------------------------------------------------------------------------------------------

@dataclass
class EditEvent:
    """One editorial event (EDL event / XML track item) on the competitor timeline."""
    seg: Segment | None            # None for a filler gap
    kind: str                      # 'clip' (RAW) | 'black' (NOT-IN-RAW / dip / flash / gap)
    rec_in: int                    # record range [rec_in, rec_out), competitor frames
    rec_out: int
    dissolve_in: int = 0           # dissolve from the previous event over [rec_in, rec_in + dissolve_in)
    tail: int = 0                  # frames this event continues under the NEXT event's dissolve
    speed: float = 1.0
    src_in: int | None = None      # RAW frame at rec_in (AE rule); None for black events
    label: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def n_rec(self) -> int:
        return self.rec_out - self.rec_in

    @property
    def seg_name(self) -> str:
        return f"S{self.seg.id:02d}" if self.seg is not None else "GAP"


def _transition_dict(t: Any) -> dict | None:
    if not t:
        return None
    return t if isinstance(t, dict) else dict(vars(t))


def edit_events(cutlist: Cutlist) -> list[EditEvent]:
    """Tile [0, competitor frames) with editorial events (see module docstring). Gaps between segments
    (which verification reports as coverage failures) become black filler events so the exported
    timeline keeps the competitor's exact duration."""
    comp_fps, raw_fps = cutlist.comp_fps, cutlist.raw_fps
    n_total = int(cutlist.competitor["frames"])
    segs = sorted(cutlist.segments, key=lambda s: (int(s.comp_in), int(s.comp_out), int(s.id)))
    events: list[EditEvent] = []
    cursor = 0
    for i, seg in enumerate(segs):
        c_in, c_out = int(seg.comp_in), int(seg.comp_out)
        nxt = segs[i + 1] if i + 1 < len(segs) else None
        rec_in = max(c_in, 0)
        rec_out = min(c_out, int(nxt.comp_in)) if nxt is not None else c_out
        rec_out = min(rec_out, n_total)
        dissolve = 0
        if events and rec_in < cursor:
            log.warning("export_xml_edl: S%02d starts at %d inside the previous event (ends %d); trimmed",
                        seg.id, rec_in, cursor)
            rec_in = cursor
        if rec_in > cursor:
            events.append(EditEvent(None, "black", cursor, rec_in, label="GAP (no segment)",
                                    warnings=[f"frames {cursor}-{rec_in - 1} are not covered by any segment"]))
        if events and events[-1].seg is not None and rec_in == cursor:
            prev = events[-1].seg
            tr = _transition_dict(seg.transition_in) or _transition_dict(prev.transition_out)
            overlap = int(prev.comp_out) - c_in
            if overlap > 0:
                if tr:
                    D = int(tr.get("duration_frames") or overlap)
                    dissolve = max(0, min(D, overlap, rec_out - rec_in))
                    events[-1].tail = dissolve
                else:
                    events[-1].warnings.append(f"overlaps S{seg.id:02d} by {overlap} frames without a transition "
                                               "(exported as a cut)")
        if rec_out <= rec_in:
            log.warning("export_xml_edl: S%02d has no visible record frames after trimming; skipped", seg.id)
            continue
        if seg.type == "raw":
            v = seg_speed(seg, comp_fps)
            j0 = seg_raw_frame(seg, rec_in, comp_fps, raw_fps)
            ev = EditEvent(seg, "clip", rec_in, rec_out, dissolve, 0, v, max(0, j0))
            if j0 < 0:
                ev.warnings.append(f"RAW frame {j0} before the RAW start at record {rec_in}; clamped to 0")
        else:
            label = seg.label or {"not_in_raw": "NOT-IN-RAW placeholder", "dip": "dip", "flash": "flash"}.get(
                seg.type, seg.type)
            if seg.type in ("dip", "flash") and seg.color:
                label += f" {seg.color}"
            ev = EditEvent(seg, "black", rec_in, rec_out, dissolve, 0, 1.0, None, label)
        events.append(ev)
        cursor = rec_out
    if cursor < n_total:
        events.append(EditEvent(None, "black", cursor, n_total, label="GAP (no segment)",
                                warnings=[f"frames {cursor}-{n_total - 1} are not covered by any segment"]))
    return events


def _media(cutlist: Cutlist, cfg: Any, role: str) -> tuple[str, str]:
    """(basename, absolute path) of the AE-imported media of a role ('raw' | 'competitor')."""
    block = cutlist.raw if role == "raw" else cutlist.competitor
    ab = str(block.get("file_abs") or "")
    f = str(block.get("file") or block.get("file_rel") or "")
    if not ab and f:
        p = Path(f)
        if not p.is_absolute():
            base = Path(getattr(cfg, "out_dir", ".") or ".") if cfg is not None else Path(".")
            p = base / p
        ab = str(p.resolve())
    name = os.path.basename(ab or f) or f"{role}.mp4"
    return name, ab


def _file_url(path: str) -> str:
    if not path:
        return ""
    p = Path(path).as_posix()
    if not p.startswith("/"):
        p = "/" + p
    return "file://localhost" + urllib.parse.quote(p)


def _seg_label(seg: Segment) -> str:
    return f"S{seg.id:02d}"


# ---------------------------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------------------------

def _transition_str(seg: Segment) -> str:
    parts = []
    for label, t in (("in", seg.transition_in), ("out", seg.transition_out)):
        t = _transition_dict(t)
        if t:
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
    s = f"s {float(t['scale']):.4f} / ({float(t['tx']):.1f}, {float(t['ty']):.1f})"
    if abs(float(t.get("rotation_deg", 0.0))) > 1e-9:
        s += f" / rot {float(t['rotation_deg']):.2f} deg"
    return s


def _csv_row(seg: Segment, comp_fps: Fraction, raw_fps: Fraction) -> list[Any]:
    n = int(seg.comp_out) - int(seg.comp_in)
    comp = f"{_tc_display(seg.comp_in, comp_fps)}-{_tc_display(seg.comp_out, comp_fps)} ({seg.comp_in}-{seg.comp_out})"
    dur = f"{n}f / {n / float(comp_fps):.3f}s"
    raw_in_f = raw_out_f = None
    if seg.type == "raw":
        try:
            raw_in_f = seg.raw_in_frame if seg.raw_in_frame is not None else seg_raw_frame(seg, seg.comp_in, comp_fps, raw_fps)
            raw_out_f = seg.raw_out_frame if seg.raw_out_frame is not None else seg_raw_frame(seg, seg.comp_out - 1, comp_fps, raw_fps)
        except ValueError:
            pass
    if seg.type == "raw" and raw_in_f is not None:
        raw = (f"{_tc_display(max(0, raw_in_f), raw_fps)}-"
               f"{_tc_display(max(0, raw_out_f if raw_out_f is not None else raw_in_f), raw_fps)}")
        if seg.raw_in_seconds is not None:
            raw += f" (raw_in {float(seg.raw_in_seconds):.6f}s)"
    elif seg.type == "not_in_raw":
        raw = seg.label or "NOT-IN-RAW"
    else:
        raw = seg.type + (f" {seg.color}" if seg.color else "")
    if seg.type != "raw":
        speed = ""
    elif seg.time_remap_keys:
        speed = ("freeze" if seg.speed == 0 else ("reverse" if seg.speed < 0 else "ramp")) + \
            f" ({len(seg.time_remap_keys)} remap keys)"
    else:
        speed = f"{float(seg.speed):.4f}" + (" (unsnapped)" if seg.unsnapped else "")
    notes = [seg.notes] if seg.notes else []
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
    t = seg.transform or {}
    tin, tout = _transition_dict(seg.transition_in), _transition_dict(seg.transition_out)
    return [
        f"S{seg.id:02d}", comp, dur, raw, speed, "yes" if seg.flip_h else "", _framing_str(seg),
        _transition_str(seg), f"{float(seg.confidence or 0.0):.2f}", "; ".join(notes),
        seg.id, seg.type, seg.comp_in, seg.comp_out, n,
        "" if raw_in_f is None else raw_in_f, "" if raw_out_f is None else raw_out_f,
        "" if seg.raw_in_seconds is None else f"{float(seg.raw_in_seconds):.9f}",
        f"{float(seg.speed):.6f}" if seg.type == "raw" else "",
        "" if seg.speed_measured is None else f"{float(seg.speed_measured):.6f}",
        int(bool(seg.flip_h)),
        "" if not t else f"{float(t['scale']):.6f}", "" if not t else f"{float(t.get('rotation_deg', 0.0)):.4f}",
        "" if not t else f"{float(t['tx']):.3f}", "" if not t else f"{float(t['ty']):.3f}",
        len(seg.transform_keys or []), seg.time_mode,
        "" if not tin else f"{tin.get('type')}:{tin.get('duration_frames')}",
        "" if not tout else f"{tout.get('type')}:{tout.get('duration_frames')}",
        int(au.get("in_offset_frames") or 0), int(au.get("out_offset_frames") or 0), au.get("exception") or "",
        seg.label or "",
    ]


def write_csv(cutlist: Cutlist, path: str | os.PathLike) -> None:
    """cutlist.csv: one row per segment, sorted by comp_in (UTF-8, RFC 4180 quoting). Timecodes as in
    report.md (common.timecode: drop-frame for 29.97 / 59.94)."""
    comp_fps, raw_fps = cutlist.comp_fps, cutlist.raw_fps
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for seg in sorted(cutlist.segments, key=lambda s: (int(s.comp_in), int(s.id))):
            w.writerow(_csv_row(seg, comp_fps, raw_fps))
    os.replace(tmp, p)


# ---------------------------------------------------------------------------------------------
# EDL (CMX3600)
# ---------------------------------------------------------------------------------------------

def _edl_line(num: int, reel: str, chan: str, trans: str, src_in: str, src_out: str, rec_in: str, rec_out: str) -> str:
    return f"{num:03d}  {reel:<8} {chan:<5} {trans:<8} {src_in} {src_out} {rec_in} {rec_out}"


def _m2_field(m2: float) -> str:
    return ("-" if m2 < 0 else "") + f"{abs(m2):07.3f}"


def _edl_src(ev: EditEvent, raw_fps: Fraction, comp_fps: Fraction) -> tuple[int, int, float | None]:
    """(src_in, src_out, m2 or None) of an event in SOURCE-TC frames (RAW frames; black: record frames)."""
    if ev.kind != "clip":
        return 0, ev.n_rec, None
    m2 = edl_m2(ev.speed, raw_fps, comp_fps)
    need_m2 = abs(m2 - _nominal(comp_fps)) > 5e-4 or _nominal(raw_fps) != _nominal(comp_fps)
    if not need_m2:
        return int(ev.src_in), int(ev.src_in) + ev.n_rec, None
    return int(ev.src_in), int(ev.src_in) + _src_advance(ev.speed, ev.n_rec, raw_fps, comp_fps), m2


def write_edl(cutlist: Cutlist, path: str | os.PathLike, cfg: Any = None) -> None:
    """CMX3600 EDL (see module docstring for the timecode / M2 conventions)."""
    comp_fps, raw_fps = cutlist.comp_fps, cutlist.raw_fps
    raw_name, raw_abs = _media(cutlist, cfg, "raw")
    has_audio = bool(cutlist.raw.get("has_audio", True))
    chan = "B" if has_audio else "V"
    events = edit_events(cutlist)
    # CMX readers (OTIO included) reject comment lines before the first event: the conventions go
    # under event 001 instead.
    lines = [f"TITLE: {SEQUENCE_NAME}", "FCM: NON-DROP FRAME", ""]
    notes = [f"* NOTE: record TC = competitor frame at {_nominal(comp_fps)} fps NDF (competitor "
             f"{fps_str(comp_fps)} fps); source TC = RAW frame at {_nominal(raw_fps)} fps NDF (RAW "
             f"{fps_str(raw_fps)} fps); M2 = speed x RAW fps x {_nominal(comp_fps)} / competitor fps",
             f"* NOTE: {len(cutlist.segments)} segments, {int(cutlist.competitor['frames'])} frames; BL = NOT-IN-RAW "
             "placeholder / dip / flash (add your own media); J/L audio offsets are listed in cutlist.csv"]
    if cutlist.added_audio:
        notes.append("* NOTE: YELLOW locators = audio the competitor added (music / SFX / voice-over), not "
                     "recreated: labelled placeholders for your own")
    # added-audio placeholders: a YELLOW locator on the event where each range starts
    aa_by_event: dict[int, list[dict]] = {}
    for mk in added_audio_markers(cutlist):
        num_at = next((i for i, ev in enumerate(events, start=1) if ev.rec_in <= mk["comp_in"] < ev.rec_out),
                      len(events))
        aa_by_event.setdefault(num_at, []).append(mk)
    prev: EditEvent | None = None
    prev_src_out = 0
    for num, ev in enumerate(events, start=1):
        rec_in, rec_out = _tc(ev.rec_in, comp_fps), _tc(ev.rec_out, comp_fps)
        s_in, s_out, m2 = _edl_src(ev, raw_fps, comp_fps)
        src_rate = raw_fps if ev.kind == "clip" else comp_fps
        reel = EDL_REEL if ev.kind == "clip" else BLACK_REEL
        if ev.dissolve_in and prev is not None:
            p_reel = EDL_REEL if prev.kind == "clip" else BLACK_REEL
            p_rate = raw_fps if prev.kind == "clip" else comp_fps
            lines.append(_edl_line(num, p_reel, chan, "C", _tc(prev_src_out, p_rate), _tc(prev_src_out, p_rate),
                                   rec_in, rec_in))
            lines.append(_edl_line(num, reel, chan, f"D {ev.dissolve_in:03d}", _tc(s_in, src_rate),
                                   _tc(s_out, src_rate), rec_in, rec_out))
            lines.append(f"* FROM CLIP NAME: {raw_name if prev.kind == 'clip' else 'BLACK'}")
            lines.append(f"* TO CLIP NAME: {raw_name if ev.kind == 'clip' else 'BLACK'}")
        else:
            lines.append(_edl_line(num, reel, chan, "C", _tc(s_in, src_rate), _tc(s_out, src_rate), rec_in, rec_out))
            lines.append(f"* FROM CLIP NAME: {raw_name if ev.kind == 'clip' else 'BLACK'}")
        if ev.kind == "clip":
            if raw_abs:
                lines.append(f"* SOURCE FILE: {raw_abs}")
            if m2 is not None:
                lines.append(f"M2   {EDL_REEL:<8} {_m2_field(m2)}        {_tc(s_in, raw_fps)}")
            seg = ev.seg
            desc = f"* SEGMENT: {_seg_label(seg)} raw speed {ev.speed:.6f}"
            if seg.flip_h:
                desc += " FLIP-H"
            if seg.time_remap_keys:
                desc += " time-remap (average speed)"
            lines.append(desc + f" conf {float(seg.confidence or 0.0):.2f}")
        else:
            name = ev.seg_name
            lines.append(f"* SEGMENT: {name} {ev.seg.type if ev.seg else 'gap'} - {ev.label}")
        for w in ev.warnings:
            lines.append(f"* WARNING: {w}")
        if num == 1:
            lines.extend(notes)
        if ev.seg is not None:
            j = f"RAW {_tc(ev.src_in, raw_fps)}" if ev.kind == "clip" else ev.label
            lines.append(f"* LOC: {rec_in} RED     {'Start' if ev.rec_in == 0 else 'Cut'} {ev.seg_name} {j}")
        for mk in aa_by_event.get(num, []):
            lines.append(f"* LOC: {_tc(mk['comp_in'], comp_fps)} YELLOW  {mk['comment']}")
        lines.append("")
        prev = ev
        prev_src_out = s_out if ev.kind == "clip" else ev.n_rec
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")


def parse_edl_text(text: str) -> list[dict]:
    """Own minimal CMX3600 parser: one dict per event {num, reel, trans, dissolve, src_in, src_out, rec_in,
    rec_out (TC strings), m2 (float | None), comments [..]}; for dissolves the B line is the event."""
    out: list[dict] = []
    cur: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("TITLE:") or line.startswith("FCM"):
            continue
        m = re.match(r"^(\d+)\s+(\S+)\s+(\S+)\s+(C|D)\s+(?:(\d+)\s+)?(\S+)\s+(\S+)\s+(\S+)\s+(\S+)$", line)
        if m:
            num = int(m.group(1))
            ev = {"num": num, "reel": m.group(2), "chan": m.group(3), "trans": m.group(4),
                  "dissolve": int(m.group(5)) if m.group(5) else 0, "src_in": m.group(6), "src_out": m.group(7),
                  "rec_in": m.group(8), "rec_out": m.group(9), "m2": None, "comments": []}
            if cur is not None and cur["num"] == num:
                ev["a_side"] = {k: cur[k] for k in ("reel", "src_in", "src_out", "rec_in", "rec_out")}
                ev["comments"] = cur["comments"]
                out[-1] = ev
            else:
                out.append(ev)
            cur = ev
            continue
        if cur is None:
            continue
        mm = re.match(r"^M2\s+(\S+)\s+(-?[0-9.]+)\s+(\S+)$", line)
        if mm:
            cur["m2"] = float(mm.group(2))
            cur["m2_tc"] = mm.group(3)
        else:
            cur["comments"].append(line.lstrip("*").strip())
    return out


# ---------------------------------------------------------------------------------------------
# FCP7 XML (xmeml v5)
# ---------------------------------------------------------------------------------------------

def _sub(parent: ET.Element, tag: str, text: Any = None, **attrib: str) -> ET.Element:
    e = ET.SubElement(parent, tag, attrib)
    if text is not None:
        e.text = str(text)
    return e


def _rate_el(parent: ET.Element, fps: Fraction) -> ET.Element:
    fps = Fraction(fps)
    r = _sub(parent, "rate")
    ntsc = fps.denominator == 1001
    tb = int(round(float(fps * Fraction(1001, 1000)))) if ntsc else int(round(float(fps)))
    if not ntsc and fps.denominator != 1:
        log.warning("export_xml_edl: %s fps is not representable in FCP7 XML; timebase %d", fps_str(fps), tb)
    _sub(r, "timebase", tb)
    _sub(r, "ntsc", "TRUE" if ntsc else "FALSE")
    return r


def _fmt(x: float, nd: int = 6) -> str:
    s = f"{float(x):.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _param(effect: ET.Element, pid: str, name: str, value: Any = None, vmin: Any = None, vmax: Any = None,
           keys: list[tuple[int, Any]] | None = None) -> ET.Element:
    p = _sub(effect, "parameter")
    _sub(p, "parameterid", pid)
    _sub(p, "name", name)
    if vmin is not None:
        _sub(p, "valuemin", vmin)
    if vmax is not None:
        _sub(p, "valuemax", vmax)

    def put(parent: ET.Element, val: Any) -> None:
        v = _sub(parent, "value")
        if isinstance(val, tuple):
            _sub(v, "horiz", _fmt(val[0], 7))
            _sub(v, "vert", _fmt(val[1], 7))
        else:
            v.text = str(val)

    if value is not None:
        put(p, value)
    for when, val in keys or []:
        kf = _sub(p, "keyframe")
        _sub(kf, "when", int(when))
        put(kf, val)
    return p


def _effect(parent: ET.Element, name: str, effectid: str, category: str, etype: str,
            mediatype: str = "video") -> ET.Element:
    e = _sub(parent, "effect")
    _sub(e, "name", name)
    _sub(e, "effectid", effectid)
    _sub(e, "effectcategory", category)
    _sub(e, "effecttype", etype)
    _sub(e, "mediatype", mediatype)
    return e


def _xml_geometry(cutlist: Cutlist, cfg: Any) -> tuple[str, int, int, Box | None]:
    """(layout mode, sequence width, height, fill box) of the XML sequence."""
    mode = str(getattr(cfg, "layout_mode", None) or cutlist.layout.get("mode") or "match") if cfg is not None \
        else str(cutlist.layout.get("mode") or "match")
    Wc, Hc = int(cutlist.competitor["width"]), int(cutlist.competitor["height"])
    box = Box.from_dict(cutlist.layout["box"]) if (cutlist.layout or {}).get("box") else None
    if mode == "source":
        return mode, int(cutlist.raw["width"]), int(cutlist.raw["height"]), None
    if mode == "fill":
        size = str(getattr(cfg, "comp_size", "competitor") or "competitor") if cfg is not None else "competitor"
        m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", size)
        W, H = (int(m.group(1)), int(m.group(2))) if m else (1080, 1920)
        return mode, W, H, box or Box(0.0, 0.0, float(Wc), float(Hc))
    return "match", Wc, Hc, None


def _fill_transform(sim: Sim, flip: bool, box: Box, raw_wh: tuple[float, float], target_wh: tuple[float, float]) -> Sim:
    try:
        from .export_ae import fill_transform
    except ImportError:  # pragma: no cover - export_ae is a sibling module
        from .render_preview import fill_transform_local as fill_transform
    return fill_transform(sim, flip, box, raw_wh, target_wh)


def _own_box(seg: Segment) -> Box | None:
    """The segment's own layout box (Segment.box, DESIGN §7 D1) when valid, else None."""
    if not seg.box:
        return None
    try:
        b = seg.box if isinstance(seg.box, Box) else Box.from_dict(seg.box)
    except (KeyError, TypeError, ValueError):
        return None
    vals = (b.x, b.y, b.w, b.h, b.corner_radius)
    if not all(math.isfinite(float(v)) for v in vals) or b.w <= 0 or b.h <= 0:
        return None
    return b


def _seg_sims(seg: Segment, mode: str, W: int, H: int, fill_box: Box | None, raw_wh: tuple[int, int]
              ) -> list[tuple[float, Sim]]:
    """[(comp_frame, Sim into the sequence frame)] -- one entry for constant framing. fill: framed from the
    segment's own box when it has one (a fullscreen period, D1; as export_ae), else the layout box."""
    if mode == "source":
        return [(float(seg.comp_in), Sim(1.0, 0.0, 0.0, 0.0))]
    keys = sorted(seg.transform_keys or [], key=lambda d: float(d["comp_frame"]))
    items = [(float(k["comp_frame"]), Sim.from_dict(k)) for k in keys] if keys else \
        ([(float(seg.comp_in), Sim.from_dict(seg.transform))] if seg.transform else [])
    if mode == "fill" and fill_box is not None:
        fb = _own_box(seg) or fill_box
        items = [(k, _fill_transform(s, bool(seg.flip_h), fb, raw_wh, (W, H))) for k, s in items]
    return items


def _basic_motion(parent: ET.Element, seg: Segment, mode: str, W: int, H: int, fill_box: Box | None,
                  box: Box | None, raw_wh: tuple[int, int], comp_fps: Fraction, raw_fps: Fraction) -> None:
    """Basic Motion (+ Crop) filters of one RAW clipitem."""
    sims = _seg_sims(seg, mode, W, H, fill_box, raw_wh)
    if not sims:
        return
    flip = bool(seg.flip_h) and mode != "source"

    def motion(sim: Sim) -> tuple[float, float, tuple[float, float]]:
        ae = sim_to_ae(sim, flip, raw_wh[0], raw_wh[1], r=1.0)
        cx, cy = (ae.position[0] - W / 2.0) / W, (ae.position[1] - H / 2.0) / H
        return 100.0 * sim.s, sim.theta_deg, (cx, cy)

    f = _sub(parent, "filter")
    e = _effect(f, "Basic Motion", "basic", "motion", "motion")
    if len(sims) == 1:
        sc, rot, ctr = motion(sims[0][1])
        _param(e, "scale", "Scale", _fmt(sc), 0, 1000)
        _param(e, "rotation", "Rotation", _fmt(rot), -8640, 8640)
        _param(e, "center", "Center", ctr)
    else:
        # keyframe times in source-media frames (FCP7 keyframes stay with the media when a clip is
        # trimmed): the RAW frame shown at the key's comp frame (AE rule), so the first key == <in>
        whens = [int(math.floor(float(raw_fps) * _raw_seconds_cont(seg, k, comp_fps, raw_fps) + _AE_EPS))
                 for k, _ in sims]
        vals = [motion(s) for _, s in sims]
        _param(e, "scale", "Scale", None, 0, 1000, [(w, _fmt(v[0])) for w, v in zip(whens, vals)])
        _param(e, "rotation", "Rotation", None, -8640, 8640, [(w, _fmt(v[1])) for w, v in zip(whens, vals)])
        _param(e, "center", "Center", None, keys=[(w, v[2]) for w, v in zip(whens, vals)])
    _param(e, "centerOffset", "Anchor Point", (0.0, 0.0))
    # crop to the competitor's box (match mode, constant framing without rotation)
    if mode == "match" and box is not None and len(sims) == 1 and abs(sims[0][1].theta_deg) < 1e-9:
        _crop_filter(parent, sims[0][1], box, raw_wh)


def _raw_seconds_cont(seg: Segment, k: float, comp_fps: Fraction, raw_fps: Fraction) -> float:
    if seg.time_remap_keys:
        return _remap_seconds(seg.time_remap_keys, float(k))
    return _raw_in_seconds(seg, raw_fps) + float(seg.speed) * (float(k) - float(seg.comp_in)) / float(comp_fps)


def _crop_filter(parent: ET.Element, sim: Sim, box: Box | None, raw_wh: tuple[int, int]) -> None:
    if box is None:
        return
    W, H = raw_wh
    x0, x1 = (box.x - sim.tx) / sim.s, (box.x + box.w - sim.tx) / sim.s
    y0, y1 = (box.y - sim.ty) / sim.s, (box.y + box.h - sim.ty) / sim.s
    left, right = max(0.0, min(100.0, 100.0 * x0 / W)), max(0.0, min(100.0, 100.0 * (W - x1) / W))
    top, bottom = max(0.0, min(100.0, 100.0 * y0 / H)), max(0.0, min(100.0, 100.0 * (H - y1) / H))
    if max(left, right, top, bottom) <= 1e-6:
        return
    f = _sub(parent, "filter")
    e = _effect(f, "Crop", "crop", "motion", "motion")
    for pid, val in (("left", left), ("right", right), ("top", top), ("bottom", bottom)):
        _param(e, pid, pid, _fmt(val, 4), 0, 100)


def _time_remap(parent: ET.Element, speed: float, mediatype: str = "video") -> None:
    f = _sub(parent, "filter")
    e = _effect(f, "Time Remap", "timeremap", "motion", "motion", mediatype)
    _param(e, "variablespeed", "variablespeed", 0, 0, 1)
    _param(e, "speed", "speed", _fmt(100.0 * abs(speed), 4), -100000, 100000)
    _param(e, "reverse", "reverse", "TRUE" if speed < 0 else "FALSE")
    _param(e, "frameblending", "frameblending", "FALSE")


def _file_el(parent: ET.Element, fid: str, defined: set[str], name: str, abs_path: str, fps: Fraction,
             frames: int, w: int, h: int, audio: dict | None) -> None:
    fe = _sub(parent, "file", id=fid)
    if fid in defined:
        return
    defined.add(fid)
    _sub(fe, "name", name)
    _sub(fe, "pathurl", _file_url(abs_path))
    _rate_el(fe, fps)
    _sub(fe, "duration", int(frames))
    tc = _sub(fe, "timecode")
    _rate_el(tc, fps)
    _sub(tc, "string", _tc(0, fps))
    _sub(tc, "frame", 0)
    _sub(tc, "displayformat", "NDF")
    media = _sub(fe, "media")
    v = _sub(media, "video")
    sc = _sub(v, "samplecharacteristics")
    _rate_el(sc, fps)
    _sub(sc, "width", int(w))
    _sub(sc, "height", int(h))
    _sub(sc, "anamorphic", "FALSE")
    _sub(sc, "pixelaspectratio", "square")
    _sub(sc, "fielddominance", "none")
    if audio:
        a = _sub(media, "audio")
        asc = _sub(a, "samplecharacteristics")
        _sub(asc, "depth", 16)
        _sub(asc, "samplerate", int(audio.get("sample_rate") or 48000))
        _sub(a, "channelcount", int(audio.get("channels") or 2))


def write_fcp7_xml(cutlist: Cutlist, path: str | os.PathLike, cfg: Any = None) -> None:
    """FCP7 XML (xmeml v5): one video track (clipitems / slug generators / cross dissolves), one audio track
    (RAW audio clipitems at the same record ranges when the RAW has audio), sequence markers at cuts plus
    one labelled range marker per added-audio placeholder (:func:`added_audio_markers`).

    Sequence size: competitor (match), the fill target (fill: cfg.comp_size or 1080x1920) or RAW (source);
    rate: always the competitor fps (exact cut timing). Basic Motion = the canonical Sim through
    geometry.sim_to_ae (Scale = 100 s, Rotation = theta, Center = (Position - frame centre) / frame size),
    'Horizontal Flip' filter for flip_h, Crop to the competitor box in match mode (constant, unrotated
    framing), Time Remap speed filter when speed != 1."""
    comp_fps, raw_fps = cutlist.comp_fps, cutlist.raw_fps
    N = int(cutlist.competitor["frames"])
    raw_name, raw_abs = _media(cutlist, cfg, "raw")
    raw_w, raw_h = int(cutlist.raw["width"]), int(cutlist.raw["height"])
    raw_frames = int(cutlist.raw["frames"])
    has_audio = bool(cutlist.raw.get("has_audio", True))
    audio_info = {"sample_rate": cutlist.raw.get("audio_sample_rate") or 48000,
                  "channels": cutlist.raw.get("audio_channels") or 2} if has_audio else None
    mode, W, H, fill_box = _xml_geometry(cutlist, cfg)
    layout_box = Box.from_dict(cutlist.layout["box"]) if (cutlist.layout or {}).get("box") else None
    events = edit_events(cutlist)

    root = ET.Element("xmeml", version="5")
    seq = _sub(root, "sequence", id="sequence-1")
    _sub(seq, "name", SEQUENCE_NAME)
    _sub(seq, "duration", N)
    _rate_el(seq, comp_fps)
    tc = _sub(seq, "timecode")
    _rate_el(tc, comp_fps)
    _sub(tc, "string", _tc(0, comp_fps))
    _sub(tc, "frame", 0)
    _sub(tc, "displayformat", "NDF")
    media = _sub(seq, "media")
    video = _sub(media, "video")
    fmt = _sub(video, "format")
    sc = _sub(fmt, "samplecharacteristics")
    _rate_el(sc, comp_fps)
    _sub(sc, "width", W)
    _sub(sc, "height", H)
    _sub(sc, "anamorphic", "FALSE")
    _sub(sc, "pixelaspectratio", "square")
    _sub(sc, "fielddominance", "none")
    vtrack = _sub(video, "track")
    defined: set[str] = set()
    n_clip = n_gen = 0
    for i, ev in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        tail = nxt.dissolve_in if nxt is not None else 0
        if ev.dissolve_in:
            ti = _sub(vtrack, "transitionitem")
            _rate_el(ti, comp_fps)
            _sub(ti, "start", ev.rec_in)
            _sub(ti, "end", ev.rec_in + ev.dissolve_in)
            _sub(ti, "alignment", "start")
            # a dip = a black slug with cross dissolves on both sides
            e = _effect(ti, "Cross Dissolve", "Cross Dissolve", "Dissolve", "transition")
            _sub(e, "wipecode", 0)
            _sub(e, "wipeaccuracy", 100)
            _sub(e, "startratio", 0)
            _sub(e, "endratio", 1)
            _sub(e, "reverse", "FALSE")
        start = -1 if ev.dissolve_in else ev.rec_in
        end = -1 if tail else ev.rec_out
        if ev.kind == "clip":
            n_clip += 1
            seg = ev.seg
            ci = _sub(vtrack, "clipitem", id=f"clipitem-{n_clip}")
            _sub(ci, "name", f"{_seg_label(seg)} {raw_name}")
            _sub(ci, "enabled", "TRUE")
            _sub(ci, "duration", raw_frames)
            _rate_el(ci, raw_fps)
            _sub(ci, "start", start)
            _sub(ci, "end", end)
            src_out = int(ev.src_in) + _src_advance(ev.speed, ev.n_rec + tail, raw_fps, comp_fps)
            _sub(ci, "in", int(ev.src_in))
            _sub(ci, "out", src_out)
            _sub(ci, "alphatype", "none")
            _sub(ci, "pixelaspectratio", "square")
            _sub(ci, "anamorphic", "FALSE")
            _file_el(ci, "file-raw", defined, raw_name, raw_abs, raw_fps, raw_frames, raw_w, raw_h, audio_info)
            if abs(ev.speed - 1.0) > 1e-9:
                _time_remap(ci, ev.speed)
            seg_box = _own_box(seg) or layout_box            # crop: the segment's own box (D1) or the layout box
            _basic_motion(ci, seg, mode, W, H, fill_box, seg_box, (raw_w, raw_h), comp_fps, raw_fps)
            if seg.flip_h and mode != "source":
                f = _sub(ci, "filter")
                _effect(f, "Horizontal Flip", "Horizontal Flip", "Transform", "filter")
            st = _sub(ci, "sourcetrack")
            _sub(st, "mediatype", "video")
            _sub(st, "trackindex", 1)
            cm = _sub(ci, "comments")
            _sub(cm, "mastercomment1", f"{_seg_label(seg)} speed {ev.speed:.6f} conf {float(seg.confidence or 0):.2f}"
                 + (" FLIP-H" if seg.flip_h else ""))
        else:
            n_gen += 1
            gi = _sub(vtrack, "generatoritem", id=f"generatoritem-{n_gen}")
            label = ev.label
            if ev.seg is not None and ev.seg.type == "not_in_raw" and "MISSING" not in label.upper():
                label = f"MISSING - not in RAW {label}"
            _sub(gi, "name", f"{ev.seg_name} {label}".strip())
            _sub(gi, "enabled", "TRUE")
            _sub(gi, "duration", ev.n_rec + tail + ev.dissolve_in)
            _rate_el(gi, comp_fps)
            _sub(gi, "start", start)
            _sub(gi, "end", end)
            _sub(gi, "in", 0)
            _sub(gi, "out", ev.n_rec + tail)
            _sub(gi, "anamorphic", "FALSE")
            _sub(gi, "alphatype", "black")
            _effect(gi, "Slug", "slug", "Matte", "generator")
    # audio track: RAW audio at the video record ranges (cuts only; J/L offsets are in cutlist.csv)
    if has_audio:
        audio = _sub(media, "audio")
        _sub(audio, "numOutputChannels", 2)
        afmt = _sub(audio, "format")
        asc = _sub(afmt, "samplecharacteristics")
        _sub(asc, "depth", 16)
        _sub(asc, "samplerate", int(audio_info["sample_rate"]))
        atrack = _sub(audio, "track")
        n_a = 0
        for ev in events:
            if ev.kind != "clip":
                continue
            n_a += 1
            ai = _sub(atrack, "clipitem", id=f"clipitem-a{n_a}")
            _sub(ai, "name", f"{_seg_label(ev.seg)} {raw_name}")
            _sub(ai, "enabled", "TRUE")
            _sub(ai, "duration", raw_frames)
            _rate_el(ai, raw_fps)
            _sub(ai, "start", ev.rec_in)
            _sub(ai, "end", ev.rec_out)
            _sub(ai, "in", int(ev.src_in))
            _sub(ai, "out", int(ev.src_in) + _src_advance(ev.speed, ev.n_rec, raw_fps, comp_fps))
            _file_el(ai, "file-raw", defined, raw_name, raw_abs, raw_fps, raw_frames, raw_w, raw_h, audio_info)
            if abs(ev.speed - 1.0) > 1e-9:
                _time_remap(ai, ev.speed, "audio")
            st = _sub(ai, "sourcetrack")
            _sub(st, "mediatype", "audio")
            _sub(st, "trackindex", 1)
    # markers at every cut
    for i, ev in enumerate(events):
        if ev.seg is None:
            continue
        mk = _sub(seq, "marker")
        _sub(mk, "name", "Start" if ev.rec_in == 0 else f"Cut {i:02d}")
        if ev.kind == "clip":
            txt = (f"{ev.seg_name} RAW {_tc(ev.src_in, raw_fps)} speed {ev.speed:.3f} "
                   f"conf {float(ev.seg.confidence or 0):.2f}" + (" flip" if ev.seg.flip_h else ""))
        else:
            txt = f"{ev.seg_name} {ev.label}"
        _sub(mk, "comment", txt)
        _sub(mk, "in", ev.rec_in)
        _sub(mk, "out", -1)
    # added audio (music / SFX / voice-over): labelled range markers = placeholders for your own
    for aa in added_audio_markers(cutlist):
        mk = _sub(seq, "marker")
        _sub(mk, "name", aa["label"])
        _sub(mk, "comment", aa["comment"])
        _sub(mk, "in", aa["comp_in"])
        _sub(mk, "out", aa["comp_out"])
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    atomic_write_text(path, '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n")


def _text(el: ET.Element | None, path: str, default: Any = None) -> Any:
    if el is None:
        return default
    x = el.find(path)
    return default if x is None or x.text is None else x.text.strip()


def _xml_rate(el: ET.Element) -> Fraction | None:
    tb = _text(el, "rate/timebase")
    if tb is None:
        return None
    ntsc = str(_text(el, "rate/ntsc", "FALSE")).upper() == "TRUE"
    return Fraction(int(tb) * 1000, 1001) if ntsc else Fraction(int(tb))


def parse_fcp7_xml(path: str | os.PathLike) -> dict:
    """Own FCP7 XML parser (the OTIO fcp adapter ignores time remapping): {'rate', 'duration', 'width',
    'height', 'items': [{tag, name, start, end (resolved through transitions), in, out, speed, reverse,
    flip, rate, generator}], 'transitions': [{start, end, alignment}], 'audio_items': [...], 'markers'}."""
    root = ET.parse(str(path)).getroot()
    if root.tag != "xmeml":
        raise ValueError(f"{path}: root element is {root.tag!r}, expected xmeml")
    seq = root.find("sequence")
    if seq is None:
        raise ValueError(f"{path}: no sequence")
    out: dict[str, Any] = {"version": root.get("version"), "rate": _xml_rate(seq),
                           "duration": int(_text(seq, "duration", -1)),
                           "width": int(_text(seq, "media/video/format/samplecharacteristics/width", 0)),
                           "height": int(_text(seq, "media/video/format/samplecharacteristics/height", 0))}

    def items_of(track: ET.Element) -> tuple[list[dict], list[dict]]:
        els = [e for e in track if e.tag in ("clipitem", "generatoritem", "transitionitem")]
        items, trans = [], []
        for idx, e in enumerate(els):
            if e.tag == "transitionitem":
                trans.append({"start": int(_text(e, "start")), "end": int(_text(e, "end")),
                              "alignment": _text(e, "alignment", "center"), "name": _text(e, "effect/name", "")})
                continue

            def cut_point(t: ET.Element) -> int:
                s, en, al = int(_text(t, "start")), int(_text(t, "end")), _text(t, "alignment", "center")
                return s if al in ("start", "start-black") else en if al in ("end", "end-black") else (s + en) // 2

            start, end = int(_text(e, "start")), int(_text(e, "end"))
            if start == -1:
                prv = els[idx - 1] if idx > 0 else None
                if prv is None or prv.tag != "transitionitem":
                    raise ValueError(f"{e.tag} {e.get('id')}: start -1 without a preceding transition")
                start = cut_point(prv)
            if end == -1:
                nx = els[idx + 1] if idx + 1 < len(els) else None
                if nx is None or nx.tag != "transitionitem":
                    raise ValueError(f"{e.tag} {e.get('id')}: end -1 without a following transition")
                end = cut_point(nx)
            speed, reverse, flip = 1.0, False, False
            for fe in e.findall("filter/effect"):
                eid = _text(fe, "effectid", "")
                if eid == "timeremap":
                    for p in fe.findall("parameter"):
                        pid = _text(p, "parameterid")
                        if pid == "speed":
                            speed = float(_text(p, "value")) / 100.0
                        elif pid == "reverse":
                            reverse = str(_text(p, "value")).upper() == "TRUE"
                elif eid in ("Horizontal Flip", "flop", "Flop"):
                    flip = True
            items.append({"tag": e.tag, "id": e.get("id"), "name": _text(e, "name", ""), "start": start, "end": end,
                          "in": int(float(_text(e, "in", 0))), "out": int(float(_text(e, "out", 0))),
                          "speed": -speed if reverse else speed, "flip": flip, "rate": _xml_rate(e),
                          "generator": e.tag == "generatoritem"})
        return items, trans

    vtracks = seq.findall("media/video/track")
    if not vtracks:
        raise ValueError(f"{path}: no video track")
    out["items"], out["transitions"] = items_of(vtracks[0])
    out["audio_items"] = []
    for t in seq.findall("media/audio/track"):
        out["audio_items"].extend(items_of(t)[0])
    out["markers"] = [{"name": _text(m, "name", ""), "in": int(_text(m, "in", 0)), "out": int(_text(m, "out", -1)),
                       "comment": _text(m, "comment", "")} for m in seq.findall("marker")]
    return out


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------

def _speed_ok(got: float, want: float) -> bool:
    if abs(want) < 1e-9:
        return abs(got) < 1e-6
    return abs(got - want) <= SPEED_TOL * abs(want)


def _check_items(kind: str, items: list[dict], events: list[EditEvent], N: int, errors: list[str]) -> None:
    """Common frame-number checks: tiling of [0, N) and per-event record ranges."""
    if len(items) != len(events):
        errors.append(f"{kind}: {len(items)} items, expected {len(events)}")
    cursor = 0
    for it, ev in zip(items, events):
        if it["start"] != ev.rec_in or it["end"] != ev.rec_out:
            errors.append(f"{kind} {ev.seg_name}: record [{it['start']}, {it['end']}) != [{ev.rec_in}, {ev.rec_out})")
        if it["start"] != cursor:
            errors.append(f"{kind}: gap/overlap at frame {cursor} (item starts at {it['start']})")
        cursor = it["end"]
    if cursor != N:
        errors.append(f"{kind}: timeline ends at frame {cursor}, competitor has {N} frames")


def _validate_edl(cutlist: Cutlist, edl_path: Path, events: list[EditEvent], errors: list[str]) -> dict:
    comp_fps, raw_fps = cutlist.comp_fps, cutlist.raw_fps
    N = int(cutlist.competitor["frames"])
    cn, rn = _nominal(comp_fps), _nominal(raw_fps)
    res: dict[str, Any] = {}
    # own parser (exact M2 fields)
    own = parse_edl_text(edl_path.read_text())
    items = []
    for e in own:
        rec_in, rec_out = _tc_to_frames(e["rec_in"], cn), _tc_to_frames(e["rec_out"], cn)
        items.append({"start": rec_in, "end": rec_out, "reel": e["reel"], "dissolve": e["dissolve"],
                      "src_in": _tc_to_frames(e["src_in"], rn if e["reel"] != BLACK_REEL else cn), "m2": e["m2"]})
    _check_items("EDL", items, events, N, errors)
    for it, ev in zip(items, events):
        if ev.kind == "clip":
            if it["reel"] == BLACK_REEL:
                errors.append(f"EDL {ev.seg_name}: RAW clip exported as BL")
                continue
            if it["src_in"] != ev.src_in:
                errors.append(f"EDL {ev.seg_name}: source in {it['src_in']} != RAW frame {ev.src_in}")
            # no M2 line = normal speed = the nominal record rate in source-TC frames per second
            got = _speed_from_m2(it["m2"] if it["m2"] is not None else float(cn), raw_fps, comp_fps)
            if not _speed_ok(got, ev.speed):
                errors.append(f"EDL {ev.seg_name}: speed {got:.5f} (M2 {it['m2']}) != {ev.speed:.5f}")
        elif it["reel"] != BLACK_REEL:
            errors.append(f"EDL {ev.seg_name}: placeholder/dip exported with reel {it['reel']} (expected BL)")
        if it["dissolve"] != ev.dissolve_in:
            errors.append(f"EDL {ev.seg_name}: dissolve {it['dissolve']} != {ev.dissolve_in}")
    locs = [c for e in own for c in e["comments"] if c.startswith("LOC:")]
    for mk in added_audio_markers(cutlist):
        want = f"LOC: {_tc(mk['comp_in'], comp_fps)} YELLOW  {mk['label']}"
        if not any(c.startswith(want) for c in locs):
            errors.append(f"EDL: no locator for the {mk['label']!r} added-audio placeholder")
    res["own"] = {"events": len(own), "total_frames": items[-1]["end"] if items else 0}
    # OTIO cmx_3600 adapter (rate = competitor fps; one rate for source and record)
    try:
        import opentimelineio as otio
    except ImportError:
        res["otio"] = {"status": "not_available"}
        return res
    try:
        tl = otio.adapters.read_from_file(str(edl_path), adapter_name="cmx_3600", rate=float(comp_fps))
    except Exception as e:  # noqa: BLE001 - any adapter failure is a validation error
        errors.append(f"EDL: OTIO cmx_3600 failed to parse: {type(e).__name__}: {e}")
        res["otio"] = {"status": "failed", "error": str(e)}
        return res
    vt = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video]
    if not vt:
        errors.append("EDL: OTIO found no video track")
        return res
    track = vt[0]
    rate = float(comp_fps)
    clips = [c for c in track if isinstance(c, otio.schema.Clip)]
    total = int(round(track.duration().rescaled_to(rate).value))
    if total != N:
        errors.append(f"EDL: OTIO total duration {total} != competitor frames {N}")
    otio_items = []
    for c in clips:
        r = c.range_in_parent()
        start = int(round(r.start_time.rescaled_to(rate).value))
        dur = int(round(r.duration.rescaled_to(rate).value))
        ts = [fx.time_scalar for fx in c.effects if isinstance(fx, otio.schema.LinearTimeWarp)]
        # OTIO counts the source TC digits at the EDL (competitor) nominal rate; re-count them at the RAW
        # nominal rate (never via opentime.to_timecode: it infers drop-frame for 29.97)
        v = int(round(c.source_range.start_time.value))
        is_black = isinstance(c.media_reference, otio.schema.GeneratorReference)
        otio_items.append({"start": start, "end": start + dur, "black": is_black,
                           "src_in": v if is_black else (v // cn) * rn + v % cn,
                           "m2": ts[0] * rate if ts else None})
    otio_errors: list[str] = []
    _check_items("EDL(otio)", otio_items, events, N, otio_errors)
    for it, ev in zip(otio_items, events):
        if ev.kind == "clip":
            if it["src_in"] != ev.src_in:
                otio_errors.append(f"EDL(otio) {ev.seg_name}: source in {it['src_in']} != {ev.src_in}")
            if it["m2"] is not None and not _speed_ok(_speed_from_m2(it["m2"], raw_fps, comp_fps), ev.speed):
                otio_errors.append(f"EDL(otio) {ev.seg_name}: M2 {it['m2']:.3f} -> speed "
                                   f"{_speed_from_m2(it['m2'], raw_fps, comp_fps):.5f} != {ev.speed:.5f}")
        elif not it["black"]:
            otio_errors.append(f"EDL(otio) {ev.seg_name}: BL event not read as a generator")
    errors.extend(otio_errors)
    res["otio"] = {"status": "ok" if not otio_errors else "mismatch", "clips": len(clips), "total_frames": total,
                   "transitions": sum(1 for c in track if isinstance(c, otio.schema.Transition))}
    return res


def _validate_xml(cutlist: Cutlist, xml_path: Path, events: list[EditEvent], errors: list[str]) -> dict:
    comp_fps, raw_fps = cutlist.comp_fps, cutlist.raw_fps
    N = int(cutlist.competitor["frames"])
    res: dict[str, Any] = {}
    mode = str((cutlist.layout or {}).get("mode") or "match")
    own = parse_fcp7_xml(xml_path)
    if own["rate"] != comp_fps:
        errors.append(f"XML: sequence rate {own['rate']} != competitor fps {fps_str(comp_fps)}")
    if own["duration"] != N:
        errors.append(f"XML: sequence duration {own['duration']} != competitor frames {N}")
    _check_items("XML", own["items"], events, N, errors)
    for it, ev in zip(own["items"], events):
        if ev.kind == "clip":
            if it["generator"]:
                errors.append(f"XML {ev.seg_name}: RAW clip exported as a generator")
                continue
            if it["in"] != ev.src_in:
                errors.append(f"XML {ev.seg_name}: in {it['in']} != RAW frame {ev.src_in}")
            if it["rate"] != raw_fps:
                errors.append(f"XML {ev.seg_name}: clip rate {it['rate']} != RAW fps {fps_str(raw_fps)}")
            if not _speed_ok(it["speed"], ev.speed):
                errors.append(f"XML {ev.seg_name}: speed {it['speed']:.5f} != {ev.speed:.5f}")
            want_flip = bool(ev.seg.flip_h) and mode != "source"
            if it["flip"] != want_flip:
                errors.append(f"XML {ev.seg_name}: horizontal flip {it['flip']} != {want_flip}")
        elif not it["generator"]:
            errors.append(f"XML {ev.seg_name}: placeholder/dip is not a generator item")
    got_mk = {(m["name"], m["in"], m["out"]) for m in own["markers"]}
    for mk in added_audio_markers(cutlist):
        if (mk["label"], mk["comp_in"], mk["comp_out"]) not in got_mk:
            errors.append(f"XML: no range marker for the {mk['label']!r} added-audio placeholder "
                          f"[{mk['comp_in']}, {mk['comp_out']})")
    want_tr = [(ev.rec_in, ev.rec_in + ev.dissolve_in) for ev in events if ev.dissolve_in]
    got_tr = [(t["start"], t["end"]) for t in own["transitions"]]
    if got_tr != want_tr:
        errors.append(f"XML: transitions {got_tr} != {want_tr}")
    res["own"] = {"items": len(own["items"]), "transitions": len(got_tr), "markers": len(own["markers"]),
                  "total_frames": own["items"][-1]["end"] if own["items"] else 0,
                  "audio_items": len(own["audio_items"])}
    try:
        import opentimelineio as otio
    except ImportError:
        res["otio"] = {"status": "not_available"}
        return res
    try:
        tl = otio.adapters.read_from_file(str(xml_path), adapter_name="fcp_xml")
    except Exception as e:  # noqa: BLE001
        errors.append(f"XML: OTIO fcp_xml failed to parse: {type(e).__name__}: {e}")
        res["otio"] = {"status": "failed", "error": str(e)}
        return res
    vt = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video]
    if not vt:
        errors.append("XML: OTIO found no video track")
        return res
    track = vt[0]
    rate = float(comp_fps)
    total = int(round(track.duration().rescaled_to(rate).value))
    clips = [c for c in track if isinstance(c, otio.schema.Clip)]
    otio_items = []
    for c in clips:
        r = c.range_in_parent()
        s = int(round(r.start_time.rescaled_to(rate).value))
        otio_items.append({"start": s, "end": s + int(round(r.duration.rescaled_to(rate).value))})
    otio_errors: list[str] = []
    if total != N:
        otio_errors.append(f"XML(otio): total duration {total} != competitor frames {N}")
    _check_items("XML(otio)", otio_items, events, N, otio_errors)
    for c, ev in zip(clips, events):
        if ev.kind == "clip":
            src = int(round(c.source_range.start_time.rescaled_to(float(raw_fps)).value))
            if src != ev.src_in:
                otio_errors.append(f"XML(otio) {ev.seg_name}: source start {src} != {ev.src_in}")
    errors.extend(otio_errors)
    res["otio"] = {"status": "ok" if not otio_errors else "mismatch", "clips": len(clips), "total_frames": total,
                   "transitions": sum(1 for c in track if isinstance(c, otio.schema.Transition))}
    return res


def validate_exports(cutlist: Cutlist, xml_path: str | os.PathLike, edl_path: str | os.PathLike) -> dict:
    """Re-parse the XML and the EDL and compare FRAME NUMBERS with the cutlist's edit events: every event's
    record range (tiling [0, competitor frames) exactly), source in (the AE-rule RAW frame), speed within
    0.2 %, dissolves, BL / slug placeholders; OTIO parses (cmx_3600 at rate = competitor fps; fcp_xml) must
    succeed and give the same totals. Returns {'ok', 'errors', 'total_frames', 'edl', 'xml', 'events'}."""
    errors: list[str] = []
    events = edit_events(cutlist)
    N = int(cutlist.competitor["frames"])
    out: dict[str, Any] = {"total_frames": N, "events": len(events)}
    for kind, p, fn in (("edl", Path(edl_path), _validate_edl), ("xml", Path(xml_path), _validate_xml)):
        if not p.exists():
            errors.append(f"{kind.upper()}: {p} does not exist")
            continue
        try:
            out[kind] = fn(cutlist, p, events, errors)
        except Exception as e:  # noqa: BLE001 - a parse crash is a validation failure, reported
            errors.append(f"{kind.upper()}: validation crashed: {type(e).__name__}: {e}")
    out["warnings"] = [f"{ev.seg_name}: {w}" for ev in events for w in ev.warnings]
    out["errors"] = errors
    out["ok"] = not errors
    if errors:
        log.warning("export validation: %d problem(s): %s", len(errors), "; ".join(errors[:5]))
    return out

