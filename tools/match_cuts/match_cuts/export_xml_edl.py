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

Audio (:func:`audio_items`)
---------------------------
RAW sync (``cutlist.settings.audio_sync`` = raw, the default) without audio lines: the audio follows the picture
events (cuts only; J/L offsets are listed in cutlist.csv) -- XML audio clipitems at the video record ranges, EDL
``B`` events. Otherwise every audible segment gets its OWN audio event (XML audio clipitem, EDL ``A`` event after
the video events, which become ``V``): competitor sync (DESIGN §7 D9, ``export_ae.audio_sync_params``) moves the
range by round(switch baseline x fps) frames (+ the genuine J/L offsets) and plays RAW time tau + v·g (the measured
A/V offset); a segment whose audio follows an audio line (FX-14: video-only retime / uncertain / placeholder over
continuous audio) plays that line. Editorial formats address whole frames: the source in is the NEAREST RAW frame
of the exact RAW time and the remainder (ms, < half a RAW frame) is written next to the event (XML clip comment,
EDL ``* AUDIO`` comment); AE and the preview keep it sample-exact.

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

from .common import atomic_write_text, fps_str, log, replace_file, timecode
from .geometry import Sim, sim_to_ae
from .model import Box, Cutlist, Segment

__all__ = ["EditEvent", "edit_events", "write_csv", "write_fcp7_xml", "write_edl", "validate_exports",
           "edl_m2", "parse_edl_text", "parse_fcp7_xml", "CSV_COLUMNS", "added_audio_markers", "AudioItem",
           "audio_items", "PremiereClip", "premiere_settings", "premiere_factor", "premiere_clips", "premiere_audio",
           "write_premiere_xml", "parse_premiere_xml", "validate_premiere_exports"]

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
            label = seg.label or {"not_in_raw": "NOT-IN-RAW placeholder", "dip": "dip", "flash": "flash",
                                  "uncertain": "UNCERTAIN"}.get(seg.type, seg.type)
            if seg.type in ("dip", "flash") and seg.color:
                label += f" {seg.color}"
            ev = EditEvent(seg, "black", rec_in, rec_out, dissolve, 0, 1.0, None, label)
        events.append(ev)
        cursor = rec_out
    if cursor < n_total:
        events.append(EditEvent(None, "black", cursor, n_total, label="GAP (no segment)",
                                warnings=[f"frames {cursor}-{n_total - 1} are not covered by any segment"]))
    return events


@dataclass
class AudioItem:
    """One audio event of the exports when the audio does not simply follow the picture events (module docstring)."""
    seg: Segment
    rec_in: int                    # record range [rec_in, rec_out), competitor frames
    rec_out: int
    src_in: int                    # NEAREST RAW frame of the exact RAW time played at rec_in
    speed: float
    remainder_ms: float            # exact RAW time at rec_in - src_in / raw_fps (ms; the sub-frame part formats lose)
    what: str                      # 'picture' | 'audio line'

    @property
    def n_rec(self) -> int:
        return self.rec_out - self.rec_in


def audio_sync_info(cutlist: Cutlist) -> dict:
    """{'mode', 'lag_s' (content offset g of the exported audio, 0 in raw sync), 'shift' (switch shift in competitor
    frames), 'split' (separate audio events needed: competitor sync or any audio line)} -- export_ae's single rule."""
    from .export_ae import audio_sync_params
    mode = str((cutlist.settings or {}).get("audio_sync") or "raw")
    g, sh = audio_sync_params(cutlist, cutlist.comp_fps, mode)
    lines = any((s.audio or {}).get("line") for s in cutlist.segments)
    return {"mode": mode, "lag_s": float(g), "shift": int(sh), "split": mode == "competitor" or lines}


def audio_items(cutlist: Cutlist) -> list[AudioItem]:
    """The audio events of the XML / EDL (module docstring): one per segment that plays RAW audio (its own map or
    its audio line, render_preview.audio_segment), in competitor sync over [comp_in + in_offset, comp_out +
    out_offset) + the switch shift with RAW time tau + v·g, in raw sync over its picture record range; clipped to
    [0, competitor frames). Empty when the audio simply follows the picture events (raw sync, no audio line)."""
    from .render_preview import audio_segment
    info = audio_sync_info(cutlist)
    if not info["split"]:
        return []
    comp_fps, raw_fps = cutlist.comp_fps, cutlist.raw_fps
    n_total = int(cutlist.competitor["frames"])
    g, sh = info["lag_s"], info["shift"]
    comp_sync = info["mode"] == "competitor"
    rec = {ev.seg.id: ev for ev in edit_events(cutlist) if ev.seg is not None}
    out: list[AudioItem] = []
    for seg in sorted(cutlist.segments, key=lambda s: (int(s.comp_in), int(s.comp_out), int(s.id))):
        a = audio_segment(seg)
        ev = rec.get(seg.id)
        if a is None or ev is None:
            continue
        au = seg.audio or {}
        if comp_sync:
            k0 = int(seg.comp_in) + int(au.get("in_offset_frames") or 0) + sh
            k1 = int(seg.comp_out) + int(au.get("out_offset_frames") or 0) + sh
            if not au.get("in_offset_frames"):
                k0 = ev.rec_in + sh                          # crossfade / trimmed record ranges stay those of the event
            if not au.get("out_offset_frames"):
                k1 = ev.rec_out + sh
        else:
            k0, k1 = ev.rec_in, ev.rec_out
        k0, k1 = max(0, k0), min(n_total, k1)
        if k1 <= k0:
            continue
        # the RAW time at record frame k0: the segment's own map + v·g (the switch shift moves the RANGE only, exactly
        # like export_ae's twins and build_audio; a remap curve plays g later: its value at k0 + g·fps)
        if a.time_remap_keys:
            tau = _remap_seconds(a.time_remap_keys, float(k0) + g * float(comp_fps))
            v = seg_speed(a, comp_fps)
        else:
            v = float(a.speed)
            tau = _raw_in_seconds(a, raw_fps) + v * (float(Fraction(k0 - int(seg.comp_in)) / comp_fps) + g)
        j = int(round(tau * float(raw_fps)))
        out.append(AudioItem(seg, k0, k1, max(0, j), v, (tau - j / float(raw_fps)) * 1000.0,
                             "audio line" if au.get("line") else "picture"))
    return out


def _audio_note(cutlist: Cutlist) -> str:
    """One line describing the exported audio sync (XML marker / EDL note)."""
    info = audio_sync_info(cutlist)
    if info["mode"] == "competitor":
        return (f"AUDIO SYNC competitor: RAW audio at the competitor's measured A/V offset {info['lag_s'] * 1000.0:+.1f} ms "
                f"(xcorr convention), switches moved {info['shift']:+d} frame(s); source = nearest RAW frame, the "
                "sub-frame remainder is noted per audio event (AE / preview are sample-exact)")
    return ("AUDIO: RAW lip-sync; audio lines (continuous audio under video-only retimes / placeholders) as separate "
            "audio events; source = nearest RAW frame, remainder noted per event")


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
    elif seg.type in ("not_in_raw", "uncertain"):
        raw = seg.label or ("NOT-IN-RAW" if seg.type == "not_in_raw" else "UNCERTAIN")
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
    replace_file(tmp, p)                     # retried / explained when Excel holds cutlist.csv open (Windows)


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
    a_items = audio_items(cutlist) if has_audio else []
    split = has_audio and audio_sync_info(cutlist)["split"]
    chan = "B" if has_audio and not split else "V"     # separate A events carry the audio (module docstring)
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
    if split:
        notes.append(f"* NOTE: {_audio_note(cutlist)}; video events V, audio events A (after the video events)")
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
    # separate audio events (competitor sync / audio lines): nearest RAW frame + the remainder as a comment
    for num, it in enumerate(a_items, start=len(events) + 1):
        ev = EditEvent(it.seg, "clip", it.rec_in, it.rec_out, speed=it.speed, src_in=it.src_in)
        s_in, s_out, m2 = _edl_src(ev, raw_fps, comp_fps)
        lines.append(_edl_line(num, EDL_REEL, "A", "C", _tc(s_in, raw_fps), _tc(s_out, raw_fps),
                               _tc(it.rec_in, comp_fps), _tc(it.rec_out, comp_fps)))
        lines.append(f"* FROM CLIP NAME: {raw_name}")
        if m2 is not None:
            lines.append(f"M2   {EDL_REEL:<8} {_m2_field(m2)}        {_tc(s_in, raw_fps)}")
        lines.append(f"* AUDIO: {_seg_label(it.seg)} {it.what}, nearest RAW frame, remainder {it.remainder_ms:+.3f} ms")
        lines.append("")
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
    # audio track: RAW audio at the video record ranges (cuts only; J/L offsets are in cutlist.csv) -- or, in
    # competitor sync / with audio lines, the separate audio events (audio_items: nearest frame + remainder)
    a_items = audio_items(cutlist) if has_audio else []
    if a_items:
        audio = _sub(media, "audio")
        _sub(audio, "numOutputChannels", 2)
        afmt = _sub(audio, "format")
        asc = _sub(afmt, "samplecharacteristics")
        _sub(asc, "depth", 16)
        _sub(asc, "samplerate", int(audio_info["sample_rate"]))
        atrack = _sub(audio, "track")
        for n_a, it in enumerate(a_items, start=1):
            ai = _sub(atrack, "clipitem", id=f"clipitem-a{n_a}")
            _sub(ai, "name", f"{_seg_label(it.seg)} {raw_name} audio")
            _sub(ai, "enabled", "TRUE")
            _sub(ai, "duration", raw_frames)
            _rate_el(ai, raw_fps)
            _sub(ai, "start", it.rec_in)
            _sub(ai, "end", it.rec_out)
            _sub(ai, "in", int(it.src_in))
            _sub(ai, "out", int(it.src_in) + _src_advance(it.speed, it.n_rec, raw_fps, comp_fps))
            _file_el(ai, "file-raw", defined, raw_name, raw_abs, raw_fps, raw_frames, raw_w, raw_h, audio_info)
            if abs(it.speed - 1.0) > 1e-9:
                _time_remap(ai, it.speed, "audio")
            st = _sub(ai, "sourcetrack")
            _sub(st, "mediatype", "audio")
            _sub(st, "trackindex", 1)
            cm = _sub(ai, "comments")
            _sub(cm, "mastercomment1", f"{_seg_label(it.seg)} {it.what}: nearest RAW frame, remainder "
                                       f"{it.remainder_ms:+.3f} ms")
        mk = _sub(seq, "marker")
        _sub(mk, "name", "Audio sync")
        _sub(mk, "comment", _audio_note(cutlist))
        _sub(mk, "in", 0)
        _sub(mk, "out", -1)
    elif has_audio:
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


