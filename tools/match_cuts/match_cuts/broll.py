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
    heard: bool = False             # placed where the competitor's sound plays (its picture's time is av_offset away)

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
    on_line = a.raw_in_seconds is not None and not a.time_remap_keys and abs(
        line.at(float(Fraction(int(a.comp_in)) / fps)) - float(a.raw_in_seconds)) < 1e-6
    if on_line and a.raw_in_interval and len(a.raw_in_interval) == 2:
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
               "broll": {"replaced": shown, "line": line.source, "ranges": [[int(s.comp_in), int(s.comp_out), int(s.id)]],
                         **({"heard": True} if line.heard else {})}},
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
                and _continues(p, s, fps) and _same_framing(p, s) and not p.transition_out and not s.transition_in
                and bool((p.audio or {}).get("mute")) == bool((s.audio or {}).get("mute"))):
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
                   cfg: Any = None, follow_audio: bool = False, hints: Any = None, speech_of: Any = None) -> dict:
    """{cutlist: the export cut list with the verified cutaways replaced (a copy; the input is untouched),
    replaced: [...], kept: [...], other_video: [...], notes: [...]}. Every listed cutaway carries its competitor
    frames [comp_in, comp_out), what the competitor showed and the audio evidence.

    ``speech_of(t0, t1)`` -> the words [(text, start s, end s, prob)] the competitor's audio says in [t0, t1)
    (competitor seconds), or None when it cannot be transcribed. With ``follow_audio``, a NOT-IN-RAW stretch with no
    RAW audio under it whose competitor audio has speech (is_speech) comes from another video I was not given: OTHER
    VIDEO -- left a NOT-IN-RAW piece (V1 and A1 stay empty there, marked), never filled with the clip before.

    ``follow_audio`` (the --premiere default): every NOT-IN-RAW / uncertain / dip / flash spot and every B-roll piece
    inside one continuous main-clip shot is filled -- V1 is never left empty: with the RAW video of the audio playing
    there (a neighbour's line, an FX-14 audio line, or the RAW moment the audio alignment found for it, verified by
    correlation), else, when the audio there is not from the RAW (music / voice-over), the previous RAW clip keeps
    playing, with no RAW audio under it."""
    fps, raw_fps = cutlist.comp_fps, cutlist.raw_fps
    strong = _cfg(cfg, "verify_audio_strong_corr", 0.8)
    tol_ms = _cfg(cfg, "audio_lag_tol_ms", 10.0)
    search_s = _cfg(cfg, "audio_residual_search_s", 0.1)
    out_cl = copy.deepcopy(cutlist)
    segs = sorted(out_cl.segments, key=lambda s: int(s.comp_in))
    res: dict = {"cutlist": out_cl, "replaced": [], "kept": [], "other_video": [], "slipped": [], "notes": []}
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

    replaced: dict[int, list[Segment]] = {}          # original segment id -> its replacement piece(s)
    used_line: dict[int, Line] = {}                   # ... the line it follows (verified)
    short_ids: set[int] = set()                       # pieces too short to hear whether the RAW audio continues
    pending: list[tuple[Segment, Segment | None, Segment | None, bool]] = []
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
                if follow_audio and s.type in ("dip", "flash"):
                    pending.append((s, A, B, False))       # V1 is never left empty
                continue                                   # dips / flashes: transitions, not B-roll
            if not geo:
                if follow_audio and s.type in ("not_in_raw", "uncertain"):
                    pending.append((s, A, B, False))
                    continue
                if s.type in ("not_in_raw", "uncertain"):
                    res["kept"].append(_row(s, fps, "no main-clip shot right before or after it", None))
                continue
            if not all(picture_off_line(s, g, fps) for g in geo):
                new = slip_onto_line(s, by_comp_in, A, B, fps, raw_fps) if follow_audio else None
                if new is not None:                        # its picture a frame or two off its sound: on its sound
                    replaced[int(s.id)] = [new]
                    res["slipped"].append({"segment": int(s.id), "comp_in": int(s.comp_in),
                                           "comp_out": int(s.comp_out), "line": (s.audio or {})["line"].get("source"),
                                           "picture_ms": round(1000.0 * (float(s.raw_in_seconds)
                                                                         - float(new.raw_in_seconds)), 1),
                                           "raw_in_seconds": new.raw_in_seconds})
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
                if ev.get("ok") is None:
                    short_ids.add(int(s.id))
                if follow_audio:
                    pending.append((s, A, B, same_line))
                    continue
                why = ("its audio could not be checked (no audio)" if not have_audio else
                       "the main-clip shot next to it is too short to measure its audio" if fwd is None and bwd is None
                       else "too short to hear whether the RAW audio continues" if ev.get("ok") is None else
                       f"the competitor's audio under it is not the main clip's RAW audio continuing (best corr "
                       f"{ev.get('corr')}): music / voice-over / its own sound -- left as the competitor has it")
                res["kept"].append(_row(s, fps, why, ev))
                continue
            new = replacement(s, used, fps, raw_fps, ev)
            replaced[int(s.id)] = [new]
            used_line[int(s.id)] = used
            res["replaced"].append(dict(_row(s, fps, None, ev), line=used.source, raw_in_seconds=new.raw_in_seconds,
                                        raw_out_seconds=round(used.at(float(Fraction(int(s.comp_out)) / fps)), 6),
                                        bridged=bool(ev.get("bridged")), how="audio"))
        # a piece too short to hear between two pieces replaced by the same line: that line too, as between two shots
        # of one line (video4: 7 frames of another RAW moment between two cutaways over S10's continuing speech)
        for x, y, z in zip(region, region[1:], region[2:]):
            lx = used_line.get(int(x.id))
            if int(y.id) not in short_ids or int(y.id) in replaced or lx is None or used_line.get(int(z.id)) is not lx:
                continue
            ev = {"ok": None, "corr": None, "lag_ms": None, "sidelobe": None, "bridged": True}
            new = replacement(y, lx, fps, raw_fps, ev)
            replaced[int(y.id)] = [new]
            used_line[int(y.id)] = lx
            pending = [q for q in pending if int(q[0].id) != int(y.id)]
            res["replaced"].append(dict(_row(y, fps, None, ev), line=lx.source, raw_in_seconds=new.raw_in_seconds,
                                        raw_out_seconds=round(lx.at(float(Fraction(int(y.comp_out)) / fps)), 6),
                                        bridged=True, how="audio"))
        i = j + 1
    if follow_audio:
        _follow_audio(segs, pending, replaced, res, comp_y, raw_y, sr, fps, raw_fps, strong, hints, have_audio,
                      by_comp_in, cutlist, speech_of)
    if replaced:
        new_segs = [x for s in segs for x in replaced.get(int(s.id), [s])]
        new_ids = {int(x.id) for v in replaced.values() for x in v}
        _clear_edges(new_segs, new_ids, fps)
        out_cl.segments = join(new_segs, new_ids, fps)
        n_rep = len(replaced) - len(res["slipped"])
        if n_rep:
            res["notes"].append(f"{n_rep} cutaway(s) replaced by the main clip; {len(out_cl.segments)} segments in "
                                f"the export (was {len(segs)})")
    if res["slipped"]:
        res["notes"].append(
            f"{len(res['slipped'])} piece(s) whose sound continues another clip play at the RAW time of that sound, "
            "their own framing kept (their picture ran a frame or two off it; played as it was, the picture would "
            "repeat or skip frames where the sound plays on): "
            + ", ".join(f"S{r['segment']:02d} ({r['picture_ms']:+.0f} ms, {r['line']})" for r in res["slipped"]))
    if res["other_video"]:
        res["notes"].append(f"{len(res['other_video'])} stretch(es) of another video (NOT-IN-RAW, speech not in the "
                            "RAW): left empty and marked")
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


