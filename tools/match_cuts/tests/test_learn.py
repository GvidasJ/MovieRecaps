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
    (run / "extras" / "report.md").write_text("# the run's report: it finished\n", encoding="utf-8")
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


def test_the_tools_framing_is_read_in_its_source_size_as_premiere_shows_it(tmp_path):
    """Premiere reads a clip's Basic Motion <center> in units of its SOURCE frame size (export_xml_edl.premiere_center).
    video017 (a 1280x720 RAW in the 1080x1920 sequence): learned.json said every clip had moved -10.2 px, where the
    user had moved none -- the tool's offset was taken in the sequence's width."""
    cx = -0.0508428
    xml = write_edit_xml(tmp_path / "1_edit.xml", [(0.0, 1.0, 10.0, cx), (1.0, 2.0, 20.0, cx), (2.0, 3.0, 30.0, 0.1)])
    full = ('<file id="file-raw"><name>raw.mp4</name><media><video><samplecharacteristics><width>1280</width>'
            "<height>720</height></samplecharacteristics></video></media></file>")
    t = xml.read_text(encoding="utf-8")
    t = t.replace('<clipitem id="v1"><name>S01 raw.mp4</name>', '<clipitem id="v1"><name>S01 raw.mp4</name>' + full, 1)
    t = t.replace('<clipitem id="v2"><name>S02 raw.mp4</name>',      # later clips only name the file
                  '<clipitem id="v2"><name>S02 raw.mp4</name><file id="file-raw"/>', 1)
    xml.write_text(t, encoding="utf-8")
    assert L.source_widths(xml) == {"v1": 1280.0, "v2": 1280.0}
    pic, _snd, _fps, width = L.tool_clips(xml)
    assert width == 1080
    assert [c.dx for c in pic] == [pytest.approx(cx * 1280), pytest.approx(cx * 1280),
                                   pytest.approx(0.1 * 1080)]           # no size known at all: the sequence's width
    assert L.tool_clips(xml, raw_width=1280.0)[0][2].dx == pytest.approx(0.1 * 1280)   # else the run's RAW width
    # the user's picture where the tool put it (Position 474.92 px = 0.439742 of the 1080 px frame): nothing moved
    user = [L.Clip(c.t0, c.t1, c.r0, c.r1, (0.439742 - 0.5) * 1080 if k < 2 else c.dx, c.scale)
            for k, c in enumerate(pic)]
    e = L.edit_changes(pic, user, pic, user)
    assert e["moved"] == [pytest.approx(0.0, abs=0.1)] * 3


def test_captions_on_a_hidden_track_are_named_and_never_an_empty_key(world):
    """A project whose captions are all on a hidden track (its eye closed before saving) shows no captions: learn
    says where they are and writes no answer.srt -- an empty key would score every run against nothing -- and an
    older key of the same competitor is removed, not kept with the new edit."""
    cases, g = world["tmp"] / "cases", world["tmp"] / "caption_glossary.txt"
    L.learn(world["project"], cases_dir=cases, glossary=g)
    case = cases / "zendaya-interview"
    assert (case / "answer.srt").is_file()
    p = world["project"]
    xml = gzip.decompress(p.read_bytes()).decode("utf-8")
    hide = "</TrackItems></ClipItems></ClipTrack></CaptionDataClipTrack>"
    assert hide in xml
    p.write_bytes(gzip.compress(xml.replace(hide, '</TrackItems></ClipItems><Track Version="1"><IsMuted>true'
                                                  "</IsMuted></Track></ClipTrack></CaptionDataClipTrack>").encode()))
    res = L.learn(p, cases_dir=cases, glossary=g)
    assert res["updated"] and not (case / "answer.srt").exists()
    note = res["files"]["answer.srt"]
    assert note.startswith("not written: your captions are on C1 (3 captions), hidden") and "old one removed" in note
    assert any(x.startswith("  Captions: not read -- your captions are on C1") for x in L.summary(res))
    rec = json.loads((case / "learned.json").read_text(encoding="utf-8"))
    assert rec["captions"]["count"] == 0 and rec["captions"]["changes"] == []
    assert (case / "answer_edit.json").is_file()                        # the cuts are still learned


