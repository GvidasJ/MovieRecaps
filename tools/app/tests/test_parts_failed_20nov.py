"""20 Nov (The Worst Employee In Key and Peele): the parts of a piece failed after an hour of Topaz, the app was closed
and opened, the parts ran a second hour and failed again. Now a parts failure is written into the piece's json at
once: a piece taken again goes straight to the whole piece, and the reason can be read while Topaz still works."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import enhance as E  # noqa: E402


def _piece(tmp_path: Path) -> E.Names:
    nm = E.names(tmp_path, "2026-11-20_x_all")
    nm.piece.write_bytes(b"piece")
    nm.info.write_text(json.dumps({"project": nm.name, "duration": 10.0, "frames": 240, "fps": "24",
                                   "width": 1920, "height": 1080, "ranges": [[0, 60], [180, 240]]}),
                       encoding="utf-8")
    return nm


def _fakes(monkeypatch, calls: list) -> None:
    def parts(*a, **k):
        calls.append("parts")
        raise E.PartsError("part 2 starts one frame late")

    def whole(src, dst, *a, **k):
        calls.append("whole")
        Path(dst).write_bytes(b"enhanced")

    monkeypatch.setattr(E, "enhance_parts", parts)
    monkeypatch.setattr(E, "run_topaz", whole)
    monkeypatch.setattr(E, "check_enhanced", lambda *a, **k: {"ok": True, "why": ""})


def test_a_parts_failure_is_written_at_once_and_a_second_take_goes_straight_to_the_whole_piece(tmp_path, monkeypatch):
    nm = _piece(tmp_path)
    calls: list = []
    _fakes(monkeypatch, calls)
    seen: list = []

    def whole_and_look(src, dst, *a, **k):                # while the whole piece runs, the json already says why
        seen.append(json.loads(nm.info.read_text(encoding="utf-8")))
        calls.append("whole")
        Path(dst).write_bytes(b"enhanced")

    monkeypatch.setattr(E, "run_topaz", whole_and_look)
    job = E.enhance_one(nm, "t1")
    assert job.state == "done" and calls == ["parts", "whole"]
    assert "one frame late" in seen[0]["parts_failed"] and "whole piece instead" in seen[0]["worker_note"]
    assert seen[0]["ranges"] == [[0, 60], [180, 240]]       # the parts stay written down (the swap reads them)
    nm.enhanced.unlink()
    calls.clear()
    job = E.enhance_one(nm, "t2")                           # taken again (the app closed and opened)
    assert job.state == "done" and calls == ["whole"]
