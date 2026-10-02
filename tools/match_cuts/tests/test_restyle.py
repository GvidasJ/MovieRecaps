"""``python -m match_cuts restyle``: the four restyle scripts wired in (match_cuts/restyle.py), on the reference
projects in ``reference/``: ``plain_captions.prproj`` (captions upgraded to graphics, not styled) must come out
exactly like ``popw_reference.prproj`` (the same project styled), capverify must pass, the pop must keep the
donor's timing to the tick, and nothing may be written when anything is wrong."""
from __future__ import annotations

import ast
import base64
import gzip
import hashlib
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from match_cuts import cli, restyle

REF_DIR = Path(__file__).resolve().parents[3] / "reference"
PLAIN, POPW = REF_DIR / "plain_captions.prproj", REF_DIR / "popw_reference.prproj"
TOOL_DIR = Path(restyle.__file__).resolve().parents[1]
PLAIN_TG, POPW_TG, CAPTIONS = "267", "76", 7          # both: 18 captions on V8 (index 7) of their one sequence
POPW_UID = "d6ca2753-d18f-4ccf-8d37-8af5fe27e955"
FRAME = 4233600000                                     # ticks per frame, 60 fps
POP_TICKS = 33868800000                                # the donor's 88% -> 100% (0.1333 s = 8 frames at 60 fps)

pytestmark = pytest.mark.skipif(not (PLAIN.is_file() and POPW.is_file()),
                                reason="reference/*.prproj not in this checkout")


# ---- reading projects, independently of restyle.py ----------------------------------------------------------------

def unpack(path: Path) -> str:
    return gzip.decompress(Path(path).read_bytes()).decode("utf-8")


def pack(text: str, path: Path) -> Path:
    Path(path).write_bytes(gzip.compress(text.encode("utf-8")))
    return Path(path)


class Doc:
    def __init__(self, text: str):
        self.x = text
        self.hd: dict[str, str] = {}
        for m in re.finditer(r'BinaryHash="([0-9a-f-]+)">([^<]+)</(\w+)>', text):
            self.hd.setdefault(m.group(1), "".join(m.group(2).split()))
        self.spans: dict[str, tuple[int, int]] = {}   # every top-level object (one tab), first of each id
        for m in re.finditer(r'\n\t<(\w+) ObjectID="(\d+)"[^>]*>.*?</\1>', text, re.S):
            self.spans.setdefault(m.group(2), (m.start() + 1, m.end()))

    def span(self, oid: str) -> tuple[int, int]:
        return self.spans[str(oid)]

    def obj(self, oid: str) -> str:
        a, b = self.span(oid)
        return self.x[a:b]

    def items(self, tg: str, idx: int) -> list[str]:
        t = re.search(rf'<VideoTrackGroup ObjectID="{tg}".*?</VideoTrackGroup>', self.x, re.S).group(0)
        tr = dict((int(i), u) for i, u in re.findall(r'<Track Index="(\d+)" ObjectURef="([^"]+)"/>', t))
        tt = re.search(rf'<VideoClipTrack ObjectUID="{tr[idx]}".*?</VideoClipTrack>', self.x, re.S).group(0)
        return re.findall(r'<TrackItem Index="\d+" ObjectRef="(\d+)"/>', tt)

    def comps(self, it: str) -> list[str]:
        ch = self.obj(re.search(r'<Components ObjectRef="(\d+)"/>', self.obj(it)).group(1))
        return re.findall(r'<Component Index="\d+" ObjectRef="(\d+)"/>', ch)

    def params(self, comp: str) -> list[str]:
        return re.findall(r'<Param Index="\d+" ObjectRef="(\d+)"/>', self.obj(comp))

    def canon(self, oid: str) -> str:
        """The object with its own id dropped and every binary value inlined (hash names are not compared)."""
        o = re.sub(r' ObjectID="\d+"', "", self.obj(oid))
        o = re.sub(r'<(\w+) Encoding="base64" BinaryHash="([0-9a-f-]+)"\s*/>',
                   lambda m: f'<{m.group(1)} Encoding="base64" BinaryHash="H">{self.hd[m.group(2)]}</{m.group(1)}>', o)
        return re.sub(r'<(\w+) Encoding="base64" BinaryHash="([0-9a-f-]+)">([^<]+)</\1>',
                      lambda m: f'<{m.group(1)} Encoding="base64" BinaryHash="H">{"".join(m.group(3).split())}'
                                f'</{m.group(1)}>', o)

    def caption(self, it: str) -> dict:
        """Everything that makes a caption look and move: its clip, its components in order and every parameter."""
        out = {"item": re.sub(r'ObjectRef="\d+"', "R", re.sub(r' ObjectID="\d+"', "", self.obj(it)))}
        for k, c in enumerate(self.comps(it)):
            out[f"c{k}"] = re.sub(r'ObjectRef="\d+"', "R", self.canon(c))
            for i, p in enumerate(self.params(c)):
                out[f"c{k}p{i}"] = self.canon(p)
        return out

    def text(self, it: str) -> str:
        p0 = self.params(self.comps(it)[-1])[0]
        b = re.search(r'BinaryHash="([0-9a-f-]+)"(?:/>|>([^<]+)<)', self.obj(p0))
        raw = base64.b64decode("".join((b.group(2) or self.hd[b.group(1)]).split()))
        return _split_tail(raw)[1].decode("utf-8")

    def inpoint(self, it: str) -> int:
        sc = self.obj(re.search(r'<SubClip ObjectRef="(\d+)"/>', self.obj(it)).group(1))
        return int(re.search(r"<InPoint>(-?\d+)</InPoint>", self.obj(re.search(r'<Clip ObjectRef="(\d+)"/>',
                                                                               sc).group(1))).group(1))

    def scale_keys(self, it: str) -> list[list[str]]:
        kf = re.search(r"<Keyframes>([^<]+)</Keyframes>", self.obj(self.params(self.comps(it)[0])[1])).group(1)
        return [r.split(",") for r in kf.split(";") if r.strip()]


