"""7 Nov: the same video made twice (the first copy from a broken episode file, then "<name> (2)"): once the new copy
sent its own piece, the old copy's waiting piece leaves the Topaz line -- it is never enhanced; the project itself and
its RAW stay. A different video with a similar name is left alone."""
from __future__ import annotations

import json
from pathlib import Path

from mr_app.core import enhance as E
from mr_app.core import projects as P


def _project(root: Path, name: str, comp_id: str, sent: float, state: str = "sent") -> Path:
    f = root / name
    f.mkdir(parents=True)
    (f / "raw.mp4").write_bytes(b"raw")
    (f / "project.json").write_text(json.dumps({
        "competitor": {"id": comp_id, "file": f"c [{comp_id}].mp4"},
        "enhance": {"name": name, "state": state, "sent": sent}}), encoding="utf-8")
    return f


def _piece(enh: Path, name: str, project: Path, made: float) -> E.Names:
    nm = E.names(enh, name)
    enh.mkdir(exist_ok=True)
    nm.piece.write_bytes(b"piece")
    nm.info.write_text(json.dumps({"project": name, "project_folder": str(project), "made": made}), encoding="utf-8")
    return nm


def test_the_old_copys_piece_leaves_the_line_once_the_new_copy_sent_its_own(tmp_path):
    projects, enh = tmp_path / "Projects", tmp_path / "Enhance"
    old = _project(projects, "2026-11-07_Work Seminar", "DWSEho1k4uN", 100.0)
    _project(projects, "2026-11-07_Work Seminar (2)", "DWSEho1k4uN", 200.0, state="used")
    nm = _piece(enh, old.name, old, 100.0)
    assert E.waiting_pieces(enh) == []
    assert not nm.piece.exists() and not nm.info.exists()
    rec = P.load(old).data["enhance"]
    assert rec["state"] == "replaced" and "(2)" in rec["why"]
    assert (old / "raw.mp4").is_file() and (old / "project.json").is_file()   # the project is never touched


def test_the_newest_copy_and_other_videos_stay_in_the_line(tmp_path):
    projects, enh = tmp_path / "Projects", tmp_path / "Enhance"
    _project(projects, "2026-11-07_Work Seminar", "DWSEho1k4uN", 100.0, state="used")
    new = _project(projects, "2026-11-07_Work Seminar (2)", "DWSEho1k4uN", 200.0)
    other = _project(projects, "2026-11-07_Work Seminar Two", "ZZZZZZZZZZZ", 50.0)
    lookalike = _project(projects, "2026-11-08_Work Seminar", "YYYYYYYYYYY", 10.0)
    a = _piece(enh, new.name, new, 200.0)
    b = _piece(enh, other.name, other, 50.0)
    c = _piece(enh, lookalike.name, lookalike, 10.0)
    names = {n.name for n in E.waiting_pieces(enh)}
    assert names == {a.name, b.name, c.name}


def test_a_piece_whose_project_cannot_be_reached_stays(tmp_path):
    enh = tmp_path / "Enhance"
    nm = _piece(enh, "x", tmp_path / "nowhere", 1.0)
    assert [n.name for n in E.waiting_pieces(enh)] == [nm.name]