# ---------------------------------------------------------------------------------------------
# follow_audio (--premiere default): every remaining spot shows the RAW video of its audio, or the previous clip
# ---------------------------------------------------------------------------------------------

HINT_CONF = 1.5           # audio-alignment windows trusted to say where the audio sits in the RAW ...
HINT_SPREAD_S = 0.1       # ... when they agree on one RAW line within this
UNIQUE_MARGIN = 0.1       # a line found by search must beat every other alignment by this


def _broll_sound(s: Segment, strong: float, tol_ms: float) -> bool:
    """A RAW piece whose sound was measured and is not its own (the competitor played something else over it)."""
    return s.type == "raw" and (s.audio or {}).get("corr") is not None and not is_anchor(s, strong, tol_ms)


def hint_line(s: Segment, hints: Any, fps: Fraction) -> Line | None:
    """The RAW line the competitor's audio follows under piece s according to the audio alignment (S5.1): its
    confident windows inside the piece agree on one RAW offset at speed 1."""
    if hints is None or getattr(hints, "comp_t", None) is None:
        return None
    t0, t1 = float(Fraction(int(s.comp_in)) / fps), float(Fraction(int(s.comp_out)) / fps)
    half = 0.5 * float(getattr(hints, "window", 1.0))
    ct = np.asarray(hints.comp_t, float)
    ok = np.asarray(hints.confident(HINT_CONF)) & (np.abs(np.asarray(hints.speed, float) - 1.0) < 0.02)
    # windows mostly inside the piece (a window is 1 s: pieces shorter than half of it have none)
    inside = ok & (ct - half >= t0 - 0.5 * half) & (ct + half <= t1 + 0.5 * half)
    if not inside.any():
        return None
    off = np.asarray(hints.raw_t, float)[inside] - ct[inside]
    med = float(np.median(off))
    if float(np.max(np.abs(off - med))) > HINT_SPREAD_S:
        return None
    return Line(t0 + med, 1.0, t0, s, f"audio found at RAW {t0 + med:.3f}s", heard=True)


