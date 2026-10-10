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

import bisect
import csv
import dataclasses
import math
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Sequence

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


def xml_in_out(src_in: int, src_out: int) -> tuple[int, int]:
    """<in> / <out> of a clip item from the plan's source frames. The plan counts a reversed clip from the frame it
    shows first (``src_in``) down to one before the last (``src_out`` < ``src_in``); FCP7 XML and Premiere want the
    source range ascending, in < out, and play it backwards from out - 1 for the Time Remap ``reverse`` flag -- the
    same frames. An item written with in > out is skipped by Premiere ("invalid start/end")."""
    return (int(src_out) + 1, int(src_in) + 1) if src_out < src_in else (int(src_in), int(src_out))


def plan_in_out(xml_in: int, xml_out: int, reverse: bool) -> tuple[int, int]:
    """The inverse of :func:`xml_in_out`: the plan's (src_in, src_out) of an item read from the XML."""
    return (int(xml_out) - 1, int(xml_in) - 1) if reverse else (int(xml_in), int(xml_out))


# A speed-changed clip item (Time Remap) counts its <in> / <out>, <duration> and keyframe <when> on the RETIMED clip
# (FCP7, as Premiere imports it): <out> - <in> is its length on the timeline and Premiere plays source time <in> x
# speed. The plan keeps source time (1/fps ticks); these map it onto that scale and back. Identity at 100 %. Written
# as source time, a 125 % clip of output/020 (23.976 fps RAW) showed RAW 629 s instead of 503 s in Premiere.

def _remap_factor(speed: float) -> float:
    """|speed| as the Time Remap filter states it (percent to 4 decimals: _time_remap) -- the factor Premiere uses."""
    s = round(abs(float(speed)) * 100.0, 4) / 100.0
    return 1.0 if s < 1e-9 or abs(s - 1.0) < 1e-9 else s


def remap_encode(lo: int, hi: int, speed: float, length: int | None = None) -> tuple[int, int]:
    """An ascending SOURCE range [lo, hi) (1/fps ticks) as the <in> / <out> of a clip item at ``speed``; ``length``:
    the item's length on the timeline (<end> - <start>, when both are real) -- <out> - <in> is exactly that."""
    s = _remap_factor(speed)
    if s == 1.0:
        return int(lo), int(hi)
    x_lo = int(round(lo / s))
    return x_lo, x_lo + (int(length) if length is not None else int(round((hi - lo) / s)))


def remap_decode(x_lo: int, x_hi: int, speed: float) -> tuple[int, int]:
    """The inverse of :func:`remap_encode`: the SOURCE range of a clip item's <in> / <out> (rounded to ticks)."""
    s = _remap_factor(speed)
    if s == 1.0:
        return int(x_lo), int(x_hi)
    lo = int(round(x_lo * s))
    return lo, lo + int(round((x_hi - x_lo) * s))


def remap_when(when: int, lo: int, speed: float, decode: bool = False) -> int:
    """A keyframe <when> of a clip item whose ascending source range starts at ``lo`` (source ticks): encoded onto
    the retimed clip like its <in> (``decode``: back to source ticks, ``lo`` then the item's decoded source start)."""
    s = _remap_factor(speed)
    if s == 1.0:
        return int(when)
    x_lo = int(round(lo / s))
    if decode:
        return lo + int(round((int(when) - x_lo) * s))
    return x_lo + int(round((int(when) - lo) / s))


def remap_duration(frames: int, speed: float) -> int:
    """A clip item's <duration>: the media's length on the retimed clip."""
    return int(math.floor(int(frames) / _remap_factor(speed)))


def item_length(start: int, end: int) -> int | None:
    """A clip item's length on the timeline, None inside a transition (<start> or <end> -1)."""
    return int(end) - int(start) if int(start) >= 0 and int(end) >= 0 else None


def xml_roundtrip(src_in: int, src_out: int, speed: float, length: int | None = None) -> tuple[int, int]:
    """The plan's (src_in, src_out) as the XML gives it back (parse_premiere_xml): exact at 100 %, within a tick at
    another speed (the retimed scale rounds)."""
    lo, hi = xml_in_out(src_in, src_out)
    return plan_in_out(*remap_decode(*remap_encode(lo, hi, speed, length), speed), float(speed) < 0)


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
    """The media's file URL. A Windows network path (\\\\server\\share\\..., e.g. a RAW over large_file_bytes left on
    a NAS) keeps its server as the URL's host (RFC 8089: file://server/share/...) -- as file://localhost/server/...
    it would be a folder of the current drive, and the clip offline; a long-path prefix (\\\\?\\) is dropped."""
    if not path:
        return ""
    s = str(path)
    if s.startswith("\\\\?\\UNC\\"):
        s = "\\\\" + s[8:]
    elif s.startswith("\\\\?\\"):
        s = s[4:]
    if s.startswith(("\\\\", "//")):
        host, _, rest = s.lstrip("\\/").replace("\\", "/").partition("/")
        return f"file://{host}/" + urllib.parse.quote(rest)
    p = Path(s).as_posix()
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


def _flop(clipitem: ET.Element) -> ET.Element:
    """A horizontal mirror on a clip item: FCP7's Flop filter (Perspective), direction Horizontal -- the FCP effect
    Premiere's XML import translates to its Horizontal Flip (and its XML export writes for one). Premiere does not
    translate an FCP effect named "Horizontal Flip": output/020 lost its mirroring that way."""
    e = _effect(_sub(clipitem, "filter"), "Flop", "Flop", "Perspective", "filter")
    p = _sub(e, "parameter")
    _sub(p, "parameterid", "direction")
    _sub(p, "name", "Direction")
    _sub(p, "valuemin", 1)
    _sub(p, "valuemax", 3)
    vl = _sub(p, "valuelist")
    for n, v in (("Horizontal", 1), ("Vertical", 2), ("Both", 3)):
        ve = _sub(vl, "valueentry")
        _sub(ve, "name", n)
        _sub(ve, "value", v)
    _sub(p, "value", 1)
    return e


