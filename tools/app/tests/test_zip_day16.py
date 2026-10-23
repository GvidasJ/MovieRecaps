"""Day 16, task 2: the zip holds the WHOLE project folder (its own folders inside), videos stored as they are, never a
zip in it; a zip of night 15's kind is made again; the board card shows its size.

Run: python -m pytest tools/app/tests/test_zip_day16.py"""
from __future__ import annotations

import json
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import share_zip  # noqa: E402


@pytest.fixture(autouse=True)
def any_free_space(monkeypatch):
    """The tests never depend on this PC's free space (the 40 GB rule has its own test)."""
    monkeypatch.setattr(share_zip, "MIN_FREE", 0)


def _old(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    t = time.time() - 3600
    os.utime(p, (t, t))
    return p


def _project(tmp_path: Path) -> tuple[Path, Path]:
    proj = tmp_path / "2026-10-23_The Coldest Dad"
    run = proj / "edit"
    media = run / "extras" / "media"
    frame = _old(media / "frame.png", b"png")
    raw = _old(media / "raw.mp4", b"r" * 50_000)
    song = _old(tmp_path / "assets" / "song.mp3", b"s" * 300)            # outside the project, small
    (run / "1_edit.xml").write_text(
        "<xmeml><sequence>"
        f"<file><pathurl>{frame.as_uri()}</pathurl></file>"
        f"<file><pathurl>{raw.as_uri()}</pathurl></file>"
        f"<file><pathurl>{song.as_uri()}</pathurl></file>"
        "</sequence></xmeml>", encoding="utf-8")
    _old(run / "2_captions.srt", b"1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    _old(run / "extras" / "report.md", b"# report\n" * 200)
    _old(run / "extras" / "debug" / "decisions.jsonl", b"{}\n" * 500)
    _old(proj / "logs" / "download_raw.log", b"ok\n")
    _old(proj / "competitor [abc].mp4", b"c" * 20_000)
    _old(proj / "template.png", b"tpl")
    _old(proj / "title.txt", b"The Coldest Dad")
    _old(proj / "titles_found.txt", b"post: x")
    _old(proj / "older.zip", b"PK old")                                  # never in it
    _old(proj / "x.7z", b"7z")
    _old(proj / "half.mp4.part", b"h")
    _old(proj / ".movierecaps.lock", b"{}")
    (proj / "project.json").write_text(json.dumps({"steps": {"edit": {"state": "done", "run_dir": str(run)}}}),
                                       encoding="utf-8")
    return proj, run


def test_the_zip_has_every_file_with_its_folders_and_stores_the_videos(tmp_path):
    proj, run = _project(tmp_path)
    z = share_zip.make(proj, run)
    top = proj.name
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        assert names == {f"{top}/{x}" for x in (
            "edit/1_edit.xml", "edit/2_captions.srt", "edit/extras/report.md", "edit/extras/debug/decisions.jsonl",
            "edit/extras/media/frame.png", "edit/extras/media/raw.mp4", "logs/download_raw.log",
            "competitor [abc].mp4", "template.png", "title.txt", "titles_found.txt", "project.json",
            "outside/song.mp3", share_zip.HOW_TO)}
        info = {i.filename: i for i in zf.infolist()}
        for v in ("edit/extras/media/raw.mp4", "competitor [abc].mp4", "edit/extras/media/frame.png",
                  "outside/song.mp3"):
            assert info[f"{top}/{v}"].compress_type == zipfile.ZIP_STORED        # as it is: fast, lossless
        assert info[f"{top}/edit/extras/report.md"].compress_type == zipfile.ZIP_DEFLATED
        assert zf.read(f"{top}/edit/extras/media/raw.mp4") == b"r" * 50_000
        assert zf.comment == share_zip.MARK
    assert not any(n.endswith((".zip", ".7z", ".part", ".lock")) for n in names)
    assert not (proj / f"{proj.name}.zip.tmp").exists() and (proj / "older.zip").is_file()


def test_the_zip_never_holds_itself_and_project_json_alone_does_not_remake_it(tmp_path):
    proj, run = _project(tmp_path)
    z = share_zip.make(proj, run)
    z2 = share_zip.make(proj, run)                                      # made again: the first zip is not in it
    assert z2 == z and not any(n.endswith(".zip") for n in zipfile.ZipFile(z).namelist())
    assert not share_zip.needs_zip(proj, run)
    t = time.time() + 5
    os.utime(proj / "project.json", (t, t))                             # the board moved the card
    assert not share_zip.needs_zip(proj, run)
    os.utime(run / "2_captions.srt", (t, t))                            # a real change
    assert share_zip.needs_zip(proj, run)


def test_a_zip_of_night_15s_kind_is_made_again(tmp_path):
    proj, run = _project(tmp_path)
    old = share_zip.zip_path(proj)
    with zipfile.ZipFile(old, "w") as zf:                               # night 15: only the edit files, no mark
        zf.writestr(f"{proj.name}/1_edit.xml", "x")
    t = time.time() + 60
    os.utime(old, (t, t))                                               # newer than every file
    assert share_zip.needs_zip(proj, run)
    assert share_zip.sweep(tmp_path) == [old]
    assert zipfile.ZipFile(old).comment == share_zip.MARK and not share_zip.needs_zip(proj, run)


def test_a_hard_link_goes_in_once_as_the_copy_the_edit_plays(tmp_path):
    proj, run = _project(tmp_path)
    raw = run / "extras" / "media" / "raw.mp4"
    try:
        os.link(raw, proj / "The.Movie.2005.mp4")                       # the project's RAW and the run's media
    except OSError:
        pytest.skip("no hard links here")
    put, left = share_zip.members(proj, run)
    arcs = [a for _f, a in put]
    assert f"{proj.name}/edit/extras/media/raw.mp4" in arcs and f"{proj.name}/The.Movie.2005.mp4" not in arcs
    assert any(f.name == "The.Movie.2005.mp4" and "same file as edit/extras/media/raw.mp4" in why for f, why in left)


def test_a_big_file_from_outside_is_left_out_and_said(tmp_path, monkeypatch):
    monkeypatch.setattr(share_zip, "BIG_BYTES", 100)
    monkeypatch.setattr(share_zip, "on_share", lambda p: True)
    proj, run = _project(tmp_path)
    put, left = share_zip.members(proj, run)
    assert any(f.name == "song.mp3" and "on the shared drive" in why for f, why in left)
    assert f"{proj.name}/edit/extras/media/raw.mp4" in [a for _f, a in put]     # inside: always in, any size


def test_no_zip_when_the_drive_would_be_too_full(tmp_path, monkeypatch):
    proj, run = _project(tmp_path)
    monkeypatch.setattr(share_zip, "MIN_FREE", 10 ** 18)
    assert share_zip.make(proj, run) is None and not share_zip.zip_path(proj).exists()


def test_the_size_text_and_the_card(tmp_path):
    assert share_zip.size_text(None) == "" and share_zip.size_text(3 * 1024 ** 3) == "3.0 GB"
    assert share_zip.size_text(250 * 1024 ** 2) == "250 MB" and share_zip.size_text(400 * 1024) == "0.4 MB"
    proj, run = _project(tmp_path)
    z = share_zip.make(proj, run)
    from mr_app.core import board
    card = board.card_of(proj)
    assert card.zip_bytes == z.stat().st_size
