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
    assert NM.utf16_len(f"{folder}\\edit-99\\.{t}_edit.xml.123456.1234567890.tmp") <= NM.PATH_LIMIT   # its temp file
    assert NM.utf16_len(f"{folder}\\edit-99\\extras\\debug\\verify_failures\\k00544.png") <= NM.PATH_LIMIT
    assert NM.fit("Short title 😭") == "Short title 😭"                  # a usual title is kept whole
    assert NM.sequence_name(long) == NM.safe_name(NM.no_emoji(long))   # the sequence: not cut for paths (day 14:
    assert NM.sequence_name(long).endswith("Every Time")               # no emoji, Premiere shows them as boxes)


def test_a_flag_and_a_half_emoji_never_break_a_name():
    flags = "🇱🇹" * 60                                                   # each flag is two code points
    s = NM.safe_name(flags)
    assert len(s) % 2 == 0 and s == "🇱🇹" * (len(s) // 2)                # never half a flag
    assert NM.safe_name("abc\ud83d def") == "abc def"                    # a lone surrogate: dropped
    assert NM.utf16_len("abc\ud83d") == 4 and NM.fit("abc\ud83d def") == "abc def"


def test_capitals_alone_do_not_rename_a_folder():
    assert NM.has_base("2026-10-20_He couldn't stop", "2026-10-20_He Couldn't Stop")
    assert NM.has_base("2026-10-20_He couldn't stop (2)", "2026-10-20_He Couldn't Stop")


def test_paths_that_only_start_like_the_old_folder_are_never_rewritten():
    text = ("  - run: .\\2026-10-20_The Truth\\edit\n  - run: .\\2026-10-20_The Truth (2)\\edit\n"
            "  - run: .\\2026-10-20_The Truth About Cats\\edit\n  - copy: D:\\Shared\\Projects\\2026-10-20_The Truth\r\n")
    out = RN._replace_paths(text, [(".\\2026-10-20_The Truth", ".\\2026-10-20_New Name"),      # noqa: SLF001
                                   ("D:\\Shared\\Projects\\2026-10-20_The Truth",
                                    "D:\\Shared\\Projects\\2026-10-20_New Name")])
    assert ".\\2026-10-20_New Name\\edit\n" in out and "D:\\Shared\\Projects\\2026-10-20_New Name\r\n" in out
    assert ".\\2026-10-20_The Truth (2)\\edit" in out and ".\\2026-10-20_The Truth About Cats\\edit" in out


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


def test_the_edit_itself_carries_the_title_as_its_sequence_without_emoji(tmp_path):
    """Day 14: ONE XML per edit -- Open in Premiere imports 1_edit.xml itself, its sequence called after the title
    without emoji (two copies of one edit made two sequences in one project; the emoji showed as boxes)."""
    p = title_project(tmp_path / "local")
    xml = p.folder / "edit" / "1_edit.xml"
    before, t0 = xml.read_bytes(), xml.stat().st_mtime
    out = PM.edit_for_premiere(xml)
    seq = NM.sequence_name(TITLE)
    assert out == xml and sorted(f.name for f in xml.parent.glob("*.xml")) == ["1_edit.xml"]       # no copy
    assert seq == "The fact that this ad sounds so wrong is hilarious"                          # no emoji
    assert PM.sequence_name_in(xml.read_text(encoding="utf-8")) == seq
    assert xml.read_bytes().replace(seq.encode("utf-8"), b"Recreated Edit (Premiere)") == before   # only its name
    assert abs(xml.stat().st_mtime - t0) < 0.01                       # its times kept (never "made again")
    assert PM.project_for(xml).name == f"{NM.file_stem(NM.fit(TITLE))}.prproj"
    assert PM.edit_for_premiere(xml) == xml and PM.named_copy is PM.edit_for_premiere


def test_a_sequence_name_with_an_ampersand_is_escaped_and_reads_back(tmp_path):
    p = title_project(tmp_path / "local", title="Tom & Jerry's best 😂")
    out = PM.edit_for_premiere(p.folder / "edit" / "1_edit.xml")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(out.read_bytes())
    assert root.find("sequence/name").text == "Tom & Jerry's best"


def test_an_old_project_keeps_night_10s_names(tmp_path):
    p = P.create(tmp_path, competitor_url="https://youtu.be/AbCdEf12345", name="A lot to catch up on")
    xml = edit_xml(p.folder / "edit")
    before = xml.read_bytes()
    out = PM.edit_for_premiere(xml)
    assert out == xml and xml.read_bytes() == before                  # the tool's own sequence name, untouched
    assert PM.project_for(xml).name == f"{p.folder.name}_edit.prproj"
    cuts = PM.edit_for_premiere(edit_xml(p.folder / "cuts"))           # night 10 named a Just cuts run's project so
    assert cuts.name == "1_edit.xml" and PM.project_for(cuts).name == "cuts_edit.prproj"


def test_a_finished_edit_gets_its_title_sequence_at_once(tmp_path):
    from mr_app.core import flow
    p = title_project(tmp_path / "local", edit=False)
    xml = edit_xml(p.folder / "edit")
    flow.record_edit(p, {"label": "PASS", "run_dir": str(p.folder / "edit")})
    assert [f.name for f in (p.folder / "edit").glob("*.xml")] == ["1_edit.xml"]
    assert PM.sequence_name_in(xml.read_text(encoding="utf-8")) == NM.sequence_name(TITLE)
    old = P.create(tmp_path, competitor_url="https://youtu.be/AbCdEf12345", name="old one")
    oxml = edit_xml(old.folder / "edit")
    flow.record_edit(old, {"label": "PASS", "run_dir": str(old.folder / "edit")})
    assert [f.name for f in (old.folder / "edit").glob("*.xml")] == ["1_edit.xml"]          # nothing new there
    assert PM.sequence_name_in(oxml.read_text(encoding="utf-8")) == "Recreated Edit (Premiere)"


def test_the_apps_old_copies_go_and_their_projects_stay_its_own(tmp_path):
    """Day 14: a run folder from day 13 -- 1_edit.xml and the app's <title>_edit.xml with its <title>.prproj: the copy
    goes (it is this very edit), the project stays and is still the app's own (never taken for your fix)."""
    p = title_project(tmp_path / "local")
    run = p.folder / "edit"
    xml = run / "1_edit.xml"
    stem = NM.file_stem(NM.fit(TITLE))
    copy = run / f"{stem}_edit.xml"
    copy.write_bytes(PM.rename_sequence(xml.read_bytes().decode("utf-8"), TITLE).encode("utf-8"))   # (as day 13 wrote it)
    RN.remember_copy(run, copy.name)
    proj = saved(run / f"{stem}.prproj")
    mine = run / "my backup.xml"
    mine.write_bytes(xml.read_bytes())                                 # your own copy: never touched
    PM.edit_for_premiere(xml)
    assert not copy.exists() and mine.exists() and proj.exists()
    assert PM.is_own_project(proj) and B._own(proj)                    # (the board never takes it for your fix)


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
        assert PM.sequence_name_in(x) == "Nobody expected this radio ad"  # day 14: the edit itself follows the title
        assert PB.file_url(str(run / "extras" / "media" / "raw.mp4")) in x       # its media: the new folder
        assert sorted(f.name for f in run.glob("*.xml")) == ["1_edit.xml"]       # one XML, no copy
    e = WT.read(hub)[0]
    assert Path(e.project) == new_local and Path(e.run) == new_local / "edit" and e.name == new_name
    assert Path(PB.pending(local)[0]["folder"]) == new_local
    pq = (hub / "problems_queue.md").read_text(encoding="utf-8")
    assert f"{new_hub}\\edit\\extras\\batch_console.log" in pq and f"run: {new_hub}\\edit\n" in pq
    assert f"**{old_hub.name}**" in pq                                   # the entry's heading (and id) as it was
    assert f"{old_hub}-2\\edit" in pq                                    # another project's path: untouched
    assert B.card_of(new_local).rename_to == "" and B.card_of(new_local).display == "Nobody expected this radio ad 📻"


def test_the_other_computers_waiting_card_finds_the_shared_copy_renamed_here(tmp_path):
    hub = tmp_path / "Projects"
    old = hub / "2026-10-20_borat"
    old.mkdir(parents=True)
    laptop = r"C:\Users\nobody-names\Desktop\bh\MovieRecaps\input\downloads\2026-10-20_borat"   # its own path
    WT.add(hub, WT.Entry(name=old.name, project=laptop, run=laptop + "\\edit", run_in_project="edit",
                         computer="laptop", result="PASS"))
    eid = WT.read(hub)[0].id
    new = hub / "2026-10-20_The fact that he asked them to teach him slang 🗣️😭"
    os.rename(old, new)
    (new / "edit").mkdir()
    assert WT.relink(hub, [(old, new)]) == ""
    e = WT.read(hub)[0]
    assert e.id == eid and e.name == new.name and e.project == laptop     # the laptop still finds its own copy
    proj, run = WT._where(e, hub)                                          # noqa: SLF001 - and this computer the new
    assert proj == new and run == new / "edit"


def test_learns_watcher_never_offers_the_apps_own_project_for_a_title_ending_in_fixed(tmp_path):
    from mr_app.core import learnkit as K
    root = tmp_path / "Projects"
    p = title_project(root, title="Finally fixed")
    out = PM.named_copy(p.folder / "edit" / "1_edit.xml")
    own = saved(PM.project_for(out))
    old = time.time() - 120
    os.utime(own, (old, old))
    assert own.name == "Finally fixed.prproj" and K.FIXED_RE.search(own.name)
    assert K.new_fixed([root], tmp_path / "seen.json") == []
    mine = saved(p.folder / "edit" / "my fixed.prproj")
    os.utime(mine, (old, old))
    assert [f for _p, f in K.new_fixed([root], tmp_path / "seen.json")] == [mine]
    assert K.prproj_of_folder(p.folder) == mine


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


def test_its_first_copy_to_the_shared_folder_makes_it_wait(tmp_path):
    from mr_app.core import locks
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = title_project(local)
    B.set_upload_title(p.folder, "After the copy")
    dest = hub / p.folder.name                                           # what publish copies into (and locks)
    dest.mkdir(parents=True)
    lock = locks.WorkLock(dest, "publish")
    lock.acquire()
    try:
        res = RN.rename(p.folder, hub=hub, local_root=local)
        assert res.state == RN.WAIT and "copied to the shared folder" in res.text and p.folder.is_dir()
    finally:
        lock.release()


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
    x = (p.folder / "edit" / "1_edit.xml").read_text(encoding="utf-8")
    assert PM.sequence_name_in(x) == "Kept because of my project"        # the edit's sequence follows the title
    assert sorted(f.name for f in (p.folder / "edit").glob("*.xml")) == ["1_edit.xml"]        # (day 14: one XML)


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
    RN.remember_project(p.folder / "edit", PM.project_for(out).name)      # (Open in Premiere made it)
    res = RN.sync_run_files(p.folder / "edit", P.load(p.folder).data)
    assert res == []                                                       # already right
    P.load(p.folder).update(title="Shiny new headline")
    d = P.load(p.folder).data
    done = RN.sync_run_files(p.folder / "edit", d)
    names = sorted(f.name for f in (p.folder / "edit").iterdir() if f.is_file())
    assert "Shiny new headline.prproj" in names and f"{NM.file_stem(NM.fit(TITLE))}.prproj" not in names and done
    assert [n for n in names if n.endswith(".xml")] == ["1_edit.xml"]
    assert PM.sequence_name_in((p.folder / "edit" / "1_edit.xml").read_text(encoding="utf-8")) == "Shiny new headline"
    assert PM.is_own_project(p.folder / "edit" / "Shiny new headline.prproj")


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
# the review's findings (day 13)
# ---------------------------------------------------------------------------------------------------------------------

def test_renaming_one_copy_keeps_a_title_only_its_twin_has(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    hub.mkdir()
    p = title_project(local)
    dest = published(p, hub)
    B.set_upload_title(dest, "Set on the laptop 🎥")                     # the YouTube title: only the shared copy
    old = time.time() - 600
    os.utime(dest / "title.txt", (old, old))
    B.set_title(p.folder, "A new headline\nfrom the queue row")           # this computer: the headline, later
    res = RN.rename(p.folder, hub=hub, local_root=local)
    assert res.state == RN.RENAMED, res.text                             # named after the YouTube title (it wins)
    assert res.folder.name.endswith("_Set on the laptop 🎥")
    for c in (res.folder, hub / res.folder.name):
        d = P.load(c).data
        assert d["upload_title"] == "Set on the laptop 🎥"               # never wiped by the other copy's ""
        assert d["title"] == "A new headline\nfrom the queue row"        # the newer headline on both
        assert (c / "title.txt").read_text(encoding="utf-8") == "Set on the laptop 🎥"


def test_a_headline_changed_on_the_shared_card_is_never_reverted_by_the_other_copy(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    hub.mkdir()
    p = title_project(local, title="First headline")
    dest = published(p, hub)
    B.set_upload_title(dest, "The YouTube title 🎥")                       # on the shared card
    res = RN.rename(dest, hub=hub, local_root=local)
    assert res.state == RN.RENAMED, res.text
    shared = res.folder
    twin = next(n for o, n in res.moved if _n(o) == _n(p.folder))
    B.set_title(shared, "Second headline")                                 # the headline, on the shared card again
    res2 = RN.rename(shared, hub=hub, local_root=local)
    assert res2.state == RN.SAME, res2.text
    for c in (shared, twin):
        d = P.load(c).data
        assert d["title"] == "Second headline" and d["upload_title"] == "The YouTube title 🎥"
    B.set_title(twin, "Third headline")                                    # and on this computer's queue row
    RN.rename(twin, hub=hub, local_root=local)
    assert P.load(shared).data["title"] == P.load(twin).data["title"] == "Third headline"


def test_a_cleared_youtube_title_is_cleared_on_both_and_the_headline_names_them(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    hub.mkdir()
    p = title_project(local, title="The headline")
    dest = published(p, hub)
    B.set_upload_title(p.folder, "Upload two")
    res = RN.rename(p.folder, hub=hub, local_root=local)
    assert res.folder.name.endswith("_Upload two")
    B.set_upload_title(res.folder, "")                                     # cleared
    res2 = RN.rename(res.folder, hub=hub, local_root=local)
    assert res2.state == RN.RENAMED and res2.folder.name.endswith("_The headline"), res2.text
    for c in (res2.folder, hub / res2.folder.name):
        assert not P.load(c).data.get("upload_title")


def test_a_local_copy_follows_its_shared_copy_renamed_on_the_other_computer(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    hub.mkdir()
    p = title_project(local, title="Old headline")
    dest = published(p, hub)
    renamed = hub / NM.folder_base("Laptop title", "2026-10-20")
    os.rename(dest, renamed)                                               # (the laptop renamed the shared copy)
    P.load(renamed).update(old_names=[dest.name], name=renamed.name)
    B.set_upload_title(renamed, "Laptop title")                            # (as the laptop's app writes it)
    B.set_title(p.folder, "Old headline\nwith a second line")              # a title change here, on the queue row
    res = RN.rename(p.folder, hub=hub, local_root=local)
    assert res.state == RN.RENAMED, res.text
    assert res.folder.name == renamed.name                                 # the same name as the shared copy
    assert P.load(res.folder).data["published"]["to"] == str(renamed)


def test_a_hand_edited_title_txt_names_the_folder_and_the_files_alike(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = title_project(local, upload="Upload old")
    (p.folder / "title.txt").write_text("Hand typed title", encoding="utf-8")   # edited by hand: it wins
    res = RN.rename(p.folder, hub=hub, local_root=local)
    assert res.state == RN.RENAMED and res.folder.name.endswith("_Hand typed title"), res.text
    assert P.load(res.folder).data["upload_title"] == "Hand typed title"
    assert sorted(f.name for f in (res.folder / "edit").glob("*.xml")) == ["1_edit.xml"]       # (day 14: one XML)
    assert PM.sequence_name_in((res.folder / "edit" / "1_edit.xml").read_text(encoding="utf-8")) == "Hand typed title"


def test_after_a_publish_the_two_copies_share_a_base_so_a_later_change_on_either_wins(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    hub.mkdir()
    p = title_project(local, title="Old headline", upload="Upload U")
    B.set_upload_title(p.folder, "Upload U")
    t0 = time.time() - 7200
    os.utime(p.folder / "title.txt", (t0, t0))
    dest = published(p, hub)
    r0 = RN.rename(dest, hub=hub, local_root=local)                      # what the window runs after a publish
    assert r0.state in (RN.SAME, RN.RENAMED), r0.text                     # (here: named after the YouTube title)
    shared, mine = hub / r0.folder.name, local / r0.folder.name
    assert shared.is_dir() and mine.is_dir()
    t1 = time.time() - 3600
    os.utime(shared / "title.txt", (t1, t1))                              # (the shared copy's title.txt is newer)
    os.utime(mine / "title.txt", (t0, t0))
    B.set_title(mine, "New headline")                                     # title.txt holds the YouTube title: unchanged
    RN.rename(mine, hub=hub, local_root=local)
    assert P.load(mine).data["title"] == P.load(shared).data["title"] == "New headline"
    B.set_upload_title(mine, "")                                          # cleared here
    res = RN.rename(mine, hub=hub, local_root=local)
    assert res.state == RN.RENAMED and res.folder.name.endswith("_New headline"), res.text
    assert not P.load(hub / res.folder.name).data.get("upload_title")


def test_the_window_syncs_a_project_right_after_it_was_published(qapp=None):
    from types import SimpleNamespace

    from mr_app.ui import main_window as MW
    seen = []
    win = SimpleNamespace(title_changed=lambda f: seen.append(f))
    MW.MainWindow.synced(win, Path("C:/p/x"))
    MW.MainWindow.synced(win, None)
    assert seen == [Path("C:/p/x")]


def _n(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def test_the_scan_reads_xml_escaped_paths_and_long_unc_ones(tmp_path):
    f = tmp_path / "tom.prproj"
    f.write_bytes(gzip.compress("<P><ActualMediaFilePath>D:\\Shared\\Projects\\2026-10-20_Tom &amp;amp; Jerry"
                                "\\edit\\extras\\media\\raw.mp4</ActualMediaFilePath>"
                                "<FilePath>\\\\?\\UNC\\192.168.101.11\\Shared\\Projects\\2026-10-20_It&apos;s Ok"
                                "\\edit\\x.mp4</FilePath></P>".replace("&amp;amp;", "&amp;").encode("utf-8")))
    got = RN.folder_names_in(f)
    assert "2026-10-20_tom & jerry" in got and "2026-10-20_it's ok" in got and "192.168.101.11" in got
    assert RN._plain_path("\\\\?\\UNC\\pc\\Shared\\a.prproj") == "\\\\pc\\Shared\\a.prproj"      # noqa: SLF001
    assert RN._plain_path("\\\\?\\D:\\a &amp; b.prproj") == "D:\\a & b.prproj"                  # noqa: SLF001


def test_a_rename_never_removes_your_own_copy_of_the_xml(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    p = title_project(local, edit=False)
    x = edit_xml(p.folder / "edit")
    PM.named_copy(x)                                                     # the app's copy (on its list)
    mine = p.folder / "edit" / "1_edit backup.xml"
    mine.write_bytes(x.read_bytes())                                     # yours: the same edit, another name
    named = p.folder / "edit" / "my version_edit.xml"
    named.write_bytes(x.read_bytes().replace(b"Recreated Edit (Premiere)", b"my version"))
    B.set_upload_title(p.folder, "Brand new title")
    res = RN.rename(p.folder, hub=hub, local_root=local)
    assert res.state == RN.RENAMED, res.text
    files = sorted(f.name for f in (res.folder / "edit").glob("*.xml"))
    assert files == ["1_edit backup.xml", "1_edit.xml", "my version_edit.xml"]       # yours stay; the app keeps none
    assert not P.load(res.folder).data["names"].get("copies")
    assert PM.sequence_name_in((res.folder / "edit" / "1_edit.xml").read_text(encoding="utf-8")) == "Brand new title"


def test_paths_with_apostrophes_after_the_old_name_are_another_project(tmp_path):
    text = "  - run: .\\2026-10-20_The dog's revenge\\edit\n  - run: .\\2026-10-20_The dog\\edit\n"
    out = RN._replace_paths(text, [(".\\2026-10-20_The dog", ".\\2026-10-20_New")])                 # noqa: SLF001
    assert out == "  - run: .\\2026-10-20_The dog's revenge\\edit\n  - run: .\\2026-10-20_New\\edit\n"


def test_the_files_leave_room_for_premieres_own_files_next_to_the_project():
    for title in ("The fact that this ad sounds so wrong is hilarious 📻😭",
                  "Before Tobey Maguire Was Spider-Man He Had One Job And It Was Hilarious To Watch Every Time 🕷️"):
        part = NM.fit(title)
        stem = NM.file_stem(part)
        run = f"{'x' * NM.ROOT_LEN}\\2026-10-20_{part} (99)\\edit-99"
        auto = f"{run}\\Adobe Premiere Pro Auto-Save\\{stem}--{'0' * 36}-2026-10-20_18-04-39.prproj"
        assert NM.utf16_len(auto) <= NM.PREMIERE_LIMIT or len(stem) <= NM.STEM_MIN + 2, (len(auto), stem)
        assert stem and title.startswith(stem.split(" ")[0])
    assert NM.file_stem("Short title") == "Short title"                  # a usual short title: whole


def test_a_twin_is_only_a_folder_linked_by_where_it_was_published(tmp_path):
    local, hub = tmp_path / "local", tmp_path / "Projects"
    hub.mkdir()
    p = title_project(local)
    dest = published(p, hub)
    stray = local / "2026-10-20_a copy made in Explorer"
    import shutil
    shutil.copytree(p.folder, stray)                                     # the same "created" second
    P.load(stray).update(published=None)
    cps = RN.copies(dest, P.load(dest).data, hub, local)
    assert [c for c, _d, _s in cps] == [dest, p.folder]                  # the real twin, never the stray copy


def test_the_other_computers_card_of_another_video_is_never_re_pointed(tmp_path):
    hub = tmp_path / "Projects"
    old = hub / "2026-10-20_video"
    old.mkdir(parents=True)
    WT.add(hub, WT.Entry(name=old.name, project=r"C:\elsewhere\2026-10-20_video", run=r"C:\elsewhere\2026-10-20_video"
                         r"\edit", run_in_project="edit", raw="other raw.mp4", competitor="other comp.mp4",
                         computer="laptop"))
    new = hub / "2026-10-20_Renamed"
    os.rename(old, new)
    WT.relink(hub, [(old, new)], {str(old): ("raw.mp4", "competitor.mp4")})
    assert WT.read(hub)[0].name == old.name                              # another video with the same folder name


def test_a_waiting_rename_is_kept_while_its_folder_cannot_be_reached(tmp_path, monkeypatch):
    from mr_app.core import publish as PBm
    gone = tmp_path / "unreachable" / "2026-10-20_x"
    RN._set_pending(gone, "it waits")                                     # noqa: SLF001
    monkeypatch.setattr(PBm, "reachable", lambda root, timeout_s=5.0: False)
    RN.retry_pending(hub=tmp_path / "hub", local_root=tmp_path / "local")
    assert [Path(x["folder"]) for x in RN.pending()] == [gone]           # (the PC asleep: kept for later)
    monkeypatch.setattr(PBm, "reachable", lambda root, timeout_s=5.0: True)
    RN.retry_pending(hub=tmp_path / "hub", local_root=tmp_path / "local")
    assert RN.pending() == []                                            # really gone: off the list


# ---------------------------------------------------------------------------------------------------------------------
# Learn still finds the run from a project made from the title-named edit
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
    edit = PM.edit_for_premiere(x)                                       # day 14: 1_edit.xml itself, title-named
    assert edit == x and PM.sequence_name_in(x.read_text(encoding="utf-8")) == NM.sequence_name(TITLE)
    # the project Premiere makes from it: <title>.prproj next to it, its sequence called after the title, the media
    # in the run's extras\media
    pr = LC.fake_prproj(PM.project_for(edit), [{"path": str(run / "extras" / "media" / "raw.mp4"), **LC.RAW}],
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
