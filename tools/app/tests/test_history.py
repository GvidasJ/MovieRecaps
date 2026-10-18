"""The History tab (mr_app.core.history): the events of real-looking project.json data (night 10's MikeMemesYou
project), failed steps, the board's dates and the Short found, the order, local times, the search, the date texts.
Run: python -m pytest tools/app/tests/test_history.py"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import history as H, paths, projects as P   # noqa: E402

TITLE = "The fact that nobody in the crew wanted to go is hilarious 🙋‍♂️😭"
NOTE = ("every file is there, but a check of 1_edit.xml found something to look at in Premiere: ['XML PERSON "
        "S34+S35+S36 at 00:01:41:44: the person speaking is not in the window (x 20-1060, y 525-1739): face at x "
        "374-1206, y 399-964 (who is there: RAW 121.95-124.20 s)', 'XML PERSON S77 at 00:02:15:55: the person "
        "speaking is not in the window']")
# night 10's project (work/t25/e2e/projects/2026-10-17_who-should-go/project.json), the long parts cut
WHO = {
    "version": 1, "name": "2026-10-17_who-should-go", "created": "2026-10-17T09:15:48+03:00",
    "updated": "2026-10-17T11:12:52+03:00", "computer": "Gvidas",
    "competitor": {"url": "https://www.youtube.com/shorts/VFLfcRr-65I", "title": "who should go?",
                   "id": "VFLfcRr-65I", "file": "who should go？ [VFLfcRr-65I].mp4"},
    "raw": {"url": "https://www.youtube.com/watch?v=-bIvihRhK50", "source_file": None,
            "title": "Young Rell's Crew - SNL", "id": "-bIvihRhK50",
            "file": "Young Rell's Crew - SNL [-bIvihRhK50].mp4"},
    "channel": "MikeMemesYou", "title": TITLE,
    "steps": {
        "download": {"state": "done", "started": "2026-10-17T09:15:48+03:00", "finished": "2026-10-17T09:16:02+03:00",
                     "error": None, "computer": "Gvidas"},
        "convert": {"state": "done", "started": "2026-10-17T09:16:02+03:00", "finished": "2026-10-17T09:16:02+03:00",
                    "error": None, "competitor": {"how": "keep"}, "raw": {"how": "keep"}},
        "template": {"state": "done", "started": "2026-10-17T10:12:15+03:00", "finished": "2026-10-17T10:12:15+03:00",
                     "error": None, "png": "template.png", "channel": "MikeMemesYou", "title": TITLE, "shrunk": True,
                     "scale_pct": 97.4, "why": "shrunk to 97 %: no break keeps both lines inside 920 px"},
        "title_check": {"state": "done", "started": "2026-10-17T09:16:02+03:00",
                        "finished": "2026-10-17T09:16:05+03:00", "error": None},
        "edit": {"state": "done", "started": "2026-10-17T10:12:15+03:00", "finished": "2026-10-17T11:12:52+03:00",
                 "error": None, "result": "OK with a note", "seconds": 3637.2, "failures": [], "notes": [NOTE]},
    },
}


def t(text):
    return datetime.fromisoformat(text)


def whats(evs):
    return [e.what for e in evs]


# ---------------------------------------------------------------------------------------------------------------------
# the events
# ---------------------------------------------------------------------------------------------------------------------

def test_a_real_projects_events_newest_first():
    evs = H.events([WHO])
    assert whats(evs) == ["Edit made · OK with a note", "Template made", "Converted", "Downloaded", "Link added"]
    assert [e.at for e in evs] == [t("2026-10-17T11:12:52+03:00"), t("2026-10-17T10:12:15+03:00"),
                                   t("2026-10-17T09:16:02+03:00"), t("2026-10-17T09:16:02+03:00"),
                                   t("2026-10-17T09:15:48+03:00")]
    assert all(e.name == "2026-10-17_who-should-go" and e.title == TITLE and e.channel == "MikeMemesYou"
               for e in evs)
    edit, template, converted, downloaded, link = evs
    assert edit.kind == H.NOTE and edit.detail.startswith("every file is there, but a check of 1_edit.xml")
    assert len(edit.detail) <= H.DETAIL_MAX and edit.detail.endswith("...")
    assert template.detail == "shrunk to 97 %: no break keeps both lines inside 920 px"
    assert converted.detail == "competitor kept as it was · RAW kept as it was"
    assert downloaded.detail == "competitor: who should go? · RAW: Young Rell's Crew - SNL"
    assert link.detail == "competitor: who should go?" and link.kind == H.INFO
    assert all(e.what != "Title check" for e in evs)                      # the title check is not an event


def test_pass_and_the_failures():
    ok = copy.deepcopy(WHO)
    ok["steps"]["edit"].update(result="PASS", notes=[])
    ok["steps"]["template"].update(shrunk=False)
    evs = H.events([ok])
    assert evs[0].what == "Edit made · PASS" and evs[0].kind == H.OK and evs[0].detail == ""
    assert evs[1].what == "Template made" and evs[1].detail == ""

    bad = copy.deepcopy(WHO)
    bad["steps"]["edit"] = {"state": "failed", "finished": "2026-10-17T11:00:00+03:00", "result": "PICTURE NOT FOUND",
                            "failures": ["the run stopped (exit 2): PICTURE NOT FOUND -- the competitor's sound is in "
                                         "the RAW but its picture is not"], "error": "the run stopped (exit 2)"}
    ev = H.events([bad])[0]
    assert (ev.what, ev.kind) == ("Edit failed · PICTURE NOT FOUND", H.FAIL)
    assert ev.detail.startswith("the run stopped (exit 2): PICTURE NOT FOUND")
    bad["steps"]["edit"].update(result="FAIL", failures=[])
    ev = H.events([bad])[0]
    assert (ev.what, ev.detail) == ("Edit failed", "the run stopped (exit 2)")

    dl = copy.deepcopy(WHO)
    dl["steps"] = {"download": {"state": "failed", "finished": "2026-10-17T09:15:50+03:00",
                                "error": "this video is private: only its owner can watch it"},
                   "edit": {"state": "waiting", "finished": None}}
    assert [(e.what, e.detail, e.kind) for e in H.events([dl])] == [
        ("Download failed", "this video is private: only its owner can watch it", H.FAIL),
        ("Link added", "competitor: who should go?", H.INFO)]
    stopped = copy.deepcopy(WHO)
    stopped["steps"]["edit"] = {"state": "waiting", "started": None, "finished": None, "result": "STOPPED"}
    assert "Edit" not in " ".join(whats(H.events([stopped])))          # Stop is not an event


def test_the_boards_dates_and_the_upload():
    p = copy.deepcopy(WHO)
    p["board"] = {"column": "uploaded", "moved_at": "2026-10-18T07:05:00+03:00",
                  "dates": {"downloaded": "2026-10-17T09:16:02+03:00",          # a step stands for it: not again
                            "fixed": "2026-10-17T15:40:00+03:00", "rendered": "2026-10-17T16:05:00+03:00",
                            "uploaded": "2026-10-18T07:05:00+03:00"},
                  "uploaded": {"at": "2026-10-18T07:00:24+03:00", "id": "pXBgisZ34JQ",
                               "url": "https://www.youtube.com/shorts/pXBgisZ34JQ",
                               "title": "The fact that nobody in the crew wanted to go is hilarious 😭 #usa",
                               "how": "auto"},
                  "pinned": True, "not_same": []}
    evs = H.events([p])
    assert whats(evs) == ["Uploaded", "Rendered", "Fixed", "Edit made · OK with a note", "Template made",
                          "Converted", "Downloaded", "Link added"]
    up = evs[0]
    assert up.at == t("2026-10-18T07:00:24+03:00") and up.kind == H.OK
    assert up.detail == "The fact that nobody in the crew wanted to go is hilarious 😭 #usa · found on YouTube"
    p["board"]["uploaded"] = None                                          # moved by hand: the board's date
    p["board"]["dates"]["edit"] = "2026-10-17T12:00:00+03:00"               # the edit step stands for it
    evs = H.events([p])
    assert evs[0].what == "Uploaded" and evs[0].at == t("2026-10-18T07:05:00+03:00") and evs[0].detail == ""
    assert whats(evs).count("Edit made · OK with a note") == 1 and "Edit made" not in whats(evs)
    p["steps"]["edit"] = {"state": "failed", "finished": "2026-10-17T11:00:00+03:00", "result": "FAIL",
                          "failures": ["x"]}
    assert "Edit made" in whats(H.events([p]))                             # moved there by hand after a failure
    p["board"]["uploaded"] = {"at": "2026-10-18T08:00:00+03:00", "how": "you", "title": ""}
    assert H.events([p])[0].detail == "marked by you"


def test_times_are_local_whatever_zone_they_were_written_in():
    p = {"name": "x", "created": "2026-10-17T06:15:48Z",
         "steps": {"download": {"state": "done", "finished": "2026-10-17T09:16:02"}},     # no offset: local
         "board": {"dates": {"fixed": "not a time", "rendered": None}}}
    evs = H.events([p])
    assert whats(evs) == ["Downloaded", "Link added"]                      # the bad dates are left out
    link = evs[1]
    assert link.at == datetime(2026, 10, 17, 6, 15, 48, tzinfo=timezone.utc)
    assert link.at.isoformat() == link.at.astimezone().isoformat()        # in this computer's time
    assert evs[0].at == datetime(2026, 10, 17, 9, 16, 2).astimezone()


def test_many_projects_merge_newest_first_from_dicts_objects_and_folders(tmp_path):
    other = {"name": "2026-10-18_the-part-is-hers", "channel": "Flick707", "title": "When she finally got the part 💀😭",
             "created": "2026-10-17T10:00:00+03:00",
             "steps": {"edit": {"state": "done", "finished": "2026-10-17T11:12:52+03:00", "result": "PASS"}}}
    proj = P.create(tmp_path / "Projects", competitor_url="https://www.youtube.com/shorts/4v3FsM0BzWQ",
                    name="The Funniest Tough Guy")
    proj.update(channel="LouLaughs", title="The Funniest Tough Guy of All Time 📞😭")
    evs = H.events([WHO, other, P.load(proj.folder), str(proj.folder), tmp_path / "missing", 42, None])
    names = [e.name for e in evs]
    assert names.count(proj.folder.name) == 2 and len(evs) == 5 + 2 + 2
    assert evs == sorted(evs, key=lambda e: e.at, reverse=True)
    same = [e for e in evs if e.at == t("2026-10-17T11:12:52+03:00")]
    assert [e.name for e in same] == ["2026-10-17_who-should-go", "2026-10-18_the-part-is-hers"]   # given order
    assert H.events([proj.folder / "project.json"])[0].title == "The Funniest Tough Guy of All Time 📞😭"


def test_the_real_projects_of_night_10_if_they_are_here():
    root = paths.work_dir() / "t25" / "e2e" / "projects"
    if not root.is_dir():
        pytest.skip("night 10's e2e projects are not on this computer")
    evs = H.events(sorted(root.iterdir()))
    assert {e.channel for e in evs} == {"LouLaughs", "MikeMemesYou", "Flick707"}
    assert {e.what for e in evs if e.what.startswith("Edit made")} == {"Edit made · PASS",
                                                                       "Edit made · OK with a note"}


# ---------------------------------------------------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def evs():
    flick = {"name": "2026-10-17_the-part-is-hers", "channel": "Flick707", "title": "When she finally got the part 💀😭",
             "created": "2026-10-17T10:00:00+03:00",
             "competitor": {"title": "Dakota Johnson Is Ready To Host SNL for the Third Time"},
             "steps": {"download": {"state": "done", "finished": "2026-10-17T10:00:30+03:00"}}}
    cafe = {"name": "2026-10-16_cafe", "channel": "LouLaughs", "title": "The Café Scene Nobody Saw Coming",
            "created": "2026-10-16T21:00:00+03:00"}
    return H.events([WHO, flick, cafe])


@pytest.mark.parametrize("text, names", [
    ("", None),
    ("crew", {"2026-10-17_who-should-go"}),
    ("CREW mike", {"2026-10-17_who-should-go"}),
    ("crew flick", set()),                                                # every word must be there
    ("cafe", {"2026-10-16_cafe"}), ("CAFÉ", {"2026-10-16_cafe"}),         # accents either way
    ("part 💀😭", {"2026-10-17_the-part-is-hers"}),                      # emoji ignored
    ("who-should-go", {"2026-10-17_who-should-go"}),                       # the folder name
    ("dakota", {"2026-10-17_the-part-is-hers"}),                           # the competitor's title (detail)
    ("97 %", {"2026-10-17_who-should-go"}),                                # the template's detail
    ("2026-10-16", {"2026-10-16_cafe"}),                                   # a day, by the folder names
    ("lou", {"2026-10-16_cafe"}),                                          # the channel's short name
])
def test_search(evs, text, names):
    got = H.search(evs, text)
    if names is None:
        assert got == evs
    else:
        assert {e.name for e in got} == names
    assert got == [e for e in evs if e in got]                             # in the timeline's order


def test_search_by_what_happened(evs):
    assert whats(H.search(evs, "edit made")) == ["Edit made · OK with a note"]
    assert {e.name for e in H.search(evs, "link added")} == {"2026-10-17_who-should-go", "2026-10-17_the-part-is-hers",
                                                             "2026-10-16_cafe"}


# ---------------------------------------------------------------------------------------------------------------------
# the dates as the app shows them
# ---------------------------------------------------------------------------------------------------------------------

def test_when_text_and_date_text():
    assert H.when_text(datetime(2026, 10, 18, 2, 44)) == "18 Oct 02:44"
    assert H.when_text(datetime(2026, 3, 5, 17, 3, 59)) == "5 Mar 17:03"
    assert H.date_text(datetime(2026, 10, 18, 2, 44)) == "18.10.2026"
    assert H.date_text(datetime(2026, 3, 5)) == "05.03.2026"
    utc = datetime(2026, 10, 17, 23, 34, 23, tzinfo=timezone.utc)          # shown in this computer's time
    local = utc.astimezone()
    assert H.when_text(utc) == f"{local.day} {H.MONTHS[local.month - 1]} {local:%H:%M}"
    three = datetime(2026, 10, 18, 2, 34, tzinfo=timezone(timedelta(hours=3)))
    assert H.when_text(three) == H.when_text(three.astimezone())
    assert [H.MONTHS[m - 1] for m in (1, 6, 9, 12)] == ["Jan", "Jun", "Sep", "Dec"]


def test_parse_time():
    assert H.parse_time("2026-10-17T09:15:48+03:00") == t("2026-10-17T09:15:48+03:00")
    assert H.parse_time(datetime(2026, 10, 17, 9, 0)).tzinfo is not None
    for bad in (None, "", "  ", "yesterday", 5, json):
        assert H.parse_time(bad) is None