def _is_flip(effect: ET.Element) -> bool:
    """Whether a filter effect mirrors its clip horizontally: Flop (direction Horizontal or Both), or an older
    export's "Horizontal Flip"."""
    eid = (effect.findtext("effectid") or effect.findtext("name") or "").strip()
    if eid == "Horizontal Flip":
        return True
    if eid.lower() != "flop":
        return False
    v = next((p.findtext("value") for p in effect.findall("parameter")), None)
    return v is None or str(v).strip() in ("1", "3")


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
                  box: Box | None, raw_wh: tuple[int, int], comp_fps: Fraction, raw_fps: Fraction,
                  speed: float = 1.0, src_lo: int = 0) -> None:
    """Basic Motion (+ Crop) filters of one RAW clipitem (``speed`` / ``src_lo``: its Time Remap speed and ascending
    source start, for keyframe times on the retimed clip: remap_when)."""
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
        whens = [remap_when(int(math.floor(float(raw_fps) * _raw_seconds_cont(seg, k, comp_fps, raw_fps) + _AE_EPS)),
                            src_lo, speed) for k, _ in sims]
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
    Flop filter (Horizontal: _flop) for flip_h, Crop to the competitor box in match mode (constant, unrotated
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
            _sub(ci, "duration", remap_duration(raw_frames, ev.speed))
            _rate_el(ci, raw_fps)
            _sub(ci, "start", start)
            _sub(ci, "end", end)
            src_out = int(ev.src_in) + _src_advance(ev.speed, ev.n_rec + tail, raw_fps, comp_fps)
            x_in, x_out = remap_encode(*xml_in_out(int(ev.src_in), src_out), ev.speed)
            _sub(ci, "in", x_in)
            _sub(ci, "out", x_out)
            _sub(ci, "alphatype", "none")
            _sub(ci, "pixelaspectratio", "square")
            _sub(ci, "anamorphic", "FALSE")
            _file_el(ci, "file-raw", defined, raw_name, raw_abs, raw_fps, raw_frames, raw_w, raw_h, audio_info)
            if abs(ev.speed - 1.0) > 1e-9:
                _time_remap(ci, ev.speed)
            seg_box = _own_box(seg) or layout_box            # crop: the segment's own box (D1) or the layout box
            _basic_motion(ci, seg, mode, W, H, fill_box, seg_box, (raw_w, raw_h), comp_fps, raw_fps, ev.speed,
                          xml_in_out(int(ev.src_in), src_out)[0])
            if seg.flip_h and mode != "source":
                _flop(ci)
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
            _sub(ai, "duration", remap_duration(raw_frames, it.speed))
            _rate_el(ai, raw_fps)
            _sub(ai, "start", it.rec_in)
            _sub(ai, "end", it.rec_out)
            x_in, x_out = remap_encode(*xml_in_out(int(it.src_in), int(it.src_in) +
                                                   _src_advance(it.speed, it.n_rec, raw_fps, comp_fps)), it.speed)
            _sub(ai, "in", x_in)
            _sub(ai, "out", x_out)
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
            _sub(ai, "duration", remap_duration(raw_frames, ev.speed))
            _rate_el(ai, raw_fps)
            _sub(ai, "start", ev.rec_in)
            _sub(ai, "end", ev.rec_out)
            x_in, x_out = remap_encode(*xml_in_out(int(ev.src_in), int(ev.src_in) +
                                                   _src_advance(ev.speed, ev.n_rec, raw_fps, comp_fps)), ev.speed)
            _sub(ai, "in", x_in)
            _sub(ai, "out", x_out)
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
# The sequence (default 1080x1920 at exactly 60/1, ntsc FALSE; an NTSC competitor 60000/1001, ntsc TRUE: sequence_fps)
# carries the edit on V1 and the RAW audio on A1;
# V2 and above stay empty for the user's template and captions. Every competitor frame k becomes sequence frames
# [f k, f (k + 1)) with f = sequence fps / competitor fps (must be an integer: 60 / 30 = 2), so every cut lands on
# the same moment as in the competitor-rate plan. Clipitem <rate> = the sequence rate (Premiere's own convention):
# <in> / <out> / keyframe <when> count SOURCE time in sequence-rate frames (1/60 s), <start> / <end> sequence frames
# -- at 100 %. A speed-changed clip counts them on the RETIMED clip (remap_encode: out - in = end - start, Premiere plays
# source <in> x speed); the plan (PremiereClip) keeps source time and only the writer / readers convert.
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
# Source in-point: <in> is a whole 1/60 s (at another speed a whole tick of the retimed clip: speed x 1/60 s of
# source). The tool's plan allows any raw_in inside the segment's frame-exact interval (floor rule at the competitor
# rate), so <in> is the tick inside that interval nearest the plan's raw_in; when no tick lies inside (the interval
# can be narrower than a tick) it is the nearest tick and the clip is listed (some competitor-rate frames may show a neighbouring RAW frame). Between two competitor frames the second
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
    src_in: int                      # SOURCE time at the cut, in sequence-rate frames (<in> at 100 %: remap_encode)
    src_out: int
    speed: float
    in_exact: bool                   # <in> lies inside the segment's frame-exact interval
    in_error_ms: float               # the source second Premiere starts at minus the plan's exact RAW time (ms)
    zoom: float                      # extra zoom about the window centre (1 = the competitor's framing exactly)
    covered: bool                    # the RAW covers the template window on every key and between keys
    keys: list[tuple[int, Sim]]      # (<when> in source sequence-rate frames, Sim RAW -> sequence px)
    retime: str | None               # why the clip's speed is not the segment's real time map (marker text)
    events: list[EditEvent] = field(default_factory=list)   # the edit events it plays (several: merged, --min-move)
    framing_note: str = ""           # --min-move: why the framing is not this clip's own (kept from the clip before)
    link_split: bool = False         # a piece of one take split so each V1 clip links to one A1 clip (link_pairs)
    person_note: str = ""            # re-framed to show the person speaking (speakers.py): what moved and why
    person_span: tuple[int, int] | None = None   # the source range it plays in the final edit (sequence-rate frames):
                                                 # the speech-safe cuts may extend it (premiere_clips(silence=))
    dissolve_frames: int = -1        # its cross dissolve's length (sequence frames); -1: the event's (ev.dissolve_in)

    @property
    def label(self) -> str:
        evs = self.events or [self.ev]
        return "+".join(_seg_label(e.seg) for e in evs) if len(evs) > 1 else _seg_label(self.seg)


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def _split_clip(cl: PremiereClip, at: int) -> tuple[PremiereClip, PremiereClip]:
    """A V1 clip as two seamless pieces at sequence frame ``at`` (the source runs on; framing keys are in source
    time, so both keep them)."""
    mid = cl.src_in + int(round((at - cl.rec_start) * cl.speed))
    return (dataclasses.replace(cl, end=at, rec_end=at, src_out=mid, link_split=True),
            dataclasses.replace(cl, start=at, rec_start=at, src_in=mid, link_split=True))


def retime_edit(clips: Sequence[PremiereClip], audio: Sequence[dict], markers: Sequence[dict], n_frames: int,
                k: float, fac: int) -> tuple[list[PremiereClip], list[dict], list[dict], int]:
    """--speed: the edit made at 100 % (every cut off speech, silences and repeats out) played ``k`` times as fast --
    every V1 clip and A1 item at speed x k, every cut, dissolve and marker at frame x / k (rounded: two clips meeting at
    a cut keep meeting), the source each plays unchanged. Each item's source in-point is taken on the 100 % timeline at
    its new first frame, and lands on the grid Premiere starts a retimed clip on (<in> counts the RETIMED clip:
    remap_encode); a piece continuing the piece before in one take (a link split, a scene cut) starts exactly where that
    one's retimed source ends -- no tick repeated or skipped at its cut. Returns (clips, audio, markers, frames)."""
    def at(x: int) -> int:
        return int(round(int(x) / float(k)))

    def chain(items: list, get, put) -> None:
        # items: [(old, new)] in timeline order; get(item) -> (rec_start, rec_end, src_in, speed); put sets src_in
        prev = None
        for old, new in items:
            r0, r1, s_in, v = get(old)
            if prev is not None:
                p_old, p_new = prev
                q0, q1, q_in, q_v = get(p_old)
                if q1 == r0 and abs(q_v - v) < 1e-9 and int(q_in + round((q1 - q0) * q_v)) == int(s_in):
                    n0, n1, n_in, n_v = get(p_new)
                    s = _remap_factor(n_v)
                    x = remap_encode(*xml_in_out(n_in, n_in + int(round((n1 - n0) * n_v))), n_v, n1 - n0)
                    put(new, _source_tick(x[0] + (n1 - n0), s) if n_v > 0 else
                        n_in + int(round((n1 - n0) * n_v)))
            prev = (old, new)
    out_c: list[PremiereClip] = []
    for cl in clips:
        a, b = at(cl.rec_start), at(cl.rec_end)
        b = max(b, a + 1)
        v = float(cl.speed) * float(k)
        s_in = cl.src_in + int(round((a * k - cl.rec_start) * float(cl.speed)))
        s_out = cl.src_in + int(round((b * k - cl.rec_start) * float(cl.speed)))
        d = -1
        if cl.start == -1:
            d0 = cl.dissolve_frames if cl.dissolve_frames >= 0 else int(getattr(cl.ev, "dissolve_in", 0) or 0) * fac
            d = max(1, at(cl.rec_start + d0) - a)
        out_c.append(dataclasses.replace(cl, start=-1 if cl.start == -1 else a, end=-1 if cl.end == -1 else b,
                                         rec_start=a, rec_end=b, src_in=s_in, src_out=s_out, speed=v,
                                         dissolve_frames=d))

    def put_clip(c: PremiereClip, n_in: int) -> None:
        c.src_out = n_in + (c.src_out - c.src_in)
        c.src_in = n_in
    chain(list(zip(clips, out_c)), lambda c: (c.rec_start, c.rec_end, c.src_in, float(c.speed)), put_clip)
    out_a: list[dict] = []
    for it in audio:
        a, b = at(it["start"]), at(it["end"])
        b = max(b, a + 1)
        v = float(it["speed"])
        n_in = int(it["in"]) + int(round((a * k - int(it["start"])) * v))
        n_out = int(it["in"]) + int(round((b * k - int(it["start"])) * v))
        out_a.append(dict(it, start=a, end=b, speed=v * float(k), **{"in": n_in, "out": n_out}))

    def put_audio(d: dict, n_in: int) -> None:
        d["out"] = n_in + (d["out"] - d["in"])
        d["in"] = n_in
    chain(list(zip(audio, out_a)), lambda d: (int(d["start"]), int(d["end"]), int(d["in"]), float(d["speed"])),
          put_audio)
    out_m = [dict(m, **{"in": at(m["in"]), "out": (at(m["out"]) if int(m["out"]) >= 0 else m["out"])})
             for m in markers]
    return out_c, out_a, out_m, at(n_frames)


def _split_audio(it: dict, at: int) -> tuple[dict, dict]:
    """An A1 item as two seamless pieces at sequence frame ``at`` (the source runs on: no fade, no click)."""
    mid = it["in"] + int(round((at - it["start"]) * float(it["speed"])))
    return (dict(it, end=at, out=mid, fade_out=False, piece=True),
            dict(it, start=at, **{"in": mid}, fade_in=False, piece=True))


def _main_audio(clips: Sequence[PremiereClip], audio: Sequence[dict]) -> dict[int, int]:
    """{V1 clip: the A1 item it overlaps most (the earlier on a tie)} for every V1 clip with audio under it."""
    out = {}
    for vi, cl in enumerate(clips):
        best = max(((_overlap(cl.rec_start, cl.rec_end, it["start"], it["end"]), -ai) for ai, it in enumerate(audio)),
                   default=(0, 0))
        if best[0] > 0:
            out[vi] = -best[1]
    return out


def link_pairs(clips: Sequence[PremiereClip], audio: Sequence[dict]
               ) -> tuple[list[PremiereClip], list[dict], list[tuple[int, int]]]:
    """V1 clips and A1 items as linked pairs (Premiere's linked clips: move, trim or cut one and its audio goes with
    it): every V1 clip with the A1 item it overlaps most, every A1 item with one V1 clip. An A1 item several V1 clips
    play over (one take of audio while the picture cuts: a framing change, retimed repeats) is split at their cuts
    into seamless pieces (the source runs on: nothing is heard); an A1 item no V1 clip claims (an audio cut inside a
    V1 clip) splits that V1 clip at its edge (the picture runs on). Audio shifted a little from its own picture (an
    A1 cut a few frames from V1's, to close a jump) stays linked to that picture. Returns (V1 clips, A1 items,
    [(V1 index, A1 index)]): a V1 clip with no audio under it (a freeze, muted B-roll) and an A1 item under an empty
    V1 are left unlinked."""
    clips, audio = list(clips), [dict(it) for it in audio]
    for _ in range(len(clips) + len(audio) + 1):            # A1 cuts inside a V1 clip: split it there
        claimed = set(_main_audio(clips, audio).values())
        orphan = next((it for ai, it in enumerate(audio) if ai not in claimed and any(
            _overlap(c.rec_start, c.rec_end, it["start"], it["end"]) for c in clips)), None)
        if orphan is None:
            break
        vi = max(range(len(clips)), key=lambda v: _overlap(clips[v].rec_start, clips[v].rec_end, orphan["start"],
                                                           orphan["end"]))
        cl = clips[vi]
        cuts = sorted(x for x in (orphan["start"], orphan["end"]) if cl.rec_start < x < cl.rec_end)
        if not cuts:
            break
        pieces, rest = [], cl
        for x in cuts:
            a, rest = _split_clip(rest, x)
            pieces.append(a)
        clips[vi:vi + 1] = pieces + [rest]
    main = _main_audio(clips, audio)
    by_a: dict[int, list[int]] = {}
    for vi, ai in main.items():
        by_a.setdefault(ai, []).append(vi)
    out_a: list[dict] = []
    pairs: list[tuple[int, int]] = []
    for ai, it in enumerate(audio):
        vs = sorted(by_a.get(ai, []), key=lambda v: clips[v].rec_start)
        pieces, rest = [], it
        for v in vs[1:]:                                    # one take under several V1 clips: a piece for each
            if rest["start"] < clips[v].rec_start < rest["end"]:
                a, rest = _split_audio(rest, clips[v].rec_start)
                pieces.append(a)
        pieces.append(rest)
        if len(pieces) == len(vs):
            for piece, v in zip(pieces, vs):
                pairs.append((v, len(out_a)))
                out_a.append(piece)
        else:                                               # not one piece per clip: link the take to its main clip
            if vs:
                pairs.append((max(vs, key=lambda v: _overlap(clips[v].rec_start, clips[v].rec_end, it["start"],
                                                             it["end"])), len(out_a)))
            out_a.append(it)
    return clips, out_a, sorted(pairs)


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
    min_clip = int(getattr(cfg, "premiere_min_clip_frames", MIN_CLIP_FRAMES)
                   if getattr(cfg, "premiere_min_clip_frames", None) is not None else MIN_CLIP_FRAMES)
    k = float(getattr(cfg, "premiere_speed", 1.0) or 1.0)
    if k > 1.0 + 1e-9 and min_clip > 0:            # --speed: no clip under min_clip frames once it plays k x as fast
        min_clip = int(math.ceil(min_clip * k - 1e-9))
    return {"size": (W, H), "fps": fps, "window": win,
            "max_zoom": float(getattr(cfg, "premiere_max_zoom", None) or 1.05),
            "static": True if static is None else bool(static),
            # --min-move counts px of the 1080x1920 sequence: the same move on a 2160x3840 one (--frame) is twice the px
            "min_move": (250.0 if move is None else max(0.0, float(move))) * W / 1080.0,
            "scene_cuts": bool(getattr(cfg, "premiere_scene_cuts", True)),
            "normal_audio": bool(getattr(cfg, "premiere_normal_audio", True)),
            "min_clip": min_clip}


def unmirror(cl: Cutlist) -> tuple[Cutlist, int]:
    """The Premiere edit without the competitor's mirror (night 3; --mirror keeps it): your finished edits of the two
    mirrored competitors (020, laptop004) have no clip flipped. Each mirrored segment shows the same part of the RAW,
    the right way round: its framing (RAW -> competitor px, of the mirrored RAW) mirrored about its box's centre --
    p' = s R(-theta) p + (2 c - s cos(theta) W - tx, ty + s sin(theta) W). Returns (cut list, segments unmirrored)."""
    import copy
    W = float(cl.raw["width"])
    Wc, Hc = float(cl.competitor["width"]), float(cl.competitor["height"])
    layout_box = Box.from_dict(cl.layout["box"]) if (cl.layout or {}).get("box") else Box(0.0, 0.0, Wc, Hc)
    segs, n = [], 0
    for s in cl.segments:
        if not s.flip_h:
            segs.append(s)
            continue
        box = _own_box(s) or layout_box
        c = box.x + box.w / 2.0

        def mirror(d: dict) -> dict:
            sim = Sim.from_dict(d)
            th = math.radians(sim.theta_deg)
            m = Sim(sim.s, -sim.theta_deg, 2.0 * c - sim.s * math.cos(th) * W - sim.tx, sim.ty + sim.s * math.sin(th) * W)
            return dict(d, **m.to_dict())
        t = copy.deepcopy(s)
        if t.transform:
            t.transform = mirror(t.transform)
        if t.transform_keys:
            t.transform_keys = [mirror(k) for k in t.transform_keys]
        t.flip_h = False
        segs.append(t)
        n += 1
    if not n:
        return cl, 0
    return dataclasses.replace(cl, segments=segs), n


RETIME_SLIVER_S = 0.25      # --premiere: a speed change this short inside one take plays at 100 % (the take runs on)
SHOT_HOLD_ZOOM = 0.05       # --min-move across a RAW shot change: held when the new shot's own framing is this alike in
#                             zoom (and under --min-move away, and the framing still shows the clip before's person)


def _line_interval(p_: Segment, k: int, cf: float) -> list[float] | None:
    """The frame-exact raw_in interval of the line of clip ``p_`` at competitor frame ``k`` (its own interval moved
    along its line): a sliver played on that line is placed exactly like the take (_pick_in_tick)."""
    iv = p_.raw_in_interval
    if not iv or len(iv) != 2 or p_.time_remap_keys:
        return None
    d = float(p_.speed) * (int(k) - int(p_.comp_in)) / cf
    return [float(iv[0]) + d, float(iv[1]) + d]


def play_on_slivers(cl: Cutlist) -> tuple[Cutlist, list[dict]]:
    """--premiere: the competitor's speed changes of at most RETIME_SLIVER_S inside one continuous take -- a RAW
    segment at 100 % on each side and the RAW running on through it (within two RAW frames) -- play at 100 % in the
    Premiere edit: a slow-motion or hold sliver this short is a frame-rate artifact or a hold, not an edit (video2's
    competitor: 4 frames at 50 % in the middle of "definitely", the take running on under them; the finished edit
    plays the take on). When the clip after continues the clip before's line, the sliver plays on that line; when the
    sliver's own RAW runs on into the next clip, it plays from where it starts (repeats.py takes out what the next
    one would show twice). Either way the take runs on as one, and its sound is never slowed inside a word.
    A sliver at 100 % inside one take whose picture is off the take's line by at most speech.MAX_JUMP_S (a picture
    glitch: video4's competitor shows 6 frames 84 ms ahead in the middle of "each other's Spidey") plays on the line
    too -- its sound already does (speech.py), and a picture jumping ahead and back would be a repeat to cut out
    inside the word. The faithful cut list (the checks against the competitor) keeps them. Returns (cut list,
    changes)."""
    import copy
    rf, cf = float(Fraction(cl.raw_fps)), float(Fraction(cl.comp_fps))
    segs = sorted(cl.segments, key=lambda s: (int(s.comp_in), int(s.id)))

    def plain(s: Segment) -> bool:
        return (s.type == "raw" and s.raw_in_seconds is not None and not s.time_remap_keys
                and abs(float(s.speed) - 1.0) < 1e-6)

    def own_sound_line(s: Segment, p_end: float) -> bool:
        # the competitor's sound under it follows a line of its own, off the take's (more than half a RAW frame from
        # where the clip before ends): left as the competitor has it -- played on, its picture would no longer cut
        # there and that line could only be shifted onto the take's by whole frames, ms off inside the word
        # (Zendaya's S19: 6 frames at 120 %, its sound 91 ms behind). A sound that continues the take (video4's
        # glitch: "S01 continued") is no line of its own.
        ln = (s.audio or {}).get("line") or {}
        r = ln.get("raw_in_seconds")
        return r is not None and abs(float(r) - p_end) > 0.5 / rf
    from .speech import MAX_JUMP_S
    out, done = [], []
    for i, s in enumerate(segs):
        if (0 < i < len(segs) - 1 and plain(s) and plain(segs[i - 1]) and plain(segs[i + 1])
                and (int(s.comp_out) - int(s.comp_in)) / cf <= RETIME_SLIVER_S + 1e-9):
            p_, n_ = segs[i - 1], segs[i + 1]
            p_end = float(p_.raw_in_seconds) + (int(p_.comp_out) - int(p_.comp_in)) / cf
            line = float(p_.raw_in_seconds) + (int(n_.comp_in) - int(p_.comp_in)) / cf
            off = abs(float(s.raw_in_seconds) - p_end)
            if (abs(line - float(n_.raw_in_seconds)) <= 2.0 / rf and 2.0 / rf < off <= MAX_JUMP_S + 1e-9
                    and not own_sound_line(s, p_end)):
                s2 = copy.deepcopy(s)
                s2.raw_in_seconds = round(p_end, 9)
                s2.raw_in_frame = int(math.floor(p_end * rf + 1e-6))
                s2.raw_in_interval = _line_interval(p_, int(s.comp_in), cf)
                out.append(s2)
                done.append({"segment": int(s.id), "comp_in": int(s.comp_in), "comp_out": int(s.comp_out),
                             "speed": 1.0, "jump_s": round(off, 4)})
                continue
        if (0 < i < len(segs) - 1 and s.type == "raw" and s.raw_in_seconds is not None and not s.time_remap_keys
                and abs(float(s.speed) - 1.0) > 1e-6 and float(s.speed) > 0
                and (int(s.comp_out) - int(s.comp_in)) / cf <= RETIME_SLIVER_S + 1e-9
                and plain(segs[i - 1]) and plain(segs[i + 1])):
            p_, n_ = segs[i - 1], segs[i + 1]
            p_end = float(p_.raw_in_seconds) + (int(p_.comp_out) - int(p_.comp_in)) / cf
            s_end = float(s.raw_in_seconds) + float(s.speed) * (int(s.comp_out) - int(s.comp_in)) / cf
            line = float(p_.raw_in_seconds) + (int(n_.comp_in) - int(p_.comp_in)) / cf
            on_line = abs(line - float(n_.raw_in_seconds)) <= 2.0 / rf     # the take runs on under it (a stall)
            if own_sound_line(s, p_end):
                out.append(s)
                continue
            runs_on = abs(p_end - float(s.raw_in_seconds)) <= 2.0 / rf and abs(s_end - float(n_.raw_in_seconds)) <= 2.0 / rf
            if on_line or runs_on:
                s2 = copy.deepcopy(s)
                s2.speed, s2.speed_measured, s2.retime = 1.0, 1.0, "none"
                if on_line:
                    s2.raw_in_seconds = round(p_end, 9)
                    s2.raw_in_frame = int(math.floor(p_end * rf + 1e-6))
                    s2.raw_in_interval = _line_interval(p_, int(s.comp_in), cf)
                out.append(s2)
                done.append({"segment": int(s.id), "comp_in": int(s.comp_in), "comp_out": int(s.comp_out),
                             "speed": round(float(s.speed), 4)})
                continue
        out.append(s)
    if not done:
        return cl, []
    cl2 = copy.copy(cl)
    cl2.segments = out
    return cl2, done


HARD_DISSOLVE_S = 0.1       # --premiere: a cross dissolve this short whose cut is inside speech becomes a hard cut


def harden_dissolves(cl: Cutlist, inside: Callable[[float], bool]) -> tuple[Cutlist, list[dict]]:
    """--premiere: a cross dissolve of at most HARD_DISSOLVE_S between two RAW clips whose cut lands inside speech
    (``inside(raw second)`` on either side, at the dissolve's middle frame) becomes a hard cut there, so the
    speech-safe cuts can move it out of the word like any cut: a dissolve is locked in place, and its sound may only
    slide inside its own frames (speech._slide_dissolves) -- video1's competitor cuts with 2-frame dissolves inside
    "tippex tippex" and "breaking even". A 2-frame dissolve is 33 ms of the two pictures mixed: nothing is lost. The
    faithful cut list (the checks against the competitor) keeps the dissolve. Returns (cut list, changes)."""
    import copy
    cf = float(Fraction(cl.comp_fps))
    rf = float(Fraction(cl.raw_fps))
    segs = sorted(cl.segments, key=lambda s: (int(s.comp_in), int(s.id)))
    out = [copy.copy(s) for s in segs]
    done = []

    def raw_at(s: Segment, k: int) -> float:
        return float(s.raw_in_seconds) + float(s.speed) * (k - int(s.comp_in)) / cf

    for i in range(len(out) - 1):
        p_, q_ = out[i], out[i + 1]
        tr = (p_.transition_out or {}) if (p_.transition_out or {}).get("type") == "crossfade" else \
            (q_.transition_in or {}) if (q_.transition_in or {}).get("type") == "crossfade" else None
        if not tr or int(p_.comp_out) <= int(q_.comp_in):
            continue
        if any(x.type != "raw" or x.raw_in_seconds is None or x.time_remap_keys or float(x.speed) <= 0
               for x in (p_, q_)):
            continue
        d = int(p_.comp_out) - int(q_.comp_in)
        if d / cf > HARD_DISSOLVE_S + 1e-9:
            continue
        mid = int(q_.comp_in) + d // 2
        if not (inside(raw_at(p_, mid)) or inside(raw_at(q_, mid))):
            continue
        p2, q2 = copy.deepcopy(p_), copy.deepcopy(q_)
        shift = float(q2.speed) * (mid - int(q2.comp_in)) / cf
        q2.raw_in_seconds = round(float(q2.raw_in_seconds) + shift, 9)
        q2.raw_in_frame = int(math.floor(float(q2.raw_in_seconds) * rf + 1e-6))
        if q2.raw_in_interval and len(q2.raw_in_interval) == 2:
            q2.raw_in_interval = [float(q2.raw_in_interval[0]) + shift, float(q2.raw_in_interval[1]) + shift]
        p2.comp_out, q2.comp_in = mid, mid
        p2.transition_out, q2.transition_in = None, None
        out[i], out[i + 1] = p2, q2
        done.append({"cut": mid, "segments": [int(p2.id), int(q2.id)], "frames": d,
                     "raw": [round(raw_at(p_, mid), 3), round(raw_at(q_, mid), 3)]})
    if not done:
        return cl, []
    cl2 = copy.copy(cl)
    cl2.segments = out
    return cl2, done


def sequence_fps(comp_fps: Fraction, want: Fraction) -> Fraction:
    """The Premiere sequence rate for a competitor: ``want`` when a competitor frame is a whole number of its frames,
    else the whole multiple of the competitor's rate nearest to it (the lower one on a tie): a 24 fps competitor in a
    48 fps sequence, 25 fps in 50 -- every cut on a competitor frame. An NTSC competitor takes the NTSC version of
    what its whole rate would take: 29.97 fps a 59.94 fps sequence (ntsc TRUE), 23.976 fps 47.952 -- a 60.00 fps
    sequence cannot place its frames (2.002 sequence frames each). A rate that is neither keeps ``want``
    (premiere_factor then says why it cannot be placed)."""
    c, w = Fraction(comp_fps), Fraction(want)
    if c <= 0:
        return w
    ntsc = c.denominator == 1001 and (c * Fraction(1001, 1000)).denominator == 1
    base = c * Fraction(1001, 1000) if ntsc else c                 # 29.97 -> 30 (whole frames per second)
    if base.denominator != 1:
        return w
    if (w / base).denominator == 1 and w >= base:
        k = int(w / base)
    else:
        lo = max(1, int(w // base))
        hi = lo + 1
        k = lo if abs(lo * base - w) <= abs(hi * base - w) else hi
    return k * c


def xml_rate(fps: Fraction) -> tuple[int, str] | None:
    """(<timebase>, <ntsc>) of a rate in FCP7 XML: a whole rate, or an NTSC one (x 1000/1001, ntsc TRUE); None for a
    rate the format cannot state."""
    f = Fraction(fps)
    if f.denominator == 1:
        return int(f), "FALSE"
    if f.denominator == 1001 and (f * Fraction(1001, 1000)).denominator == 1:
        return int(f * Fraction(1001, 1000)), "TRUE"
    return None


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


def _clip_raw_s(cl: PremiereClip, fps: Fraction) -> tuple[float, float]:
    """(first, last) RAW second the clip shows, in playing order."""
    f = float(fps)
    return cl.src_in / f, cl.src_out / f


def _last_raw_s(cl: PremiereClip, fps: Fraction) -> float:
    """The RAW second of the last frame the clip shows (its end is exclusive): what the next clip's shot is compared
    with."""
    return cl.src_out / float(fps) - 0.5 * max(abs(float(cl.speed)), 1e-3) / float(fps) * (1 if cl.src_out >= cl.src_in
                                                                                          else -1)


def _people_of(cfg: Any) -> Any:
    """The speakers.Context the pipeline attached to the run's Config (who is in the picture and who speaks), or
    None: then no person check and the framing is the competitor's (and faces.main_face_x where it cannot be)."""
    return getattr(cfg, "premiere_people", None)


def _person_of(cl: PremiereClip, sp: Any, fps: Fraction) -> Any:
    """What clip ``cl`` has to show (speakers.Faces), or None (nothing analysed / no check) -- over the source it
    plays in the final edit (``person_span``: extended where its speech is finished) when that is known."""
    if sp is None:
        return None
    if cl.person_span is not None:
        a, b = cl.person_span[0] / float(fps), cl.person_span[1] / float(fps)
    else:
        a, b = _clip_raw_s(cl, fps)
    return sp.faces(min(a, b), max(a, b))


def _final_spans(clips: list[PremiereClip], silence: Any) -> None:
    """Each clip's ``person_span``: the source range it plays once the speech-safe cuts and the silence removal are
    applied -- its own range, widened by what is added at its own two edges (silence.Ripple.ext, stage by stage: a
    clip may play on to finish its words, or start earlier) -- so its framing is chosen for, and checked on, the
    same stretch. (Removed frames inside it only shorten it: the span is the outer bound.)"""
    stages = list(silence.stages()) if silence is not None else []
    for c in clips:
        r0, r1 = int(c.rec_start), int(c.rec_end)
        e0 = e1 = 0
        for st in stages:
            e0 += int(st.ext(r0, "start"))
            e1 += int(st.ext(r1, "end"))
            r0, r1 = st.map1(r0), st.map1(r1)
        lo, hi = sorted((int(c.src_in), int(c.src_out)))
        v = abs(float(c.speed)) or 1.0
        c.person_span = (lo - int(round(e0 * v)), hi + int(round(e1 * v)))


def _person_after_merge(clips: list[PremiereClip], sp: Any, raw_wh: tuple[float, float],
                        win: tuple[float, float, float, float], fps: Fraction) -> int:
    """Once the pieces of one take are joined into one clip (_merge_continuous), the joined clip is checked again on
    its whole stretch -- as the XML's person check reads it -- and moved sideways to show its person when it does not
    (speakers.frame_run, zoom kept). Returns how many moved."""
    from . import speakers
    moved = 0
    for c in clips:
        if len(c.keys) != 1 or len(c.events or [c.ev]) < 2 or abs(float(c.keys[0][1].theta_deg)) > 1e-9:
            continue
        old = c.keys[0][1]
        f = _person_of(c, sp, fps)
        flip = bool(c.seg.flip_h)
        if f is None or f.how == "nobody" or speakers.shows_anyone(old, f, raw_wh[0], flip, win):
            continue
        new = speakers.frame_run(old, [(c, f, flip)], raw_wh, win)[0]
        if new is None:
            continue
        c.keys = [(c.keys[0][0], new)]
        c.covered = _covers(new, raw_wh, win, tol=1e-6)
        b = speakers.target(new, f, raw_wh[0], flip, win)
        who = {"speaker": "the person speaking", "biggest face": "the biggest face (who speaks is unclear)",
               "a person": "a person (nobody speaks)"}.get(f.how, f.how)
        cx = (b[0] + b[2]) / 2.0 if b else float("nan")
        dx = (new.tx + new.s * raw_wh[0] / 2.0) - (old.tx + old.s * raw_wh[0] / 2.0)
        c.person_note = (f"re-framed to show {who} (RAW x {cx:.0f}) over the whole take its pieces play -- the "
                         f"picture moved {dx:+.0f} px sideways, zoom kept")
        moved += 1
    return moved


def _hold_after_merge(clips: list[PremiereClip], sp: Any, raw_wh: tuple[float, float],
                      win: tuple[float, float, float, float], fps: Fraction, min_move: float) -> int:
    """--min-move once more on the joined clips: a clip that took its own framing (under min_move px from the one
    before, inside one RAW shot) because the framing before would not show its person -- judged on the piece it was
    then -- keeps the framing before when that does show its person over the whole joined take (as the XML check
    reads it). Returns how many changed."""
    from . import speakers
    held = 0
    for ca, cb in zip(clips, clips[1:]):
        if len(ca.keys) != 1 or len(cb.keys) != 1 or "would not show its person" not in (cb.framing_note or ""):
            continue
        fa, own = ca.keys[0][1], cb.keys[0][1]
        mv = framing_move(fa, own, raw_wh)
        if not (0.5 < mv < min_move) or not sp.same_shot(_last_raw_s(ca, fps), _clip_raw_s(cb, fps)[0]):
            continue
        if not _covers(fa, raw_wh, win, tol=1e-6) or not speakers.passes(fa, _person_of(cb, sp, fps), raw_wh[0],
                                                                        bool(cb.seg.flip_h), win):
            continue
        if cb.zoom > 0:
            cb.zoom = fa.s / (own.s / cb.zoom)
        cb.keys = [(cb.keys[0][0], fa)]
        cb.covered = True
        cb.framing_note = (f"framing kept from {ca.label}: its own framing moves {mv:.0f} px, under --min-move "
                           f"{min_move:g}, and the framing before shows its person over the whole take")
        held += 1
    return held


def _hold_framing(clips: list[PremiereClip], raw_wh: tuple[float, float], win: tuple[float, float, float, float],
                  min_move: float, subject: str = "the competitor's", sp: Any = None,
                  fps: Fraction | None = None) -> list[list[PremiereClip]]:
    """--min-move (after the fixed framing): a clip takes its own framing when it is at least min_move px from the
    framing on screen (framing_move), at a shot change of the RAW to a framing that is not alike (``sp.same_shot``;
    over SHOT_HOLD_ZOOM in zoom, or the framing on screen would not show the person of the clip before: a new shot
    chooses its framing fresh then), or when the framing on screen would not show the clip's person
    (speakers.passes); otherwise it keeps that framing exactly -- across real cuts and alike RAW shots too (your
    habit: one framing for alike shots -- video018's wide shots, zendaya-age, video1-3) -- changed only as little as
    needed if it would not cover the window. A clip with no framing keeps the one on screen. Returns the stretches of
    clips that show one framing."""
    from . import speakers
    runs: list[list[PremiereClip]] = []
    held: Sim | None = None
    held_from = ""
    last_t: float | None = None                  # the RAW second the clip before ends on (its shot)
    prev: PremiereClip | None = None             # the clip before: a framing held across a shot shows its person
    for cl in clips:
        when = cl.keys[0][0] if cl.keys else cl.src_in
        own = cl.keys[0][1] if cl.keys else None
        t_in = _clip_raw_s(cl, fps)[0] if fps is not None else None
        new_shot = sp is not None and last_t is not None and t_in is not None and not sp.same_shot(last_t, t_in)
        across = False
        if (new_shot and own is not None and held is not None and prev is not None
                and abs(own.s / held.s - 1.0) <= SHOT_HOLD_ZOOM
                and speakers.passes(held, _person_of(prev, sp, fps), raw_wh[0], bool(prev.seg.flip_h), win)):
            new_shot, across = False, True       # an alike framing across the RAW cut: held as inside one shot
        prev = cl
        last_t = _last_raw_s(cl, fps) if fps is not None else None
        if own is not None and held is not None and _same_framing(own, held) and not new_shot:
            runs[-1].append(cl)                          # already showing it
            continue
        move = framing_move(held, own, raw_wh) if (held is not None and own is not None) else None
        keep = None
        if held is not None and not (move is not None and move >= min_move) and (not new_shot or own is None):
            keep = held if _covers(held, raw_wh, win, tol=1e-6) else _least_cover(held, raw_wh, win)
            if own is not None and sp is not None and fps is not None and not speakers.passes(
                    keep, _person_of(cl, sp, fps), raw_wh[0], bool(cl.seg.flip_h), win):
                keep = None                              # holding it would hide this clip's person
        if keep is None:
            if own is not None and held is not None and move is not None and move < min_move:
                cl.framing_note = ("its own framing: a new shot of the RAW (--min-move holds only alike framings "
                                   "across a shot)" if new_shot else
                                   "its own framing: the framing before would not show its person")
            if own is not None:
                held, held_from = own, cl.label
            runs.append([cl])
            continue
        cl.framing_note = (f"framing kept from {held_from}: " +
                           (f"{subject} moves {move:.0f} px here, under --min-move {min_move:g}" if move is not None
                            else "no framing measured here") +
                           (" (an alike framing across a RAW shot change)" if across else "") +
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
    replaced by the RAW (broll.py; the framing there is a neighbour's), else None. A 'keeps playing (short)' spot --
    the competitor's 1-2 frame cutaway over which the clip's own take plays on -- does not count: the framing there
    is the take's own (video018: S07 re-centred the whole S05..S10 stretch, 80 px from both yours and the
    competitor's)."""
    for e in cl.events or [cl.ev]:
        seg = e.seg
        b = (seg.audio or {}).get("broll") or {}
        hit = [r for r in b.get("ranges") or [] if max(int(r[0]), e.rec_in) < min(int(r[1]), e.rec_out)
               and not (len(r) > 3 and r[3] == "keeps playing (short)")]
        if hit or seg.type in ("uncertain", "not_in_raw"):
            return f"{_seg_label(seg)} {b.get('replaced') or seg.type.replace('_', '-')} replaced"
    return None


def _face_centred(fr: Sim, face_x: float, raw_wh: tuple[float, float], win: tuple[float, float, float, float]) -> Sim:
    """The same zoom and height, moved sideways so RAW x face_x sits at the window's centre (then the least move that
    still covers the window)."""
    return _least_cover(Sim(fr.s, 0.0, win[0] + win[2] / 2.0 - fr.s * float(face_x), fr.ty), raw_wh, win)


def _settle_framing(clips: list[PremiereClip], cutlist: Cutlist, raw_wh: tuple[float, float],
                    win: tuple[float, float, float, float], min_move: float, fps: Fraction, sp: Any = None) -> None:
    """The final fixed framings: --min-move on the competitor's framings (inside one shot of the RAW, never hiding
    the clip's person), then every stretch that shows one framing and cannot take it from the competitor -- it holds
    a replaced B-roll / NOT-IN-RAW / uncertain spot, the framing would leave part of the window uncovered, or it does
    not show the person speaking (``sp``: speakers.py) -- keeps its zoom and is moved sideways to centre that person
    (one position for the whole stretch when one fits, else each clip its own); where the RAW's people were not
    analysed, the main face (faces.main_face_x over the stretch's frames). Then --min-move again between the final
    framings."""
    from . import faces, speakers
    runs = _hold_framing(clips, raw_wh, win, min_move, sp=sp, fps=fps)
    video = str(cutlist.raw.get("file_abs") or cutlist.raw.get("file") or "")
    raw_fps = float(cutlist.raw_fps)
    face_run = False
    for run in runs:
        fr = run[0].keys[0][1] if run[0].keys else None
        if fr is None:
            continue
        why = [w for w in (_unreliable(c) for c in run) if w]
        need = [(c, _person_of(c, sp, fps), bool(c.seg.flip_h)) for c in run] if sp is not None else []
        # re-framed only where the framing shows nobody of the clip: a person shown -- even not the one the speech
        # detection picked -- is the competitor's choice (speakers.shows_anyone; 021, laptop004)
        hidden = [c for c, f, fl in need if not speakers.shows_anyone(fr, f, raw_wh[0], fl, win)]
        if not why and not hidden and _covers(fr, raw_wh, win, tol=1e-6):
            continue
        if sp is not None and any(f is not None and f.how != "nobody" for _, f, _ in need):
            base = fr if _covers(fr, raw_wh, win, tol=1e-6) else _least_cover(fr, raw_wh, win)
            # the competitor's framing (covering the window) wins where it shows a person (speakers.shows_anyone)
            keep_base = all(speakers.shows_anyone(base, f, raw_wh[0], fl, win) for _, f, fl in need)
            news = [None] * len(run) if keep_base else speakers.frame_run(base, need, raw_wh, win)
            reason = "; ".join(why) if why else ("it would not show the person speaking" if hidden else
                                                 "the framing would leave part of the window uncovered")
            span = run[0].label + (f"..{run[-1].label}" if len(run) > 1 else "")
            for c, new in zip(run, news):
                new = new if new is not None else base
                c.keys = [(c.keys[0][0] if c.keys else c.src_in, new)]
                c.covered = _covers(new, raw_wh, win, tol=1e-6)
                c.framing_note = (f"{span}: {reason}: the competitor's framing, moved the least to cover the window"
                                  if keep_base else f"{span}: {reason}: framed on the person (zoom kept)")
            face_run = True
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
        _hold_framing(clips, raw_wh, win, min_move, subject="its framing", sp=sp, fps=fps)


def _merge_continuous(clips: list[PremiereClip]) -> list[PremiereClip]:
    """--min-move: neighbouring clips that play one continuous RAW take (the next one starts on the very source frame
    the previous one ends on -- a tick off on a retimed clip's in-point grid --, the same speed within SPEED_TOL, same
    flip, no transition, no retime) with the same fixed framing become one clip -- no cut there; it plays on at the
    first one's speed (output/020: S02 at 125 % and S03 at 125.125 %, one take). Real cuts (a jump in RAW time) stay."""
    out: list[PremiereClip] = []
    for cl in clips:
        p = out[-1] if out else None
        exact = abs(cl.speed - p.speed) < 1e-9 if p is not None else True
        if (p is not None and p.end != -1 and cl.start != -1 and p.rec_end == cl.rec_start
                and abs(cl.src_in - p.src_out) <= (0 if exact and _remap_factor(p.speed) == 1.0 else 1)
                and _speed_ok(cl.speed, p.speed) and not p.retime and not cl.retime
                and bool(p.seg.flip_h) == bool(cl.seg.flip_h) and len(p.keys) == 1 and len(cl.keys) == 1
                and _same_framing(p.keys[0][1], cl.keys[0][1])):
            p.end, p.rec_end = cl.end, cl.rec_end
            p.src_out = cl.src_out if exact and cl.src_in == p.src_out else                 p.src_in + int(round((p.rec_end - p.rec_start) * float(p.speed)))
            p.events = (p.events or [p.ev]) + (cl.events or [cl.ev])
            p.person_note = p.person_note or cl.person_note
            if p.person_span is not None or cl.person_span is not None:
                sa = p.person_span or (min(p.src_in, p.src_out), max(p.src_in, p.src_out))
                sb = cl.person_span or (min(cl.src_in, cl.src_out), max(cl.src_in, cl.src_out))
                p.person_span = (min(sa[0], sb[0]), max(sa[1], sb[1]))
            continue
        out.append(cl)
    return out


def snap_to_shots(clips: list[PremiereClip], changes_s: Sequence[float] | None, fps: Fraction
                  ) -> tuple[list[PremiereClip], list[dict]]:
    """V1 cuts one sequence frame off a RAW shot change, moved onto it. Premiere shows at sequence frame r the RAW
    frame floor(t x raw_fps) of the clip's source time t (shots.py), so the change at RAW frame k first shows on tick
    ceil(k x fps / raw_fps). The source in-points (_pick_in_tick), the speech-safe cuts and the audio lines place a
    cut on the NEAREST tick of a RAW time: one tick early when k x fps / raw_fps has a fraction under one half (a
    29.97 fps RAW in 60 fps: k mod 500 < 250), one late when an in-point rounds up past the change -- video018
    S05|S06: S06's first frame showed RAW 22032, its shot before's last; S16|S17: S16's last frame RAW 22483, the next
    shot's first, at S16's framing. The two clips trade that frame: the clip after a cut whose first frame is the
    shot before's last starts one frame later (the clip before plays one more frame of its own shot); the clip before
    whose last frame is the next shot's first ends one frame earlier (the clip after starts one frame earlier in its
    source). Nothing else moves: the sequence keeps its length and A1 its cuts (a take runs on under the picture; a
    one-frame split edit elsewhere). Only between two clips of two frames or more with no dissolve between them,
    where the clip whose <in> moves plays at 100 % and the frame the other one takes stays in its own shot. Returns
    (clips, [{'clips', 'from', 'to'}])."""
    if not changes_s or len(clips) < 2:
        return clips, []
    f = float(fps)
    cs = sorted(float(c) for c in changes_s)

    def shot(tick: float) -> int:
        return bisect.bisect_right(cs, tick / f + 1e-9)
    out, moves = list(clips), []
    for i in range(len(out) - 1):
        a, b = out[i], out[i + 1]
        r = a.rec_end
        if (a.end == -1 or b.start == -1 or b.rec_start != r or float(a.speed) <= 0.0
                or abs(float(b.speed) - 1.0) > 1e-9 or r - a.rec_start < 2 or b.rec_end - r < 2):
            continue

        def ta(j: int) -> float:
            return a.src_in + (j - a.rec_start) * float(a.speed)

        def tb(j: int) -> float:
            return b.src_in + (j - r)
        early = shot(tb(r)) != shot(tb(r + 1)) and shot(ta(r)) == shot(ta(r - 1))
        late = shot(ta(r - 1)) != shot(ta(r - 2)) and shot(tb(r - 1)) == shot(tb(r))
        if early == late:
            continue
        d = 1 if early else -1
        out[i] = dataclasses.replace(a, end=r + d, rec_end=r + d,
                                     src_out=a.src_in + int(round((r + d - a.rec_start) * float(a.speed))))
        out[i + 1] = dataclasses.replace(b, start=r + d, rec_start=r + d, src_in=b.src_in + d,
                                         in_error_ms=b.in_error_ms + 1000.0 * d / f)
        moves.append({"clips": f"{a.label}|{b.label}", "from": r, "to": r + d})
    return out, moves


MIN_CLIP_FRAMES = 10        # --premiere: a V1 clip shorter than this (sequence frames: 1/6 s at 60 fps) is a mini cut;
#                             yours are 0.3 s or longer (one 10-frame piece on video3); output/020's were 3-9 frames


def _shot_change_ticks(cl: PremiereClip, changes_s: Sequence[float], raw_fps: Fraction, fps: Fraction,
                       lo: int, hi: int) -> list[int]:
    """Sequence frames r in (lo, hi) of clip ``cl`` (playing forward) where a RAW shot change first shows: Premiere
    shows at r the RAW frame floor(t x raw_fps) of t = (src_in + (r - rec_start) x speed) / fps (shots.py), so the
    change at RAW frame k first shows on the smallest r with src_in + (r - rec_start) x speed >= k x fps / raw_fps."""
    v = Fraction(cl.speed).limit_denominator(1_000_000)
    if v <= 0:
        return []
    out = []
    for c in changes_s:
        k = round(Fraction(c).limit_denominator(1_000_000) * raw_fps)
        r = cl.rec_start + math.ceil((Fraction(k) * fps / raw_fps - cl.src_in) / v)
        if lo < r < hi:
            out.append(int(r))
    return sorted(set(out))


def merge_mini_clips(clips: list[PremiereClip], changes_s: Sequence[float] | None, raw_fps: Fraction, fps: Fraction,
                     min_frames: int = MIN_CLIP_FRAMES) -> tuple[list[PremiereClip], list[dict]]:
    """--premiere: no mini cuts. A V1 clip shorter than ``min_frames`` (no dissolve on either side) goes into its
    neighbours: the clip before plays on over it (its take runs on), else the clip after starts that much earlier in
    its own source -- whichever does not reach into another RAW shot (a sliver of a shot is a flash frame). A clip
    that starts a new RAW shot of the take before it (a real shot change: its cut stays) is kept, and so is one neither
    neighbour can cover. Its events go to the clip that covers it, so A1 follows (premiere_audio: one clip's events =
    one take of sound). Returns (clips, [{'clip', 'into', 'frames', 'how'}])."""
    if min_frames <= 0 or len(clips) < 2:
        return clips, []
    cs = sorted(float(c) for c in (changes_s or ()))
    f = float(fps)

    def shot(t_tick: float) -> int:
        return bisect.bisect_right(cs, t_tick / f + 1e-9)

    def raw_at(cl: PremiereClip, r: int) -> float:              # source position (sequence ticks) shown at frame r
        return cl.src_in + (r - cl.rec_start) * float(cl.speed)
    out, moves = list(clips), []
    i = 0
    while i < len(out):
        m = out[i]
        n = m.rec_end - m.rec_start
        a = out[i - 1] if i > 0 else None
        b = out[i + 1] if i + 1 < len(out) else None
        if n >= min_frames or m.start == -1 or m.end == -1 or len(out) < 2:
            i += 1
            continue
        if a is not None and a.end != -1 and a.rec_end == m.rec_start and float(a.speed) > 0 and cs and \
                abs(m.src_in - a.src_out) <= 1 and shot(raw_at(m, m.rec_start)) != shot(raw_at(a, a.rec_end - 1)):
            i += 1                                              # a new RAW shot of the take before: a real cut
            continue
        done = None
        if a is not None and a.end != -1 and a.rec_end == m.rec_start and float(a.speed) > 0 and not a.retime and \
                all(shot(raw_at(a, r)) == shot(raw_at(a, a.rec_end - 1)) for r in range(a.rec_end, m.rec_end)):
            a.end, a.rec_end = m.end, m.rec_end
            a.src_out = a.src_in + int(round((a.rec_end - a.rec_start) * float(a.speed)))
            a.events = (a.events or [a.ev]) + (m.events or [m.ev])
            done = (a, "the clip before plays on")
        elif b is not None and b.start != -1 and b.rec_start == m.rec_end and float(b.speed) > 0 and not b.retime \
                and b.src_in - int(round(n * float(b.speed))) >= 0 and \
                all(shot(raw_at(b, r)) == shot(raw_at(b, b.rec_start)) for r in range(m.rec_start, b.rec_start)):
            d = int(round(n * float(b.speed)))
            b.start, b.rec_start, b.src_in = m.start, m.rec_start, b.src_in - d
            b.in_exact, b.in_error_ms = False, b.in_error_ms - 1000.0 * d / f
            b.events = (m.events or [m.ev]) + (b.events or [b.ev])
            done = (b, "the clip after starts earlier")
        if done is None:
            i += 1
            continue
        moves.append({"clip": m.label, "into": done[0].label, "frames": int(n), "how": done[1], "at": int(m.rec_start)})
        del out[i]
        i = max(0, i - 1)
    return out, moves


def split_at_shots(clips: list[PremiereClip], changes_s: Sequence[float] | None, raw_fps: Fraction, fps: Fraction,
                   fac: int = 1) -> tuple[list[PremiereClip], list[dict]]:
    """--premiere: Premiere's Scene Edit Detection ("apply a cut at each detected cut point"), as you always run it:
    every V1 clip is split at every RAW shot change it plays (shots.py), on the frame the new shot first shows; the
    pieces play on seamlessly (the same source, speed and framing) and link_pairs splits A1 with them. Not inside a
    cross dissolve's frames, nor a reverse. Returns (clips, [{'clip', 'at'}])."""
    if not changes_s:
        return clips, []
    out: list[PremiereClip] = []
    splits = []
    for cl in clips:
        lo = cl.rec_start + (int(cl.ev.dissolve_in or 0) * fac if cl.start == -1 else 0)   # after a dissolve's frames
        ticks = _shot_change_ticks(cl, changes_s, raw_fps, fps, lo, cl.rec_end)
        rest = cl
        for r in ticks:
            if rest.rec_start < r < rest.rec_end:
                p, rest = _split_clip(rest, r)
                out.append(p)
                splits.append({"clip": cl.label, "at": int(r)})
        out.append(rest)
    # a split one RAW frame or less after a clip's start: that frame is the shot before's last, at this clip's framing
    # (a flash; snap_to_shots moves such a cut only for a clip at 100 %): the clip before, adjacent and still in that
    # shot, plays on over it (output/020 --fast: S12 at 125 %, 1 frame at 00:00:19:03)
    cs = sorted(float(c) for c in changes_s)
    one = float(fps) / float(raw_fps)
    i = 1
    while i < len(out) - 1:
        a, p, b = out[i - 1], out[i], out[i + 1]
        n = p.rec_end - p.rec_start
        if (p.start != -1 and a.end != -1 and a.rec_end == p.rec_start and b.rec_start == p.rec_end and p.link_split
                and b.link_split and p.src_out == b.src_in and n * abs(float(p.speed)) <= one + 1e-9
                and float(a.speed) > 0 and not a.retime):
            t = lambda c, r: (c.src_in + (r - c.rec_start) * float(c.speed)) / float(fps)     # noqa: E731
            if all(bisect.bisect_right(cs, t(a, r) + 1e-9) == bisect.bisect_right(cs, t(a, a.rec_end - 1) + 1e-9)
                   for r in range(a.rec_end, p.rec_end)):
                out[i - 1] = dataclasses.replace(a, end=p.end, rec_end=p.rec_end, src_out=a.src_in + int(round(
                    (p.rec_end - a.rec_start) * float(a.speed))))
                del out[i]
                continue
        i += 1
    return out, splits


def _source_seconds(seg: Segment, k: float, comp_fps: Fraction, raw_fps: Fraction) -> float:
    """The plan's exact RAW time at competitor frame k (continuous; remap keys interpolated)."""
    if seg.time_remap_keys:
        return _remap_seconds(seg.time_remap_keys, float(k))
    return _raw_in_seconds(seg, raw_fps) + float(seg.speed) * (float(k) - float(seg.comp_in)) / float(comp_fps)


def _pick_in_tick(seg: Segment, rec_in: int, tau0: float, comp_fps: Fraction, fps: Fraction,
                  speed: float = 1.0) -> tuple[int, bool]:
    """(<in> in 1/fps source ticks, inside the frame-exact interval): the tick inside the segment's feasible raw_in
    interval (shifted to rec_in) nearest the plan's raw time, else the nearest tick. At another ``speed`` Premiere
    starts the clip at a whole tick of the RETIMED clip (remap_encode: source time x * speed / fps), so the candidates
    are those -- returned as the source tick that encodes to it."""
    f = float(fps)
    s = _remap_factor(speed)
    iv = seg.raw_in_interval if not seg.time_remap_keys else None
    if iv and len(iv) == 2 and float(iv[1]) > float(iv[0]):
        shift = float(seg.speed) * (rec_in - int(seg.comp_in)) / float(comp_fps)
        lo, hi = float(iv[0]) + shift, float(iv[1]) + shift
        x_lo, x_hi = math.ceil(lo * f / s - 1e-9), math.floor(hi * f / s - 1e-9)
        if x_hi >= x_lo:
            x = min(max(int(round(tau0 * f / s)), x_lo), x_hi)
            if lo - 1e-9 <= x * s / f < hi:
                return _source_tick(x, s), True
    return _source_tick(int(round(tau0 * f / s)), s), False


def _played_s(n: int, speed: float, fps: Fraction) -> float:
    """The source second Premiere starts a clip at whose plan in-point is source tick n (remap_encode's tick x
    times the speed)."""
    s = _remap_factor(speed)
    return int(round(n / s)) * s / float(fps)


def _source_tick(x: int, s: float) -> int:
    """The source tick n with remap_encode(n) == x (retimed tick x at factor s): round(x * s), moved one tick when
    rounding back would land on a neighbour (s > 1)."""
    n = int(round(x * s))
    if s != 1.0:
        for c in (n, n - 1, n + 1):
            if int(round(c / s)) == x:
                return c
    return n


OTHER_VIDEO = "OTHER VIDEO \u2013 not in RAW"     # the marker on a stretch of another video (broll.py)


def other_video_of(seg: Segment | None) -> dict | None:
    """The other-video record of a NOT-IN-RAW piece broll.py found to show another video, else None."""
    if seg is None or seg.type != "not_in_raw":
        return None
    ov = (seg.audio or {}).get("other_video")
    return ov if isinstance(ov, dict) else None


def other_video_comment(ov: dict, ev: Any, comp_fps: Fraction) -> str:
    said = " ".join(str(w[0]) for w in ov.get("words") or [])
    n = int(ev.rec_out) - int(ev.rec_in)
    return (f"the competitor shows another video here (not in the RAW: its speech is not in the RAW audio) -- V1 and "
            f"A1 are left empty for exactly its length, {n / float(comp_fps):.2f} s: put that video here. Competitor "
            f"{_tc(int(ev.rec_in), comp_fps)}-{_tc(int(ev.rec_out), comp_fps)}" + (f"; it says: \"{said}\"" if said
                                                                                    else ""))


def other_video_name(a: int, b: int, fps: Fraction) -> str:
    """The marker's name: OTHER VIDEO \u2013 not in RAW (start\u2013end), the stretch's timecodes in the edit."""
    return f"{OTHER_VIDEO} ({_tc(a, fps)}\u2013{_tc(b, fps)})"


def premiere_clips(cutlist: Cutlist, cfg: Any = None, silence: Any = None
                   ) -> tuple[list[PremiereClip], list[dict], list[str]]:
    """(V1 clips, markers [{name, comment, in, out}], warnings) of the Premiere export (sequence-rate frames).
    ``silence``: the edit's silence.Ripple -- each clip's person framing is then chosen for the source it plays in the
    final edit (_final_spans), as the XML's person check reads it."""
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
            ov = other_video_of(seg)
            if ov is not None:                   # another video (broll.py): V1 and A1 stay empty, exactly this long
                markers.append({"name": OTHER_VIDEO, "comment": other_video_comment(ov, ev, comp_fps),
                                "in": ev.rec_in * fac, "out": ev.rec_out * fac, "other_video": ev.seg_name})
            elif seg is not None and seg.type in ("uncertain", "not_in_raw"):
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
        if seg.time_remap_keys or abs(float(seg.speed)) < 1e-9:      # a freeze is placed at 100 %, marked RETIME
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
        n_in, exact = _pick_in_tick(seg, ev.rec_in, tau0, comp_fps, fps, v)
        if not exact and not seg.time_remap_keys:
            step = f"1/{float(fps):g} s" if _remap_factor(v) == 1.0 else                 f"{100.0 * _remap_factor(v):g} % x 1/{float(fps):g} s (Premiere's in-point grid at that speed)"
            warnings.append(f"{ev.seg_name}: no {step} source in-point inside its frame-exact interval; nearest is "
                            f"{1000.0 * (_played_s(n_in, v, fps) - tau0):+.2f} ms from the plan (a few frames may show a "
                            "neighbouring RAW frame)")
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
                                  1000.0 * (_played_s(n_in, v, fps) - tau0), z, covered, keys, retime, [ev]))
    clips, mini = merge_mini_clips(clips, getattr(cfg, "premiere_shots", None), raw_fps, fps, st["min_clip"])
    for m in mini:                      # no mini cuts: a clip of a few frames goes into its neighbour
        # (before the framing below: the person check and --min-move judge the joined clip -- zendaya)
        markers_note = (f"{m['clip']}: {m['frames']} frame(s) -- a mini cut, joined into {m['into']} "
                        f"({m['how']}; {_tc(m['at'], fps)})")
        warnings.append(markers_note)
    if st["static"]:
        # fewer reframes and cuts: hold the framing under min_move px (inside one RAW shot), frame on the person speaking
        # what the competitor cannot frame or does not show them, then join the pieces of one take that are left alike
        sp = _people_of(cfg)
        if sp is not None and silence is not None and getattr(silence, "active", False):
            _final_spans(clips, silence)
        competitor = {id(c): (c.keys[0][1] if c.keys else None) for c in clips}
        _settle_framing(clips, cutlist, raw_wh, win, st["min_move"], fps, sp)
        if sp is not None:
            _person_notes(clips, competitor, sp, raw_wh, win, fps)
        clips = _merge_continuous(clips)
        if sp is not None:
            _person_after_merge(clips, sp, raw_wh, win, fps)
            if _hold_after_merge(clips, sp, raw_wh, win, fps, st["min_move"]):
                clips = _merge_continuous(clips)
    return clips, markers, warnings


def _person_notes(clips: list[PremiereClip], competitor: dict[int, Sim | None], sp: Any, raw_wh: tuple[float, float],
                  win: tuple[float, float, float, float], fps: Fraction) -> None:
    """The re-framed clips' notes (the end summary lists them): every clip whose competitor framing would not show
    its person and whose final framing does -- who, where in the RAW, how far the picture moved."""
    from . import speakers
    for c in clips:
        old, new = competitor.get(id(c)), (c.keys[0][1] if c.keys else None)
        f = _person_of(c, sp, fps)
        if old is None or new is None or f is None or f.how == "nobody":
            continue
        flip = bool(c.seg.flip_h)
        before = speakers.passes(old, f, raw_wh[0], flip, win)
        after = speakers.passes(new, f, raw_wh[0], flip, win)
        if before or _same_framing(old, new) or (not after and speakers.shows_anyone(new, f, raw_wh[0], flip, win)):
            continue                     # (the competitor's framing kept on another person: its choice, no note)
        b = speakers.target(new, f, raw_wh[0], flip, win)
        who = {"speaker": "the person speaking", "biggest face": "the biggest face (who speaks is unclear)",
               "a person": "a person (nobody speaks)"}.get(f.how, f.how)
        cx = (b[0] + b[2]) / 2.0 if b else float("nan")
        dx = (new.tx + new.s * raw_wh[0] / 2.0) - (old.tx + old.s * raw_wh[0] / 2.0)     # the picture's centre
        dy = (new.ty + new.s * raw_wh[1] / 2.0) - (old.ty + old.s * raw_wh[1] / 2.0)
        c.person_note = ((f"re-framed to show {who} (RAW x {cx:.0f}): the competitor's framing showed "
                          f"{'nobody' if f.how == 'a person' else 'someone else'} -- the picture moved "
                          f"{dx:+.0f} px sideways" + (f", {dy:+.0f} px up/down" if abs(dy) >= 0.5 else "") +
                          ", zoom kept") if after else
                         f"could not show {who} (RAW x {cx:.0f}) with the zoom kept: re-frame it by hand")


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
        lo = xml_in_out(clip.src_in, clip.src_out)[0]
        whens = [remap_when(w, lo, clip.speed) for w, _ in clip.keys]
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


PREMIERE_MASTERCLIP = "masterclip-raw"


def write_premiere_xml(cutlist: Cutlist, path: str | os.PathLike, cfg: Any = None, silence: Any = None,
                       speed: float = 1.0) -> dict:
    """recreated_edit.xml for Premiere Pro (--premiere; see the section comment above): the 1080x1920 / 60.00 fps
    sequence, V1 = the RAW clips framed into the template window, A1 = their RAW audio at the same cuts (an audio
    line where FX-14 found one), markers on UNCERTAIN / NOT-IN-RAW (and RETIME) spots, V2+ empty. ``silence`` (a
    silence.Ripple: the speech-safe cuts, then the silences and repeats): its ranges are cut out -- everything after
    them moves earlier --, clips are extended where their speech must finish, and A1 fades over
    silence.FADE_FRAMES on both sides of every removed range (Audio Levels keyframes: no click). ``speed`` (--speed /
    100): the finished edit played that fast (retime_edit). With --frame (cfg.frame_png) the sequence is the frame's
    (frame.py), the window its hole, and V2 holds the PNG over the whole edit. Returns {'clips', 'markers', 'warnings',
    'factor'}."""
    from . import silence as sil
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
    clips, markers, warnings = premiere_clips(cutlist, cfg, silence)
    audio_items = premiere_audio(cutlist, clips, cfg) if has_audio else []
    if silence is not None and silence.active:
        clips, audio_items, markers = sil.apply_premiere(clips, audio_items, markers, silence)
        N = silence.new_frames
    clips, snapped = snap_to_shots(clips, getattr(cfg, "premiere_shots", None), fps)   # cuts on the RAW's own cuts
    for m in snapped:
        warnings.append(f"{m['clips']}: the cut moved {m['to'] - m['from']:+d} frame onto the RAW's shot change "
                        f"({_tc(m['from'], fps)} -> {_tc(m['to'], fps)})")
    scene: list[dict] = []
    if st["scene_cuts"]:                # Scene Edit Detection: a cut at every RAW shot change inside a clip
        clips, scene = split_at_shots(clips, getattr(cfg, "premiere_shots", None), raw_fps, fps, fac)
    clips, audio_items, pairs = link_pairs(clips, audio_items)       # V1 + A1 as linked clips
    if abs(float(speed) - 1.0) > 1e-9:                                # --speed: the whole edit that much faster
        clips, audio_items, markers, N = retime_edit(clips, audio_items, markers, N, float(speed), fac)
    a_of = {v: a for v, a in pairs}
    v_of = {a: v for v, a in pairs}
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
            _sub(ti, "start", cl.rec_start)
            _sub(ti, "end", cl.rec_start + (cl.dissolve_frames if cl.dissolve_frames >= 0 else ev.dissolve_in * fac))
            _sub(ti, "alignment", "start")
            e = _effect(ti, "Cross Dissolve", "Cross Dissolve", "Dissolve", "transition")
            _sub(e, "wipecode", 0)
            _sub(e, "wipeaccuracy", 100)
            _sub(e, "startratio", 0)
            _sub(e, "endratio", 1)
            _sub(e, "reverse", "FALSE")
        ci = _sub(vtrack, "clipitem", id=f"clipitem-{n}")
        _sub(ci, "masterclipid", PREMIERE_MASTERCLIP)         # one master clip: the Project panel shows one RAW
        _sub(ci, "name", raw_name)                            # the segment ids are in the comments
        _sub(ci, "enabled", "TRUE")
        _sub(ci, "duration", remap_duration(src_dur, cl.speed))
        _rate_el(ci, fps)
        _sub(ci, "start", cl.start)
        _sub(ci, "end", cl.end)
        x_in, x_out = remap_encode(*xml_in_out(cl.src_in, cl.src_out), cl.speed, item_length(cl.start, cl.end))
        _sub(ci, "in", x_in)
        _sub(ci, "out", x_out)
        _sub(ci, "alphatype", "none")
        _sub(ci, "pixelaspectratio", "square")
        _sub(ci, "anamorphic", "FALSE")
        _file_el(ci, "file-raw", defined, raw_name, raw_abs, raw_fps, raw_frames, raw_w, raw_h, audio_info)
        if abs(cl.speed - 1.0) > 1e-9:
            _time_remap(ci, cl.speed)
        note = _premiere_motion(ci, cl, W, H, (raw_w, raw_h))
        if cl.seg.flip_h:
            _flop(ci)
        stv = _sub(ci, "sourcetrack")
        _sub(stv, "mediatype", "video")
        _sub(stv, "trackindex", 1)
        if n - 1 in a_of:
            _links(ci, n, a_of[n - 1] + 1)
        cm = _sub(ci, "comments")
        _sub(cm, "mastercomment1", f"{cl.label} speed {cl.speed:.6f} conf {float(cl.seg.confidence or 0):.2f}")
        _sub(cm, "mastercomment2", note + (f"; {cl.person_note}" if cl.person_note else ""))
        _sub(cm, "mastercomment3", ("source in inside the frame-exact interval" if cl.in_exact else
                                    "source in = nearest 1/60 s (outside the frame-exact interval)")
             + f" ({cl.in_error_ms:+.2f} ms from the plan)")
    if str(getattr(cfg, "frame_png", "") or ""):                      # --frame: the PNG on V2, over everything
        _frame_track(video, str(cfg.frame_png), N, fps, W, H)
    # A1: the RAW audio at the SAME record ranges as V1 (picture-synced; an audio line where FX-14 found one)
    if has_audio:
        audio = _sub(media, "audio")
        _sub(audio, "numOutputChannels", 2)
        afmt = _sub(audio, "format")
        asc = _sub(afmt, "samplecharacteristics")
        _sub(asc, "depth", 16)
        _sub(asc, "samplerate", int(audio_info["sample_rate"]))
        atrack = _sub(audio, "track")
        for n_a, it in enumerate(audio_items, start=1):
            ai = _sub(atrack, "clipitem", id=f"clipitem-a{n_a}")
            _sub(ai, "masterclipid", PREMIERE_MASTERCLIP)
            _sub(ai, "name", raw_name)
            _sub(ai, "enabled", "TRUE")
            _sub(ai, "duration", remap_duration(src_dur, it["speed"]))
            _rate_el(ai, fps)
            _sub(ai, "start", it["start"])
            _sub(ai, "end", it["end"])
            x_in, x_out = remap_encode(*xml_in_out(it["in"], it["out"]), it["speed"], item_length(it["start"], it["end"]))
            _sub(ai, "in", x_in)
            _sub(ai, "out", x_out)
            _file_el(ai, "file-raw", defined, raw_name, raw_abs, raw_fps, raw_frames, raw_w, raw_h, audio_info)
            if abs(it["speed"] - 1.0) > 1e-9:
                _time_remap(ai, it["speed"], "audio")
            if it.get("fade_in") or it.get("fade_out"):
                _audio_fades(ai, it, sil.FADE_FRAMES)
            sta = _sub(ai, "sourcetrack")
            _sub(sta, "mediatype", "audio")
            _sub(sta, "trackindex", 1)
            if n_a - 1 in v_of:
                _links(ai, v_of[n_a - 1] + 1, n_a)
            cm = _sub(ai, "comments")
            _sub(cm, "mastercomment1", f"{_seg_label(it['seg'])} {it['what']}")
    for m in markers:
        mk = _sub(seq, "marker")
        _sub(mk, "name", other_video_name(m["in"], m["out"], fps) if m.get("other_video") else m["name"])
        _sub(mk, "comment", m["comment"])
        _sub(mk, "in", m["in"])
        _sub(mk, "out", m["out"])
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    atomic_write_text(path, '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n")
    for w in warnings:
        log.info("premiere export: %s", w)
    reframed: list[dict] = []
    for cl in clips:                   # the clips framed on their person (speakers.py), each once
        if cl.person_note and not (reframed and reframed[-1]["label"] == cl.label and reframed[-1]["note"] == cl.person_note):
            s0 = cl.start if cl.start != -1 else cl.rec_start
            reframed.append({"label": cl.label, "start": int(s0), "tc": _tc(int(s0), fps), "note": cl.person_note})
    return {"clips": len(clips), "markers": len(markers), "warnings": warnings, "factor": fac, "links": len(pairs),
            "reframed": reframed, "snapped": snapped, "scene_cuts": scene,
            "other_video": [{"segment": m["other_video"], "in": m["in"], "out": m["out"],
                             "name": other_video_name(m["in"], m["out"], fps)} for m in markers if m.get("other_video")]}


FRAME_CLIP = "clipitem-frame"


def speed_problems(base_xml: str | os.PathLike, fast_xml: str | os.PathLike, k: float) -> list[str]:
    """--speed: 1_edit.xml (``fast_xml``) against the same edit at 100 % (``base_xml``, the one every other check
    read): the same V1 clips and A1 items in the same order, each at its 100 % speed x k, starting and ending at its
    100 % frame / k (within a frame), playing the same RAW (within Premiere's in-point grid at that speed: k ticks, +1
    for the rounding) -- the timing bug of output/020 (in / out counted as source time on a retimed clip: RAW 629 s
    instead of 503 s) shows here as clips far off their RAW. The sequence lasts its 100 % length / k."""
    a, b = parse_premiere_xml(base_xml), parse_premiere_xml(fast_xml)
    out: list[str] = []
    f = float(a["timebase"])
    if abs(b["duration"] - a["duration"] / k) > 1.0:
        out.append(f"XML SPEED: the sequence lasts {b['duration']} frames, want {a['duration'] / k:.1f} "
                   f"({a['duration']} / {k:g})")
    tol = int(math.ceil(abs(k))) + 1
    for kind in ("clips", "audio"):
        xa, xb = a[kind], b[kind]
        if len(xa) != len(xb):
            out.append(f"XML SPEED: {len(xb)} {'V1 clips' if kind == 'clips' else 'A1 items'}, the 100 % edit has "
                       f"{len(xa)}")
            continue
        for i, (p, q) in enumerate(zip(xa, xb), start=1):
            what = f"{'V1' if kind == 'clips' else 'A1'} item {i} ({p.get('label') or '?'})"
            if abs(float(q["speed"]) - float(p["speed"]) * k) > 2e-4 * abs(float(p["speed"]) * k):
                out.append(f"XML SPEED {what}: speed {100 * float(q['speed']):.3f} %, want "
                           f"{100 * float(p['speed']) * k:.3f} %")
            for key in ("start", "end"):
                if int(p[key]) >= 0 and abs(int(q[key]) - int(p[key]) / k) > 1.0:
                    out.append(f"XML SPEED {what}: {key} {q[key]}, want {int(p[key]) / k:.1f}")
            if abs(int(q["in"]) - int(p["in"])) > tol * 2 or abs(int(q["out"]) - int(p["out"])) > tol * 2:
                out.append(f"XML SPEED {what}: plays RAW {int(q['in']) / f:.3f}-{int(q['out']) / f:.3f} s, the 100 % "
                           f"edit {int(p['in']) / f:.3f}-{int(p['out']) / f:.3f} s")
    return out


def frame_track_problems(xml_path: str | os.PathLike, n: int) -> list[str]:
    """--frame: V2 must hold exactly the frame PNG, from the first frame of the edit to its last (``n`` frames), with
    straight alpha and an existing file -- otherwise part of the edit is shown without its frame."""
    root = ET.parse(str(xml_path)).getroot()
    seq = root.find("sequence")
    tracks = seq.findall("media/video/track") if seq is not None else []
    if len(tracks) < 2:
        return ["XML FRAME: no V2 track with the frame"]
    items = tracks[1].findall("clipitem")
    if len(items) != 1 or items[0].get("id") != FRAME_CLIP:
        return [f"XML FRAME: V2 holds {len(items)} item(s), want the frame PNG alone"]
    ci = items[0]
    out = []
    if (int(_text(ci, "start", -9)), int(_text(ci, "end", -9))) != (0, int(n)):
        out.append(f"XML FRAME: the frame covers {_text(ci, 'start')}-{_text(ci, 'end')}, want 0-{n} (the whole edit)")
    if str(_text(ci, "alphatype", "")).lower() != "straight":
        out.append("XML FRAME: alphatype is not straight (the hole would not show V1)")
    url = str(_text(ci, "file/pathurl", "") or "")
    path = urllib.parse.unquote(url.replace("file://localhost/", "", 1)) if url.startswith("file://localhost/") else ""
    if not path or not Path(path).is_file():
        out.append(f"XML FRAME: the frame file {url} does not exist")
    return out


def _frame_track(video: ET.Element, png: str, n: int, fps: Fraction, W: int, H: int) -> None:
    """V2 (--frame, frame.py): the frame PNG over the whole edit -- one still clip with straight alpha (its transparent
    hole shows V1 under it), scaled to fill the W x H sequence (Scale 100 for a PNG of the sequence's size, 200 for one
    half as big), its centre on the sequence's."""
    from .frame import load_frame
    fr = load_frame(png)
    name = Path(fr.path).name
    tr = _sub(video, "track")
    ci = _sub(tr, "clipitem", id=FRAME_CLIP)
    _sub(ci, "masterclipid", "masterclip-frame")
    _sub(ci, "name", name)
    _sub(ci, "enabled", "TRUE")
    _sub(ci, "duration", int(n))
    _rate_el(ci, fps)
    _sub(ci, "start", 0)
    _sub(ci, "end", int(n))
    _sub(ci, "in", 0)
    _sub(ci, "out", int(n))
    _sub(ci, "alphatype", "straight")
    _sub(ci, "pixelaspectratio", "square")
    _sub(ci, "anamorphic", "FALSE")
    fe = _sub(ci, "file", id="file-frame")
    _sub(fe, "name", name)
    _sub(fe, "pathurl", _file_url(fr.path))
    _rate_el(fe, fps)
    _sub(fe, "duration", int(n))
    media = _sub(fe, "media")
    sc = _sub(_sub(media, "video"), "samplecharacteristics")
    _rate_el(sc, fps)
    _sub(sc, "width", int(fr.width))
    _sub(sc, "height", int(fr.height))
    _sub(sc, "anamorphic", "FALSE")
    _sub(sc, "pixelaspectratio", "square")
    _sub(sc, "fielddominance", "none")
    e = _effect(_sub(ci, "filter"), "Basic Motion", "basic", "motion", "motion")
    _param(e, "scale", "Scale", _fmt(fr.premiere_scale(W, H)), 0, 1000)
    _param(e, "rotation", "Rotation", "0", -8640, 8640)
    _param(e, "center", "Center", (0.0, 0.0))
    _param(e, "centerOffset", "Anchor Point", (0.0, 0.0))
    st = _sub(ci, "sourcetrack")
    _sub(st, "mediatype", "video")
    _sub(st, "trackindex", 1)
    x, y, w, h = fr.window(W, H)
    cm = _sub(ci, "comments")
    _sub(cm, "mastercomment1", "FRAME")
    _sub(cm, "mastercomment2", f"--frame {name} ({fr.width}x{fr.height}): Scale {fr.premiere_scale(W, H):g} %, "
                               f"Position {W / 2:g}, {H / 2:g} px; its transparent hole x {x:.0f}-{x + w:.0f}, "
                               f"y {y:.0f}-{y + h:.0f} on this sequence -- every V1 clip covers it")


def _links(parent: ET.Element, v: int, a: int) -> None:
    """The <link>s of one linked pair, written into both its clip items the way Premiere exports linked clips: the V1
    clip (clipitem-<v>, the v-th clip of V1) and its A1 clip (clipitem-a<a>, the a-th of A1)."""
    for ref, kind, idx in ((f"clipitem-{v}", "video", v), (f"clipitem-a{a}", "audio", a)):
        lk = _sub(parent, "link")
        _sub(lk, "linkclipref", ref)
        _sub(lk, "mediatype", kind)
        _sub(lk, "trackindex", 1)
        _sub(lk, "clipindex", idx)
        if kind == "audio":
            _sub(lk, "groupindex", 1)


def _audio_fades(parent: ET.Element, it: dict, n: int) -> None:
    """Audio Levels keyframes (media time, like <in> / <out>) fading A1 in over its first n frames and / or out
    over its last n frames, where a silence was cut out (silence.py): the cut cannot click."""
    keys: list[tuple[int, str]] = []
    if it.get("fade_in"):
        keys += [(it["in"], "0"), (it["in"] + n, "1")]
    if it.get("fade_out"):
        keys += [(it["out"] - n, "1"), (it["out"], "0")]
    s = _remap_factor(it.get("speed", 1.0))
    if s != 1.0:                       # on the retimed clip: from its encoded ends, never shorter than one tick
        x_in, x_out = remap_encode(*xml_in_out(it["in"], it["out"]), it["speed"], item_length(it["start"], it["end"]))
        m = max(1, int(round(n / s)))
        keys = ([(x_in, "0"), (x_in + m, "1")] if it.get("fade_in") else []) +             ([(x_out - m, "1"), (x_out, "0")] if it.get("fade_out") else [])
    f = _sub(parent, "filter")
    e = _effect(f, "Audio Levels", "audiolevels", "audiolevels", "audiolevels", "audio")
    _param(e, "level", "Level", None, 0, "3.98109", sorted(keys))


def no_audio_reason(cl: PremiereClip, comp_fps: Fraction) -> str | None:
    """Why V1 clip ``cl`` plays no audio on A1 on purpose (premiere_audio), or None: a freeze (a frozen picture is
    silent, as in the preview) or a cutaway the competitor showed over music / voice-over (picture only)."""
    for e in cl.events or [cl.ev]:
        seg = e.seg
        if seg is None:
            continue
        if (seg.audio or {}).get("mute"):
            return "muted on purpose: the competitor showed a cutaway over music / voice-over here (picture only)"
        if abs(seg_speed(seg, comp_fps)) < 1e-9:
            return "a freeze: a frozen picture plays no audio"
    return None


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
        line = bool((seg.audio or {}).get("line"))
        cl = by_seg.get(seg.id)
        start, end = ev.rec_in * fac, ev.rec_out * fac
        if cl is not None and st["normal_audio"] and (a is None or line):
            # every picture plays its own sound (no muted, silent or replaced A1) -- unless its audio line carries on
            # the sound already playing (one take of sound under the pictures: video018's "insurance")
            prev = out[-1] if out else None
            # its own sound at 100 % under a retimed picture (FX-14 "own in-point") is the picture's own sound
            keep_line = a is not None and line and str(((seg.audio or {}).get("line") or {}).get("source") or ""
                                                      ).startswith("own in-point")
            if not keep_line and a is not None and line and prev is not None and prev["end"] == start:
                v_l = float(a.speed)
                tau_l = _raw_in_seconds(a, raw_fps) + v_l * float(Fraction(ev.rec_in - int(seg.comp_in)) / comp_fps)
                keep_line = abs(int(round(tau_l * float(fps))) - prev["out"]) <= 1 and abs(v_l - prev["speed"]) < 1e-6
            if not keep_line:
                a, line = seg, False
        if a is None:
            continue
        if cl is not None and not line:
            if abs(seg_speed(cl.seg, comp_fps)) < 1e-9 and not st["normal_audio"]:
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


def item_label(el: ET.Element) -> str:
    """A clip item's segment label for messages ("S01+S02"): the first word of its first comment (the clips
    themselves are all named after the RAW), else the first word of its name."""
    c = _text(el, "comments/mastercomment1", "") or ""
    return (c.split() or str(_text(el, "name", "?") or "?").split() or ["?"])[0]


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
    'height', 'items': [{tag, name, start, end (resolved through transitions), in, out (the plan's source frames:
    plan_in_out), speed (negative: reverse), flip, rate, generator}], 'transitions': [{start, end, alignment}],
    'audio_items': [...], 'markers'}."""
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
                elif _is_flip(fe):
                    flip = True
            s_in, s_out = plan_in_out(*remap_decode(int(float(_text(e, "in", 0))), int(float(_text(e, "out", 0))),
                                                    speed), reverse)
            items.append({"tag": e.tag, "id": e.get("id"), "name": _text(e, "name", ""), "start": start, "end": end,
                          "in": s_in, "out": s_out, "speed": -speed if reverse else speed, "flip": flip,
                          "rate": _xml_rate(e), "generator": e.tag == "generatoritem"})
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
            if abs(it["in"] - ev.src_in) > (0 if _remap_factor(ev.speed) == 1.0 else 1):   # retimed scale rounds
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
        slack = [0 if _remap_factor(it.speed) == 1.0 else 1 for it in want_a]     # the retimed scale rounds
        if len(got_a) != len(exp_a) or any(g[:2] != w[:2] or abs(g[2] - w[2]) > s
                                           for g, w, s in zip(got_a, exp_a, slack)):
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
    for i, (c, ev) in enumerate(zip(clips, events)):
        if ev.kind == "clip":
            src = int(round(c.source_range.start_time.rescaled_to(float(raw_fps)).value))
            tail = events[i + 1].dissolve_in if i + 1 < len(events) else 0
            want = xml_in_out(int(ev.src_in), int(ev.src_in) + _src_advance(ev.speed, ev.n_rec + tail, raw_fps,
                                                                             comp_fps))[0]
            if src != want:                              # a reversed clip: its source range starts at the low end
                otio_errors.append(f"XML(otio) {ev.seg_name}: source start {src} != {want}")
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


def _remap_speed(el: ET.Element) -> float:
    """A clip item's Time Remap speed (1.0 without one), negative when its ``reverse`` flag is set."""
    speed, reverse = 1.0, False
    for eff in el.findall("filter/effect"):
        if _text(eff, "effectid") == "timeremap":
            for p in eff.findall("parameter"):
                if _text(p, "parameterid") == "speed":
                    speed = float(_text(p, "value")) / 100.0
                elif _text(p, "parameterid") == "reverse":
                    reverse = str(_text(p, "value")).upper() == "TRUE"
    return -abs(speed) if reverse else speed


def parse_premiere_xml(path: str | os.PathLike) -> dict:
    """Own re-parse of write_premiere_xml's file: sequence rate / size, tracks, clipitems, transitions, markers.
    Clip and A1 items carry the plan's source frames (``in`` / ``out``: plan_in_out) and their speed (negative:
    reverse)."""
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
            speed = _remap_speed(el)
            lo, hi = remap_decode(int(_text(el, "in")), int(_text(el, "out")), speed)
            s_in, s_out = plan_in_out(lo, hi, speed < 0)
            motion = _motion_of(el)
            motion["keys"] = {pid: [(remap_when(w, lo, speed, decode=True), v) for w, v in kf]
                              for pid, kf in (motion.get("keys") or {}).items()}
            out["clips"].append({"id": el.get("id"), "links": _link_refs(el),
                                 "name": _text(el, "name"), "label": item_label(el), "start": int(_text(el, "start")), "end": int(_text(el, "end")),
                                 "in": s_in, "out": s_out,
                                 "timebase": int(_text(el, "rate/timebase", 0)), "ntsc": _text(el, "rate/ntsc"),
                                 "speed": speed, "motion": motion,
                                 "flip": any(_is_flip(e) for e in el.findall("filter/effect"))})
        elif el.tag == "generatoritem":
            out.setdefault("generators", []).append(_text(el, "name"))
    at = seq.find("media/audio/track")
    for el in (at.findall("clipitem") if at is not None else []):
        levels = [(int(_text(k, "when")), float(_text(k, "value"))) for eff in el.findall("filter/effect")
                  if _text(eff, "effectid") == "audiolevels" for k in eff.findall("parameter/keyframe")]
        speed = _remap_speed(el)
        lo, hi = remap_decode(int(_text(el, "in")), int(_text(el, "out")), speed)
        s_in, s_out = plan_in_out(lo, hi, speed < 0)
        levels = [(remap_when(w, lo, speed, decode=True), v) for w, v in levels]
        out["audio"].append({"id": el.get("id"), "links": _link_refs(el),
                             "name": _text(el, "name"), "label": item_label(el), "start": int(_text(el, "start")), "end": int(_text(el, "end")),
                             "in": s_in, "out": s_out, "speed": speed, "levels": levels})
    for mk in seq.findall("marker"):
        out["markers"].append({"name": _text(mk, "name"), "comment": _text(mk, "comment"),
                               "in": int(_text(mk, "in")), "out": int(_text(mk, "out"))})
    return out


def _link_refs(el: ET.Element) -> list[tuple[str, str]]:
    """A clip item's <link>s: [(linkclipref, mediatype)]."""
    return [(str(_text(lk, "linkclipref", "")), str(_text(lk, "mediatype", ""))) for lk in el.findall("link")]


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


_WHOLE = re.compile(r"-?\d+")


def premiere_item_problems(xml_path: str | os.PathLike) -> list[str]:
    """The hard item check of the final XML, on its own numbers only (what Premiere's FCP translation reads): on
    every video and audio track, every clip item has whole-frame start / end / in / out, start < end and in < out,
    out - in equal to its length on the sequence x its speed (exactly at 100 %, within a frame otherwise), in / out
    inside its media's length (its <duration>), and no overlap with the items next to it. An item joined by a
    transition (start or end -1) runs from / to the transition's edge for its media and to its cut point on the
    track. Returns one line per broken item (none: []); Premiere skips such an item ("invalid start/end")."""
    root = ET.parse(str(xml_path)).getroot()
    seq = root.find("sequence")
    if seq is None:
        return ["no <sequence> in the XML"]
    seq_rate = _xml_rate(seq) or Fraction(60)
    out: list[str] = []

    def tc(f: int) -> str:
        r = int(round(float(seq_rate)))
        return f"{f // (3600 * r):02d}:{f // (60 * r) % 60:02d}:{f // r % 60:02d}:{f % r:02d}"
    heard: list[tuple[int, int, str]] = []           # every audio item: two never play at the same moment
    for kind in ("video", "audio"):
        for n, track in enumerate(seq.findall(f"media/{kind}/track"), start=1):
            label = f"{kind[0].upper()}{n}"
            els = [e for e in track if e.tag in ("clipitem", "generatoritem", "transitionitem")]
            spans: list[tuple[int, int, str]] = heard if kind == "audio" else []
            for idx, e in enumerate(els):
                if e.tag == "transitionitem":
                    continue
                name = str(_text(e, 'name', e.get('id') or '?'))
                name = name if name.split()[:1] == [item_label(e)] else f"{item_label(e)} {name}"
                raw = {k: _text(e, k) for k in ("start", "end", "in", "out")}
                bad = [k for k, v in raw.items() if v is None or not _WHOLE.fullmatch(v)]
                if bad:
                    out.append(f"{label} {name}: {', '.join(f'{k} {raw[k]!r}' for k in bad)} not a whole frame")
                    continue
                start, end, a, b = (int(raw[k]) for k in ("start", "end", "in", "out"))
                prv = els[idx - 1] if idx > 0 and els[idx - 1].tag == "transitionitem" else None
                nxt = els[idx + 1] if idx + 1 < len(els) and els[idx + 1].tag == "transitionitem" else None
                if (start == -1 and prv is None) or (end == -1 and nxt is None):
                    out.append(f"{label} {name}: start / end -1 without a transition next to it")
                    continue

                def cut(t: ET.Element) -> int:
                    s0, e0, al = int(_text(t, "start")), int(_text(t, "end")), _text(t, "alignment", "center")
                    return s0 if al in ("start", "start-black") else e0 if al in ("end", "end-black") else (s0 + e0) // 2
                s_cut = cut(prv) if start == -1 else start
                e_cut = cut(nxt) if end == -1 else end
                s_media = int(_text(prv, "start")) if start == -1 else start
                e_media = int(_text(nxt, "end")) if end == -1 else end
                where = f"{label} {name} at {tc(max(0, s_cut))}"
                if not s_cut < e_cut:
                    out.append(f"{where}: start {s_cut} is not before end {e_cut}")
                    continue
                spans.append((s_cut, e_cut, where))
                if e.tag == "generatoritem":
                    continue
                if not a < b:
                    out.append(f"{where}: in {a} is not before out {b}")
                    continue
                # FCP7 / Premiere count a retimed clip's <in> / <out> on the retimed clip (remap_encode): out - in is
                # its length on the timeline at any speed (in the clip's rate); a source-time length plays elsewhere
                speed = abs(_remap_speed(e))
                rate = _xml_rate(e) or seq_rate
                want = (e_media - s_media) * float(rate) / float(seq_rate)
                tol = 0 if rate == seq_rate else 1
                if abs((b - a) - want) > tol + 1e-6:
                    retimed = abs(speed - 1.0) > 1e-9
                    out.append(f"{where}: out - in = {b - a} {'retimed' if retimed else 'source'} frames, but it lasts "
                               f"{e_media - s_media} sequence frames at {100.0 * speed:g} % (want {want:g}"
                               + ("; in / out count on the retimed clip)" if retimed else ")"))
                dur = _text(e, "duration")
                if dur is not None and _WHOLE.fullmatch(dur) and (a < 0 or b > int(dur)):
                    out.append(f"{where}: in / out {a}-{b} outside its media (0-{int(dur)})")
                elif a < 0:
                    out.append(f"{where}: in {a} before the start of its media")
            if kind == "video":
                out += _overlaps(spans, tc, "")
    return out + _overlaps(heard, tc, " (doubled audio: two audio clips at the same moment)")


def _overlaps(spans: list[tuple[int, int, str]], tc: Any, what: str) -> list[str]:
    out = []
    spans.sort()
    for (s0, e0, w0), (s1, e1, w1) in zip(spans, spans[1:]):
        if s1 < e0:
            out.append(f"{w1}: overlaps {w0.split(' at ')[0]}, which ends at {tc(e0)}{what}")
    return out


def premiere_speech_problems(xml_path: str | os.PathLike, speech: Any) -> list[str]:
    """The hard speech check of the final XML, on its own numbers only (speech.py): no audio cut of A1 -- the start
    or end of an item where the RAW does not play on -- lands inside speech of ``speech`` (the RAW's
    speech.SpeechMap). One line per cut, with what is said there."""
    from .speech import audio_cuts, check
    x = parse_premiere_xml(xml_path)
    fps = _seq_rate(x)
    items = [dict(it, name=it.get("label") or "?") for it in x["audio"] if it["start"] >= 0]
    out = []
    for r in check(audio_cuts(items, fps, int(x["duration"])), speech, fps):
        out.append(f"A1 {r['clip']} {r['edge']}s at {_tc(int(r['at']), fps)} inside speech: RAW {r['raw_s']:.2f} s, "
                   f"sound {r['speech'][0]:.2f}-{r['speech'][1]:.2f} s ('{r['said']}')")
    return out


def _fixed_framing(c: dict) -> tuple | None:
    """A parsed V1 clip's fixed Basic Motion (scale, rotation, centre, flip) as the flash check compares it; None for
    a keyframed one (its framing moves: compared by shot only)."""
    m = c.get("motion") or {}
    if m.get("keys"):
        return None
    return (round(float(m.get("scale", 100.0)), 3), round(float(m.get("rotation", 0.0)), 3),
            tuple(round(float(v), 5) for v in m.get("center", (0.0, 0.0))), bool(c.get("flip")))


def premiere_flash_problems(xml_path: str | os.PathLike, changes_s: Sequence[float], raw_fps: Any = None) -> list[str]:
    """The hard flash check of the final XML (shots.py): every run of V1 frames showing one RAW shot (``changes_s``:
    the RAW's shot changes, s), across cuts that stay in that shot, lasts shots.MIN_SHOT_S, and none of its framings
    lasts one RAW frame (``raw_fps``) or less. A clip joined to its neighbour by a cross dissolve counts where it shows
    alone; the dissolve's own frames mix two pictures (any length). (Dropping such clips made the sequence read black
    up to the next clip: video018's S05, which starts inside a 2-frame dissolve, left S06's first frame -- the last
    frame of S05's own shot, at S05's framing -- looking like a 1-frame flash.) One line per flash."""
    from .shots import flash_problems
    x = parse_premiere_xml(xml_path)
    fps = _seq_rate(x)
    trans = sorted((int(t["start"]), int(t["end"])) for t in x["transitions"])
    items = []
    for c, (s0, e0) in zip(x["clips"], _track_ranges(x["clips"], x["transitions"])):
        a = next((t1 for t0, t1 in trans if t0 == s0), s0) if c["start"] == -1 else s0    # alone after the dissolve
        if e0 <= a:
            continue
        sp = float(c["speed"])
        base = (c["in"] + (1 if sp < 0 else 0)) if sp else c["in"]
        items.append({"label": c.get("label"), "start": a, "end": e0, "speed": sp, "in": base + (a - s0) * sp,
                      "framing": _fixed_framing(c)})
    items += [{"label": "dissolve", "start": t0, "end": t1, "speed": None, "allowed": True} for t0, t1 in trans]
    items += [{"label": "OTHER VIDEO", "start": a, "end": b, "speed": None, "allowed": True}   # filled by hand
              for a, b in other_video_ranges(x)]
    return flash_problems(items, fps, changes_s, raw_fps=raw_fps)


LINK_AUDIO_SHARE = 0.5     # a V1 clip with A1 under at least this share of it has audio (it must be linked)


def _track_ranges(items: Sequence[dict], transitions: Sequence[dict]) -> list[tuple[int, int]]:
    """The record ranges of a track's clip items, an edge inside a cross dissolve (-1) at the transition's start."""
    starts = sorted(int(t["start"]) for t in transitions)
    out = []
    for it in items:
        s0, e0 = int(it["start"]), int(it["end"])
        if s0 == -1:
            s0 = max((t for t in starts if t < e0), default=e0)
        if e0 == -1:
            e0 = min((t for t in starts if t > s0), default=s0)
        out.append((s0, e0))
    return out


def premiere_link_problems(xml_path: str | os.PathLike) -> tuple[list[str], list[str]]:
    """The hard link check of the final XML, on its own numbers (Premiere's linked clips: write_premiere_xml's
    link_pairs): every V1 clip with audio under it (A1 under at least LINK_AUDIO_SHARE of it) is linked to exactly one
    A1 clip, and every A1 clip with picture over it to exactly one V1 clip -- the same pair in both clip items, the
    two overlapping on the sequence. Returns (problems, exceptions): a V1 clip with no audio under it (a freeze, muted
    B-roll: silent on purpose) and an A1 clip under an empty V1 are left unlinked and listed. Another video's
    stretches (OTHER VIDEO) have no clip at all."""
    x = parse_premiere_xml(xml_path)
    fps = _seq_rate(x)
    vr = _track_ranges(x["clips"], x["transitions"])
    ar = [(int(a["start"]), int(a["end"])) for a in x["audio"]]
    vid = {c["id"]: i for i, c in enumerate(x["clips"])}
    aid = {a["id"]: i for i, a in enumerate(x["audio"])}
    problems, exceptions = [], []

    def name(kind: str, i: int) -> str:
        it, (s0, _) = (x["clips"][i], vr[i]) if kind == "V1" else (x["audio"][i], ar[i])
        return f"{kind} {it.get('label')} at {_tc(s0, fps)}"
    v_links = [[aid[r] for r, k in c["links"] if k == "audio" and r in aid] for c in x["clips"]]
    a_links = [[vid[r] for r, k in a["links"] if k == "video" and r in vid] for a in x["audio"]]
    for i, c in enumerate(x["clips"]):
        bad = [r for r, k in c["links"] if (k == "video" and r not in vid) or (k == "audio" and r not in aid)]
        if bad:
            problems.append(f"{name('V1', i)}: links to {', '.join(bad)}, which is no clip of V1 / A1")
        heard = sum(_overlap(*vr[i], *a) for a in ar)
        has_audio = heard >= LINK_AUDIO_SHARE * max(1, vr[i][1] - vr[i][0])
        if len(v_links[i]) == 1:
            j = v_links[i][0]
            if a_links[j] != [i]:
                problems.append(f"{name('V1', i)}: linked to {name('A1', j)}, which does not link back to it alone")
            if not _overlap(*vr[i], *ar[j]):
                problems.append(f"{name('V1', i)}: linked to {name('A1', j)}, which plays elsewhere on the sequence")
        elif v_links[i] or has_audio:
            problems.append(f"{name('V1', i)}: linked to {len(v_links[i])} A1 clips (exactly one: its audio)")
        else:
            exceptions.append(f"{name('V1', i)}: no audio under it (silent on purpose) -- not linked")
    for j, a in enumerate(x["audio"]):
        seen = any(_overlap(*v, *ar[j]) for v in vr)
        if len(a_links[j]) == 1:
            continue                                     # (checked from its V1 clip)
        if a_links[j] or seen:
            problems.append(f"{name('A1', j)}: linked to {len(a_links[j])} V1 clips (exactly one: its picture)")
        else:
            exceptions.append(f"{name('A1', j)}: under an empty V1 (nothing to link it to) -- not linked")
    return problems, exceptions


def premiere_other_video_problems(xml_path: str | os.PathLike, want: Sequence[tuple[str, int]]) -> list[str]:
    """The check of another video's stretches in the final XML (broll.py): one OTHER VIDEO marker per stretch, in
    order, exactly as long as the competitor's (``want``: [(segment, sequence frames)]), and nothing on V1 or A1
    inside it (left empty, to be filled by hand). One line per problem."""
    x = parse_premiere_xml(xml_path)
    fps = _seq_rate(x)
    got = other_video_ranges(x)
    out = []
    if len(got) != len(want):
        out.append(f"{len(got)} OTHER VIDEO marker(s), {len(want)} stretch(es) of another video in the plan")
    for (name, n), (a, b) in zip(want, got):
        if b - a != int(n):
            out.append(f"{name} at {_tc(a, fps)}: {b - a} frame(s) left for the other video, the competitor's stretch "
                       f"is {int(n)}")
        for kind, items in (("V1", x["clips"]), ("A1", x["audio"])):
            for it in items:
                s0 = it["start"] if it["start"] != -1 else it["end"] - 1
                e0 = it["end"] if it["end"] != -1 else it["start"] + 1
                if s0 < b and e0 > a:
                    out.append(f"{name} at {_tc(a, fps)}: {kind} {it.get('label')} plays inside the other video's "
                               f"stretch ({_tc(max(a, s0), fps)}-{_tc(min(b, e0), fps)}); it must stay empty")
    return out


def other_video_ranges(x: dict) -> list[tuple[int, int]]:
    """The stretches of another video in a parsed XML (parse_premiere_xml): its OTHER VIDEO markers' [in, out)."""
    return sorted((int(m["in"]), int(m["out"])) for m in x.get("markers") or []
                  if str(m.get("name") or "").startswith(OTHER_VIDEO) and int(m["out"]) > int(m["in"]))


def held_at_shot(t: float, changes_s: Sequence[float], seq_fps: float, raw_fps: Any = None) -> bool:
    """Is the edge at RAW second ``t`` one the flash guard holds shots.MIN_SHOT_S from a RAW shot change? Within 1.5
    sequence frames of it, or up to one RAW frame past it: the hold lands on the RAW's own frames."""
    from .shots import MIN_SHOT_S
    late = 1.5 / seq_fps + (1.0 / float(Fraction(raw_fps)) if raw_fps else 0.0)
    return any(-1.5 / seq_fps <= abs(t - c) - MIN_SHOT_S <= late for c in changes_s)


def premiere_silence_problems(xml_path: str | os.PathLike, speech: Any, pad_after: float, pad_before: float,
                              changes_s: Sequence[float] = (), raw_fps: Any = None,
                              kept: Sequence[tuple[int, int]] = ()) -> list[str]:
    """The silence check at the cuts of the final XML: at every audio cut of A1 (speech.audio_cuts), the silence at
    the end of the item before it plus the silence at the start of the item after it (the quiet between the RAW's
    sounds, ``speech``: its speech.SpeechMap) is at most ``pad_after`` + ``pad_before`` (+ two frames of rounding:
    cut points land on whole frames). An edge held
    shots.MIN_SHOT_S from a RAW shot change (``changes_s``: no flash frame) may keep more -- placed on the RAW's own
    frames, up to one ``raw_fps`` frame past it (video1: 0.28 s past a change, its RAW at 25 fps). One line per cut."""
    from .shots import MIN_SHOT_S
    from .speech import audio_cuts
    x = parse_premiere_xml(xml_path)
    fps = _seq_rate(x)
    f = float(fps)
    its = sorted([dict(it, name=it.get("label") or "?") for it in x["audio"] if it["start"] >= 0],
                 key=lambda d: d["start"])
    edges = {(e[1], e[3]): e for e in audio_cuts(its, fps, int(x["duration"]))}
    near_shot = lambda t: held_at_shot(t, changes_s, f, raw_fps)   # noqa: E731
    out = []
    for a, b in zip(its, its[1:]):
        if a["end"] != b["start"] or ("end", a["end"]) not in edges or ("start", b["start"]) not in edges:
            continue
        e_raw, s_raw = a["out"] / f, b["in"] / f
        a0, b1 = a["in"] / f, b["out"] / f
        last = max((s.s1 for s in speech.sounds if s.s0 < e_raw - 1e-6 and s.s1 > a0), default=a0)
        first = min((s.s0 for s in speech.sounds if s.s1 > s_raw + 1e-6 and s.s0 < b1), default=b1)
        quiet = max(0.0, e_raw - min(last, e_raw)) + max(0.0, max(first, s_raw) - s_raw)
        if any(k0 - 2 <= int(a["end"]) <= k1 + 2 for k0, k1 in kept):
            continue                         # a short action-captioned beat ends / starts here: its quiet is kept
        if quiet > pad_after + pad_before + 2.0 / f + 1e-6 and not (near_shot(e_raw) or near_shot(s_raw)):
            out.append(f"{a.get('label')} / {b.get('label')} at {_tc(int(a['end']), fps)}: {quiet:.2f} s of silence "
                       f"across the cut ({max(0.0, e_raw - min(last, e_raw)):.2f} s + "
                       f"{max(0.0, max(first, s_raw) - s_raw):.2f} s; at most {pad_after:g} + {pad_before:g} s)")
    return out


def _pads(cfg: Any) -> tuple[float, float]:
    from .silence import Settings
    st = Settings.from_cfg(cfg)
    return st.pad_after, st.pad_before


def _seq_rate(x: dict) -> Fraction:
    return (Fraction(int(x["timebase"] or 60) * 1000, 1001) if str(x["ntsc"]).upper() == "TRUE"
            else Fraction(int(x["timebase"] or 60)))


def premiere_repeat_problems(xml_path: str | os.PathLike, allow_repeats: bool = False) -> list[str]:
    """The hard repeat check of the final XML, on its own numbers only (repeats.py): no RAW frames / audio play twice
    -- no stutter at a cut, and (unless ``allow_repeats``) no RAW moment over repeats.REPEAT_S twice anywhere. A V1 /
    A1 item under a RETIME freeze marker is left out (placed at 100 % to be redone by hand). One line per repeat."""
    from .repeats import REPEAT_S, Span, check
    root = ET.parse(str(xml_path)).getroot()
    seq = root.find("sequence")
    if seq is None:
        return ["no <sequence> in the XML"]
    seq_rate = _xml_rate(seq) or Fraction(60)
    freezes = [(int(_text(m, "in")), int(_text(m, "out"))) for m in seq.findall("marker")
               if str(_text(m, "name", "")).startswith("RETIME") and "freeze" in str(_text(m, "comment", ""))]
    spans: list[Span] = []
    file_rate = {f.get("id"): float(_xml_rate(f) or 0) for f in root.iter("file") if f.find("rate") is not None}
    for kind in ("video", "audio"):
        tr = seq.find(f"media/{kind}/track")
        els = [e for e in (list(tr) if tr is not None else []) if e.tag in ("clipitem", "transitionitem")]
        for idx, e in enumerate(els):
            if e.tag != "clipitem":
                continue
            start, end, a, b = (int(float(_text(e, k, 0))) for k in ("start", "end", "in", "out"))
            dis = start == -1
            if start == -1 and idx > 0 and els[idx - 1].tag == "transitionitem":
                start = int(_text(els[idx - 1], "start"))
            if end == -1 and idx + 1 < len(els) and els[idx + 1].tag == "transitionitem":
                end = int(_text(els[idx + 1], "start"))
            if start < 0 or end <= start or b <= a or any(f0 < end and start < f1 for f0, f1 in freezes):
                continue
            v = _remap_speed(e) * float(_xml_rate(e) or seq_rate) / float(seq_rate)
            if abs(v) < 1e-9:
                continue
            a, b = remap_decode(a, b, _remap_speed(e))           # the source range (in / out count on the retimed clip)
            name = item_label(e)
            fe = e.find("file")
            spans.append(Span("V1" if kind == "video" else "A1", name, start, end, float(a if v > 0 else b), v, dis,
                              file_rate.get(fe.get("id") if fe is not None else None, 0.0) if kind == "video" else 0.0,
                              seq_fps=float(seq_rate)))
    out = []
    for d in check(spans, seq_rate, allow_repeats):
        f = float(seq_rate)
        lo, hi = d["raw"]
        what = ("a stutter at a cut" if d["kind"] == "stutter" else
                f"the same moment over {REPEAT_S:g} s twice (--allow-repeats keeps it)")
        first, second = sorted([(d["copy"][0], d["kept"]), ((d["remove"] or d["copy"])[0], d["removed"])])
        out.append(f"{d['track']} {first[1]} at {_tc(first[0], seq_rate)} and {second[1]} at "
                   f"{_tc(second[0], seq_rate)} both play RAW {lo / f:.2f}-{hi / f:.2f} s: {what}")
    return out


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
        name = item_label(ci)
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


def premiere_person_problems(xml_path: str | os.PathLike, sp: Any, cfg: Any = None,
                             spans: Sequence[tuple[float, float]] | None = None) -> tuple[list[str], list[str], dict]:
    """The hard person check of the final XML, on its own numbers: every V1 clip's framing as Premiere shows it
    (Position = sequence centre + <center> x the clip's source size, Scale, Horizontal Flip) shows the person speaking
    in the template window (speakers.shown: at most SHOWN_FRAC of the face cut off) -- or, when nobody speaks there,
    at least one person (speakers.py on ``sp``: the RAW's people, speech and shots). Returns (problems, exceptions, counts): a clip with nobody in the
    picture (B-roll, an object) or whose RAW was not analysed is listed, not failed; another video's stretch (OTHER
    VIDEO) has no clip at all and is listed too."""
    from . import speakers
    st = premiere_settings(cfg)
    W, H = st["size"]
    win = st["window"]
    root = ET.parse(str(xml_path)).getroot()
    sizes = {f.get("id") or "": (float(f.findtext("media/video/samplecharacteristics/width")),
                                 float(f.findtext("media/video/samplecharacteristics/height")))
             for f in root.iter("file") if f.findtext("media/video/samplecharacteristics/width")}
    x = parse_premiere_xml(xml_path)
    fps = _seq_rate(x)
    f = float(fps)
    seq = root.find("sequence")
    items = seq.find("media/video/track").findall("clipitem") if seq is not None else []
    problems, exceptions = [], []
    counts = {"clips": 0, "speaker": 0, "biggest face": 0, "a person": 0, "nobody": 0, "not analysed": 0}
    tol = 1.5 / f
    for el, c in zip(items, x["clips"]):
        counts["clips"] += 1
        fe = el.find("file")
        src = sizes.get(fe.get("id") if fe is not None else "", None)
        s0 = int(c["start"]) if int(c["start"]) >= 0 else int(c["end"])
        where = f"{c.get('label')} at {_tc(s0, fps)}"
        a, b = sorted((c["in"] / f, c["out"] / f))
        home = [sp_ for sp_ in spans or [] if sp_[0] - tol <= a and b <= sp_[1] + tol]
        if home:                         # a piece of a planned clip (a silence cut split it): judged as that clip
            a, b = min(home, key=lambda sp_: sp_[1] - sp_[0])
        need = sp.faces(a, b) if sp is not None else None
        if need is None:
            counts["not analysed"] += 1
            exceptions.append(f"{where}: its RAW ({a:.2f}-{b:.2f} s) was not analysed for people -- not checked")
            continue
        counts[need.how] = counts.get(need.how, 0) + 1
        if need.how == "nobody":
            exceptions.append(f"{where}: nobody in the picture (RAW {a:.2f}-{b:.2f} s) -- nothing to show, not checked")
            continue
        if src is None:
            problems.append(f"{where}: no source size in the XML, its framing cannot be checked")
            continue
        m = c["motion"]
        sc = float(m.get("scale", 100.0)) / 100.0
        ch, cv = m.get("center", (0.0, 0.0))
        px, py = W / 2.0 + ch * src[0], H / 2.0 + cv * src[1]
        sim = Sim(sc, 0.0, px - sc * src[0] / 2.0, py - sc * src[1] / 2.0)
        if abs(float(m.get("rotation", 0.0))) > 1e-6 or m.get("keys"):
            exceptions.append(f"{where}: rotated / keyframed framing -- not checked")
            continue
        if speakers.passes(sim, need, src[0], bool(c.get("flip")), win):
            continue
        t = speakers.target(sim, need, src[0], bool(c.get("flip")), win)
        r = speakers.on_screen(sim, t, src[0], bool(c.get("flip"))) if t else None
        who = {"speaker": "the person speaking", "biggest face": "the biggest face (who speaks is unclear)",
               "a person": "any person (nobody speaks)"}.get(need.how, need.how)
        if speakers.shows_anyone(sim, need, src[0], bool(c.get("flip")), win):
            exceptions.append(f"{where}: shows another person than {who} found (the competitor's choice of whom to "
                              f"show, kept) -- check" + (f": that face at x {r[0]:.0f}-{r[2]:.0f}" if r else ""))
            continue
        problems.append(f"{where}: {who} is not in the window (x {win[0]:.0f}-{win[0] + win[2]:.0f}, y "
                        f"{win[1]:.0f}-{win[1] + win[3]:.0f})" +
                        (f": face at x {r[0]:.0f}-{r[2]:.0f}, y {r[1]:.0f}-{r[3]:.0f}" if r else "") +
                        f" (who is there: RAW {a:.2f}-{b:.2f} s)")
    for a_, b_ in other_video_ranges(x):
        exceptions.append(f"OTHER VIDEO {_tc(a_, fps)}-{_tc(b_, fps)}: another video's stretch, no clip -- not checked")
    return problems, exceptions, counts


def validate_premiere_exports(cutlist: Cutlist, xml_path: str | os.PathLike, edl_path: str | os.PathLike | None,
                              cfg: Any = None, silence: Any = None, speech: Any = None,
                              shots: Sequence[float] | None = None) -> dict:
    """Re-parse the Premiere XML (and the EDL, which stays at the competitor rate) and check: the sequence is exactly
    W x H at the Premiere rate (xml_rate: ntsc TRUE for an NTSC rate only); V1 only (V2+ empty), every clip's record
    range = its event's range x
    the rate factor (cuts on the competitor's moments), source in / out / speed as planned; A1 cut exactly like V1
    (same record ranges, the same source in-point as the picture unless an audio line plays); Basic Motion covers
    the template window, keeps the competitor's framing (the window centre shows the RAW point the competitor's box
    centre shows) and zooms at most premiere_max_zoom; one marker per UNCERTAIN / NOT-IN-RAW spot. ``silence`` (the
    silence.Ripple write_premiere_xml cut out): the competitor's cuts are checked on the plan, the XML against the plan
    with those ranges removed (the sequence that much shorter, clips split and moved, A1 faded on both sides of every
    such cut, markers moved).
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
    # the hard item check: every item as Premiere's FCP translation reads it (whole frames, start < end, in < out,
    # lengths, inside its media, no overlap, never two audio clips at once); the hard repeat check: no RAW frames /
    # audio play twice (repeats.py). Both on the XML's own numbers, before anything else reads it
    try:
        bad_items = premiere_item_problems(xml_path)
    except Exception as e:  # noqa: BLE001 - an unreadable XML cannot be shown to be importable
        bad_items = [f"the item check could not read the XML: {type(e).__name__}: {e}"]
    try:
        reps = premiere_repeat_problems(xml_path, bool(getattr(cfg, "allow_repeats", False))) if not bad_items else []
    except Exception as e:  # noqa: BLE001
        reps = [f"the repeat check could not read the XML: {type(e).__name__}: {e}"]
    try:
        talk = premiere_speech_problems(xml_path, speech) if speech is not None and not bad_items else []
    except Exception as e:  # noqa: BLE001
        talk = [f"the speech check could not read the XML: {type(e).__name__}: {e}"]
    try:
        flash = (premiere_flash_problems(xml_path, shots, cutlist.raw_fps) if shots is not None and not bad_items
                 else [])
    except Exception as e:  # noqa: BLE001
        flash = [f"the flash check could not read the XML: {type(e).__name__}: {e}"]
    try:
        hush = (premiere_silence_problems(xml_path, speech, *_pads(cfg), shots or (), cutlist.raw_fps,
                                          getattr(cfg, "premiere_beats", None) or ())
                if speech is not None and not bad_items and not getattr(cfg, "keep_silence", False) else [])
    except Exception as e:  # noqa: BLE001
        hush = [f"the silence check could not read the XML: {type(e).__name__}: {e}"]
    fac0 = premiere_factor(cutlist.comp_fps, st["fps"])
    want_ov = [(ev.seg_name, (ev.rec_out - ev.rec_in) * fac0) for ev in events
               if ev.kind != "clip" and other_video_of(ev.seg) is not None]
    try:
        ov = premiere_other_video_problems(xml_path, want_ov) if not bad_items else []
        x0 = parse_premiere_xml(xml_path)
        out["other_video"] = [{"segment": name, "in": a, "out": b, "name": other_video_name(a, b, _seq_rate(x0))}
                              for (name, _), (a, b) in zip(want_ov, other_video_ranges(x0))]
    except Exception as e:  # noqa: BLE001
        ov = [f"the other-video check could not read the XML: {type(e).__name__}: {e}"]
    try:
        links, out["link_exceptions"] = premiere_link_problems(xml_path) if not bad_items else ([], [])
        xl = parse_premiere_xml(xml_path)
        out["link_counts"] = {"v1": len(xl["clips"]), "a1": len(xl["audio"]),
                              "linked": sum(1 for c in xl["clips"] if [k for _, k in c["links"]].count("audio") == 1)}
    except Exception as e:  # noqa: BLE001
        links, out["link_exceptions"] = [f"the link check could not read the XML: {type(e).__name__}: {e}"], []
    sp = _people_of(cfg)
    persons: list[str] = []
    if sp is not None and not bad_items:
        try:
            plan_spans = None
            try:                             # each planned clip's whole stretch (the pieces of one are judged on it)
                pc, _, _ = premiere_clips(cutlist, cfg, silence)
                f_ = float(premiere_settings(cfg)["fps"])
                plan_spans = [(min(c.person_span or (c.src_in, c.src_out)) / f_,
                               max(c.person_span or (c.src_in, c.src_out)) / f_) for c in pc]
            except Exception:  # noqa: BLE001 - each clip on its own then
                plan_spans = None
            persons, out["person_exceptions"], out["person_counts"] = premiere_person_problems(xml_path, sp, cfg,
                                                                                               plan_spans)
            if persons:
                log.info("premiere person check: the planned clips' stretches %s",
                         [(round(a_, 2), round(b_, 2)) for a_, b_ in plan_spans or []])
        except Exception as e:  # noqa: BLE001
            persons = [f"the person check could not read the XML: {type(e).__name__}: {e}"]
    errors += ([f"XML ITEM {b}" for b in bad_items] + [f"XML REPEAT {r}" for r in reps] +
               [f"XML SPEECH {t}" for t in talk] + [f"XML FLASH {t}" for t in flash] +
               [f"XML SILENCE {t}" for t in hush] + [f"XML OTHER VIDEO {t}" for t in ov] +
               [f"XML LINK {t}" for t in links] + [f"XML PERSON {t}" for t in persons])
    out["person_problems"] = persons if sp is not None else None
    out["other_video_problems"], out["link_problems"] = ov, links
    out["item_problems"], out["repeat_problems"], out["speech_problems"] = bad_items, reps, talk
    out["flash_problems"], out["silence_problems"] = flash, hush
    out["speech_checked"], out["flash_checked"] = speech is not None, shots is not None
    try:
        clips, markers, warnings = premiere_clips(cutlist, cfg, silence)
        plan_clips = clips
        want_a = premiere_audio(cutlist, clips, cfg) if bool(cutlist.raw.get("has_audio", True)) else []
        cut = silence is not None and silence.active
        if cut:
            from .silence import apply_premiere
            clips, want_a, markers = apply_premiere(clips, want_a, markers, silence)
        clips, _ = snap_to_shots(clips, getattr(cfg, "premiere_shots", None), st["fps"])   # as write_premiere_xml
        if st["scene_cuts"]:
            clips, _ = split_at_shots(clips, getattr(cfg, "premiere_shots", None), cutlist.raw_fps, st["fps"],
                                      premiere_factor(cutlist.comp_fps, st["fps"]))
        clips, want_a, _ = link_pairs(clips, want_a)          # as write_premiere_xml links them
        x = parse_premiere_xml(xml_path)
    except Exception as e:  # noqa: BLE001
        errors.append(f"XML: validation crashed: {type(e).__name__}: {e}")
        out.update(errors=errors, warnings=[], ok=False)
        return out
    fps, (W, H), win = st["fps"], st["size"], st["window"]
    fac = premiere_factor(cutlist.comp_fps, fps)
    raw_wh = (float(cutlist.raw["width"]), float(cutlist.raw["height"]))
    want_rate = xml_rate(fps)                    # 60 / 48 / 50 (ntsc FALSE), 59.94 / 47.952 for NTSC (ntsc TRUE)
    if want_rate is None or (x["timebase"], str(x["ntsc"]).upper()) != want_rate:
        errors.append(f"XML: sequence rate timebase {x['timebase']} ntsc {x['ntsc']} (want {fps_str(fps)} exactly: "
                      + ("not a rate FCP7 XML can state" if want_rate is None else
                         f"timebase {want_rate[0]} ntsc {want_rate[1]}") + ")")
    if (x["width"], x["height"]) != (W, H):
        errors.append(f"XML: sequence {x['width']}x{x['height']} (want {W}x{H})")
    want_n = silence.new_frames if cut else out["total_frames"] * fac
    if x["duration"] != want_n:
        errors.append(f"XML: sequence duration {x['duration']} (want {want_n})")
    for cl in plan_clips:              # the competitor's cuts, before the silences go and a cut moves onto a RAW cut
        evs = cl.events or [cl.ev]
        if (cl.rec_start, cl.rec_end) != (evs[0].rec_in * fac, evs[-1].rec_out * fac) or \
                any(a.rec_out != b.rec_in for a, b in zip(evs, evs[1:])):
            errors.append(f"XML {cl.label}: record range {cl.rec_start}-{cl.rec_end} is not the competitor cut x {fac}")
    framed = bool(str(getattr(cfg, "frame_png", "") or ""))
    if x["video_tracks"] != (2 if framed else 1):
        errors.append(f"XML: {x['video_tracks']} video tracks (" + ("V1 and the frame on V2" if framed else
                                                                     "V1 only; V2+ must stay empty") + ")")
    elif framed:
        errors += frame_track_problems(xml_path, x["duration"])
    if x["audio_tracks"] > 1:
        errors.append(f"XML: {x['audio_tracks']} audio tracks (A1 only)")
    if x.get("generators"):
        errors.append(f"XML: generator items on V1: {x['generators'][:3]}")
    if len(x["clips"]) != len(clips):
        errors.append(f"XML: {len(x['clips'])} V1 clips, expected {len(clips)}")
    trans = {t["start"]: t for t in x["transitions"]}
    for got, cl in zip(x["clips"], clips):
        name = cl.label
        s0 = got["start"] if got["start"] != -1 else (cl.rec_start if cl.rec_start in trans else None)
        if s0 != cl.rec_start or (got["start"] == -1) != (cl.start == -1):
            errors.append(f"XML {name}: start {got['start']} (want {cl.start}, record {cl.rec_start})")
        if got["end"] != cl.end:
            errors.append(f"XML {name}: end {got['end']} (want {cl.end})")
        if (got["in"], got["out"]) != xml_roundtrip(cl.src_in, cl.src_out, cl.speed, item_length(cl.start, cl.end)):
            errors.append(f"XML {name}: in/out {got['in']}/{got['out']} (want {cl.src_in}/{cl.src_out})")
        if (got["timebase"], str(got["ntsc"]).upper()) != want_rate:
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
        tick = 0 if _remap_factor(cl.speed) == 1.0 else 2          # the retimed scale rounds (in-point and offset)
        got_w, want_w = [w for w, _ in m["keys"].get("scale", [])], [w for w, _ in cl.keys]
        if m["keys"] and (len(got_w) != len(want_w) or any(abs(a - b) > tick for a, b in zip(got_w, want_w))):
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
    # --min-move: inside one RAW shot the framing changes only by min_move px or more (or to cover the window, or to
    # show the clip's person: speakers.py), and one continuous RAW take with one framing is one clip
    changes = 0
    sp = _people_of(cfg)
    if st["static"] and len(x["clips"]) == len(clips):
        from . import speakers

        def fixed(c: dict) -> Sim | None:
            m = c["motion"]
            return _sim_from_motion(m["scale"], m["rotation"], m["center"], W, H, raw_wh) \
                if all(k in m for k in ("scale", "rotation", "center")) and not m["keys"] else None

        plan_of = {id(e): c for c in plan_clips for e in (c.events or [c.ev])}

        def planned(c: PremiereClip) -> list[PremiereClip]:
            """The planned clips (before the silences were cut and the pieces of one take joined) final clip c plays."""
            out: list[PremiereClip] = []
            for e in c.events or [c.ev]:
                pc = plan_of.get(id(e))
                if pc is not None and all(pc is not x for x in out):
                    out.append(pc)
            return out

        def free_change(ca: PremiereClip, cb: PremiereClip, fa: Sim) -> bool:
            """A change under min_move is the rule's: at a shot change of the RAW, or the framing before would not
            show cb's person -- judged on the final clips, or as the plan judged it on its own clips: the silence
            removal moves clip edges afterwards (video1: a clip played on 0.28 s into the next shot of the RAW, so the
            final clips seem one shot), and the pieces it joins were framed one by one (video1: S17, 5 frames whose
            person S16's framing would not show, joined with S18)."""
            if sp is None:
                return False
            if not sp.same_shot(_last_raw_s(ca, fps), _clip_raw_s(cb, fps)[0]):
                return True
            pa, pb = planned(ca), planned(cb)
            if pa and pb and pa[-1] is not pb[0] and not sp.same_shot(_last_raw_s(pa[-1], fps),
                                                                   _clip_raw_s(pb[0], fps)[0]):
                return True
            for c in [cb] + pb:
                if not speakers.passes(fa, _person_of(c, sp, fps), raw_wh[0], bool(c.seg.flip_h), win):
                    return True
            return False
        for (ga, ca), (gb, cb) in zip(zip(x["clips"], clips), zip(x["clips"][1:], clips[1:])):
            fa, fb = fixed(ga), fixed(gb)
            if fa is None or fb is None:
                continue
            mv = framing_move(fa, fb, raw_wh)
            if mv > 0.5:
                changes += 1
                if mv < st["min_move"] - 0.5 and "changed the least to cover" not in cb.framing_note \
                        and not free_change(ca, cb, fa):
                    errors.append(f"XML {cb.label}: the framing changes by {mv:.0f} px after {ca.label} "
                                  f"(under --min-move {st['min_move']:g})")
            elif (ga["end"] != -1 and ga["end"] == gb["start"] and gb["in"] == ga["out"]
                  and _speed_ok(gb["speed"], ga["speed"]) and not (ca.link_split and cb.link_split)
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
    if len(x["audio"]) != len(want_a):
        errors.append(f"XML: {len(x['audio'])} A1 clips, expected {len(want_a)}")
    ev_ranges = ({(it["start"], it["end"]) for it in want_a} if cut else
                 {(ev.rec_in * fac, ev.rec_out * fac) for ev in events}) | {(cl.rec_start, cl.rec_end) for cl in clips} \
        | {(it["start"], it["end"]) for it in want_a if it.get("piece")}        # one take split for its link
    for got, it in zip(x["audio"], want_a):
        tick = 0 if _remap_factor(it["speed"]) == 1.0 else 2         # the retimed scale rounds (in-point and offset)
        want_src = xml_roundtrip(it["in"], it["out"], it["speed"], item_length(it["start"], it["end"]))
        if (got["start"], got["end"], got["in"], got["out"]) != (it["start"], it["end"], *want_src):
            errors.append(f"XML A1 {_seg_label(it['seg'])}: {got} (want {it['start']}-{it['end']} in {it['in']})")
        if (got["start"], got["end"]) not in ev_ranges:
            errors.append(f"XML A1 {_seg_label(it['seg'])}: range {got['start']}-{got['end']} is not a V1 cut range")
        cl = next((c for c in clips if c.rec_start <= got["start"] and got["end"] <= c.rec_end), None)
        if it["what"] == "picture" and cl is not None and not it.get("piece") and \
                abs(got["in"] - (cl.src_in + int(round((got["start"] - cl.rec_start) * cl.speed)))) > tick +                 (0 if _remap_factor(cl.speed) == 1.0 else 1):
            errors.append(f"XML A1 {_seg_label(it['seg'])}: source in {got['in']} is not V1's at that point")
        from .silence import FADE_FRAMES
        fade = ([(it["in"], 0.0), (it["in"] + FADE_FRAMES, 1.0)] if it.get("fade_in") else []) + \
            ([(it["out"] - FADE_FRAMES, 1.0), (it["out"], 0.0)] if it.get("fade_out") else [])
        got_lv, want_lv = sorted(got.get("levels") or []), sorted(fade)
        if len(got_lv) != len(want_lv) or any(abs(a[0] - b[0]) > tick or a[1] != b[1] for a, b in zip(got_lv, want_lv)):
            errors.append(f"XML A1 {_seg_label(it['seg'])} at {got['start']}: audio fades {got.get('levels')} "
                          f"(want {sorted(fade)} where a silence was cut out)")
    # every V1 clip has its audio on A1 (the XML's own A1 items), unless it was removed on purpose: listed
    out["audio_exceptions"] = []
    if not bool(cutlist.raw.get("has_audio", True)):
        out["audio_exceptions"].append("the RAW has no audio track: A1 is empty")
    else:
        heard = sorted((a["start"], a["end"]) for a in x["audio"])
        for cl in clips:
            holes, at = [], cl.rec_start
            for a0, a1 in heard:
                if a1 <= at or a0 >= cl.rec_end:
                    continue
                if a0 > at:
                    holes.append((at, a0))
                at = max(at, a1)
            if at < cl.rec_end:
                holes.append((at, cl.rec_end))
            if not holes:
                continue
            span = ", ".join(f"{_tc(a, fps)}-{_tc(b, fps)}" for a, b in holes)
            why = no_audio_reason(cl, cutlist.comp_fps)
            if why:
                out["audio_exceptions"].append(f"{cl.label} {span}: {why}")
            else:
                errors.append(f"XML A1: V1 clip {cl.label} has no audio on A1 at {span}")
    # another video's stretches (broll.py): V1 and A1 empty on purpose -- every check allows them, and lists them
    out["gap_exceptions"] = []
    for ov in out.get("other_video") or []:
        a, b = int(ov["in"]), int(ov["out"])
        tag = f"OTHER VIDEO {_tc(a, fps)}-{_tc(b, fps)} ({(b - a) / float(fps):.2f} s)"
        out["audio_exceptions"].append(f"{tag}: A1 empty on purpose -- another video's sound goes there")
        out.setdefault("link_exceptions", []).append(f"{tag}: V1 and A1 empty on purpose -- nothing to link")
        out["gap_exceptions"].append(f"{tag}: V1 empty on purpose -- no clip to cover the window, not a black flash, "
                                     "no silence cut")
    # markers: every UNCERTAIN / NOT-IN-RAW spot
    have = {(m["in"], m["out"]) for m in x["markers"]}
    for ev in events:
        if ev.seg is not None and ev.kind != "clip" and ev.seg.type in ("uncertain", "not_in_raw"):
            want_m = (ev.rec_in * fac, ev.rec_out * fac)
            if cut:
                want_m = (silence.map_hole(*want_m) if other_video_of(ev.seg) is not None else
                          (silence.map(want_m[0]), max(silence.map(want_m[0]), silence.map(want_m[1]))))
            if want_m not in have:
                errors.append(f"XML: no marker on {ev.seg_name} ({ev.seg.type}, {want_m[0]}-{want_m[1]})")
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
