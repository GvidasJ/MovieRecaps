"""YOUR AUDIO CHAIN IN THE SAVED PROJECT (run 18, tasks 1 and 2: every channel).

An XML can't carry Clip Gain, a Hard Limiter or Amplify. So 1_edit.xml gives each A1 voice clip its "Normalize All
Peaks to 0 dB" as Gain filters, which Premiere imports as one or more EXTRA Volume effects (two of +7.6 dB on one
clip, stacked). The saved .prproj CAN carry your chain, so after Premiere imported the edit and the project was saved,
``fix(prproj, xml)`` turns each A1 clip into exactly:

1. **Clip Gain** = its own normalisation (the clip's loudest peak to 0 dB: the gain in its XML comment, "audio chain:
   Audio Gain +12.40 dB ...", measured by the tool on the sound that clip plays) -- ``<Gain>`` on its AudioClip
2. **Volume**: only the clip's own built-in Volume, at 0 dB; every extra Volume effect is removed (with its values)
3. **Hard Limiter** (Maximum Amplitude 0 dB, Input Boost 2.00 dB, Look-Ahead 7 ms, Release 100 ms, Link Channels 1.00)
   and below it **Amplify +3.00 dB** -- copied object for object from your CorrectVolume preset
   (``assets\\premiere\\CorrectVolume.prfpset``, the same bytes as ``docs\\sceneproof\\CorrectVolume.prfpset``), in the
   form for the clip's channels (mono: Premiere makes A1 a mono clip)

The song's clips (A2, ``core.song_gain``) get the same two effects with ``add_effects``. Only those objects change:
the rest of the file stays byte for byte as Premiere saved it (song_gain keeps a copy of the saved project as
``<name>.before_song_gain.prproj``). ``needs_fix`` says whether anything is still to do.

Premiere's stored values (checked on your projects): the Volume's Level 1.0 = +15 dB, so 0 dB is 0.177828; the Hard
Limiter's Input Boost 0..1 over -100..+50 dB (2 dB = 0.68); Amplify's Gain 0..1 over -96..+48 dB (+3 dB = 0.6875).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

PRESET = Path(__file__).resolve().parents[4] / "assets" / "premiere" / "CorrectVolume.prfpset"
HARD_LIMITER_MATCH = "e0b23f05-f1a7-4ef7-9b50-7ec3e3002058"
AMPLIFY_MATCH = "f14fee7d-fe5f-4202-b790-f53209e56411"
EFFECTS = (HARD_LIMITER_MATCH, AMPLIFY_MATCH)         # in this order, after the Volume
TICKS_PER_SECOND = 254016000000
A1_ID = re.compile(r"clipitem-a\d+$")
_A1_COMMENT = re.compile(r"audio chain: Audio Gain ([+-]?\d+(?:\.\d+)?) dB")
VOLUME_0DB = "0.17782799899578094"                    # the Volume's Level at 0 dB, as Premiere writes it
TOL = 1e-4


# ---------------------------------------------------------------------------------------------------------------------
# the edit (1_edit.xml)
# ---------------------------------------------------------------------------------------------------------------------

def _fps(root: ET.Element) -> Fraction:
    seq = root.find(".//sequence")
    rate = seq.find("rate") if seq is not None else None
    tb = int((rate.findtext("timebase") if rate is not None else None) or 30)
    ntsc = ((rate.findtext("ntsc") if rate is not None else "") or "").strip().upper() == "TRUE"
    return Fraction(tb * 1000, 1001) if ntsc else Fraction(tb)


def a1_pieces(xml: Path) -> tuple[str, Fraction, list[tuple[int, float]]]:
    """(A1's file name, the sequence's frame rate, [(start frame, Clip Gain dB)] in order) from the edit's A1 clips
    with an audio chain comment; ('', 0, []) when there are none."""
    try:
        root = ET.parse(str(xml)).getroot()
    except (OSError, ET.ParseError):
        return "", Fraction(0), []
    names = {f.get("id"): f.findtext("name") for f in root.iter("file") if f.findtext("name")}
    name, out = "", []
    for ci in root.iter("clipitem"):
        if not A1_ID.match(ci.get("id") or ""):
            continue
        m = _A1_COMMENT.search(ci.findtext("comments/mastercomment2") or "")
        if not m:
            continue
        f = ci.find("file")
        name = name or (f is not None and (f.findtext("name") or names.get(f.get("id")))) or ci.findtext("name") or ""
        out.append((int(ci.findtext("start") or 0), float(m.group(1))))
    return name, _fps(root), sorted(out)


# ---------------------------------------------------------------------------------------------------------------------
# the saved project's objects
# ---------------------------------------------------------------------------------------------------------------------

def spans(text: str) -> dict[str, tuple[int, int, str]]:
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


def clips_of(text: str, media_name: str) -> list[dict]:
    """The timeline's audio clips playing ``media_name``, in order: [{item, start, audioclip, chain}] (object ids)."""
    sp = spans(text)
    body = {k: text[s:e] for k, (s, e, _t) in sp.items()}
    media = {k for k, b in body.items() if sp[k][2] == "Media" and Path(
        (re.search(r"<ActualMediaFilePath>([^<]*)<", b) or [None, ""])[1].replace("\\", "/")).name == media_name}
    if not media:
        return []

    def reaches_media(k: str, depth: int = 0) -> bool:
        if k in media:
            return True
        if depth > 4 or k not in body:
            return False
        return any(reaches_media(r, depth + 1) for r in re.findall(r'Object(?:U)?Ref="([^"]+)"', body[k]))

    clips = {k for k, b in body.items() if sp[k][2] == "AudioClip" and reaches_media(_ref(b, "Source") or "")}
    track_of = {it: k for k, b in body.items() if sp[k][2] == "AudioClipTrack"
                for it in re.findall(r'<TrackItem Index="\d+" ObjectRef="([^"]+)"', b)}
    out = []
    for k, b in body.items():
        if sp[k][2] != "AudioClipTrackItem":
            continue
        ac = _ref(body.get(_ref(b, "SubClip") or "", ""), "Clip")
        if ac not in clips:
            continue
        start = int((re.search(r"<Start>(-?\d+)</Start>", b) or [None, "0"])[1])
        out.append({"item": k, "start": start, "audioclip": ac, "chain": _ref(b, "Components"),
                    "track": track_of.get(k, "")})
    return sorted(out, key=lambda c: c["start"])


