"""Night 10, item 1: the free-space warning before a run (core.space). A run folder on the RAW's own drive links its
media (no extra space); on another drive it copies them, so that drive needs room for the RAW and the competitor."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import space  # noqa: E402


def _video(p: Path, n: int) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\0" * n)
    return p


def test_same_drive_needs_only_the_run_files(tmp_path):
    comp, raw = _video(tmp_path / "in" / "c.mp4", 1000), _video(tmp_path / "in" / "raw.mp4", 5000)
    n, why = space.need_bytes(comp, raw, tmp_path / "out" / "video1")
    assert n == space.RUN_FILES + space.RESERVE and why == "the run's own files"
    assert space.same_drive(raw, tmp_path / "out" / "video1" / "extras")       # a folder that does not exist yet


def test_another_drive_needs_the_copies_and_a_full_drive_is_named(tmp_path, monkeypatch):
    comp, raw = _video(tmp_path / "in" / "c.mp4", 1000), _video(tmp_path / "in" / "raw.mp4", 5 * space.GB // 1000)
    monkeypatch.setattr(space, "same_drive", lambda a, b: False)
    monkeypatch.setattr(space, "_cache_dir", lambda w=None: tmp_path / "work")   # day 17: this PC's cache is on D:
    n, why = space.need_bytes(comp, raw, tmp_path / "out" / "video1")
    assert n == space.RUN_FILES + space.RESERVE + 1000 + raw.stat().st_size
    assert "a copy of the RAW" in why and "another drive" in why
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(space.shutil, "disk_usage", lambda p: usage._replace(free=space.GB))
    probs = space.problems([("video1", comp, raw, tmp_path / "out" / "video1"),
                            ("video2", comp, raw, tmp_path / "out" / "video2")])
    assert len(probs) == 1 and probs[0].startswith("not enough free space on ")
    assert "video1" in probs[0] and "video2" in probs[0] and "only 1.0 GB is free" in probs[0]
    monkeypatch.setattr(space.shutil, "disk_usage", lambda p: usage._replace(free=100 * space.GB))
    assert space.problems([("video1", comp, raw, tmp_path / "out" / "video1")]) == []


def test_an_input_the_tool_converts_counts_twice(tmp_path):
    comp, raw = _video(tmp_path / "c.mp4", 10), _video(tmp_path / "raw.webm", 1000)
    n, why = space.need_bytes(comp, raw, tmp_path / "out")
    assert n == space.RUN_FILES + space.RESERVE + 2000 and "converted copy" in why
