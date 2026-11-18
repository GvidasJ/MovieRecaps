"""18 Nov (the first real run after the handover: The Best Business Idea (2) made again as a test -- its zip held
360 MB of the checks' pictures and the 199 MB normal RAW piece nothing plays, and the enhanced picture was squeezed
from 44 to 7 Mb/s to make room; then the clean-up round made the whole zip a second time, encoding the picture
again on the graphics card, only because project.json had changed):

* the normal RAW's piece of an edit that plays the enhanced picture stays out of the zip whatever its size;
* the checks' pictures (edit\\extras\\debug\\verify_failures, cuts ...) stay out of a zip that would be over 1 GB
  with them -- before any video is made lighter -- and the folder may still become its zip;
* a zip made again takes its lighter copies from the older zip (no second encode)."""
from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from mr_app.core import archive as A
from mr_app.core import share_zip as Z

from test_zip_enhanced_only_7nov import _big, _project


def _small(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(size)
    return path


def test_the_raws_own_piece_under_200_mb_stays_out_too_when_the_edit_plays_the_enhanced_picture(tmp_path):
    f = _project(tmp_path)
    media = f / "edit" / "extras" / "media"
    _small(media / "raw.mov", 5000)                       # (199 MB in the real case: 1 MB under the old limit)
    _small(media / "raw_ae.mp4", 5000)
    _small(media / "competitor_ref.mp4", 5000)             # the competitor to compare with: it goes in
    _small(f / "comp.mp4", 5000)
    put, left = Z.members(f, f / "edit")
    names = {Path(a).name for _x, a in put}
    assert {"raw_enhanced.mp4", "competitor_ref.mp4", "comp.mp4", "raw_stereo.wav"} <= names
    assert "raw.mov" not in names and "raw_ae.mp4" not in names
    assert sum("normal picture" in why for _x, why in left) == 2


def test_an_edit_on_the_normal_raw_keeps_its_piece_in_the_zip(tmp_path):
    f = _project(tmp_path, state="sent")                   # no Topaz picture (yet): the piece is what it plays
    _small(f / "edit" / "extras" / "media" / "raw.mov", 5000)
    put, _left = Z.members(f, f / "edit")
    assert "raw.mov" in {Path(a).name for _x, a in put}


def _pictures(f: Path, each: int = 4000) -> list[Path]:
    dbg = f / "edit" / "extras" / "debug"
    out = [_small(dbg / "verify_failures" / f"k{i:05d}.png", each) for i in range(3)]
    out += [_small(dbg / "cuts" / f"cut_{i:02d}.png", each) for i in range(2)]
    _small(dbg / "layout.png", 300)                        # one small picture of the layout: it stays
    _small(dbg / "decisions.jsonl", 300)
    return out


def test_the_checks_pictures_stay_out_only_when_the_zip_would_be_over_the_limit(tmp_path):
    f = _project(tmp_path)
    pics = _pictures(f)
    put, _left = Z.members(f, f / "edit")
    same, out = Z.without_check_pictures(put, limit=10 ** 12)          # it fits: everything goes in, as before
    assert same == put and out == []
    less, out = Z.without_check_pictures(put, limit=1000)              # over the limit: the pictures stay out
    assert sorted(x for x, _a in out) == sorted(pics)
    names = {a.split("/", 1)[1] for _x, a in less}
    assert "edit/extras/debug/layout.png" in names and "edit/extras/debug/decisions.jsonl" in names
    assert not any("verify_failures" in n or "/cuts/" in n for n in names)


@pytest.fixture
def real(tmp_path, monkeypatch):
    """A project with a real enhanced video (20 s, about 1.5 MB), the checks' pictures, the RAW's piece."""
    from match_cuts.common import ffmpeg_bin
    monkeypatch.setenv("MOVIERECAPS_ZIP_ANY_DRIVE", "1")
    monkeypatch.setattr(Z, "MIN_FREE", 0)
    f = tmp_path / "2026-11-18_Zip test"
    media = f / "edit" / "extras" / "media"
    media.mkdir(parents=True)
    enh = media / "raw_enhanced.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:d=20",
                    "-f", "lavfi", "-i", "sine=duration=20", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "6M",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(enh)], check=True)
    wav = media / "raw_stereo.wav"
    wav.write_bytes(b"w" * 1000)
    (f / "edit" / "1_edit.xml").write_text(
        "<xmeml><sequence>" + "".join(f"<file><pathurl>{p.as_uri()}</pathurl></file>" for p in (enh, wav))
        + "</sequence></xmeml>", encoding="utf-8")
    (f / "project.json").write_text(json.dumps({"enhance": {"state": "used"}, "steps": {"edit": {
        "state": "done", "result": "PASS", "run_dir": str(f / "edit"), "finished": "2026-11-18T08:00:00+02:00"}}}),
        encoding="utf-8")
    _small(media / "raw.mov", 300_000)
    pics = _pictures(f, each=2_000_000)                                 # 10 MB of pictures
    return f, enh, pics