def chain_components(text: str, chain: str, sp: dict | None = None) -> list[dict]:
    """The chain's components in order: [{id, name, match, intrinsic, params: {name: param id}}]."""
    sp = sp if sp is not None else spans(text)
    if chain not in sp:
        return []
    cb = text[sp[chain][0]:sp[chain][1]]
    out = []
    for comp in re.findall(r'<Component Index="\d+" ObjectRef="([^"]+)"', cb):
        if comp not in sp:
            continue
        b = text[sp[comp][0]:sp[comp][1]]
        params = {}
        for par in re.findall(r'<Param Index="\d+" ObjectRef="([^"]+)"', b):
            if par in sp:
                n = re.search(r"<Name>([^<]*)</Name>", text[sp[par][0]:sp[par][1]])
                params[n.group(1) if n else par] = par
        out.append({"id": comp, "name": (re.search(r"<DisplayName>([^<]*)<", b) or [None, ""])[1],
                    "match": (re.search(r"<FilterMatchName>([^<]*)<", b) or [None, ""])[1],
                    "intrinsic": "<Intrinsic>true</Intrinsic>" in b, "params": params,
                    "channel_type": int((re.search(r"<ChannelType>(\d+)<", b) or [None, "0"])[1])})
    return out


def param_value(text: str, sp: dict, par: str) -> float | None:
    m = re.search(r"<CurrentValue>([^<]*)</CurrentValue>", text[sp[par][0]:sp[par][1]]) if par in sp else None
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------------------------------
# your preset's effects, as objects to add
# ---------------------------------------------------------------------------------------------------------------------

_TEMPLATES: dict[tuple[str, int], list[tuple[str, str]]] = {}


def template(match: str, channel_type: int, preset: Path = PRESET) -> list[tuple[str, str]]:
    """[(old id, object text)] of one effect of your preset for ``channel_type`` (0 mono, 1 stereo, 2 5.1): its
    AudioFilterComponent first, then its parameters."""
    key = (match, int(channel_type))
    if key in _TEMPLATES:
        return _TEMPLATES[key]
    text = Path(preset).read_text(encoding="utf-8")
    sp = spans(text)
    for k, (s, e, tag) in sp.items():
        b = text[s:e]
        if tag != "AudioFilterComponent" or f"<FilterMatchName>{match}</FilterMatchName>" not in b:
            continue
        if int((re.search(r"<ChannelType>(\d+)<", b) or [None, "-1"])[1]) != int(channel_type):
            continue
        objs = [(k, b)] + [(p, text[sp[p][0]:sp[p][1]]) for p in re.findall(r'<Param Index="\d+" ObjectRef="([^"]+)"', b)
                           if p in sp]
        _TEMPLATES[key] = objs
        return objs
    raise LookupError(f"{Path(preset).name} has no effect {match} for channel type {channel_type}")


