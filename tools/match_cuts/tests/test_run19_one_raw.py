"""Run 19, task 1: only ONE RAW per project, and the edit plays the whole movie.

Since night 15 (SceneProof's song; every channel since run 18) the song is on A2 of 1_edit.xml when a run that
analysed a piece of the movie is moved onto the movie (excerpt.finish) -- and the item check counted the song as
"doubled audio" with A1, so the move failed: 1_edit.xml kept playing the piece (extras/media/raw.mov) and the movie's
link it had already made (raw.mp4) stayed next to it, unused (a real 7.1 GB copy in D:\\Shared, both in the zip)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import excerpt as E
from match_cuts import export_xml_edl as X
from match_cuts import music
from match_cuts.common import ffmpeg_bin

from test_day14_excerpt import _movie, _xml

CLIPS = [("c1", 0, 24, 10, 34, 1.0), ("c2", 24, 48, 100, 124, 1.0), ("c3", 48, 72, 200, 224, 1.0)]


def _wav(path: Path, samples: int, seed: int = 1) -> Path:
    y = (np.random.default_rng(seed).standard_normal((samples, 2)) * 2000).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(y.tobytes())
    return path


def _song(path: Path) -> Path:
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "20", str(path)], check=True)
    return path


def _seq_duration(xml: Path, frames: int) -> None:
    root = ET.parse(xml).getroot()
    ET.SubElement(root.find("sequence"), "duration").text = str(frames)
    xml.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, "unicode"),
                   encoding="utf-8")


def _piece_run(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    """A finished piece run as the pipeline leaves it before the move: the movie, its piece in work, the run folder's
    media (raw.mov: the piece's hard link, raw_stereo.wav: the piece's sound), 1_edit.xml on the piece with the song on
    A2 (music.add_music, as the pipeline does after S9) and its 60 fps planning copy."""
    (tmp_path / "in").mkdir()
    movie = _movie(tmp_path / "in" / "movie.mp4")
    m = E.movie_facts(movie)
    monkeypatch.setattr(E, "MARGIN_S", 6.0)
    monkeypatch.setattr(E, "EPISODE_MAX_S", 0.0)
    monkeypatch.setattr(E, "MAX_SHARE", 0.9)
    plan = E.plan_piece(m, [15.0])
    made = E.cut_piece(m, plan, tmp_path / "work" / "excerpt" / E.PIECE_NAME)
    n = E._frames_in(made["path"])
    ex = E.Excerpt(piece=str(made["path"]), movie=str(movie), k0=plan.k0, frames=n, fps=m.fps, movie_frames=m.frames,
                   movie_seconds=round(m.duration, 3), places=[15.0], sound="wav")
    run = tmp_path / "proj" / "edit"
    extras = run / "extras"
    media = extras / "media"
    media.mkdir(parents=True)
    os.link(made["path"], media / "raw.mov")
    _wav(media / "raw_stereo.wav", n * 2002)                  # (23.976 fps: 2002 samples a frame)
    xml = _xml(run / "1_edit.xml", CLIPS, str(media / "raw.mov"), str(media / "raw_stereo.wav"))
    _seq_duration(xml, 72)
    shutil.copyfile(xml, extras / "edit_60fps.xml")
    song = _song(tmp_path / "in" / "song.wav")
    res = music.add_music(xml, song, 0.0, 1.0, [5.0, -1.0], media_dir=media, volume_db=8.5)
    assert res.get("clips") == 2, res
    (extras / "report.md").write_text("# report\n", encoding="utf-8")
    return SimpleNamespace(movie=movie, ex=ex, run=run, extras=extras, media=media, xml=xml, n=n, k0=plan.k0)


def _clips(xml: Path, fid: str) -> list[tuple[str, int, int]]:
    root = ET.parse(xml).getroot()
    return [(ci.get("id"), int(ci.findtext("in")), int(ci.findtext("out"))) for ci in root.iter("clipitem")
            if ci.find("file") is not None and ci.find("file").get("id") == fid]


def _videos(media: Path) -> list[str]:
    return sorted(p.name for p in media.iterdir() if p.suffix.lower() in (".mp4", ".mov", ".m4v", ".mkv"))


# ---------------------------------------------------------------------------------------------------------------------
# the song under the voice is not "doubled audio"
# ---------------------------------------------------------------------------------------------------------------------

def test_the_song_under_the_voice_is_not_doubled_audio(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    xml = _xml(tmp_path / "1_edit.xml", CLIPS, str(media / "raw.mov"), str(media / "raw_stereo.wav"))
    _seq_duration(xml, 72)
    assert X.premiere_item_problems(xml) == []
    assert music.add_music(xml, _song(tmp_path / "song.wav"), 0.0, 1.0, [5.0, -1.0], media_dir=media).get("clips") == 2
    assert X.premiere_item_problems(xml) == []               # (until run 19: 3 x "doubled audio" with A1)
    root = ET.parse(xml).getroot()
    tracks = root.findall("sequence/media/audio/track")
    a, b = tracks[-1].findall("clipitem")[:2]                # the song's two clips made to overlap each other ...
    a.find("end").text = str(int(a.findtext("end")) + 5)
    b.find("start").text = str(int(b.findtext("start")) - 5)
    b.find("in").text = str(int(b.findtext("in")) - 5)
    bad = tmp_path / "bad.xml"
    bad.write_text(ET.tostring(root, "unicode"), encoding="utf-8")
    probs = X.premiere_item_problems(bad)
    assert probs and all("doubled audio" not in p for p in probs) and any("overlaps" in p for p in probs)
    for ci in tracks[-1].findall("clipitem"):                # ... and any OTHER sound under A1 is still doubled audio
        ci.find("file").set("id", "file-other")
    bad.write_text(ET.tostring(root, "unicode"), encoding="utf-8")
    assert any("doubled audio" in p for p in X.premiere_item_problems(bad))


# ---------------------------------------------------------------------------------------------------------------------
# a piece run with the song: the edit plays the movie, the run folder holds ONE RAW
# ---------------------------------------------------------------------------------------------------------------------

def test_a_piece_run_with_the_song_plays_the_whole_movie_and_keeps_one_raw(tmp_path, monkeypatch):
    r = _piece_run(tmp_path, monkeypatch)
    before_v1 = _clips(r.xml, "file-raw")
    before_a1 = _clips(r.xml, "file-raw-sound")
    song_before = _clips(r.xml, music.FILE_ID)
    out = E.move_run(r.ex, r.run, r.extras, r.media, r.media / "raw.mov")
    assert out["moved"] and out["placed"] == "hardlink" and out["exact"]
    assert _videos(r.media) == ["raw.mp4"]                   # ONE RAW: the movie (no piece next to it)
    assert os.path.samefile(r.media / "raw.mp4", r.movie)    # (a hard link of the movie: no extra space)
    assert not Path(r.ex.piece).exists()                     # the piece in work is gone too
    assert _clips(r.xml, "file-raw") == [(c, i + r.k0, o + r.k0) for c, i, o in before_v1]   # frame-exact, movie
    assert _clips(r.xml, "file-raw-sound") == [(c, i + r.k0, o + r.k0) for c, i, o in before_a1]
    assert _clips(r.xml, music.FILE_ID) == song_before       # the song as it was
    text = r.xml.read_text(encoding="utf-8")
    assert "/media/raw.mp4" in text and "raw.mov" not in text
    assert "raw.mov" not in (r.extras / "edit_60fps.xml").read_text(encoding="utf-8")
    assert X.premiere_item_problems(r.xml) == []
    with wave.open(str(r.media / "raw_stereo.wav"), "rb") as w:      # A1: silence up to the piece, then the piece
        assert w.getnframes() == (r.k0 + r.n) * 2002
    info = E.run_info(r.run)
    assert info and info["k0"] == r.k0 and E.failed_move(r.run) is None
    assert "Piece of the movie" in (r.extras / "report.md").read_text(encoding="utf-8")


def test_a_failed_move_leaves_no_movie_next_to_the_piece_and_says_so(tmp_path, monkeypatch):
    r = _piece_run(tmp_path, monkeypatch)
    xml_before = r.xml.read_bytes()
    wav_before = (r.media / "raw_stereo.wav").stat().st_size
    monkeypatch.setattr(E, "check_moved", lambda *a, **k: {"ok": False, "rows": [], "why": "c2: not the frame"})
    with pytest.raises(RuntimeError, match="not the frame"):
        E.move_run(r.ex, r.run, r.extras, r.media, r.media / "raw.mov")
    assert _videos(r.media) == ["raw.mov"]                   # the movie's link it made is gone again
    assert r.xml.read_bytes() == xml_before and (r.media / "raw_stereo.wav").stat().st_size == wav_before
    assert not list(r.media.glob("*.part*")) and not list(r.run.glob("*.part*"))
    # the pipeline's step: a warning, the edit's line, extras/excerpt.json -- and the app's note
    warned: list[str] = []
    ctx = SimpleNamespace(excerpt=r.ex, cfg=SimpleNamespace(deliver=r.run, out=r.extras, media_dir=r.media),
                          edit_media={"picture": {"path": str(r.media / "raw.mov")}, "line": "V1 plays ..."},
                          raw_conform=SimpleNamespace(path=str(r.media / "raw_ae.mp4")), warn=warned.append,
                          dlog=SimpleNamespace(record=lambda *a, **k: None))
    res = E.finish(ctx)
    assert res["moved"] is False and res["plays"] == "raw.mov" and "not the frame" in res["error"]
    assert warned and "plays only the piece" in warned[0] and "of 0:40" in warned[0]
    assert "only the piece" in ctx.edit_media["line"]
    fm = E.failed_move(r.run)
    assert fm and fm["plays"] == "raw.mov" and E.run_info(r.run) is None   # (not read as moved)
    from match_cuts import batch, check_all
    monkeypatch.setattr(check_all, "read_run", lambda *a, **k: {"deliverables": {"status": "pass"},
                                                                 "coverage": {"status": "pass"}})
    c = batch.checks_of(r.run)
    assert c["pass"] and c["notes"] and "plays only the piece of the movie" in c["notes"][0]


def test_the_move_keeps_a_movie_already_in_the_media(tmp_path, monkeypatch):
    """The app's Clean up (run 19) finishes an old failed move: the movie is already in the run's media (the link
    the old run left there) -- it is kept as it is, the piece goes."""
    r = _piece_run(tmp_path, monkeypatch)
    os.link(r.movie, r.media / "raw.mp4")                    # (what the failed move left behind)
    ex = E.Excerpt(**{**r.ex.to_dict(), "movie": str(r.media / "raw.mp4"), "piece": str(r.media / "raw.mov")})
    out = E.move_run(ex, r.run, r.extras, r.media, r.media / "raw.mov")
    assert out["moved"] and out["placed"] == "same" and _videos(r.media) == ["raw.mp4"]
    assert r.movie.is_file() and os.path.samefile(r.media / "raw.mp4", r.movie)
