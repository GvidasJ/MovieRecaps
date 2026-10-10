"""``python -m match_cuts restyle PROJECT.prproj``: give every plain caption of a Premiere project the POPW style, its
position and its pop animation (restyle-prompt.md), and write ``3_captions_styled.prproj`` into the newest
numbered run folder (``--out`` for another).

The work is done by the four scripts in ``restyle_scripts/`` (capfix.py, capfix_xdonor.py, injectstyle.py,
capverify.py), each run as its own Python process on the unpacked project XML; this module only chooses their
arguments, checks the result and repacks it:

1. unpack the ``.prproj`` (gzipped XML) with Python's gzip module;
2. count the caption clips on every video track: **plain** (the text component only, as Premiere leaves them after
   "Upgrade caption to graphic") and **styled** (Motion, Graphic Group, Text);
3. the target is the track with the most plain captions;
4. the donor is a styled caption on another track of the same project (capfix.py within one sequence,
   capfix_xdonor.py from another sequence); a project without one takes it from the reference project
   (``reference/popw_reference.prproj``, capfix_xdonor.py), after injectstyle.py when it has no style item yet;
5. capverify.py must pass, and every restyled caption's keyframes must keep the donor's timing to the tick (the pop
   starts on the caption's in-point and lasts exactly as long as the donor's); otherwise nothing is written;
6. repack, and print a short report: captions styled, punctuation cleaned, and text worth a look (reported, never
   changed).

The original project is never written to.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import os
import re
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

SCRIPTS = Path(__file__).resolve().parent / "restyle_scripts"
DEFAULT_DONOR = Path(__file__).resolve().parents[3] / "reference" / "popw_reference.prproj"
TICKS_PER_SECOND = 254016000000          # Premiere's time base


class RestyleError(Exception):
    """The project could not be restyled; nothing was written. The message says why."""


# ---- reading the project XML (top-level objects sit at exactly one tab, as in the scripts) --------------------------

class ProjectXml:
    """Look-ups into an unpacked project. Every object is found at ``\\n\\t<Tag ObjectID="N"`` (one tab): Premiere
    reuses low ObjectIDs in nested metadata, so a looser match finds the wrong object."""

    def __init__(self, text: str):
        self.text = text
        self._ids: dict[str, tuple[int, str]] = {}
        for m in re.finditer(r'\n\t<(\w+) ObjectID="(\d+)"', text):
            self._ids.setdefault(m.group(2), (m.start() + 1, m.group(1)))
        self._uids: dict[str, tuple[int, str]] = {}
        for m in re.finditer(r'\n\t<(\w+) ObjectUID="([^"]+)"', text):
            self._uids.setdefault(m.group(2), (m.start() + 1, m.group(1)))
        self._hashes: dict[str, str] = {}
        for m in re.finditer(r'BinaryHash="([0-9a-f-]+)">([^<]+)</StartKeyframeValue>', text):
            self._hashes.setdefault(m.group(1), "".join(m.group(2).split()))

    def _block(self, where: tuple[int, str] | None) -> str | None:
        if where is None:
            return None
        start, tag = where
        end = self.text.find(f"</{tag}>", start)
        return None if end < 0 else self.text[start:end + len(tag) + 3]

    def obj(self, oid: str) -> str | None:
        return self._block(self._ids.get(str(oid)))

    def uobj(self, uid: str) -> str | None:
        return self._block(self._uids.get(uid))

    def components(self, item: str) -> list[str]:
        ti = self.obj(item) or ""
        c = re.search(r'<Components ObjectRef="(\d+)"/>', ti)
        return re.findall(r'<Component Index="\d+" ObjectRef="(\d+)"/>', self.obj(c.group(1)) or "") if c else []

    def params(self, comp: str) -> list[str]:
        return re.findall(r'<Param Index="\d+" ObjectRef="(\d+)"/>', self.obj(comp) or "")

    def text_component(self, item: str) -> str | None:
        return next((r for r in self.components(item) if "AE.ADBE Text" in (self.obj(r) or "")), None)

    def caption_text(self, item: str) -> str | None:
        """The caption's words (the tail of its text blob), None when it cannot be read."""
        tc = self.text_component(item)
        if tc is None:
            return None
        pp = dict(re.findall(r'<Param Index="(\d+)" ObjectRef="(\d+)"/>', self.obj(tc) or ""))
        b = re.search(r'BinaryHash="([0-9a-f-]+)"(?:/>|>([^<]+)<)', self.obj(pp.get("0", "")) or "")
        if not b:
            return None
        data = b.group(2) or self._hashes.get(b.group(1))
        if data is None:
            return None
        raw = base64.b64decode("".join(data.split()))
        end = len(raw)
        while end > 0 and raw[end - 1] == 0:
            end -= 1
        for s in range(end - 1, 3, -1):
            n = struct.unpack("<I", raw[s - 4:s])[0]
            if n == end - s and 0 < n < 500:
                return raw[s:end].decode("utf-8", "replace")
        return None

    def parent_style(self, item: str) -> str | None:
        tc = self.text_component(item)
        m = re.search(r'<ParentStyle ObjectURef="([^"]+)"/>', self.obj(tc) or "") if tc else None
        return m.group(1) if m else None

    def inpoint(self, item: str) -> int:
        sc = self.obj(re.search(r'<SubClip ObjectRef="(\d+)"/>', self.obj(item) or "").group(1)) or ""
        cl = re.search(r'<Clip ObjectRef="(\d+)"/>', sc).group(1)
        return int(re.search(r"<InPoint>(-?\d+)</InPoint>", self.obj(cl) or "").group(1))

    def start(self, item: str) -> int:
        m = re.search(r"<Start>(-?\d+)</Start>", self.obj(item) or "")
        return int(m.group(1)) if m else 0

    def keyframes(self, item: str) -> list[str | None]:
        """The ``<Keyframes>`` text (None: no keyframes) of every Motion and Graphic Group parameter, in order."""
        out: list[str | None] = []
        for comp in self.components(item)[:2]:
            for p in self.params(comp):
                m = re.search(r"<Keyframes>([^<]+)</Keyframes>", self.obj(p) or "")
                out.append(m.group(1) if m else None)
        return out

    def first_style_item(self) -> str | None:
        m = re.search(r'<StyleProjectItem ObjectUID="([^"]+)"', self.text)
        return m.group(1) if m else None


