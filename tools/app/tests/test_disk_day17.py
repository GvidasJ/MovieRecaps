"""Day 17, task 1: C: no longer fills up -- the project zip is made only off Windows' own drive (never in
input\\downloads on C:), and the tool's cache lives on D: (work\\cache a junction to D:\\mr_cache; the free-space
check counts it there).

Run: python -m pytest tools/app/tests/test_disk_day17.py"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import cache_move, share_zip, space  # noqa: E402


def _finished(proj: Path) -> Path:
    run = proj / "edit"
    run.mkdir(parents=True)
    (run / "1_edit.xml").write_text("<xmeml/>", encoding="utf-8")
    (proj / "project.json").write_text(json.dumps({"steps": {"edit": {"state": "done", "run_dir": str(run)}}}),
                                       encoding="utf-8")
    return run


def test_no_zip_on_the_windows_drive(tmp_path, monkeypatch):
    monkeypatch.delenv("MOVIERECAPS_ZIP_ANY_DRIVE", raising=False)
    monkeypatch.setattr(share_zip, "MIN_FREE", 0)
    monkeypatch.setenv("SystemDrive", "C:")
    assert not share_zip.drive_ok(Path("C:/Users/gvida/Desktop/bh/MovieRecaps/input/downloads/2026-10-24_x"))
    assert share_zip.drive_ok(Path("D:/Shared/Projects/2026-10-24_x"))
    assert share_zip.drive_ok(Path("//192.168.101.11/Shared/Projects/2026-10-24_x"))
    assert share_zip.drive_ok(Path("E:/Projects/x"))
    proj = tmp_path / "2026-10-24_The Worst First Word"
    run = _finished(proj)
    monkeypatch.setenv("SystemDrive", os.path.splitdrive(str(tmp_path))[0])     # the test folder's drive is "C:"
    assert share_zip.make(proj, run) is None and not share_zip.zip_path(proj).exists()
    assert not share_zip.needs_zip(proj, run)
    assert share_zip.sweep(tmp_path) == []                                      # the 10-minute sweep makes none either
    monkeypatch.setenv("SystemDrive", "Q:")                                     # the same folder "on another drive"
    assert share_zip.make(proj, run) == share_zip.zip_path(proj)


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows'")
def test_cache_move_keeps_every_file_and_leaves_a_junction(tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "cache" / "proxy").mkdir(parents=True)
    (work / "cache" / "proxy" / "a.npy").write_bytes(b"p" * 1000)
    (work / "cache" / "raw_index" / "deep").mkdir(parents=True)
    (work / "cache" / "raw_index" / "deep" / "b.npz").write_bytes(b"r" * 5000)
    dst = tmp_path / "d_drive" / "mr_cache"
    from mr_app.core import paths
    monkeypatch.setattr(paths, "work_dir", lambda: work)
    monkeypatch.setenv("MOVIERECAPS_CACHE_DIR", str(dst))
    monkeypatch.delenv("MOVIERECAPS_NO_CACHE_MOVE", raising=False)
    monkeypatch.setattr(cache_move, "busy", lambda: [])
    lines: list[str] = []
    assert cache_move.move(log=lines.append), lines
    assert cache_move.is_junction(work / "cache")
    assert (dst / "proxy" / "a.npy").read_bytes() == b"p" * 1000
    assert (work / "cache" / "raw_index" / "deep" / "b.npz").read_bytes() == b"r" * 5000     # found through it
    assert os.path.samefile(cache_move.real_dir(), dst)
    assert cache_move.move(log=lines.append)                                    # a second time: nothing to do
    assert "points there" in cache_move.state()
    # the free-space check counts the cache on its real folder (D: on the PC), not on work's drive
    assert os.path.samefile(space._cache_dir(work), dst)


def test_cache_move_waits_while_movierecaps_runs(tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "cache").mkdir(parents=True)
    (work / "cache" / "x.json").write_text("{}")
    from mr_app.core import paths
    monkeypatch.setattr(paths, "work_dir", lambda: work)
    monkeypatch.setenv("MOVIERECAPS_CACHE_DIR", str(tmp_path / "dst"))
    monkeypatch.delenv("MOVIERECAPS_NO_CACHE_MOVE", raising=False)
    monkeypatch.setattr(cache_move, "busy", lambda: ["123 python -m match_cuts --competitor ..."])
    lines: list[str] = []
    assert not cache_move.move(log=lines.append)
    assert "MovieRecaps is running" in lines[0] and (work / "cache" / "x.json").is_file()
    assert not cache_move.is_junction(work / "cache")


def test_no_d_drive_keeps_work_cache(monkeypatch):
    monkeypatch.delenv("MOVIERECAPS_CACHE_DIR", raising=False)
    monkeypatch.setattr(cache_move, "_fixed", lambda d: False)
    assert cache_move.target() is None
    monkeypatch.setattr(cache_move, "_fixed", lambda d: True)
    assert cache_move.target() == Path("D:\\mr_cache")
