"""prproj.py: reading a saved Premiere project -- clips, their media and ranges, Motion, text graphics, caption tracks
and the edit's audio (a small project built here with Premiere's object layout)."""
from __future__ import annotations

import base64
import gzip
import struct

import pytest

from match_cuts import prproj as P

T = P.TICKS


def flat(*strings: str) -> str:
    """A flatbuffer-like blob: some header bytes, then each string length-prefixed and NUL-terminated (as Premiere
    stores a text graphic's Source Text / a caption block); the text is the last one."""
    b = b"\x0c\x00\x00\x00\x00\x00\x06\x00"
    for s in strings:
        raw = s.encode("utf-8")
        b += struct.pack("<I", len(raw)) + raw + b"\x00"
        b += b"\x00" * (-len(b) % 4)
    return base64.b64encode(b).decode()


def project(tmp_path, shared: bool = False, hidden: bool = False, layers: bool = False):
    """A finished project; ``shared``: also text that Premiere stores once (BinaryHash) -- a caption left as
    imported (its block empty, the data in the imported caption file's block) and a graphic whose Source Text
    another graphic holds; ``hidden``: the caption track muted and a muted video track of more graphics (V3), as your
    finished projects keep the imported caption track muted next to the graphics you made."""
    objs = []
    nid = [100]

    def oid() -> str:
        nid[0] += 1
        return str(nid[0])

    def clip_item(kind, start, end, name, media_uid=None, src_in=0.0, comps=(), speed=None):
        sub, clip, chain = oid(), oid(), oid()
        it = oid()
        src = oid()
        mtag = "VideoMediaSource" if kind == "video" else "AudioMediaSource"
        objs.append(f'<{"VideoClipTrackItem" if kind == "video" else "AudioClipTrackItem"} ObjectID="{it}">'
                    f'<ClipTrackItem Version="8"><ComponentOwner Version="1"><Components ObjectRef="{chain}"/>'
                    f'</ComponentOwner><TrackItem Version="3"><Start>{int(start * T)}</Start><End>{int(end * T)}</End>'
                    f'</TrackItem><SubClip ObjectRef="{sub}"/></ClipTrackItem></{"VideoClipTrackItem" if kind == "video" else "AudioClipTrackItem"}>')
        objs.append(f'<SubClip ObjectID="{sub}"><Clip ObjectRef="{clip}"/><Name>{name}</Name></SubClip>')
        sp = f"<PlaybackSpeed>{speed}</PlaybackSpeed>" if speed else ""
        objs.append(f'<{"VideoClip" if kind == "video" else "AudioClip"} ObjectID="{clip}"><Clip Version="18">'
                    f'<Source ObjectRef="{src}"/><InPoint>{int(src_in * T)}</InPoint>'
                    f'<OutPoint>{int((src_in + end - start) * T)}</OutPoint>{sp}</Clip>'
                    f'</{"VideoClip" if kind == "video" else "AudioClip"}>')
        objs.append(f'<{mtag} ObjectID="{src}"><MediaSource Version="4">'
                    + (f'<Media ObjectURef="{media_uid}"/>' if media_uid else "") + f'</MediaSource></{mtag}>')
        crefs = ""
        for k, (match, params) in enumerate(comps):
            cid = oid()
            prefs = ""
            for j, (pname, ptag, value_xml) in enumerate(params):
                pid = oid()
                objs.append(f'<{ptag} ObjectID="{pid}"><Name>{pname}</Name>{value_xml}</{ptag}>')
                prefs += f'<Param Index="{j}" ObjectRef="{pid}"/>'
            objs.append(f'<VideoFilterComponent ObjectID="{cid}"><Component Version="6"><Params Version="1">{prefs}'
                        f'</Params></Component><MatchName>{match}</MatchName></VideoFilterComponent>')
            crefs += f'<Component Index="{k}" ObjectRef="{cid}"/>'
        objs.append(f'<VideoComponentChain ObjectID="{chain}"><ComponentChain Version="3"><Components Version="1">'
                    f'{crefs}</Components></ComponentChain></VideoComponentChain>')
        return it

    motion = ("AE.ADBE Motion", [("Position", "PointComponentParam",
                                  "<StartKeyframe>-91445760000000000,0.4:0.5,0,0,0,0,0,0,5,4,0,0,0,0</StartKeyframe>"),
                                 ("Scale", "VideoComponentParam",
                                  "<StartKeyframe>-91445760000000000,120.,0,0,0,0,0,0</StartKeyframe>")])

    def text(s, h=None, empty=False):
        attr = f' BinaryHash="{h}"' if h else ""
        value = (f"<StartKeyframeValue Encoding=\"base64\"{attr}/>" if empty else
                 f"<StartKeyframeValue Encoding=\"base64\"{attr}>{flat('AnimationType', 'ArialMT', s)}"
                 "</StartKeyframeValue>")
        return ("AE.ADBE Text", [("Source Text", "ArbVideoComponentParam", value)])

    v1 = [clip_item("video", 0.0, 1.0, "S01 raw.mp4", "m-raw", 170.4, [motion]),
          clip_item("video", 1.0, 2.5, "S02 raw.mp4", "m-raw", 180.0, [motion])]
    v2 = [clip_item("video", 0.0, 0.6, "Graphic", "m-gfx", 3600.0, [text("So as")]),
          clip_item("video", 0.6, 1.2, "Graphic", "m-gfx", 3600.0, [text("a joke", "h-gfx" if shared else None)])]
    if shared:                                          # the same words again: saved empty, the data shared
        v2.append(clip_item("video", 1.2, 1.8, "Graphic", "m-gfx", 3600.0, [text("a joke", "h-gfx", empty=True)]))
    v3 = [clip_item("video", 0.2 * k, 0.2 * k + 0.2, "Graphic", "m-gfx", 3600.0, [text(f"before edit {k}")])
          for k in range(3)] if hidden else []
    if layers:                                          # graphics of several text layers (video4: an extra, empty one)
        empty = ("AE.ADBE Text", [("Source Text", "ArbVideoComponentParam",
                                   f'<StartKeyframeValue Encoding="base64">{flat("MinionPro-Regular")}'
                                   "</StartKeyframeValue>")])
        v2 += [clip_item("video", 1.8, 2.4, "Graphic", "m-gfx", 3600.0, [empty, text("That would have")]),
               clip_item("video", 2.4, 3.0, "Graphic", "m-gfx", 3600.0, [text("been fun"), empty]),
               clip_item("video", 3.0, 3.6, "Graphic", "m-gfx", 3600.0, [text("two"), text("lines")]),
               clip_item("video", 3.6, 4.2, "Graphic", "m-gfx", 3600.0, [empty])]
    a1 = [clip_item("audio", 0.0, 1.0, "S01 raw.mp4 audio", "m-raw", 170.4),
          clip_item("audio", 1.0, 2.5, "S02 raw.mp4 audio", "m-raw", 180.0)]
    a2 = [clip_item("audio", 0.0, 2.5, "competitor.mp4", "m-comp", 0.0)]
    blk = oid()
    objs.append(f'<Block ObjectID="{blk}"><FormattedTextData Encoding="base64">{flat("ArialMT", "Hello there")}'
                "</FormattedTextData></Block>")
    cap = oid()
    objs.append(f'<CaptionDataClipTrackItem ObjectID="{cap}"><DataClipTrackItem Version="1"><ClipTrackItem Version="8">'
                f"<TrackItem Version=\"3\"><Start>{int(0.2 * T)}</Start><End>{int(0.9 * T)}</End></TrackItem>"
                f'</ClipTrackItem></DataClipTrackItem><BlockVector Version="1"><BlockVectorItem Index="0" '
                f'ObjectRef="{blk}"/></BlockVector></CaptionDataClipTrackItem>')

    caps = [cap]
    if shared:
        src_blk, own_blk, cap2 = oid(), oid(), oid()
        objs.append(f'<Block ObjectID="{src_blk}"><FormattedTextData Encoding="base64" BinaryHash="h-cap">'
                    f'{flat("ArialMT", "as imported")}</FormattedTextData></Block>')       # on no track
        objs.append(f'<Block ObjectID="{own_blk}"><FormattedTextData Encoding="base64" BinaryHash="h-cap"/></Block>')
        objs.append(f'<CaptionDataClipTrackItem ObjectID="{cap2}"><DataClipTrackItem Version="1"><ClipTrackItem '
                    f'Version="8"><TrackItem Version="3"><Start>{int(1.0 * T)}</Start><End>{int(1.5 * T)}</End>'
                    f'</TrackItem></ClipTrackItem></DataClipTrackItem><BlockVector Version="1"><BlockVectorItem '
                    f'Index="0" ObjectRef="{own_blk}"/></BlockVector></CaptionDataClipTrackItem>')
        caps.append(cap2)

    def track(tag, uid, items, muted=False):
        refs = "".join(f'<TrackItem Index="{i}" ObjectRef="{x}"/>' for i, x in enumerate(items))
        inner = (f'<ClipTrack Version="2"><ClipItems Version="3"><TrackItems Version="1">{refs}</TrackItems>'
                 f'</ClipItems><Track Version="1"><IsMuted>{"true" if muted else "false"}</IsMuted></Track></ClipTrack>')
        if tag == "CaptionDataClipTrack":                   # Premiere's layout: the caption track's flags one level down
            inner = f'<DataClipTrack Version="1">{inner}</DataClipTrack>'
        objs.append(f'<{tag} ObjectUID="{uid}">{inner}</{tag}>')

    track("VideoClipTrack", "vt1", v1)
    track("VideoClipTrack", "vt2", v2)
    if hidden:
        track("VideoClipTrack", "vt3", v3, muted=True)
    track("AudioClipTrack", "at1", a1)
    track("AudioClipTrack", "at2", a2)
    track("CaptionDataClipTrack", "ct1", caps, muted=hidden)
    for uid, path in (("m-raw", "C:\\x\\output\\media\\raw.mp4"), ("m-comp", "C:\\x\\input\\competitor.mp4"),
                      ("m-gfx", "1196574294")):
        objs.append(f'<Media ObjectUID="{uid}"><ActualMediaFilePath>{path}</ActualMediaFilePath></Media>')
    objs.append('<VideoTrackGroup ObjectID="10"><TrackGroup Version="1"><Tracks Version="1">'
                '<Track Index="0" ObjectURef="vt1"/><Track Index="1" ObjectURef="vt2"/>'
                + ('<Track Index="2" ObjectURef="vt3"/>' if hidden else "") + '</Tracks>'
                '<FrameRate>4233600000</FrameRate></TrackGroup><FrameRect>0,0,1080,1920</FrameRect></VideoTrackGroup>')
    objs.append('<AudioTrackGroup ObjectID="11"><TrackGroup Version="1"><Tracks Version="1">'
                '<Track Index="0" ObjectURef="at1"/><Track Index="1" ObjectURef="at2"/></Tracks></TrackGroup>'
                '</AudioTrackGroup>')
    objs.append('<DataTrackGroup ObjectID="12"><TrackGroup Version="1"><Tracks Version="1">'
                '<Track Index="0" ObjectURef="ct1"/></Tracks></TrackGroup></DataTrackGroup>')
    seq = ('<Sequence ObjectUID="seq-1"><TrackGroups Version="1">'
           '<TrackGroup Version="1" Index="0"><First>v</First><Second ObjectRef="10"/></TrackGroup>'
           '<TrackGroup Version="1" Index="1"><First>a</First><Second ObjectRef="11"/></TrackGroup>'
           '<TrackGroup Version="1" Index="2"><First>d</First><Second ObjectRef="12"/></TrackGroup>'
           '</TrackGroups><Name>Recreated Edit (Premiere)</Name></Sequence>')
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<PremiereData Version="3">\n\t' + "\n\t".join([seq] + objs) + \
          "\n</PremiereData>\n"
    p = tmp_path / "finished.prproj"
    p.write_bytes(gzip.compress(xml.encode("utf-8")))
    return p