def _renumber(objs: list[tuple[str, str]], first: int, comp_id: int) -> tuple[str, list[str]]:
    """The objects with fresh ObjectIDs from ``first`` on (their references too) and the component's chain ID
    ``comp_id``; (the component's new id, [texts])."""
    new = {old: str(first + i) for i, (old, _t) in enumerate(objs)}
    out = []
    for old, t in objs:
        t = re.sub(r'(Object(?:ID|Ref)=")([^"]+)(")', lambda m: m.group(1) + new.get(m.group(2), m.group(2)) + m.group(3), t)
        out.append(t)
    out[0] = re.sub(r"<ID>\d+</ID>", f"<ID>{comp_id}</ID>", out[0], count=1)
    return new[objs[0][0]], out


def max_object_id(text: str) -> int:
    return max((int(v) for v in re.findall(r'ObjectID="(\d+)"', text)), default=0)


# ---------------------------------------------------------------------------------------------------------------------
# the changes
# ---------------------------------------------------------------------------------------------------------------------

class Edit:
    """Changes to the project text, applied at once (object spans are taken from the text as it was)."""

    def __init__(self, text: str):
        self.text, self.sp = text, spans(text)
        self.replace: dict[str, str] = {}        # object id -> its new text ('' removes it)
        self.added: list[str] = []
        self.next_id = max_object_id(text) + 1

    def body(self, k: str) -> str:
        if k in self.replace:
            return self.replace[k]
        s, e, _t = self.sp[k]
        return self.text[s:e]

    def set(self, k: str, new: str) -> None:
        if new != self.body(k):
            self.replace[k] = new

    def apply(self) -> str:
        new = self.text
        for k in sorted(self.replace, key=lambda k: -self.sp[k][0]):
            s, e, _t = self.sp[k]
            rep = self.replace[k]
            if not rep:                            # removed: its line too
                e = e + 1 if new[e:e + 1] == "\n" else e
            new = new[:s] + rep + new[e:]
        if self.added:
            at = new.rfind("</PremiereData>")
            new = new[:at] + "\n".join(self.added) + "\n" + new[at:]
        return new


def set_gain(obj: str, value: str) -> str:
    v = f"<Gain>{value}</Gain>"
    if "<Gain>" in obj:
        return re.sub(r"<Gain>[^<]*</Gain>", v, obj, count=1)
    return obj.replace("</SecondaryContents>", "</SecondaryContents>\n\t\t" + v, 1) if "</SecondaryContents>" in obj \
        else obj.replace("\n\t\t<AudioChannelLayout>", "\n\t\t" + v + "\n\t\t<AudioChannelLayout>", 1)


def set_level(obj: str, value: str) -> str:
    obj = re.sub(r"(<StartKeyframe>-?\d+,)[^,<]*(,)", lambda m: f"{m.group(1)}{float(value):.12f}{m.group(2)}", obj,
                 count=1)
    return re.sub(r"<CurrentValue>[^<]*</CurrentValue>", f"<CurrentValue>{value}</CurrentValue>", obj, count=1)


def _chain_ids(ed: Edit, chain: str) -> list[str]:
    return re.findall(r'<Component Index="\d+" ObjectRef="([^"]+)"', ed.body(chain))


def _write_chain(ed: Edit, chain: str, ids: list[str]) -> None:
    b = ed.body(chain)
    m = re.search(r"(\n\t*)<Components Version=\"1\">(.*?)(\n\t*)</Components>", b, re.S)
    if not m:
        return
    ind = m.group(3) + "\t"
    inner = "".join(f'{ind}<Component Index="{i}" ObjectRef="{c}"/>' for i, c in enumerate(ids))
    ed.set(chain, b[:m.start(2)] + inner + b[m.end(2):])


def remove_extra_volumes(ed: Edit, chain: str) -> int:
    """Every Volume effect of the chain but the clip's own built-in one is taken out (with its parameters); how many."""
    comps = chain_components(ed.text, chain, ed.sp)
    extra = [c for c in comps if c["name"] == "Volume" and not c["intrinsic"]]
    if not extra:
        return 0
    gone = {c["id"] for c in extra}
    _write_chain(ed, chain, [c for c in _chain_ids(ed, chain) if c not in gone])
    for c in extra:
        ed.set(c["id"], "")
        for p in c["params"].values():
            ed.set(p, "")
    return len(extra)


def builtin_volume_level(ed: Edit, chain: str) -> str | None:
    """The param id of the clip's own Volume's Level."""
    for c in chain_components(ed.text, chain, ed.sp):
        if c["name"] == "Volume" and c["intrinsic"]:
            return c["params"].get("Level")
    return None


