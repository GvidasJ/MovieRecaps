"""THE SONG'S REAL CLIP GAIN IN THE SAVED PROJECT (day 17, task 3: SceneProof).

An XML can't carry Clip Gain, so 1_edit.xml gives each song piece (A2) the Volume "its Clip Gain + 8.5 dB" (+13.5 /
+7.5 dB: the same loudness). Your own project has Audio Gain -> Set Gain to +5 dB / -1 dB on the two song clips and
the song's Volume Level 8.5 dB on both. The saved .prproj CAN carry that: ``<Gain>`` on the song's AudioClip (1.778 for
+5 dB, 0.891 for -1 dB, as in docs\\sceneproof\\song.prproj) and the Volume's Level parameter.

``fix(prproj, xml)``: after Premiere imported the edit and the project was saved, each song clip of the saved project
gets its real Clip Gain and Volume 8.5 dB (read from the edit's own clip comments: "your Clip Gain +5 + Volume 8.5 dB").
Only those values change -- the file is otherwise byte for byte as Premiere saved it (a copy of the saved project is
kept next to it as ``<name>.before_song_gain.prproj``). ``needs_fix`` says whether it still has to be done.

Premiere's Volume Level is stored as a gain where 1.0 is +15 dB: value = 10 ** ((dB - 15) / 20) (the XML's +13.5 dB
was saved as 0.8414).
"""
from __future__ import annotations

import gzip
import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

MUSIC_ID = "clipitem-music"
_COMMENT = re.compile(r"Clip Gain ([+-]?\d+(?:\.\d+)?) \+ Volume ([+-]?\d+(?:\.\d+)?) dB")
BACKUP = ".before_song_gain.prproj"
TOL = 1e-4


def level_value(db: float) -> float:
    """Premiere's stored Volume Level for ``db`` (1.0 = +15 dB)."""
    return 10.0 ** ((float(db) - 15.0) / 20.0)


def gain_value(db: float) -> float:
    """The AudioClip's <Gain> for a Clip Gain of ``db`` (+5 dB -> 1.7782794237136841, as Premiere writes it: the
    nearest 32-bit float)."""
    return float(gain_text(db))


# your song.prproj's own values (Premiere's Set Gain to: +5 dB / -1 dB) -- written as they are
KNOWN_GAINS = {5.0: "1.7782794237136841", -1.0: "0.89125096797943115"}


def gain_text(db: float) -> str:
    """The <Gain> text: your project's own for +5 / -1 dB, else the nearest 32-bit float."""
    import struct
    if float(db) in KNOWN_GAINS:
        return KNOWN_GAINS[float(db)]
    return f"{struct.unpack('f', struct.pack('f', 10.0 ** (float(db) / 20.0)))[0]:.17g}"


def pieces(xml: Path) -> tuple[str, list[tuple[int, float, float]]]:
    """(the song's file name, [(start frame on the sequence, Clip Gain dB, Volume dB)] in order) from the edit's music
    clips; ('', []) when the edit has no song with its Clip Gain in the comments."""
    try:
        root = ET.parse(str(xml)).getroot()
    except (OSError, ET.ParseError):
        return "", []
    name, out = "", []
    for ci in root.iter("clipitem"):
        if not (ci.get("id") or "").startswith(MUSIC_ID):
            continue
        m = _COMMENT.search(ci.findtext("comments/mastercomment2") or "")
        if not m:
            continue
        name = name or (ci.findtext("name") or "")
        out.append((int(ci.findtext("start") or 0), float(m.group(1)), float(m.group(2))))
    return name, sorted(out)


def _read(prproj: Path) -> str:
    return gzip.decompress(Path(prproj).read_bytes()).decode("utf-8")


def _spans(text: str) -> dict[str, tuple[int, int, str]]:
    """ObjectID / ObjectUID -> (start, end, tag) of each top-level object (one tab in, as Premiere writes them)."""
    out = {}
    for m in re.finditer(r'^\t<(\w+) Object(?:ID|UID)="([^"]+)"[^>]*>', text, re.M):
        tag = m.group(1)
        end = text.find(f"\n\t</{tag}>", m.end())
        if end < 0:
            continue
        out[m.group(2)] = (m.start(), end + len(f"\n\t</{tag}>"), tag)
    return out