# ---------------------------------------------------------------------------------------------
# Premiere Pro only (--premiere): 60.00 fps sequence for an overlay template with a video window
# ---------------------------------------------------------------------------------------------
#
# The sequence (default 1080x1920 at exactly 60/1, ntsc FALSE) carries the edit on V1 and the RAW audio on A1;
# V2 and above stay empty for the user's template and captions. Every competitor frame k becomes sequence frames
# [f k, f (k + 1)) with f = sequence fps / competitor fps (must be an integer: 60 / 30 = 2), so every cut lands on
# the same moment as in the competitor-rate plan. Clipitem <rate> = the sequence rate (Premiere's own convention):
# <in> / <out> / keyframe <when> count SOURCE time in sequence-rate frames (1/60 s), <start> / <end> sequence frames.
#
# Framing: the region of the RAW the competitor shows inside its box (the segment's own box -- a fullscreen period,
# DESIGN §7 D1 -- else the layout box) is mapped onto the template window by ONE uniform scale + translation (box
# centre -> window centre, the scale that makes the box cover the window): RAW -> sequence = A o Sim. Where the RAW
# does not cover the whole window under that map (a shot the competitor letterboxed / showed small) the clip is
# zoomed about the window centre by the least factor that covers it, at most premiere_max_zoom (1.05); one factor
# per clip so an animated framing never pumps. Pans / zooms / rotation stay Basic Motion keyframes. Motion values
# follow this module's FCP7 convention (Scale = 100 s of the native RAW size, Center = (Position - frame centre) /
# frame size, Rotation = theta); each clip's comment carries the Premiere Effect Controls values to compare after
# import (Position in sequence px, Scale %).
#
# Source in-point: <in> is a whole 1/60 s. The tool's plan allows any raw_in inside the segment's frame-exact
# interval (floor rule at the competitor rate), so <in> is the 1/60 s tick inside that interval nearest the plan's
# raw_in; when no tick lies inside (the interval can be narrower than 1/60 s) it is the nearest tick and the clip is
# listed (some competitor-rate frames may show a neighbouring RAW frame). Between two competitor frames the second
# 60 fps frame shows the RAW 1/60 s later (Premiere samples the source at the sequence rate).
#
# Not expressible in Premiere's XML import (said in markers and the validation warnings, never silently changed):
# variable time remapping / freezes / ramps (Premiere imports one constant speed per clip: the clip is placed at its
# first source frame with the segment's average speed -- 100 % for a freeze -- and a RETIME marker), and dissolves
# against black (dips / fades: listed, not exported; a cross dissolve between two clips is exported).

PREMIERE_SEQUENCE_NAME = "Recreated Edit (Premiere)"


@dataclass
class PremiereClip:
    """One V1 clip (and its A1 twin) of the Premiere export."""
    seg: Segment
    ev: EditEvent
    start: int                       # sequence frames (Premiere rate); -1 inside a transition, like FCP7
    end: int
    rec_start: int                   # the event's record range at the Premiere rate (always real values)
    rec_end: int
    src_in: int                      # SOURCE time at the cut, in sequence-rate frames (<in>)
    src_out: int
    speed: float
    in_exact: bool                   # <in> lies inside the segment's frame-exact interval
    in_error_ms: float               # <in> / fps minus the plan's exact RAW time at the cut (ms)
    zoom: float                      # extra zoom about the window centre (1 = the competitor's framing exactly)
    covered: bool                    # the RAW covers the template window on every key and between keys
    keys: list[tuple[int, Sim]]      # (<when> in source sequence-rate frames, Sim RAW -> sequence px)
    retime: str | None               # why the clip's speed is not the segment's real time map (marker text)
    events: list[EditEvent] = field(default_factory=list)   # the edit events it plays (several: merged, --min-move)
    framing_note: str = ""           # --min-move: why the framing is not this clip's own (kept from the clip before)

    @property
    def label(self) -> str:
        evs = self.events or [self.ev]
        return "+".join(_seg_label(e.seg) for e in evs) if len(evs) > 1 else _seg_label(self.seg)


def premiere_center(position: tuple[float, float], seq_wh: tuple[float, float], src_wh: tuple[float, float]
                    ) -> tuple[float, float]:
    """The Basic Motion <center> value for a Premiere Position (sequence px). Premiere reads <center> as the offset
    from the sequence centre in units of the SOURCE clip's frame size, not the sequence's: a 1920x1080 RAW in the
    1080x1920 sequence with <center> (0.622738, 0.064497) shows at Position 540 + 0.622738 x 1920 = 1735.7,
    960 + 0.064497 x 1080 = 1029.7 (the S21 gap the user measured; dividing by the sequence size had put it there)."""
    return ((position[0] - seq_wh[0] / 2.0) / float(src_wh[0]), (position[1] - seq_wh[1] / 2.0) / float(src_wh[1]))


def premiere_position(center: tuple[float, float], seq_wh: tuple[float, float], src_wh: tuple[float, float]
                      ) -> tuple[float, float]:
    """The Position (sequence px) Premiere shows for a Basic Motion <center> value (inverse of premiere_center)."""
    return (seq_wh[0] / 2.0 + float(center[0]) * float(src_wh[0]), seq_wh[1] / 2.0 + float(center[1]) * float(src_wh[1]))


def premiere_settings(cfg: Any = None) -> dict:
    """{'size': (W, H), 'fps': Fraction, 'window': (x, y, w, h) CORNER px, 'max_zoom'} of the Premiere export."""
    size = str(getattr(cfg, "premiere_size", None) or "1080x1920")
    m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", size)
    W, H = (int(m.group(1)), int(m.group(2))) if m else (1080, 1920)
    fps = Fraction(str(getattr(cfg, "premiere_fps", None) or "60"))
    win = tuple(float(v) for v in (getattr(cfg, "premiere_window", None) or (42.0, 555.0, 998.0, 1037.0)))
    static = getattr(cfg, "premiere_static_framing", None)
    move = getattr(cfg, "premiere_min_move", None)
    return {"size": (W, H), "fps": fps, "window": win,
            "max_zoom": float(getattr(cfg, "premiere_max_zoom", None) or 1.05),
            "static": True if static is None else bool(static),
            "min_move": 250.0 if move is None else max(0.0, float(move))}


def premiere_factor(comp_fps: Fraction, seq_fps: Fraction) -> int:
    """Sequence frames per competitor frame; ValueError when it is not a whole number (the cuts could not land on the
    competitor's moments: e.g. a 29.97 fps competitor in a 60.00 fps sequence)."""
    r = Fraction(seq_fps) / Fraction(comp_fps)
    if r.denominator != 1 or r < 1:
        raise ValueError(f"a {fps_str(Fraction(comp_fps))} fps edit cannot be placed frame-exactly on a "
                         f"{fps_str(Fraction(seq_fps))} fps sequence (ratio {fps_str(r)} is not a whole number)")
    return int(r)


def _window_map(sim: Sim, box: Box, win: tuple[float, float, float, float]) -> Sim:
    """A o sim: the competitor box mapped onto the window (uniform cover scale, box centre -> window centre)."""
    k = max(win[2] / box.w, win[3] / box.h)
    bx, by = box.x + box.w / 2.0, box.y + box.h / 2.0
    wx, wy = win[0] + win[2] / 2.0, win[1] + win[3] / 2.0
    return Sim(sim.s * k, sim.theta_deg, k * (sim.tx - bx) + wx, k * (sim.ty - by) + wy)


def _zoomed(sim: Sim, z: float, win: tuple[float, float, float, float]) -> Sim:
    wx, wy = win[0] + win[2] / 2.0, win[1] + win[3] / 2.0
    return Sim(sim.s * z, sim.theta_deg, z * (sim.tx - wx) + wx, z * (sim.ty - wy) + wy)


