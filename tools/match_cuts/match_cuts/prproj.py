"""prproj.py: read a Premiere Pro project (``.prproj``) -- the sequences of a finished edit, their clips and captions.

A ``.prproj`` is gzip-compressed XML: a flat list of objects that point at each other (``ObjectRef`` -> an
``ObjectID``, ``ObjectURef`` -> an ``ObjectUID``). A sequence holds track groups (video, audio, captions), a track
holds track items, a track item holds its place on the sequence (``Start`` / ``End``) and a sub-clip, the sub-clip a
clip (``InPoint`` / ``OutPoint`` in the media) and the clip a media source (the file). Times are Premiere ticks:
254016000000 a second. Text is a flatbuffer (base64): a text graphic's ``Source Text`` parameter, a caption's
``FormattedTextData``; the text is the buffer's last string.

Only what the finished edit needs is read: each clip's place, its media and range, its speed, and on video its
Motion (position, scale) or its text; each caption's text and place. Unknown objects are skipped, never guessed.
"""
from __future__ import annotations

import base64
import gzip
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TICKS = 254016000000                   # Premiere ticks a second
VIDEO, AUDIO, CAPTION = "video", "audio", "caption"


@dataclass
class Item:
    """One clip on a track (seconds). ``src_in`` / ``src_out``: the range of the media it plays (the media's own
    seconds, ``speed`` ignored); ``media``: the file path ('' for a graphic or a caption)."""
    kind: str
    track: int                          # 1-based (V1, A1, ...)
    start: float
    end: float
    name: str = ""
    media: str = ""
    src_in: float = 0.0
    src_out: float = 0.0
    speed: float = 1.0
    text: str = ""                      # a text graphic's text / a caption's text
    enabled: bool = True
    position: tuple[float, float] | None = None   # Motion position, a fraction of the frame (0.5, 0.5 = centred)
    scale: float | None = None          # Motion scale, % (100 = the media's own size)
    keyframed: list[str] = field(default_factory=list)   # Motion parameters with keyframes (not read)

    @property
    def is_graphic(self) -> bool:
        """A text graphic (its media is Premiere's own, not a file)."""
        return self.kind == VIDEO and bool(self.text)


@dataclass
class Sequence:
    name: str
    fps: float
    width: int
    height: int
    items: list[Item] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max((it.end for it in self.items), default=0.0)

    def track(self, kind: str, n: int) -> list[Item]:
        return sorted((it for it in self.items if it.kind == kind and it.track == n), key=lambda it: it.start)

    def tracks(self, kind: str) -> list[int]:
        return sorted({it.track for it in self.items if it.kind == kind})


@dataclass
class Project:
    path: str
    sequences: list[Sequence]


def _load(path: str | Path) -> ET.Element:
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return ET.fromstring(data)


def flat_text(b64: str | None) -> str:
    """The text of a Premiere flatbuffer (a text graphic's Source Text, a caption block): its last string
    (4-byte little-endian length, UTF-8, NUL)."""
    if not b64:
        return ""
    try:
        buf = base64.b64decode("".join(str(b64).split()))
    except (ValueError, TypeError):
        return ""
    best = ""
    i = 0
    while i + 5 <= len(buf):
        n = struct.unpack_from("<I", buf, i)[0]
        if 0 < n < 4096 and i + 4 + n < len(buf) and buf[i + 4 + n] == 0:
            raw = buf[i + 4: i + 4 + n]
            try:
                s = raw.decode("utf-8")
            except UnicodeDecodeError:
                s = ""
            if s and all(ch.isprintable() or ch in "\r\n\t" for ch in s):
                best = s
                i += 4 + n
                continue
        i += 1
    return best.replace("\r", "\n").strip()


def _value(par: ET.Element | None) -> str:
    """A component parameter's static value: the text after the time in ``StartKeyframe`` ("<ticks>,<value>,...")."""
    if par is None:
        return ""
    sk = par.findtext("StartKeyframe") or ""
    parts = sk.split(",")
    return parts[1] if len(parts) > 1 else (par.findtext("CurrentValue") or "")


def read(path: str | Path) -> Project:
    root = _load(path)
    ids: dict[str, ET.Element] = {}
    uids: dict[str, ET.Element] = {}
    for el in root:
        if "ObjectID" in el.attrib:
            ids[el.attrib["ObjectID"]] = el
        if "ObjectUID" in el.attrib:
            uids[el.attrib["ObjectUID"]] = el

    def ref(el: ET.Element | None) -> ET.Element | None:
        if el is None:
            return None
        if "ObjectRef" in el.attrib:
            return ids.get(el.attrib["ObjectRef"])
        if "ObjectURef" in el.attrib:
            return uids.get(el.attrib["ObjectURef"])
        return None

    seqs = []
    for seq in (e for e in root if e.tag == "Sequence"):
        s = Sequence(seq.findtext("Name") or "", 0.0, 0, 0)
        for tg in seq.findall("TrackGroups/TrackGroup"):
            g = ref(tg.find("Second"))
            if g is None:
                continue
            kind = {"VideoTrackGroup": VIDEO, "AudioTrackGroup": AUDIO, "DataTrackGroup": CAPTION}.get(g.tag)
            if kind is None:
                continue
            fr = g.findtext("TrackGroup/FrameRate")
            if kind == VIDEO and fr and int(fr) > 0:
                s.fps = TICKS / int(fr)
                rect = (g.findtext("FrameRect") or "").split(",")
                if len(rect) == 4:
                    s.width, s.height = int(rect[2]), int(rect[3])
            for n, tr in enumerate(g.findall("TrackGroup/Tracks/Track"), start=1):
                t = ref(tr)
                if t is None:
                    continue
                for ti in t.iter("TrackItem"):
                    if "ObjectRef" not in ti.attrib:
                        continue
                    it = ref(ti)
                    if it is not None:
                        s.items.extend(_items(it, kind, n, ref))
        seqs.append(s)
    return Project(str(path), seqs)