def test_read_a_project(tmp_path):
    proj = P.read(project(tmp_path))
    s = P.main_sequence(proj)
    assert s.name == "Recreated Edit (Premiere)" and s.fps == pytest.approx(60.0) and (s.width, s.height) == (1080, 1920)
    assert s.tracks("video") == [1, 2] and s.tracks("audio") == [1, 2] and s.tracks("caption") == [1]
    v1 = s.track("video", 1)
    assert [(it.start, it.end, it.src_in) for it in v1] == [(0.0, 1.0, pytest.approx(170.4)),
                                                           (1.0, 2.5, pytest.approx(180.0))]
    assert v1[0].media.endswith("raw.mp4") and v1[0].position == (0.4, 0.5) and v1[0].scale == 120.0
    assert P.file_name(v1[0].media) == "raw.mp4"
    assert s.duration == 2.5


def test_captions_from_a_caption_track_and_from_graphics(tmp_path):
    s = P.main_sequence(P.read(project(tmp_path)))
    cc = P.captions_of(s)                                   # a caption track wins
    assert [(c.kind, c.text, c.start) for c in cc] == [("caption", "Hello there", pytest.approx(0.2))]
    s.items = [it for it in s.items if it.kind != "caption"]
    gfx = P.captions_of(s)                                  # else the track of text graphics
    assert [(c.text, c.start, c.end) for c in gfx] == [("So as", 0.0, 0.6), ("a joke", 0.6, 1.2)]
    assert all(c.is_graphic for c in gfx)


