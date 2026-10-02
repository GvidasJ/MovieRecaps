"""--no-broll: where the competitor cuts away while the RAW audio keeps playing, the main clip plays through.

A CUTAWAY is a piece of the competitor's edit whose picture leaves the main clip -- B-roll from another moment of the
RAW, a NOT-IN-RAW insert, an uncertain piece -- next to a MAIN-CLIP shot (a RAW segment at constant speed whose audio
follows its own picture: the analysis measured a strong audio correlation at a lag within audio_lag_tol_ms).

The main clip's time line is that shot's RAW time map extended over the cutaway (forward from the shot before it,
backward from the shot after it). When the competitor's audio under the cutaway is the RAW audio of that line -- the
same hypothesis test as the continuous audio lines of audio_align (FX-14): a peak >= verify_audio_strong_corr within
±audio_lag_tol_ms that beats every other alignment up to audio_residual_search_s, after calibrating the render lag on
the anchor shot itself -- the cutaway is replaced by the RAW video of that line, framed like the anchor, and joined to
the anchor (one continuous clip) when timing and framing continue unchanged. A NOT-IN-RAW / uncertain piece whose
audio already follows a verified audio line (FX-14) uses that line. A piece too short to measure is replaced only
between two shots of the same line. Where the audio under the cutaway does not continue (music, a voice-over, the
cutaway's own sound), the cutaway is kept exactly as the competitor has it and listed.

Only what you import changes -- recreated_edit.xml (Premiere / FCP7), the EDL and cutlist.csv; cutlist.json, the
preview renders and the verification stay faithful to the competitor (so the checks still prove every cut).
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

import numpy as np

from .model import Cutlist, Segment

JUMP_FRAMES = 2.0         # a RAW picture more than this many competitor frames off the main clip's line: a cutaway
MIN_WINDOW_S = 0.1        # audio windows shorter than this cannot be measured (bridged between two shots of the line)
EDGE_FRAMES = 2           # frames left out of the audio window at each end (J/L audio edges)
CALIBRATE_S = 1.0         # the anchor's render lag is searched within ±this (the competitor's A/V offset)


def _cfg(cfg: Any, name: str, default: float) -> float:
    v = getattr(cfg, name, None) if cfg is not None else None
    return float(default if v is None else v)


@dataclass
class Line:
    """The main clip's RAW time line: RAW seconds = raw_in + v (t - t_in) at competitor time t."""
    raw_in: float
    v: float
    t_in: float
    anchor: Segment
    source: str
    lag_s: float = 0.0              # render lag calibrated on the anchor (the competitor's audio offset there)

    def at(self, t: float) -> float:
        return self.raw_in + self.v * (t - self.t_in)


def seg_name(s: Segment) -> str:
    return f"S{int(s.id):02d}"


def is_anchor(s: Segment, strong: float, tol_ms: float) -> bool:
    """A main-clip shot: RAW, constant positive speed, and its audio follows its own picture."""
    au = s.audio or {}
    return (s.type == "raw" and not s.time_remap_keys and s.raw_in_seconds is not None and s.speed is not None
            and math.isfinite(float(s.speed)) and float(s.speed) > 0 and (s.retime or "none") == "none"
            and not au.get("line") and au.get("corr") is not None and float(au["corr"]) >= strong
            and au.get("lag_ms") is not None and abs(float(au["lag_ms"])) <= tol_ms)


def line_of(s: Segment, fps: Fraction, source: str) -> Line:
    return Line(float(s.raw_in_seconds), float(s.speed), float(Fraction(int(s.comp_in)) / fps), s, source)


def picture_off_line(s: Segment, line: Line, fps: Fraction) -> bool:
    """Does this piece's picture leave the line? (NOT-IN-RAW / uncertain always; a RAW piece when its RAW time at
    comp_in is more than JUMP_FRAMES competitor frames from the line's)."""
    if s.type in ("not_in_raw", "uncertain"):
        return True
    if s.type != "raw" or s.raw_in_seconds is None:
        return False
    t = float(Fraction(int(s.comp_in)) / fps)
    return abs(float(s.raw_in_seconds) - line.at(t)) > JUMP_FRAMES / float(fps)


