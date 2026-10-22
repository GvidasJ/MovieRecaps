"""placed_by_sound.py: never nothing (night 15, task 3 step 5).

When the competitor's sound is found in the RAW but its picture is not, even after searching it again
(pipeline.stop_if_picture_not_found: PICTURE NOT FOUND), the run still stops -- but first it writes
``1_edit_placed_by_sound.xml`` in the run folder: the competitor's length on the usual Premiere sequence, V1 / A1 the
RAW where its SOUND placed each stretch of the competitor (the sound windows found at the normal speed, grouped into
runs of one RAW offset; each run reaches half-way to the next), the RAW scaled to cover the template window and
centred (the competitor's framing is unknown), a marker "PLACED BY SOUND -- check the framing" over the whole
sequence and "NOT IN RAW" markers where the sound placed nothing. It is never 1_edit.xml: the run is not a normal
edit, and the app shows the PICTURE NOT FOUND stop with this file named in it.

New code lives here, not in model.py / common.py (their edit would re-run every cached stage of every video).
"""
from __future__ import annotations

import copy
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from .common import fps_str, log
from .model import Cutlist, Segment
from .raw_only import SEQ_FPS, framing, window_box

XML_NAME = "1_edit_placed_by_sound.xml"
MARKER = "PLACED BY SOUND -- check the framing"
SAME_OFFSET_S = 0.12        # sound windows whose RAW offsets differ less than this are one run
MIN_RUN_WINDOWS = 3         # a run of fewer windows is noise (a lone window agreeing with nothing)
NORMAL_SPEED = 0.02         # only windows found at the normal speed (|speed - 1| under this)
MAX_REACH_S = 3.0           # a run reaches at most this far past its own windows into a gap the sound left


def runs(comp_t: Any, raw_t: Any, ok: Any, speed: Any = None, window: float = 1.0) -> list[dict]:
    """The sound's runs: [{'from', 'to' (competitor s, its windows' edges), 'offset' (RAW s - competitor s),
    'windows'}], in competitor order."""
    ct, rt = np.asarray(comp_t, np.float64), np.asarray(raw_t, np.float64)
    m = np.asarray(ok, bool) & np.isfinite(rt)
    if speed is not None:
        m &= np.abs(np.asarray(speed, np.float64) - 1.0) < NORMAL_SPEED
    idx = np.flatnonzero(m)
    idx = idx[np.argsort(ct[idx], kind="stable")]
    out: list[dict] = []
    cur: list[int] = []

    def close() -> None:
        if len(cur) >= MIN_RUN_WINDOWS:
            d = float(np.median(rt[cur] - ct[cur]))
            out.append({"from": float(ct[cur[0]]) - window / 2.0, "to": float(ct[cur[-1]]) + window / 2.0,
                        "offset": d, "windows": len(cur)})
    for i in idx:
        if cur and abs((rt[i] - ct[i]) - float(np.median(rt[cur] - ct[cur]))) > SAME_OFFSET_S:
            close()
            cur = []
        cur.append(int(i))
    close()
    return out


def spans(rs: list[dict], duration: float, raw_duration: float) -> list[dict]:
    """The runs as [{'from', 'to', 'offset'}] tiling what the sound placed: overlaps split half-way, each run reaching
    half-way (at most MAX_REACH_S) into a gap, the first from 0 and the last to the end when that is near; the RAW
    stays inside [0, raw_duration]."""
    out: list[dict] = []
    for k, r in enumerate(rs):
        a, b = r["from"], r["to"]
        prev, nxt = (rs[k - 1] if k else None), (rs[k + 1] if k + 1 < len(rs) else None)
        a = max(r["from"] - MAX_REACH_S, 0.0 if prev is None else (prev["to"] + r["from"]) / 2.0)
        b = min(r["to"] + MAX_REACH_S, duration if nxt is None else (r["to"] + nxt["from"]) / 2.0)
        a, b = max(a, 0.0, -r["offset"]), min(b, duration, raw_duration - r["offset"])
        if out and a < out[-1]["to"]:
            a = out[-1]["to"]
        if b - a > 1e-6:
            out.append({"from": a, "to": b, "offset": r["offset"]})
    return out