def _keeps_playing(prev: Segment | None, nxt: Segment | None, s: Segment, fps: Fraction,
                   raw_len_s: float | None) -> Line | None:
    """The previous RAW clip continued over piece s (or, with none before it or past the RAW's end, the next one
    played back into it), framed like that clip."""
    def line_from(c: Segment, src: str) -> Line | None:
        if c is None or c.type != "raw" or c.raw_in_seconds is None:
            return None
        if c.time_remap_keys:
            ks = sorted(c.time_remap_keys, key=lambda d: float(d["comp_frame"]))
            k = ks[-1] if int(c.comp_in) <= int(s.comp_in) else ks[0]
            return Line(float(k["raw_seconds"]), 1.0, float(Fraction(int(round(float(k["comp_frame"])))) / fps), c,
                        src)
        return Line(float(c.raw_in_seconds), float(c.speed or 1.0), float(Fraction(int(c.comp_in)) / fps), c, src)
    t0, t1 = float(Fraction(int(s.comp_in)) / fps), float(Fraction(int(s.comp_out)) / fps)
    for cand in (line_from(prev, f"{seg_name(prev)} keeps playing" if prev is not None else ""),
                 line_from(nxt, f"{seg_name(nxt)} played into it" if nxt is not None else "")):
        if cand is None:
            continue
        lo, hi = min(cand.at(t0), cand.at(t1)), max(cand.at(t0), cand.at(t1))
        if lo >= 0 and (raw_len_s is None or hi <= raw_len_s):
            return cand
    return None


