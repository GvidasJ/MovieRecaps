"""Night 15, task 1: folder and file names keep only letters, numbers, spaces and - _ ( ) (Premiere's XML import
failed -- "generic error" / "File Import Failure" -- on folders named with emoji); the folders made before are renamed
once by the start-up sweep (a folder a Premiere project plays keeps its name); the title with its emoji stays in
project.json, title.txt and the board."""
from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mr_app.core import board as B  # noqa: E402
from mr_app.core import names as NM  # noqa: E402
from mr_app.core import premiere as PM  # noqa: E402
from mr_app.core import projects as P  # noqa: E402
from mr_app.core import publish as PB  # noqa: E402
from mr_app.core import renames as RN  # noqa: E402

from test_names_day13 import edit_xml  # noqa: E402


@pytest.fixture(autouse=True)
def _quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(PM, "state", lambda: PM.State())
    monkeypatch.setattr(RN, "scan_places", lambda hub: [])
    monkeypatch.setattr(NM, "root_len", lambda *roots: NM.ROOT_LEN)
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    RN.BUSY.clear()
    yield
    RN.BUSY.clear()


@pytest.mark.parametrize("title, want", [
    ("The Most Dramatic Dad of All Time 💀😭", "The Most Dramatic Dad of All Time"),
    ("The fact that she’d go this far for some milk is hilarious 🥛😭",
     "The fact that shed go this far for some milk is hilarious"),
    ("Dr. Dolittle 2 (2001) [1080p] [YTS.MX]", "Dr Dolittle 2 (2001) 1080p YTS MX"),
    ("Ąžuolas Über Жук – é_ü", "Ąžuolas Über Жук - é_ü"),            # every language's letters stay
    ("Tom & Jerry: the end?!", "Tom and Jerry the end"),
    ("AC/DC | live 🎸", "AC DC live"),
    ("CON 😭", "CON_"),                                                # still never a Windows device name
])
def test_folder_and_file_names_keep_only_letters_numbers_spaces_and_dash_underscore_brackets(title, want):
    part = NM.fit(title)
    assert part == want
    assert NM.is_plain(part.rstrip("_")) and all(c.isalnum() or c in " -_()" for c in part)
    assert NM.folder_base(title, "2026-10-22") == f"2026-10-22_{want}"
    assert part.startswith(NM.file_stem(part)) and NM.is_plain(NM.file_stem(part).rstrip("_"))   # (cut for Premiere's
    #                                                                       Auto-Save path)


def test_the_title_itself_keeps_its_emoji_and_the_sequence_its_signs(tmp_path):
    title = "The fact that she’d go this far 🥛😭"
    p = P.create(tmp_path / "local", competitor_url="https://youtu.be/AbCdEf12345",
                 folder=NM.folder_base(title, "2026-10-22"), names=NM.marker("headline"))
    B.set_title(p.folder, title)
    assert p.folder.name == "2026-10-22_The fact that shed go this far"
    assert P.load(p.folder).data["title"] == title                       # project.json
    assert (p.folder / "title.txt").read_text(encoding="utf-8").strip() == title
    assert B.card_of(p.folder).display == title                          # the board
    assert NM.sequence_name(title) == "The fact that she’d go this far"   # Premiere's sequence / render name


def test_pathurls_are_percent_encoded_utf8_with_the_server_as_host():
    u = PB.file_url(r"D:\Shared\Projects\2026-10-22_Ąžuolas [x] (1) - é\edit\extras\media\raw.mp4")
    assert u == ("file://localhost/D%3A/Shared/Projects/2026-10-22_%C4%84%C5%BEuolas%20%5Bx%5D%20%281%29%20-%20%C3%A9"
                 "/edit/extras/media/raw.mp4")
    assert PB.url_to_path(u) == r"D:\Shared\Projects\2026-10-22_Ąžuolas [x] (1) - é\edit\extras\media\raw.mp4"
    n = PB.file_url(r"\\192.168.101.11\Shared\Projects\2026-10-22_A b\edit\x.png")
    assert n == "file://192.168.101.11/Shared/Projects/2026-10-22_A%20b/edit/x.png"
    assert urllib.parse.unquote(n).endswith("2026-10-22_A b/edit/x.png")