def build_cutlist(raw_block: dict, duration: float, sp: list[dict], win: tuple[float, float, float, float],
                  seq_wh: tuple[int, int], fps: Fraction = SEQ_FPS) -> Cutlist:
    """The edit on the ``fps`` planning grid (the Premiere export's: 60, or 59.94 for an NTSC competitor): one RAW
    segment per span, NOT IN RAW segments where the sound placed nothing; every clip the RAW covering the window,
    centred."""
    f = float(fps)
    n = int(round(duration * f))
    sim = framing((float(raw_block["width"]), float(raw_block["height"])), win, None)
    segs: list[Segment] = []
    k = 0

    def add(a: int, b: int, kind: str, raw_s: float | None, label: str) -> None:
        if b <= a:
            return
        kw: dict = {"raw_in_seconds": raw_s, "speed": 1.0, "transform": sim.to_dict(), "confidence": 0.5,
                    "raw_in_interval": None if raw_s is None else [raw_s - 0.25 / f, raw_s + 0.25 / f]}
        if raw_s is None:
            kw = {"confidence": 0.0}
        segs.append(Segment(id=len(segs) + 1, type=kind, comp_in=a, comp_out=b, label=label, **kw))
    for s in sp:
        a, b = int(round(s["from"] * f)), min(n, int(round(s["to"] * f)))
        if a > k:
            add(k, a, "not_in_raw", None, "the sound placed nothing here")
            k = a
        a = max(a, k)
        add(a, b, "raw", a / f + s["offset"], "placed by sound")
        k = max(k, b)
    add(k, n, "not_in_raw", None, "the sound placed nothing here")
    comp = {"file": "", "width": int(seq_wh[0]), "height": int(seq_wh[1]), "fps": fps_str(fps), "frames": n}
    layout = {"mode": "match", "layout_kind": "boxed", "box": window_box(win), "background": "solid",
              "background_detail": {"type": "solid", "color": "#000000"}, "canvas_bg": "#000000", "zones": [],
              "captions": []}
    return Cutlist(1, comp, dict(raw_block), layout, segs)


def add_marker(xml: Path, name: str = MARKER, comment: str = "") -> None:
    """A marker over the whole sequence (in 0, out -1, like the export's own "Audio sync" one)."""
    from xml.sax.saxutils import escape
    text = xml.read_text(encoding="utf-8")
    at = text.rfind("</sequence>")
    if at < 0:
        return
    mk = (f"<marker><name>{escape(name)}</name><comment>{escape(comment)}</comment><in>0</in><out>-1</out>"
          "</marker>\n")
    xml.write_text(text[:at] + mk + text[at:], encoding="utf-8")


def write(ctx: Any) -> dict:
    """The placed-by-sound edit of a PICTURE NOT FOUND run (module docstring): {'path', 'clips', 'covered' (share of
    the competitor), 'why'}; {'path': None, 'why'} when the sound places too little. Never raises."""
    try:
        return _write(ctx)
    except Exception as e:  # noqa: BLE001 - the stop's own message stays
        log.warning("placed by sound: not made (%s: %s)", type(e).__name__, e)
        return {"path": None, "why": f"{type(e).__name__}: {e}"}


def _write(ctx: Any) -> dict:
    from . import export_xml_edl, pipeline
    cfg = ctx.cfg
    h = getattr(ctx, "hints", None)
    if h is None or not len(getattr(h, "comp_t", ())) or getattr(ctx, "raw_info", None) is None:
        return {"path": None, "why": "no sound placement"}
    duration = float(getattr(ctx.comp_info, "duration", 0.0) or 0.0)
    raw_dur = float(ctx.raw_info.duration or 0.0)
    rs = runs(h.comp_t, h.raw_t, h.confident(cfg.audio_min_conf), h.speed, float(getattr(h, "window", 1.0)))
    sp = spans(rs, duration, raw_dur)
    covered = sum(s["to"] - s["from"] for s in sp) / duration if duration > 0 else 0.0
    if not sp or covered < 0.25:
        return {"path": None, "why": f"the sound places only {100 * covered:.0f} % of the competitor"}
    st = export_xml_edl.premiere_settings(cfg)
    raw_block = pipeline.media_block(ctx.raw_info, ctx.raw_conform, ctx.raw_input)
    cl = build_cutlist(raw_block, duration, sp, st["window"], st["size"], Fraction(st["fps"]))
    c = copy.copy(cfg)
    c.premiere_shots = None
    ex = pipeline.premiere_media(ctx, cl)        # V1 the original RAW (or the checked copy), A1 its stereo sound
    xml = Path(cfg.deliver) / XML_NAME
    rate = export_xml_edl.sequence_rate_for_raw(Fraction(ctx.raw_info.fps))
    res = export_xml_edl.write_premiere_xml(ex, xml, c, None, seq_rate=rate)
    add_marker(xml, MARKER, f"{len(sp)} stretch(es) of the RAW placed where the competitor's sound is "
                            f"({100 * covered:.0f} % of it); the picture was not found -- the RAW is centred in the "
                            "window, check the framing and the cuts")
    out = {"path": str(xml), "clips": int(res.get("clips") or 0), "covered": round(covered, 4),
           "spans": [{k: round(v, 3) for k, v in s.items()} for s in sp], "fps": fps_str(rate)}
    log.info("placed by sound: %s -- %d clip(s), %.0f %% of the competitor", xml.name, out["clips"], 100 * covered)
    try:
        ctx.dlog.record("inputs", "placed_by_sound", **{k: v for k, v in out.items() if k != "spans"},
                        spans=out["spans"][:50])
    except Exception:  # noqa: BLE001
        pass
    return out


__all__ = ["XML_NAME", "MARKER", "runs", "spans", "build_cutlist", "add_marker", "write"]