def test_text_that_premiere_stores_once_is_read_from_its_shared_copy(tmp_path):
    """Premiere stores a binary value once per project: an element whose data is already stored (the same
    BinaryHash) is saved empty. Your video017_fixed.prproj: the 34 captions left as imported shared their text with
    the imported caption file's blocks and read empty -- only the 9 you edited (their own copy) were read."""
    s = P.main_sequence(P.read(project(tmp_path, shared=True)))
    assert [(c.text, c.start) for c in P.captions_of(s)] == [("Hello there", pytest.approx(0.2)),
                                                           ("as imported", pytest.approx(1.0))]
    s.items = [it for it in s.items if it.kind != "caption"]
    assert [(c.text, c.start) for c in P.captions_of(s)] == [("So as", 0.0), ("a joke", 0.6),
                                                           ("a joke", pytest.approx(1.2))]


def test_captions_a_hidden_track_never_counts(tmp_path):
    """What the finished video shows: a muted caption track (your finished projects keep the imported one muted
    next to the graphics, its text from before your edits) and a muted track of more graphics lose to the visible
    graphics, however many captions they hold."""
    s = P.main_sequence(P.read(project(tmp_path, hidden=True)))
    assert s.hidden == {("caption", 1), ("video", 3)}
    assert [(c.text, c.track) for c in P.captions_of(s)] == [("So as", 2), ("a joke", 2)]
    assert P.hidden_text(s) == [("caption", 1, 1), ("video", 3, 3)]          # what is left out, to name it
    assert [P.track_name(k, n) for k, n, _c in P.hidden_text(s)] == ["C1", "V3"]
    s.hidden.clear()                                        # (unhidden, the caption track would win again)
    assert [c.text for c in P.captions_of(s)] == ["Hello there"]


