"""The board's model (mr_app.core.board): each card's column from its steps, a move by hand and when a later step
overrides it, every column's date, pins and the order, both folders without hanging, the two titles and title.txt,
the upload marks, Clean up (and what it never deletes). Run: python -m pytest tools/app/tests/test_board.py"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import board as B      # noqa: E402
from mr_app.core import locks as L      # noqa: E402
from mr_app.core import machine as M    # noqa: E402
from mr_app.core import projects as P   # noqa: E402
from mr_app.core import publish as PB   # noqa: E402

OLD = time.time() - 3 * 3600


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(tmp_path / "settings.json"))


def t(hhmm: str, day: int = 18) -> str:
    return f"2026-10-{day:02d}T{hhmm}:00+03:00"


def make(root: Path, name: str = "video one", channel: str = "LouLaughs", created: str | None = t("01:00"),
         **steps) -> Path:
    """A project; steps: download="02:44" (done then) or edit=("done", "05:10", "PASS")."""
    p = P.create(root, competitor_url=f"https://youtu.be/{abs(hash(name)) % 10 ** 11:011d}", name=name)

    def apply(d: dict) -> None:
        if created:
            d["created"] = created
        d["channel"] = channel
        for step, v in steps.items():
            state, hhmm, result = v if isinstance(v, tuple) else ("done", v, None)
            d["steps"][step].update(state=state, started=t(hhmm), finished=t(hhmm))
            if result:
                d["steps"][step]["result"] = result
    p.change(apply)
    return p.folder


def board_of(folder: Path) -> dict:
    return P.load(folder).data.get("board") or {}


def old(p: Path) -> Path:
    os.utime(p, (OLD, OLD))
    return p


def test_the_columns():
    assert B.COLUMNS == [("link", "Link"), ("downloaded", "Downloaded"), ("converted", "Converted"),
                         ("template", "Template"), ("edit", "Edit made"), ("fixed", "Fixed"),
                         ("rendered", "Rendered"), ("uploaded", "Uploaded")]


@pytest.mark.parametrize("steps, column", [
    ({}, "link"),
    ({"download": "02:44"}, "downloaded"),
    ({"download": "02:44", "convert": "02:50"}, "converted"),
    ({"download": "02:44", "convert": "02:50", "template": "03:00"}, "template"),
    ({"download": "02:44", "convert": "02:50", "template": "03:00", "edit": ("done", "03:30", "PASS")}, "edit"),
    ({"download": "02:44", "template": "03:00", "edit": ("done", "03:30", "OK with a note")}, "edit"),
    ({"download": "02:44", "template": "03:00", "edit": ("failed", "03:30", "FAIL")}, "template"),
    ({"download": "02:44", "convert": ("running", "02:50", None)}, "downloaded"),
    ({"download": ("failed", "02:44", None)}, "link"),
])
def test_the_column_its_steps_reached(tmp_path, steps, column):
    c = B.card_of(make(tmp_path, **steps))
    assert c.column == column == c.auto_column and not c.error and c.moved is False


def test_a_fixed_prproj_and_an_upload_move_it_on(tmp_path):
    f = make(tmp_path, download="02:44", convert="02:50", template="03:00", edit=("done", "03:30", "PASS"))
    (f / "edit").mkdir()
    fixed = f / "edit" / "video one_fixed.prproj"
    fixed.write_bytes(b"x")
    (f / "edit" / "Adobe Premiere Pro Auto-Save").mkdir()
    (f / "edit" / "Adobe Premiere Pro Auto-Save" / "video one_fixed-2.prproj").write_bytes(b"x")   # not counted
    newer = f / "Fixed v2.prproj"
    newer.write_bytes(b"y")
    os.utime(fixed, (OLD, OLD))
    c = B.card_of(f)
    assert c.column == "fixed" and c.fixed == newer
    assert c.dates["fixed"] == datetime.fromtimestamp(newer.stat().st_mtime).astimezone()
    B.mark_uploaded(f, datetime(2026, 10, 18, 9, 5).astimezone(), video_id="abc123", url="https://y/abc123",
                    title="Upload title")
    c = B.card_of(f)
    assert c.column == "uploaded" and c.uploaded == {"at": "2026-10-18T09:05:00" + c.uploaded["at"][19:],
                                                     "id": "abc123", "url": "https://y/abc123",
                                                     "title": "Upload title", "how": "auto"}
    assert c.dates["uploaded"] == datetime(2026, 10, 18, 9, 5).astimezone()


def test_every_column_keeps_its_date_and_time(tmp_path):
    c = B.card_of(make(tmp_path, download="02:44", convert="02:50", template="03:00"))
    want = {"link": t("01:00"), "downloaded": t("02:44"), "converted": t("02:50"), "template": t("03:00")}
    assert c.dates == {k: datetime.fromisoformat(v) for k, v in want.items()}
    assert c.date == datetime.fromisoformat(t("03:00"))
    assert c.date_lines() == [f"{B.LABELS[k]} {B.when_text(datetime.fromisoformat(v))}" for k, v in want.items()]
    d = datetime.fromisoformat(t("02:44")).astimezone()
    assert f"Downloaded {d.day} {d:%b %H:%M}" in c.date_lines()                 # "Downloaded 18 Oct 02:44"


def test_a_move_by_hand_wins_until_a_later_step_further_right(tmp_path):
    f = make(tmp_path, created=t("01:00", day=10), download="02:44", convert="02:50", template="03:00",
             edit=("done", "03:30", "PASS"))
    for st in ("download", "convert", "template", "edit"):      # all of it days ago
        P.load(f).change(lambda d, s=st: d["steps"][s].update(finished=d["steps"][s]["finished"].replace("-18T",
                                                                                                         "-10T")))
    an_hour_ago = (datetime.now() - timedelta(hours=1)).astimezone()
    c = B.move(f, "template", now=an_hour_ago)                 # back: the edit is to be made again
    assert c.column == "template" and c.auto_column == "edit" and c.moved is True
    assert board_of(f)["column"] == "template" and board_of(f)["moved_at"] == B._iso(an_hour_ago)
    fixed = f / "x_fixed.prproj"                               # fixed after the move: further right -> fixed
    fixed.write_bytes(b"x")
    assert B.card_of(f).column == "fixed"
    B.move(f, "rendered")                                      # by hand, now (after the fixed prproj)
    os.utime(fixed, None)                                      # saved again: but Fixed is left of Rendered
    assert B.card_of(f).column == "rendered"
    B.mark_uploaded(f, datetime.now().astimezone())
    assert B.card_of(f).column == "uploaded"
    with pytest.raises(ValueError):
        B.move(f, "published")


def test_a_move_keeps_a_date_the_column_had(tmp_path):
    f = make(tmp_path, download="02:44", convert="02:50", template="03:00", edit=("done", "03:30", "PASS"))
    B.move(f, "template", now=t("04:00"))
    assert "template" not in board_of(f).get("dates", {})       # its step's date stays the date
    assert B.card_of(f).dates["template"] == datetime.fromisoformat(t("03:00"))
    B.move(f, "rendered", now=t("05:00"))
    B.move(f, "edit", now=t("05:30"))
    B.move(f, "rendered", now=t("06:00"))
    assert board_of(f)["dates"] == {"rendered": t("05:00")}     # the first time it got there
    assert B.card_of(f).dates["edit"] == datetime.fromisoformat(t("03:30"))


def test_uploaded_by_hand_and_moved_out_again(tmp_path):
    f = make(tmp_path, download="02:44")
    c = B.move(f, "uploaded", now=t("07:00"))
    assert c.column == "uploaded" and c.uploaded == {"at": t("07:00"), "how": "you"}
    assert B.uploads([c]) == [(datetime.fromisoformat(t("07:00")), "LouLaughs")]
    c = B.move(f, "rendered", now=t("07:05"))                  # not uploaded after all
    assert c.column == "rendered" and c.uploaded is None and "uploaded" not in c.dates and c.not_same == []
    assert B.uploads([c]) == []
    B.mark_uploaded(f, t("08:00"), video_id="vid1", how="auto")
    c = B.move(f, "rendered", now=t("08:05"))                  # an automatic match you took back
    assert c.uploaded is None and c.not_same == ["vid1"]


def test_not_same_remembers_each_id_once(tmp_path):
    f = make(tmp_path)
    assert B.card_of(f).not_same == []
    B.not_same(f, "a1")
    c = B.not_same(f, "a1")
    B.not_same(f, "")
    assert c.not_same == ["a1"] and B.card_of(f).not_same == ["a1"] and board_of(f)["not_same"] == ["a1"]


def test_pins_first_then_the_newest(tmp_path):
    a = make(tmp_path, "aaa", download="02:00")
    b = make(tmp_path, "bbb", download="03:00")
    c = make(tmp_path, "ccc", download="04:00")
    d = make(tmp_path, "ddd", download="01:00")
    B.pin(d, now=t("09:00"))
    B.pin(a, now=t("08:00"))
    B.pin(a, now=t("10:00"))                                   # pinned again: keeps its place
    assert board_of(a)["pinned_at"] == t("08:00")
    cards = [B.card_of(x) for x in (a, b, c, d)]
    col = B.ordered(cards)
    assert [x.folder for x in col["downloaded"]] == [a, d, c, b]
    assert set(col) == set(B.KEYS) and col["link"] == []
    B.pin(a, False)
    assert board_of(a)["pinned"] is False and "pinned_at" not in board_of(a)
    assert [x.folder for x in B.ordered([B.card_of(x) for x in (a, b, c, d)])["downloaded"]] == [d, c, b, a]


def test_a_broken_project_json_never_breaks_the_board(tmp_path):
    f = tmp_path / "2026-10-18_broken"
    f.mkdir()
    (f / "project.json").write_text("{not json", encoding="utf-8")
    c = B.card_of(f)
    assert c.column == "link" and c.error.startswith("project.json cannot be read") and c.name == f.name
    (f / "project.json").write_text('{"steps": "nonsense", "board": [1], "created": 5}', encoding="utf-8")
    c = B.card_of(f)
    assert c.column == "link" and c.dates == {} and not c.pinned
    assert B.card_of(tmp_path / "nothing").error == "this folder has no project.json"


# ---------------------------------------------------------------------------------------------------------------------
# both folders
# ---------------------------------------------------------------------------------------------------------------------

def test_load_cards_shows_each_project_once_and_the_shared_copy_wins(tmp_path):
    shared, local = tmp_path / "Shared" / "Projects", tmp_path / "input" / "downloads"
    a_local = make(local, "aaa", download="02:00")
    a_shared = shared / a_local.name                            # a's published copy
    shutil.copytree(a_local, a_shared)
    P.load(a_local).update(published={"to": str(a_shared)})
    P.load(a_shared).update(published={"to": str(a_shared)}, board={"column": "rendered", "moved_at": t("09:00")})
    b = make(local, "bbb")                                      # only here
    other = make(shared, "ccc", created=t("00:10"))             # the other computer's "ccc"
    mine = make(local, "ccc", created=t("00:20"))               # another video with the same folder name
    moved = make(local, "ddd")                                  # published under another name (-2)
    shutil.copytree(moved, shared / f"{moved.name}-2")
    P.load(moved).update(published={"to": str(shared / f"{moved.name}-2")})
    cards = B.load_cards(shared, local)
    got = sorted((c.folder.parent.parent.name, c.folder.name, c.where) for c in cards)
    assert got == sorted([("Shared", a_shared.name, "shared"), ("Shared", other.name, "shared"),
                          ("Shared", f"{moved.name}-2", "shared"), ("input", b.name, "local"),
                          ("input", mine.name, "local")])
    a = next(c for c in cards if c.folder == a_shared)
    assert a.column == "rendered" and a.published is True and a.where == "shared"
    assert next(c for c in cards if c.folder == b).published is False


def test_load_cards_without_a_shared_folder_never_hangs(tmp_path, monkeypatch):
    shared, local = tmp_path / "Shared" / "Projects", tmp_path / "local"
    make(shared, "aaa")
    lb = make(local, "bbb")
    assert [c.folder for c in B.load_cards(None, local)] == [lb]
    assert [c.folder for c in B.load_cards(tmp_path / "nowhere" / "x" / "y", local)] == [lb]
    real = PB._answers
    monkeypatch.setattr(PB, "_answers", lambda root: time.sleep(5) or real(root) if "Shared" in str(root)
                        else real(root))                        # the PC asleep: its share blocks
    t0 = time.time()
    assert [c.folder for c in B.load_cards(shared, local, timeout_s=0.5)] == [lb]
    assert time.time() - t0 < 2.0
    assert B.load_cards(None, None) == [] and B.load_cards(tmp_path / "a", tmp_path / "b") == []


def test_load_cards_with_one_folder_for_both(tmp_path):
    root = tmp_path / "Projects"
    a = make(root, "aaa")
    cards = B.load_cards(root, root)
    assert [(c.folder, c.where) for c in cards] == [(a, "shared")]


def test_a_card_shows_who_works_on_it(tmp_path):
    f = make(tmp_path)
    other = "laptop" if M.which() == "pc" else "pc"             # the other computer, wherever this runs
    lk = L.WorkLock(f, "edit", me={"computer": other, "host": "Gvidas", "id": "OTHER", "user": "r"},
                    pid=77).acquire()
    try:
        c = B.card_of(f)
        assert c.lock is not None
        assert c.lock["text"].startswith(f"the {M.label(other)} is working on this project since")
    finally:
        lk.release()
    assert B.card_of(f).lock is None
    (f / L.LOCK_FILE).write_text(json.dumps({"computer": "laptop", "id": "X", "pid": 1, "what": "edit",
                                             "started": t("01:00"), "heartbeat": t("01:00")}), encoding="utf-8")
    old(f / L.LOCK_FILE)
    assert B.card_of(f).lock is None                             # stale: nobody works on it


# ---------------------------------------------------------------------------------------------------------------------
# the titles
# ---------------------------------------------------------------------------------------------------------------------

def test_the_headline_and_the_upload_title(tmp_path):
    f = make(tmp_path)
    headline = "The fact that this meme was\nan actual ad is hilarious \U0001F36B\U0001F62D"
    c = B.set_title(f, headline)
    assert (f / "title.txt").read_bytes() == headline.encode("utf-8")           # exactly as typed
    assert P.load(f).data["title"] == headline and c.title == headline and c.upload_title == ""
    assert B.title_of(f) == headline and B.copy_title(f) == headline and B.copy_title(c) == headline
    c = B.set_upload_title(f, "This meme was a real ad \U0001F62D #shorts")
    assert (f / "title.txt").read_text(encoding="utf-8") == "This meme was a real ad \U0001F62D #shorts"
    assert P.load(f).data["upload_title"] == "This meme was a real ad \U0001F62D #shorts"
    assert B.title_of(f) == headline and c.title == headline                    # the headline as before
    assert B.copy_title(f) == B.copy_title(c) == c.upload_title == "This meme was a real ad \U0001F62D #shorts"
    B.set_title(f, "A new headline")                            # the upload title stays in title.txt
    assert (f / "title.txt").read_text(encoding="utf-8") == "This meme was a real ad \U0001F62D #shorts"
    assert B.title_of(f) == "A new headline" and P.load(f).data["title"] == "A new headline"
    c = B.set_upload_title(f, "")                               # back to the headline
    assert (f / "title.txt").read_text(encoding="utf-8") == "A new headline"
    assert c.upload_title == "" and B.copy_title(c) == "A new headline"


def test_title_txt_edited_by_hand_wins_and_is_kept(tmp_path):
    f = make(tmp_path)
    B.set_title(f, "Headline")
    (f / "title.txt").write_bytes("﻿Headline, edited\r\nby hand\r\n".encode("utf-8"))   # Notepad
    assert B.title_of(f) == "Headline, edited\nby hand" and B.copy_title(f) == "Headline, edited\nby hand"
    B.set_upload_title(f, "Upload")                             # the hand edit goes into project.json first
    assert P.load(f).data["title"] == "Headline, edited\nby hand" and B.title_of(f) == "Headline, edited\nby hand"
    assert (f / "title.txt").read_text(encoding="utf-8") == "Upload"
    (f / "title.txt").write_text("Upload, edited", encoding="utf-8")
    assert B.card_of(f).upload_title == "Upload, edited" and B.title_of(f) == "Headline, edited\nby hand"
    B.set_title(f, "Headline 3")
    d = P.load(f).data
    assert (d["title"], d["upload_title"]) == ("Headline 3", "Upload, edited")
    assert (f / "title.txt").read_text(encoding="utf-8") == "Upload, edited"
    (f / "title.txt").write_text("  \n", encoding="utf-8")       # emptied by hand: project.json's
    assert B.copy_title(f) == "Upload, edited" and B.title_of(f) == "Headline 3"


def test_no_title_txt_is_made_for_no_title(tmp_path):
    f = make(tmp_path)
    B.set_upload_title(f, "")
    assert not (f / "title.txt").exists() and B.copy_title(f) == "" and B.title_of(f) == ""


# ---------------------------------------------------------------------------------------------------------------------
# uploads for the charts
# ---------------------------------------------------------------------------------------------------------------------

def test_uploads_are_the_uploaded_cards_times_and_channels(tmp_path):
    a = make(tmp_path, "aaa", channel="LouLaughs")
    b = make(tmp_path, "bbb", channel="Flick707")
    c = make(tmp_path, "ccc", channel="MikeMemesYou")
    B.mark_uploaded(a, t("12:00"))
    B.mark_uploaded(b, t("09:30", day=17), video_id="x")
    B.move(c, "rendered")
    got = B.uploads([B.card_of(x) for x in (a, b, c)])
    assert got == [(datetime.fromisoformat(t("09:30", day=17)), "Flick707"),
                   (datetime.fromisoformat(t("12:00")), "LouLaughs")]


# ---------------------------------------------------------------------------------------------------------------------
# clean up
# ---------------------------------------------------------------------------------------------------------------------

def finished_edit(f: Path, name: str = "edit") -> Path:
    e = f / name
    (e / "extras" / "media").mkdir(parents=True)
    (e / "extras" / "report.md").write_text("# report", encoding="utf-8")
    (e / "1_edit.xml").write_text("<xmeml/>", encoding="utf-8")
    return e


def with_originals(f: Path, raw_copied: bool = True) -> tuple[Path, Path]:
    raw, comp = f / "Some RAW [abc].mp4", f / "comp [def].mp4"
    raw.write_bytes(os.urandom(3 * B.SAMPLE + 123))
    comp.write_bytes(os.urandom(400_000))
    P.load(f).update(raw={"file": raw.name}, competitor={"file": comp.name})
    media = finished_edit(f) / "extras" / "media"
    (shutil.copyfile if raw_copied else os.link)(raw, media / "raw.mp4")   # a copy (another drive) or a link
    os.link(comp, media / "competitor_ref.mp4")
    return raw, comp


def temp_junk(f: Path) -> dict[str, Path]:
    files = {"part": f / "x.mp4.part", "tmp": f / ".project.json.1.2.tmp", "info": f / ".raw_abc.info.json",
             "ytdl": f / "x.mp4.ytdl", "stdout": f / ".edit.app.stdout", "thumbs": f / "Thumbs.db",
             "jlock": f / "project.json.lock", "logtmp": f / "logs" / "a.temp",
             "claim": f / f"{L.LOCK_FILE}.tok123.claim1"}
    (f / "logs").mkdir(exist_ok=True)
    for p in files.values():
        p.write_bytes(b"j" * 100)
        old(p)
    return files


def test_cleanup_plan_and_cleanup(tmp_path):
    f = make(tmp_path, download="02:00")
    junk = temp_junk(f)
    young = f / "downloading.mp4.part"                          # in use right now: not junk
    young.write_bytes(b"y" * 10)
    json_lock = f / "project.json.lock"
    finished_edit(f)
    inside = f / "edit" / "extras" / "half.part"                # in an edit folder: never cleaned
    inside.write_bytes(b"p")
    old(inside)
    plan = B.cleanup_plan([f])
    assert {j.path for j in plan} == set(junk.values())
    assert all(j.why and j.size == 100 for j in plan) and B.total(plan) == "9 files, 900 bytes"
    assert next(j for j in plan if j.path == junk["part"]).why.startswith("a partial file")
    assert next(j for j in plan if j.path == json_lock).kind == "lock"
    freed, errors = B.cleanup(plan)
    assert freed == 900 and errors == []
    assert not any(p.exists() for p in junk.values())
    assert young.exists() and inside.exists() and (f / "project.json").exists() and (f / "edit" / "1_edit.xml").exists()


def test_a_stale_work_lock_is_junk_an_active_one_stops_the_whole_project(tmp_path):
    f = make(tmp_path)
    lock = f / L.LOCK_FILE
    lock.write_text(json.dumps({"computer": "laptop", "id": "X", "pid": 1, "what": "edit", "started": t("01:00"),
                                "heartbeat": t("01:00")}), encoding="utf-8")
    old(lock)
    plan = B.cleanup_plan([f])
    assert [(j.path, j.kind) for j in plan] == [(lock, "lock")] and "no heartbeat" in plan[0].why
    lk = L.WorkLock(f, me={"computer": "pc", "host": "G", "id": "Y", "user": "u"}, pid=5).acquire()
    try:
        old(temp_junk(f)["part"])
        assert B.cleanup_plan([f]) == []                         # someone works on it: left alone
        freed, errors = B.cleanup(plan + [B.Junk(f / L.LOCK_FILE, 1, "x", "lock")])
        assert freed == 0 and lock.exists() and len(errors) == 2  # active again: kept
    finally:
        lk.release()


def test_cleanup_refuses_what_is_protected_whatever_it_is_told(tmp_path):
    f = make(tmp_path, download="02:00")
    raw, comp = with_originals(f)
    B.set_title(f, "Title")
    (f / "template.png").write_bytes(b"png")
    outside = tmp_path / "not a project" / "x.part"
    outside.parent.mkdir()
    outside.write_bytes(b"o")
    old(outside)
    edit_part = f / "edit" / "extras" / "y.part"
    edit_part.write_bytes(b"p")
    old(edit_part)
    bad = [B.Junk(f / "edit" / "1_edit.xml", 1, "x"), B.Junk(f / "template.png", 1, "x"),
           B.Junk(f / "project.json", 1, "x"), B.Junk(f / "title.txt", 1, "x"), B.Junk(f / "edit", 1, "x"),
           B.Junk(f / "edit" / "extras" / "media" / "raw.mp4", 1, "x", "original", raw), B.Junk(edit_part, 1, "x"),
           B.Junk(outside, 1, "x"), B.Junk(raw, 1, "x"),                    # the RAW as if it were a temp file
           B.Junk(raw, 1, "x", "original", tmp_path / "elsewhere.mp4")]     # its "copy" is not an edit's media
    freed, errors = B.cleanup(bad)
    assert freed == 0 and len(errors) == len(bad)
    for p in (f / "edit" / "1_edit.xml", f / "template.png", f / "project.json", f / "title.txt", raw, outside,
              edit_part, f / "edit" / "extras" / "media" / "raw.mp4"):
        assert p.exists(), p
    assert B.protected(f / "edit" / "extras" / "media" / "raw.mp4").startswith("it is in an edit folder")
    assert B.protected(f / "edit-2" / "x.part") == "it is in an edit folder (edit folders are never cleaned)"
    assert B.protected(outside) == "it is not inside a project folder" and B.protected(f / "x.part") == ""


def test_downloaded_originals_go_only_when_uploaded_and_really_copied(tmp_path):
    f = make(tmp_path, download="02:00")
    raw, comp = with_originals(f)                               # the RAW copied into the media, the comp linked
    assert B.cleanup_plan([f]) == []                            # not rendered / uploaded yet
    B.move(f, "rendered")
    plan = B.cleanup_plan([f])
    assert [(j.path, j.kind, j.same_as) for j in plan] == [(raw, "original", f / "edit" / "extras" / "media" /
                                                            "raw.mp4")]
    assert plan[0].size == raw.stat().st_size and "the downloaded RAW" in plan[0].why
    freed, errors = B.cleanup(plan)
    assert freed == 3 * B.SAMPLE + 123 and errors == [] and not raw.exists()
    assert comp.exists() and (f / "edit" / "extras" / "media" / "raw.mp4").exists()


def test_an_original_that_is_not_the_same_video_stays(tmp_path):
    f = make(tmp_path)
    raw, comp = with_originals(f)
    media_raw = f / "edit" / "extras" / "media" / "raw.mp4"
    data = bytearray(media_raw.read_bytes())
    data[len(data) // 2] ^= 0xFF                                # converted inside the run: other bytes
    media_raw.write_bytes(bytes(data))
    B.mark_uploaded(f, t("10:00"))
    assert B.cleanup_plan([f]) == []


def test_cleanup_checks_an_original_again(tmp_path):
    f = make(tmp_path)
    raw, comp = with_originals(f)
    B.mark_uploaded(f, t("10:00"))
    plan = B.cleanup_plan([f])
    assert [j.path for j in plan] == [raw]
    (f / "edit" / "extras" / "media" / "raw.mp4").unlink()        # the edit's copy went meanwhile
    freed, errors = B.cleanup(plan)
    assert freed == 0 and raw.exists() and errors[0].startswith("kept Some RAW [abc].mp4")


def test_a_published_cards_own_downloads_are_cleaned_here(tmp_path):
    shared, local = tmp_path / "Shared", tmp_path / "local"
    f = make(local)
    raw, comp = with_originals(f, raw_copied=False)             # here: linked, so nothing to free yet
    dest = shared / f.name
    shutil.copytree(f, dest)                                    # published: the media copied ...
    (dest / raw.name).unlink()                                  # ... not the downloaded originals
    (dest / comp.name).unlink()
    shutil.rmtree(f / "edit")                                   # the local edit deleted since
    B.mark_uploaded(dest, t("10:00"))
    card = B.card_of(dest)
    assert B.cleanup_plan([card]) == []                         # the shared copy has no originals
    plan = B.cleanup_plan([card], local_root=local)
    assert sorted(j.path for j in plan) == sorted([raw, comp])   # here they are single files now
    assert all(PB._norm(j.same_as).startswith(PB._norm(dest)) for j in plan)
    freed, errors = B.cleanup(plan)
    assert errors == [] and not raw.exists() and not comp.exists() and (dest / "edit" / "extras" / "media" /
                                                                         "raw.mp4").exists()