def test_your_edit_is_read_from_the_track_that_is_shown(world):
    """A hidden video track of the RAW (a backup copy of the edit, say) never stands for your edit, even when it plays
    more of the RAW than the visible one."""
    raw = str(world["run"] / "extras" / "media" / "raw.mp4")
    items = [P.Item("video", 1, 0.0, 1.0, media=raw, src_in=10.0), P.Item("video", 1, 1.0, 2.0, media=raw, src_in=20.0),
             P.Item("video", 2, 0.0, 3.0, media=raw, src_in=50.0)]                      # hidden V2: longer
    seq = P.Sequence("edit", 60.0, 1080, 1920, items, hidden={("video", 2)})
    pic, _snd = L.user_clips(seq, "raw.mp4")
    assert [c.r0 for c in pic] == [10.0, 20.0]
    seq.hidden.clear()
    assert [c.r0 for c in L.user_clips(seq, "raw.mp4")[0]] == [50.0]


def test_the_glossary_keeps_each_correction_once_with_its_videos(tmp_path):
    g = tmp_path / "caption_glossary.txt"
    a = L.WordChange("zendeya", "Zendaya", "words")
    assert L.add_to_glossary([a], "video-1", g) == [("zendeya", "Zendaya")]
    assert L.add_to_glossary([a], "video-2", g) == []
    assert L.read_glossary(g) == [("zendeya", "Zendaya", ["video-1", "video-2"])]
    # the captions read the written side after the transcription
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