@dataclass
class Track:
    tg: str                  # the sequence's VideoTrackGroup ObjectID
    idx: int                 # 0-based (V1 = 0)
    sequence: str
    frame_ticks: int | None  # ticks per frame of the sequence
    items: list[str]
    plain: list[str]
    styled: list[str]
    other: list[str]         # clips that are not captions
    odd: list[str]           # captions with other components than plain or styled
    frame: tuple[int, int] | None = None   # the sequence's frame size (px)

    @property
    def name(self) -> str:
        return f"V{self.idx + 1}"

    @property
    def label(self) -> str:
        return f'{self.name} of "{self.sequence}"' if self.sequence else self.name


def scan(px: ProjectXml) -> list[Track]:
    """Every video track of every sequence with its plain and styled caption clips."""
    seq_names: dict[str, str] = {}
    for m in re.finditer(r"\n\t<Sequence [^>]*>.*?</Sequence>", px.text, re.S):
        names = re.findall(r"\n\t\t<Name>([^<]*)</Name>", m.group(0))
        for tg in re.findall(r'<Second ObjectRef="(\d+)"/>', m.group(0)):
            seq_names[tg] = names[-1] if names else ""
    tracks: list[Track] = []
    for m in re.finditer(r'\n\t<VideoTrackGroup ObjectID="(\d+)".*?</VideoTrackGroup>', px.text, re.S):
        tg, block = m.group(1), m.group(0)
        fr = re.search(r"<FrameRate>(\d+)</FrameRate>", block)
        fsz = re.search(r"<FrameRect>0,0,(\d+),(\d+)</FrameRect>", block)
        for i, uid in re.findall(r'<Track Index="(\d+)" ObjectURef="([^"]+)"/>', block):
            items = re.findall(r'<TrackItem Index="\d+" ObjectRef="(\d+)"/>', px.uobj(uid) or "")
            t = Track(tg, int(i), seq_names.get(tg, ""), int(fr.group(1)) if fr else None, items, [], [], [], [],
                      (int(fsz.group(1)), int(fsz.group(2))) if fsz else None)
            for it in items:
                refs = px.components(it)
                objs = [px.obj(r) or "" for r in refs]
                if not any("AE.ADBE Text" in o for o in objs):
                    t.other.append(it)
                elif len(refs) == 1:
                    t.plain.append(it)
                elif (len(refs) == 3 and "AE.ADBE Motion" in objs[0] and "AE.ADBE Graphic Group" in objs[1]
                      and "AE.ADBE Text" in objs[2]):
                    t.styled.append(it)
                else:
                    t.odd.append(it)
            tracks.append(t)
    return tracks


