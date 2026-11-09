"""7 Nov (your call: "everything must be in enhanced"): a finished edit with clips outside the piece sent to Topaz
sends a new piece with all of them instead (cut from the file the edit plays; its start in the movie's frames), and
the old one leaves the line; a short RAW goes to Topaz whole."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import enhance as E  # noqa: E402
from mr_app.core import enhance_swap as ES  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402

from test_match_normal_7nov import _project  # noqa: E402


def _movie(path: Path, seconds: int = 40) -> Path:
    from match_cuts.common import ffmpeg_bin
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=24:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast", "-g", "48",
                    "-keyint_min", "48", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(path)], check=True)
    return path


def _sent(p, enh: Path, start=100, frames=240):
    p.update(enhance={"state": "sent", "name": E.safe_name(p.folder.name), "folder": str(enh),
                      "piece": {"start_frame": start, "frames": frames, "fps": "24", "topaz_on": ""}})
    nm = E.names(enh, E.safe_name(p.folder.name))
    enh.mkdir(exist_ok=True)
    nm.piece.write_bytes(b"old")
    nm.info.write_text("{}", encoding="utf-8")
    return nm


def test_clips_outside_the_piece_send_a_new_piece_with_all_of_them(tmp_path):
    p = _project(tmp_path, [(120, 150), (330, 400)])                   # the piece: frames 100-340 -> v1 is outside
    _movie(p.folder / "edit" / "extras" / "media" / "raw.mp4")
    enh = tmp_path / "Enhance"
    old = _sent(p, enh)
    o = ES.outside(p.folder)
    assert o["need"] == [(330, 400)]
    r = ES.cover_all(p.folder)
    assert r["ok"] and r["clips"] == 1, r
    rec = PJ.load(p.folder).data["enhance"]
    assert rec["state"] == "sent" and rec["name"].endswith("_all") and rec["swapped"] is None
    info = rec["piece"]
    assert info["start_frame"] <= 100 and info["start_frame"] + info["frames"] >= 400
    assert E.names(enh, rec["name"]).piece.is_file() and not old.piece.exists()     # the old one left the line
    assert ES.outside(p.folder)["need"] == []                                         # now every clip is in it


def test_every_clip_inside_changes_nothing(tmp_path):
    p = _project(tmp_path, [(120, 150), (200, 260)])
    enh = tmp_path / "Enhance"
    old = _sent(p, enh)
    r = ES.cover_all(p.folder)
    assert r["ok"] and r["clips"] == 0 and old.piece.is_file()


def test_a_short_raw_goes_to_topaz_whole(tmp_path):
    raw = _movie(tmp_path / "raw.mp4", 40)
    info = E.make_piece(None, raw, "short", tmp_path / "Enhance", project=tmp_path)
    assert info["start_frame"] == 0 and abs(info["duration"] - 40.0) < 0.2


def test_a_failed_topaz_sends_all_the_clips_again(tmp_path):
    p = _project(tmp_path, [(120, 150), (330, 400)])
    _movie(p.folder / "edit" / "extras" / "media" / "raw.mp4")
    enh = tmp_path / "Enhance"
    _sent(p, enh)
    p.update(enhance={"state": "failed", "why": "Topaz stopped"})
    assert ES.outside(p.folder)["need"] == [(120, 150), (330, 400)]           # no piece: every clip
    r = ES.cover_all(p.folder)
    assert r["ok"] and r["clips"] == 2, r
    rec = PJ.load(p.folder).data["enhance"]
    assert rec["state"] == "sent" and rec["redos"] == 1 and rec["why"] is None
    assert rec["piece"]["start_frame"] <= 120 and rec["piece"]["start_frame"] + rec["piece"]["frames"] >= 400


def test_topaz_is_tried_at_most_three_times(tmp_path):
    p = _project(tmp_path, [(330, 400)])
    _movie(p.folder / "edit" / "extras" / "media" / "raw.mp4")
    _sent(p, tmp_path / "Enhance")
    p.update(enhance={"redos": ES.MAX_REDOS})
    r = ES.cover_all(p.folder)
    assert not r["ok"] and "3 times" in r["why"]


def test_a_wrong_first_guess_is_not_added_to_the_new_piece(tmp_path):
    """8 Nov (Abbott Elementary: the guess at the episode's end, the clips at 2:00-2:18 -> a 2:25 piece): the new
    piece is the edit's first clip to its last, not the old piece too."""
    p = _project(tmp_path, [(120, 150), (200, 260)])
    _movie(p.folder / "edit" / "extras" / "media" / "raw.mp4", 60)
    _sent(p, tmp_path / "Enhance", start=900, frames=400)           # the guess: 0:37-0:54, no clip in it
    r = ES.cover_all(p.folder)
    assert r["ok"] and r["clips"] == 2, r
    info = PJ.load(p.folder).data["enhance"]["piece"]
    assert info["start_frame"] <= 120 and info["start_frame"] + info["frames"] >= 260
    assert info["start_frame"] + info["frames"] < 600                   # not out to the old piece


def test_a_raw_that_cannot_be_cut_gets_the_right_length(tmp_path):
    """9 Nov (What About Bob: odd pixels; Car Salesman: a ProRes copy): movie_facts gives no length for a RAW that
    cannot be cut -- it was taken as 0: a 1 s 'whole RAW' piece, a 3 s piece for 23 clips. Now its real length."""
    from match_cuts.common import ffmpeg_bin
    raw = tmp_path / "prores.mov"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:d=240",
                    "-c:v", "prores_ks", "-profile:v", "0", str(raw)], check=True)
    info = E.make_piece(None, raw, "long", tmp_path / "Enhance", project=tmp_path, span=(100.0, 160.0))
    assert abs(info["duration"] - 62.0) < 1.5 and abs(info["start_s"] - 99.0) < 1.5, info