def _emoji_project(root: Path, title: str, day: str = "2026-10-21") -> P.Project:
    """A project made before night 15: its folder named with the title's emoji."""
    folder = f"{day}_{NM.safe_name(title)}"
    p = P.create(root, competitor_url="https://youtu.be/AbCdEf12345", folder=folder, names=NM.marker("headline"))
    p.update(title=title)
    edit_xml(p.folder / "edit")
    p.set_step("edit", P.DONE, result="PASS", run_dir=str(p.folder / "edit"))
    return p


def test_the_sweep_renames_an_emoji_folder_once_with_its_edit_relinked(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = _emoji_project(local, "The Most Dramatic Dad of All Time 💀😭")
    assert p.folder.name.endswith("💀😭") and RN.not_plain(p.folder, p.data)
    assert [r.folder for r in RN.plain_sweep(hub=hub, local_root=local, dry_run=True)] == [p.folder]
    assert p.folder.is_dir()                                             # (a dry run changes nothing)
    out = RN.plain_sweep(hub=hub, local_root=local)
    assert [r.state for r in out] == [RN.RENAMED]
    new = local / "2026-10-21_The Most Dramatic Dad of All Time"
    assert out[0].folder == new and new.is_dir() and not p.folder.exists()
    d = P.load(new).data
    assert d["title"] == "The Most Dramatic Dad of All Time 💀😭" and d[NM.KEY].get(RN.SWEEP_DONE)
    xml = (new / "edit" / "1_edit.xml").read_text(encoding="utf-8")
    assert "%F0%9F" not in xml and PB.file_url(str(new / "edit" / "extras" / "media" / "raw.mp4")) in xml
    assert d["steps"]["edit"]["run_dir"] == str(new / "edit")
    assert RN.plain_sweep(hub=hub, local_root=local) == []              # looked at once


def test_the_sweep_keeps_a_folder_a_premiere_project_plays_and_says_why(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = _emoji_project(local, "The Funniest Lab Accident of All Time 🧪😭")
    p.update(premiere={"opened": P.now(), "computer": "PC", "run": "edit"})
    out = RN.plain_sweep(hub=hub, local_root=local)
    assert [r.state for r in out] == [RN.KEPT] and "Premiere" in out[0].text and p.folder.is_dir()
    d = P.load(p.folder).data
    assert d[NM.KEY]["kept"]["why"] and d[NM.KEY].get(RN.SWEEP_DONE)
    assert RN.plain_sweep(hub=hub, local_root=local) == []              # not asked again at every start


def test_the_sweep_leaves_plain_and_old_projects_alone(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    plain = P.create(local, competitor_url="https://youtu.be/AbCdEf12345", folder="2026-10-21_Already plain",
                     names=NM.marker("headline"))
    plain.update(title="Already plain")
    old = P.create(local, competitor_url="https://youtu.be/AbCdEf12345", folder="2026-10-17_old 😭")  # no title rule
    assert RN.plain_sweep(hub=hub, local_root=local) == []
    assert plain.folder.is_dir() and old.folder.is_dir()


def test_the_sweep_waits_while_something_uses_the_folder(tmp_path):
    from mr_app.core import locks
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = _emoji_project(local, "Renamed later 😂")
    lock = locks.WorkLock(p.folder, "edit")
    lock.acquire()
    try:
        out = RN.plain_sweep(hub=hub, local_root=local)
        assert [r.state for r in out] == [RN.WAIT] and RN.pending()
    finally:
        lock.release()
    assert RN.retry_pending(hub=hub, local_root=local)[0].state == RN.RENAMED
    assert (local / "2026-10-21_Renamed later").is_dir()