BROLL_GAP_S = 1.0         # follow_audio: a RAW piece is B-roll only when its picture is this far from the RAW of its audio
RUN_CORR = 0.6            # a found audio run is kept when the competitor's audio follows it this well (music may lie under)
RUN_CONF = 1.3            # audio-alignment windows used to find the runs under a cutaway
MUTE_MIN_S = 0.5          # a spot this long with no RAW audio found has music / voice-over: no RAW audio under it
OWN_SOUND_MS = 100.0      # a RAW piece whose sound is its own picture's RAW (corr >= strong) this near: the main clip
#                           (an anchor needs it within audio_lag_tol_ms; video4's S27 was 22 ms off)
OTHER_MIN_S = 0.25        # a NOT-IN-RAW stretch this long or longer can be another video (shots.MIN_SHOT_S)
OTHER_MIN_WORDS = 2       # ... when the competitor's audio says at least this many words there ...
OTHER_MIN_SPEECH_S = 0.3  # ... spoken over at least this long
OTHER_MIN_PROB = 0.4      # ... heard this surely (median word probability: lyrics / noise read as words are unsure)


def is_speech(words: Any) -> bool:
    """Do these words [(text, start, end, prob)] make speech (not a word or two misheard in music / noise)?"""
    ws = [w for w in words or [] if any(ch.isalnum() for ch in str(w[0]))]
    if len(ws) < OTHER_MIN_WORDS or sum(max(0.0, float(w[2]) - float(w[1])) for w in ws) < OTHER_MIN_SPEECH_S:
        return False
    return float(np.median([float(w[3]) if len(w) > 3 else 1.0 for w in ws])) >= OTHER_MIN_PROB


def other_video_label(comp_in: int, comp_out: int, fps: Fraction) -> str:
    from .common import timecode
    return f"OTHER VIDEO \u2013 not in RAW ({timecode(comp_in, fps)}\u2013{timecode(comp_out, fps)})"


def other_video_piece(s: Segment, ka: int, kb: int, words: Any, fps: Fraction, seg_id: int) -> Segment:
    """The NOT-IN-RAW piece [ka, kb) of s that shows another video: no picture, no RAW audio (V1 / A1 stay empty)."""
    ws = [[str(w[0]), round(float(w[1]), 3), round(float(w[2]), 3), round(float(w[3]) if len(w) > 3 else 1.0, 3)]
          for w in words or []]
    return Segment(id=seg_id, type="not_in_raw", comp_in=ka, comp_out=kb, confidence=float(s.confidence or 0.0),
                   region=int(s.region), box=copy.deepcopy(s.box),
                   audio={"in_offset_frames": 0, "out_offset_frames": 0, "pitch_preserved": None, "lag_ms": None,
                          "corr": None, "exception": None, "line": None,
                          "other_video": {"comp_in": ka, "comp_out": kb, "words": ws, "segment": int(s.id)}},
                   label=other_video_label(ka, kb, fps),
                   notes=(f"the competitor shows another video here: its speech (\"{' '.join(w[0] for w in ws)}\") is "
                          "not in the RAW -- V1 and A1 left empty for exactly its length"))