def _ref(body: str, tag: str) -> str | None:
    m = re.search(rf"<{tag} Object(?:U)?Ref=\"([^\"]+)\"", body)
    return m.group(1) if m else None


def song_clips(text: str, song_name: str) -> list[dict]:
    """The song's clips on the timeline, in order: [{start, audioclip, level_param}] (ids of the objects to change)."""
    sp = _spans(text)
    body = {k: text[s:e] for k, (s, e, _t) in sp.items()}
    media = {k for k, b in body.items() if sp[k][2] == "Media" and
             Path((re.search(r"<ActualMediaFilePath>([^<]*)<", b) or [None, ""])[1].replace("\\", "/")).name == song_name}
    if not media:
        return []

    def reaches_media(k: str, depth: int = 0) -> bool:
        if k in media:
            return True
        if depth > 4 or k not in body:
            return False
        return any(reaches_media(r, depth + 1) for r in re.findall(r'Object(?:U)?Ref="([^"]+)"', body[k]))

    clips = {k for k, b in body.items() if sp[k][2] == "AudioClip" and reaches_media(_ref(b, "Source") or "")}
    out = []
    for k, b in body.items():
        if sp[k][2] != "AudioClipTrackItem":
            continue
        sub = _ref(b, "SubClip")
        ac = _ref(body.get(sub, ""), "Clip")
        if ac not in clips:
            continue
        start = int((re.search(r"<Start>(-?\d+)</Start>", b) or [None, "0"])[1])
        chain = _ref(b, "Components")
        level = None
        for comp in re.findall(r'<Component Index="\d+" ObjectRef="([^"]+)"', body.get(chain, "")):
            cb = body.get(comp, "")
            if "<DisplayName>Volume</DisplayName>" not in cb:
                continue
            for par in re.findall(r'<Param Index="\d+" ObjectRef="([^"]+)"', cb):
                if "<Name>Level</Name>" in body.get(par, ""):
                    level = par
            break
        out.append({"start": start, "audioclip": ac, "level": level, "chain": chain})
    return sorted(out, key=lambda c: c["start"])


def _set_gain(obj: str, value: str) -> str:
    v = f"<Gain>{value}</Gain>"
    if "<Gain>" in obj:
        return re.sub(r"<Gain>[^<]*</Gain>", v, obj, count=1)
    return obj.replace("</SecondaryContents>", "</SecondaryContents>\n\t\t" + v, 1) if "</SecondaryContents>" in obj \
        else obj.replace("\n\t\t<AudioChannelLayout>", "\n\t\t" + v + "\n\t\t<AudioChannelLayout>", 1)


def _set_level(obj: str, value: float) -> str:
    obj = re.sub(r"(<StartKeyframe>-?\d+,)[^,<]*(,)", lambda m: f"{m.group(1)}{value:.12f}{m.group(2)}", obj, count=1)
    return re.sub(r"<CurrentValue>[^<]*</CurrentValue>", f"<CurrentValue>{value:.17g}</CurrentValue>", obj, count=1)


def _current(text: str, xml: Path) -> tuple[list[dict], list[tuple[int, float, float]]]:
    name, ps = pieces(xml)
    if not ps:
        return [], []
    return song_clips(text, name), ps


def needs_fix(prproj: Path, xml: Path) -> bool:
    """The saved project still lacks the edit's song Clip Gain / Volume 8.5 dB, or A1's chain (core.a1_chain)."""
    try:
        text = _read(prproj)
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeDecodeError):
        return False
    from . import a1_chain
    return _song_needs_fix(text, xml) or a1_chain.a1_needs_fix(text, xml)


