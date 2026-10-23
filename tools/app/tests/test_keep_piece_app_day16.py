"""Day 16, task 3 (the app's part): when the finished edit plays only the movie's piece, the whole movie leaves the
project folder -- only a hard link or a copy of the download that is still at its place, never a converted one --
and the project reads the RAW where it stays.

Run: python -m pytest tools/app/tests/test_keep_piece_app_day16.py"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import flow  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402


def _project(tmp_path: Path, link: bool, kept: bool = True, how: str = "keep") -> tuple[PJ.Project, Path, Path]:
    dl = tmp_path / "Downloads" / "The.Movie.2005.mp4"
    dl.parent.mkdir(parents=True)
    dl.write_bytes(b"m" * 4096)
    p = PJ.create(tmp_path / "projects", name="movie")
    here = p.folder / dl.name
    if link:
        try:
            os.link(dl, here)
        except OSError:
            pytest.skip("no hard links here")
    else:
        here.write_bytes(dl.read_bytes())
    p.update(raw={"file": dl.name, "source_file": str(dl)})
    p.set_step("convert", PJ.DONE, raw={"how": how})
    run = p.folder / "edit"
    (run / "extras").mkdir(parents=True)
    (run / "extras" / "keep_piece.json").write_text(json.dumps({"kept": kept}), encoding="utf-8")
    return PJ.load(p.folder), here, dl


@pytest.mark.parametrize("link", [True, False])
def test_the_whole_movie_leaves_the_folder_and_is_read_where_it_stays(tmp_path, link):
    p, here, dl = _project(tmp_path, link)
    said = flow.drop_whole_raw(p, p.folder / "edit")
    assert "left the project folder" in said and ("hard link" in said if link else "a copy" in said)
    assert not here.exists() and dl.is_file() and dl.read_bytes() == b"m" * 4096     # the download untouched
    q = PJ.load(p.folder)
    assert q.data["raw"]["kept_at"] == str(dl) and q.file("raw") == dl                 # read where it stays


def test_nothing_goes_without_a_piece_a_conversion_or_the_download(tmp_path):
    p, here, dl = _project(tmp_path / "a", link=False, kept=False)
    assert flow.drop_whole_raw(p, p.folder / "edit") == "" and here.is_file()
    p, here, dl = _project(tmp_path / "b", link=False, how="encode")                    # converted: the app's work
    assert flow.drop_whole_raw(p, p.folder / "edit") == "" and here.is_file()
    p, here, dl = _project(tmp_path / "c", link=False)
    dl.unlink()                                                                          # the download is gone
    assert flow.drop_whole_raw(p, p.folder / "edit") == "" and here.is_file()
    p, here, dl = _project(tmp_path / "d", link=False)
    dl.write_bytes(b"other")                                                             # not the same movie
    assert flow.drop_whole_raw(p, p.folder / "edit") == "" and here.is_file()
