"""music.py: a channel's song on A2 of 1_edit.xml (night 15, task 5: SceneProof).

``--music FILE`` (with ``--music-start`` / ``--music-split`` / ``--music-gains``): after the edit is made and checked
(S9), the song is put on A2 of the finished 1_edit.xml -- from ``--music-start`` s into the song (SceneProof: 0.52 s),
cut at ``--music-split`` s of the sequence (12.32 s), each piece at its own level (+5 dB before the cut, -1 dB after),
to the end of the sequence (or of the song). A1 -- your voice and its audio chain -- is not touched, and the edit's own
checks never see A2 (they read every audio track as the RAW's sound).

The levels: your song.prproj sets them as Clip Gain (Gain 1.778 = +5 dB, 0.891 = -1 dB). No FCP7 XML form sets a clip's
Audio Gain in Premiere 2024 (tested on day 11, export_xml_edl's audio chain notes); Audio Levels becomes the clip's own
Volume, so each piece gets Volume +5 dB / -1 dB -- the same loudness. Premiere makes the song a mono clip of its first
channel, as it does with A1's stereo sound.

The song is linked (else copied) into extras/media next to the RAW, so the run folder holds everything it plays.

Day 16 (task 4) -- your song chain exactly: in your project each song clip has Clip Gain (+5 dB before the cut, -1 dB
after), Volume Level 8.5 dB, a Hard Limiter (Maximum Amplitude 0 dB, Input Boost 2.00 dB, Look-Ahead 7 ms, Release
100 ms, Link Channels 1.00) and Amplify +3.00 dB. An XML carries only the Volume (``--music-volume`` 8.5), never Clip
Gain, a Hard Limiter or Amplify. So each piece's Volume is its Clip Gain + 8.5 dB (+13.5 dB before the cut, +7.5 dB
after) and the preset "MovieRecaps song chain" (``song_preset``: the Hard Limiter with Input Boost 2.00 dB + Amplify
+3.00 dB, assets\\premiere) goes on both song clips by hand. Measured on the song (day 16, ffmpeg's limiter with the
same settings): the same loudness as your chain whether Premiere applies a clip's Volume before or after its effects --
the song's loudest peak is -10.1 dBFS, so the limiter only works where every gain is before it. (A song that peaks
higher would need the Clip Gain inside the Input Boost: ``song_preset(gain_db)`` makes such a preset.)
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence

from .common import ffprobe_bin, log

SONG_INPUT_BOOST_DB = 2.0          # day 16: your song's Hard Limiter Input Boost (song.prproj: 2.00 dB)
SONG_AMPLIFY_DB = 3.0               # ... and its Amplify (your prompt: +3.00 dB)
PRESET_TEMPLATE = Path(__file__).resolve().parents[3] / "assets" / "premiere" / "MovieRecaps audio chain.prfpset"
CLIP_ID = "clipitem-music"
FILE_ID = "file-music"
MASTER_ID = "masterclip-music"


def parse_gains(text: Any) -> list[float]:
    """'5,-1' -> [5.0, -1.0] (dB: before the cut, after it)."""
    if isinstance(text, (list, tuple)):
        return [float(v) for v in text]
    vals = [float(v) for v in str(text or "").replace(";", ",").split(",") if v.strip()]
    return vals or [0.0]


def probe(path: str | Path) -> dict:
    """{'duration' s, 'sample_rate', 'channels'} of the song (ffprobe); {} when it cannot be read."""
    try:
        r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=sample_rate,channels:format=duration", "-of", "json", str(path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        d = json.loads(r.stdout or "{}")
        s = (d.get("streams") or [{}])[0]
        return {"duration": float(d["format"]["duration"]), "sample_rate": int(s.get("sample_rate") or 48000),
                "channels": int(s.get("channels") or 2)}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return {}


def pieces(seq_frames: int, fps: Fraction, song_s: float, start_s: float, split_s: float,
           gains_db: Sequence[float]) -> list[dict]:
    """The song's clips on the sequence: [{'start', 'end', 'in', 'out' (sequence frames / song frames at ``fps``),
    'gain_db'}] -- cut at ``split_s``, to the end of the sequence or of the song."""
    f = float(fps)
    end = min(int(seq_frames), int(math.floor((song_s - start_s) * f)))
    if end <= 0:
        return []
    cuts = [0] + [c for c in [int(round(split_s * f))] if 0 < c < end] + [end]
    out = []
    for k, (a, b) in enumerate(zip(cuts, cuts[1:])):
        g = float(gains_db[min(k, len(gains_db) - 1)]) if gains_db else 0.0
        s0 = int(round(start_s * f)) + a
        out.append({"start": a, "end": b, "in": s0, "out": s0 + (b - a), "gain_db": g})
    return out


def _sub(parent: ET.Element, tag: str, text: Any = None, **attrs) -> ET.Element:
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def _rate(parent: ET.Element, fps: Fraction) -> None:
    r = _sub(parent, "rate")
    tb = int(round(float(fps)))
    _sub(r, "timebase", tb)
    _sub(r, "ntsc", "TRUE" if Fraction(fps).denominator == 1001 else "FALSE")


def _url(path: Path) -> str:
    from .export_xml_edl import _file_url          # the same pathurl as every other file of the XML (night 15, task 1)
    return _file_url(str(path.resolve()))


def _xml_rate(seq: ET.Element) -> Fraction:
    tb = int(seq.findtext("rate/timebase") or 60)
    ntsc = str(seq.findtext("rate/ntsc") or "FALSE").upper() == "TRUE"
    return Fraction(tb * 1000, 1001) if ntsc else Fraction(tb)


def place_media(song: Path, media_dir: Path | None) -> Path:
    """The song in the run's extras/media (a hard link, else a copy); the song itself when that fails."""
    if media_dir is None:
        return song
    dst = Path(media_dir) / song.name
    try:
        if dst.is_file() and dst.stat().st_size == song.stat().st_size:
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(song, dst)
        except OSError:
            shutil.copy2(song, dst)
        return dst
    except OSError as e:
        log.warning("music: %s could not be put into %s (%s) -- the XML plays it where it is", song.name, media_dir, e)
        return song


