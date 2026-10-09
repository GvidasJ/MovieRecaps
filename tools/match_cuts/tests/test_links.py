"""Linked clips: every V1 clip of 1_edit.xml imports into Premiere linked to its own A1 clip (move, trim or cut one and
its audio goes with it) -- export_xml_edl.link_pairs, the <link>s write_premiere_xml writes, and the hard check
premiere_link_problems."""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_export_xml_edl as T  # noqa: E402

from match_cuts import export_xml_edl as ex, silence as S  # noqa: E402
from match_cuts.config import Config  # noqa: E402

SR = 16000


def clip(a: int, b: int, src: int, speed: float = 1.0) -> ex.PremiereClip:
    return ex.PremiereClip(None, None, a, b, a, b, src, src + int(round((b - a) * speed)), speed, True, 0.0, 1.0, True,
                           [], None)


def audio(a: int, b: int, src: int, **kw) -> dict:
    return dict({"seg": None, "start": a, "end": b, "in": src, "out": src + (b - a), "speed": 1.0,
                 "what": "picture"}, **kw)


def spans(items) -> list:
    return [(c.rec_start, c.rec_end, c.src_in, c.src_out) if isinstance(c, ex.PremiereClip) else
            (c["start"], c["end"], c["in"], c["out"]) for c in items]


def test_one_take_of_audio_under_several_clips_is_split_into_seamless_pieces():
    # Zendaya: the picture changes framing at 190 (and its take plays on), A1 is one take 0-192
    v = [clip(0, 190, 9257), clip(190, 192, 9447), clip(192, 289, 9466)]
    a = [audio(0, 192, 9257, fade_in=True, fade_out=True), audio(192, 289, 9466)]
    vs, as_, pairs = ex.link_pairs(v, a)
    assert spans(vs) == spans(v)                                          # V1 untouched
    assert spans(as_) == [(0, 190, 9257, 9447), (190, 192, 9447, 9449), (192, 289, 9466, 9563)]   # source runs on
    assert [(x.get("fade_in"), x.get("fade_out")) for x in as_[:2]] == [(True, False), (False, True)]
    assert pairs == [(0, 0), (1, 1), (2, 2)]


def test_audio_shifted_from_its_picture_stays_linked_to_it():
    # the A1 cut 2 frames after V1's (it plays on to close a jump): no split, each clip with its own audio
    v = [clip(0, 100, 0), clip(100, 200, 500)]
    a = [audio(0, 102, 0), audio(102, 200, 502)]
    vs, as_, pairs = ex.link_pairs(v, a)
    assert spans(vs) == spans(v) and spans(as_) == spans(a) and pairs == [(0, 0), (1, 1)]


def test_an_audio_cut_inside_a_clip_splits_the_picture_seamlessly():
    v = [clip(0, 100, 1000)]
    a = [audio(0, 60, 1000), audio(60, 100, 2000)]
    vs, as_, pairs = ex.link_pairs(v, a)
    assert spans(vs) == [(0, 60, 1000, 1060), (60, 100, 1060, 1100)] and all(c.link_split for c in vs)
    assert pairs == [(0, 0), (1, 1)]


def test_silent_clips_and_audio_under_an_empty_v1_stay_unlinked():
    v = [clip(0, 50, 0), clip(50, 80, 300), clip(100, 130, 600)]          # 50-80: a freeze (no audio); 80-100: empty
    a = [audio(0, 50, 0), audio(82, 98, 400), audio(100, 130, 600)]
    vs, as_, pairs = ex.link_pairs(v, a)
    assert pairs == [(0, 0), (2, 2)]


def _export(tmp_path):
    cl = T.premiere_cutlist()
    raw = (0.2 * np.sin(np.arange(200 * SR) * 0.3)).astype(np.float32)
    raw[int(6.9 * SR):int(7.6 * SR)] = 0.0
    cfg = Config(out_dir=str(tmp_path), premiere=True, silence_db=-20.0, min_silence=0.35, pad_before=0.08,
                 pad_after=0.12)
    plan = S.plan_premiere(cl, raw, SR, cfg)
    xml = tmp_path / "1_edit.xml"
    res = ex.write_premiere_xml(cl, xml, cfg, plan["ripple"])
    return cl, cfg, plan, xml, res


def test_the_xml_links_every_clip_to_its_audio_both_ways(tmp_path):
    cl, cfg, plan, xml, res = _export(tmp_path)
    x = ex.parse_premiere_xml(xml)
    assert (res["links"], len(x["audio"])) == (9, 10)                 # A1 S06: an audio line under an empty V1
    for c in x["clips"]:
        if c["links"]:
            (vref, vk), (aref, ak) = c["links"]
            a = next(it for it in x["audio"] if it["id"] == aref)
            assert (vref, vk, ak) == (c["id"], "video", "audio") and a["links"] == c["links"]
    root = ET.parse(str(xml)).getroot()
    lk = root.find("sequence/media/video/track/clipitem/link")
    assert [e.tag for e in lk] == ["linkclipref", "mediatype", "trackindex", "clipindex"]   # Premiere's own layout
    v = ex.validate_premiere_exports(cl, xml, None, cfg, plan["ripple"])
    assert v["ok"], v["errors"]
    assert v["link_problems"] == []
    # the one thing with nothing to link: the uncertain spot's audio line under an empty V1 (the freeze plays its
    # own sound now, linked like every other clip)
    assert [e.split(": ")[0] for e in v["link_exceptions"]] == ["A1 S06 at 00:00:04:53"]


def _tamper(xml: Path, dest: Path, fn) -> Path:
    tree = ET.parse(str(xml))
    fn(tree.getroot().find("sequence"))
    tree.write(str(dest), encoding="utf-8", xml_declaration=True)
    return dest


def test_the_link_check_fails_unlinked_and_doubly_linked_clips(tmp_path):
    cl, cfg, plan, xml, res = _export(tmp_path)
    assert ex.premiere_link_problems(xml)[0] == []

    def unlink_first(seq):
        for track in (seq.find("media/video/track"), seq.find("media/audio/track")):
            first = track.find("clipitem")
            for lk in first.findall("link"):
                first.remove(lk)
    probs, _ = ex.premiere_link_problems(_tamper(xml, tmp_path / "unlinked.xml", unlink_first))
    assert probs == ["V1 S01 at 00:00:00:00: linked to 0 A1 clips (exactly one: its audio)",
                     "A1 S01 at 00:00:00:00: linked to 0 V1 clips (exactly one: its picture)"]

    def link_twice(seq):
        v = seq.find("media/video/track").findall("clipitem")[1]
        extra = ET.SubElement(v, "link")
        for tag, val in (("linkclipref", "clipitem-a3"), ("mediatype", "audio"), ("trackindex", "1"),
                         ("clipindex", "3")):
            ET.SubElement(extra, tag).text = val
    probs, _ = ex.premiere_link_problems(_tamper(xml, tmp_path / "twice.xml", link_twice))
    assert probs and probs[0].endswith("linked to 2 A1 clips (exactly one: its audio)")
