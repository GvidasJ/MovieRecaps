"""learn.py: what the user corrected in a finished Premiere project, kept for next time -- the caption glossary, the cut
and framing changes (and the defaults they suggest), the new test case and the git commands that push it (Task 6).
A small finished project and a run folder are built here; the videos are stand-in files (a file under 100 MB is
copied as it is)."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_prproj import flat  # noqa: E402

from match_cuts import learn as L  # noqa: E402
from match_cuts import prproj as P  # noqa: E402

T = P.TICKS


def write_project(path: Path, raw_path: str, clips, captions, fps_ticks: int = 4233600000) -> Path:
    """A finished project: V1 / A1 clips of raw_path [(t0, t1, src_in, position x or None)] (seconds), one caption
    track [(t0, t1, text)], a 1080x1920 60 fps sequence."""
    objs: list[str] = []
    nid = [100]

    def oid() -> str:
        nid[0] += 1
        return str(nid[0])

    def clip_item(kind, start, end, src_in, pos_x=None):
        sub, clip, chain, it, src = oid(), oid(), oid(), oid(), oid()
        tag = "VideoClipTrackItem" if kind == "video" else "AudioClipTrackItem"
        objs.append(f'<{tag} ObjectID="{it}"><ClipTrackItem Version="8"><ComponentOwner Version="1">'
                    f'<Components ObjectRef="{chain}"/></ComponentOwner><TrackItem Version="3">'
                    f'<Start>{int(round(start * T))}</Start><End>{int(round(end * T))}</End></TrackItem>'
                    f'<SubClip ObjectRef="{sub}"/></ClipTrackItem></{tag}>')
        objs.append(f'<SubClip ObjectID="{sub}"><Clip ObjectRef="{clip}"/><Name>raw.mp4</Name></SubClip>')
        ctag = "VideoClip" if kind == "video" else "AudioClip"
        objs.append(f'<{ctag} ObjectID="{clip}"><Clip Version="18"><Source ObjectRef="{src}"/>'
                    f'<InPoint>{int(round(src_in * T))}</InPoint><OutPoint>{int(round((src_in + end - start) * T))}'
                    f'</OutPoint></Clip></{ctag}>')
        mtag = "VideoMediaSource" if kind == "video" else "AudioMediaSource"
        objs.append(f'<{mtag} ObjectID="{src}"><MediaSource Version="4"><Media ObjectURef="m-raw"/></MediaSource>'
                    f'</{mtag}>')
        crefs = ""
        if kind == "video" and pos_x is not None:
            cid, p1, p2 = oid(), oid(), oid()
            objs.append(f'<PointComponentParam ObjectID="{p1}"><Name>Position</Name><StartKeyframe>'
                        f'-91445760000000000,{pos_x}:0.5,0,0,0,0,0,0,5,4,0,0,0,0</StartKeyframe></PointComponentParam>')
            objs.append(f'<VideoComponentParam ObjectID="{p2}"><Name>Scale</Name><StartKeyframe>'
                        f'-91445760000000000,120.,0,0,0,0,0,0</StartKeyframe></VideoComponentParam>')
            objs.append(f'<VideoFilterComponent ObjectID="{cid}"><Component Version="6"><Params Version="1">'
                        f'<Param Index="0" ObjectRef="{p1}"/><Param Index="1" ObjectRef="{p2}"/></Params></Component>'
                        f'<MatchName>AE.ADBE Motion</MatchName></VideoFilterComponent>')
            crefs = f'<Component Index="0" ObjectRef="{cid}"/>'
        objs.append(f'<VideoComponentChain ObjectID="{chain}"><ComponentChain Version="3"><Components Version="1">'
                    f'{crefs}</Components></ComponentChain></VideoComponentChain>')
        return it

    v1 = [clip_item("video", a, b, s, x) for a, b, s, x in clips]
    a1 = [clip_item("audio", a, b, s) for a, b, s, _x in clips]
    caps = []
    for a, b, text in captions:
        blk, cap = oid(), oid()
        objs.append(f'<Block ObjectID="{blk}"><FormattedTextData Encoding="base64">{flat("ArialMT", text)}'
                    "</FormattedTextData></Block>")
        objs.append(f'<CaptionDataClipTrackItem ObjectID="{cap}"><DataClipTrackItem Version="1"><ClipTrackItem '
                    f'Version="8"><TrackItem Version="3"><Start>{int(round(a * T))}</Start><End>{int(round(b * T))}'
                    f'</End></TrackItem></ClipTrackItem></DataClipTrackItem><BlockVector Version="1">'
                    f'<BlockVectorItem Index="0" ObjectRef="{blk}"/></BlockVector></CaptionDataClipTrackItem>')
        caps.append(cap)

    def track(tag, uid, items):
        refs = "".join(f'<TrackItem Index="{i}" ObjectRef="{x}"/>' for i, x in enumerate(items))
        objs.append(f'<{tag} ObjectUID="{uid}"><ClipTrack Version="2"><ClipItems Version="3"><TrackItems Version="1">'
                    f"{refs}</TrackItems></ClipItems></ClipTrack></{tag}>")
    track("VideoClipTrack", "vt1", v1)
    track("AudioClipTrack", "at1", a1)
    track("CaptionDataClipTrack", "ct1", caps)
    objs.append(f'<Media ObjectUID="m-raw"><ActualMediaFilePath>{raw_path}</ActualMediaFilePath></Media>')
    objs.append(f'<VideoTrackGroup ObjectID="10"><TrackGroup Version="1"><Tracks Version="1"><Track Index="0" '
                f'ObjectURef="vt1"/></Tracks><FrameRate>{fps_ticks}</FrameRate></TrackGroup>'
                '<FrameRect>0,0,1080,1920</FrameRect></VideoTrackGroup>')
    objs.append('<AudioTrackGroup ObjectID="11"><TrackGroup Version="1"><Tracks Version="1"><Track Index="0" '
                'ObjectURef="at1"/></Tracks></TrackGroup></AudioTrackGroup>')
    objs.append('<DataTrackGroup ObjectID="12"><TrackGroup Version="1"><Tracks Version="1"><Track Index="0" '
                'ObjectURef="ct1"/></Tracks></TrackGroup></DataTrackGroup>')
    seq = ('<Sequence ObjectUID="seq-1"><TrackGroups Version="1">'
           '<TrackGroup Version="1" Index="0"><First>v</First><Second ObjectRef="10"/></TrackGroup>'
           '<TrackGroup Version="1" Index="1"><First>a</First><Second ObjectRef="11"/></TrackGroup>'
           '<TrackGroup Version="1" Index="2"><First>d</First><Second ObjectRef="12"/></TrackGroup>'
           '</TrackGroups><Name>Recreated Edit (Premiere)</Name></Sequence>')
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<PremiereData Version="3">\n\t' + "\n\t".join([seq] + objs) + \
          "\n</PremiereData>\n"
    path.write_bytes(gzip.compress(xml.encode("utf-8")))
    return path


def write_edit_xml(path: Path, clips) -> Path:
    """A run's 1_edit.xml: V1 / A1 clips [(t0, t1, src_in, center x)] (seconds; centre: a fraction of the width)."""
    def ci(i, a, b, s, cx, video):
        mot = (f"<filter><effect><effectid>basic</effectid><parameter><parameterid>scale</parameterid><value>120"
               f"</value></parameter><parameter><parameterid>center</parameterid><value><horiz>{cx}</horiz><vert>0"
               f"</vert></value></parameter></effect></filter>") if video else ""
        return (f'<clipitem id="{"v" if video else "a"}{i}"><name>S{i:02d} raw.mp4</name><start>{round(a * 60)}'
                f'</start><end>{round(b * 60)}</end><in>{round(s * 60)}</in><out>{round((s + b - a) * 60)}</out>'
                f"<rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate>{mot}</clipitem>")
    v = "".join(ci(i, a, b, s, cx, True) for i, (a, b, s, cx) in enumerate(clips, start=1))
    au = "".join(ci(i, a, b, s, cx, False) for i, (a, b, s, cx) in enumerate(clips, start=1))
    end = round(max(b for _a, b, _s, _c in clips) * 60)
    path.write_text(f'<?xml version="1.0" encoding="UTF-8"?><xmeml version="4"><sequence><name>Recreated Edit '
                    f"(Premiere)</name><rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate><duration>{end}</duration>"
                    f"<media><video><format><samplecharacteristics><width>1080</width><height>1920</height>"
                    f"</samplecharacteristics></format><track>{v}</track></video><audio><track>{au}</track></audio>"
                    f"</media></sequence></xmeml>", encoding="utf-8")
    return path


