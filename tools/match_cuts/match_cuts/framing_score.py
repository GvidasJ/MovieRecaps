"""framing_score.py: how many of the user's picture clips the tool frames as the user did (night 8) -- check-all's
"framing" column.

The answer key (answer_edit.json "framing", written by learn from the user's project: learn.write_case) holds each of
the user's picture clips of the RAW -- RAW seconds [src_in, src_out) on the case's raw.mp4, the sideways offset of
the picture from the sequence's centre (``dx``, sequence px) and its scale (%) -- or None where the user keyframed
the framing (a pan: not compared). A clip is framed **the same** when the tool's clip playing the middle of its RAW
range sits within SAME_PX sequence px sideways and SAME_ZOOM_PCT of its zoom (learn's FRAMING_PX / ZOOM_PCT: what
learn counts as "moved" / "zoomed"). Both sides are 1080 px wide sequences (the template); the tool's framing comes
from the edit check-all scores (run_folders.planned_files).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

SAME_PX = 40.0               # learn.FRAMING_PX
SAME_ZOOM_PCT = 3.0          # learn.ZOOM_PCT


def key_framing(answer_edit: str | Path | None) -> list[dict] | None:
    """The key's framed picture clips ([{src_in, src_out, dx, scale}]), None when the key holds no framing."""
    if answer_edit is None or Path(answer_edit).suffix.lower() != ".json" or not Path(answer_edit).is_file():
        return None
    d = json.loads(Path(answer_edit).read_text(encoding="utf-8"))
    fr = d.get("framing")
    return list(fr) if isinstance(fr, list) else None


def score(key: Sequence[dict], tool: Sequence[Any]) -> dict:
    """{clips, same, moved: [...], zoomed: [...], missing, examples} -- ``tool``: learn.Clip picture clips (r0, r1 RAW s,
    dx, scale)."""
    n = same = missing = 0
    moved, zoomed, rows = [], [], []
    for k in key:
        if k.get("dx") is None or k.get("scale") is None:
            continue                                         # keyframed by the user: not compared
        mid = 0.5 * (float(k["src_in"]) + float(k["src_out"]))
        t = next((c for c in tool if min(c.r0, c.r1) - 1e-6 <= mid < max(c.r0, c.r1) + 1e-6), None)
        n += 1
        if t is None or t.dx is None or t.scale is None:
            missing += 1
            rows.append({"raw": round(mid, 2), "why": "the tool plays no fixed framing there"})
            continue
        dx = float(t.dx) - float(k["dx"])
        dz = 100.0 * (float(t.scale) / float(k["scale"]) - 1.0) if float(k["scale"]) else 0.0
        ok = abs(dx) <= SAME_PX and abs(dz) <= SAME_ZOOM_PCT
        same += int(ok)
        if abs(dx) > SAME_PX:
            moved.append(round(dx, 1))
        if abs(dz) > SAME_ZOOM_PCT:
            zoomed.append(round(dz, 1))
        if not ok:
            rows.append({"raw": round(mid, 2), "dx": round(dx, 1), "zoom": round(dz, 1)})
    return {"clips": n, "same": same, "same_pct": round(100.0 * same / n, 1) if n else 0.0, "moved": moved,
            "zoomed": zoomed, "missing": missing, "rows": rows}


def score_run(answer_edit: str | Path | None, run_dir: str | Path) -> dict | None:
    """The run's framing against the key (None: the key holds no framing). A run on a sequence of another size
    (video021: its 2160 x 3840 frame, your 1080 x 1920 project) is compared in the key's sequence pixels."""
    key = key_framing(answer_edit)
    if key is None:
        return None
    import dataclasses
    from .learn import load_cutlist, run_files, tool_clips
    from .run_folders import planned_files
    run = Path(run_dir)
    rf = run_files(run)
    width = None
    if rf is not None:
        cl = load_cutlist(rf)
        width = float((cl.get("raw") or {}).get("width") or 0) or None
    pic, _snd, _fps, seq_w = tool_clips(planned_files(run)[0], width)
    size = json.loads(Path(answer_edit).read_text(encoding="utf-8")).get("framing_size") or [seq_w]
    k = float(size[0]) / float(seq_w) if seq_w else 1.0
    if abs(k - 1.0) > 1e-9:
        pic = [dataclasses.replace(c, dx=None if c.dx is None else c.dx * k,
                                   scale=None if c.scale is None else c.scale * k) for c in pic]
    return score(key, pic)


def line(sc: dict) -> str:
    return f"{sc['same']}/{sc['clips']} ({sc['same_pct']:.0f} %)" if sc.get("clips") else "no framed clip"
