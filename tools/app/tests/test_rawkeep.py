"""Day 13, keep the original RAW -- the Board's Clean up for older runs that play a big converted raw_stereo.mp4: the
original RAW must still be on this computer and be the very file the run was made from (its content hash), Premiere
must be able to play it as it is, and no Premiere project may play raw_stereo (only read, never changed); the space is
shown, and only after you said yes the original is hard-linked in, raw_stereo's sound becomes raw_stereo.wav, the
tool's own XMLs play the two files (passing the tool's checks as before) and raw_stereo.mp4 goes.

Run: python -m pytest tools/app/tests/test_rawkeep.py"""
from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts" / "tests"))

from match_cuts import export_xml_edl as X  # noqa: E402
from match_cuts import stereo  # noqa: E402
from match_cuts.common import ffmpeg_bin, file_hash  # noqa: E402
from match_cuts.config import Config  # noqa: E402
from match_cuts.keep_speed import keep_speed  # noqa: E402

from mr_app.core import projects as PJ  # noqa: E402
from mr_app.core import rawkeep as RK  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")
from test_board_ui import pump_until, qapp  # noqa: E402,F401 - the Board page's test helpers (qapp: a fixture)


def _video(path: Path, seconds: float = 2.0) -> Path:
    """A 5.1 RAW whose picture is big for its length (lossless), like a film's converted copy against its sound."""
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24000/1001",
                    "-f", "lavfi", "-i", "anoisesrc=color=pink:sample_rate=48000:amplitude=0.4:seed=3",
                    "-filter_complex", "[1:a]pan=5.1|FL=c0|FR=c0|FC=c0|LFE=c0|BL=c0|BR=c0[a]", "-map", "0:v",
                    "-map", "[a]", "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def originals(tmp_path_factory):
    d = tmp_path_factory.mktemp("downloads")
    raw = _video(d / "Borat (2006) (1080p BluRay x265 Silence).mp4")
    one = Path(stereo.stereo_copy(raw, d / "stereo_made.mp4")["path"])       # what a run before day 13 made
    return SimpleNamespace(dir=d, raw=raw, one=one)


def old_run(root: Path, originals, issues=(), name: str = "borat") -> SimpleNamespace:
    """A project with a finished run made before day 13: 1_edit.xml (written by the tool) and extras/edit_60fps.xml
    playing extras/media/raw_stereo.mp4 on V1 and A1, the cut list naming the original RAW and its hash."""
    folder = PJ.create(root, name=name).folder
    edit = folder / "edit"
    media = edit / "extras" / "media"
    media.mkdir(parents=True)
    (edit / "extras" / "report.md").write_text("# report", encoding="utf-8")
    one = media / "raw_stereo.mp4"
    one.write_bytes(originals.one.read_bytes())
    from test_keep_speed import cutlist
    kc, _ = keep_speed(cutlist())
    kc.raw = stereo.stereo_block(dict(kc.raw, audio_channels=6), one, edit / "extras")
    xml = edit / "1_edit.xml"
    X.write_premiere_xml(kc, xml, Config(out_dir=str(edit / "extras"), premiere=True))
    planned = edit / "extras" / "edit_60fps.xml"
    planned.write_text(xml.read_text(encoding="utf-8"), encoding="utf-8")
    raw = {"file": "media/raw_ae.mp4", "source_path": str(originals.dir / "gone" / originals.raw.name),
           "source_hash": file_hash(originals.raw), "source_issues": list(issues), "frames": 48,
           "duration_s": 2.002, "fps": "24000/1001", "width": 640, "height": 360}
    (edit / "extras" / "cutlist.json").write_text(json.dumps({"raw": raw}), encoding="utf-8")
    return SimpleNamespace(folder=folder, edit=edit, media=media, one=one, xml=xml, planned=planned)


def _plan(r, originals, extra=()):
    return RK.plan([r.folder], roots=[originals.dir], extra_roots=list(extra))