def srt(caps) -> str:
    def ts(t):
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(caps, start=1))


@pytest.fixture
def world(tmp_path):
    """A run folder (001) of a competitor and a RAW, and the user's finished project of it."""
    src = tmp_path / "inputs"
    src.mkdir()
    comp, raw = src / "zendaya-interview.mp4", src / "raw_source.mp4"
    comp.write_bytes(b"competitor video" * 64)
    raw.write_bytes(b"raw video" * 64)
    run = tmp_path / "output" / "001"
    (run / "extras" / "media").mkdir(parents=True)
    (run / "extras" / "media" / "raw.mp4").write_bytes(raw.read_bytes())
    (run / "extras" / "cutlist.json").write_text(json.dumps({
        "competitor": {"file": "media/competitor_ref.mp4", "source_path": str(comp)},
        "raw": {"file": "media/raw.mp4", "source_path": str(raw)}}), encoding="utf-8")
    write_edit_xml(run / "1_edit.xml", [(0.0, 1.0, 170.4, 0.0), (1.0, 2.5, 180.0, 0.0), (2.5, 3.0, 190.0, 0.0)])
    (run / "2_captions.srt").write_text(srt([(0.0, 1.0, "I went to the zendeya"), (1.0, 2.0, "interview with tom holland"),
                                             (2.0, 3.0, "and it was great")]), encoding="utf-8")
    proj = write_project(tmp_path / "finished.prproj", str(run / "extras" / "media" / "raw.mp4").replace("/", "\\"),
                         [(0.0, 0.9, 170.5, None),            # S01: starts 0.1 s later
                          (0.9, 2.6, 180.0, 0.4),             # S02: plays on 0.2 s, moved 108 px to the left
                          (2.6, 3.1, 200.0, None)],           # S03 gone; a clip of the RAW added
                         [(0.0, 0.9, "I went to the Zendaya"), (0.9, 2.0, "interview with Tom Holland"),
                          (2.0, 3.1, "and it was amazing")])
    return {"tmp": tmp_path, "run": run, "project": proj, "comp": comp, "raw": raw}