def _split_tail(raw: bytes) -> tuple[bytes, bytes]:
    end = len(raw)
    while end > 0 and raw[end - 1] == 0:
        end -= 1
    for s in range(end - 1, 3, -1):
        n = struct.unpack("<I", raw[s - 4:s])[0]
        if n == end - s and 0 < n < 500:
            return raw[:s - 4], raw[s:end]
    raise ValueError("no text in the blob")


def differences(a: Doc, items_a: list[str], b: Doc, items_b: list[str]) -> list[str]:
    assert len(items_a) == len(items_b)
    out = []
    for k, (x, y) in enumerate(zip(items_a, items_b)):
        ca, cb = a.caption(x), b.caption(y)
        out += [f"caption {k} ({a.text(x)!r}): {key}" for key in sorted(set(ca) | set(cb))
                if ca.get(key) != cb.get(key)]
    return out


POPW_DOC = Doc(unpack(POPW)) if POPW.is_file() else None


def _edit_obj(text: str, oid: str, fn) -> str:
    d = Doc(text)
    a, b = d.span(oid)
    new = fn(text[a:b])
    assert new != text[a:b]
    return text[:a] + new + text[b:]


def set_caption_text(text: str, item: str, words: str) -> str:
    """The plain caption ``item`` reading ``words`` (its text blob rebuilt the way Premiere stores it). Premiere
    stores a blob once and lets identical ones point at it by hash: when this caption held the data, the next
    caption pointing at it gets the data instead."""
    d = Doc(text)
    p0 = d.params(d.comps(item)[-1])[0]
    old = re.search(r'BinaryHash="([0-9a-f-]+)">([^<]+)</StartKeyframeValue>', d.obj(p0))
    if old:
        refs = [m for m in re.finditer(rf'<StartKeyframeValue Encoding="base64" BinaryHash="{old.group(1)}"\s*/>',
                                       text)]
        if refs:
            m = refs[0]
            text = (text[:m.start()] + f'<StartKeyframeValue Encoding="base64" BinaryHash="{old.group(1)}">'
                    f'{old.group(2)}</StartKeyframeValue>' + text[m.end():])
            d = Doc(text)

    def fn(pxml: str) -> str:
        b = re.search(r'BinaryHash="([0-9a-f-]+)"(?:/>|>([^<]+)</StartKeyframeValue>)', pxml)
        head, _ = _split_tail(base64.b64decode("".join((b.group(2) or d.hd[b.group(1)]).split())))
        t = words.encode("utf-8")
        blob = bytearray(head + struct.pack("<I", len(t)) + t + b"\0" * (4 * ((len(t) + 4) // 4) - len(t)))
        blob[0:4] = struct.pack("<I", len(blob) - 12)
        h = hashlib.md5(blob).hexdigest()[:24]
        h = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:24]}{len(blob) + 12:08x}"
        new = f'<StartKeyframeValue Encoding="base64" BinaryHash="{h}">{base64.b64encode(bytes(blob)).decode()}\n\t\t' \
              '</StartKeyframeValue>'
        return re.sub(r'<StartKeyframeValue Encoding="base64" BinaryHash="[0-9a-f-]+"'
                      r'(?:/>|>[^<]*</StartKeyframeValue>)',
                      lambda m: new, pxml, count=1)
    return _edit_obj(text, p0, fn)


# ---- the reference: plain_captions.prproj -> popw_reference.prproj ------------------------------------------------

@pytest.fixture(scope="module")
def plain_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("plain")
    src = d / "plain_captions.prproj"
    shutil.copy(PLAIN, src)
    lines: list[str] = []
    out = restyle.restyle(src, echo=lines.append)
    return src, out, lines


def test_writes_name_styled_next_to_the_original_and_never_touches_it(plain_run):
    src, out, _ = plain_run
    assert out == src.with_name("plain_captions_styled.prproj")
    assert src.read_bytes() == PLAIN.read_bytes()
    assert out.read_bytes()[:2] == b"\x1f\x8b"                         # a gzipped project, as Premiere saves it
    assert sorted(p.name for p in src.parent.iterdir()) == ["plain_captions.prproj", "plain_captions_styled.prproj"]
    with pytest.raises(restyle.RestyleError, match="already exists"):  # a second run never replaces it silently
        restyle.restyle(src, echo=lambda s: None)


def test_restyled_plain_reference_is_popw_reference(plain_run, tmp_path):
    out = Doc(unpack(plain_run[1]))
    items = out.items(PLAIN_TG, CAPTIONS)
    assert len(items) == 18
    assert differences(out, items, POPW_DOC, POPW_DOC.items(POPW_TG, CAPTIONS)) == []
    # and capverify, run on its own, agrees
    (tmp_path / "orig.xml").write_bytes(gzip.decompress(PLAIN.read_bytes()))
    (tmp_path / "out.xml").write_bytes(gzip.decompress(plain_run[1].read_bytes()))
    code, log = restyle.run_script("capverify.py", tmp_path / "orig.xml", tmp_path / "out.xml", PLAIN_TG, 0, CAPTIONS,
                                   cwd=tmp_path)
    assert code == 0 and "checked 18 captions" in log and "all checks passed" in log, log


def test_pop_keeps_the_donors_timing_to_the_tick(plain_run):
    out = Doc(unpack(plain_run[1]))
    donor = POPW_DOC.items(POPW_TG, CAPTIONS)[0]
    dk = POPW_DOC.scale_keys(donor)
    assert int(dk[1][0]) - int(dk[0][0]) == POP_TICKS == 8 * FRAME   # 0.1333 s: 8 frames at 60 fps, not 4
    for it in out.items(PLAIN_TG, CAPTIONS):
        k = out.scale_keys(it)
        assert int(k[0][0]) == out.inpoint(it)                         # the pop starts on the caption's first frame
        assert int(k[1][0]) - int(k[0][0]) == POP_TICKS
        assert [r[1:] for r in k] == [r[1:] for r in dk]               # 88 -> 100 with the donor's easing
        assert k[0][1].startswith("88") and k[1][1].startswith("100")


def test_nothing_but_the_captions_changes(plain_run):
    before, after = Doc(unpack(PLAIN)), Doc(unpack(plain_run[1]))
    touched = set()
    for it in before.items(PLAIN_TG, CAPTIONS):
        touched.add(re.search(r'<Components ObjectRef="(\d+)"/>', before.obj(it)).group(1))
        text = before.comps(it)[-1]
        touched |= {text, *before.params(text)}
    assert len(before.spans) > 1000
    for oid in before.spans:
        if oid not in touched:
            assert after.obj(oid) == before.obj(oid), oid
    assert "\r" not in after.x and after.x.startswith(before.x[:2000])  # line endings and header as they were


def test_report_counts_cleans_and_flags(plain_run):
    lines = plain_run[2]
    text = "\n".join(lines)
    assert lines[0].startswith('Restyled 18 captions on V8 of "FOCUS SCENE 1" -> ')
    assert "copied from popw_reference.prproj V8" in text
    assert "pop: Scale 88% -> 100% over 0.1333 s (33868800000 ticks = 8 frames at 60 fps)" in text
    assert "capverify: all checks passed" in text
    assert 'Punctuation cleaned: 1 caption(s)' in text and '"we going after this." -> "we going after this"' in text
    assert 'ends on a weak word "that"' in text                        # reported, not changed


def test_text_flags_report_doubled_weak_long_and_placeholders():
    flags = restyle.text_flags(["I I know", "know the", "this caption is far too long to read", "**", "fine"])
    assert flags == [(0, 'doubled word "I"'),
                     (1, 'starts with "know", the word the caption before ends on'),
                     (1, 'ends on a weak word "the"'),
                     (2, "36 characters (over 24)"),
                     (3, "a *...* placeholder: write the action there")]


# ---- the other donor routes ----------------------------------------------------------------------------------------

def test_project_without_a_style_item_gets_popw_injected(tmp_path):
    x = unpack(PLAIN)
    x = re.sub(r'\n\t<StyleProjectItem ObjectUID="[^"]+".*?</StyleProjectItem>', "", x, count=1, flags=re.S)
    x = re.sub(rf'\n\s*<Item Index="\d+" ObjectURef="{POPW_UID}"/>', "", x, count=1)
    assert "<StyleProjectItem" not in x and f'ObjectURef="{POPW_UID}"' not in x
    lines: list[str] = []
    out = restyle.restyle(pack(x, tmp_path / "no_style.prproj"), echo=lines.append)
    y = Doc(unpack(out))
    style = re.findall(r'\n\t<StyleProjectItem ObjectUID="([^"]+)".*?<Name>([^<]*)</Name>', y.x, re.S)
    assert style == [(POPW_UID, "POPW")]
    root = re.search(r"\n\t<RootProjectItem .*?</RootProjectItem>", y.x, re.S).group(0)
    assert f'ObjectURef="{POPW_UID}"/>' in root                          # in the project panel
    assert differences(y, y.items(PLAIN_TG, CAPTIONS), POPW_DOC, POPW_DOC.items(POPW_TG, CAPTIONS)) == []
    assert any("style item POPW added" in ln for ln in lines)


def move_first_caption(x: str, tg: str, src: int, dst: int) -> str:
    """The first clip of track ``src`` moved to the empty track ``dst`` of the same sequence."""
    t = re.search(rf'<VideoTrackGroup ObjectID="{tg}".*?</VideoTrackGroup>', x, re.S).group(0)
    tr = dict((int(i), u) for i, u in re.findall(r'<Track Index="(\d+)" ObjectURef="([^"]+)"/>', t))

    def span(u: str) -> tuple[int, int]:
        m = re.search(rf'\n\t<VideoClipTrack ObjectUID="{u}".*?</VideoClipTrack>', x, re.S)
        return m.start(), m.end()
    a, b = span(tr[src])
    first = re.search(r'\n\s*<TrackItem Index="0" ObjectRef="(\d+)"/>', x[a:b])
    rest = x[a:b][:first.start()] + x[a:b][first.end():]
    rest = re.sub(r'<TrackItem Index="(\d+)"', lambda m: f'<TrackItem Index="{int(m.group(1)) - 1}"', rest)
    x = x[:a] + rest + x[b:]
    a, b = span(tr[dst])
    assert "<TrackItems" not in x[a:b]
    moved = x[a:b].replace('<ClipItems Version="3">', '<ClipItems Version="3">\n\t\t\t\t<TrackItems Version="1">\n'
                           f'\t\t\t\t\t<TrackItem Index="0" ObjectRef="{first.group(1)}"/>\n\t\t\t\t</TrackItems>', 1)
    return x[:a] + moved + x[b:]


def test_styled_caption_on_another_track_is_the_donor(tmp_path):
    # V7: the first caption, styled from the reference by hand (the script); V8: the 17 others, plain
    (tmp_path / "plain.xml").write_bytes(move_first_caption(unpack(PLAIN), PLAIN_TG, CAPTIONS, 6).encode("utf-8"))
    (tmp_path / "ref.xml").write_bytes(gzip.decompress(POPW.read_bytes()))
    code, log = restyle.run_script("capfix_xdonor.py", tmp_path / "ref.xml", POPW_TG, CAPTIONS, tmp_path / "plain.xml",
                                   tmp_path / "mixed.xml", PLAIN_TG, 6, cwd=tmp_path)
    assert code == 0, log
    mixed = pack((tmp_path / "mixed.xml").read_text(encoding="utf-8"), tmp_path / "mixed.prproj")
    lines: list[str] = []
    out = restyle.restyle(mixed, donor=tmp_path / "no_such_reference.prproj", echo=lines.append)  # not needed
    assert lines[0].startswith("Restyled 17 captions on V8")
    assert 'copied from V7 of "FOCUS SCENE 1" of this project' in lines[1]
    y, ref = Doc(unpack(out)), POPW_DOC.items(POPW_TG, CAPTIONS)
    assert differences(y, y.items(PLAIN_TG, 6) + y.items(PLAIN_TG, CAPTIONS), POPW_DOC, ref) == []


def test_trimmed_donor_anchors_the_pop_on_its_keyframes(tmp_path):
    d = unpack(POPW)
    doc = Doc(d)
    item = doc.items(POPW_TG, CAPTIONS)[0]
    clip = re.search(r'<Clip ObjectRef="(\d+)"/>',
                     doc.obj(re.search(r'<SubClip ObjectRef="(\d+)"/>', doc.obj(item)).group(1))).group(1)
    d = _edit_obj(d, clip, lambda o: re.sub(r"<InPoint>(-?\d+)</InPoint>",           # its head trimmed by 4 frames
                                            lambda m: f"<InPoint>{int(m.group(1)) + 4 * FRAME}</InPoint>", o, count=1))
    src = tmp_path / "plain_captions.prproj"
    shutil.copy(PLAIN, src)
    lines: list[str] = []
    out = restyle.restyle(src, donor=pack(d, tmp_path / "trimmed.prproj"), echo=lines.append)
    assert any(f"note: donor keyframes sit {-4 * FRAME:+d} ticks from its in-point" in ln for ln in lines)
    y = Doc(unpack(out))                                     # every pop still starts on its caption's first frame
    assert differences(y, y.items(PLAIN_TG, CAPTIONS), POPW_DOC, POPW_DOC.items(POPW_TG, CAPTIONS)) == []


# ---- failures write nothing ----------------------------------------------------------------------------------------

def test_capverify_problem_writes_nothing_and_prints_the_problems(tmp_path, capsys):
    d = unpack(POPW)
    doc = Doc(d)
    scale = doc.params(doc.comps(doc.items(POPW_TG, CAPTIONS)[0])[0])[1]
    d = _edit_obj(d, scale, lambda o: o.replace(",88.,", ",90.,", 1))   # a donor whose pop starts at 90%
    bad = pack(d, tmp_path / "bad.prproj")
    src = tmp_path / "plain_captions.prproj"
    shutil.copy(PLAIN, src)
    assert cli.main(["restyle", str(src), "--donor", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "capverify found problems" in err and "18 PROBLEM(S)" in err and "pop does not start at 88%" in err
    assert "nothing was written" in err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bad.prproj", "plain_captions.prproj"]
    assert src.read_bytes() == PLAIN.read_bytes()


def test_already_styled_or_missing_projects_are_refused(tmp_path):
    src = tmp_path / "popw.prproj"
    shutil.copy(POPW, src)
    with pytest.raises(restyle.RestyleError, match="no plain caption clips found.*already styled"):
        restyle.restyle(src, echo=lambda s: None)
    with pytest.raises(restyle.RestyleError, match="project not found"):
        restyle.restyle(tmp_path / "nope.prproj", echo=lambda s: None)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["popw.prproj"]


# ---- Windows: files as UTF-8 with their line endings, gzip in Python, numbers kept --------------------------------

def test_scripts_open_every_file_as_utf8_without_newline_translation():
    for f in sorted(restyle.SCRIPTS.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        opens = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"]
        assert opens, f.name
        for n in opens:
            kw = {k.arg: k.value.value for k in n.keywords if isinstance(k.value, ast.Constant)}
            assert kw.get("encoding") == "utf-8" and kw.get("newline") == "", (f.name, ast.unparse(n))
        assert "zcat" not in src and "gzip" not in src
    assert "zcat" not in Path(restyle.__file__).read_text(encoding="utf-8")


def test_cli_under_a_non_utf8_locale_keeps_numbers(tmp_path, venv_python):
    """The CLI as the user runs it, with the locale's encoding ASCII (like cp1252 on Windows, the scripts' old
    open() without an encoding fails there on the project's non-ASCII bytes), on captions with numbers in them."""
    x = unpack(PLAIN)
    items = Doc(x).items(PLAIN_TG, CAPTIONS)
    x = set_caption_text(x, items[0], "it cost £4.50, Mr. Smith.")
    x = set_caption_text(x, items[1], "15,000 fans at 6 a.m.")
    x = set_caption_text(x, items[2], "the C.I.D. said 3.5, then 4,")
    src = pack(x, tmp_path / "my edit.prproj")
    (tmp_path / "my edit_styled.prproj").write_bytes(b"an earlier result")
    env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONCOERCECLOCALE="0", PYTHONUTF8="0")
    env.pop("PYTHONIOENCODING", None)
    cmd = [venv_python, "-m", "match_cuts", "restyle", str(src)]
    r = subprocess.run(cmd, cwd=TOOL_DIR, env=env, capture_output=True)
    assert r.returncode == 1 and b"already exists" in r.stderr and b"nothing was written" in r.stderr
    assert (tmp_path / "my edit_styled.prproj").read_bytes() == b"an earlier result"
    r = subprocess.run(cmd + ["--overwrite"], cwd=TOOL_DIR, env=env, capture_output=True)
    assert r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace")
    y = Doc(unpack(tmp_path / "my edit_styled.prproj"))
    out = y.items(PLAIN_TG, CAPTIONS)
    assert [y.text(it) for it in out[:3]] == ["it cost £4.50 Mr Smith", "15,000 fans at 6 am",
                                               "the CID said 3.5 then 4"]
    assert "\r" not in y.x and "_22FIXEDSpider-Man： Homecoming：" in y.x   # non-ASCII and LF kept
    assert b'"15,000 fans at 6 a.m." -> "15,000 fans at 6 am"' in r.stdout