def _covers(sim: Sim, raw_wh: tuple[float, float], win: tuple[float, float, float, float], tol: float = 1e-6) -> bool:
    """The (flipped or not) RAW rectangle covers the window: its 4 corners map back inside [0, W] x [0, H]."""
    th = math.radians(sim.theta_deg)
    c, s = math.cos(th), math.sin(th)
    for qx, qy in ((win[0], win[1]), (win[0] + win[2], win[1]), (win[0], win[1] + win[3]),
                   (win[0] + win[2], win[1] + win[3])):
        dx, dy = (qx - sim.tx) / sim.s, (qy - sim.ty) / sim.s
        px, py = c * dx + s * dy, -s * dx + c * dy
        if not (-tol <= px <= raw_wh[0] + tol and -tol <= py <= raw_wh[1] + tol):
            return False
    return True


def _between_keys(a: Sim, b: Sim, raw_wh: tuple[float, float], n: int = 4) -> list[Sim]:
    """Sims Premiere shows between two keys: Position (of the RAW centre), Scale and Rotation interpolated linearly."""
    out = []
    pa, pb = sim_to_ae(a, False, *raw_wh).position, sim_to_ae(b, False, *raw_wh).position
    cx, cy = raw_wh[0] / 2.0, raw_wh[1] / 2.0
    for i in range(1, n):
        u = i / n
        s = a.s + u * (b.s - a.s)
        th = a.theta_deg + u * (b.theta_deg - a.theta_deg)
        px, py = pa[0] + u * (pb[0] - pa[0]), pa[1] + u * (pb[1] - pa[1])
        r = math.radians(th)
        out.append(Sim(s, th, px - s * (math.cos(r) * cx - math.sin(r) * cy), py - s * (math.sin(r) * cx + math.cos(r) * cy)))
    return out


def _cover_zoom(sims: list[Sim], raw_wh: tuple[float, float], win: tuple[float, float, float, float],
                zmax: float) -> tuple[float, bool]:
    """(least zoom z in [1, zmax] about the window centre that covers the window for every Sim, covered)."""
    probe = list(sims)
    for a, b in zip(sims[:-1], sims[1:]):
        probe += _between_keys(a, b, raw_wh)

    def ok(z: float) -> bool:
        return all(_covers(_zoomed(s, z, win), raw_wh, win) for s in probe)

    if ok(1.0):
        return 1.0, True
    if not ok(zmax):
        return zmax, False
    lo, hi = 1.0, zmax
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if ok(mid) else (mid, hi)
    return hi, True


def _static_framing(mapped: list[tuple[float, Sim]], k0: int, k1: int, raw_wh: tuple[float, float],
                    win: tuple[float, float, float, float]) -> tuple[Sim, float]:
    """One fixed framing for a clip (--premiere default: no camera movement): rotation 0, the competitor's framing
    (already mapped into the template window) averaged over the clip's frames [k0, k1) -- its Scale and the
    Position of the RAW centre, keys interpolated as they play -- then scaled up only as much as needed and moved
    the least to fully cover the window. Returns (Sim RAW -> sequence px, zoom over the average framing)."""
    import numpy as np
    W, H = float(raw_wh[0]), float(raw_wh[1])
    c = np.array([W / 2.0, H / 2.0])
    keys = sorted(mapped, key=lambda kv: kv[0])
    ks = np.array([k for k, _ in keys], float)
    sc = np.array([sm.s for _, sm in keys], float)
    cen = np.array([sm.linear() @ c + np.array([sm.tx, sm.ty]) for _, sm in keys], float)
    frames = np.arange(int(k0), max(int(k0) + 1, int(k1)), dtype=float)
    s_avg = float(np.mean(np.interp(frames, ks, sc)))
    cx = float(np.mean(np.interp(frames, ks, cen[:, 0])))
    cy = float(np.mean(np.interp(frames, ks, cen[:, 1])))
    x0, y0, ww, wh = (float(v) for v in win)
    s_cov = max(s_avg, ww / W, wh / H) * (1.0 + 1e-9)
    cx = min(max(cx, x0 + ww - s_cov * W / 2.0), x0 + s_cov * W / 2.0)
    cy = min(max(cy, y0 + wh - s_cov * H / 2.0), y0 + s_cov * H / 2.0)
    return Sim(s_cov, 0.0, cx - s_cov * W / 2.0, cy - s_cov * H / 2.0), s_cov / s_avg


def _picture_box(sim: Sim, raw_wh: tuple[float, float]) -> tuple[float, float, float, float]:
    """(left, right, top, bottom) of the RAW picture in sequence px."""
    th = math.radians(sim.theta_deg)
    c, s = math.cos(th), math.sin(th)
    xs, ys = [], []
    for x, y in ((0.0, 0.0), (raw_wh[0], 0.0), (0.0, raw_wh[1]), (raw_wh[0], raw_wh[1])):
        xs.append(sim.s * (c * x - s * y) + sim.tx)
        ys.append(sim.s * (s * x + c * y) + sim.ty)
    return min(xs), max(xs), min(ys), max(ys)


def framing_move(a: Sim, b: Sim, raw_wh: tuple[float, float]) -> float:
    """How far the picture moves from framing a to framing b, in sequence px: the biggest movement of its centre or
    of one of its edges (a zoom moves the edges, so it counts too)."""
    la, ra, ta, ba = _picture_box(a, raw_wh)
    lb, rb, tb, bb = _picture_box(b, raw_wh)
    centre = math.hypot((lb + rb - la - ra) / 2.0, (tb + bb - ta - ba) / 2.0)
    return max(centre, abs(lb - la), abs(rb - ra), abs(tb - ta), abs(bb - ba))


def _same_framing(a: Sim, b: Sim) -> bool:
    return max(abs(a.s - b.s), abs(a.theta_deg - b.theta_deg), abs(a.tx - b.tx), abs(a.ty - b.ty)) < 1e-9


def _least_cover(sim: Sim, raw_wh: tuple[float, float], win: tuple[float, float, float, float]) -> Sim:
    """The smallest change of a fixed framing (rotation 0) that covers the window: scaled up about its centre only as
    much as needed, then moved the least."""
    W, H = float(raw_wh[0]), float(raw_wh[1])
    cx, cy = sim.tx + sim.s * W / 2.0, sim.ty + sim.s * H / 2.0
    x0, y0, ww, wh = (float(v) for v in win)
    need = max(ww / W, wh / H)
    s = sim.s if sim.s >= need * (1.0 + 1e-12) else need * (1.0 + 1e-9)
    cx = min(max(cx, x0 + ww - s * W / 2.0), x0 + s * W / 2.0)
    cy = min(max(cy, y0 + wh - s * H / 2.0), y0 + s * H / 2.0)
    return Sim(s, 0.0, cx - s * W / 2.0, cy - s * H / 2.0)


def _hold_framing(clips: list[PremiereClip], raw_wh: tuple[float, float], win: tuple[float, float, float, float],
                  min_move: float, subject: str = "the competitor's") -> list[list[PremiereClip]]:
    """--min-move (after the fixed framing): a clip takes its own framing only when it is at least min_move px from
    the framing on screen (framing_move); otherwise it keeps that framing exactly -- across real cuts too -- changed
    only as little as needed if it would not cover the window. A clip with no framing keeps the one on screen.
    Returns the stretches of clips that show one framing."""
    runs: list[list[PremiereClip]] = []
    held: Sim | None = None
    held_from = ""
    for cl in clips:
        when = cl.keys[0][0] if cl.keys else cl.src_in
        own = cl.keys[0][1] if cl.keys else None
        if own is not None and held is not None and _same_framing(own, held):
            runs[-1].append(cl)                          # already showing it
            continue
        move = framing_move(held, own, raw_wh) if (held is not None and own is not None) else None
        if held is None or (move is not None and move >= min_move):
            if own is not None:
                held, held_from = own, cl.label
            runs.append([cl])
            continue
        keep = held if _covers(held, raw_wh, win, tol=1e-6) else _least_cover(held, raw_wh, win)
        cl.framing_note = (f"framing kept from {held_from}: " +
                           (f"{subject} moves {move:.0f} px here, under --min-move {min_move:g}" if move is not None
                            else "no framing measured here") +
                           ("" if keep is held else "; changed the least to cover the window"))
        if own is not None and cl.zoom > 0:
            cl.zoom = keep.s / (own.s / cl.zoom)          # relative to this clip's own (average) framing
        cl.keys = [(when, keep)]
        cl.covered = _covers(keep, raw_wh, win, tol=1e-6)
        if keep is held:
            runs[-1].append(cl)
        else:
            runs.append([cl])
        held = keep
    return runs


def _unreliable(cl: PremiereClip) -> str | None:
    """Why the clip's framing cannot be copied from the competitor: it plays a B-roll / NOT-IN-RAW / uncertain spot
    replaced by the RAW (broll.py; the framing there is a neighbour's), else None."""
    for e in cl.events or [cl.ev]:
        seg = e.seg
        b = (seg.audio or {}).get("broll") or {}
        hit = [r for r in b.get("ranges") or [] if max(int(r[0]), e.rec_in) < min(int(r[1]), e.rec_out)]
        if hit or seg.type in ("uncertain", "not_in_raw"):
            return f"{_seg_label(seg)} {b.get('replaced') or seg.type.replace('_', '-')} replaced"
    return None


def _face_centred(fr: Sim, face_x: float, raw_wh: tuple[float, float], win: tuple[float, float, float, float]) -> Sim:
    """The same zoom and height, moved sideways so RAW x face_x sits at the window's centre (then the least move that
    still covers the window)."""
    return _least_cover(Sim(fr.s, 0.0, win[0] + win[2] / 2.0 - fr.s * float(face_x), fr.ty), raw_wh, win)