def _donor_track(tracks: Sequence[Track], exclude: Track | None = None) -> Track | None:
    """The track the scripts can take the style from: its FIRST clip is a styled caption (they clone that one).
    Most styled captions first; the target's own sequence before the others."""
    ok = [t for t in tracks if t is not exclude and t.items and t.items[0] in t.styled]
    if not ok:
        return None
    same = exclude.tg if exclude else None
    return max(ok, key=lambda t: (t.tg == same, len(t.styled), -t.idx))


# ---- running the scripts -------------------------------------------------------------------------------------------

def run_script(script: str, *args: object, cwd: Path) -> tuple[int, str]:
    """(exit code, stdout + stderr) of one of the restyle scripts, run with this Python."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")       # their console output (caption text) is UTF-8 too
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], cwd=str(cwd), env=env,
                       capture_output=True)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def _must(script: str, *args: object, cwd: Path) -> str:
    code, out = run_script(script, *args, cwd=cwd)
    if code != 0:
        raise RestyleError(f"{script} failed (exit {code}):\n{out.rstrip()}")
    return out


def _unpack(path: Path) -> tuple[bytes, bool]:
    """(the project XML, whether the file was gzipped -- a .prproj always is)."""
    raw = path.read_bytes()
    gz = raw[:2] == b"\x1f\x8b"
    return (gzip.decompress(raw) if gz else raw), gz


def _pack(data: bytes) -> bytes:
    import io
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as g:
        g.write(data)
    return buf.getvalue()


def _style_link_check(work: ProjectXml, donor: ProjectXml, donor_item: str) -> None:
    """The scripts link every caption to the project's FIRST style item: it has to be the donor's style."""
    want, have = donor.parent_style(donor_item), work.first_style_item()
    if want and have and want != have:
        raise RestyleError(f"the project's first style item ({have}) is not the style of the donor caption ({want}): "
                           "the captions would be linked to the wrong style")


# ---- the checks after the scripts ----------------------------------------------------------------------------------

def timing_problems(out: ProjectXml, items: Sequence[str], donor: ProjectXml, donor_item: str) -> list[str]:
    """Every keyframe of every restyled caption must sit where the donor's does, counted from the caption's
    in-point instead of the donor's first Scale keyframe, to the tick, with the same values and easing."""
    dk = donor.keyframes(donor_item)
    scale = dk[1] if len(dk) > 1 else None
    anchor = int(scale.split(",")[0]) if scale else donor.inpoint(donor_item)
    problems = []
    for it in items:
        tk = out.keyframes(it)
        txt = out.caption_text(it)
        if len(tk) != len(dk):
            problems.append(f"{txt!r}: {len(tk)} Motion / Graphic Group parameters, the donor has {len(dk)}")
            continue
        ip = out.inpoint(it)
        for k, (a, b) in enumerate(zip(dk, tk)):
            if (a is None) != (b is None):
                problems.append(f"{txt!r}: parameter {k} keyframed differently from the donor")
                continue
            if a is None:
                continue
            ra = [r.split(",") for r in a.split(";") if r.strip()]
            rb = [r.split(",") for r in b.split(";") if r.strip()]
            if len(ra) != len(rb) or any(x[1:] != y[1:] or int(x[0]) - anchor != int(y[0]) - ip
                                         for x, y in zip(ra, rb)):
                problems.append(f"{txt!r}: parameter {k} keyframes are not the donor's timing")
    return problems


def pop_timing(donor: ProjectXml, donor_item: str) -> tuple[str, str, int] | None:
    """(start %, end %, ticks) of the donor's Scale pop."""
    dk = donor.keyframes(donor_item)
    if len(dk) < 2 or not dk[1]:
        return None
    rows = [r.split(",") for r in dk[1].split(";") if r.strip()]
    return rows[0][1].rstrip("."), rows[-1][1].rstrip("."), int(rows[-1][0]) - int(rows[0][0])