def test_the_words_the_user_changed_between_unchanged_words():
    tool = ["I went to the zendeya", "interview with tom holland", "and it was great"]
    user = ["I went to the Zendaya", "interview with Tom Holland", "and it was amazing"]
    ch, counts = L.caption_changes(tool, user)
    assert [(c.heard, c.written, c.kind) for c in ch] == [("zendeya", "Zendaya", "words"), ("tom", "Tom", "case"),
                                                          ("holland", "Holland", "case")]
    assert counts["rewritten"] == 1                       # "great" -> "amazing" ends the text: no anchor after it
    # a caption's first word in capitals is the style, not a name
    ch, _ = L.caption_changes(["so we went"], ["So we went"])
    assert ch == []


def test_the_glossary_keeps_each_correction_once_with_its_videos(tmp_path):
    g = tmp_path / "caption_glossary.txt"
    a = L.WordChange("zendeya", "Zendaya", "words")
    assert L.add_to_glossary([a], "video-1", g) == [("zendeya", "Zendaya")]
    assert L.add_to_glossary([a], "video-2", g) == []
    assert L.read_glossary(g) == [("zendeya", "Zendaya", ["video-1", "video-2"])]
    # the captions' hot words read the written side
    from match_cuts import captions
    text = g.read_text(encoding="utf-8")
    assert "zendeya -> Zendaya" in text and text.startswith("# Caption glossary")
    assert captions.glossary_entries(g) == [("zendeya", "Zendaya")]


def test_the_run_is_found_from_the_projects_media_new_and_old_layout(world, tmp_path):
    seq = P.main_sequence(P.read(world["project"]))
    rf = L.find_run(seq)
    assert rf["dir"] == world["run"] and rf["edit"].name == "1_edit.xml"
    old = tmp_path / "old_output"
    (old / "media").mkdir(parents=True)
    (old / "recreated_edit.xml").write_text("<xmeml/>", encoding="utf-8")
    assert L.run_files(old)["captions"].name == "captions.srt"
    with pytest.raises(L.LearnError):
        L.find_run(seq, tmp_path)                          # not a run folder