def _settle_framing(clips: list[PremiereClip], cutlist: Cutlist, raw_wh: tuple[float, float],
                    win: tuple[float, float, float, float], min_move: float, fps: Fraction) -> None:
    """The final fixed framings: --min-move on the competitor's framings, then every stretch that shows one framing
    and cannot take it from the competitor -- it holds a replaced B-roll / NOT-IN-RAW / uncertain spot, or the framing
    would leave part of the window uncovered -- keeps its zoom and height with the main person's face at the window's
    centre (faces.main_face_x over the stretch's frames), and --min-move again between the final framings."""
    from . import faces
    runs = _hold_framing(clips, raw_wh, win, min_move)
    video = str(cutlist.raw.get("file_abs") or cutlist.raw.get("file") or "")
    raw_fps = float(cutlist.raw_fps)
    face_run = False
    for run in runs:
        fr = run[0].keys[0][1] if run[0].keys else None
        if fr is None:
            continue
        why = [w for w in (_unreliable(c) for c in run) if w]
        if not why and _covers(fr, raw_wh, win, tol=1e-6):
            continue
        times = [t for c in run for t in
                 ((c.src_in + (c.src_out - c.src_in) * (i + 0.5) / 5.0) / float(fps) for i in range(5))]
        view = ((win[0] - fr.tx) / fr.s, (win[0] + win[2] - fr.tx) / fr.s)
        fx, n = faces.main_face_x(video, raw_fps, times, view)
        reason = "; ".join(why) if why else "the framing would leave part of the window uncovered"
        if fx is None:
            new = _least_cover(fr, raw_wh, win)
            note = f"{reason}: no face found, framing kept" + ("" if new is fr else " (moved the least to cover)")
        else:
            new = _face_centred(fr, fx, raw_wh, win)
            note = (f"{reason}: face-centred -- the main face (RAW x {fx:.0f}, {n} frames) at the window centre, "
                    f"zoom kept")
        span = run[0].label + (f"..{run[-1].label}" if len(run) > 1 else "")
        for c in run:
            c.keys = [(c.keys[0][0] if c.keys else c.src_in, new)]
            c.covered = _covers(new, raw_wh, win, tol=1e-6)
            c.framing_note = f"{span}: {note}"
        face_run = True
    if face_run:                     # a moved stretch may now sit under min_move from its neighbour: hold again
        _hold_framing(clips, raw_wh, win, min_move, subject="its framing")


def _merge_continuous(clips: list[PremiereClip]) -> list[PremiereClip]:
    """--min-move: neighbouring clips that play one continuous RAW take (the next one starts on the very source frame
    the previous one ends on, same speed, same flip, no transition, no retime) with the same fixed framing become one
    clip -- no cut there. Real cuts (a jump in RAW time) stay."""
    out: list[PremiereClip] = []
    for cl in clips:
        p = out[-1] if out else None
        if (p is not None and p.end != -1 and cl.start != -1 and p.rec_end == cl.rec_start and cl.src_in == p.src_out
                and abs(cl.speed - p.speed) < 1e-9 and not p.retime and not cl.retime
                and bool(p.seg.flip_h) == bool(cl.seg.flip_h) and len(p.keys) == 1 and len(cl.keys) == 1
                and _same_framing(p.keys[0][1], cl.keys[0][1])):
            p.end, p.rec_end, p.src_out = cl.end, cl.rec_end, cl.src_out
            p.events = (p.events or [p.ev]) + (cl.events or [cl.ev])
            continue
        out.append(cl)
    return out


def _source_seconds(seg: Segment, k: float, comp_fps: Fraction, raw_fps: Fraction) -> float:
    """The plan's exact RAW time at competitor frame k (continuous; remap keys interpolated)."""
    if seg.time_remap_keys:
        return _remap_seconds(seg.time_remap_keys, float(k))
    return _raw_in_seconds(seg, raw_fps) + float(seg.speed) * (float(k) - float(seg.comp_in)) / float(comp_fps)


def _pick_in_tick(seg: Segment, rec_in: int, tau0: float, comp_fps: Fraction, fps: Fraction) -> tuple[int, bool]:
    """(<in> in 1/fps ticks, inside the frame-exact interval): the tick inside the segment's feasible raw_in interval
    (shifted to rec_in) nearest the plan's raw time, else the nearest tick."""
    f = float(fps)
    iv = seg.raw_in_interval if not seg.time_remap_keys else None
    if iv and len(iv) == 2 and float(iv[1]) > float(iv[0]):
        shift = float(seg.speed) * (rec_in - int(seg.comp_in)) / float(comp_fps)
        lo, hi = float(iv[0]) + shift, float(iv[1]) + shift
        n_lo, n_hi = math.ceil(lo * f - 1e-9), math.floor(hi * f - 1e-9)
        if n_hi >= n_lo:
            n = min(max(int(round(tau0 * f)), n_lo), n_hi)
            if lo - 1e-9 <= n / f < hi:
                return int(n), True
    return int(round(tau0 * f)), False


def premiere_clips(cutlist: Cutlist, cfg: Any = None) -> tuple[list[PremiereClip], list[dict], list[str]]:
    """(V1 clips, markers [{name, comment, in, out}], warnings) of the Premiere export (sequence-rate frames)."""
    st = premiere_settings(cfg)
    comp_fps, raw_fps, fps = cutlist.comp_fps, cutlist.raw_fps, st["fps"]
    fac = premiere_factor(comp_fps, fps)
    raw_wh = (float(cutlist.raw["width"]), float(cutlist.raw["height"]))
    Wc, Hc = float(cutlist.competitor["width"]), float(cutlist.competitor["height"])
    layout_box = Box.from_dict(cutlist.layout["box"]) if (cutlist.layout or {}).get("box") else Box(0.0, 0.0, Wc, Hc)
    win = st["window"]
    events = edit_events(cutlist)
    clips: list[PremiereClip] = []
    markers: list[dict] = []
    warnings: list[str] = []
    for i, ev in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        prev = events[i - 1] if i > 0 else None
        seg = ev.seg
        if ev.kind != "clip":
            if seg is not None and seg.type in ("uncertain", "not_in_raw"):
                kind = "UNCERTAIN" if seg.type == "uncertain" else "NOT IN RAW"
                markers.append({"name": f"{kind} {ev.seg_name}", "comment": ev.label or kind,
                                "in": ev.rec_in * fac, "out": ev.rec_out * fac})
            if ev.dissolve_in or (nxt is not None and nxt.dissolve_in):
                warnings.append(f"{ev.seg_name}: dissolve to / from black not exported (Premiere XML import keeps cross "
                                "dissolves between two clips only)")
            continue
        for r in ((seg.audio or {}).get("broll") or {}).get("ranges") or []:   # --no-broll (broll.py)
            lo, hi = max(int(r[0]), ev.rec_in), min(int(r[1]), ev.rec_out)
            if hi > lo:
                name = f"S{int(r[2]):02d}" if len(r) > 2 else ev.seg_name
                how = r[3] if len(r) > 3 else "audio"
                comment = ("the competitor's picture here was not the RAW of its audio; this is the RAW video of the "
                           "audio playing here" if how == "audio" else
                           "the competitor showed a cutaway for a frame or two here; the previous RAW clip keeps "
                           "playing" if how == "keeps playing (short)" else
                           "the competitor showed a cutaway over music / voice-over here; the previous RAW clip keeps "
                           "playing (no RAW audio under it)")
                markers.append({"name": f"B-ROLL REPLACED {name}", "comment": comment,
                                "in": lo * fac, "out": hi * fac})
        tail = nxt.dissolve_in if (nxt is not None and nxt.kind == "clip") else 0
        dis_in = ev.dissolve_in if (prev is not None and prev.kind == "clip") else 0
        retime = None
        if seg.time_remap_keys:
            v = seg_speed(seg, comp_fps)
            what = "freeze" if abs(v) < 1e-9 else ("frame blend" if seg.frame_mix else "variable speed")
            if abs(v) < 1e-9:
                v = 1.0
            retime = (f"RETIME {ev.seg_name}: {what} -- Premiere's XML import keeps one constant speed per clip; placed "
                      f"at its first source frame at {100.0 * v:.2f} %, redo the {what} by hand")
            markers.append({"name": f"RETIME {ev.seg_name}", "comment": retime, "in": ev.rec_in * fac,
                            "out": ev.rec_out * fac})
            warnings.append(retime)
        else:
            v = float(seg.speed)
        tau0 = _source_seconds(seg, ev.rec_in, comp_fps, raw_fps)
        n_in, exact = _pick_in_tick(seg, ev.rec_in, tau0, comp_fps, fps)
        if not exact and not seg.time_remap_keys:
            warnings.append(f"{ev.seg_name}: no 1/{float(fps):g} s source in-point inside its frame-exact interval; "
                            f"nearest tick is {1000.0 * (n_in / float(fps) - tau0):+.2f} ms from the plan (a few frames "
                            "may show a neighbouring RAW frame)")
        n_seq = (ev.n_rec + tail) * fac
        n_out = n_in + int(round(n_seq * v))
        # framing: the competitor box region -> the template window, keys at their SOURCE time (FCP7 media time)
        box = _own_box(seg) or layout_box
        items = sorted(seg.transform_keys or [], key=lambda d: float(d["comp_frame"]))
        sims = [(float(k["comp_frame"]), Sim.from_dict(k)) for k in items] if items else \
            ([(float(seg.comp_in), Sim.from_dict(seg.transform))] if seg.transform else [])
        if not sims:
            warnings.append(f"{ev.seg_name}: no framing in the cutlist; placed at Premiere's default position")
        mapped = [(k, _window_map(s, box, win)) for k, s in sims]
        if mapped and st["static"]:
            # no camera movement: one fixed Position / Scale, rotation 0, covering the window
            one, z = _static_framing(mapped, ev.rec_in, ev.rec_out, raw_wh, win)
            covered = _covers(one, raw_wh, win, tol=1e-6)
            keys = [(int(round(_source_seconds(seg, ev.rec_in, comp_fps, raw_fps) * float(fps))), one)]
        else:
            z, covered = _cover_zoom([s for _, s in mapped], raw_wh, win, st["max_zoom"]) if mapped else (1.0, False)
            if mapped and not covered:
                warnings.append(f"{ev.seg_name}: the RAW does not cover the template window even at {100.0 * z:.0f} % "
                                "of the competitor's framing (a letterboxed / small shot); check the clip by hand")
            keys = [(int(round(_source_seconds(seg, k, comp_fps, raw_fps) * float(fps))), _zoomed(s, z, win))
                    for k, s in mapped]
        clips.append(PremiereClip(seg, ev, -1 if dis_in else ev.rec_in * fac, -1 if tail else ev.rec_out * fac,
                                  ev.rec_in * fac, ev.rec_out * fac, n_in, n_out, v, exact,
                                  1000.0 * (n_in / float(fps) - tau0), z, covered, keys, retime, [ev]))
    if st["static"]:
        # fewer reframes and cuts: hold the framing under min_move px, face-centre what the competitor cannot frame,
        # then join the pieces of one take that are left alike
        _settle_framing(clips, cutlist, raw_wh, win, st["min_move"], fps)
        clips = _merge_continuous(clips)
    return clips, markers, warnings


