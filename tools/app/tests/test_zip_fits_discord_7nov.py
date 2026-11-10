"""7 Nov (your call: "I grab the zip and put it into Discord" -- Nitro: 1 GB): a zip that would be over FIT_BYTES
gets the biggest video (the Topaz picture) as a lighter copy -- the same length and sound, its bitrate lower -- and
stays under; the project folder keeps the full file; the zip says so (HOW TO OPEN, .zipinfo.json); an older zip over
the limit is made again once; the check before a folder becomes its zip takes the lighter copy as its file."""
from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from mr_app.core import archive as A
from mr_app.core import share_zip as Z


def _video(path: Path, seconds: float = 4.0) -> Path:
    from match_cuts.common import ffmpeg_bin
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=60:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-preset",
                    "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True)
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_ZIP_ANY_DRIVE", "1")
    monkeypatch.setattr(Z, "MIN_FREE", 0)
    f = tmp_path / "Projects" / "2026-11-07_Work Seminar (2)"
    media = f / "edit" / "extras" / "media"
    enh = _video(media / "raw_enhanced.mp4")
    wav = media / "raw_stereo.wav"
    wav.write_bytes(b"RIFF" + b"\0" * 1000)
    (f / "edit" / "1_edit.xml").write_text(
        "<xmeml><sequence>" + "".join(f"<file><pathurl>{p.as_uri()}</pathurl></file>" for p in (enh, wav))
        + "</sequence></xmeml>", encoding="utf-8")
    (f / "project.json").write_text(json.dumps({"enhance": {"state": "used"}}), encoding="utf-8")
    return f


def test_a_small_zip_is_left_as_it_is(project):
    put, _left = Z.members(project, project / "edit")
    new, note = Z.fit(put, project.parent / "x.zip", limit=10 ** 10)
    assert new == put and note == {}


def test_a_big_zip_gets_a_lighter_copy_of_the_picture_and_fits(project, monkeypatch):
    enh = project / "edit" / "extras" / "media" / "raw_enhanced.mp4"
    full = enh.stat().st_size
    limit = full // 2
    monkeypatch.setattr(Z, "FIT_MARGIN", 50_000)
    monkeypatch.setattr(Z, "FIT_BYTES", limit)
    z = Z.make(project, project / "edit")
    assert z is not None and z.stat().st_size <= limit
    assert enh.stat().st_size == full                                    # the project keeps the full one
    assert not list(project.parent.glob("*.light*.tmp"))                  # the lighter copy only lives in the zip
    note = Z.zip_info(z)
    arc = f"{Z.plain_name(project)}/edit/extras/media/raw_enhanced.mp4"
    assert note["tried"] and note["files"][arc]["from_bytes"] == full
    with zipfile.ZipFile(z) as zf:
        how = zf.read(f"{Z.plain_name(project)}/{Z.HOW_TO}").decode()
        assert "LIGHTER COPY" in how
        zf.extract(arc, project.parent / "out")
    from mr_app.core.share_zip import _probe
    a, b = _probe(enh), _probe(project.parent / "out" / arc)
    assert abs(a["dur"] - b["dur"]) < 0.1                                # the same length
    assert not Z.needs_zip(project, project / "edit")                    # not made again and again
    assert A.check_zip(project, z, project / "edit") == ""               # the lighter copy counts as its file


def test_an_older_zip_over_the_limit_is_made_again_once(project, monkeypatch):
    z = Z.make(project, project / "edit")                                # made before (no limit then)
    monkeypatch.setattr(Z, "FIT_BYTES", z.stat().st_size - 1)
    assert Z.needs_zip(project, project / "edit")


def test_the_switch_keeps_the_full_picture(project, monkeypatch):
    monkeypatch.setenv(Z.FIT_ENV, "1")
    put, _left = Z.members(project, project / "edit")
    assert Z.fit(put, project.parent / "x.zip", limit=1) == (put, {})


def test_two_big_videos_both_get_lighter_and_the_zip_fits(project, monkeypatch):
    """The Bathroom Break zip (2.9 GB): 8 clips still played the whole episode (1 GB) next to the Topaz picture --
    both go in lighter, at one bitrate."""
    media = project / "edit" / "extras" / "media"
    raw = _video(media / "raw.mp4", 6.0)
    xml = project / "edit" / "1_edit.xml"
    xml.write_text(xml.read_text(encoding="utf-8").replace(
        "</sequence>", f"<file><pathurl>{raw.as_uri()}</pathurl></file></sequence>"), encoding="utf-8")
    enh = media / "raw_enhanced.mp4"
    both = enh.stat().st_size + raw.stat().st_size
    limit = int(both * 0.45)
    monkeypatch.setattr(Z, "FIT_BYTES", limit)
    monkeypatch.setattr(Z, "FIT_MARGIN", 50_000)
    z = Z.make(project, project / "edit")
    assert z is not None and z.stat().st_size <= limit
    note = Z.zip_info(z)
    assert len(note["files"]) == 2 and note["v"] == Z.FIT_VERSION
    assert not Z.needs_zip(project, project / "edit")


def test_two_writers_never_share_a_zip_and_a_damaged_zip_is_not_kept(project, monkeypatch):
    """10 Nov (Suspicious Friend: its picture damaged inside the zip -- two computers wrote it at once): a second
    writer finds the zip's lock and leaves it; a zip that does not read back is not put in place."""
    z = Z.zip_path(project)
    lk = z.with_name(z.name + ".lock")
    lk.write_text("{}")
    assert Z.make(project, project / "edit") is None and not z.exists()       # someone else is writing it
    lk.unlink()
    real = zipfile.ZipFile.testzip
    monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self: "x/raw_enhanced.mp4")
    assert Z.make(project, project / "edit") is None and not z.exists()       # damaged: not kept
    assert not list(z.parent.glob("*.tmp")) and not lk.exists()
    monkeypatch.setattr(zipfile.ZipFile, "testzip", real)
    assert Z.make(project, project / "edit") == z and not lk.exists()
