"""7 Nov (your call: "no need to have small cuts"): pieces of V1 that play one continuous take -- the next one starts
in the source exactly where the one before ends, at the same speed and (almost) the same framing -- are joined into
one clip, and the same is done to their A1 sound clips. A real cut (another place in the RAW, a different framing,
a speed change, keyframes) stays as it is. Only 1_edit.xml's clipitems change; every other byte of meaning stays.

``join(xml)`` -> how many clips were joined (0: the file is left untouched)."""
from __future__ import annotations

import copy
import os
import xml.etree.ElementTree as ET
from pathlib import Path

SCALE_REL = 0.015           # framing "the same": scale within 1.5 % ...
CENTER_ABS = 0.02           # ... and the center within 2 % of the frame (Premiere's -0.5..0.5 units)


def _i(ci, tag) -> int | None:
    t = ci.findtext(tag)
    try:
        return int(float(t)) if t is not None else None
    except ValueError:
        return None


def _fid(ci) -> str:
    f = ci.find("file")
    return f.get("id", "") if f is not None else ""


def _motion(ci) -> tuple[float | None, tuple[float, float] | None, str]:
    """(scale, center, the rest of the filters as text) -- the rest must be the same to join."""
    scale, center = None, None
    rest = []
    for flt in ci.findall("filter"):
        f2 = copy.deepcopy(flt)
        for p in f2.iter("parameter"):
            pid = p.findtext("parameterid")
            if pid == "scale":
                try:
                    scale = float(p.findtext("value"))
                except (TypeError, ValueError):
                    pass
                p.find("value").text = ""
            elif pid == "center":
                v = p.find("value")
                try:
                    center = (float(v.findtext("horiz")), float(v.findtext("vert")))
                except (TypeError, ValueError, AttributeError):
                    pass
                for e in v:
                    e.text = ""
        rest.append(ET.tostring(f2, encoding="unicode"))
    return scale, center, "".join(" ".join(r.split()) for r in rest)


def _continues(a, b) -> bool:
    if _fid(a) != _fid(b) or not _fid(a):
        return False
    s1, e1, i1, o1 = (_i(a, k) for k in ("start", "end", "in", "out"))
    s2, e2, i2, o2 = (_i(b, k) for k in ("start", "end", "in", "out"))
    if None in (s1, e1, i1, o1, s2, e2, i2, o2) or s1 < 0 or s2 < 0:
        return False
    if e1 != s2 or o1 != i2 or (e1 - s1) != (o1 - i1) or (e2 - s2) != (o2 - i2):
        return False                                   # a jump in the source, a gap, or not at 1x
    if a.find(".//keyframe") is not None or b.find(".//keyframe") is not None:
        return False
    sa, ca, ra = _motion(a)
    sb, cb, rb = _motion(b)
    if ra != rb:
        return False
    if (sa is None) != (sb is None) or (ca is None) != (cb is None):
        return False
    if sa is not None and abs(sa - sb) > SCALE_REL * max(abs(sa), 1e-9):
        return False
    if ca is not None and (abs(ca[0] - cb[0]) > CENTER_ABS or abs(ca[1] - cb[1]) > CENTER_ABS):
        return False
    return True


def _relink(tracks: list) -> None:
    """Each <link>'s clipindex = where its clip now is in its track (1-based)."""
    where = {}
    for tr in tracks:
        for k, ci in enumerate(tr.findall("clipitem"), 1):
            where[ci.get("id")] = k
    for tr in tracks:
        for ci in tr.findall("clipitem"):
            for ln in ci.findall("link"):
                ref = ln.findtext("linkclipref")
                if ref in where and ln.find("clipindex") is not None:
                    ln.find("clipindex").text = str(where[ref])


def join_tree(root) -> int:
    joined = 0
    for seq in root.iter("sequence"):
        vt = seq.findall("media/video/track")
        at = seq.findall("media/audio/track")
        if not vt:
            continue
        v1 = vt[0]
        a1 = at[0] if at else None
        k = 0
        while True:
            vs = v1.findall("clipitem")
            if k + 1 >= len(vs):
                break
            a, b = vs[k], vs[k + 1]
            if not _continues(a, b):
                k += 1
                continue
            pa = pb = None                              # their sound on A1 (linked, the same times)
            if a1 is not None:
                by_id = {c.get("id"): c for c in a1.findall("clipitem")}

                def sound(ci):
                    for ln in ci.findall("link"):
                        if ln.findtext("mediatype") == "audio":
                            return by_id.get(ln.findtext("linkclipref"))
                    return None
                pa, pb = sound(a), sound(b)
                if (pa is None) != (pb is None) or (pa is not None and not (
                        _continues(pa, pb) and _i(pa, "start") == _i(a, "start") and _i(pb, "end") == _i(b, "end"))):
                    k += 1
                    continue
            for x, y in ((a, b), (pa, pb)):
                if x is None:
                    continue
                x.find("end").text = y.findtext("end")
                x.find("out").text = y.findtext("out")
            v1.remove(b)
            if pb is not None:
                a1.remove(pb)
            joined += 1
        _relink(vt + at)
    return joined


def join(xml: str | os.PathLike) -> int:
    """1_edit.xml with its continuous pieces joined (written only when something was joined)."""
    xml = Path(xml)
    try:
        text = xml.read_text(encoding="utf-8")
        start = text.index("<xmeml")
        root = ET.fromstring(text[start:])
    except (OSError, ValueError, ET.ParseError):
        return 0
    n = join_tree(root)
    if not n:
        return 0
    ET.indent(root, space="  ")
    tmp = xml.with_name(xml.stem + ".join.part.xml")
    tmp.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode")
                   + "\n", encoding="utf-8")
    ET.parse(str(tmp))
    os.replace(tmp, xml)
    return n