SONG_PRESET = "MovieRecaps song chain"


def preset_name(gain_db: float = 0.0) -> str:
    """'MovieRecaps song chain' (a preset with a Clip Gain in its Input Boost: 'MovieRecaps song chain +5')."""
    return SONG_PRESET if not gain_db else f"{SONG_PRESET} {gain_db:+g}"


def boost_value(db: float) -> str:
    """A Hard Limiter Input Boost in a preset file: -100..+50 dB as 0..1 (0 dB = 0.666667, your 2.00 dB = 0.68)."""
    return f"{(float(db) + 100.0) / 150.0:.12f}"


def amplify_value(db: float) -> str:
    """An Amplify gain in a preset file: -96..+48 dB as 0..1 (+3 dB = 0.6875, +4.5 dB = 0.697917)."""
    return f"{(float(db) + 96.0) / 144.0:.12f}"


def song_preset(gain_db: float = 0.0, template: str | Path = PRESET_TEMPLATE) -> str:
    """The text of the preset for a song piece with Clip Gain ``gain_db``: the A1 preset (Premiere's own export, its
    Hard Limiter and Amplify for 1 / 2 / 6 channels) with Input Boost SONG_INPUT_BOOST_DB + gain_db, Amplify
    SONG_AMPLIFY_DB, its own name and a new preset id."""
    import re
    import uuid
    t = Path(template).read_text(encoding="utf-8")
    name = preset_name(gain_db)
    boost = SONG_INPUT_BOOST_DB + float(gain_db)
    t = t.replace("<Name>MovieRecaps audio chain</Name>", f"<Name>{name}</Name>")
    t = re.sub(r"(<MZ\.EffectPresets\.PresetUID>)[^<]*(<)", lambda m: m.group(1) + str(uuid.uuid4()) + m.group(2), t)
    desc = (f"Hard Limiter Maximum Amplitude 0 dB, Input Boost {boost:.2f} dB, Look-Ahead 7 ms, Release 100 ms, Link "
            f"Channels 1.00 + Amplify +{SONG_AMPLIFY_DB:.2f} dB. MovieRecaps: put it on both song clips (A2) of a "
            "SceneProof 1_edit.xml.")
    t = re.sub(r"<Description>[^<]*</Description>", f"<Description>{desc}</Description>", t)
    # every Input Boost parameter (only the Hard Limiter has one) and every Amplify Gain
    t = re.sub(r"(<Name>Input Boost</Name>.*?<StartKeyframe>-?\d+,)([^,<]*)(,.*?<CurrentValue>)([^<]*)(<)",
               lambda m: m.group(1) + boost_value(boost) + m.group(3) + boost_value(boost) + m.group(5), t, flags=re.S)
    t = re.sub(r"(<Name>Gain</Name>.*?<StartKeyframe>-?\d+,)([^,<]*)(,.*?<CurrentValue>)([^<]*)(<)",
               lambda m: m.group(1) + amplify_value(SONG_AMPLIFY_DB) + m.group(3) + amplify_value(SONG_AMPLIFY_DB)
               + m.group(5), t, flags=re.S)
    return t