def text_flags(texts: Sequence[str]) -> list[tuple[int, str]]:
    """(caption index, what) for text worth a look: doubled words, a weak last word, over 24 characters."""
    from .captions import HARD_CAP, is_weak, norm
    flags: list[tuple[int, str]] = []
    prev_last = None
    for i, t in enumerate(texts):
        words = [w for w in t.split() if norm(w)]
        for a, b in zip(words, words[1:]):
            if norm(a) == norm(b):
                flags.append((i, f'doubled word "{b}"'))
        if words and prev_last is not None and norm(words[0]) == prev_last:
            flags.append((i, f'starts with "{words[0]}", the word the caption before ends on'))
        if words and is_weak(words[-1]):
            flags.append((i, f'ends on a weak word "{words[-1]}"'))
        if len(t) > HARD_CAP:
            flags.append((i, f"{len(t)} characters (over {HARD_CAP})"))
        if re.fullmatch(r"\*+", t.strip()):
            flags.append((i, "a *...* placeholder: write the action there"))
        prev_last = norm(words[-1]) if words else None
    return flags


def _tc(ticks: int, frame_ticks: int | None) -> str:
    if not frame_ticks:
        return f"{ticks / TICKS_PER_SECOND:.3f}s"
    fps = round(TICKS_PER_SECOND / frame_ticks)
    f = int(round(ticks / frame_ticks))
    return f"{f // (3600 * fps):02d}:{f // (60 * fps) % 60:02d}:{f // fps % 60:02d}:{f % fps:02d}"


# ---- the entry point -----------------------------------------------------------------------------------------------

def styled_path(out_dir: str | os.PathLike | None = None) -> Path:
    """Where the styled project goes: ``<folder>/3_captions_styled.prproj`` -- the given folder, else the newest
    numbered run folder (in ./output, else the repository's output/)."""
    from .run_folders import STYLED_PRPROJ, default_output_bases, newest_run_dir
    if out_dir:
        d = Path(out_dir)
        if not d.is_dir():
            raise RestyleError(f"--out folder not found: {d}")
        return d / STYLED_PRPROJ
    for base in default_output_bases():
        d = newest_run_dir(base)
        if d is not None:
            return d / STYLED_PRPROJ
    raise RestyleError("no numbered run folder found (" + ", ".join(str(b) for b in default_output_bases()) +
                       "): pass --out with the folder to write 3_captions_styled.prproj into")