def test_a_graphics_text_layers_an_empty_one_is_not_its_font_name(tmp_path):
    """An empty text layer keeps only its font name: video4's project has a graphic of an extra, empty layer
    ('MinionPro-Regular') before its real one ('That would have'); read as the last layer, either order would give
    the font, or blank the text. A text always comes after its font (and animation) names in the buffer."""
    assert P.flat_text(flat("MinionPro-Regular")) == ""
    assert P.flat_text(flat("ArialMT", "Hi")) == "Hi"
    s = P.main_sequence(P.read(project(tmp_path, layers=True)))
    v2 = s.track("video", 2)
    assert [it.text for it in v2] == ["So as", "a joke", "That would have", "been fun", "two\nlines", ""]
    assert [c.text for c in P.captions_of(s.__class__(s.name, s.fps, s.width, s.height,
                                                        [it for it in s.items if it.kind != "caption"]))][2:] == [
        "That would have", "been fun", "two\nlines"]                         # the empty graphic is no caption


def test_captions_a_disabled_caption_is_not_shown(tmp_path):
    p = project(tmp_path)
    xml = gzip.decompress(p.read_bytes()).decode("utf-8")
    i = xml.index("<CaptionDataClipTrackItem")
    j = xml.index("</ClipTrackItem>", i)
    p.write_bytes(gzip.compress((xml[:j] + "<Disabled>true</Disabled>" + xml[j:]).encode("utf-8")))
    s = P.main_sequence(P.read(p))
    assert [c.enabled for c in s.track("caption", 1)] == [False]
    assert [c.text for c in P.captions_of(s)] == ["So as", "a joke"]      # the graphics: the only captions shown
    # where Premiere keeps the flag in a caption is not known from real files: anywhere in the item counts, as for
    # a video clip
    k = xml.index(">", xml.index("<CaptionDataClipTrackItem")) + 1
    p.write_bytes(gzip.compress((xml[:k] + "<Disabled>true</Disabled>" + xml[k:]).encode("utf-8")))
    assert [c.enabled for c in P.main_sequence(P.read(p)).track("caption", 1)] == [False]


