"""Day 13, section 4: file names = the video's title (core/names.py, core/renames.py, premiere.py's named copy).

* the name rules: Windows-safe (\\ / : * ? " < > | gone, emoji kept, no space or dot at the end, CON / NUL ...),
  100 characters at most, cut at a word and never inside an emoji, short enough for Windows' path limit; (2), (3);
* the choice: the YouTube title, else the headline (its two lines joined), else the post's title, else the headline
  on the competitor's video;
* the edit: <title>_edit.xml next to 1_edit.xml with the sequence named after the title (1_edit.xml untouched),
  <title>.prproj made next to it, the app's own project never taken for your fix;
* renaming later with everything linked (project.json, the shared copy, 1_edit.xml's media paths, the Learn waiting
  card, the projects waiting for the shared folder, problems_queue.md), waiting while something uses the folder,
  keeping the name when a Premiere project plays its files; Learn still finds the run.

Run: python -m pytest tools/app/tests/test_names_day13.py"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
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
from mr_app.core import waiting as WT  # noqa: E402

TITLE = "The fact that this ad sounds so wrong is hilarious 📻😭"
REAL_ROOT_LEN = NM.root_len                     # (the tests below plan names for the usual projects folder)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch, tmp_path):
    """No real Premiere, no real Desktop / recent projects, the app's small files in the test's folder; the names
    planned for the usual projects folder (the test's own folder is ~175 characters long: names would be cut for
    Windows' path limit -- that rule has its own test)."""
    monkeypatch.setattr(PM, "state", lambda: PM.State())
    monkeypatch.setattr(RN, "scan_places", lambda hub: [])
    monkeypatch.setattr(NM, "root_len", lambda *roots: NM.ROOT_LEN)
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    RN.BUSY.clear()
    yield
    RN.BUSY.clear()


# ---------------------------------------------------------------------------------------------------------------------
# the name rules
# ---------------------------------------------------------------------------------------------------------------------

def test_forbidden_characters_go_and_emoji_stay():
    s = NM.safe_name('Spider-Man: No Way Home? "AC/DC" <live> | *best* \\ end 🕷️😭')
    assert not any(ch in s for ch in '\\/:*?"<>|')
    assert s == "Spider-Man No Way Home AC DC live best end 🕷️😭"
    assert NM.safe_name("Tobey\tMaguire\nhad one job  🍫") == "Tobey Maguire had one job 🍫"
    assert NM.safe_name("bad\x00control\x1fchars\u200b\u202e") == "badcontrolchars"


def test_no_space_or_dot_at_the_end_and_nothing_left_is_empty():
    assert NM.safe_name("  the end...  ") == "the end"
    assert NM.safe_name("wait . . .") == "wait"
    assert NM.safe_name(" ... ") == "" and NM.safe_name("???") == "" and NM.safe_name(None) == ""


def test_windows_device_names_are_never_a_name():
    for dev in ("CON", "con", "NUL", "Aux", "PRN", "COM1", "lpt9", "COM¹"):
        out = NM.safe_name(dev)
        assert out == dev + "_", out
    assert NM.safe_name("Con. Air") == "Con_. Air"                   # (the part before the first dot)
    assert NM.safe_name("Console wars") == "Console wars"           # only the whole word is a device
    assert NM.safe_name("NUL.txt") == "NUL_.txt"


def test_at_most_100_characters_cut_at_a_word_never_inside_an_emoji():
    long = " ".join(["word"] * 40) + " 😭"
    s = NM.safe_name(long)
    assert len(s) <= 100 and s.endswith("word") and not s.endswith(" ")
    emo = "a" * 98 + "👨‍👩‍👧"                                         # a family emoji is 5 code points
    s2 = NM.safe_name(emo)
    assert len(s2) <= 100 and "\u200d" not in s2[-1:] and (s2 == "a" * 98 or s2.endswith("👧"))
    accent = "x" * 99 + "e\u0301"                                       # 'e' + a combining accent -> NFC é
    assert NM.safe_name(accent).endswith("é")


def test_names_fit_windows_path_limit_with_emoji_counted_twice():
    long = "Before Tobey Maguire Was Spider-Man He Had One Job And It Was Hilarious To Watch Every Time 🕷️📺😭"
    t = NM.fit(long)
    assert len(t) <= NM.MAX_LEN
    root = "C:\\Users\\gvida\\Desktop\\bh\\MovieRecaps\\input\\downloads"
    folder = f"{root}\\2026-10-20_{t} (99)"
    assert NM.utf16_len(f"{folder}\\edit-99\\{t}_edit.xml") <= NM.PATH_LIMIT
    assert NM.utf16_len(f"{folder}\\edit-99\\extras\\debug\\verify_failures\\k00544.png") <= NM.PATH_LIMIT
    assert NM.fit("Short title 😭") == "Short title 😭"                  # a usual title is kept whole
    assert NM.sequence_name(long) == NM.safe_name(long)                # the sequence: not cut for paths


def test_a_long_projects_folder_makes_the_names_shorter():
    real = REAL_ROOT_LEN
    deep = "C:\\" + "\\".join(["folder"] * 20)                          # 141 characters
    n = real(deep)
    assert n == len(deep) and real("D:\\Shared\\Projects") == NM.ROOT_LEN
    t = NM.fit("They stopped making comedy like this 😭", n)
    assert t and len(t) < len("They stopped making comedy like this 😭")
    assert NM.utf16_len(f"{deep}\\2026-10-20_{t} (99)\\edit-99\\{t}_edit.xml") <= NM.PATH_LIMIT


def test_the_choice_youtube_title_then_headline_then_post_then_video():
    d = {"upload_title": "My YouTube title 🎥", "title": "line one\nline two",
         "post_title": "Where the Nestle Crunch meme came from", "video_headline": "On video"}
    assert NM.title_of(d) == ("My YouTube title 🎥", "youtube")
    d["upload_title"] = "  "
    assert NM.title_of(d) == ("line one line two", "headline")         # both lines joined by a space
    d["title"] = ""
    assert NM.title_of(d) == ("Where the Nestle Crunch meme came from", "post")
    d["post_title"] = "Video by darkhumor"                             # says nothing: skipped
    assert NM.title_of(d) == ("On video", "video")
    assert NM.title_of({}) == ("", "")
    assert NM.display_name({"name": "2026-10-19_video-by-x"}) == "2026-10-19_video-by-x"


def test_a_taken_name_gets_2_and_3(tmp_path):
    base = f"2026-10-20_{TITLE}"
    assert NM.unique_folder(tmp_path, base) == tmp_path / base
    (tmp_path / base).mkdir()
    assert NM.unique_folder(tmp_path, base) == tmp_path / f"{base} (2)"
    (tmp_path / f"{base} (2)").mkdir()
    assert NM.unique_folder(tmp_path, base) == tmp_path / f"{base} (3)"
    assert NM.unique_folder(tmp_path, base, current=tmp_path / base) == tmp_path / base     # its own name
    assert NM.has_base(f"{base} (2)", base) and NM.has_base(base, base) and not NM.has_base(f"{base} x", base)


def test_a_new_project_is_named_after_its_title_with_2_when_taken(tmp_path):
    base = NM.folder_base(TITLE, "2026-10-20")
    a = P.create(tmp_path, competitor_url="https://youtu.be/AbCdEf12345", folder=base, names=NM.marker("headline"))
    b = P.create(tmp_path, competitor_url="https://youtu.be/AbCdEf12345", folder=base, names=NM.marker("headline"))
    assert a.folder.name == base and b.folder.name == f"{base} (2)"
    assert a.data["name"] == base and NM.uses_titles(a.data) and a.data["names"]["from"] == "headline"
    old = P.create(tmp_path, competitor_url="https://youtu.be/AbCdEf12345", name="Some Title")
    assert old.folder.name.endswith("_some-title") and not NM.uses_titles(old.data)       # the old way, unmarked


# ---------------------------------------------------------------------------------------------------------------------
# the edit: <title>_edit.xml, <title>.prproj
# ---------------------------------------------------------------------------------------------------------------------

def edit_xml(run: Path, media_file: str = "raw.mp4") -> Path:
    """A 1_edit.xml as the tool writes it: the sequence "Recreated Edit (Premiere)", V1 playing the run's media."""
    media = run / "extras" / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / media_file).write_bytes(b"r" * 100)
    (media / "frame.png").write_bytes(b"p" * 10)
    url = PB.file_url(str(media / media_file))
    furl = PB.file_url(str(media / "frame.png"))
    text = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5">\n'
            '  <sequence id="sequence-1">\n    <name>Recreated Edit (Premiere)</name>\n    <duration>30</duration>\n'
            '    <media><video><track><clipitem id="c1"><name>raw.mp4</name><file id="file-raw"><name>raw.mp4</name>'
            f'<pathurl>{url}</pathurl></file></clipitem></track><track><clipitem id="f1"><name>frame.png</name>'
            f'<file id="file-frame"><pathurl>{furl}</pathurl></file></clipitem></track></video></media>\n'
            '  </sequence>\n</xmeml>\n')
    x = run / "1_edit.xml"
    x.write_text(text, encoding="utf-8")
    (run / "extras" / "report.md").write_text("# report\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(x, (old, old))
    return x


def title_project(root: Path, title: str = TITLE, *, upload: str = "", edit: bool = True) -> P.Project:
    base = NM.folder_base(title, "2026-10-20")
    p = P.create(root, competitor_url="https://youtu.be/AbCdEf12345", folder=base, names=NM.marker("headline"))
    p.update(title=title, upload_title=upload or None, channel="LouLaughs")
    if edit:
        edit_xml(p.folder / "edit")
        p.set_step("edit", P.DONE, result="PASS", run_dir=str(p.folder / "edit"))
    return p


def test_the_edit_copy_is_named_after_the_title_with_the_sequence_renamed(tmp_path):
    p = title_project(tmp_path / "local")
    xml = p.folder / "edit" / "1_edit.xml"
    before = xml.read_bytes()
    out = PM.named_copy(xml)
    assert out.name == f"{NM.fit(TITLE)}_edit.xml"
    assert PM.sequence_name_in(out.read_text(encoding="utf-8")) == NM.sequence_name(TITLE)
    assert xml.read_bytes() == before                                  # 1_edit.xml exactly as it was
    assert out.read_bytes().replace(NM.sequence_name(TITLE).encode("utf-8"), b"Recreated Edit (Premiere)") == \
        before                                                         # every other byte the same
    assert abs(out.stat().st_mtime - xml.stat().st_mtime) < 0.01       # the edit's time (Open in Premiere compares)
    assert PM.project_for(out).name == f"{NM.fit(TITLE)}.prproj"
    assert PM.named_copy(xml) == out                                   # made once


def test_a_sequence_name_with_an_ampersand_is_escaped_and_reads_back(tmp_path):
    p = title_project(tmp_path / "local", title="Tom & Jerry's best 😂")
    out = PM.named_copy(p.folder / "edit" / "1_edit.xml")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(out.read_bytes())
    assert root.find("sequence/name").text == "Tom & Jerry's best 😂"


def test_an_old_project_keeps_night_10s_names(tmp_path):
    p = P.create(tmp_path, competitor_url="https://youtu.be/AbCdEf12345", name="A lot to catch up on")
    xml = edit_xml(p.folder / "edit")
    out = PM.named_copy(xml)
    assert out.name == f"{p.folder.name}_edit.xml" and out.read_bytes() == xml.read_bytes()
    assert PM.project_for(out).name == f"{p.folder.name}_edit.prproj"


def test_a_finished_edit_gets_its_title_copy_at_once(tmp_path):
    from mr_app.core import flow
    p = title_project(tmp_path / "local", edit=False)
    edit_xml(p.folder / "edit")
    flow.record_edit(p, {"label": "PASS", "run_dir": str(p.folder / "edit")})
    assert (p.folder / "edit" / f"{NM.fit(TITLE)}_edit.xml").is_file()
    old = P.create(tmp_path, competitor_url="https://youtu.be/AbCdEf12345", name="old one")
    edit_xml(old.folder / "edit")
    flow.record_edit(old, {"label": "PASS", "run_dir": str(old.folder / "edit")})
    assert [f.name for f in (old.folder / "edit").glob("*.xml")] == ["1_edit.xml"]          # nothing new there


def saved(path: Path, sequence: str = TITLE) -> Path:
    """A .prproj saved with a sequence in it (what holds_edit looks for)."""
    path.write_bytes(gzip.compress(f'<PremiereData><Sequence ObjectUID="1"><Name>{sequence}</Name></Sequence>'
                                   '</PremiereData>'.encode("utf-8")))
    return path


def test_the_apps_own_title_project_is_never_taken_for_your_fix(tmp_path):
    p = title_project(tmp_path / "local", title="Finally fixed 😭")       # the title even ends with "fixed"
    out = PM.named_copy(p.folder / "edit" / "1_edit.xml")
    own = PM.project_for(out)
    assert own.name == "Finally fixed 😭.prproj"
    saved(own)
    assert PM.is_own_project(own)
    st = own.stat()
    now = st.st_mtime + 60
    assert not WT.is_fix(own, finished=st.st_mtime - 100, now=now)       # saved at once: not a fix yet
    made = getattr(st, "st_birthtime", None) or st.st_ctime
    os.utime(own, (made + WT.OWN_PROJECT_S + 5, made + WT.OWN_PROJECT_S + 5))
    assert WT.is_fix(own, finished=made - 100, now=made + WT.OWN_PROJECT_S + 60)   # saved 10 min later: a fix
    assert B._fixed_prproj(p.folder) is None                              # not "Fixed" on the board either
    mine = saved(p.folder / "edit" / "cop fixed.prproj")
    assert not PM.is_own_project(mine) and B._fixed_prproj(p.folder) == mine
    assert PM.saved_own_project(p.folder / "edit") == own                 # Open in Premiere opens it again


# ---------------------------------------------------------------------------------------------------------------------
# renaming later
# ---------------------------------------------------------------------------------------------------------------------

def published(p: P.Project, hub: Path) -> Path:
    res = PB.publish(p.folder, hub)
    return res.dest


def test_a_new_youtube_title_renames_both_copies_with_everything_linked(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    hub.mkdir()
    p = title_project(local)
    PM.named_copy(p.folder / "edit" / "1_edit.xml")                      # <title>_edit.xml as the edit's end made it
    dest = published(p, hub)
    assert dest == hub / p.folder.name
    old_local, old_hub = p.folder, dest
    WT.add(hub, WT.Entry(name=old_local.name, project=str(old_local), run=str(old_local / "edit"),
                         run_in_project="edit", channel="LouLaughs", result="PASS"))
    PB.queue_pending(local, old_local, hub, "the PC was asleep")
    (hub / "problems_queue.md").write_text(f"- [ ] **{old_hub.name}** · OK with a note: x\n  - run: {old_hub}\\edit\n"
                                           f"  - log: {old_hub}\\edit\\extras\\batch_console.log\n"
                                           f"  - other: {old_hub}-2\\edit\n", encoding="utf-8")
    B.set_upload_title(old_local, "Nobody expected this radio ad 📻")
    res = RN.rename(old_local, hub=hub, local_root=local)
    assert res.state == RN.RENAMED, res.text
    new_name = "2026-10-20_Nobody expected this radio ad 📻"
    new_local, new_hub = local / new_name, hub / new_name
    assert res.folder == new_local and not old_local.exists() and not old_hub.exists()
    assert new_local.is_dir() and new_hub.is_dir()
    assert {(o.name, n.name) for o, n in res.moved} == {(old_local.name, new_name)}
    dl, dh = P.load(new_local).data, P.load(new_hub).data
    for d in (dl, dh):
        assert d["name"] == new_name and old_local.name in d["old_names"]
        assert d["names"]["from"] == "youtube" and "kept" not in d["names"]
    assert dl["published"]["to"] == str(new_hub)                          # the twin's new name
    assert Path(dl["steps"]["edit"]["run_dir"]) == new_local / "edit"
    for run in (new_local / "edit", new_hub / "edit"):
        x = (run / "1_edit.xml").read_text(encoding="utf-8")
        assert PM.sequence_name_in(x) == "Recreated Edit (Premiere)"     # 1_edit.xml keeps its name and sequence
        assert PB.file_url(str(run / "extras" / "media" / "raw.mp4")) in x       # its media: the new folder
        copies = sorted(f.name for f in run.glob("*.xml"))
        assert copies == ["1_edit.xml", "Nobody expected this radio ad 📻_edit.xml"]       # the old copy gone
        assert PM.sequence_name_in((run / copies[1]).read_text(encoding="utf-8")) == "Nobody expected this radio ad 📻"
    e = WT.read(hub)[0]
    assert Path(e.project) == new_local and Path(e.run) == new_local / "edit" and e.name == new_name
    assert Path(PB.pending(local)[0]["folder"]) == new_local
    pq = (hub / "problems_queue.md").read_text(encoding="utf-8")
    assert f"**{new_name}**" in pq and f"{new_hub}\\edit\\extras\\batch_console.log" in pq
    assert f"{old_hub}-2\\edit" in pq                                    # another project's path: untouched
    assert B.card_of(new_local).rename_to == "" and B.card_of(new_local).display == "Nobody expected this radio ad 📻"


def test_the_other_computer_publishes_into_the_renamed_shared_copy(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    hub.mkdir()
    p = title_project(local)
    dest = published(p, hub)
    renamed_hub = hub / "2026-10-20_A new name"
    os.rename(dest, renamed_hub)                                         # (renamed on the other computer)
    P.load(renamed_hub).update(old_names=[dest.name], name=renamed_hub.name)
    assert PB.destination(p.folder, hub) == renamed_hub
    cards = B.load_cards(hub, local)
    assert len(cards) == 1 and cards[0].folder == renamed_hub            # shown once, not twice


def test_it_waits_while_something_uses_the_folder_then_renames(tmp_path):
    from mr_app.core import locks
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = title_project(local)
    B.set_upload_title(p.folder, "Renamed later 😂")
    lock = locks.WorkLock(p.folder, "edit")
    lock.acquire()
    try:
        res = RN.request(p.folder, hub=hub, local_root=local)
        assert res.state == RN.WAIT and "working on it" in res.text and p.folder.is_dir()
        assert RN.waiting_for(p.folder) and len(RN.pending()) == 1
        assert RN.retry_pending(hub=hub, local_root=local)[0].state == RN.WAIT
    finally:
        lock.release()
    out = RN.retry_pending(hub=hub, local_root=local)
    assert out[0].state == RN.RENAMED and RN.pending() == []
    assert (local / "2026-10-20_Renamed later 😂").is_dir()


def test_a_row_of_the_app_or_premiere_using_it_makes_it_wait(tmp_path, monkeypatch):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = title_project(local)
    B.set_upload_title(p.folder, "Another title")

    class Page:
        def uses(self, folder):
            return "it waits in the Match cuts queue" if Path(folder) == p.folder else ""
    page = Page()
    RN.register_busy(page.uses)
    assert RN.rename(p.folder, hub=hub, local_root=local).state == RN.WAIT
    assert RN.rename(p.folder, hub=hub, local_root=local, busy=False).state == RN.RENAMED    # the row asks itself
    q = local / "2026-10-20_Another title"
    B.set_upload_title(q, "Third title")
    RN.unregister_busy(page.uses)
    own = q / "edit" / "Another title.prproj"
    monkeypatch.setattr(PM, "state", lambda: PM.State(running=True, title=f"Adobe Premiere Pro 2024 - {own}",
                                                      project=own))
    res = RN.rename(q, hub=hub, local_root=local)
    assert res.state == RN.WAIT and "Premiere has" in res.text


def test_a_rename_that_windows_refuses_waits(tmp_path, monkeypatch):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = title_project(local, edit=False)
    B.set_upload_title(p.folder, "Blocked title")

    def refuse(a, b):
        raise PermissionError(32, "The process cannot access the file because it is being used by another process")
    monkeypatch.setattr(RN.os, "rename", refuse)
    res = RN.rename(p.folder, hub=hub, local_root=local)
    assert res.state == RN.WAIT and "open in another program" in res.text and p.folder.is_dir()


def test_a_premiere_project_that_plays_its_files_keeps_the_name(tmp_path, monkeypatch):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = title_project(local)
    B.set_upload_title(p.folder, "Kept because of my project")
    desktop = tmp_path / "Desktop" / "cop.prproj"                        # plays the RAW from the folder by its path
    desktop.parent.mkdir(exist_ok=True)
    desktop.write_bytes(gzip.compress(
        f'<PremiereData><Media><ActualMediaFilePath>{p.folder}\\edit\\extras\\media\\raw.mp4</ActualMediaFilePath>'
        '</Media></PremiereData>'.encode("utf-8")))
    monkeypatch.setattr(RN, "scan_places", lambda hub: [desktop])
    res = RN.rename(p.folder, hub=hub, local_root=local)
    assert res.state == RN.KEPT and "cop.prproj" in res.text and p.folder.is_dir()
    d = P.load(p.folder).data
    assert d["names"]["kept"]["for"] == "2026-10-20_Kept because of my project"
    c = B.card_of(p.folder)
    assert c.rename_to == "" and "cop.prproj" in c.kept                  # no Rename to title button for it
    copies = sorted(f.name for f in (p.folder / "edit").glob("*.xml"))
    assert "Kept because of my project_edit.xml" in copies               # the app's own copy follows the title


def test_a_saved_project_in_the_folder_or_open_in_premiere_before_keeps_the_name(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = title_project(local)
    out = PM.named_copy(p.folder / "edit" / "1_edit.xml")
    own = saved(PM.project_for(out))                                     # you saved the app's project with the edit
    B.set_upload_title(p.folder, "A later title")
    res = RN.rename(p.folder, hub=hub, local_root=local)
    assert res.state == RN.KEPT and own.name in res.text
    assert own.is_file() and out.is_file()                               # your project and its copy stay as they are
    q = title_project(local, title="Another video")
    RN.note_premiere(q.folder / "edit" / "1_edit.xml")
    assert P.load(q.folder).data["premiere"]["run"] == "edit"
    B.set_upload_title(q.folder, "Its new title")
    assert RN.rename(q.folder, hub=hub, local_root=local).state == RN.KEPT


def test_an_empty_own_project_is_renamed_with_the_folder(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = title_project(local, edit=False)                                  # no edit yet: nothing can play its files
    edit_xml(p.folder / "edit")
    out = PM.named_copy(p.folder / "edit" / "1_edit.xml")
    PM.project_for(out).write_bytes(PM.template().read_bytes() if PM.template().is_file() else b"\x1f\x8b")
    os.utime(PM.project_for(out), (time.time() - 3600, time.time() - 3600))
    res = RN.sync_run_files(p.folder / "edit", P.load(p.folder).data)
    assert res == []                                                       # already right
    P.load(p.folder).update(title="Shiny new headline")
    d = P.load(p.folder).data
    done = RN.sync_run_files(p.folder / "edit", d)
    names = sorted(f.name for f in (p.folder / "edit").iterdir() if f.is_file())
    assert "Shiny new headline_edit.xml" in names and "Shiny new headline.prproj" in names
    assert f"{NM.fit(TITLE)}_edit.xml" not in names and done


def test_old_projects_are_renamed_only_by_rename_to_title(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = P.create(local, competitor_url="https://youtu.be/AbCdEf12345", name="video by mindculpt")
    p.update(title="The fact that he\nsaid it 😭")
    assert RN.rename(p.folder, hub=hub, local_root=local).state == RN.OLD
    c = B.card_of(p.folder)
    assert c.display == "The fact that he said it 😭" and c.rename_to.endswith("_The fact that he said it 😭")
    res = RN.rename(p.folder, hub=hub, local_root=local, explicit=True)
    assert res.state == RN.RENAMED and res.folder.name.endswith("_The fact that he said it 😭")
    assert NM.uses_titles(P.load(res.folder).data)                        # from now on it follows its title


def test_no_title_keeps_the_name(tmp_path):
    p = P.create(tmp_path / "local", competitor_url="https://youtu.be/AbCdEf12345", names=NM.marker(""))
    assert RN.rename(p.folder, hub=tmp_path / "hub", local_root=tmp_path / "local").state == RN.NO_TITLE


def test_scanning_a_project_reads_its_folder_names_once(tmp_path):
    f = tmp_path / "x.prproj"
    f.write_bytes(gzip.compress(b"<P><FilePath>D:\\Shared\\Projects\\2026-10-19_video-by-mindculpt\\edit\\extras"
                                b"\\media\\raw_stereo.mp4</FilePath><FilePath>\\\\192.168.101.11\\Shared\\x\\y.mp4"
                                b"</FilePath></P>"))
    got = RN.folder_names_in(f)
    assert "2026-10-19_video-by-mindculpt" in got and "edit" in got and "raw_stereo.mp4" not in got
    assert RN.folder_names_in(f) is got                                  # read once while the file is the same


# ---------------------------------------------------------------------------------------------------------------------
# Learn still finds the run from a project made from <title>_edit.xml
# ---------------------------------------------------------------------------------------------------------------------

def test_learn_finds_the_run_of_a_project_made_from_the_title_copy(tmp_path):
    import test_learn_core as LC
    from mr_app.core import learnkit as K
    root = tmp_path / "Projects"
    proj = root / NM.folder_base(TITLE, "2026-10-20")
    run = LC.fake_run(proj / "edit")
    (proj / "project.json").write_text(json.dumps({"name": proj.name, "created": P.now(), "title": TITLE,
                                                   "names": NM.marker("headline")}), encoding="utf-8")
    x = run / "1_edit.xml"
    x.write_text(x.read_text(encoding="utf-8").replace(
        "<sequence>", "<sequence id='sequence-1'><name>Recreated Edit (Premiere)</name>", 1), encoding="utf-8")
    copy = PM.named_copy(x)
    assert copy.name == f"{NM.fit(TITLE)}_edit.xml"
    # the project Premiere makes from it: <title>.prproj next to it, its sequence called after the title, the media
    # in the run's extras\media
    pr = LC.fake_prproj(PM.project_for(copy), [{"path": str(run / "extras" / "media" / "raw.mp4"), **LC.RAW}],
                        [{"name": NM.sequence_name(TITLE), "fps": 30.0,
                          "video": [[(0.0, 5.0, 0, 1.0), (5.0, 9.0, 0, 8.0)]],
                          "audio": [[(0.0, 5.0, 0, 1.0), (5.0, 9.0, 0, 8.0)]],
                          "captions": [(0.0, 2.0, "chocolate is"), (2.0, 4.0, "so good")]}])
    s = K.search_run(pr, [root], repo=tmp_path)
    assert s.candidates and s.candidates[0].run_dir == run and "exact" in s.candidates[0].ways, s.problems
    assert s.candidates[0].sequence.startswith(f"'{NM.sequence_name(TITLE)}'")
    from match_cuts import learn as L
    from match_cuts import prproj as PR
    seq = PR.main_sequence(PR.read(pr))
    assert L.find_run(seq)["dir"] == run                                  # the tool's own LEARN step finds it too