def test_a_run_that_has_not_finished_is_never_read(world, tmp_path):
    """Task 10: 1_edit.xml and cutlist.json are written long before the captions, the checks and the report -- a run
    still going on (or one that stopped early) is refused, whether found from the project, given, or reused by a
    finished folder's learn (Task 8: video4's run read mid-run kept 1 change of 19 instead of 3)."""
    (world["run"] / "extras" / "report.md").unlink()
    seq = P.main_sequence(P.read(world["project"]))
    for call in (lambda: L.find_run(seq), lambda: L.find_run(seq, world["run"])):
        with pytest.raises(L.LearnError, match="not a finished run"):
            call()
    assert not L.finished_run(world["run"])
    (world["run"] / "extras" / "report.md").write_text("done", encoding="utf-8")
    assert L.finished_run(world["run"]) and L.find_run(seq)["dir"] == world["run"]


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
    assert len(s) == 1 and "clips end later on 3 videos" in s[0] and "--pad-after 0.28 instead of 0.05" in s[0]
    from match_cuts.config import Config
    assert Config().pad_after == 0.05                                      # nothing changed


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
    assert got == {("want to", "wanna"): False,             # a spoken form: the video's style (video4 the other way)
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


# ---- finished folders (Task 8) ---------------------------------------------------------------------------------------

def _run(path: Path, comp: Path, raw: Path, segs, screen=None, edit=None, caps=None) -> Path:
    """A run folder: its cut list (segments [(comp_in, comp_out, type, raw second, scale)] at 30 fps of a 640x480
    25 fps RAW, the picture in a 1080x810 box), the captions it read from the screen, its 1_edit.xml and SRT."""
    run = path / "001"
    (run / "extras" / "debug").mkdir(parents=True)
    rows = [{"id": i, "type": t, "comp_in": a, "comp_out": b, "raw_in_seconds": r, "speed": 1.0,
             "transform": {"scale": s, "rotation_deg": 0.0, "tx": 540 - 320 * s, "ty": 960 - 240 * s}}
            for i, (a, b, t, r, s) in enumerate(segs, start=1)]
    (run / "extras" / "cutlist.json").write_text(json.dumps({
        "competitor": {"fps": "30/1", "frames": segs[-1][1], "width": 1080, "height": 1920, "source_path": str(comp)},
        "raw": {"fps": "25/1", "width": 640, "height": 480, "source_path": str(raw)},
        "layout": {"box": {"x": 0.0, "y": 555.0, "w": 1080.0, "h": 810.0}}, "segments": rows}), encoding="utf-8")
    (run / "extras" / "debug" / "captions.json").write_text(json.dumps({"screen": [
        {"comp_in": a, "comp_out": b, "text": t} for a, b, t in (screen or [])]}), encoding="utf-8")
    write_edit_xml(run / "1_edit.xml", edit or [(0.0, 1.0, 10.0, 0.0)])
    (run / "2_captions.srt").write_text(srt(caps or []), encoding="utf-8")
    (run / "extras" / "report.md").write_text("# the run's report: it finished\n", encoding="utf-8")
    return run


@pytest.fixture
def finished(tmp_path):
    """A finished folder: the competitor, the RAW, your final video and project; the tool's run of the competitor and
    the run that read your final video (made beforehand: learn uses them as they are)."""
    d = tmp_path / "finished" / "video1"
    d.mkdir(parents=True)
    for n in ("competitor.mp4", "raw.mp4", "final.mp4"):
        (d / n).write_bytes(n.encode() * 64)
    write_project(d / "project.prproj", "C:/elsewhere/raw.mp4", [(0.0, 3.0, 0.0, None)],
                  [(0.0, 0.5, "We're the most"), (0.5, 1.2, "notorious gang"), (1.2, 2.0, "in the country")])
    # the competitor: 10-12 s, a cut to 20-22 s; the tool's edit keeps the gap 12-12.5 s and cuts at 20.5
    tool = _run(tmp_path / "runs" / "tool", d / "competitor.mp4", d / "raw.mp4",
                [(0, 60, "raw", 10.0, 1.6875), (60, 120, "raw", 20.0, 1.6875)],
                edit=[(0.0, 2.5, 10.0, 0.0), (2.5, 4.0, 20.5, 0.0)],
                caps=[(0.0, 0.5, "we're the most"), (0.5, 1.2, "notorius gang"), (1.2, 2.0, "in the country")])
    # yours: 10-12 s, a cut to 20.5-22 s, zoomed in (scale 2.25: three quarters of the RAW's width)
    user = _run(tmp_path / "runs" / "user", d / "final.mp4", d / "raw.mp4",
                [(0, 60, "raw", 10.0, 2.25), (60, 105, "raw", 20.5, 2.25)],
                screen=[(0, 15, "We're the most"), (15, 36, "notorious gang"), (36, 60, "in the country")])
    return {"tmp": tmp_path, "dir": d, "tool": tool, "user": user, "cases": tmp_path / "cases",
            "glossary": tmp_path / "glossary.txt"}


def _learn(f, **kw):
    return L.learn_folder(f["dir"], f["tmp"] / "runs", f["cases"], tool_run=f["tool"], user_run=f["user"],
                          glossary=f["glossary"], log=lambda *_a: None, **kw)


def test_a_finished_folder_your_cuts_from_final_mp4_and_your_captions_from_the_project(finished):
    r = _learn(finished)
    case = r["case"]
    assert case.name == "video1" and not r["updated"]
    assert {p.name for p in case.iterdir()} == {"competitor.mp4", "raw.mp4", "answer.srt", "answer_edit.json",
                                                "case.json", "learned.json"}
    assert "We're the most" in (case / "answer.srt").read_text(encoding="utf-8")       # the project's captions ...
    assert r["captions"]["source"] == "project" and "show on final.mp4's screen" in r["captions"]["why"]
    edit = json.loads((case / "answer_edit.json").read_text(encoding="utf-8"))
    assert edit["track"] == "picture" and [(p["start"], p["src_in"]) for p in edit["audio"]] == [(0.0, 10.0), (2.0, 20.5)]
    c = r["compare"]
    assert (c["tool"]["cuts"], c["tool"]["reproduced"]) == (1, 0)          # 12 -> 20.5 against the tool's 12.5 -> 20.5
    assert c["tool"]["near"] == 1 and c["length"] == {"yours": 3.5, "tool": 4.0, "competitor": 4.0}
    assert c["competitor_cuts_kept"] == 1                                  # the competitor's cut, trimmed
    assert c["framing"]["zoom_median"] == pytest.approx(2.25 / 1.6875, abs=1e-3)       # you show less of the RAW
    assert [(x.heard, x.written) for x in r["changes"]] == [("notorius", "notorious")]      # -> the glossary
    assert json.loads((case / "case.json").read_text(encoding="utf-8"))["timeline"] == "edit"
    lines = L.folder_summary({"done": [r], "skipped": [], "suggestions": [], "git": []})
    assert any("the tool reproduces 0/1 of your cuts" in x for x in lines)


def test_final_only_reads_your_captions_from_the_screen_and_ignores_the_project(finished):
    (finished["dir"] / "project.prproj").unlink()                    # not even needed
    r = _learn(finished, final_only=True)
    assert r["captions"]["source"] == "screen"
    srt_text = (r["case"] / "answer.srt").read_text(encoding="utf-8")
    assert "00:00:00,500 --> 00:00:01,200\nnotorious gang" in srt_text


def test_a_projects_captions_of_another_video_are_not_used(finished):
    write_project(finished["dir"] / "project.prproj", "C:/elsewhere/raw.mp4", [(0.0, 3.0, 0.0, None)],
                  [(0.0, 0.5, "Excuse me"), (0.5, 1.2, "did you phone us"), (1.2, 2.0, "the breakdown service")])
    r = _learn(finished)
    assert r["captions"]["source"] is None and "not this video's" in r["captions"]["why"]
    assert not (r["case"] / "answer.srt").exists() and (r["case"] / "answer_edit.json").is_file()


def test_folders_missing_a_file_or_not_belonging_together_are_skipped(finished, tmp_path):
    other = finished["dir"].parent / "video2"
    other.mkdir()
    (other / "final.mp4").write_bytes(b"x" * 64)
    with pytest.raises(L.LearnError, match="skipped: no competitor.mp4, raw.mp4, project.prproj"):
        L.learn_folder(other, tmp_path / "runs", finished["cases"], log=lambda *_a: None)
    lost = _run(tmp_path / "runs" / "lost", finished["dir"] / "final.mp4", finished["dir"] / "raw.mp4",
                [(0, 30, "raw", 10.0, 2.25), (30, 105, "not_in_raw", None, 2.25)])
    with pytest.raises(L.LearnError, match="final.mp4 does not come from raw.mp4"):
        L.learn_folder(finished["dir"], tmp_path / "runs", finished["cases"], tool_run=finished["tool"], user_run=lost,
                       log=lambda *_a: None)
    assert L.finished_folders(finished["dir"].parent) == [finished["dir"], other]


def test_a_finished_folder_without_final_mp4_takes_your_edit_from_the_project(finished):
    """video5: a Premiere project with no final.mp4 and no After Effects comp -- your cuts, audio cuts, framing and
    captions are the project's own: the answer key is its A1 clips of the RAW and its captions as they are, your
    framing its V1 clips' Motion; no run of a finished video is made."""
    d = finished["dir"]
    (d / "final.mp4").unlink()
    write_project(d / "project.prproj", "D:/downloads/episode 3.mp4",
                  [(0.0, 2.0, 10.0, 0.5), (2.0, 3.5, 20.5, 0.6)],          # yours: 10-12 s, a cut to 20.5-22 s
                  [(0.0, 0.5, "Were the most"), (0.5, 1.2, "notorious gang"), (1.2, 2.0, "in the country")])
    assert L.missing_files(d) == []
    r = L.learn_folder(d, finished["tmp"] / "runs", finished["cases"], tool_run=finished["tool"],
                       glossary=finished["glossary"], log=lambda *_a: None)
    case = r["case"]
    assert r["runs"]["user"] is None
    assert {p.name for p in case.iterdir()} == {"competitor.mp4", "raw.mp4", "answer.srt", "answer_edit.json",
                                                "case.json", "learned.json"}
    edit = json.loads((case / "answer_edit.json").read_text(encoding="utf-8"))
    assert edit["track"] == "sound" and edit["fps"] == pytest.approx(60.0) and "project.prproj" in edit["what"]
    assert [(p["start"], p["end"], p["src_in"]) for p in edit["audio"]] == [
        (0.0, 2.0, pytest.approx(10.0)), (2.0, 3.5, pytest.approx(20.5))]
    srt_text = (case / "answer.srt").read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:00,500\nWere the most" in srt_text           # as they are: no punctuation added
    assert r["captions"]["source"] == "project" and "as they are" in r["captions"]["why"]
    learned = json.loads((case / "learned.json").read_text(encoding="utf-8"))
    assert learned["kind"] == "project" and set(learned["runs"]) == {"tool"}
    assert learned["sound"]["yours"]["picture_clips"] == 2 and learned["sound"]["yours"]["framing"] == 2
    c = r["compare"]
    assert (c["tool"]["cuts"], c["tool"]["reproduced"], c["tool"]["near"]) == (1, 0, 1)    # 12 -> 20.5 vs 12.5 -> 20.5
    assert c["framing"]["pieces"] == 2                 # your framing (Motion) against the competitor's
    meta = json.loads((case / "case.json").read_text(encoding="utf-8"))
    assert meta["timeline"] == "edit" and "project.prproj holds it" in meta["notes"]
    lines = L.folder_summary({"done": [r], "skipped": [], "suggestions": [], "git": []})
    assert any("your edit read from project.prproj" in x for x in lines) and not any("None" in x for x in lines)


def test_your_edit_in_a_project_is_the_clips_of_the_raw_not_the_template(monkeypatch, tmp_path):
    """The project's RAW is the media its clips play most whose size, frame rate and length are the RAW's -- the
    template PNG on V2 and the competitor kept for reference are not your edit. The framing: the part of the RAW the
    template window shows (Position / Scale; Premiere's defaults where the project keeps none)."""
    raw = "C:/films/raw source.mp4"
    items = [P.Item(P.VIDEO, 1, 0.0, 2.0, media=raw, src_in=10.0, position=(0.5, 0.5), scale=300.0),
             P.Item(P.VIDEO, 1, 2.0, 3.0, media=raw, src_in=20.0),                     # Motion untouched
             P.Item(P.VIDEO, 2, 0.0, 3.0, media="C:/template/overlay.png"),
             P.Item(P.VIDEO, 3, 0.0, 3.0, media="C:/films/competitor.mp4", enabled=False),
             P.Item(P.AUDIO, 1, 0.0, 2.0, media=raw, src_in=10.0),
             P.Item(P.AUDIO, 1, 2.0, 3.0, media=raw, src_in=20.0)]
    project = P.Project("p", [P.Sequence("Edit", 60.0, 1080, 1920, items)],
                        {raw: {"width": 640, "height": 480, "fps": 25.0, "duration": 600.0},
                         "C:/template/overlay.png": {"width": 1080, "height": 1920},
                         "C:/films/competitor.mp4": {"width": 1080, "height": 1920, "fps": 30.0, "duration": 60.0}})
    monkeypatch.setattr(P, "read", lambda path: project)
    pic, snd, info = L.project_edit(tmp_path / "project.prproj", {"width": 640, "height": 480, "fps": "25/1",
                                                                    "duration_s": 600.0})
    assert info["media"] == raw and info["sound_clips"] == 2 and info["picture_clips"] == 2
    assert [(p.t0, p.raw) for p in snd.pieces] == [(0.0, 10.0), (2.0, 20.0)] and snd.what == "sound"
    v0, v1 = pic.pieces[0].view, pic.pieces[1].view
    # 300 %: the RAW 1920 x 1440 px centred -- the 998 px window shows 998 / 1920 of its width, centred
    assert v0[0] == pytest.approx(0.5, abs=1e-3) and v0[2] == pytest.approx(998 / 1920, abs=1e-3)
    assert v1[2] == pytest.approx(640 / 640, abs=1e-3)     # 100 %: 640 px, narrower than the window: all of it
    with pytest.raises(L.LearnError, match="no clip of project.prproj plays raw.mp4"):
        L.project_edit(tmp_path / "project.prproj", {"width": 1280, "height": 720, "fps": "30/1"})


def test_the_caption_row_is_the_text_that_changes_most():
    """video4's final: a static @-handle under the picture (one event of 751 frames) had won the caption band over 19
    captions in the middle of the picture; the row with the most events is the captions."""
    lay = {"captions": [{"type": "captions", "comp_in": 120, "comp_out": 871, "x": 584, "y": 1550, "w": 52, "h": 22}]
           + [{"type": "text", "comp_in": 40 * i, "comp_out": 40 * i + 30, "x": 380, "y": 1102 + (i % 3), "w": 320,
               "h": 46} for i in range(19)],
           "zones": [{"type": "captions", "x": 0, "y": 1530, "w": 1080, "h": 60}, {"type": "logo", "x": 1, "y": 1}]}
    out, moved = L.caption_rows(lay)
    assert moved and [z["type"] for z in out["zones"]] == ["logo"]
    caps = [e for e in out["captions"] if e["type"] == "captions"]
    assert len(caps) == 19 and all(1100 <= e["y"] <= 1105 for e in caps)
    same, moved2 = L.caption_rows(out)
    assert not moved2


def test_a_spoken_form_against_its_written_one_is_style_not_glossary():
    ch, _ = L.caption_changes(["and they said we're gonna do the meme", "really quickly now"],
                              ["and they said we're going to do the meme", "really quickly now"])
    assert [(c.heard, c.written, c.glossary) for c in ch] == [("gonna", "going to", False)]
    assert "style" in ch[0].why


def test_your_edit_is_placed_by_its_sound(tmp_path):
    """The run of your final video: its sound 25 ms after its picture (the render's A/V offset, taken out); the second
    clip's picture 30 ms ahead of its sound (a picture shifted by Topaz / After Effects: no cut of yours); a cutaway
    over the RAW's continuing sound (proved by the run's cutaway check) given that sound; a real cut after it."""
    from match_cuts import edit_score as E
    run = tmp_path / "001"
    (run / "extras" / "debug").mkdir(parents=True)
    segs = [{"id": 1, "type": "raw", "comp_in": 0, "comp_out": 60, "raw_in_seconds": 10.0, "speed": 1.0,
             "audio": {"corr": 0.95, "lag_ms": -25.0}},
            {"id": 2, "type": "raw", "comp_in": 60, "comp_out": 120, "raw_in_seconds": 11.03, "speed": 1.0,
             "audio": {"corr": 0.95, "lag_ms": -55.0}},
            {"id": 3, "type": "not_in_raw", "comp_in": 120, "comp_out": 180},
            {"id": 4, "type": "raw", "comp_in": 180, "comp_out": 240, "raw_in_seconds": 20.0, "speed": 1.0,
             "audio": {"corr": 0.95, "lag_ms": -25.0}}]
    (run / "extras" / "cutlist.json").write_text(json.dumps({
        "competitor": {"fps": "60/1", "frames": 240, "width": 1080, "height": 1920},
        "raw": {"fps": "60/1", "width": 1280, "height": 720}, "segments": segs,
        "audio": {"av_offset": {"status": "not_measured", "lag_ms": 0.0}}}), encoding="utf-8")
    (run / "extras" / "debug" / "decisions.jsonl").write_text(json.dumps({
        "stage": "broll", "decision": "no_broll", "kept": [], "replaced": [
            {"segment": 3, "comp_in": 120, "comp_out": 180, "corr": 0.9, "how": "audio", "bridged": False,
             "line": "S02 continued", "raw_in_seconds": 12.03, "showed": "NOT-IN-RAW insert"}]}) + "\n",
        encoding="utf-8")
    pic = E.Edit.from_cutlist(run / "extras" / "cutlist.json")
    assert [round(c.t, 2) for c in pic.cuts()] == [1.0, 2.0, 3.0]
    snd, info = L.sound_edit(run, pic)
    assert snd.what == "sound" and info["offset_ms"] == -25.0
    assert [(round(c.t, 2), round(c.out, 3), round(c.into, 3)) for c in snd.cuts()] == [(3.0, 13.0, 20.0)]
    assert info["moved"] == [{"start": 1.0, "end": 2.0, "frames": -1.8}]
    assert info["given"] == [{"start": 2.0, "end": 3.0, "raw": 12.0, "corr": 0.9, "showed": "NOT-IN-RAW insert"}]


def test_ordinary_words_heard_wrong_are_not_glossary_entries():
    """video1: "of" -> "to" and "you're getting" -> "you get in" are what was heard at one moment -- as glossary
    entries they would change every "of" the audio allows; a spelling (same letters) or a name stays."""
    ch, _ = L.caption_changes(["the gang of the country was here", "and you're getting it right now ok",
                               "a banknote-forging gang in the town", "and Toby said it was so"],
                              ["the gang to the country was here", "and you get in it right now ok",
                               "a banknote forging gang in the town", "and Tobey said it was so"])
    got = {(c.heard, c.written): c.glossary for c in ch}
    assert got[("of", "to")] is False and got[("you're getting", "you get in")] is False
    assert got[("banknote-forging", "banknote forging")] is True and got[("Toby", "Tobey")] is True


def test_the_glossary_is_not_given_to_the_speech_model(monkeypatch):
    """A hot word changes how the whole transcript is punctuated and capitalised, in videos that never say it too
    (Deadpool: 47 -> 42 captions exact with "others" and "Tobey" as hot words): the hot words are the allowlist's, and
    the glossary is applied after the transcription only (caption_recheck.glossary_readings)."""
    from match_cuts import caption_rules, captions
    monkeypatch.setattr(captions, "glossary_entries", lambda path=None: [("Toby", "Tobey"), ("other's", "others")])
    monkeypatch.setattr(caption_rules, "read_allowlist", lambda *a, **k: ["MJ", "MCU", "MJ", ""])
    assert captions.caption_hints() == ["MJ", "MCU"]
