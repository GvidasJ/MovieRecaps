"""7 Nov (your call): a finished project keeps only the RAW's scene (+- 10 s) and becomes ONE zip next to the other
projects once its Short is uploaded (or a week after its edit) -- core.archive, core.share_zip; the tool's switches
for a new edit (core.run.scene_env); the motivation strip still counts a zipped project's upload."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import archive as A  # noqa: E402
from mr_app.core import projects as PJ  # noqa: E402
from mr_app.core import run as RN  # noqa: E402
from mr_app.core import share_zip as Z  # noqa: E402

NOW = datetime(2026, 11, 7, 12, 0).astimezone()


def _url(p: Path) -> str:
    return "file://localhost/" + p.resolve().as_posix().lstrip("/")


def _xml(path: Path, media: list[Path]) -> None:
    clips = "".join(f"<clipitem id=\"c{i}\"><file id=\"f{i}\"><name>{m.name}</name><pathurl>{_url(m)}</pathurl>"
                    f"</file><in>0</in><out>10</out></clipitem>" for i, m in enumerate(media))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<xmeml><sequence><media><video><track>{clips}</track></video></media></sequence></xmeml>",
                    encoding="utf-8")


def _big(path: Path, extra: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(Z.BIG_BYTES + 1024 + extra)
    return path


def _project(root: Path, name: str, *, result: str = "PASS", uploaded: bool = True, days_old: float = 1.0,
             state: str = PJ.DONE) -> PJ.Project:
    p = PJ.create(root, name=name)
    media = p.folder / "edit" / "extras" / "media"
    media.mkdir(parents=True)
    (media / "raw.mp4").write_bytes(b"r" * 5000)
    (p.folder / "comp.mp4").write_bytes(b"c" * 300)
    _xml(p.folder / "edit" / "1_edit.xml", [media / "raw.mp4"])
    (p.folder / "edit" / "extras" / "report.md").write_text("report", encoding="utf-8")
    (p.folder / "edit" / "extras" / "compare.mp4").write_bytes(b"x" * 100)
    p.update(competitor={"file": "comp.mp4"}, channel="DanCuts", title="The Funniest Thing")
    p.set_step("edit", state, result=result, run_dir=str(p.folder / "edit"))
    fin = (NOW - timedelta(days=days_old)).isoformat(timespec="seconds")

    def apply(d):
        d["steps"]["edit"]["finished"] = fin
        if uploaded:
            d["board"] = {"uploaded": {"at": fin, "how": "auto"}, "column": "uploaded"}
    p.change(apply)
    return PJ.load(p.folder)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_ZIP_ANY_DRIVE", "1")
    monkeypatch.setattr(Z, "MIN_FREE", 0)
    monkeypatch.setattr(A, "_keep_scene", lambda run, lines: {"kept": False, "why": "a test movie"})
    r = tmp_path / "Shared" / "Projects"
    r.mkdir(parents=True)
    return r


# -- the zip next to the folder --
def test_the_zip_sits_next_to_the_folder_and_the_old_one_inside_goes(root):
    p = _project(root, "zip one", uploaded=False)
    old = Z.old_zip_path(p.folder)
    with zipfile.ZipFile(old, "w") as zf:                   # a zip of the older kind, inside the folder
        zf.writestr("x.txt", "x")
        zf.comment = Z.MARK
    z = Z.make(p.folder, p.folder / "edit")
    assert z == root / f"{Z.plain_name(p.folder)}.zip" and z.is_file() and Z.zip_path(p.folder) == z
    assert not old.exists()
    assert Z.needs_zip(p.folder, p.folder / "edit") is False


def test_another_projects_zip_of_the_same_name_is_never_written_over(root):
    p = _project(root, "same name", uploaded=False)
    taken = root / f"{Z.plain_name(p.folder)}.zip"
    with zipfile.ZipFile(taken, "w") as zf:
        zf.writestr(f"x/{Z.HOW_TO}", "another folder\r\nHOW TO OPEN")
    z = Z.make(p.folder, p.folder / "edit")
    assert z is not None and z != taken and z.name.endswith("(2).zip") and Z.zip_path(p.folder) == z
    assert zipfile.ZipFile(taken).read(f"x/{Z.HOW_TO}").startswith(b"another folder")


# -- one zip per project --
def test_an_uploaded_project_becomes_its_zip_and_its_upload_still_counts(root):
    p = _project(root, "uploaded")
    folder = p.folder
    out = A.tidy(root, now=NOW)
    z = root / f"{Z.plain_name(folder)}.zip"
    assert any(": now only " in x for x in out), out
    assert not folder.exists() and z.is_file() and not (root / A.TRASH).exists()
    names = zipfile.ZipFile(z).namelist()
    top = Z.plain_name(folder)
    assert f"{top}/edit/1_edit.xml" in names and f"{top}/edit/extras/media/raw.mp4" in names
    assert f"{top}/edit/extras/compare.mp4" not in names                          # the check videos went first
    idx = A.read_index(root)
    assert idx[0]["folder"] == folder.name and idx[0]["zip"] == z.name and idx[0]["channel"] == "DanCuts"
    ups = A.zipped_uploads(root)
    assert len(ups) == 1 and ups[0][1] == "DanCuts"
    assert A.zip_of_gone(folder) == z


def test_not_uploaded_it_waits_20_minutes_then_becomes_its_zip(root):
    """9 Nov (your call: only the zip stays): 20 minutes after the edit, not a week."""
    young = _project(root, "young", uploaded=False, days_old=10 / 1440)
    old = _project(root, "old", uploaded=False, days_old=30 / 1440)
    A.tidy(root, now=NOW)
    assert young.folder.is_dir() and (root / f"{Z.plain_name(young.folder)}.zip").is_file()   # its zip to send
    assert (young.folder / "edit" / "extras" / "compare.mp4").is_file()                        # nothing changed
    assert not old.folder.exists() and (root / f"{Z.plain_name(old.folder)}.zip").is_file()
    assert A.zipped_uploads(root) == []


def test_a_project_made_before_keeps_its_zip_inside_until_it_is_finished(root):
    p = _project(root, "before", uploaded=False, days_old=10 / 1440)
    old = Z.old_zip_path(p.folder)
    with zipfile.ZipFile(old, "w") as zf:
        zf.writestr("x.txt", "x")
        zf.comment = Z.MARK
    A.tidy(root, now=NOW)
    assert old.is_file() and not Z.zip_path(p.folder).exists() and p.folder.is_dir()


def test_a_file_the_zip_does_not_hold_keeps_the_folder(root):
    p = _project(root, "rar inside")
    (p.folder / "from my friend.rar").write_bytes(b"rar")
    out = A.tidy(root, now=NOW)
    assert p.folder.is_dir() and (p.folder / "from my friend.rar").is_file()
    assert any("would be lost" in x for x in out), out
    assert A.read_index(root) == []


def test_a_folder_in_use_stays_and_goes_in_a_later_round(root, monkeypatch):
    p = _project(root, "in use")
    real = os.replace

    def busy(a, b):
        if Path(a) == p.folder:
            raise PermissionError(13, "in use")
        return real(a, b)
    monkeypatch.setattr(A.os, "replace", busy)
    out = A.tidy(root, now=NOW)
    assert p.folder.is_dir() and any("in use" in x for x in out) and A.read_index(root) == []
    monkeypatch.setattr(A.os, "replace", real)
    A.tidy(root, now=NOW)
    assert not p.folder.exists() and len(A.read_index(root)) == 1


def test_unfinished_failed_or_with_topaz_projects_are_left_alone(root):
    running = _project(root, "running", state=PJ.RUNNING)
    failed = _project(root, "failed", result="FAIL", state=PJ.FAILED, uploaded=False, days_old=30)
    topaz = _project(root, "topaz")
    topaz.update(enhance={"state": "sent"})
    A.tidy(root, now=NOW)
    for p in (running, failed, topaz):
        assert p.folder.is_dir()


def test_an_uploaded_fail_you_fixed_by_hand_becomes_its_zip_too(root):
    p = _project(root, "fixed by hand", result="FAIL", state=PJ.FAILED)
    A.tidy(root, now=NOW)
    assert not p.folder.exists() and (root / f"{Z.plain_name(p.folder)}.zip").is_file()


def test_switched_off_the_folders_stay(root):
    p = _project(root, "off")
    A.tidy(root, now=NOW, one_zip=False)
    assert p.folder.is_dir() and Z.zip_path(p.folder).is_file()


def test_the_round_stops_when_this_computer_makes_a_video(root):
    a, b = _project(root, "a"), _project(root, "b")
    A.tidy(root, now=NOW, stop=lambda: not a.folder.exists())    # an edit started once the first one was done
    assert not a.folder.exists() and b.folder.is_dir()


# -- only the scene: what leaves the folder --
def test_the_raw_nothing_plays_leaves_the_folder_but_never_the_played_one_or_the_competitor(tmp_path):
    p = PJ.create(tmp_path / "p", name="raw")
    media = p.folder / "edit" / "extras" / "media"
    played = _big(media / "raw.mov")
    _xml(p.folder / "edit" / "1_edit.xml", [played])
    down = _big(p.folder / "The Office S03E02.mp4")
    conv = _big(p.folder / "The Office S03E02.converted.mp4")
    comp = _big(p.folder / "competitor.mp4")
    small = p.folder / "small.mp4"
    small.write_bytes(b"s" * 10)
    enh = p.folder / "enhance" / "raw_enhanced.mp4"
    enh.parent.mkdir()
    try:
        os.link(played, enh)                               # a hard link of what the edit plays: it costs nothing
    except OSError:
        enh = None
    p.update(competitor={"file": "competitor.mp4"})
    gone = A.drop_unplayed(p.folder, PJ.load(p.folder).data)
    assert sorted(gone) == sorted([down.name, conv.name])
    assert played.is_file() and comp.is_file() and small.is_file() and (enh is None or enh.is_file())
    assert set(PJ.load(p.folder).data["raw"]["removed"]) == {down.name, conv.name}


def test_without_an_edit_nothing_leaves(tmp_path):
    p = PJ.create(tmp_path / "p", name="noedit")
    down = _big(p.folder / "movie.mp4")
    assert A.drop_unplayed(p.folder, {}) == [] and down.is_file()


def test_the_check_videos_go(tmp_path):
    run = tmp_path / "proj" / "edit"
    (run / "extras" / "debug").mkdir(parents=True)
    (run / "1_edit.xml").write_text("<xmeml/>", encoding="utf-8")
    for n in ("preview_recreation.mp4", "compare.mp4", "debug/recreation_check.mp4"):
        (run / "extras" / n).write_bytes(b"v" * 50)
    (run / "extras" / "report.md").write_text("r", encoding="utf-8")
    assert A.drop_check_videos(tmp_path / "proj") == 150
    assert not any((run / "extras" / n).exists() for n in ("preview_recreation.mp4", "compare.mp4"))
    assert (run / "extras" / "report.md").is_file()


# -- a new edit plays only its scene --
def test_the_tool_is_asked_for_the_scene_plus_minus_10_s(monkeypatch):
    monkeypatch.setenv(RN.SCENE_ENV, "1")
    env = RN.scene_env()
    assert env["MATCH_CUTS_KEEP_PIECE"] == "1" and env["MATCH_CUTS_KEEP_MARGIN_S"] == "10"
    assert env["MATCH_CUTS_KEEP_MIN_RAW_S"] == "120"
    monkeypatch.setenv(RN.SCENE_ENV, "0")
    assert RN.scene_env() == {}


def test_the_tool_reads_the_apps_switches(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))
    K = pytest.importorskip("match_cuts.keep_piece")
    seen = {}
    monkeypatch.setattr(K, "keep_run", lambda run, sources, **kw: seen.update(kw) or {"kept": False})
    cfg = type("C", (), {"premiere": True, "deliver": "x", "raw": None})()
    ctx = type("X", (), {"cfg": cfg})()
    monkeypatch.setenv("MATCH_CUTS_KEEP_PIECE", "1")
    monkeypatch.setenv("MATCH_CUTS_KEEP_MARGIN_S", "10")
    monkeypatch.setenv("MATCH_CUTS_KEEP_MIN_RAW_S", "120")
    monkeypatch.setenv("MATCH_CUTS_KEEP_SHARE", "0.95")
    K.finish(ctx)
    assert seen == {"margin_s": 10.0, "share": 0.95, "min_raw_s": 120.0}
    assert K._span(10) == "10 s" and K._span(600) == "10 min"


# -- the whole round on a real movie (ffmpeg) --
def _ffmpeg() -> str:
    try:
        from match_cuts.common import ffmpeg_bin
        return ffmpeg_bin()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def test_a_real_movie_keeps_only_its_scene_then_becomes_one_zip(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))
    K = pytest.importorskip("match_cuts.keep_piece")
    pytest.importorskip("av")                              # (the tool's own video reader)
    pytest.importorskip("cv2")
    from match_cuts import export_xml_edl
    monkeypatch.setenv("MOVIERECAPS_ZIP_ANY_DRIVE", "1")
    monkeypatch.setattr(Z, "MIN_FREE", 0)
    monkeypatch.setattr(A, "KEEP_MIN_RAW_S", 0.0)          # (a one-minute movie stands for an episode)
    monkeypatch.setattr(export_xml_edl, "premiere_item_problems", lambda p: [])
    root = tmp_path / "Shared" / "Projects"
    p = _project(root, "real movie")
    media = p.folder / "edit" / "extras" / "media"
    (media / "raw.mp4").unlink()
    try:
        subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
                        "-t", "60", "-c:v", "libx264", "-g", "24", "-keyint_min", "24", "-sc_threshold", "0",
                        "-pix_fmt", "yuv420p", str(media / "raw.mp4")], check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no ffmpeg")
    raw = media / "raw.mp4"
    clip = (f"<clipitem id=\"clipitem-1\"><name>raw.mp4</name><rate><timebase>24</timebase><ntsc>FALSE</ntsc></rate>"
            f"<duration>1440</duration><start>0</start><end>48</end><in>600</in><out>648</out>"
            f"<file id=\"file-raw\"><name>raw.mp4</name><pathurl>{export_xml_edl._file_url(str(raw))}</pathurl>"
            f"<rate><timebase>24</timebase><ntsc>FALSE</ntsc></rate><duration>1440</duration></file></clipitem>")
    (p.folder / "edit" / "1_edit.xml").write_text(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE xmeml>\n<xmeml version=\"5\"><sequence><rate>"
        "<timebase>24</timebase><ntsc>FALSE</ntsc></rate><duration>48</duration><media><video><track>"
        f"{clip}</track></video></media></sequence></xmeml>\n", encoding="utf-8")
    whole = raw.stat().st_size
    out = A.tidy(root, now=NOW)
    z = root / f"{Z.plain_name(p.folder)}.zip"
    assert z.is_file() and not p.folder.exists(), out
    top = Z.plain_name(p.folder)
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        piece = zf.getinfo(f"{top}/edit/extras/media/raw.mov")
        xml = zf.read(f"{top}/edit/1_edit.xml").decode("utf-8")
        info = json.loads(zf.read(f"{top}/edit/extras/keep_piece.json"))
    assert f"{top}/edit/extras/media/raw.mp4" not in names                             # the whole movie went
    assert piece.file_size < whole / 2                                                  # 600-648 +- 10 s of 60 s
    g = info["groups"][0]
    assert g["k0"] <= 600 - 240 and g["k1"] >= 648 + 240 and "raw.mov" in xml
    assert f"<in>{600 - g['k0'] + g['offset']}</in>" in xml


def test_a_zip_made_earlier_is_made_again_when_project_json_changed(root):
    p = _project(root, "earlier", uploaded=False, days_old=10 / 1440)
    A.tidy(root, now=NOW)                                 # in the works: its zip to send
    z = Z.zip_path(p.folder)
    assert z.is_file() and p.folder.is_dir()

    def uploaded(d):
        d["board"] = {"uploaded": {"at": NOW.isoformat(timespec="seconds"), "how": "auto"}, "column": "uploaded"}
        d["title"] = "a much longer title than before, so project.json is not the same size"
    PJ.load(p.folder).change(uploaded)
    out = A.tidy(root, now=NOW)                           # uploaded: slimmed (project.json changes), zip made again
    assert not p.folder.exists() and z.is_file(), out
    inside = json.loads(zipfile.ZipFile(z).read(f"{Z.plain_name(p.folder)}/project.json"))
    assert inside["board"]["uploaded"] and inside.get("slim")


def test_a_project_you_unzip_again_is_not_zipped_away_under_your_hands(root):
    p = _project(root, "unzip me")
    folder = p.folder
    A.tidy(root, now=NOW)
    z = root / f"{Z.plain_name(folder)}.zip"
    assert not folder.exists() and z.is_file()
    with zipfile.ZipFile(z) as zf:                        # you unzip it into the shared folder to work on it
        zf.extractall(root)
    back = root / Z.plain_name(folder)
    assert back == folder and (back / "project.json").is_file()
    A.tidy(root, now=datetime.now().astimezone())
    assert back.is_dir()                                  # left alone for a week
    A.tidy(root, now=datetime.now().astimezone() + timedelta(days=8))
    assert not back.exists() and z.is_file()              # then only the zip again


def test_keeping_the_whole_raw_its_zip_holds_it_too(root):
    p = _project(root, "whole raw")
    down = _big(p.folder / "The Office S03E02.mp4")
    out = A.tidy(root, now=NOW, scene_only=False)
    z = root / f"{Z.plain_name(p.folder)}.zip"
    assert not p.folder.exists() and z.is_file(), out
    assert f"{Z.plain_name(p.folder)}/{down.name}" in zipfile.ZipFile(z).namelist()
