"""Run 19, task 1 -- one RAW per project: the Board's Clean up for projects made before run 19 whose edit plays only
the piece of the movie the run analysed (the song on A2 stopped the edit's move onto the movie), with the whole movie
unused next to it (in the shared folder a real copy). On Yes the edit is moved onto the whole movie, every clip at its
own frame (checked against the movie), and the piece goes; a RAW copy nothing plays goes; a Premiere project that plays
a file keeps it (your projects are only read). Nothing happens without Yes.

Run: python -m pytest tools/app/tests/test_rawcopies_run19.py"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from match_cuts import excerpt as E  # noqa: E402
from match_cuts import music  # noqa: E402
from match_cuts.common import ffmpeg_bin  # noqa: E402
from match_cuts.export_xml_edl import _file_url  # noqa: E402

from mr_app.core import projects as PJ  # noqa: E402
from mr_app.core import rawcopies as RC  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")
from test_board_ui import pump_until, qapp  # noqa: E402,F401

CLIPS = [("c1", 0, 24, 10, 34), ("c2", 24, 48, 100, 124), ("c3", 48, 72, 200, 224)]


@pytest.fixture(autouse=True)
def _no_recent_projects(monkeypatch):
    """Never the real Premiere's recent list in a test."""
    monkeypatch.setattr(RC, "recent_projects", lambda: [])


@pytest.fixture(scope="module")
def movie_piece(tmp_path_factory):
    """A 40 s 'movie' (23.976 fps, keyframes every 24 frames, 5.1) and its piece cut by match_cuts.excerpt."""
    d = tmp_path_factory.mktemp("movie")
    movie = d / "movie.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24000/1001",
                    "-f", "lavfi", "-i", "anoisesrc=color=pink:sample_rate=48000:amplitude=0.4:seed=3",
                    "-filter_complex", "[1:a]pan=5.1|FL=0.5*c0|FR=0.4*c0|FC=0.3*c0|LFE=0.2*c0|BL=0.1*c0|BR=0.1*c0[a]",
                    "-map", "0:v", "-map", "[a]", "-t", "40", "-c:v", "libx264", "-bf", "2", "-g", "24",
                    "-keyint_min", "24", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", str(movie)],
                   check=True)
    m = E.movie_facts(movie)
    keep = (E.MARGIN_S, E.EPISODE_MAX_S, E.MAX_SHARE)          # (the film rules, with a 6 s margin: a real piece)
    E.MARGIN_S, E.EPISODE_MAX_S, E.MAX_SHARE = 6.0, 0.0, 0.9
    try:
        plan = E.plan_piece(m, [15.0])
    finally:
        E.MARGIN_S, E.EPISODE_MAX_S, E.MAX_SHARE = keep
    piece = Path(E.cut_piece(m, plan, d / "raw_piece.mov")["path"])
    song = d / "song.wav"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "20", str(song)], check=True)
    return SimpleNamespace(movie=movie, piece=piece, m=m, k0=plan.k0, n=E._frames_in(piece), song=song)


def _rate(parent) -> None:
    r = ET.SubElement(parent, "rate")
    ET.SubElement(r, "timebase").text = "24"
    ET.SubElement(r, "ntsc").text = "TRUE"


def _edit_xml(path: Path, v1: Path, wav: Path) -> None:
    """1_edit.xml as a run on the piece leaves it: V1 / A1 clips on the piece and its sound, 72 frames."""
    root = ET.Element("xmeml", version="5")
    seq = ET.SubElement(root, "sequence")
    _rate(seq)
    ET.SubElement(seq, "duration").text = "72"
    media = ET.SubElement(seq, "media")
    for kind, fid, f in (("video", "file-raw", v1), ("audio", "file-raw-sound", wav)):
        tr = ET.SubElement(ET.SubElement(media, kind), "track")
        for k, (cid, st, en, i, o) in enumerate(CLIPS):
            ci = ET.SubElement(tr, "clipitem", id=f"{cid}-{kind}")
            ET.SubElement(ci, "name").text = f.name
            ET.SubElement(ci, "duration").text = "1000"
            _rate(ci)
            for tag, v in (("start", st), ("end", en), ("in", i), ("out", o)):
                ET.SubElement(ci, tag).text = str(v)
            fe = ET.SubElement(ci, "file", id=fid)
            if k == 0:
                ET.SubElement(fe, "name").text = f.name
                ET.SubElement(fe, "pathurl").text = _file_url(str(f))
                _rate(fe)
                ET.SubElement(fe, "duration").text = "1000"
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, "unicode"),
                    encoding="utf-8")