def test_an_old_run_gets_the_original_raw_and_a_stereo_wav_and_its_big_copy_goes_only_when_asked(tmp_path,
                                                                                               originals):
    r = old_run(tmp_path, originals)
    said: list = []
    items = RK.plan([r.folder], say=said.append, roots=[originals.dir], extra_roots=[])
    assert len(items) == 1
    it = items[0]
    assert it.ok, it.problems
    assert it.original == originals.raw and it.copy_bytes == 0 and it.channels == 6       # found by its name + hash
    assert it.freed == it.size - it.wav_bytes > 0 and "a hard link" in it.line() and "raw_stereo.wav" in it.line()
    assert set(it.xmls) == {r.xml, r.planned}
    assert said and all("Keep the original RAW (1 of 1)" in s for s in said)
    assert r.one.is_file() and not (r.media / "raw.mp4").exists()           # the plan never changes anything
    freed, errors = RK.replace(items, extra_roots=[])
    assert errors == [] and freed > 0
    assert not r.one.exists()
    pic, wav = r.media / "raw.mp4", r.media / "raw_stereo.wav"
    assert os.path.samefile(pic, originals.raw)                              # a hard link: no space
    from match_cuts import edit_media
    info = edit_media.wav_info(wav)
    assert info["codec"] == "pcm_s16le" and info["channels"] == 2 and info["start"] == 0.0
    for x in (r.xml, r.planned):
        assert stereo.xml_media_names(x) == {"media/raw.mp4", "media/raw_stereo.wav"}
        assert X.sound_file_problems(x) == [] and X.premiere_link_problems(x)[0] == []
        assert X.premiere_sync_problems(x)[0] == [] and X.premiere_item_problems(x) == []
        root = ET.parse(x).getroot()
        seq = root.find("sequence")
        assert {c.find("file").get("id") for c in seq.findall("media/video/track")[0].findall("clipitem")} == \
            {"file-raw"}
        assert {c.find("file").get("id") for c in seq.findall("media/audio/track")[0].findall("clipitem")} == \
            {X.SOUND_FILE_ID}
    assert "Clean up (day 13)" in (r.edit / "extras" / "report.md").read_text(encoding="utf-8")
    assert RK.plan([r.folder], roots=[originals.dir], extra_roots=[]) == []     # nothing left to do


def test_the_rewritten_xml_keeps_every_clip_as_it_was(tmp_path, originals):
    r = old_run(tmp_path, originals)
    before = X.parse_premiere_xml(r.xml)
    RK.replace(_plan(r, originals), extra_roots=[])
    after = X.parse_premiere_xml(r.xml)
    for kind in ("clips", "audio"):
        keys = ("start", "end", "in", "out", "speed", "links")
        assert [{k: c[k] for k in keys} for c in before[kind]] == [{k: c[k] for k in keys} for c in after[kind]]
    assert [c["motion"] for c in before["clips"]] == [c["motion"] for c in after["clips"]]
    assert before["duration"] == after["duration"] and before["markers"] == after["markers"]


def test_a_project_playing_the_big_copy_keeps_it_and_is_never_changed(tmp_path, originals):
    r = old_run(tmp_path, originals)
    desk = tmp_path / "Desktop"
    desk.mkdir()
    fixed = desk / "borat_fixed.prproj"
    fixed.write_bytes(gzip.compress(f"<PremiereData><Media><FilePath>{r.one}</FilePath></Media></PremiereData>"
                                    .encode()))
    data, when = fixed.read_bytes(), os.stat(fixed).st_mtime_ns
    it = _plan(r, originals, [desk])[0]
    assert not it.ok and any("borat_fixed.prproj plays raw_stereo.mp4" in p for p in it.problems)
    assert RK.replace([it], extra_roots=[desk]) == (0, [])                    # not ok: nothing done
    assert r.one.is_file() and fixed.read_bytes() == data and os.stat(fixed).st_mtime_ns == when