def add_music(xml: str | Path, song: str | Path, start_s: float = 0.0, split_s: float = 0.0,
              gains_db: Sequence[float] = (0.0,), media_dir: str | Path | None = None,
              volume_db: float | None = None) -> dict:
    """Put ``song`` on A2 of ``xml`` (module docstring). ``volume_db`` (day 16): your Volume Level on every piece, added
    to its gain (8.5: +13.5 / +7.5 dB); None: each piece's Volume is its gain (night 15). Returns {'clips', 'pieces',
    'file', 'seconds'} or {'error'}. Never raises."""
    try:
        return _add(Path(xml), Path(song), float(start_s), float(split_s), list(gains_db),
                    Path(media_dir) if media_dir else None, volume_db)
    except Exception as e:  # noqa: BLE001 - the edit stays as it was
        log.warning("music: not added (%s: %s)", type(e).__name__, e)
        return {"error": f"{type(e).__name__}: {e}"}


def _add(xml: Path, song: Path, start_s: float, split_s: float, gains: list[float], media_dir: Path | None,
         volume_db: float | None = None) -> dict:
    if not song.is_file():
        return {"error": f"the song is not there: {song}"}
    info = probe(song)
    if not info:
        return {"error": f"the song cannot be read: {song.name}"}
    root = ET.parse(str(xml)).getroot()
    seq = root.find("sequence")
    if seq is None:
        return {"error": "no sequence in the XML"}
    if any(ci.get("id", "").startswith(CLIP_ID) for ci in root.iter("clipitem")):
        return {"error": "the XML has its music already"}
    fps = _xml_rate(seq)
    n = int(seq.findtext("duration") or 0)
    ps = pieces(n, fps, info["duration"], start_s, split_s, gains)
    if not ps:
        return {"error": "the song is shorter than its start"}
    placed = place_media(song, media_dir)
    media = seq.find("media")
    audio = media.find("audio")
    if audio is None:
        audio = _sub(media, "audio")
        _sub(audio, "numOutputChannels", 2)
        sc = _sub(_sub(audio, "format"), "samplecharacteristics")
        _sub(sc, "depth", 16)
        _sub(sc, "samplerate", info["sample_rate"])
    tracks = audio.findall("track")
    if not tracks:                                   # no A1 (a RAW without sound): an empty A1, the song on A2
        _sub(audio, "track")
    tr = _sub(audio, "track")
    song_frames = int(math.floor(info["duration"] * float(fps)))
    for k, p in enumerate(ps, start=1):
        ci = _sub(tr, "clipitem", id=f"{CLIP_ID}-{k}")
        _sub(ci, "masterclipid", MASTER_ID)
        _sub(ci, "name", song.name)
        _sub(ci, "enabled", "TRUE")
        _sub(ci, "duration", song_frames)
        _rate(ci, fps)
        for t in ("start", "end", "in", "out"):
            _sub(ci, t, p[t])
        if k == 1:
            fe = _sub(ci, "file", id=FILE_ID)
            _sub(fe, "name", placed.name)
            _sub(fe, "pathurl", _url(placed))
            _rate(fe, fps)
            _sub(fe, "duration", song_frames)
            au = _sub(_sub(fe, "media"), "audio")
            sc = _sub(au, "samplecharacteristics")
            _sub(sc, "depth", 16)
            _sub(sc, "samplerate", info["sample_rate"])
            _sub(au, "channelcount", info["channels"])
        else:
            _sub(ci, "file", id=FILE_ID)
        e = _sub(_sub(ci, "filter"), "effect")
        _sub(e, "name", "Audio Levels")
        _sub(e, "effectid", "audiolevels")
        _sub(e, "effectcategory", "audiolevels")
        _sub(e, "effecttype", "audiolevels")
        _sub(e, "mediatype", "audio")
        pa = _sub(e, "parameter", authoringApp="PremierePro")
        _sub(pa, "parameterid", "level")
        _sub(pa, "name", "Level")
        _sub(pa, "valuemin", 0)
        _sub(pa, "valuemax", "3.98109")
        vol = p["gain_db"] if volume_db is None else p["gain_db"] + float(volume_db)
        _sub(pa, "value", f"{10.0 ** (vol / 20.0):.6f}")
        st = _sub(ci, "sourcetrack")
        _sub(st, "mediatype", "audio")
        _sub(st, "trackindex", 1)
        cm = _sub(ci, "comments")
        _sub(cm, "mastercomment1", "MUSIC")
        if volume_db is None:
            _sub(cm, "mastercomment2", f"{song.name} from {p['in'] / float(fps):.2f} s, Volume {p['gain_db']:+g} dB "
                                       "(your Clip Gain; an XML cannot set Clip Gain)")
        else:
            _sub(cm, "mastercomment2", f"{song.name} from {p['in'] / float(fps):.2f} s, Volume {vol:+g} dB (your Clip "
                                       f"Gain {p['gain_db']:+g} + Volume {float(volume_db):g} dB) -- drop the preset "
                                       f"\"{SONG_PRESET}\" on it (Hard Limiter + Amplify)")
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    tmp = xml.with_suffix(".music.tmp")
    tmp.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n", encoding="utf-8")
    tmp.replace(xml)
    out = {"clips": len(ps), "file": str(placed), "seconds": round(ps[-1]["end"] / float(fps), 3),
           "pieces": [{"from_s": round(p["start"] / float(fps), 3), "to_s": round(p["end"] / float(fps), 3),
                       "song_s": round(p["in"] / float(fps), 3), "gain_db": p["gain_db"],
                       **({"volume_db": p["gain_db"] + float(volume_db), "preset": SONG_PRESET}
                          if volume_db is not None else {})} for p in ps]}
    log.info("music: %s on A2 -- %s", song.name, "; ".join(f"{q['from_s']:.2f}-{q['to_s']:.2f} s at "
                                                          f"{q['gain_db']:+g} dB" for q in out["pieces"]))
    return out


def apply(ctx: Any) -> dict | None:
    """The pipeline's step (after S9): the song on A2 of the run's 1_edit.xml when --music is set."""
    cfg = ctx.cfg
    song = str(getattr(cfg, "music_file", "") or "")
    if not song:
        return None
    from . import run_folders
    xml = Path(cfg.deliver) / run_folders.EDIT_XML
    if not xml.is_file():
        return {"error": "no 1_edit.xml"}
    res = add_music(xml, song, float(getattr(cfg, "music_start_s", 0.0) or 0.0),
                    float(getattr(cfg, "music_split_s", 0.0) or 0.0),
                    parse_gains(getattr(cfg, "music_gains_db", "0")), getattr(cfg, "media_dir", None),
                    getattr(cfg, "music_volume_db", None))
    if res.get("error"):
        ctx.warn(f"the music was not added to 1_edit.xml: {res['error']}")
    try:
        ctx.dlog.record("premiere", "music", **res)
    except Exception:  # noqa: BLE001
        pass
    return res


__all__ = ["parse_gains", "pieces", "add_music", "apply"]