def _premiere_motion(parent: ET.Element, clip: PremiereClip, W: int, H: int, raw_wh: tuple[int, int]) -> str:
    """Basic Motion of one clip; returns the Effect Controls text of its first key (the clip comment)."""
    flip = bool(clip.seg.flip_h)

    def motion(sim: Sim) -> tuple[float, float, tuple[float, float], tuple[float, float]]:
        ae = sim_to_ae(sim, flip, raw_wh[0], raw_wh[1], r=1.0)
        return 100.0 * sim.s, sim.theta_deg, premiere_center(ae.position, (W, H), raw_wh), ae.position
    if not clip.keys:
        return "no framing"
    f = _sub(parent, "filter")
    e = _effect(f, "Basic Motion", "basic", "motion", "motion")
    vals = [motion(s) for _, s in clip.keys]
    if len(clip.keys) == 1 or all(abs(v[0] - vals[0][0]) < 1e-9 and abs(v[1] - vals[0][1]) < 1e-9 and
                                  max(abs(v[2][0] - vals[0][2][0]), abs(v[2][1] - vals[0][2][1])) < 1e-12 for v in vals):
        sc, rot, ctr, _ = vals[0]
        _param(e, "scale", "Scale", _fmt(sc), 0, 1000)
        _param(e, "rotation", "Rotation", _fmt(rot), -8640, 8640)
        _param(e, "center", "Center", ctr)
    else:
        whens = [w for w, _ in clip.keys]
        _param(e, "scale", "Scale", None, 0, 1000, [(w, _fmt(v[0])) for w, v in zip(whens, vals)])
        _param(e, "rotation", "Rotation", None, -8640, 8640, [(w, _fmt(v[1])) for w, v in zip(whens, vals)])
        _param(e, "center", "Center", None, keys=[(w, v[2]) for w, v in zip(whens, vals)])
    _param(e, "centerOffset", "Anchor Point", (0.0, 0.0))
    sc, rot, _, pos = vals[0]
    return (f"Premiere Motion (first key): Position {pos[0]:.1f}, {pos[1]:.1f} px; Scale {sc:.2f} %; "
            f"Rotation {rot:.2f} deg" + ("; Horizontal Flip" if flip else "") +
            (f"; {len(clip.keys)} keys" if len(clip.keys) > 1 else "") +
            (f"; {clip.framing_note}" if clip.framing_note else
             "" if abs(clip.zoom - 1.0) < 1e-6 else f"; zoomed {100.0 * (clip.zoom - 1.0):.2f} % to cover the window") +
            (f"; one clip for {clip.label} (one continuous RAW take, same framing)" if len(clip.events) > 1 else ""))