def test_the_big_copy_stays_when_the_original_is_gone_another_file_or_not_playable(tmp_path, originals):
    r = old_run(tmp_path, originals)
    it = RK.plan([r.folder], roots=[tmp_path / "nowhere"], extra_roots=[])[0]
    assert not it.ok and "not on this computer any more" in it.problems[0]
    other = tmp_path / "elsewhere"
    other.mkdir()
    _video(other / originals.raw.name, seconds=2.0)                            # the same name, another file
    data = (other / originals.raw.name).read_bytes()
    (other / originals.raw.name).write_bytes(data[:-10] + b"0123456789")
    it = RK.plan([r.folder], roots=[other], extra_roots=[])[0]
    assert not it.ok and "not on this computer any more" in it.problems[0]
    r2 = old_run(tmp_path / "b", originals, issues=["vfr: PTS jitter 0.6 frames at nominal 24000/1001"], name="vfr")
    it = RK.plan([r2.folder], roots=[originals.dir], extra_roots=[])[0]
    assert not it.ok and "frame for frame" in it.problems[0]
    assert r.one.is_file() and r2.one.is_file()


def test_a_failing_check_leaves_the_run_as_it_was(tmp_path, originals, monkeypatch):
    r = old_run(tmp_path, originals)
    items = _plan(r, originals)
    xml_before = r.xml.read_text(encoding="utf-8")
    from match_cuts import export_xml_edl
    monkeypatch.setattr(export_xml_edl, "sound_file_problems", lambda _p: ["A1 would be off the picture"])
    freed, errors = RK.replace(items, extra_roots=[])
    assert freed == 0 and errors and "kept as it was" in errors[0]
    assert r.one.is_file() and r.xml.read_text(encoding="utf-8") == xml_before
    assert not (r.media / "raw.mp4").exists() and not (r.media / "raw_stereo.wav").exists()


def test_board_clean_up_offers_the_original_raw_with_the_space_saved_and_replaces_only_on_yes(qapp, tmp_path,
                                                                                             monkeypatch, originals):
    from PySide6 import QtWidgets

    from mr_app.core import publish
    from mr_app.tools import board_tool as BT
    local, shared = tmp_path / "local", tmp_path / "shared"
    local.mkdir()
    shared.mkdir()
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(local))
    monkeypatch.setattr(RK, "default_roots", lambda _project: [originals.dir])
    monkeypatch.setattr(RK, "project_roots", lambda: [])
    r = old_run(local, originals)
    toasts: list = []
    ctx = SimpleNamespace(settings=SimpleNamespace(save_to=str(shared), upload_check_minutes=30),
                          toast=lambda text, kind="ok": toasts.append((text, kind)))
    page = BT.BoardPage(ctx)
    page._up_timer.stop()                                       # noqa: SLF001 - no Shorts check in tests
    page.check_uploads = lambda force=False: None
    page.show()
    try:
        assert pump_until(qapp, lambda: len(page.cards) == 1)
        progress: list = []
        page.cleanup_progress.connect(progress.append)
        asked: list = []
        answer = [QtWidgets.QMessageBox.StandardButton.No]
        monkeypatch.setattr(BT.QMessageBox, "question",
                            lambda _w, _t, text, *a, **k: (asked.append(text), answer[0])[1])
        page.clean_up()
        assert pump_until(qapp, lambda: asked and page.b_clean.isEnabled(), 60.0)
        text = asked[0]
        it = RK.plan([r.folder], roots=[originals.dir], extra_roots=[])[0]
        assert "Keep the original RAW" in text and "raw_stereo.mp4" in text and "raw_stereo.wav" in text
        assert f"Space saved: {publish.plain_size(it.freed)}" in text
        assert any("Keep the original RAW (1 of 1)" in p for p in progress)
        assert r.one.is_file()                                                     # No: nothing changed
        answer[0] = QtWidgets.QMessageBox.StandardButton.Yes
        page.clean_up()
        assert pump_until(qapp, lambda: not r.one.exists(), 60.0)
        assert (r.media / "raw.mp4").is_file() and (r.media / "raw_stereo.wav").is_file()
        assert pump_until(qapp, lambda: any("Freed" in t for t, _k in toasts))
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()
