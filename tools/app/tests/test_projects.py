"""The video projects (mr_app.core.projects): one folder per video, its project.json (atomic, re-read before each
change, shared by two programs), the steps' states, the logs. Run: python -m pytest tools/app/tests/test_projects.py"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import projects as P   # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("title, short", [
    ("The Funniest Tough Guy of All Time \U0001F602", "the-funniest-tough-guy-of-all-time"),
    ("Don't Stop -- Caf\u00e9 \u00c9clair!!", "dont-stop-cafe-eclair"),
    ("Warum Magirus-Deutz in der UdSSR zur Legende wurde \u2013 und in Deutschland verschwand",
     "warum-magirus-deutz-in-der-udssr-zur"),                        # cut at a word, <= 40 characters
    ("\u65e5\u672c\u8a9e\u306e\u30bf\u30a4\u30c8\u30eb", ""),        # nothing plain is left
    ("Supercalifragilisticexpialidocious" * 2, ("supercalifragilisticexpialidocious" * 2)[:40]),
    ("  CON  ", "con"),
])
def test_the_short_name_is_plain_lowercase_words(title, short):
    got = P.short_name(title)
    assert got == short and len(got) <= 40
    assert all(c.isascii() and (c.isalnum() or c == "-") for c in got)


@pytest.mark.parametrize("url, vid", [
    ("https://www.youtube.com/shorts/4v3FsM0BzWQ", "4v3FsM0BzWQ"),
    ("https://youtu.be/w2fdx42TqPg?si=abc", "w2fdx42TqPg"),
    ("https://www.youtube.com/watch?v=0_-gShi3T-g&t=3s", "0_-gShi3T-g"),
    ("https://m.youtube.com/watch?feature=share&v=hkFYOzYx_6M", "hkFYOzYx_6M"),
    ("youtube.com/live/abcdefghijk", "abcdefghijk"),
    ("https://www.tiktok.com/@someone/video/7234567890123456789", "7234567890123456789"),
    ("https://example.com/clips/my-video.mp4", "my-video"),
    ("", None),
])
def test_the_link_id(url, vid):
    assert P.link_id(url) == vid


def test_the_folder_is_the_date_and_the_competitors_title_else_its_link_id():
    assert P.folder_name("The Funniest Tough Guy of All Time \U0001F602", day="2026-10-17") == \
        "2026-10-17_the-funniest-tough-guy-of-all-time"
    assert P.folder_name(None, competitor_url="https://www.youtube.com/shorts/4v3FsM0BzWQ", day="2026-10-17") == \
        "2026-10-17_4v3fsm0bzwq"
    assert P.folder_name("\u65e5\u672c\u8a9e", competitor_url="https://youtu.be/w2fdx42TqPg", day="2026-10-17") == \
        "2026-10-17_w2fdx42tqpg"                                    # a title with nothing plain: the id
    assert P.folder_name(None, raw_file=r"D:\RAWs\Breaking Bad S01E01.mkv", day="2026-10-17") == \
        "2026-10-17_breaking-bad-s01e01"
    assert P.folder_name(None, day="2026-10-17") == "2026-10-17_video"


# ---------------------------------------------------------------------------------------------------------------------
# project.json
# ---------------------------------------------------------------------------------------------------------------------

def test_create_makes_the_folder_and_project_json(tmp_path):
    p = P.create(tmp_path / "Projects with spaces", competitor_url="https://www.youtube.com/shorts/4v3FsM0BzWQ",
                 raw_url="https://www.youtube.com/watch?v=w2fdx42TqPg", name="The Funniest Tough Guy \U0001F602")
    assert p.folder == tmp_path / "Projects with spaces" / f"{TODAY}_the-funniest-tough-guy"
    d = json.loads((p.folder / "project.json").read_text(encoding="utf-8"))
    assert d == p.data
    assert d["version"] == P.VERSION and d["name"] == p.folder.name and d["computer"] == P.computer()
    assert datetime.fromisoformat(d["created"]).tzinfo is not None and d["updated"] == d["created"]
    assert d["competitor"] == {"url": "https://www.youtube.com/shorts/4v3FsM0BzWQ", "title": None,
                               "id": "4v3FsM0BzWQ", "file": None}
    assert d["raw"] == {"url": "https://www.youtube.com/watch?v=w2fdx42TqPg", "source_file": None, "title": None,
                        "id": "w2fdx42TqPg", "file": None}
    assert d["channel"] is None and d["title"] is None
    assert list(d["steps"]) == list(P.STEPS) == ["download", "convert", "template", "title_check", "edit"]
    assert all(s == {"state": "waiting", "started": None, "finished": None, "error": None}
               for s in d["steps"].values())
    assert sorted(x.name for x in p.folder.iterdir()) == ["project.json"]       # no temp or lock file left


def test_the_same_name_twice_gets_2_and_3(tmp_path):
    a = P.create(tmp_path, competitor_url="https://youtu.be/w2fdx42TqPg")
    b = P.create(tmp_path, competitor_url="https://youtu.be/w2fdx42TqPg")
    c = P.create(tmp_path, competitor_url="https://youtu.be/w2fdx42TqPg")
    assert [x.folder.name for x in (a, b, c)] == [f"{TODAY}_w2fdx42tqpg", f"{TODAY}_w2fdx42tqpg-2",
                                                  f"{TODAY}_w2fdx42tqpg-3"]


def test_a_dropped_raw_file_is_remembered_with_its_full_path(tmp_path):
    raw = tmp_path / "my RAWs" / "Episode 1.mkv"
    p = P.create(tmp_path / "root", competitor_url="https://youtu.be/w2fdx42TqPg", raw_file=raw)
    assert p.data["raw"]["source_file"] == str(raw) and p.data["raw"]["url"] is None


def test_the_steps_states(tmp_path):
    p = P.create(tmp_path, competitor_url="https://youtu.be/w2fdx42TqPg")
    p.set_step("download", "running")
    s = p.step("download")
    assert s["state"] == "running" and s["started"] and s["finished"] is None and s["computer"] == P.computer()
    p.set_step("download", "failed", error="this video is private")
    s = p.step("download")
    assert s["state"] == "failed" and s["finished"] and s["error"] == "this video is private"
    p.set_step("download", "running")                                    # tried again: the old error goes
    assert p.step("download")["error"] is None and p.step("download")["finished"] is None
    p.set_step("download", "done", competitor={"format": "299+140", "size": 123, "file": Path("a b.mp4")})
    s = p.step("download")
    assert s["state"] == "done" and s["competitor"] == {"format": "299+140", "size": 123, "file": "a b.mp4"}
    p.set_step("template", "skipped")
    assert p.step("template")["state"] == "skipped" and p.step("template")["started"] is None
    p.set_step("edit", "done")                                           # done without running: started = now
    assert p.step("edit")["started"] == p.step("edit")["finished"]
    with pytest.raises(ValueError):
        p.set_step("upload", "done")
    with pytest.raises(ValueError):
        p.set_step("download", "finished")
    assert P.load(p.folder).data == p.data                               # all of it on disk


def test_update_merges_into_the_dicts_and_sets_the_rest(tmp_path):
    p = P.create(tmp_path, competitor_url="https://youtu.be/w2fdx42TqPg")
    t0 = p.data["updated"]
    time.sleep(1.1)
    p.update(competitor={"title": "A title \U0001F602", "file": "A title \U0001F602 [w2fdx42TqPg].mp4"},
             channel="Lou Laughs", title="My headline", uploaded=False)
    d = P.load(p.folder).data
    assert d["competitor"]["url"] == "https://youtu.be/w2fdx42TqPg"     # kept
    assert d["competitor"]["file"] == "A title \U0001F602 [w2fdx42TqPg].mp4"
    assert (d["channel"], d["title"], d["uploaded"]) == ("Lou Laughs", "My headline", False)
    assert d["updated"] > t0
    assert p.file("competitor") == p.folder / "A title \U0001F602 [w2fdx42TqPg].mp4"
    assert p.file("raw") is None
    assert "\U0001F602" in (p.folder / "project.json").read_text(encoding="utf-8")   # plain UTF-8, not \\u escapes


def test_two_programs_do_not_lose_each_others_steps(tmp_path):
    """Two Project objects on one folder (the app and part 2's board): each change re-reads the file first."""
    a = P.create(tmp_path, competitor_url="https://youtu.be/w2fdx42TqPg")
    b = P.load(a.folder)
    a.set_step("download", "done")
    b.set_step("template", "running")                                     # b's data was read before a's change
    a.update(channel="Lou Laughs")
    d = P.load(a.folder).data
    assert d["steps"]["download"]["state"] == "done" and d["steps"]["template"]["state"] == "running"
    assert d["channel"] == "Lou Laughs"


def test_many_threads_changing_one_project_lose_nothing(tmp_path):
    p = P.create(tmp_path, competitor_url="https://youtu.be/w2fdx42TqPg")
    errors = []

    def work(k: int) -> None:
        try:
            q = P.load(p.folder)
            for j in range(5):
                q.update(**{f"field_{k}_{j}": j})
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ths = [threading.Thread(target=work, args=(k,)) for k in range(6)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    d = P.load(p.folder).data
    assert not errors
    assert all(d[f"field_{k}_{j}"] == j for k in range(6) for j in range(5))
    assert sorted(x.name for x in p.folder.iterdir()) == ["project.json"]


def test_a_lock_left_by_a_program_that_died_is_broken(tmp_path, monkeypatch):
    p = P.create(tmp_path, competitor_url="https://youtu.be/w2fdx42TqPg")
    lock = p.folder / "project.json.lock"
    lock.write_text("OTHER-PC 1234")
    old = time.time() - P.LOCK_STALE_S - 5
    os.utime(lock, (old, old))
    t0 = time.time()
    p.update(channel="x")
    assert time.time() - t0 < 2 and not lock.exists() and P.load(p.folder).data["channel"] == "x"
    lock.write_text("OTHER-PC 1234")                                      # a fresh lock: waited for, then written
    monkeypatch.setattr(P, "LOCK_WAIT_S", 0.3)
    p.update(channel="y")
    assert P.load(p.folder).data["channel"] == "y"


def test_a_reader_that_meets_a_half_moment_tries_again(tmp_path, monkeypatch):
    f = tmp_path / "project.json"
    f.write_text('{"a": 1}', encoding="utf-8")
    calls = []
    real = Path.read_text

    def flaky(self, *a, **k):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("being replaced")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", flaky)
    assert P.read_json(f) == {"a": 1} and len(calls) == 3


def test_logs_go_to_the_logs_folder(tmp_path):
    p = P.create(tmp_path, competitor_url="https://youtu.be/w2fdx42TqPg")
    assert p.log_path("download", "raw") == p.folder / "logs" / "download_raw.log"
    assert p.log_path("convert") == p.folder / "logs" / "convert.log"
    assert (p.folder / "logs").is_dir()


def test_list_projects_newest_first_and_broken_ones_left_out(tmp_path):
    root = tmp_path / "root"
    for day, name in (("2026-10-15", "old"), ("2026-10-17", "new"), ("2026-10-16", "mid")):
        d = root / f"{day}_{name}"
        d.mkdir(parents=True)
        P.write_json(d / "project.json", P._new_data(d.name, None, None, None))
    (root / "2026-10-18_broken").mkdir()
    (root / "2026-10-18_broken" / "project.json").write_text("{not json", encoding="utf-8")
    (root / "no project here").mkdir()
    got = [p.folder.name for p in P.list_projects(root)]
    assert got == ["2026-10-17_new", "2026-10-16_mid", "2026-10-15_old"]
    assert P.list_projects(tmp_path / "nothing") == []
    with pytest.raises(FileNotFoundError):
        P.load(root / "no project here")


def test_the_default_root_is_the_apps_projects_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_PROJECTS", str(tmp_path / "Shared Projects"))
    p = P.create(competitor_url="https://youtu.be/w2fdx42TqPg")
    assert p.folder.parent == tmp_path / "Shared Projects"
    assert [x.folder for x in P.list_projects()] == [p.folder]
