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


def project(tmp_path):
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

    def text(s):
        return ("AE.ADBE Text", [("Source Text", "ArbVideoComponentParam",
                                  f"<StartKeyframeValue Encoding=\"base64\">{flat('AnimationType', 'ArialMT', s)}"
                                  "</StartKeyframeValue>")])

    v1 = [clip_item("video", 0.0, 1.0, "S01 raw.mp4", "m-raw", 170.4, [motion]),
          clip_item("video", 1.0, 2.5, "S02 raw.mp4", "m-raw", 180.0, [motion])]
    v2 = [clip_item("video", 0.0, 0.6, "Graphic", "m-gfx", 3600.0, [text("So as")]),
          clip_item("video", 0.6, 1.2, "Graphic", "m-gfx", 3600.0, [text("a joke")])]
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

    def track(tag, uid, items):
        refs = "".join(f'<TrackItem Index="{i}" ObjectRef="{x}"/>' for i, x in enumerate(items))
        objs.append(f'<{tag} ObjectUID="{uid}"><ClipTrack Version="2"><ClipItems Version="3"><TrackItems Version="1">'
                    f"{refs}</TrackItems></ClipItems></ClipTrack></{tag}>")

    track("VideoClipTrack", "vt1", v1)
    track("VideoClipTrack", "vt2", v2)
    track("AudioClipTrack", "at1", a1)
    track("AudioClipTrack", "at2", a2)
    track("CaptionDataClipTrack", "ct1", [cap])
    for uid, path in (("m-raw", "C:\\x\\output\\media\\raw.mp4"), ("m-comp", "C:\\x\\input\\competitor.mp4"),
                      ("m-gfx", "1196574294")):
        objs.append(f'<Media ObjectUID="{uid}"><ActualMediaFilePath>{path}</ActualMediaFilePath></Media>')
    objs.append('<VideoTrackGroup ObjectID="10"><TrackGroup Version="1"><Tracks Version="1">'
                '<Track Index="0" ObjectURef="vt1"/><Track Index="1" ObjectURef="vt2"/></Tracks>'
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


def test_audio_pieces_pick_the_edit_not_the_reference_track(tmp_path):
    s = P.main_sequence(P.read(project(tmp_path)))
    a = P.audio_pieces(s)                                   # raw.mp4 is cut into clips; competitor.mp4 plays whole
    assert [(p["start"], p["end"], p["src_in"], p["media"]) for p in a] == [
        (0.0, 1.0, pytest.approx(170.4), "raw.mp4"), (1.0, 2.5, pytest.approx(180.0), "raw.mp4")]
    assert P.audio_pieces(s, "competitor.mp4")[0]["end"] == 2.5


def test_flat_text_takes_the_last_string():
    assert P.flat_text(flat("AnimationType", "ArialMT", "I\u2019m actually")) == "I\u2019m actually"
    assert P.flat_text("") == "" and P.flat_text("not base64 !!") == ""
