"""4 Nov: enhance a FINISHED edit without doing the cuts again.

    python tools/app/enhance_edit.py send  "<project folder>" [pc|laptop]
        the part of the RAW the edit plays (+5 s each side) -> the Enhance folder; Topaz takes it as always
    python tools/app/enhance_edit.py apply "<project folder>"
        when <name>_E_ENHANCED.mp4 is back: checked, copied into the project (enhance/raw_enhanced.mp4), and
        edit/1_edit.xml plays it (the normal one kept as edit/extras/1_edit_normal.xml): same cuts, same sound
        (A1 untouched), the V1 clips' in/out moved by the piece's start, their scale halved (2x the pixels)
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "match_cuts")]

from mr_app.core import enhance as E          # noqa: E402
from mr_app.core import projects as PJ        # noqa: E402

MARGIN_S = 5.0


def _rate(el) -> Fraction:
    tb = int(el.find("rate/timebase").text)
    ntsc = (el.find("rate/ntsc").text or "").upper() == "TRUE"
    return Fraction(tb * 1000, 1001) if ntsc else Fraction(tb)


def _raw_clips(root):
    seq = root.find(".//sequence")
    out = []
    for tr in seq.findall("media/video/track"):
        for c in tr.findall("clipitem"):
            f = c.find("file")
            if f is not None and f.get("id") == "file-raw":
                out.append((tr, c))
    return seq, out


def _name(p) -> str:
    return E.safe_name(p.folder.name) + "_E"


def send(folder: str, where: str = "") -> None:
    p = PJ.load(folder)
    xml = p.folder / "edit" / "1_edit.xml"
    root = ET.parse(xml).getroot()
    seq, clips = _raw_clips(root)
    fps = _rate(clips[0][1])
    lo = min(int(c.find("in").text) for _t, c in clips) / float(fps)
    hi = max(int(c.find("out").text) for _t, c in clips) / float(fps)
    raw = p.file("raw")
    if raw is None or not raw.is_file():                    # the RAW the edit plays (one RAW per project)
        raw = p.folder / "edit" / "extras" / "media" / "raw.mp4"
    a, b = max(0.0, lo - MARGIN_S), hi + MARGIN_S
    places = [a, max(a, b - 3.0)]
    share = Path(r"D:\Shared\Enhance") if Path(r"D:\Shared\Enhance").exists() else None
    if share is None:                                        # (run from the Linux side of the PC)
        share = Path.home() / "mnt" / "Shared" / "Enhance"
    nm = E.names(share, _name(p))
    info = E._encoded_piece(raw, places, (b - a) + 1.0, nm, _name(p), p.folder, "a finished edit", time.perf_counter(),
                            lambda s, t: print(f"{int(100 * s)} % {t}"), where)        # noqa: SLF001
    print("sent:", info["where"], "->", nm.piece)


def apply(folder: str) -> None:
    p = PJ.load(folder)
    share = Path(r"D:\Shared\Enhance")
    if not share.exists():
        share = Path.home() / "mnt" / "Shared" / "Enhance"
    nm = E.names(share, _name(p))
    info = E.read_info(nm.info) or {}
    chk = E.check_enhanced(nm.enhanced, info)
    if not chk["ok"]:
        sys.exit(f"the enhanced piece is not there or not right: {chk['why']}")
    dst = p.folder / E.PROJECT_SUB / E.PROJECT_FILE
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(nm.enhanced, dst)
    xml = p.folder / "edit" / "1_edit.xml"
    keep = p.folder / "edit" / "extras" / "1_edit_normal.xml"
    if not keep.exists():
        shutil.copyfile(xml, keep)
    tree = ET.parse(keep)
    root = tree.getroot()
    seq, clips = _raw_clips(root)
    fps = _rate(clips[0][1])
    start = int(round(float(info["start_s"]) * float(fps)))
    n_piece = int(round(float(chk["frames"]) / E.OUT_FPS * float(fps)))
    raw_def = next(c.find("file") for _t, c in clips if len(list(c.find("file"))))
    enh_def = ET.Element("file", {"id": "file-enhanced"})
    ET.SubElement(enh_def, "name").text = dst.name
    raw_url = raw_def.findtext("pathurl") or ""                 # the project folder as Windows / Premiere sees it
    cut = raw_url.find("/edit/extras/media/")
    if cut < 0:
        sys.exit("1_edit.xml's RAW is not in edit/extras/media: can't place the enhanced piece")
    ET.SubElement(enh_def, "pathurl").text = raw_url[:cut] + f"/{E.PROJECT_SUB}/{E.PROJECT_FILE}"
    r = ET.SubElement(enh_def, "rate")
    ET.SubElement(r, "timebase").text = str(E.OUT_FPS)
    ET.SubElement(r, "ntsc").text = "FALSE"
    ET.SubElement(enh_def, "duration").text = str(int(chk["frames"]))
    md = ET.SubElement(ET.SubElement(enh_def, "media"), "video")
    sc = ET.SubElement(md, "samplecharacteristics")
    r2 = ET.SubElement(sc, "rate")
    ET.SubElement(r2, "timebase").text = str(E.OUT_FPS)
    ET.SubElement(r2, "ntsc").text = "FALSE"
    ET.SubElement(sc, "width").text = str(chk["width"])
    ET.SubElement(sc, "height").text = str(chk["height"])
    ET.SubElement(sc, "pixelaspectratio").text = "square"
    swapped, kept, defined = 0, 0, False
    for _t, c in clips:
        i0, i1 = int(c.find("in").text), int(c.find("out").text)
        if i0 < start or i1 > start + n_piece - 1:
            kept += 1
            continue
        c.find("in").text, c.find("out").text = str(i0 - start), str(i1 - start)
        c.find("duration").text = str(n_piece)
        c.find("name").text = dst.name
        f = c.find("file")
        idx = list(c).index(f)
        c.remove(f)
        c.insert(idx, copy.deepcopy(enh_def) if not defined else ET.Element("file", {"id": "file-enhanced"}))
        defined = True
        for par in c.iter("parameter"):
            if (par.findtext("parameterid") or "") == "scale":
                for v in par.iter("value"):
                    if v.text and v.text.strip():
                        v.text = f"{float(v.text) / 2.0:.6f}"
        swapped += 1
    if kept:                                         # clips outside the piece keep the RAW: its full definition first
        first = next(c for _t, c in clips if c.find("file").get("id") == "file-raw")
        f = first.find("file")
        if not len(list(f)):
            idx = list(first).index(f)
            first.remove(f)
            first.insert(idx, copy.deepcopy(raw_def))
    tree.write(xml, encoding="UTF-8", xml_declaration=True)
    print(f"1_edit.xml plays the enhanced piece: {swapped} clip(s) swapped, {kept} kept on the normal RAW "
          f"(the normal edit: {keep})")
    p.update(enhance={"state": "used", "file": f"{E.PROJECT_SUB}\\{E.PROJECT_FILE}", "after_edit": True,
                      "text": f"enhanced after the edit: {chk['width']}x{chk['height']}, {E.OUT_FPS} fps"})
    E.clear(share, _name(p))


if __name__ == "__main__":
    if sys.argv[1] == "send":
        send(sys.argv[2], *(sys.argv[3:4] or [""]))
    else:
        apply(sys.argv[2])