def _items(it: ET.Element, kind: str, track: int, ref) -> list[Item]:
    """The Item(s) of one track item element (a clip, a text graphic, or a caption track item)."""
    if it.tag.endswith("TransitionTrackItem"):
        return []
    base = it.find("ClipTrackItem/TrackItem")
    if base is None:
        base = it.find(".//TrackItem")
    if base is None or base.findtext("Start") is None:
        return []
    start, end = int(base.findtext("Start") or 0) / TICKS, int(base.findtext("End") or 0) / TICKS
    if kind == CAPTION:
        return [Item(CAPTION, track, start, end, text=_caption_text(it, ref))]
    sub = ref(it.find("ClipTrackItem/SubClip"))
    out = Item(kind, track, start, end)
    if sub is not None:
        out.name = sub.findtext("Name") or ""
        clip = ref(sub.find("Clip"))
        c = clip.find("Clip") if clip is not None else None
        if c is not None:
            ip, op = c.findtext("InPoint"), c.findtext("OutPoint")
            out.src_in = int(ip) / TICKS if ip else 0.0
            out.src_out = int(op) / TICKS if op else 0.0
            sp = c.findtext("PlaybackSpeed")
            if sp:
                try:
                    out.speed = float(sp)
                except ValueError:
                    pass
            src = ref(c.find("Source"))
            media = ref(src.find("MediaSource/Media")) if src is not None else None
            if media is not None:
                out.media = media.findtext("ActualMediaFilePath") or media.findtext("FilePath") or ""
    dis = it.findtext("ClipTrackItem/Disabled") or it.findtext(".//Disabled")
    out.enabled = str(dis).lower() != "true"
    chain = ref(it.find("ClipTrackItem/ComponentOwner/Components"))
    if chain is not None and kind == VIDEO:
        for cref in chain.findall("ComponentChain/Components/Component"):
            comp = ref(cref)
            if comp is None:
                continue
            match = comp.findtext("MatchName") or ""
            params = {}
            for p in comp.findall("Component/Params/Param"):
                par = ref(p)
                if par is not None:
                    params.setdefault(par.findtext("Name") or "", par)
            if match == "AE.ADBE Motion":
                pos = _value(params.get("Position"))
                if ":" in pos:
                    x, y = pos.split(":")[:2]
                    out.position = (float(x), float(y))
                sc = _value(params.get("Scale"))
                if sc:
                    out.scale = float(sc.rstrip("."))
                out.keyframed = [k for k in ("Position", "Scale") if params.get(k) is not None
                                 and (params[k].findtext("Keyframes") or "").strip()]
            elif match == "AE.ADBE Text":
                st = params.get("Source Text")
                if st is not None:
                    out.text = flat_text(st.findtext("StartKeyframeValue"))
    return [out]


def _caption_text(it: ET.Element, ref) -> str:
    """A caption track item's text: its blocks (FormattedTextData), lines joined."""
    texts = []
    for b in it.findall("BlockVector/BlockVectorItem"):
        blk = ref(b)
        if blk is not None:
            t = flat_text(blk.findtext("FormattedTextData"))
            if t:
                texts.append(t)
    return "\n".join(texts)


def main_sequence(project: Project) -> Sequence | None:
    """The sequence that holds the edit: the one with the most clips of media (ties: the longest)."""
    def weight(s: Sequence) -> tuple:
        return (sum(1 for it in s.items if it.media), s.duration)
    return max(project.sequences, key=weight, default=None)


def captions_of(seq: Sequence) -> list[Item]:
    """The finished captions: the caption track with the most captions, else the video track with the most text
    graphics (captions styled as graphics)."""
    best: list[Item] = []
    for kind in (CAPTION, VIDEO):
        for n in seq.tracks(kind):
            items = [it for it in seq.track(kind, n) if it.text and (kind == CAPTION or it.is_graphic)]
            if len(items) > len(best):
                best = items
        if best:
            return best
    return best


def audio_pieces(seq: Sequence, media_name: str | None = None) -> list[dict[str, Any]]:
    """The edit's audio as [{start, end, src_in, speed, media}] (seconds): the A track playing the most of the given
    media (by file name; default: the media cut into the most clips -- a reference track plays in one)."""
    clips = [it for it in seq.items if it.kind == AUDIO and it.media and it.enabled]
    if not clips:
        return []
    if media_name is None:
        tally: dict[str, list] = {}
        for it in clips:
            t = tally.setdefault(file_name(it.media), [0, 0.0])
            t[0] += 1
            t[1] += it.end - it.start
        media_name = max(tally, key=lambda k: tuple(tally[k]))
    mine = [it for it in clips if file_name(it.media) == media_name]
    per_track: dict[int, float] = {}
    for it in mine:
        per_track[it.track] = per_track.get(it.track, 0.0) + it.end - it.start
    n = max(per_track, key=per_track.get)
    return [{"start": round(it.start, 6), "end": round(it.end, 6), "src_in": round(it.src_in, 6),
             "speed": it.speed, "media": file_name(it.media)}
            for it in sorted((it for it in mine if it.track == n), key=lambda it: it.start)]


def file_name(path: str) -> str:
    """The file name of a media path written on Windows or elsewhere."""
    return Path(str(path).replace("\\", "/")).name