def add_effects(ed: Edit, chain: str, preset: Path = PRESET) -> int:
    """Hard Limiter then Amplify (your preset's values, for the chain's channels) at the end of the chain, unless the
    chain has them already; how many were added."""
    comps = chain_components(ed.text, chain, ed.sp)
    have = {c["match"] for c in comps}
    ctype = next((c["channel_type"] for c in comps if c["name"] == "Volume"),
                 int((re.search(r"<ChannelType>(\d+)<", ed.body(chain)) or [None, "0"])[1]))
    ids = _chain_ids(ed, chain)
    used = [int(v) for c in comps for v in re.findall(r"<ID>(\d+)</ID>", ed.body(c["id"]))] or [0]
    n = 0
    for match in EFFECTS:
        if match in have:
            continue
        objs = template(match, ctype, preset)
        cid, texts = _renumber(objs, ed.next_id, max(used) + 1)
        ed.next_id += len(objs)
        used.append(max(used) + 1)
        ed.added += texts
        ids.append(cid)
        n += 1
    if n:
        _write_chain(ed, chain, ids)
    return n


def has_effects_in_order(text: str, chain: str, sp: dict | None = None, one_volume: bool = True) -> bool:
    """The chain is: the built-in Volume, no other Volume (``one_volume``), then the Hard Limiter, then Amplify."""
    comps = chain_components(text, chain, sp)
    if one_volume and [c for c in comps if c["name"] == "Volume" and not c["intrinsic"]]:
        return False
    order = [c["match"] for c in comps if c["match"] in EFFECTS]
    return order == list(EFFECTS)


def _a1(text: str, xml: Path) -> tuple[list[dict], list[tuple[int, float]], Fraction]:
    name, fps, ps = a1_pieces(xml)
    if not ps:
        return [], [], fps
    return clips_of(text, name), ps, fps


def _match(clips: list[dict], ps: list[tuple[int, float]], fps: Fraction) -> list[tuple[dict, float]] | None:
    """Each A1 clip of the project with its piece of the edit (by its start frame), on every track that holds the
    whole edit (an edit imported twice: both); None when no track matches."""
    if not clips or not ps or not fps:
        return None
    tpf = Fraction(TICKS_PER_SECOND) / fps
    tracks: dict[str, list[dict]] = {}
    for c in clips:
        tracks.setdefault(c.get("track", ""), []).append(c)
    out = []
    for cs in tracks.values():
        if len(cs) == len(ps) and all(abs(c["start"] / tpf - s) <= 1.01 for c, (s, _g) in zip(cs, ps)):
            out += [(c, g) for c, (_s, g) in zip(cs, ps)]
    return out or None


def a1_needs_fix(text: str, xml: Path) -> bool:
    from .song_gain import gain_value
    clips, ps, fps = _a1(text, xml)
    pairs = _match(clips, ps, fps)
    if not pairs:
        return False
    sp = spans(text)
    for c, g in pairs:
        a = text[sp[c["audioclip"]][0]:sp[c["audioclip"]][1]]
        m = re.search(r"<Gain>([^<]*)</Gain>", a)
        if abs((float(m.group(1)) if m else 1.0) - gain_value(g)) > TOL:
            return True
        if not has_effects_in_order(text, c["chain"], sp):
            return True
        for comp in chain_components(text, c["chain"], sp):
            if comp["name"] == "Volume" and comp["intrinsic"] and "Level" in comp["params"]:
                v = param_value(text, sp, comp["params"]["Level"])
                if v is not None and abs(v - float(VOLUME_0DB)) > TOL:
                    return True
    return False


def a1_fix(ed: Edit, xml: Path) -> str:
    """The A1 changes into ``ed`` (module docstring); what was done, in plain words ('' when nothing; a reason when
    the clips don't match)."""
    from .song_gain import gain_text
    clips, ps, fps = _a1(ed.text, xml)
    if not ps:
        return ""
    pairs = _match(clips, ps, fps)
    if pairs is None:
        return (f"A1's Clip Gain was not written: the project's A1 clips ({len(clips)}) are not the edit's "
                f"{len(ps)}")
    removed = added = 0
    for c, g in pairs:
        ed.set(c["audioclip"], set_gain(ed.body(c["audioclip"]), gain_text(g)))
        removed += remove_extra_volumes(ed, c["chain"])
        lv = builtin_volume_level(ed, c["chain"])
        if lv:
            ed.set(lv, set_level(ed.body(lv), VOLUME_0DB))
        added += add_effects(ed, c["chain"])
    if not ed.replace and not ed.added:
        return ""
    gains = [g for _c, g in pairs]
    return (f"A1: {len(pairs)} clips with their own Clip Gain ({min(gains):+.2f} to {max(gains):+.2f} dB), Volume 0 dB"
            + (f" ({removed} extra Volume effects removed)" if removed else "")
            + (", Hard Limiter (Input Boost 2 dB) + Amplify +3 dB" if added else ""))