def test_a_zip_over_the_limit_drops_the_pictures_before_it_touches_the_picture(real, monkeypatch):
    f, enh, pics = real
    size = enh.stat().st_size
    monkeypatch.setattr(Z, "FIT_BYTES", size + 2_000_000)               # the video fits -- without the pictures
    monkeypatch.setattr(Z, "FIT_MARGIN", 1000)
    encoded = []
    monkeypatch.setattr(Z, "_encode", lambda *a, **k: encoded.append(a) or False)
    z = Z.make(f, f / "edit")
    assert z is not None and encoded == []                              # nothing was made lighter
    with zipfile.ZipFile(z) as zf:
        names = [n.split("/", 1)[1] for n in zf.namelist()]
        assert zf.getinfo(f"{Z.plain_name(f)}/edit/extras/media/raw_enhanced.mp4").file_size == size   # as Topaz made it
        how = zf.read(f"{Z.plain_name(f)}/{Z.HOW_TO}").decode("utf-8")
    assert not any("verify_failures" in n or "/cuts/" in n for n in names) and "edit/extras/media/raw.mov" not in names
    assert "a check picture" in how and "k00000.png" in how              # HOW TO OPEN says what was left out
    assert z.stat().st_size <= Z.FIT_BYTES
    assert A.check_zip(f, z, f / "edit") == ""                           # the folder may become this zip
    assert not Z.needs_zip(f, f / "edit")


def test_a_zip_made_again_takes_its_lighter_copy_from_the_older_zip(real, monkeypatch):
    f, enh, _pics = real
    size = enh.stat().st_size
    monkeypatch.setattr(Z, "FIT_BYTES", int(size * 0.6))                # the video must be made lighter
    monkeypatch.setattr(Z, "FIT_MARGIN", 1000)
    real_encode = Z._encode                                             # noqa: SLF001
    calls = []

    def encode(src, dst, vbps):                                         # (x264 here: no graphics card in a test)
        from match_cuts.common import ffmpeg_bin
        calls.append(Path(src).name)
        r = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a?",
                            "-c:v", "libx264", "-preset", "ultrafast", "-b:v", str(int(vbps)), "-maxrate",
                            str(int(vbps)), "-bufsize", str(int(vbps)), "-c:a", "copy", "-f", "mp4", str(dst)])
        return r.returncode == 0
    monkeypatch.setattr(Z, "_encode", encode)
    z = Z.make(f, f / "edit")
    assert z is not None and calls == ["raw_enhanced.mp4"]
    arc = f"{Z.plain_name(f)}/edit/extras/media/raw_enhanced.mp4"
    first = Z.zip_info(z)["files"][arc]
    assert first["from_bytes"] == size and first["bytes"] < size and "reused" not in Z.zip_info(z)
    with zipfile.ZipFile(z) as zf:
        light = zf.read(arc)
    # the clean-up round changes project.json (its "slim" note): the folder's check says so, the zip is made again
    d = json.loads((f / "project.json").read_text(encoding="utf-8"))
    d["slim"] = {"at": "2026-11-18T08:30:00+02:00", "lines": ["the RAW stays whole"]}
    (f / "project.json").write_text(json.dumps(d), encoding="utf-8")
    assert "project.json changed after the zip was made" in A.check_zip(f, z, f / "edit")
    z2 = Z.make(f, f / "edit")
    assert z2 == z and calls == ["raw_enhanced.mp4"]                    # NOT encoded a second time
    assert Z.zip_info(z)["reused"] == [arc]
    with zipfile.ZipFile(z) as zf:
        assert zf.read(arc) == light                                    # the very same lighter copy
        assert json.loads(zf.read(f"{Z.plain_name(f)}/project.json"))["slim"]      # with the new project.json
    assert A.check_zip(f, z, f / "edit") == ""
    assert not list(f.parent.glob("*.light*.tmp"))
    # ... but a NEW enhanced picture (another size) is encoded again
    with open(enh, "ab") as fh:
        fh.write(b"\0" * 4096)
    Z.make(f, f / "edit")
    assert calls == ["raw_enhanced.mp4", "raw_enhanced.mp4"]
    assert real_encode is not None