def restyle(project: str | os.PathLike, donor: str | os.PathLike | None = None, overwrite: bool = False,
            echo: Callable[[str], None] = print, out_dir: str | os.PathLike | None = None) -> Path:
    """Restyle the plain captions of ``project`` and write ``3_captions_styled.prproj`` into ``out_dir`` (default: the
    newest numbered run folder); returns that path. The project itself is never written to. Raises RestyleError (and
    writes nothing) when anything is wrong, capverify's problems included."""
    src = Path(project)
    if not src.is_file():
        raise RestyleError(f"project not found: {src}")
    dst = styled_path(out_dir)
    if dst.resolve() == src.resolve():
        raise RestyleError(f"{src} is the styled copy itself: restyle the project you saved in Premiere (it is never "
                           "overwritten)")
    if dst.exists() and not overwrite:
        raise RestyleError(f"{dst} already exists: rename or delete it, or add --overwrite")
    ref = Path(donor) if donor else DEFAULT_DONOR
    data, gz = _unpack(src)
    px = ProjectXml(data.decode("utf-8"))
    tracks = scan(px)
    target = max(tracks, key=lambda t: len(t.plain), default=None)
    if target is None or not target.plain:
        raise RestyleError("no plain caption clips found: import 2_captions.srt, drag it onto the sequence, upgrade the "
                           "captions to graphics and save the project first" +
                           (" (the captions here are already styled)" if any(t.styled for t in tracks) else ""))
    if target.styled or target.other or target.odd:
        raise RestyleError(f"{target.label} has {len(target.plain)} plain captions but also "
                           f"{len(target.styled) + len(target.other) + len(target.odd)} other clips: the restyle "
                           "works on a track of "
                           "plain captions only (move the other clips to another track)")
    for it in target.plain:
        t = px.caption_text(it)
        if t is not None and not re.sub(r"[\s.,]", "", t):
            raise RestyleError(f"the caption at {_tc(px.start(it), target.frame_ticks)} is only dots and commas "
                               f"({t!r}): it would be empty once they are stripped -- write its words first")
    same = _donor_track(tracks, exclude=target)
    with tempfile.TemporaryDirectory(prefix="match_cuts_restyle_") as tmp:
        td = Path(tmp)
        orig = td / "project.xml"
        orig.write_bytes(data)
        work, out_xml = orig, td / "styled.xml"
        dpx: ProjectXml | None = None
        notes: list[str] = []
        if same is None or "<StyleProjectItem" not in px.text:
            if not ref.is_file():
                raise RestyleError(f"the reference project {ref} is missing (it supplies the POPW style): pass "
                                   "--donor with a correctly styled project")
            ref_xml = td / "reference.xml"
            ref_xml.write_bytes(_unpack(ref)[0])
            dpx = ProjectXml(ref_xml.read_bytes().decode("utf-8"))
            if "<StyleProjectItem" not in px.text:
                work = td / "with_style.xml"
                _must("injectstyle.py", ref_xml, orig, work, cwd=td)
                notes.append(f"style item POPW added to the project panel (from {ref.name})")
        wpx = px if work == orig else ProjectXml(work.read_bytes().decode("utf-8"))
        donor_frame = None
        if same is not None:
            donor_px, donor_item = px, same.items[0]
            donor_frame = same.frame
            source = f"{same.label} of this project"
            _style_link_check(wpx, donor_px, donor_item)
            if same.tg == target.tg:
                log = _must("capfix.py", work, out_xml, target.tg, same.idx, target.idx, cwd=td)
                verify_idx = same.idx
            else:
                log = _must("capfix_xdonor.py", work, same.tg, same.idx, work, out_xml, target.tg, target.idx,
                            cwd=td)
                verify_idx = None
        else:
            dtrack = _donor_track(scan(dpx))
            if dtrack is None:
                raise RestyleError(f"{ref.name} has no styled caption to copy the style from")
            donor_px, donor_item = dpx, dtrack.items[0]
            donor_frame = dtrack.frame
            source = f"{ref.name} {dtrack.name}"
            _style_link_check(wpx, donor_px, donor_item)
            log = _must("capfix_xdonor.py", ref_xml, dtrack.tg, dtrack.idx, work, out_xml, target.tg, target.idx,
                        cwd=td)
            verify_idx = None
        # the scripts' own notes (a trimmed donor: its keyframes, not its in-point, anchor the pop)
        notes += [ln.strip() for ln in log.splitlines() if ln.startswith("note:")]
        if verify_idx is None:        # capverify also checks a donor track: give it one without captions
            free = [t.idx for t in tracks
                    if t.tg == target.tg and t is not target and not (t.plain or t.styled or t.odd)]
            verify_idx = free[0] if free else target.idx
        code, vout = run_script("capverify.py", orig, out_xml, target.tg, verify_idx, target.idx, cwd=td)
        if code != 0:
            raise RestyleError("capverify found problems:\n" + vout.rstrip())
        result = out_xml.read_bytes()
        opx = ProjectXml(result.decode("utf-8"))
        problems = timing_problems(opx, target.plain, donor_px, donor_item)
        if problems:
            raise RestyleError("the pop timing differs from the donor's:\n" + "\n".join(f"  - {p}" for p in problems))
        before = [px.caption_text(it) or "" for it in target.plain]
        after = [opx.caption_text(it) or "" for it in target.plain]
        pop = pop_timing(donor_px, donor_item)
        tmp_dst = dst.with_name(dst.name + ".tmp")
        try:
            from .common import replace_file
            tmp_dst.write_bytes(_pack(result) if gz else result)
            replace_file(tmp_dst, dst)               # --overwrite while Premiere has it open: retried, then explained
        finally:
            if tmp_dst.exists():
                tmp_dst.unlink()
    # ---- the report ----
    starts = [px.start(it) for it in target.plain]
    ft = target.frame_ticks
    echo(f"Restyled {len(after)} captions on {target.label} -> {dst}")
    echo(f"  style, position and pop copied from {source}")
    if donor_frame and target.frame and donor_frame[0] != target.frame[0]:
        r = target.frame[0] / float(donor_frame[0])
        echo(f"  SIZE: the donor caption comes from a {donor_frame[0]}x{donor_frame[1]} sequence, your captions are on "
             f"a {target.frame[0]}x{target.frame[1]} one (--frame: 2160x3840). Premiere keeps a graphic's size in pixels, "
             f"so they look {r:g} x {'smaller' if r > 1 else 'bigger'} -- select them all and multiply Effect Controls > "
             f"Motion > Scale by {r:g}, or style one caption at the right size and restyle with it as the donor")
    for n in notes:
        echo(f"  {n}")
    if pop:
        a, b, ticks = pop
        frames = f" = {ticks / ft:g} frames at {TICKS_PER_SECOND / ft:g} fps" if ft else ""
        echo(f"  pop: Scale {a}% -> {b}% over {ticks / TICKS_PER_SECOND:.4f} s ({ticks} ticks{frames}), "
             "starting on each caption's first frame, exactly as in the donor")
    echo("  capverify: all checks passed")
    cleaned = [(i, a, b) for i, (a, b) in enumerate(zip(before, after)) if a != b]
    echo(f"Punctuation cleaned: {len(cleaned)} caption(s)")
    for i, a, b in cleaned:
        echo(f'  {_tc(starts[i], ft)}  "{a}" -> "{b}"')
    flags = text_flags(after)
    echo(f"Worth a look (reported, not changed): {len(flags)}")
    for i, what in flags:
        echo(f'  {_tc(starts[i], ft)}  "{after[i]}": {what}')
    left = [t for t in tracks if t is not target and t.plain]
    if left:
        echo("Plain captions on other tracks, left as they are (only the track with the most is restyled): " +
             ", ".join(f"{t.label}: {len(t.plain)}" for t in left))
    for line in frame_check(dst):
        echo(line)
    return dst