def _render(line: Line, raw_y: np.ndarray, sr: int, n0: int, n1: int, lag_s: float) -> np.ndarray:
    from .audio_align import _Model
    return _Model(line.anchor, "stretch", line.raw_in, line.v, line.t_in).render(raw_y, sr, n0, n1, lag_s)


def _window(s: Segment, fps: Fraction, sr: int, n: int) -> tuple[int, int]:
    e = min(EDGE_FRAMES, max(0, (int(s.comp_out) - int(s.comp_in)) // 5))
    a = int(round(Fraction(int(s.comp_in) + e) * sr / fps))
    b = int(round(Fraction(int(s.comp_out) - e) * sr / fps))
    return max(0, a), min(n, b)


def calibrate(line: Line, comp_y: np.ndarray, raw_y: np.ndarray, sr: int, fps: Fraction) -> float | None:
    """The render lag that aligns the anchor's own audio (None when the anchor's audio cannot be measured)."""
    from .audio_align import xcorr_lag
    w0, w1 = _window(line.anchor, fps, sr, comp_y.size)
    if (w1 - w0) < int(MIN_WINDOW_S * sr):
        return None
    lag, pk = xcorr_lag(comp_y[w0:w1], _render(line, raw_y, sr, w0, w1, 0.0), sr, CALIBRATE_S)
    return float(lag) if pk > 0 else None


def verify_piece(s: Segment, line: Line, comp_y: np.ndarray, raw_y: np.ndarray, sr: int, fps: Fraction,
                 strong: float, tol_ms: float, search_s: float) -> dict:
    """{ok: True / False / None (too short to measure), corr, sidelobe, lag_ms} of the competitor's audio under piece s
    against the line: a hypothesis test -- the best alignment within ±tol must reach `strong` and beat every other
    alignment up to ±search_s."""
    from .audio_align import xcorr_lag_side
    w0, w1 = _window(s, fps, sr, comp_y.size)
    if (w1 - w0) < int(MIN_WINDOW_S * sr):
        return {"ok": None, "corr": None, "sidelobe": None, "lag_ms": None}
    lag, pk, sl = xcorr_lag_side(comp_y[w0:w1], _render(line, raw_y, sr, w0, w1, line.lag_s), sr, search_s,
                                 inner_s=tol_ms / 1000.0)
    ok = bool(pk >= strong and pk > sl and abs(lag) * 1000.0 <= tol_ms)
    return {"ok": ok, "corr": round(float(pk), 4), "sidelobe": round(float(sl), 4), "lag_ms": round(lag * 1000.0, 3)}


def _framing_at(anchor: Segment, frame: int) -> dict | None:
    """The anchor's framing nearest a competitor frame (its key there when it is animated)."""
    keys = sorted(anchor.transform_keys or [], key=lambda k: float(k["comp_frame"]))
    if keys:
        k = min(keys, key=lambda k: abs(float(k["comp_frame"]) - frame))
        return {q: k[q] for q in ("scale", "rotation_deg", "tx", "ty") if q in k}
    return copy.deepcopy(anchor.transform)


def replacement(s: Segment, line: Line, fps: Fraction, raw_fps: Fraction, evidence: dict) -> Segment:
    """The RAW video of the line over piece s, framed like the line's anchor."""
    a = line.anchor
    t0 = float(Fraction(int(s.comp_in)) / fps)
    t1 = float(Fraction(int(s.comp_out) - 1) / fps)
    r0 = line.at(t0)
    edge = int(a.comp_out) - 1 if int(a.comp_in) <= int(s.comp_in) else int(a.comp_in)
    iv = None
    if a.raw_in_interval and len(a.raw_in_interval) == 2:
        sh = line.v * float(Fraction(int(s.comp_in) - int(a.comp_in)) / fps)
        iv = [float(a.raw_in_interval[0]) + sh, float(a.raw_in_interval[1]) + sh]
    shown = ("NOT-IN-RAW" if s.type == "not_in_raw" else "an uncertain match" if s.type == "uncertain" else
             f"RAW {float(s.raw_in_seconds):.3f}s" if s.raw_in_seconds is not None else s.type)
    return Segment(
        id=int(s.id), type="raw", comp_in=int(s.comp_in), comp_out=int(s.comp_out),
        raw_in_frame=int(math.floor(r0 * float(raw_fps) + 1e-6)), raw_in_seconds=round(r0, 9),
        raw_out_frame=int(math.floor(line.at(t1) * float(raw_fps) + 1e-6)), speed=line.v, speed_measured=line.v,
        flip_h=bool(a.flip_h), transform=_framing_at(a, edge), transform_keys=[], easing="linear",
        time_remap_keys=[], transition_in=copy.deepcopy(s.transition_in), transition_out=copy.deepcopy(s.transition_out),
        audio={"in_offset_frames": 0, "out_offset_frames": 0, "pitch_preserved": None,
               "lag_ms": evidence.get("lag_ms"), "corr": evidence.get("corr"), "exception": None, "line": None,
               "broll": {"replaced": shown, "line": line.source, "ranges": [[int(s.comp_in), int(s.comp_out), int(s.id)]]}},
        time_mode="stretch", retime="none", region=int(a.region), box=copy.deepcopy(a.box),
        confidence=float(a.confidence), raw_in_interval=iv, label=f"B-ROLL REPLACED {seg_name(s)}",
        notes=f"--no-broll: the competitor showed {shown} here over the main clip's continuing RAW audio "
              f"({line.source}); the main clip plays through")


def _same_framing(a: Segment, b: Segment) -> bool:
    if a.transform_keys or b.transform_keys or bool(a.flip_h) != bool(b.flip_h) or a.box != b.box:
        return False
    ta, tb = a.transform or {}, b.transform or {}
    return all(abs(float(ta.get(k, 0.0)) - float(tb.get(k, 0.0))) <= 1e-6 for k in ("scale", "rotation_deg", "tx", "ty"))


def _continues(a: Segment, b: Segment, fps: Fraction) -> bool:
    """b plays on a's time line right after it (same speed, RAW time within a quarter frame)."""
    if a.type != "raw" or b.type != "raw" or a.time_remap_keys or b.time_remap_keys or a.speed is None \
            or b.speed is None or abs(float(a.speed) - float(b.speed)) > 1e-9 or a.raw_in_seconds is None \
            or b.raw_in_seconds is None or int(a.comp_out) != int(b.comp_in):
        return False
    want = float(a.raw_in_seconds) + float(a.speed) * float(Fraction(int(b.comp_in) - int(a.comp_in)) / fps)
    return abs(float(b.raw_in_seconds) - want) <= 0.25 / float(fps)


def join(segs: list[Segment], replaced_ids: set[int], fps: Fraction) -> list[Segment]:
    """Join a replaced piece with its neighbours on the same line (one continuous clip) when the framing and
    speed continue unchanged and no transition sits between them."""
    out: list[Segment] = []
    for s in segs:
        p = out[-1] if out else None
        if (p is not None and (int(p.id) in replaced_ids or int(s.id) in replaced_ids or
                               (p.audio or {}).get("broll") or (s.audio or {}).get("broll"))
                and _continues(p, s, fps) and _same_framing(p, s) and not p.transition_out and not s.transition_in):
            p.comp_out = int(s.comp_out)
            p.raw_out_frame = s.raw_out_frame
            p.transition_out = copy.deepcopy(s.transition_out)
            pa, sa = p.audio or {}, s.audio or {}
            p.audio = dict(pa, out_offset_frames=int(sa.get("out_offset_frames") or 0))
            merged = list((pa.get("broll") or {}).get("joined") or [int(p.id)]) + \
                list((sa.get("broll") or {}).get("joined") or [int(s.id)])
            ranges = list((pa.get("broll") or {}).get("ranges") or []) + list((sa.get("broll") or {}).get("ranges") or [])
            p.audio["broll"] = dict(pa.get("broll") or {}, joined=merged, ranges=ranges)
            p.label = p.label if p.label.startswith("B-ROLL") else s.label
            p.notes = "; ".join(x for x in (p.notes, s.notes) if x)
            continue
        out.append(s)
    return out


def apply_no_broll(cutlist: Cutlist, comp_y: np.ndarray | None, raw_y: np.ndarray | None, sr: int,
                   cfg: Any = None) -> dict:
    """{cutlist: the export cut list with the verified cutaways replaced (a copy; the input is untouched),
    replaced: [...], kept: [...], notes: [...]}. Every listed cutaway carries its competitor frames [comp_in,
    comp_out), what the competitor showed and the audio evidence."""
    fps, raw_fps = cutlist.comp_fps, cutlist.raw_fps
    strong = _cfg(cfg, "verify_audio_strong_corr", 0.8)
    tol_ms = _cfg(cfg, "audio_lag_tol_ms", 10.0)
    search_s = _cfg(cfg, "audio_residual_search_s", 0.1)
    out_cl = copy.deepcopy(cutlist)
    segs = sorted(out_cl.segments, key=lambda s: int(s.comp_in))
    res: dict = {"cutlist": out_cl, "replaced": [], "kept": [], "notes": []}
    have_audio = comp_y is not None and raw_y is not None and len(comp_y) and len(raw_y)
    if not have_audio:
        res["notes"].append("no competitor or RAW audio: cutaways cannot be checked, nothing replaced")
    comp_y = np.asarray(comp_y if have_audio else np.zeros(0), np.float32)
    raw_y = np.asarray(raw_y if have_audio else np.zeros(0), np.float32)
    anchors = {int(s.id) for s in segs if is_anchor(s, strong, tol_ms)}
    by_comp_in = {int(s.comp_in): s for s in segs}
    lines: dict[int, Line] = {}

    def anchor_line(a: Segment, back: bool) -> Line | None:
        key = (int(a.id) << 1) | int(back)
        if key not in lines:
            ln = line_of(a, fps, f"{seg_name(a)} continued" + (" back" if back else ""))
            lag = calibrate(ln, comp_y, raw_y, sr, fps) if have_audio else None
            if lag is None:
                lines[key] = None
            else:
                ln.lag_s = lag
                lines[key] = ln
        return lines[key]

    replaced: dict[int, Segment] = {}
    i, n = 0, len(segs)
    while i < n:
        if int(segs[i].id) in anchors:
            i += 1
            continue
        j = i
        while j + 1 < n and int(segs[j + 1].id) not in anchors and int(segs[j + 1].comp_in) == int(segs[j].comp_out):
            j += 1
        region = segs[i:j + 1]
        A = segs[i - 1] if i > 0 and int(segs[i - 1].id) in anchors and \
            int(segs[i - 1].comp_out) == int(region[0].comp_in) else None
        B = segs[j + 1] if j + 1 < n and int(segs[j + 1].id) in anchors and \
            int(segs[j + 1].comp_in) == int(region[-1].comp_out) else None
        fwd = anchor_line(A, False) if A is not None else None
        bwd = anchor_line(B, True) if B is not None else None
        geo = [line_of(x, fps, "") for x in (A, B) if x is not None]     # the main clip's line(s), picture only
        same_line = A is not None and B is not None and _continues_line(A, B, fps)
        for s in region:
            if s.type not in ("raw", "not_in_raw", "uncertain"):
                continue                                   # dips / flashes: transitions, not B-roll
            if not geo:
                if s.type in ("not_in_raw", "uncertain"):
                    res["kept"].append(_row(s, fps, "no main-clip shot right before or after it", None))
                continue
            if not all(picture_off_line(s, g, fps) for g in geo):
                continue                                   # the main clip itself (a retime / effect): not a cutaway
            ev, used = _check(s, fwd, bwd, comp_y, raw_y, sr, fps, strong, tol_ms, search_s) if have_audio else \
                ({"ok": False, "corr": None, "lag_ms": None, "sidelobe": None}, None)
            fx14 = (s.audio or {}).get("line") if s.type in ("not_in_raw", "uncertain") else None
            if used is None and fx14 and have_audio:
                used = _fx14_line(s, fx14, by_comp_in, A, B, fps)
                ev = {"ok": True, "corr": fx14.get("corr"), "lag_ms": fx14.get("lag_ms"), "sidelobe": None}
            if used is None and ev.get("ok") is None and same_line and fwd is not None:
                used, ev = fwd, dict(ev, bridged=True)    # too short to hear: between two shots of the same line
            if used is None:
                why = ("its audio could not be checked (no audio)" if not have_audio else
                       "the main-clip shot next to it is too short to measure its audio" if fwd is None and bwd is None
                       else "too short to hear whether the RAW audio continues" if ev.get("ok") is None else
                       f"the competitor's audio under it is not the main clip's RAW audio continuing (best corr "
                       f"{ev.get('corr')}): music / voice-over / its own sound -- left as the competitor has it")
                res["kept"].append(_row(s, fps, why, ev))
                continue
            new = replacement(s, used, fps, raw_fps, ev)
            replaced[int(s.id)] = new
            res["replaced"].append(dict(_row(s, fps, None, ev), line=used.source, raw_in_seconds=new.raw_in_seconds,
                                        raw_out_seconds=round(used.at(float(Fraction(int(s.comp_out)) / fps)), 6),
                                        bridged=bool(ev.get("bridged"))))
        i = j + 1
    if replaced:
        new_segs = [replaced.get(int(s.id), s) for s in segs]
        _clear_edges(new_segs, set(replaced), fps)
        out_cl.segments = join(new_segs, set(replaced), fps)
        res["notes"].append(f"{len(replaced)} cutaway(s) replaced by the main clip; {len(out_cl.segments)} segments "
                            f"in the export (was {len(segs)})")
    return res


def _continues_line(a: Segment, b: Segment, fps: Fraction) -> bool:
    """Shot b is on shot a's time line (a jump hidden under the cutaway is not)."""
    if abs(float(a.speed) - float(b.speed)) > 1e-9:
        return False
    want = float(a.raw_in_seconds) + float(a.speed) * float(Fraction(int(b.comp_in) - int(a.comp_in)) / fps)
    return abs(float(b.raw_in_seconds) - want) <= JUMP_FRAMES / float(fps)


def _check(s: Segment, fwd: Line | None, bwd: Line | None, comp_y, raw_y, sr, fps, strong, tol_ms,
           search_s) -> tuple[dict, Line | None]:
    """(evidence, line) for piece s: the verified forward / backward line with the higher peak; else (the best
    failed evidence, None), or ({ok: None}, None) when the piece is too short to measure."""
    tried = [(verify_piece(s, ln, comp_y, raw_y, sr, fps, strong, tol_ms, search_s), ln) for ln in (fwd, bwd)
             if ln is not None]
    ok = [t for t in tried if t[0]["ok"]]
    if ok:
        return max(ok, key=lambda t: float(t[0]["corr"]))
    measured = [t[0] for t in tried if t[0]["ok"] is not None]
    if measured:
        return max(measured, key=lambda e: float(e["corr"])), None
    return {"ok": None, "corr": None, "lag_ms": None, "sidelobe": None}, None


def _fx14_line(s: Segment, fx14: dict, by_comp_in: dict, A: Segment | None, B: Segment | None,
               fps: Fraction) -> Line:
    """The verified FX-14 audio line of a NOT-IN-RAW / uncertain piece, anchored (for the framing) on the shot it
    continues."""
    try:
        anchor = by_comp_in.get(int(fx14.get("id")))
    except (TypeError, ValueError):
        anchor = None
    anchor = anchor if anchor is not None and anchor.type == "raw" else (A or B)
    t = float(Fraction(int(s.comp_in)) / fps)
    return Line(float(fx14["raw_in_seconds"]), float(fx14.get("speed") or 1.0), t, anchor,
                f"audio line ({fx14.get('source') or 'continuous RAW audio'})")


def _clear_edges(segs: list[Segment], replaced: set[int], fps: Fraction) -> None:
    """Where a replaced piece meets a shot of its own line, the boundary is no cut any more: no transition, no J/L."""
    for a, b in zip(segs, segs[1:]):
        if (int(a.id) in replaced or int(b.id) in replaced) and _continues(a, b, fps):
            a.transition_out = None
            b.transition_in = None
            a.audio = dict(a.audio or {}, out_offset_frames=0)
            b.audio = dict(b.audio or {}, in_offset_frames=0)


def _row(s: Segment, fps: Fraction, why: str | None, ev: dict | None) -> dict:
    shown = ("NOT-IN-RAW insert" if s.type == "not_in_raw" else "uncertain picture" if s.type == "uncertain" else
             f"RAW {float(s.raw_in_seconds):.3f}s" if s.raw_in_seconds is not None else s.type)
    row = {"segment": int(s.id), "comp_in": int(s.comp_in), "comp_out": int(s.comp_out), "showed": shown,
           "corr": (ev or {}).get("corr"), "lag_ms": (ev or {}).get("lag_ms")}
    if why:
        row["why"] = why
    return row