def write_premiere_xml(cutlist: Cutlist, path: str | os.PathLike, cfg: Any = None) -> dict:
    """recreated_edit.xml for Premiere Pro (--premiere; see the section comment above): the 1080x1920 / 60.00 fps
    sequence, V1 = the RAW clips framed into the template window, A1 = their RAW audio at the same cuts (an audio
    line where FX-14 found one), markers on UNCERTAIN / NOT-IN-RAW (and RETIME) spots, V2+ empty. Returns
    {'clips', 'markers', 'warnings', 'factor'}."""
    st = premiere_settings(cfg)
    comp_fps, raw_fps, fps = cutlist.comp_fps, cutlist.raw_fps, st["fps"]
    fac = premiere_factor(comp_fps, fps)
    W, H = st["size"]
    N = int(cutlist.competitor["frames"]) * fac
    raw_name, raw_abs = _media(cutlist, cfg, "raw")
    raw_w, raw_h = int(cutlist.raw["width"]), int(cutlist.raw["height"])
    raw_frames = int(cutlist.raw["frames"])
    src_dur = int(math.floor(raw_frames * float(fps) / float(raw_fps)))
    has_audio = bool(cutlist.raw.get("has_audio", True))
    audio_info = {"sample_rate": cutlist.raw.get("audio_sample_rate") or 48000,
                  "channels": cutlist.raw.get("audio_channels") or 2} if has_audio else None
    clips, markers, warnings = premiere_clips(cutlist, cfg)
    if str((cutlist.settings or {}).get("audio_sync") or "raw") == "competitor":
        warnings.append("--audio-sync competitor is not used by the Premiere export: A1 keeps the RAW lip-sync at the "
                        "same cuts as V1")

    root = ET.Element("xmeml", version="5")
    seq = _sub(root, "sequence", id="sequence-1")
    _sub(seq, "name", PREMIERE_SEQUENCE_NAME)
    _sub(seq, "duration", N)
    _rate_el(seq, fps)
    tc = _sub(seq, "timecode")
    _rate_el(tc, fps)
    _sub(tc, "string", _tc(0, fps))
    _sub(tc, "frame", 0)
    _sub(tc, "displayformat", "NDF")
    media = _sub(seq, "media")
    video = _sub(media, "video")
    fmt = _sub(video, "format")
    sc = _sub(fmt, "samplecharacteristics")
    _rate_el(sc, fps)
    _sub(sc, "width", W)
    _sub(sc, "height", H)
    _sub(sc, "anamorphic", "FALSE")
    _sub(sc, "pixelaspectratio", "square")
    _sub(sc, "fielddominance", "none")
    vtrack = _sub(video, "track")                       # V1 only: V2+ stay empty for the template / captions
    defined: set[str] = set()
    for n, cl in enumerate(clips, start=1):
        ev = cl.ev
        prev_clip = clips[n - 2] if n >= 2 else None
        if cl.start == -1 and prev_clip is not None:
            ti = _sub(vtrack, "transitionitem")
            _rate_el(ti, fps)
            _sub(ti, "start", ev.rec_in * fac)
            _sub(ti, "end", (ev.rec_in + ev.dissolve_in) * fac)
            _sub(ti, "alignment", "start")
            e = _effect(ti, "Cross Dissolve", "Cross Dissolve", "Dissolve", "transition")
            _sub(e, "wipecode", 0)
            _sub(e, "wipeaccuracy", 100)
            _sub(e, "startratio", 0)
            _sub(e, "endratio", 1)
            _sub(e, "reverse", "FALSE")
        ci = _sub(vtrack, "clipitem", id=f"clipitem-{n}")
        _sub(ci, "name", f"{cl.label} {raw_name}")
        _sub(ci, "enabled", "TRUE")
        _sub(ci, "duration", src_dur)
        _rate_el(ci, fps)
        _sub(ci, "start", cl.start)
        _sub(ci, "end", cl.end)
        _sub(ci, "in", cl.src_in)
        _sub(ci, "out", cl.src_out)
        _sub(ci, "alphatype", "none")
        _sub(ci, "pixelaspectratio", "square")
        _sub(ci, "anamorphic", "FALSE")
        _file_el(ci, "file-raw", defined, raw_name, raw_abs, raw_fps, raw_frames, raw_w, raw_h, audio_info)
        if abs(cl.speed - 1.0) > 1e-9:
            _time_remap(ci, cl.speed)
        note = _premiere_motion(ci, cl, W, H, (raw_w, raw_h))
        if cl.seg.flip_h:
            f = _sub(ci, "filter")
            _effect(f, "Horizontal Flip", "Horizontal Flip", "Transform", "filter")
        stv = _sub(ci, "sourcetrack")
        _sub(stv, "mediatype", "video")
        _sub(stv, "trackindex", 1)
        cm = _sub(ci, "comments")
        _sub(cm, "mastercomment1", f"{cl.label} speed {cl.speed:.6f} conf {float(cl.seg.confidence or 0):.2f}")
        _sub(cm, "mastercomment2", note)
        _sub(cm, "mastercomment3", ("source in inside the frame-exact interval" if cl.in_exact else
                                    "source in = nearest 1/60 s (outside the frame-exact interval)")
             + f" ({cl.in_error_ms:+.2f} ms from the plan)")
    # A1: the RAW audio at the SAME record ranges as V1 (picture-synced; an audio line where FX-14 found one)
    if has_audio:
        audio = _sub(media, "audio")
        _sub(audio, "numOutputChannels", 2)
        afmt = _sub(audio, "format")
        asc = _sub(afmt, "samplecharacteristics")
        _sub(asc, "depth", 16)
        _sub(asc, "samplerate", int(audio_info["sample_rate"]))
        atrack = _sub(audio, "track")
        for n_a, it in enumerate(premiere_audio(cutlist, clips, cfg), start=1):
            ai = _sub(atrack, "clipitem", id=f"clipitem-a{n_a}")
            _sub(ai, "name", f"{_seg_label(it['seg'])} {raw_name} audio")
            _sub(ai, "enabled", "TRUE")
            _sub(ai, "duration", src_dur)
            _rate_el(ai, fps)
            _sub(ai, "start", it["start"])
            _sub(ai, "end", it["end"])
            _sub(ai, "in", it["in"])
            _sub(ai, "out", it["out"])
            _file_el(ai, "file-raw", defined, raw_name, raw_abs, raw_fps, raw_frames, raw_w, raw_h, audio_info)
            if abs(it["speed"] - 1.0) > 1e-9:
                _time_remap(ai, it["speed"], "audio")
            sta = _sub(ai, "sourcetrack")
            _sub(sta, "mediatype", "audio")
            _sub(sta, "trackindex", 1)
            cm = _sub(ai, "comments")
            _sub(cm, "mastercomment1", f"{_seg_label(it['seg'])} {it['what']}")
    for m in markers:
        mk = _sub(seq, "marker")
        _sub(mk, "name", m["name"])
        _sub(mk, "comment", m["comment"])
        _sub(mk, "in", m["in"])
        _sub(mk, "out", m["out"])
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    atomic_write_text(path, '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n")
    for w in warnings:
        log.info("premiere export: %s", w)
    return {"clips": len(clips), "markers": len(markers), "warnings": warnings, "factor": fac}


def premiere_audio(cutlist: Cutlist, clips: list[PremiereClip], cfg: Any = None) -> list[dict]:
    """A1 items [{seg, start, end, in, out, speed, what}] (sequence-rate frames): every V1 clip's RAW audio over the
    clip's record range (the same source in-point as the picture, so A1 and V1 stay locked), or the segment's audio
    line (FX-14) when its audio follows one; uncertain / NOT-IN-RAW spots get audio only from an audio line. A clip
    whose picture is a freeze plays no audio (as the preview: frozen pieces are silent)."""
    from .render_preview import audio_segment
    st = premiere_settings(cfg)
    comp_fps, raw_fps, fps = cutlist.comp_fps, cutlist.raw_fps, st["fps"]
    fac = premiere_factor(comp_fps, fps)
    by_seg = {e.seg.id: cl for cl in clips for e in (cl.events or [cl.ev])}
    out: list[dict] = []
    for ev in edit_events(cutlist):
        seg = ev.seg
        if seg is None:
            continue
        a = audio_segment(seg)
        if a is None:
            continue
        line = bool((seg.audio or {}).get("line"))
        cl = by_seg.get(seg.id)
        start, end = ev.rec_in * fac, ev.rec_out * fac
        if cl is not None and not line:
            if cl.seg.time_remap_keys and abs(seg_speed(cl.seg, comp_fps)) < 1e-9:
                continue                                      # frozen picture: silent
            prev = out[-1] if out else None
            if prev is not None and prev.get("clip") is cl and prev["end"] == start:
                prev["end"] = end                             # one V1 clip (merged pieces): one A1 clip, no cut
                prev["out"] = prev["in"] + int(round((end - prev["start"]) * cl.speed))
                continue
            n_in = cl.src_in + int(round((start - cl.rec_start) * cl.speed))
            out.append({"seg": seg, "start": start, "end": end, "in": n_in,
                        "out": n_in + int(round((end - start) * cl.speed)), "speed": cl.speed, "what": "picture",
                        "clip": cl})
            continue
        v = float(a.speed)
        tau = _raw_in_seconds(a, raw_fps) + v * float(Fraction(ev.rec_in - int(seg.comp_in)) / comp_fps)
        n_in = int(round(tau * float(fps)))
        out.append({"seg": seg, "start": start, "end": end, "in": n_in, "out": n_in + int(round((end - start) * v)),
                    "speed": v, "what": "audio line"})
    return out


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
    own_all = parse_edl_text(edl_path.read_text(encoding="utf-8"))
    own = [e for e in own_all if e["chan"] in ("V", "B")]
    own_a = [e for e in own_all if e["chan"] not in ("V", "B")]
    want_a = audio_items(cutlist) if bool(cutlist.raw.get("has_audio", True)) else []
    if len(own_a) != len(want_a):
        errors.append(f"EDL: {len(own_a)} audio events, expected {len(want_a)}")
    for e, it in zip(own_a, want_a):
        got = (_tc_to_frames(e["rec_in"], cn), _tc_to_frames(e["rec_out"], cn), _tc_to_frames(e["src_in"], rn))
        if got != (it.rec_in, it.rec_out, it.src_in):
            errors.append(f"EDL audio {_seg_label(it.seg)}: record/source {got} != {(it.rec_in, it.rec_out, it.src_in)}")
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
    res["own"] = {"events": len(own), "audio_events": len(own_a), "total_frames": items[-1]["end"] if items else 0}
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
    want_a = audio_items(cutlist) if bool(cutlist.raw.get("has_audio", True)) else []
    if want_a:
        got_a = [(it["start"], it["end"], it["in"]) for it in own["audio_items"]]
        exp_a = [(it.rec_in, it.rec_out, it.src_in) for it in want_a]
        if got_a != exp_a:
            errors.append(f"XML: audio items {got_a[:6]} != {exp_a[:6]}")
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



def _num(el: ET.Element | None) -> float | None:
    try:
        return float(el.text) if el is not None and el.text is not None else None
    except ValueError:
        return None


def _motion_of(ci: ET.Element) -> dict:
    """{'scale', 'rotation', 'center': (h, v), 'keys': {pid: [(when, value)]}} of a clipitem's Basic Motion."""
    out: dict[str, Any] = {"keys": {}}
    for eff in ci.findall("filter/effect"):
        if _text(eff, "effectid") != "basic":
            continue
        for p in eff.findall("parameter"):
            pid = _text(p, "parameterid")
            v = p.find("value")
            if v is not None:
                out[pid] = (float(_text(v, "horiz")), float(_text(v, "vert"))) if v.find("horiz") is not None \
                    else float(v.text)
            kf = []
            for k in p.findall("keyframe"):
                kv = k.find("value")
                val = (float(_text(kv, "horiz")), float(_text(kv, "vert"))) if kv.find("horiz") is not None \
                    else float(kv.text)
                kf.append((int(_text(k, "when")), val))
            if kf:
                out["keys"][pid] = kf
                out.setdefault(pid, kf[0][1])
    return out


def parse_premiere_xml(path: str | os.PathLike) -> dict:
    """Own re-parse of write_premiere_xml's file: sequence rate / size, tracks, clipitems, transitions, markers."""
    root = ET.parse(str(path)).getroot()
    seq = root.find("sequence")
    if seq is None:
        raise ValueError("no <sequence>")
    vfmt = seq.find("media/video/format/samplecharacteristics")
    out: dict[str, Any] = {
        "timebase": int(_text(seq, "rate/timebase", 0)), "ntsc": _text(seq, "rate/ntsc"),
        "duration": int(_text(seq, "duration", 0)),
        "width": int(_text(vfmt, "width", 0)) if vfmt is not None else 0,
        "height": int(_text(vfmt, "height", 0)) if vfmt is not None else 0,
        "video_tracks": len(seq.findall("media/video/track")), "audio_tracks": len(seq.findall("media/audio/track")),
        "clips": [], "transitions": [], "audio": [], "markers": []}
    vt = seq.find("media/video/track")
    for el in (list(vt) if vt is not None else []):
        if el.tag == "transitionitem":
            out["transitions"].append({"start": int(_text(el, "start")), "end": int(_text(el, "end"))})
        elif el.tag == "clipitem":
            speed = 1.0
            for eff in el.findall("filter/effect"):
                if _text(eff, "effectid") == "timeremap":
                    for p in eff.findall("parameter"):
                        if _text(p, "parameterid") == "speed":
                            speed = float(_text(p, "value")) / 100.0
                        if _text(p, "parameterid") == "reverse" and _text(p, "value") == "TRUE":
                            speed = -abs(speed)
            out["clips"].append({"name": _text(el, "name"), "start": int(_text(el, "start")), "end": int(_text(el, "end")),
                                 "in": int(_text(el, "in")), "out": int(_text(el, "out")),
                                 "timebase": int(_text(el, "rate/timebase", 0)), "ntsc": _text(el, "rate/ntsc"),
                                 "speed": speed, "motion": _motion_of(el),
                                 "flip": any(_text(e, "effectid") == "Horizontal Flip" for e in el.findall("filter/effect"))})
        elif el.tag == "generatoritem":
            out.setdefault("generators", []).append(_text(el, "name"))
    at = seq.find("media/audio/track")
    for el in (at.findall("clipitem") if at is not None else []):
        out["audio"].append({"name": _text(el, "name"), "start": int(_text(el, "start")), "end": int(_text(el, "end")),
                             "in": int(_text(el, "in")), "out": int(_text(el, "out"))})
    for mk in seq.findall("marker"):
        out["markers"].append({"name": _text(mk, "name"), "comment": _text(mk, "comment"),
                               "in": int(_text(mk, "in")), "out": int(_text(mk, "out"))})
    return out


def _sim_from_motion(scale: float, rot: float, center: tuple[float, float], W: int, H: int,
                     raw_wh: tuple[float, float]) -> Sim:
    """Inverse of the Basic Motion convention: RAW -> sequence px Sim (unflipped RAW frame; flip keeps the centre)."""
    s = scale / 100.0
    px, py = premiere_position(center, (W, H), raw_wh)
    r = math.radians(rot)
    cx, cy = raw_wh[0] / 2.0, raw_wh[1] / 2.0
    return Sim(s, rot, px - s * (math.cos(r) * cx - math.sin(r) * cy), py - s * (math.sin(r) * cx + math.cos(r) * cy))


def _inv(sim: Sim, q: tuple[float, float]) -> tuple[float, float]:
    th = math.radians(sim.theta_deg)
    dx, dy = (q[0] - sim.tx) / sim.s, (q[1] - sim.ty) / sim.s
    return math.cos(th) * dx + math.sin(th) * dy, -math.sin(th) * dx + math.cos(th) * dy


GAP_TOL_PX = 0.01                    # premiere_gaps: less than this uncovered is rounding of the written values


def premiere_gaps(xml_path: str | os.PathLike, cfg: Any = None) -> list[str]:
    """The hard coverage check of the final XML, on its own numbers only: every V1 clip's picture edges in the
    sequence as Premiere computes them -- Position = sequence centre + <center> x the clip's SOURCE size (its <file>
    width / height), half size = Scale x source size / 2, turned by Rotation -- at every Motion key, against the
    template window. Returns one line per clip that leaves any of the window uncovered (none: [])."""
    st = premiere_settings(cfg)
    W, H = st["size"]
    x0, y0, ww, wh = (float(v) for v in st["window"])
    x1, y1 = x0 + ww, y0 + wh
    root = ET.parse(str(xml_path)).getroot()
    sizes: dict[str, tuple[float, float]] = {}
    for f in root.iter("file"):
        w, h = f.findtext("media/video/samplecharacteristics/width"), f.findtext("media/video/samplecharacteristics/height")
        if w and h:
            sizes[f.get("id") or ""] = (float(w), float(h))
    seq = root.find("sequence")
    rate = int(seq.findtext("rate/timebase") or 60) if seq is not None else 60
    track = seq.find("media/video/track") if seq is not None else None
    out: list[str] = []
    for ci in (track.findall("clipitem") if track is not None else []):
        name = (ci.findtext("name") or "?").split(" ")[0]
        f = ci.find("file")
        src = sizes.get(f.get("id") if f is not None else "", None)
        if src is None:
            out.append(f"{name}: no source size in the XML, its picture edges cannot be checked")
            continue
        vals: dict[str, list] = {"scale": [100.0], "rotation": [0.0], "center": [(0.0, 0.0)]}
        for eff in ci.findall("filter/effect"):
            if eff.findtext("effectid") != "basic":
                continue
            for prm in eff.findall("parameter"):
                pid = prm.findtext("parameterid")
                if pid not in vals:
                    continue
                els = [k.find("value") for k in prm.findall("keyframe")] or [prm.find("value")]
                got = []
                for v in els:
                    if v is None:
                        continue
                    got.append((float(v.findtext("horiz")), float(v.findtext("vert"))) if v.find("horiz") is not None
                               else float(v.text))
                if got:
                    vals[pid] = got
        n = max(len(v) for v in vals.values())
        start = int(ci.findtext("start") or -1)
        start = start if start >= 0 else int(ci.findtext("end") or 0)
        tc = f"{start // (3600 * rate):02d}:{start // (60 * rate) % 60:02d}:{start // rate % 60:02d}:{start % rate:02d}"
        worst = None
        for k in range(n):
            sc = float(vals["scale"][min(k, len(vals["scale"]) - 1)]) / 100.0
            rot = math.radians(float(vals["rotation"][min(k, len(vals["rotation"]) - 1)]))
            ch, cv = vals["center"][min(k, len(vals["center"]) - 1)]
            px, py = W / 2.0 + ch * src[0], H / 2.0 + cv * src[1]        # Premiere's Position
            hw, hh = sc * src[0] / 2.0, sc * src[1] / 2.0
            c, si = math.cos(rot), math.sin(rot)
            # how far each window corner lies outside the picture, measured along the picture's own axes
            over = 0.0
            for qx, qy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                u, v = c * (qx - px) + si * (qy - py), -si * (qx - px) + c * (qy - py)
                over = max(over, abs(u) - hw, abs(v) - hh)
            if over > GAP_TOL_PX and (worst is None or over > worst[0]):
                worst = (over, px, py, sc, (px - hw, px + hw, py - hh, py + hh))
        if worst is not None:
            over, px, py, sc, (l, r, t, b) = worst
            sides = [f"x {x0:.0f}-{min(l, x1):.0f}" if l > x0 + GAP_TOL_PX else "",
                     f"x {max(r, x0):.0f}-{x1:.0f}" if r < x1 - GAP_TOL_PX else "",
                     f"y {y0:.0f}-{min(t, y1):.0f}" if t > y0 + GAP_TOL_PX else "",
                     f"y {max(b, y0):.0f}-{y1:.0f}" if b < y1 - GAP_TOL_PX else ""]
            out.append(f"{name} at {tc}: Position {px:.1f}, {py:.1f} Scale {100.0 * sc:.1f} -- picture x {l:.0f}-{r:.0f}, "
                       f"y {t:.0f}-{b:.0f}; the window is uncovered at " +
                       (", ".join(x for x in sides if x) or f"its corners (by {over:.1f} px)"))
    return out


def validate_premiere_exports(cutlist: Cutlist, xml_path: str | os.PathLike, edl_path: str | os.PathLike | None,
                              cfg: Any = None) -> dict:
    """Re-parse the Premiere XML (and the EDL, which stays at the competitor rate) and check: the sequence is exactly
    W x H at the Premiere rate (ntsc FALSE); V1 only (V2+ empty), every clip's record range = its event's range x
    the rate factor (cuts on the competitor's moments), source in / out / speed as planned; A1 cut exactly like V1
    (same record ranges, the same source in-point as the picture unless an audio line plays); Basic Motion covers
    the template window, keeps the competitor's framing (the window centre shows the RAW point the competitor's box
    centre shows) and zooms at most premiere_max_zoom; one marker per UNCERTAIN / NOT-IN-RAW spot.
    Returns {'ok', 'errors', 'warnings', 'xml', 'edl', 'total_frames', 'events'}."""
    errors: list[str] = []
    st = premiere_settings(cfg)
    events = edit_events(cutlist)
    out: dict[str, Any] = {"total_frames": int(cutlist.competitor["frames"]), "events": len(events)}
    if edl_path is not None:
        p = Path(edl_path)
        if not p.exists():
            errors.append(f"EDL: {p} does not exist")
        else:
            try:
                out["edl"] = _validate_edl(cutlist, p, events, errors)
            except Exception as e:  # noqa: BLE001 - a parse crash is a validation failure, reported
                errors.append(f"EDL: validation crashed: {type(e).__name__}: {e}")
    try:
        clips, markers, warnings = premiere_clips(cutlist, cfg)
        x = parse_premiere_xml(xml_path)
    except Exception as e:  # noqa: BLE001
        errors.append(f"XML: validation crashed: {type(e).__name__}: {e}")
        out.update(errors=errors, warnings=[], ok=False)
        return out
    fps, (W, H), win = st["fps"], st["size"], st["window"]
    fac = premiere_factor(cutlist.comp_fps, fps)
    raw_wh = (float(cutlist.raw["width"]), float(cutlist.raw["height"]))
    if fps.denominator != 1 or x["timebase"] != int(fps) or x["ntsc"] != "FALSE":
        errors.append(f"XML: sequence rate timebase {x['timebase']} ntsc {x['ntsc']} (want {fps_str(fps)} exactly, ntsc FALSE)")
    if (x["width"], x["height"]) != (W, H):
        errors.append(f"XML: sequence {x['width']}x{x['height']} (want {W}x{H})")
    if x["duration"] != out["total_frames"] * fac:
        errors.append(f"XML: sequence duration {x['duration']} (want {out['total_frames'] * fac})")
    if x["video_tracks"] != 1:
        errors.append(f"XML: {x['video_tracks']} video tracks (V1 only; V2+ must stay empty)")
    if x["audio_tracks"] > 1:
        errors.append(f"XML: {x['audio_tracks']} audio tracks (A1 only)")
    if x.get("generators"):
        errors.append(f"XML: generator items on V1: {x['generators'][:3]}")
    if len(x["clips"]) != len(clips):
        errors.append(f"XML: {len(x['clips'])} V1 clips, expected {len(clips)}")
    trans = {t["start"]: t for t in x["transitions"]}
    for got, cl in zip(x["clips"], clips):
        name = cl.label
        evs = cl.events or [cl.ev]
        if (cl.rec_start, cl.rec_end) != (evs[0].rec_in * fac, evs[-1].rec_out * fac) or \
                any(a.rec_out != b.rec_in for a, b in zip(evs, evs[1:])):
            errors.append(f"XML {name}: record range {cl.rec_start}-{cl.rec_end} is not the competitor cut x {fac}")
        s0 = got["start"] if got["start"] != -1 else (cl.rec_start if cl.rec_start in trans else None)
        if s0 != cl.rec_start or (got["start"] == -1) != (cl.start == -1):
            errors.append(f"XML {name}: start {got['start']} (want {cl.start}, record {cl.rec_start})")
        if got["end"] != cl.end:
            errors.append(f"XML {name}: end {got['end']} (want {cl.end})")
        if (got["in"], got["out"]) != (cl.src_in, cl.src_out):
            errors.append(f"XML {name}: in/out {got['in']}/{got['out']} (want {cl.src_in}/{cl.src_out})")
        if (got["timebase"], got["ntsc"]) != (int(fps), "FALSE"):
            errors.append(f"XML {name}: clip rate {got['timebase']} {got['ntsc']} (want the sequence rate)")
        if not _speed_ok(got["speed"], cl.speed):
            errors.append(f"XML {name}: speed {got['speed']:.6f} (want {cl.speed:.6f})")
        m = got["motion"]
        if not cl.keys:
            continue
        if not all(k in m for k in ("scale", "rotation", "center")):
            errors.append(f"XML {name}: Basic Motion incomplete")
            continue
        n_keys = max([len(v) for v in m["keys"].values()] or [1])
        if n_keys != len(cl.keys) and not (n_keys == 1 and not m["keys"]):
            errors.append(f"XML {name}: {n_keys} motion keys (want {len(cl.keys)})")
        if m["keys"] and [w for w, _ in m["keys"].get("scale", [])] != [w for w, _ in cl.keys]:
            errors.append(f"XML {name}: motion key times differ from the source times of the framing keys")
        vals = list(zip(m["keys"]["scale"], m["keys"]["rotation"], m["keys"]["center"])) if m["keys"] else \
            [((0, m["scale"]), (0, m["rotation"]), (0, m["center"]))]
        if st["static"]:
            # no camera movement: no keys, rotation 0, the planned fixed framing, the window covered
            if m["keys"]:
                errors.append(f"XML {name}: motion keyframes on a clip that must hold one fixed framing")
            ps = _sim_from_motion(vals[0][0][1], vals[0][1][1], vals[0][2][1], W, H, raw_wh)
            want = cl.keys[0][1]
            if abs(float(vals[0][1][1])) > 1e-9:
                errors.append(f"XML {name}: rotation {vals[0][1][1]} (want 0)")
            if abs(ps.s / want.s - 1.0) > 1e-4 or math.hypot(ps.tx - want.tx, ps.ty - want.ty) > 0.5:
                errors.append(f"XML {name}: Motion differs from the planned fixed framing")
            if not _covers(ps, raw_wh, win, tol=0.01):
                errors.append(f"XML {name}: the RAW does not cover the template window")
            continue
        box = _own_box(cl.seg) or (Box.from_dict(cutlist.layout["box"]) if (cutlist.layout or {}).get("box") else
                                   Box(0.0, 0.0, float(cutlist.competitor["width"]), float(cutlist.competitor["height"])))
        items = sorted(cl.seg.transform_keys or [], key=lambda d: float(d["comp_frame"]))
        comp_sims = [Sim.from_dict(k) for k in items] if items else [Sim.from_dict(cl.seg.transform)]
        if len(comp_sims) != len(vals):
            comp_sims = [comp_sims[0]] * len(vals)
        parsed = [_sim_from_motion(sc[1], ro[1], ce[1], W, H, raw_wh) for sc, ro, ce in vals]
        wc = (win[0] + win[2] / 2.0, win[1] + win[3] / 2.0)
        bc = (box.x + box.w / 2.0, box.y + box.h / 2.0)
        k_cover = max(win[2] / box.w, win[3] / box.h)
        for i, (ps, cs) in enumerate(zip(parsed, comp_sims)):
            a, b = _inv(ps, wc), _inv(cs, bc)
            if math.hypot(a[0] - b[0], a[1] - b[1]) > 0.5:
                errors.append(f"XML {name} key {i}: the window centre shows RAW {a[0]:.1f},{a[1]:.1f}, the competitor's "
                              f"box centre RAW {b[0]:.1f},{b[1]:.1f} (framing not kept)")
            z = ps.s / (cs.s * k_cover)
            if not (1.0 - 1e-6 <= z <= st["max_zoom"] + 1e-6):
                errors.append(f"XML {name} key {i}: {100.0 * (z - 1.0):+.2f} % beyond the competitor's framing "
                              f"(allowed 0 .. {100.0 * (st['max_zoom'] - 1.0):.0f} %)")
            if cl.covered and not _covers(ps, raw_wh, win, tol=0.01):
                errors.append(f"XML {name} key {i}: the RAW does not cover the template window")
    # --min-move: the framing changes only by min_move px or more (or to cover the window), and one continuous RAW
    # take with one framing is one clip
    changes = 0
    if st["static"] and len(x["clips"]) == len(clips):
        def fixed(c: dict) -> Sim | None:
            m = c["motion"]
            return _sim_from_motion(m["scale"], m["rotation"], m["center"], W, H, raw_wh) \
                if all(k in m for k in ("scale", "rotation", "center")) and not m["keys"] else None
        for (ga, ca), (gb, cb) in zip(zip(x["clips"], clips), zip(x["clips"][1:], clips[1:])):
            fa, fb = fixed(ga), fixed(gb)
            if fa is None or fb is None:
                continue
            mv = framing_move(fa, fb, raw_wh)
            if mv > 0.5:
                changes += 1
                if mv < st["min_move"] - 0.5 and "changed the least to cover" not in cb.framing_note:
                    errors.append(f"XML {cb.label}: the framing changes by {mv:.0f} px after {ca.label} "
                                  f"(under --min-move {st['min_move']:g})")
            elif (ga["end"] != -1 and ga["end"] == gb["start"] and gb["in"] == ga["out"]
                  and _speed_ok(gb["speed"], ga["speed"])
                  and ga["flip"] == gb["flip"] and not ca.retime and not cb.retime):
                errors.append(f"XML {ca.label} / {cb.label}: one continuous RAW take with the same framing, "
                              "but two clips")
    # the hard check: every clip's picture edges from the XML's own numbers, as Premiere reads them
    try:
        gaps = premiere_gaps(xml_path, cfg)
    except Exception as e:  # noqa: BLE001 - an unreadable XML cannot be shown to cover the window
        gaps = [f"the coverage check could not read the XML: {type(e).__name__}: {e}"]
    errors += [f"XML GAP {g}" for g in gaps]
    out["gaps"] = gaps
    # A1: same cuts as V1
    want_a = premiere_audio(cutlist, clips, cfg) if bool(cutlist.raw.get("has_audio", True)) else []
    if len(x["audio"]) != len(want_a):
        errors.append(f"XML: {len(x['audio'])} A1 clips, expected {len(want_a)}")
    ev_ranges = {(ev.rec_in * fac, ev.rec_out * fac) for ev in events} | {(cl.rec_start, cl.rec_end) for cl in clips}
    for got, it in zip(x["audio"], want_a):
        if (got["start"], got["end"], got["in"], got["out"]) != (it["start"], it["end"], it["in"], it["out"]):
            errors.append(f"XML A1 {_seg_label(it['seg'])}: {got} (want {it['start']}-{it['end']} in {it['in']})")
        if (got["start"], got["end"]) not in ev_ranges:
            errors.append(f"XML A1 {_seg_label(it['seg'])}: range {got['start']}-{got['end']} is not a V1 cut range")
        cl = next((c for c in clips if c.rec_start <= got["start"] and got["end"] <= c.rec_end), None)
        if it["what"] == "picture" and cl is not None and \
                got["in"] != cl.src_in + int(round((got["start"] - cl.rec_start) * cl.speed)):
            errors.append(f"XML A1 {_seg_label(it['seg'])}: source in {got['in']} is not V1's at that point")
    # markers: every UNCERTAIN / NOT-IN-RAW spot
    have = {(m["in"], m["out"]) for m in x["markers"]}
    for ev in events:
        if ev.seg is not None and ev.kind != "clip" and ev.seg.type in ("uncertain", "not_in_raw"):
            if (ev.rec_in * fac, ev.rec_out * fac) not in have:
                errors.append(f"XML: no marker on {ev.seg_name} ({ev.seg.type}, {ev.rec_in * fac}-{ev.rec_out * fac})")
    out["xml"] = {"clips": len(x["clips"]), "audio": len(x["audio"]), "markers": len(x["markers"]),
                  "rate": f"{x['timebase']} ntsc {x['ntsc']}", "size": f"{x['width']}x{x['height']}",
                  "in_exact": sum(1 for c in clips if c.in_exact), "zoomed": sum(1 for c in clips if c.zoom > 1.0),
                  "not_covered": [c.label for c in clips if not c.covered],
                  "framing_changes": changes, "merged": sum(len(c.events) - 1 for c in clips if c.events),
                  "framing_kept": sum(1 for c in clips if c.framing_note.startswith("framing kept")),
                  "face_centred": sum(1 for c in clips if "face-centred" in c.framing_note), "min_move": st["min_move"]}
    out["warnings"] = list(warnings) + [f"{ev.seg_name}: {w}" for ev in events for w in ev.warnings]
    out["errors"] = errors
    out["ok"] = not errors
    if errors:
        log.warning("premiere export validation: %d problem(s): %s", len(errors), "; ".join(errors[:5]))
    return out