def test_learn_makes_a_test_case_the_glossary_and_the_git_commands(world):
    cases, g = world["tmp"] / "cases", world["tmp"] / "caption_glossary.txt"
    res = L.learn(world["project"], cases_dir=cases, glossary=g)
    case = res["case"]
    assert case == cases / "zendaya-interview" and not res["updated"]
    assert {p.name for p in case.iterdir()} == {"competitor.mp4", "raw.mp4", "answer.srt", "answer_edit.json",
                                                "case.json", "learned.json"}
    assert (case / "raw.mp4").read_bytes() == world["raw"].read_bytes()      # under 100 MB: copied as it is
    assert "Tom Holland" in (case / "answer.srt").read_text(encoding="utf-8")
    edit = json.loads((case / "answer_edit.json").read_text(encoding="utf-8"))
    assert [(p["start"], p["src_in"]) for p in edit["audio"]] == [(0.0, pytest.approx(170.5)),
                                                                 (0.9, pytest.approx(180.0)),
                                                                 (2.6, pytest.approx(200.0))]
    assert json.loads((case / "case.json").read_text(encoding="utf-8"))["timeline"] == "edit"
    e = res["edit"]
    assert e["starts"][:2] == [pytest.approx(0.1), pytest.approx(0.0)]
    assert e["ends"][:2] == [pytest.approx(0.0), pytest.approx(0.2)]
    assert e["removed"] == 1 and e["added"] == 1 and e["moved"] == [pytest.approx(-108.0)]
    assert [h for h, _w, _v in L.read_glossary(g)] == ["zendeya", "tom", "holland"]
    lines = L.summary(res)
    assert any("git add" in x and "cases/zendaya-interview" in x.replace("\\", "/") for x in lines)
    assert any("'zendeya' -> 'Zendaya'" in x for x in lines)
    # the same competitor again: the case is updated, not doubled
    again = L.learn(world["project"], cases_dir=cases, glossary=g)
    assert again["case"] == case and again["updated"]


def test_a_project_of_another_run_is_refused(world):
    write_edit_xml(world["run"] / "1_edit.xml", [(0.0, 3.0, 500.0, 0.0)])     # a later run overwrote the folder
    with pytest.raises(L.LearnError, match="not the run it was made from"):
        L.learn(world["project"], cases_dir=world["tmp"] / "cases", glossary=world["tmp"] / "g.txt")


def test_the_same_change_on_several_videos_suggests_a_new_default_and_changes_nothing():
    rec = {"edit": {"starts": [0.0] * 5, "ends": [0.2, 0.25, 0.2, 0.0, 0.3], "removed": 0, "added": 0, "moved": [],
                    "zoomed": []}}
    two = [dict(rec, video=f"v{i}") for i in range(2)]
    assert L.suggestions(two) == []                                       # two videos: not yet
    three = [dict(rec, video=f"v{i}") for i in range(3)]
    s = L.suggestions(three)
    assert len(s) == 1 and "clips end later on 3 videos" in s[0] and "--pad-after 0.38 instead of 0.15" in s[0]
    from match_cuts.config import Config
    assert Config().pad_after == 0.15                                      # nothing changed


def test_a_run_whose_raw_is_another_video_is_refused(world):
    """The older flat output folder keeps only the latest run: a project made from an earlier one must not be compared
    with it. The project's own record of the RAW (size, frame rate, length) decides."""
    props = {"width": 1280, "height": 720, "fps": 59.94, "duration": 283.8}
    other = {"raw": {"width": 1920, "height": 1080, "fps": "24000/1001", "duration_s": 143.7}}
    msg = L.media_mismatch(props, other)
    assert "1920x1080 against 1280x720" in msg and "23.976 fps against 59.940" in msg and "143.7 s against 283.8 s" in msg
    same = {"raw": {"width": 1280, "height": 720, "fps": "60000/1001", "duration_s": 283.75}}   # a smaller copy too
    assert L.media_mismatch(props, same) is None and L.media_mismatch({}, other) is None


def test_only_real_corrections_go_into_the_glossary():
    ch, _ = L.caption_changes(["so i want to go to the school now ok", "and it was like crazy right there"],
                              ["so i wanna go to the School now ok", "and it was LIKE crazy right there"])
    got = {(c.heard, c.written): c.glossary for c in ch}
    assert got == {("want to", "wanna"): True,              # spelled alike: your way of writing it
                   ("school", "School"): False,             # an ordinary word in capitals: style
                   ("like", "LIKE"): False}                 # emphasis
    ch, _ = L.caption_changes(["we saw it 4 times and then"], ["we saw it fake name and times and then"])
    assert [c.glossary for c in ch] == [False]              # other words, not a spelling


def test_the_cuts_come_from_the_sound_when_the_picture_is_an_after_effects_comp(world):
    seq = P.main_sequence(P.read(world["project"]))
    seq.items = [it for it in seq.items if it.kind != P.VIDEO]           # the picture: a linked comp instead
    pic, snd = L.user_clips(seq, "raw.mp4")
    assert pic == [] and [round(c.r0, 1) for c in snd] == [170.5, 180.0, 200.0]
    tool_pic, tool_snd, _fps, _w = L.tool_clips(world["run"] / "1_edit.xml")
    e = L.edit_changes(tool_snd, snd, tool_pic, pic)
    assert e["framing"].startswith("not compared") and e["starts"][:2] == [pytest.approx(0.1), pytest.approx(0.0)]
