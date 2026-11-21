"""21 Nov (your job 2; 20 Nov, the laptop: Topaz stopped with "\\\\192.168.101.11\\Shared\\Enhance\\..._TOENHANCE.mp4:
No such file or directory" while the file was there): a short network drop is not a failed Topaz try. The laptop keeps
what Topaz made, waits until the Enhance folder answers and goes on the same way; no failure file (so the PC sends no
new piece), no try counted, the parts not marked failed. Away too long: the piece is let go ("waiting") and taken
again later. A real failure stays a failure."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import enhance as E  # noqa: E402

NAME = "2026-11-21_drop_all"


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(E, "SHARE_LOOK_S", 0.01)
    monkeypatch.setattr(E, "check_enhanced", lambda *a, **k: {"ok": True, "why": ""})


def _piece(tmp_path: Path, ranges=None) -> E.Names:
    nm = E.names(tmp_path / "Enhance", NAME)
    nm.folder.mkdir(parents=True, exist_ok=True)
    nm.piece.write_bytes(b"piece")
    d = {"project": nm.name, "duration": 10.0, "frames": 240, "fps": "24", "width": 1920, "height": 1080}
    if ranges:
        d["ranges"] = ranges
    nm.info.write_text(json.dumps(d), encoding="utf-8")
    return nm


def _drop_text(nm: E.Names) -> str:
    return (f"Topaz stopped (code 4294967294): \\\\192.168.101.11\\Shared\\Enhance\\{nm.name}_TOENHANCE.mp4: "
            "No such file or directory")


def _run(nm: E.Names, **k) -> E.Job:
    token = E.take(nm)
    assert token
    seen: list = []
    job = E.enhance_one(nm, token, None, on_change=lambda j: seen.append(j.wait), **k)
    job.seen_waits = [w for w in seen if w]
    return job


def test_the_laptops_no_such_file_while_the_file_is_there_goes_on_the_same_way(tmp_path, monkeypatch):
    nm = _piece(tmp_path)
    calls: list = []

    def topaz(src, dst, *a, **k):
        calls.append(Path(dst).name)
        if len(calls) == 1:
            raise E.EnhanceError(_drop_text(nm))
        Path(dst).write_bytes(b"enhanced")
    monkeypatch.setattr(E, "run_topaz", topaz)
    job = _run(nm)
    assert job.state == "done", job.why
    assert len(calls) == 2 and calls[0] == calls[1] == nm.part.name              # the same way, twice
    assert not nm.failed.exists() and nm.enhanced.is_file() and not nm.lock.exists()
    info = E.read_info(nm.info)
    assert len(info["share_drops"]) == 1 and "No such file" in info["share_drops"][0]["why"]
    assert "parts_failed" not in info and job.seen_waits and E.SHARE_TEXT in job.seen_waits[0]


def test_the_folder_away_for_a_while_is_waited_for(tmp_path, monkeypatch):
    nm = _piece(tmp_path)
    away = {"n": 0}
    real_readable = E._readable

    def readable(path):
        if Path(path) == nm.piece and away["n"] < 4:
            away["n"] += 1
            return False
        return real_readable(path)
    calls: list = []

    def topaz(src, dst, *a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise E.EnhanceError("Topaz stopped (code 1): Error opening input files: Invalid argument")
        Path(dst).write_bytes(b"enhanced")
    monkeypatch.setattr(E, "_readable", readable)
    monkeypatch.setattr(E, "run_topaz", topaz)
    job = _run(nm)
    assert job.state == "done" and len(calls) == 2 and away["n"] == 4 and not nm.failed.exists()


def test_a_drop_in_the_parts_goes_on_with_the_parts_not_the_whole_piece(tmp_path, monkeypatch):
    nm = _piece(tmp_path, ranges=[[0, 60], [180, 240]])
    calls: list = []

    def parts(nm_, info, tz, prog, cancel, scale):
        calls.append("parts")
        if len(calls) == 1:
            raise E.ShareDrop(f"{E.SHARE_TEXT} (part 2's frames)")
        nm_.part.write_bytes(b"parts")

    def whole(*a, **k):
        calls.append("whole")
    monkeypatch.setattr(E, "enhance_parts", parts)
    monkeypatch.setattr(E, "run_topaz", whole)
    job = _run(nm)
    assert job.state == "done" and calls == ["parts", "parts"] and not job.note
    assert "parts_failed" not in E.read_info(nm.info) and not nm.failed.exists()


def test_the_parts_keep_what_was_made_when_the_folder_drops(tmp_path, monkeypatch):
    """enhance_parts itself: a part that cannot be read because the folder is away is a drop, and the parts made so
    far are not deleted (as after a stop)."""
    nm = _piece(tmp_path, ranges=[[0, 60], [180, 240]])
    monkeypatch.setattr(E, "_seek_lands_right", lambda *a, **k: "")
    monkeypatch.setattr(E, "_frame_times", lambda p: [])
    made: list = []

    def topaz(src, dst, n, *a, **k):
        Path(dst).write_bytes(b"x")
        made.append(Path(dst))
    monkeypatch.setattr(E, "run_topaz", topaz)
    monkeypatch.setattr(E, "_frames_of", lambda p: 0)                        # nothing can be read ...
    real_readable = E._readable
    monkeypatch.setattr(E, "_readable", lambda p: False if Path(p) == nm.piece else real_readable(p))   # (the share)
    with pytest.raises(E.ShareDrop):
        E.enhance_parts(nm, E.read_info(nm.info), E.Topaz(Path("ffmpeg"), Path("."), Path("."), fake=True), None,
                        None, 2)
    assert made and all(f.is_file() for f in made)                            # part 1's file stays


def test_a_real_topaz_failure_is_still_a_failure(tmp_path, monkeypatch):
    nm = _piece(tmp_path)
    calls: list = []

    def topaz(src, dst, *a, **k):
        calls.append(1)
        raise E.EnhanceError(f"Topaz stopped (code 69): {nm.piece}: Invalid data found when processing input")
    monkeypatch.setattr(E, "run_topaz", topaz)
    job = _run(nm)
    assert job.state == "failed" and len(calls) == 1 and nm.failed.is_file()
    assert "Invalid data" in nm.failed.read_text(encoding="utf-8")


def test_too_many_drops_in_one_take_are_a_failure_as_before(tmp_path, monkeypatch):
    nm = _piece(tmp_path)
    monkeypatch.setattr(E, "SHARE_DROPS", 2)
    calls: list = []

    def topaz(src, dst, *a, **k):
        calls.append(1)
        raise E.EnhanceError(_drop_text(nm))
    monkeypatch.setattr(E, "run_topaz", topaz)
    job = _run(nm)
    assert job.state == "failed" and len(calls) == 3 and nm.failed.is_file()
    t = E.note_failure(tmp_path / "proj_missing", job.why)                     # (the PC: tried the same way again)
    assert t == {} or t.get("same_way")
    assert any(w in job.why.lower() for w in E._SAME_WAY)                    # noqa: SLF001


def test_away_too_long_the_piece_is_let_go_not_failed_and_what_topaz_made_stays(tmp_path, monkeypatch):
    nm = _piece(tmp_path)
    monkeypatch.setattr(E, "SHARE_WAIT_S", 0.05)
    real_readable = E._readable
    monkeypatch.setattr(E, "_readable", lambda p: False if Path(p) == nm.piece else real_readable(p))
    resume = E._resume_file(nm.part)                                        # noqa: SLF001
    seg = E._seg_file(nm.part, 0)                                           # noqa: SLF001

    def topaz(src, dst, *a, **k):
        seg.write_bytes(b"half of it")
        resume.write_text(json.dumps({"sig": {}, "segs": [{"file": seg.name}]}), encoding="utf-8")
        raise E.EnhanceError(_drop_text(nm))
    monkeypatch.setattr(E, "run_topaz", topaz)
    job = _run(nm)
    assert job.state == "waiting" and E.SHARE_TEXT in job.why and "taken again" in job.line()
    assert not nm.failed.exists() and not nm.lock.exists()                  # nothing for the PC; free to take again
    assert seg.is_file() and resume.is_file()                               # Topaz's work kept
    assert E.waiting_pieces(nm.folder) and E.waiting_pieces(nm.folder)[0].name == nm.name


def test_a_piece_the_pc_removed_meanwhile_ends_without_a_failure_file(tmp_path, monkeypatch):
    nm = _piece(tmp_path)
    part1 = E._part_file(nm, 0)                                             # noqa: SLF001

    def topaz(src, dst, *a, **k):
        part1.write_bytes(b"x")
        nm.piece.unlink()
        nm.info.unlink()
        raise E.EnhanceError(_drop_text(nm))
    monkeypatch.setattr(E, "run_topaz", topaz)
    job = _run(nm)
    assert job.state == "failed" and job.why == E.GONE_TEXT
    assert not nm.failed.exists() and not part1.exists()


def test_what_is_a_drop():
    p = Path(r"\\192.168.101.11\Shared\Enhance") / f"{NAME}_TOENHANCE.mp4"
    assert E.share_drop(E.ShareDrop("x"), p)
    e53 = OSError(2, "The network path was not found", None, 53)
    assert getattr(e53, "winerror", 53) == 53 and E.share_drop(e53, p)
    assert not E.share_drop(E.EnhanceError("stopped"), p)
    assert not E.share_drop(E.PartsError("part 2: one frame late"), p)


def test_a_topaz_stop_keeps_its_blocks_and_so_does_a_drop(tmp_path, monkeypatch):
    """run_topaz's own clean-up: a failure for a reason drops what was kept, a drop does not."""
    nm = _piece(tmp_path)
    tz = E.Topaz(Path("ffmpeg"), Path("."), Path("."), fake=True)
    for text, kept in ((_drop_text(nm), True), ("Topaz stopped (code 69): Invalid data found", False)):
        def tries(src, dst, *a, text=text, **k):
            Path(dst).write_bytes(b"half")
            raise E.EnhanceError(text)
        monkeypatch.setattr(E, "_topaz_tries", tries)
        with pytest.raises(E.EnhanceError):
            E.run_topaz(nm.piece, nm.part, 600, tz, None, threading.Event(), 2, None, "24")
        assert E._resume_file(nm.part).is_file() is kept, text              # noqa: SLF001
        E.drop_resume(nm.part)