def failed_run(root: Path, mp, name: str, published_to: Path | None = None) -> SimpleNamespace:
    """A project made before run 19: its edit plays the piece (raw.mov) with the song on A2, the whole movie
    (raw.mp4, a copy as in the shared folder) unused next to it, the piece's record in the decision log."""
    p = PJ.create(root, folder=name)
    if published_to is not None:
        p.update(published={"to": str(published_to), "at": PJ.now()})
    edit = p.folder / "edit"
    media = edit / "extras" / "media"
    (edit / "extras" / "debug").mkdir(parents=True)
    media.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(mp.piece, media / "raw.mov")
    shutil.copyfile(mp.movie, media / "raw.mp4")
    y = (np.random.default_rng(1).standard_normal((mp.n * 2002, 2)) * 2000).astype("<i2")
    with wave.open(str(media / "raw_stereo.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(y.tobytes())
    xml = edit / "1_edit.xml"
    _edit_xml(xml, media / "raw.mov", media / "raw_stereo.wav")
    shutil.copyfile(xml, edit / "extras" / "edit_60fps.xml")
    assert music.add_music(xml, mp.song, 0.0, 1.0, [5.0, -1.0], media_dir=media, volume_db=8.5).get("clips") == 2
    rec = {"decision": "excerpt", "stage": "inputs", "k0": mp.k0, "frames": mp.n, "fps": mp.m.fps,
           "movie_frames": mp.m.frames, "movie_seconds": round(mp.m.duration, 3), "places": [15.0], "cut_seconds": 1.0,
           "sound": "wav", "movie": str(p.folder / "movie.mp4"), "piece": "work/excerpt/raw_piece.mov"}
    (edit / "extras" / "debug" / "decisions.jsonl").write_text(
        json.dumps({"decision": "env", "stage": "env"}) + "\n" + json.dumps(rec) + "\n", encoding="utf-8")
    (edit / "extras" / "report.md").write_text("# report\n", encoding="utf-8")
    return SimpleNamespace(folder=p.folder, edit=edit, media=media, xml=xml)


def _clips(xml: Path) -> list[tuple[str, int, int]]:
    root = ET.parse(xml).getroot()
    return [(ci.get("id"), int(ci.findtext("in")), int(ci.findtext("out"))) for ci in root.iter("clipitem")
            if ci.find("file") is not None and ci.find("file").get("id") in ("file-raw", "file-raw-sound")]


def _videos(media: Path) -> list[str]:
    return sorted(f.name for f in media.iterdir() if f.suffix.lower() in (".mp4", ".mov"))


def test_a_project_and_its_twin_play_the_whole_movie_once_after_yes(tmp_path, movie_piece):
    shared, local = tmp_path / "shared", tmp_path / "local"
    s = failed_run(shared, movie_piece, "2026-10-25_The Hardest Guessing Game of All Time")
    t = failed_run(local, movie_piece, "2026-10-25_The Hardest Guessing Game of All Time (2)", published_to=s.folder)
    before = {r.edit: _clips(r.xml) for r in (s, t)}
    found = RC.plan([s.folder], local_root=local)
    assert len(found) == 2 and {it.kind for it in found} == {"piece"}
    assert {it.tag for it in found} == {"shared folder", "this computer"}
    for it in found:
        assert it.ok, it.problems
        assert "plays only the piece" in it.line() and "raw.mov" in it.line() and "raw.mp4" in it.line()
        assert it.notes and "SSIM 1.000" in it.notes[0]       # moved aside and checked
    assert _videos(s.media) == ["raw.mov", "raw.mp4"]         # (planning changes nothing)
    freed, errors = RC.remove(found)
    assert errors == []
    for r in (s, t):
        assert _videos(r.media) == ["raw.mp4"]                # ONE RAW: the whole movie
        assert _clips(r.xml) == [(c, i + movie_piece.k0, o + movie_piece.k0) for c, i, o in before[r.edit]]
        text = r.xml.read_text(encoding="utf-8")
        assert "raw.mov" not in text and "/media/raw.mp4" in text and "MUSIC" in text
        assert E.run_info(r.edit) is not None                 # Learn reads it as a run on the movie
    piece = movie_piece.piece.stat().st_size
    grows = movie_piece.k0 * 2002 * 4                         # (A1's sound: silence up to the piece)
    assert abs(freed - 2 * (piece - grows)) < 4096            # (+ the WAV headers)
    assert RC.plan([s.folder], local_root=local) == []        # nothing left to do


def test_your_premiere_project_playing_the_piece_keeps_it(tmp_path, movie_piece, monkeypatch):
    shared = tmp_path / "shared"
    s = failed_run(shared, movie_piece, "2026-10-24_The Most Strategic Team Pick of All Time (5)")
    mine = tmp_path / "Copied_r2_POPW" / "r2_POPW.prproj"      # your project elsewhere (Premiere's recent list)
    mine.parent.mkdir()
    mine.write_bytes(gzip.compress(f"<PremiereData><FilePath>{s.media / 'raw.mov'}</FilePath></PremiereData>"
                                   .encode()))
    monkeypatch.setattr(RC, "recent_projects", lambda: [mine])
    xml_before = s.xml.read_bytes()
    found = RC.plan([s.folder], local_root=tmp_path / "local")
    assert len(found) == 1 and not found[0].ok
    assert "r2_POPW.prproj plays raw.mov" in found[0].problems[0]
    assert RC.remove(found) == (0, [])                        # not ok: nothing done
    assert _videos(s.media) == ["raw.mov", "raw.mp4"] and s.xml.read_bytes() == xml_before
    assert gzip.decompress(mine.read_bytes()).count(b"raw.mov") == 1        # (only read)


def test_a_move_that_does_not_check_out_changes_nothing(tmp_path, movie_piece, monkeypatch):
    s = failed_run(tmp_path / "shared", movie_piece, "a project")
    monkeypatch.setattr(E, "check_moved", lambda *a, **k: {"ok": False, "rows": [], "why": "c2: not the frame"})
    found = RC.plan([s.folder])
    assert len(found) == 1 and not found[0].ok and "not the piece's" in found[0].problems[0]
    found[0].ok = True                                        # (even if it were said yes to: move_run checks again)
    freed, errors = RC.remove(found)
    assert freed == 0 and errors and "could not be moved" in errors[0]
    assert _videos(s.media) == ["raw.mov", "raw.mp4"]


def test_an_unused_copy_of_a_whole_raw_run_goes_the_played_one_stays(tmp_path, movie_piece):
    p = PJ.create(tmp_path / "shared", name="whole raw")
    edit = p.folder / "edit"
    media = edit / "extras" / "media"
    media.mkdir(parents=True)
    (edit / "extras" / "report.md").write_text("# report\n", encoding="utf-8")
    shutil.copyfile(movie_piece.movie, media / "raw.mp4")
    shutil.copyfile(movie_piece.piece, media / "raw.mov")     # (a stale copy: nothing plays it)
    (edit / "1_edit.xml").write_text(f"<xmeml><file id=\"file-raw\"><pathurl>{_file_url(str(media / 'raw.mp4'))}"
                                     "</pathurl></file></xmeml>", encoding="utf-8")
    found = RC.plan([p.folder])
    assert len(found) == 1 and found[0].kind == "unused" and found[0].ok
    assert "raw.mov" in found[0].line() and "nothing plays it" in found[0].line()
    freed, errors = RC.remove(found)
    assert errors == [] and freed == movie_piece.piece.stat().st_size
    assert _videos(media) == ["raw.mp4"]


def test_an_unfinished_run_or_a_paused_one_is_not_offered(tmp_path, movie_piece):
    s = failed_run(tmp_path / "shared", movie_piece, "a project")
    (s.edit / "extras" / "report.md").unlink()                # no report: the run never finished (or is paused)
    assert RC.plan([s.folder]) == []


def test_board_clean_up_shows_the_projects_and_does_it_only_on_yes(qapp, tmp_path, monkeypatch, movie_piece):
    from PySide6 import QtWidgets

    from mr_app.tools import board_tool as BT
    local, shared = tmp_path / "local", tmp_path / "shared"
    local.mkdir()
    shared.mkdir()
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(local))
    s = failed_run(shared, movie_piece, "2026-10-25_The Hardest Guessing Game of All Time")
    toasts: list = []
    ctx = SimpleNamespace(settings=SimpleNamespace(save_to=str(shared), upload_check_minutes=30),
                          toast=lambda text, kind="ok": toasts.append((text, kind)))
    page = BT.BoardPage(ctx)
    page._up_timer.stop()                                       # noqa: SLF001 - no Shorts check in tests
    page.check_uploads = lambda force=False: None
    page.show()
    try:
        assert pump_until(qapp, lambda: len(page.cards) == 1)
        asked: list = []
        answer = [QtWidgets.QMessageBox.StandardButton.No]
        monkeypatch.setattr(BT.QMessageBox, "question",
                            lambda _w, _t, text, *a, **k: (asked.append(text), answer[0])[1])
        page.clean_up()
        assert pump_until(qapp, lambda: asked and page.b_clean.isEnabled(), timeout_s=60)
        text = asked[0]
        assert "One RAW per project" in text and "The Hardest Guessing Game" in text and "plays only the piece" in text
        assert "the whole movie" in text
        assert _videos(s.media) == ["raw.mov", "raw.mp4"]                     # No: nothing done
        answer[0] = QtWidgets.QMessageBox.StandardButton.Yes
        page.clean_up()
        assert pump_until(qapp, lambda: _videos(s.media) == ["raw.mp4"], timeout_s=60)
        assert pump_until(qapp, lambda: any("Freed" in t for t, _k in toasts))
        assert "raw.mov" not in s.xml.read_text(encoding="utf-8")
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()