def audio_runs(s: Segment, hints: Any, fps: Fraction, comp_y: np.ndarray, raw_y: np.ndarray, sr: int
               ) -> list[tuple[int, int, float]] | None:
    """Where the competitor's audio under piece s sits in the RAW, as runs [(comp_in, comp_out, RAW - comp offset s)]
    at speed 1: the audio alignment's confident windows grouped by offset (an editor who cut pauses out of the audio
    under a cutaway gives several runs), each switch placed on the frame where the audio fits the next offset better
    (frame-by-frame correlation), every run then checked by correlation. None when nothing is found."""
    if hints is None or getattr(hints, "comp_t", None) is None or comp_y.size == 0 or raw_y.size == 0:
        return None
    a, b = int(s.comp_in), int(s.comp_out)
    t0, t1 = float(Fraction(a) / fps), float(Fraction(b) / fps)
    ct = np.asarray(hints.comp_t, float)
    # windows straddling an audio cut read a little less sure and a little off speed: accepted here, every run is
    # checked by correlation below
    ok = np.asarray(hints.confident(RUN_CONF)) & (np.abs(np.asarray(hints.speed, float) - 1.0) <= 0.05)
    sel = np.nonzero(ok & (ct >= t0 - 0.3) & (ct <= t1 + 0.3))[0]
    if not len(sel):
        return None
    offs = np.asarray(hints.raw_t, float)[sel] - ct[sel]
    clusters: list[list[int]] = [[0]]
    for i in range(1, len(sel)):
        if abs(offs[i] - float(np.median(offs[clusters[-1]]))) <= 0.05:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    # a run needs a window centred inside the piece; the windows just outside only extend the runs at its edges
    inside = [c for c in clusters if any(t0 + 0.1 <= ct[sel[i]] <= t1 - 0.1 for i in c)]
    if not inside:
        return None
    clusters = inside
    lines = [(float(np.median(offs[c])), float(ct[sel[c[0]]]), float(ct[sel[c[-1]]])) for c in clusters]
    spf = float(sr) / float(fps)

    def fit(k: int, off: float) -> float:
        n0, n1 = int(round(k * spf)), int(round((k + 1) * spf))
        if n1 > comp_y.size:
            return 0.0
        r0 = int(round((k / float(fps) + off) * sr))
        if r0 < 0 or r0 + (n1 - n0) > raw_y.size:
            return 0.0
        x, y = comp_y[n0:n1].astype(np.float64), raw_y[r0:r0 + (n1 - n0)].astype(np.float64)
        x, y = x - x.mean(), y - y.mean()
        d = float(np.sqrt((x * x).sum() * (y * y).sum()))
        return float((x * y).sum() / d) if d > 0 else 0.0
    cuts = [a]
    for (oa, _, ea), (ob, sb, _) in zip(lines, lines[1:]):
        lo = max(cuts[-1] + 1, int(math.floor(ea * float(fps))))
        hi = min(b - 1, int(math.ceil(sb * float(fps))))
        if hi < lo:
            lo, hi = hi, lo
        lo, hi = max(cuts[-1] + 1, lo), max(cuts[-1] + 1, min(b - 1, hi))
        fa = [fit(k, oa) for k in range(lo, hi + 1)]
        fb = [fit(k, ob) for k in range(lo, hi + 1)]
        best = max(range(len(fa) + 1), key=lambda m: sum(fa[:m]) + sum(fb[m:]))
        cuts.append(min(b - 1, lo + best))
    cuts.append(b)
    runs = [(cuts[i], cuts[i + 1], lines[i][0]) for i in range(len(lines)) if cuts[i + 1] > cuts[i]]
    return runs or None


def _check_run(k0: int, k1: int, off: float, fps: Fraction, comp_y, raw_y, sr: int) -> tuple[bool | None, float, float]:
    """(ok | None when shorter than MIN_WINDOW_S, peak, lag s) of the competitor's audio over frames [k0, k1)
    against RAW at the offset."""
    from .audio_align import xcorr_lag_side
    w0, w1 = int(round(Fraction(k0) * sr / fps)), min(comp_y.size, int(round(Fraction(k1) * sr / fps)))
    if w1 - w0 < int(MIN_WINDOW_S * sr):
        return None, 0.0, 0.0
    r0 = int(round(w0 + off * sr))
    if r0 < 0 or r0 + (w1 - w0) > raw_y.size:
        return False, 0.0, 0.0
    lag, pk, sl = xcorr_lag_side(comp_y[w0:w1], raw_y[r0:r0 + (w1 - w0)], sr, 0.05, inner_s=0.02)
    return bool(pk >= RUN_CORR and pk > sl), float(pk), float(lag)


