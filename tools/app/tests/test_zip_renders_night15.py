"""Night 15, task 7: every finished project gets a zip to send (core.share_zip), and Media Encoder's renders go into
renders\\<day>\\ folders (core.renders) -- moved, never deleted, with a log to put them back."""
from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path

from mr_app.core import renders, share_zip

LOG = """
10/22/2026 04:30:06 AM : Queue Started


 - Source File: C:\\Users\\me\\AppData\\Local\\Temp\\debby_POPW.prproj
 - Output File: {a}
 - Preset Used: INFIQ
 - Encoding Time: 00:01:11
10/22/2026 04:31:18 AM : File Successfully Encoded


10/21/2026 11:02:00 PM : Queue Started
 - Output File: {b}
10/21/2026 11:03:10 PM : File Successfully Encoded

 - Output File: {c}
10/21/2026 11:05:00 PM : Encoding Failed
"""


def _old(p: Path, data: bytes = b"video") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    t = time.time() - 3600
    os.utime(p, (t, t))
    return p


def test_renders_move_into_the_folder_of_their_day_and_can_be_put_back(tmp_path):
    a, b, c = _old(tmp_path / "xasd.mp4"), _old(tmp_path / "docs" / "660.mp4"), _old(tmp_path / "failed.mp4")
    clash = _old(tmp_path / "renders" / "2026-10-22" / "xasd.mp4", b"another")
    log = tmp_path / "AMEEncodingLog.txt"
    log.write_text(LOG.format(a=a, b=b, c=c), encoding="utf-8")
    found = renders.finished_renders(log.read_text(encoding="utf-8"))
    assert [p for p, _w in found] == [a, b]                        # the failed one is not a render
    moved = renders.sort_renders([log])
    assert sorted(d for _s, d in moved) == sorted([tmp_path / "renders" / "2026-10-22" / "xasd-2.mp4",
                                                   tmp_path / "docs" / "renders" / "2026-10-21" / "660.mp4"])
    assert not a.exists() and clash.read_bytes() == b"another" and c.exists()        # nothing overwritten or lost
    assert renders.sort_renders([log]) == []                       # done once: the next sweep has nothing to do
    assert renders.put_back(tmp_path / "renders" / renders.MOVED_LOG) == 1 and a.read_bytes() == b"video"


def test_a_render_still_being_written_waits(tmp_path):
    a = tmp_path / "new.mp4"
    a.write_bytes(b"x")                                             # written now
    log = tmp_path / "AMEEncodingLog.txt"
    log.write_text(LOG.format(a=a, b=tmp_path / "gone.mp4", c=tmp_path / "c.mp4"), encoding="utf-8")
    assert renders.plan([log]) == [] and a.exists()


def _project(tmp_path: Path, big_on_share: bool = True) -> tuple[Path, Path]:
    proj = tmp_path / "2026-10-22_The Funniest Dad"
    run = proj / "edit"
    media = run / "extras" / "media"
    frame = _old(media / "frame.png", b"png")
    raw = _old(media / "raw.mp4", b"r" * 4096)
    (run / "1_edit.xml").write_text(
        "<xmeml><sequence>"
        f"<file><pathurl>{frame.as_uri()}</pathurl></file>"
        f"<file><pathurl>{raw.as_uri()}</pathurl></file>"
        f"<file><pathurl>{frame.as_uri()}</pathurl></file>"
        "<file><pathurl>file:///Z:/nowhere/song.mp3</pathurl></file>"
        "</sequence></xmeml>", encoding="utf-8")
    (run / "2_captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    (run / "video_edit.prproj").write_bytes(b"prproj")
    (proj / "title.txt").write_text("The Funniest Dad", encoding="utf-8")
    (proj / "project.json").write_text(json.dumps({"steps": {"edit": {"state": "done", "run_dir": str(run)}}}),
                                       encoding="utf-8")
    return proj, run


def test_a_finished_project_zip_has_the_whole_folder_and_how_to_open_it(tmp_path, monkeypatch):
    """Day 16 (task 2) replaced night 15's "only the edit files": the whole folder (test_zip_day16.py has the rest)."""
    monkeypatch.setattr(share_zip, "BIG_BYTES", 1000)
    monkeypatch.setattr(share_zip, "on_share", lambda p: True)
    monkeypatch.setattr(share_zip, "MIN_FREE", 0)                  # (not this PC's free space)
    proj, run = _project(tmp_path)
    z = share_zip.make(proj, run)
    assert z == proj / f"{proj.name}.zip"
    names = zipfile.ZipFile(z).namelist()
    top = proj.name
    assert set(names) == {f"{top}/edit/1_edit.xml", f"{top}/edit/2_captions.srt", f"{top}/edit/video_edit.prproj",
                          f"{top}/title.txt", f"{top}/project.json", f"{top}/edit/extras/media/frame.png",
                          f"{top}/edit/extras/media/raw.mp4", f"{top}/{share_zip.HOW_TO}"}
    how = zipfile.ZipFile(z).read(f"{top}/{share_zip.HOW_TO}").decode("utf-8")
    assert "song.mp3: not found where the edit points" in how and r"pick edit\1_edit.xml" in how
    assert not share_zip.needs_zip(proj, run)
    t = time.time() - 7200                                         # (the zip made before ...)
    os.utime(z, (t, t))                                            # ... Premiere saved the project: made again
    assert share_zip.needs_zip(proj, run)
    assert share_zip.sweep(tmp_path) == [z] and not share_zip.needs_zip(proj, run)
    assert share_zip.sweep(tmp_path) == []
    assert (run / "extras" / "media" / "raw.mp4").is_file()         # nothing moved or deleted


def test_no_finished_edit_no_zip(tmp_path):
    proj = tmp_path / "p"
    (proj / "edit").mkdir(parents=True)
    assert share_zip.make(proj) is None and not (proj / "p.zip").exists()


def test_the_pcs_own_share_path_is_read_from_its_disk(monkeypatch, tmp_path):
    f = _old(tmp_path / "Shared" / "x.png")
    from mr_app.core import learn_hub
    monkeypatch.setattr(learn_hub, "for_pc", lambda p: str(f) if str(p).startswith("\\\\") else str(p))
    assert share_zip.reach(Path(r"\\192.168.101.11\Shared\x.png")) == f
    assert share_zip._path_of_url("file://192.168.101.11/Shared/a%20b.mp4") == Path(r"\\192.168.101.11\Shared\a b.mp4")  # noqa: SLF001
    assert share_zip._path_of_url("file:///D:/x/y.mp4") == Path("D:/x/y.mp4")                                       # noqa: SLF001
