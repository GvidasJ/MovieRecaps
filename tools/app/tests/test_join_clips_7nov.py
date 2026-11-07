"""7 Nov: the pieces of one continuous take are joined (V1 with its A1); a jump in the source or a new framing
stays a cut."""
from __future__ import annotations

from pathlib import Path

from mr_app.core import join_clips as J


def _clip(cid, start, end, inn, out, fid, scale=80.0, cx=0.0, link=None, kind="video"):
    links = f"<link><linkclipref>{link}</linkclipref><mediatype>{'audio' if kind == 'video' else 'video'}</mediatype>" \
            f"<trackindex>1</trackindex><clipindex>0</clipindex></link>" if link else ""
    flt = (f"<filter><effect><effectid>basic</effectid><parameter><parameterid>scale</parameterid><value>{scale}"
           f"</value></parameter><parameter><parameterid>center</parameterid><value><horiz>{cx}</horiz><vert>0</vert>"
           f"</value></parameter></effect></filter>") if kind == "video" else ""
    return (f'<clipitem id="{cid}"><start>{start}</start><end>{end}</end><in>{inn}</in><out>{out}</out>'
            f'<file id="{fid}"/>{flt}{links}</clipitem>')


def _xml(tmp: Path, v: list[tuple], a: list[tuple]) -> Path:
    vx = "".join(_clip(f"v{k}", *c, link=f"a{k}") for k, c in enumerate(v))
    ax = "".join(_clip(f"a{k}", *c[:5], link=f"v{k}", kind="audio") for k, c in enumerate(a))
    p = tmp / "1_edit.xml"
    p.write_text(f'<?xml version="1.0"?>\n<xmeml version="5"><sequence><media><video><track>{vx}</track></video>'
                 f"<audio><track>{ax}</track></audio></media></sequence></xmeml>", encoding="utf-8")
    return p


def _clips(p: Path):
    import xml.etree.ElementTree as ET
    t = p.read_text(encoding="utf-8")
    r = ET.fromstring(t[t.index("<xmeml"):])
    return [[(c.findtext("start"), c.findtext("end"), c.findtext("in"), c.findtext("out"))
             for c in r.find(f".//{m}/track").findall("clipitem")] for m in ("video", "audio")]


def test_one_take_in_three_pieces_becomes_one_clip(tmp_path):
    v = [(0, 40, 100, 140, "f"), (40, 42, 140, 142, "f"), (42, 90, 142, 190, "f", 80.5)]
    a = [(0, 40, 300, 340, "s"), (40, 42, 340, 342, "s"), (42, 90, 342, 390, "s")]
    p = _xml(tmp_path, v, a)
    assert J.join(p) == 2
    vv, aa = _clips(p)
    assert vv == [("0", "90", "100", "190")] and aa == [("0", "90", "300", "390")]


def test_a_jump_or_a_new_framing_stays_a_cut(tmp_path):
    v = [(0, 40, 100, 140, "f"), (40, 60, 150, 170, "f"), (60, 90, 170, 200, "f", 80.0, 0.2)]
    a = [(0, 40, 300, 340, "s"), (40, 60, 350, 370, "s"), (60, 90, 370, 400, "s")]
    p = _xml(tmp_path, v, a)
    before = p.read_text(encoding="utf-8")
    assert J.join(p) == 0 and p.read_text(encoding="utf-8") == before