def _follow_audio(segs: list[Segment], pending: list, replaced: dict[int, list[Segment]], res: dict, comp_y, raw_y,
                  sr: int, fps: Fraction, raw_fps: Fraction, strong: float, hints: Any, have_audio: bool,
                  by_comp_in: dict, cutlist: Cutlist, speech_of: Any = None) -> None:
    raw_len = None
    try:
        raw_len = float(cutlist.raw.get("frames")) / float(raw_fps)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    order = {int(x.id): i for i, x in enumerate(segs)}
    next_id = max([int(x.id) for x in segs] + [0]) + 1

    def flat_before(k: int) -> list[Segment]:
        return [y for x in segs[:k] for y in replaced.get(int(x.id), [x])]

    def flat_after(k: int) -> list[Segment]:
        return [y for x in segs[k + 1:] for y in replaced.get(int(x.id), [x])]

    for s, A, B, same_line in sorted(pending, key=lambda t: int(t[0].comp_in)):
        k = order[int(s.id)]
        t0 = float(Fraction(int(s.comp_in)) / fps)
        pieces: list[tuple[int, int, Line, dict, str]] = []          # (comp_in, comp_out, line, evidence, how)
        fx14 = (s.audio or {}).get("line") if s.type in ("not_in_raw", "uncertain") or other_clips_line(s) else None
        if fx14 and have_audio:
            ln = _fx14_line(s, fx14, by_comp_in, A, B, fps)
            if ln.anchor is not None:
                pieces = [(int(s.comp_in), int(s.comp_out), ln,
                           {"ok": True, "corr": fx14.get("corr"), "lag_ms": fx14.get("lag_ms")}, "audio")]
        if not pieces and have_audio:
            runs = audio_runs(s, hints, fps, comp_y, raw_y, sr)
            if runs:
                if s.type == "raw" and s.raw_in_seconds is not None and all(
                        abs(float(s.raw_in_seconds) + float(s.speed or 1.0) * (float(Fraction(ka) / fps) - t0)
                            - (float(Fraction(ka) / fps) + off)) <= BROLL_GAP_S for ka, _, off in runs):
                    continue                    # its picture is the RAW of its own audio (an A/V shift): the main clip
                frame_src = _framing_source(flat_before(k), flat_after(k))
                for ka, kb, off in runs:
                    okr, pk, lag = _check_run(ka, kb, off, fps, comp_y, raw_y, sr)
                    if okr is False or frame_src is None:
                        continue
                    ta = float(Fraction(ka) / fps)
                    ln = Line(ta + off + lag, 1.0, ta, frame_src, f"audio found at RAW {ta + off + lag:.3f}s",
                              heard=True)
                    pieces.append((ka, kb, ln, {"ok": True, "corr": round(pk, 4) if okr else None,
                                                "lag_ms": round(lag * 1000.0, 3)}, "audio"))
        if s.type == "raw" and not pieces:
            au = s.audio or {}
            if au.get("corr") is not None and float(au["corr"]) >= strong and au.get("lag_ms") is not None \
                    and abs(float(au["lag_ms"])) <= OWN_SOUND_MS and not other_clips_line(s):
                continue                        # its own sound is its picture's RAW (a small A/V shift): the main clip
            from .shots import MIN_SHOT_S
            if (s.audio or {}).get("corr") is None and not same_line                     and (int(s.comp_out) - int(s.comp_in)) / float(fps) >= MIN_SHOT_S - 1e-9:
                res["kept"].append(_row(s, fps, "its sound could not be measured (too short) and it is not inside one "
                                                "continuous shot: left as the competitor has it", None))
                continue
            # shorter than a shot can be (shots.MIN_SHOT_S): left as it is, it would be a flash frame -- the clip
            # before plays on over it, as over a flash (video4: 4 frames at the end of the competitor's rewind)
            prev = next((x for x in reversed(flat_before(k)) if x.type == "raw"), None)
            ln = _keeps_playing(prev, None, s, fps, raw_len)
            if ln is None or s.raw_in_seconds is None or abs(ln.at(t0) - float(s.raw_in_seconds)) <= BROLL_GAP_S:
                continue                        # the picture already continues (or nearly) the clip before: no cutaway
        # what the found runs leave uncovered: another video when the competitor's audio there has speech (a
        # NOT-IN-RAW stretch only), else the previous RAW clip keeps playing (no RAW audio under it)
        filled: list[tuple[int, int, Line, dict, str]] = []
        cursor = int(s.comp_in)
        for ka, kb, ln, ev, how in sorted(pieces, key=lambda p: p[0]) + [(int(s.comp_out), int(s.comp_out), None, {},
                                                                           "")]:
            said = (speech_of(float(Fraction(cursor) / fps), float(Fraction(ka) / fps))
                    if (ka > cursor and s.type == "not_in_raw" and speech_of is not None and have_audio
                        and (ka - cursor) / float(fps) >= OTHER_MIN_S - 1e-9) else None)
            if said is not None and is_speech(said):
                filled.append((cursor, ka, None, {"words": said}, "other video"))
            elif ka > cursor:
                prev_segs = flat_before(k) + [_as_seg(p, s, fps, raw_fps) for p in filled if p[2] is not None]
                prev = next((x for x in reversed(prev_segs) if x.type == "raw"), None)
                nxt = next((x for x in flat_after(k) if x.type == "raw"), None)
                gap = Segment(id=int(s.id), type=s.type, comp_in=cursor, comp_out=ka)
                kl = _keeps_playing(prev, nxt, gap, fps, raw_len)
                if kl is not None:
                    filled.append((cursor, ka, kl, {"ok": None, "corr": None, "lag_ms": None}, "keeps playing"))
            if ln is not None:
                filled.append((ka, kb, ln, ev, how))
            cursor = max(cursor, kb)
        if not filled:
            res["kept"].append(_row(s, fps, "no RAW clip to continue over it", None))
            continue
        news: list[Segment] = []
        for n, (ka, kb, ln, ev, how) in enumerate(filled):
            if how == "other video":
                new = other_video_piece(s, ka, kb, ev.get("words"), fps, int(s.id) if not n else next_id)
                if n:
                    next_id += 1
                news.append(new)
                res["other_video"].append(dict(_row(s, fps, None, None), comp_in=ka, comp_out=kb, label=new.label,
                                               words=" ".join(w[0] for w in new.audio["other_video"]["words"])))
                continue
            part = Segment(id=int(s.id), type=s.type, comp_in=ka, comp_out=kb, raw_in_seconds=s.raw_in_seconds,
                           speed=s.speed, transition_in=s.transition_in if ka == int(s.comp_in) else None,
                           transition_out=s.transition_out if kb == int(s.comp_out) else None)
            new = replacement(part, ln, fps, raw_fps, ev)
            if n:
                new.id = next_id
                next_id += 1
            if how == "keeps playing":
                if (kb - ka) / float(fps) >= MUTE_MIN_S:
                    new.audio["mute"] = True     # long enough to know: the audio there is not from the RAW
                    new.notes = (f"--premiere: the competitor showed {new.audio['broll']['replaced']} here over audio "
                                 f"that is not from the RAW (music / voice-over); {ln.source} (no RAW audio under it)")
                else:
                    how = "keeps playing (short)"
                    new.notes = (f"--premiere: the competitor showed {new.audio['broll']['replaced']} here for "
                                 f"{kb - ka} frame(s), too short to check its audio; {ln.source}")
            new.audio["broll"]["how"] = how
            new.audio["broll"]["ranges"] = [[ka, kb, int(s.id), how]]
            filled[n] = (ka, kb, ln, ev, how)
            news.append(new)
        replaced[int(s.id)] = news
        shown = [(f, x) for f, x in zip(filled, news) if f[4] != "other video"]      # the pieces the RAW fills
        if not shown:
            continue
        hows = sorted({f[4] for f, _ in shown})
        row = dict(_row(s, fps, None, shown[0][0][3]), line="; ".join(f[2].source for f, _ in shown),
                   raw_in_seconds=shown[0][1].raw_in_seconds,
                   raw_out_seconds=round(shown[-1][0][2].at(float(Fraction(int(shown[-1][0][1])) / fps)), 6),
                   bridged=False, how=hows[0] if len(hows) == 1 else "audio + keeps playing",
                   parts=[{"comp_in": f[0], "comp_out": f[1], "how": f[4], "corr": f[3].get("corr"),
                           "raw_in_seconds": round(f[2].at(float(Fraction(f[0]) / fps)), 6) if f[2] else None}
                          for f in filled])
        res["replaced"].append(row)
    res["replaced"].sort(key=lambda r: r["comp_in"])


