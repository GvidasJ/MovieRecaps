"""7 Nov (your call: only the enhanced picture stays): a project whose edit plays the Topaz picture zips without the
normal RAW's piece (edit\\extras\\media\\raw.mp4), and a zip made a moment too early -- with the normal RAW in it --
is made again."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from mr_app.core import share_zip as Z

BIG = Z.BIG_BYTES + 1


def _big(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:                  # a sparse file: big, but no disk space
        f.truncate(BIG)
    return path


def _project(root: Path, state: str = "used") -> Path:
    f = root / "2026-11-07_Work Seminar (2)"
    media = f / "edit" / "extras" / "media"
    enh = _big(media / "raw_enhanced.mp4")
    wav = media / "raw_stereo.wav"
    wav.write_bytes(b"w")
    (f / "edit" / "1_edit.xml").write_text(
        "<xmeml><sequence>" + "".join(f"<file><pathurl>{p.as_uri()}</pathurl></file>" for p in (enh, wav))
        + "</sequence></xmeml>", encoding="utf-8")
    (f / "project.json").write_text(json.dumps({"enhance": {"state": state}}), encoding="utf-8")
    return f


def test_the_normal_raws_piece_stays_out_when_the_edit_plays_the_enhanced_one(tmp_path):
    f = _project(tmp_path)
    _big(f / "edit" / "extras" / "media" / "raw.mp4")
    put, left = Z.members(f, f / "edit")
    names = {Path(a).name for _x, a in put}
    assert "raw_enhanced.mp4" in names and "raw.mp4" not in names
    assert any(x.name == "raw.mp4" and "normal picture" in why for x, why in left)


def test_without_the_topaz_picture_the_raw_piece_still_goes_in(tmp_path):
    f = _project(tmp_path, state="sent")
    _big(f / "edit" / "extras" / "media" / "raw.mp4")
    put, _left = Z.members(f, f / "edit")
    assert "raw.mp4" in {Path(a).name for _x, a in put}


def test_a_zip_holding_a_file_that_left_the_folder_is_made_again(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_ZIP_ANY_DRIVE", "1")
    f = _project(tmp_path)
    z = Z.zip_path(f)
    put, _left = Z.members(f, f / "edit")
    with zipfile.ZipFile(z, "w", allowZip64=True) as zf:     # the zip as it should be ...
        for _x, a in put:
            zf.writestr(a, b"")
        zf.comment = Z.MARK
    assert not Z.needs_zip(f, f / "edit")
    with zipfile.ZipFile(z, "a", allowZip64=True) as zf:     # ... and with the normal RAW put in too early
        with zf.open(f"{Z.plain_name(f)}/edit/extras/media/raw.mp4", "w", force_zip64=True) as w:
            w.write(b"\0" * 1024)
    monkeypatch.setattr(Z, "BIG_BYTES", 100)
    assert Z.needs_zip(f, f / "edit")
