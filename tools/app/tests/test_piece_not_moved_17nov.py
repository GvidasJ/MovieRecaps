"""17 Nov (The Biggest Meltdown, The Worst Prank: Topaz ran twice): when the run analysed a piece of the movie and its
edit could not be moved onto the movie (extras/excerpt.json: moved false), 1_edit.xml counts the piece's frames. The
piece Topaz got before the edit (in the movie's frames) is still found -- no second piece -- and a new piece, when one
is needed, says its start in the movie's frames too."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import enhance_swap as ES  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402

from test_cover_all_7nov import _movie, _sent  # noqa: E402
from test_match_normal_7nov import _project  # noqa: E402

K0 = 5000


def _not_moved(p, k0=K0, frames=900, moved=False):
    ex = p.folder / "edit" / "extras" / "excerpt.json"
    ex.write_text(json.dumps({"k0": k0, "frames": frames, "fps": "24", "moved": {"moved": moved}}), encoding="utf-8")


def test_the_piece_cut_before_the_edit_is_found_when_the_edit_plays_only_the_piece(tmp_path):
    p = _project(tmp_path, [(120, 150), (200, 260)])                 # the edit: the piece's frames
    _not_moved(p)
    enh = tmp_path / "Enhance"
    old = _sent(p, enh, start=K0 + 100, frames=240)                  # Topaz's piece: the movie's frames
    assert ES.outside(p.folder)["need"] == []
    r = ES.cover_all(p.folder)
    assert r["ok"] and r["clips"] == 0 and old.piece.is_file()       # no second piece


def test_without_the_note_the_same_piece_is_not_found(tmp_path):
    p = _project(tmp_path, [(120, 150), (200, 260)])
    _sent(p, tmp_path / "Enhance", start=K0 + 100, frames=240)
    assert len(ES.outside(p.folder)["need"]) == 2


def test_an_edit_moved_onto_the_movie_has_no_group(tmp_path):
    p = _project(tmp_path, [(120, 150)])
    _not_moved(p, moved=True)
    assert ES._groups(p.folder / "edit") == []                       # noqa: SLF001


def test_a_new_piece_says_its_start_in_the_movies_frames(tmp_path):
    p = _project(tmp_path, [(120, 150), (330, 400)])
    _movie(p.folder / "edit" / "extras" / "media" / "raw.mp4")
    _not_moved(p)
    enh = tmp_path / "Enhance"
    _sent(p, enh, start=K0 + 100, frames=240)                        # frames 100-340 of the piece: v1 is outside
    assert ES.outside(p.folder)["need"] == [(K0 + 330, K0 + 400)]
    r = ES.cover_all(p.folder)
    assert r["ok"] and r["clips"] == 1, r
    info = PJ.load(p.folder).data["enhance"]["piece"]
    assert info["units"] == "movie"
    assert info["start_frame"] <= K0 + 120 and info["start_frame"] + info["frames"] >= K0 + 400
    assert ES.outside(p.folder)["need"] == []


def test_an_older_second_piece_keeps_counting_the_pieces_frames(tmp_path):
    p = _project(tmp_path, [(120, 150), (200, 260)])
    _not_moved(p)
    _sent(p, tmp_path / "Enhance", start=100, frames=240)            # made before 17 Nov: the piece's frames
    p.update(enhance={"piece": {"start_frame": 100, "frames": 240, "fps": "24", "topaz_on": "",
                                "project": p.folder.name + "_all"}})
    assert ES.outside(p.folder)["need"] == []


def test_topaz_after_the_edit_gets_exactly_what_the_edit_plays(tmp_path, monkeypatch):
    """17 Nov: Topaz on this computer -- no piece before the edit; after it, one piece from the edit's first clip to
    its last (not the whole RAW), also where "send to Topaz" is off (the row asked for it), and it is no redo."""
    from mr_app.core import enhance as E
    p = _project(tmp_path, [(120, 150), (330, 400)])
    _movie(p.folder / "edit" / "extras" / "media" / "raw.mp4")
    enh = tmp_path / "Enhance"
    enh.mkdir()
    monkeypatch.setattr(E, "sends", lambda *a, **k: False)
    p.update(enhance={"state": "skipped", "why": "Topaz comes after the edit", "after_edit": True, "topaz_on": "pc",
                      "name": E.safe_name(p.folder.name), "folder": str(enh)})
    r = ES.cover_all(p.folder)
    assert r["ok"] and r["clips"] == 2, r
    rec = PJ.load(p.folder).data["enhance"]
    assert rec["state"] == "sent" and not rec.get("redos") and rec.get("redo") is None and not rec.get("after_edit")
    info = rec["piece"]
    assert info["topaz_on"] == "pc"
    assert info["start_frame"] <= 120 and info["start_frame"] + info["frames"] >= 400
    assert info["frames"] < 24 * 20                                   # 280 frames + 1 s each side, not the 40 s RAW