def test_audio_pieces_take_an_unmuted_track_first(tmp_path):
    """Your RAW's sound on two tracks, the longer one muted (a backup, or the original under an enhanced copy): the
    edit is the one that is heard -- the muted one only when nothing else plays the RAW."""
    a = [P.Item("audio", 1, 0.0, 3.0, media="C:\\x\\raw.mp4", src_in=10.0),          # muted: longer
         P.Item("audio", 2, 0.0, 1.0, media="C:\\x\\raw.mp4", src_in=20.0),
         P.Item("audio", 2, 1.0, 2.0, media="C:\\x\\raw.mp4", src_in=30.0)]
    s = P.Sequence("edit", 60.0, 1080, 1920, a, hidden={("audio", 1)})
    assert [p["src_in"] for p in P.audio_pieces(s, "raw.mp4")] == [20.0, 30.0]
    s.items = a[:1]
    assert [p["src_in"] for p in P.audio_pieces(s, "raw.mp4")] == [10.0]


def test_audio_pieces_pick_the_edit_not_the_reference_track(tmp_path):
    s = P.main_sequence(P.read(project(tmp_path)))
    a = P.audio_pieces(s)                                   # raw.mp4 is cut into clips; competitor.mp4 plays whole
    assert [(p["start"], p["end"], p["src_in"], p["media"]) for p in a] == [
        (0.0, 1.0, pytest.approx(170.4), "raw.mp4"), (1.0, 2.5, pytest.approx(180.0), "raw.mp4")]
    assert P.audio_pieces(s, "competitor.mp4")[0]["end"] == 2.5


def test_flat_text_takes_the_last_string():
    assert P.flat_text(flat("AnimationType", "ArialMT", "I\u2019m actually")) == "I\u2019m actually"
    assert P.flat_text("") == "" and P.flat_text("not base64 !!") == ""


def test_what_premiere_knows_about_a_media_file(tmp_path):
    """A media file's size, frame rate and length as Premiere measured it (learn: is the run's RAW the project's?)."""
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<PremiereData Version="3">\n\t'
           '<Media ObjectUID="m-raw"><VideoStream ObjectRef="153"/><AudioStream ObjectRef="152"/>'
           '<ActualMediaFilePath>C:/x/output/001/extras/media/raw.mp4</ActualMediaFilePath></Media>\n\t'
           '<VideoStream ObjectID="153"><FrameRate>4237833600</FrameRate><FrameRect>0,0,1280,720</FrameRect>'
           f'<Duration>{int(283.75 * T)}</Duration></VideoStream>\n\t'
           f'<AudioStream ObjectID="152"><Duration>{int(283.75 * T)}</Duration></AudioStream>\n</PremiereData>\n')
    p = tmp_path / "m.prproj"
    p.write_bytes(gzip.compress(xml.encode("utf-8")))
    m = P.read(p).media["C:/x/output/001/extras/media/raw.mp4"]
    assert (m["width"], m["height"]) == (1280, 720) and m["fps"] == pytest.approx(59.94, abs=0.001)
    assert m["duration"] == pytest.approx(283.75)