def other_clips_line(s: Segment) -> bool:
    """A RAW piece whose sound the audio stage found on ANOTHER clip's line (FX-14: "S08 continued", bridged or not,
    or the in-point line of a piece before it): its corr / lag are that line's, not its own picture's, and that line
    is the RAW video of the audio playing there. Task 10 (video4 on the full-size files): 1 and 4 frames of other RAW
    moments under S08's continuing speech read as "their own sound" and were kept -- two flash frames."""
    ln = (s.audio or {}).get("line")
    try:
        return s.type == "raw" and bool(ln) and int(ln["id"]) != int(s.comp_in)
    except (KeyError, TypeError, ValueError):
        return False


def slip_onto_line(s: Segment, by_comp_in: dict, A: Segment | None, B: Segment | None, fps: Fraction,
                   raw_fps: Fraction) -> Segment | None:
    """--premiere (follow_audio): a RAW piece whose sound is on ANOTHER clip's line (other_clips_line), at that line's
    speed, whose picture runs a frame or two off the line (within JUMP_FRAMES: no cutaway), played ON the line -- its
    picture at the RAW time of its sound, its own framing kept; None when that does not apply.

    A picture a frame or two off its own sound is what the match's time / translation confound leaves open (or a slip
    nobody sees). Played as it is, V1 repeats or skips those frames at the cut while A1 plays on, and the repeat
    removal (repeats.py) then cuts the sound as well: your video1 (final.mp4), S09 -- its picture 3 frames behind
    S08's line, whose sound it continues: the speech-safe cuts kept A1 playing on, the repeat removal took the 5
    repeated V1 frames out of A1 too, and A1 jumped 0.08 s inside "Exactly, so I'm open"."""
    fx = (s.audio or {}).get("line") or {}
    if (not other_clips_line(s) or s.time_remap_keys or (s.retime or "none") != "none" or s.speed is None
            or s.raw_in_seconds is None or fx.get("raw_in_seconds") is None):
        return None
    v = float(fx.get("speed") or 1.0)
    d = float(fx["raw_in_seconds"]) - float(s.raw_in_seconds)
    if abs(float(s.speed) - v) > 1e-9 or abs(d) <= 1e-6 or abs(d) > JUMP_FRAMES / float(fps):
        return None
    ln = _fx14_line(s, fx, by_comp_in, A, B, fps)
    new = replacement(s, ln, fps, raw_fps, {"ok": True, "corr": fx.get("corr"), "lag_ms": fx.get("lag_ms")})
    # only its time moves: its own framing, box, label and confidence; no B-roll marker (it is no cutaway)
    new.flip_h, new.transform, new.transform_keys = bool(s.flip_h), copy.deepcopy(s.transform), \
        copy.deepcopy(s.transform_keys)
    new.easing, new.box, new.region = s.easing, copy.deepcopy(s.box), int(s.region)
    new.confidence, new.label = s.confidence, s.label
    new.audio.pop("broll", None)
    new.notes = (f"--premiere: its picture ran {-1000.0 * d:+.0f} ms off the RAW time of its sound ({ln.source}); "
                 "played at that time, its own framing kept, so the picture neither repeats nor skips frames where "
                 "the sound plays on")
    return new


def _as_seg(p: tuple, s: Segment, fps: Fraction, raw_fps: Fraction) -> Segment:
    ka, kb, ln, ev, how = p
    return replacement(Segment(id=int(s.id), type="raw", comp_in=ka, comp_out=kb), ln, fps, raw_fps, ev)


def _framing_source(before: list[Segment], after: list[Segment]) -> Segment | None:
    """The RAW clip whose framing a found-by-audio piece takes: the nearest RAW clip before it, else after it."""
    for x in list(reversed(before)) + list(after):
        if x.type == "raw" and (x.transform or x.transform_keys):
            return x
    return None