def holds_edit_sound(prproj: Path, xml: Path) -> bool:
    """Run 19: the saved project holds the edit's A1 clips (or, without A1 work, its song clips) as the edit made them
    -- only then does "nothing to fix" mean the sound is set."""
    try:
        text = _read(prproj)
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeDecodeError):
        return False
    from . import a1_chain
    clips, ps, fps = a1_chain._a1(text, xml)                     # noqa: SLF001
    if ps:
        return a1_chain._match(clips, ps, fps) is not None       # noqa: SLF001
    sc, sp = _current(text, xml)
    return bool(sp) and len(sc) == len(sp)


def has_work(xml: Path) -> bool:
    """The edit has something the saved project must get: a song with its Clip Gain, or A1 clips with their chain."""
    from . import a1_chain
    return bool(pieces(xml)[1] or a1_chain.a1_pieces(xml)[2])


def _song_needs_fix(text: str, xml: Path, effects: bool = True) -> bool:
    """The song clips lack their Clip Gain / Volume -- or (``effects``) your CorrectVolume's Hard Limiter + Amplify
    (run 18, task 2)."""
    from . import a1_chain
    clips, ps = _current(text, xml)
    if not clips or len(clips) != len(ps):
        return False
    sp = _spans(text)
    for c, (_s, g, v) in zip(clips, ps):
        if effects and c.get("chain") and not a1_chain.has_effects_in_order(text, c["chain"], sp,
                                                                                     one_volume=False):
            return True
        a = text[sp[c["audioclip"]][0]:sp[c["audioclip"]][1]]
        m = re.search(r"<Gain>([^<]*)</Gain>", a)
        if not m or abs(float(m.group(1)) - gain_value(g)) > TOL:
            return True
        if c["level"]:
            lv = re.search(r"<CurrentValue>([^<]*)</CurrentValue>", text[sp[c["level"]][0]:sp[c["level"]][1]])
            if lv and abs(float(lv.group(1)) - level_value(v)) > TOL:
                return True
    return False


def fix(prproj: Path, xml: Path) -> str:
    """Write each song clip's real Clip Gain and Volume (module docstring) and A1's chain (core.a1_chain); what was
    done, in plain words ('' when there is nothing to do). Never raises."""
    from . import a1_chain
    prproj = Path(prproj)
    try:
        text = _read(prproj)
        ed = a1_chain.Edit(text)
        said = []
        clips, ps = _current(text, xml)
        if ps and len(clips) != len(ps):
            said.append(f"the song's Clip Gain was not written: the project has {len(clips)} song clips, "
                        f"the edit {len(ps)}")
        elif ps:
            if _song_needs_fix(text, xml, effects=False):
                for c, (_s, g, v) in zip(clips, ps):
                    ed.set(c["audioclip"], _set_gain(ed.body(c["audioclip"]), gain_text(g)))
                    if c["level"]:
                        ed.set(c["level"], _set_level(ed.body(c["level"]), level_value(v)))
                said.append("the song's Clip Gain " + " / ".join(f"{g:+g} dB" for _s, g, _v in ps) +
                            f" and Volume {ps[0][2]:g} dB")
            if sum(a1_chain.add_effects(ed, c["chain"]) for c in clips if c.get("chain")):
                said.append("the song's Hard Limiter (Input Boost 2 dB) + Amplify +3 dB (your CorrectVolume)")
        a1 = a1_chain.a1_fix(ed, xml)
        if a1:
            said.append(a1)
        new = ed.apply()
        if new == text:
            return "; ".join(s for s in said if "not written" in s)
        backup = prproj.with_name(prproj.stem + BACKUP)
        if not backup.exists():
            shutil.copy2(prproj, backup)
        tmp = prproj.with_name(prproj.name + ".tmp")
        tmp.write_bytes(gzip.compress(new.encode("utf-8")))
        os.replace(tmp, prproj)
        return "; ".join(said) + f" written into {prproj.name}"
    except Exception as e:  # noqa: BLE001 - the project stays as Premiere saved it
        return f"the audio chain was not written ({type(e).__name__}: {e})"