def frame_check(styled: Path) -> list[str]:
    """--frame runs: the restyled captions' place against the frame's caption zone (the run folder's
    extras/frame.json): inside the transparent hole, clear of the header and the headline. A report line (or a
    warning per caption outside it); [] for a run without a frame."""
    import json
    from .run_folders import EXTRAS
    info = Path(styled).parent / EXTRAS / "frame.json"
    if not info.is_file():
        return []
    try:
        fr = json.loads(info.read_text(encoding="utf-8"))
        lo, hi = (float(v) for v in fr["caption_zone_frac"])
        from . import prproj as PR
        pr = PR.read(styled)
        size = tuple(int(v) for v in fr.get("sequence") or ())
        # the frame's sequence (a project built on a template holds other videos' sequences too), its most captions
        seqs = [s for s in pr.sequences if (s.width, s.height) == size] or pr.sequences
        caps = max((PR.captions_of(s) for s in seqs), key=len, default=[])
    except Exception as e:  # noqa: BLE001 - a check, never a failure
        return [f"Frame: the captions' place could not be checked ({type(e).__name__}: {e})"]
    ys = [(c, float(c.position[1])) for c in caps if c.position is not None]
    if not ys:
        return [f"Frame {Path(fr.get('path', '')).name}: keep the captions between {100 * lo:.0f} % and {100 * hi:.0f} % "
                "of the height (inside the hole, clear of the header and headline) -- their place could not be read"]
    out_of = [(c, y) for c, y in ys if not lo <= y <= hi]
    if not out_of:
        return [f"Frame {Path(fr.get('path', '')).name}: every caption sits at {100 * ys[0][1]:.0f} % of the height -- "
                f"inside the hole, clear of the header and headline ({100 * lo:.0f}-{100 * hi:.0f} %)"]
    return [f"FRAME: {len(out_of)} caption(s) outside the hole's caption zone ({100 * lo:.0f}-{100 * hi:.0f} % of the "
            f"height): over the header / headline or the frame's bottom -- move them (Effect Controls > Position):"] + \
        [f"  {c.start:.2f} s \"{c.text}\": at {100 * y:.0f} %" for c, y in out_of[:12]]


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m match_cuts restyle",
        description="Give every plain caption of a Premiere project (captions upgraded to graphics) the POPW style, "
                    "position and pop animation, and write 3_captions_styled.prproj into the newest numbered run "
                    "folder (or --out). The original is never changed; when capverify finds a problem nothing is "
                    "written.")
    p.add_argument("project", help="the saved .prproj")
    p.add_argument("--donor", default=None, metavar="PRPROJ",
                   help="a correctly styled project to take the style from when this one has no styled caption "
                        f"(default {DEFAULT_DONOR})")
    p.add_argument("--out", default=None, metavar="FOLDER",
                   help="the folder to write 3_captions_styled.prproj into (default: the newest numbered run folder, "
                        "e.g. output\\003)")
    p.add_argument("--overwrite", action="store_true", help="replace an existing 3_captions_styled.prproj")
    args = p.parse_args(argv)
    try:
        restyle(args.project, donor=args.donor, overwrite=args.overwrite, out_dir=args.out)
    except RestyleError as e:
        print(f"match_cuts restyle: {e}", file=sys.stderr)
        print("match_cuts restyle: nothing was written", file=sys.stderr)
        return 1
    return 0
