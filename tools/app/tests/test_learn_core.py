"""Night 11, section 3 (the simpler Learn tab), the core: finding the run of a fixed .prproj by itself
(learnkit.find_run / search_run), a dropped folder, the watcher for a new fixed.prproj, the auto names, the form,
the copy + learn queue, the check card (learn_check) and the hub files (learn_hub: standing rules, wishes, the learn
queue, tonight's prompt). Tiny fake .prproj files (gzip XML, the way prproj.py reads them) and fake run folders; the
real fixes (video11, video12, chocolate) read-only, skipped when they are not on this computer.

Run: python -m pytest tools/app/tests/test_learn_core.py"""
from __future__ import annotations

import base64
import gzip
import json
import os
import struct
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import learn_check as C, learn_hub as H, learnkit as K   # noqa: E402

REPO = Path(__file__).resolve().parents[3]
TICKS = 254016000000
ELSEWHERE = r"C:\Users\nobody-learn-core\Desktop\bh\MovieRecaps"      # another computer's repo (never here)


@pytest.fixture(autouse=True)
def _own_settings(tmp_path, monkeypatch):
    """This computer's settings never read (machine.py / settings.py): a settings file of the test's own."""
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(tmp_path / "settings.json"))


# ---------------------------------------------------------------------------------------------------------------------
# fakes: a .prproj and a run folder
# ---------------------------------------------------------------------------------------------------------------------

def _flat(text: str) -> str:
    """A Premiere text flatbuffer prproj.flat_text reads: a font name, then the text (length-prefixed, NUL-ended)."""
    def s(x: str) -> bytes:
        b = x.encode("utf-8")
        return struct.pack("<I", len(b)) + b + b"\0"
    return base64.b64encode(s("Arial") + s(text) + b"\0").decode()


def fake_prproj(path: Path, media: list[dict], seqs: list[dict]) -> Path:
    """A .prproj as prproj.read reads it. media: [{path, file_path?, history?, w, h, fps, dur}]; seqs: [{name, fps, w,
    h, video: [[(start, end, media #, src_in), ...] per track], audio: [...], captions: [(start, end, text)]}]."""
    root = ET.Element("PremiereData", Version="3")
    n = [100]

    def new_id() -> str:
        n[0] += 1
        return str(n[0])
    for i, m in enumerate(media):
        vs = new_id()
        v = ET.SubElement(root, "VideoStream", ObjectID=vs)
        ET.SubElement(v, "FrameRect").text = f"0,0,{m.get('w', 1920)},{m.get('h', 1080)}"
        ET.SubElement(v, "FrameRate").text = str(int(round(TICKS / float(m.get("fps", 30.0)))))
        ET.SubElement(v, "Duration").text = str(int(round(float(m.get("dur", 60.0)) * TICKS)))
        me = ET.SubElement(root, "Media", ObjectUID=f"m{i}")
        ET.SubElement(me, "VideoStream", ObjectRef=vs)
        for k, h in enumerate(m.get("history") or []):
            ET.SubElement(me, f"MediaFileHistory{k}").text = h
        ET.SubElement(me, "FilePath").text = m.get("file_path") or m["path"]
        ET.SubElement(me, "ActualMediaFilePath").text = m["path"]
    for s in seqs:
        se = ET.SubElement(root, "Sequence", ObjectUID=new_id())
        ET.SubElement(se, "Name").text = s.get("name", "Recreated Edit (Premiere)")
        tgs = ET.SubElement(se, "TrackGroups")
        groups = (("VideoTrackGroup", "VideoClipTrackItem", s.get("video") or []),
                  ("AudioTrackGroup", "AudioClipTrackItem", s.get("audio") or []),
                  ("DataTrackGroup", "CaptionDataClipTrackItem", [s["captions"]] if s.get("captions") else []))
        for gtag, itag, tracks in groups:
            gid = new_id()
            ET.SubElement(ET.SubElement(tgs, "TrackGroup"), "Second", ObjectRef=gid)
            g = ET.SubElement(root, gtag, ObjectID=gid)
            inner = ET.SubElement(g, "TrackGroup")
            ET.SubElement(inner, "FrameRate").text = str(int(round(TICKS / float(s.get("fps", 30.0)))))
            if gtag == "VideoTrackGroup":
                ET.SubElement(g, "FrameRect").text = f"0,0,{s.get('w', 1080)},{s.get('h', 1920)}"
            trs = ET.SubElement(inner, "Tracks")
            for items in tracks:
                tuid = f"t{new_id()}"
                ET.SubElement(trs, "Track", ObjectURef=tuid)
                t = ET.SubElement(root, "ClipTrack", ObjectUID=tuid)
                tis = ET.SubElement(ET.SubElement(ET.SubElement(t, "ClipTrack"), "ClipItems"), "TrackItems")
                for it in items:
                    iid = new_id()
                    ET.SubElement(tis, "TrackItem", ObjectRef=iid)
                    x = ET.SubElement(root, itag, ObjectID=iid)
                    cti = ET.SubElement(x, "ClipTrackItem")
                    ti = ET.SubElement(cti, "TrackItem")
                    ET.SubElement(ti, "Start").text = str(int(round(it[0] * TICKS)))
                    ET.SubElement(ti, "End").text = str(int(round(it[1] * TICKS)))
                    if gtag == "DataTrackGroup":
                        bid = new_id()
                        ET.SubElement(ET.SubElement(x, "BlockVector"), "BlockVectorItem", ObjectRef=bid)
                        ET.SubElement(ET.SubElement(root, "CaptionBlock", ObjectID=bid), "FormattedTextData").text = \
                            _flat(it[2])
                        continue
                    _start, _end, mi, src_in = it
                    sub_id, clip_id, src_id = new_id(), new_id(), new_id()
                    ET.SubElement(cti, "SubClip", ObjectRef=sub_id)
                    sub = ET.SubElement(root, "SubClip", ObjectID=sub_id)
                    ET.SubElement(sub, "Name").text = "clip"
                    ET.SubElement(sub, "Clip", ObjectRef=clip_id)
                    c = ET.SubElement(ET.SubElement(root, "VideoClip", ObjectID=clip_id), "Clip")
                    ET.SubElement(c, "InPoint").text = str(int(round(src_in * TICKS)))
                    ET.SubElement(c, "OutPoint").text = str(int(round((src_in + it[1] - it[0]) * TICKS)))
                    ET.SubElement(c, "Source", ObjectRef=src_id)
                    ms = ET.SubElement(root, "VideoMediaSource", ObjectID=src_id)
                    ET.SubElement(ET.SubElement(ms, "MediaSource"), "Media", ObjectURef=f"m{mi}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(ET.tostring(root, encoding="utf-8")))
    return path


def _xmeml(clips: list[tuple[float, float, float]], fps: int = 30) -> str:
    """A 1_edit.xml the tool reads back (export_xml_edl.parse_premiere_xml): V1 and A1 clip items, [(start, end, RAW
    in)] in seconds."""
    def item(prefix: str, i: int, t0: float, t1: float, r0: float) -> str:
        return (f"<clipitem id='{prefix}{i}'><name>raw.mp4</name><start>{round(t0 * fps)}</start>"
                f"<end>{round(t1 * fps)}</end><in>{round(r0 * fps)}</in><out>{round((r0 + t1 - t0) * fps)}</out>"
                f"<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate></clipitem>")
    v = "".join(item("c", i, *c) for i, c in enumerate(clips))
    a = "".join(item("a", i, *c) for i, c in enumerate(clips))
    dur = round(max(c[1] for c in clips) * fps)
    return (f"<?xml version='1.0' encoding='UTF-8'?><xmeml version='4'><sequence><rate><timebase>{fps}</timebase>"
            f"<ntsc>FALSE</ntsc></rate><duration>{dur}</duration><media><video><format><samplecharacteristics>"
            f"<width>1080</width><height>1920</height></samplecharacteristics></format><track>{v}</track></video>"
            f"<audio><track>{a}</track></audio></media></sequence></xmeml>")


def _srt(caps: list[tuple[float, float, str]]) -> str:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(caps, start=1))


RAW = {"w": 640, "h": 480, "fps": 30.0, "dur": 30.7}


def fake_run(d: Path, *, raw_abs: str | None = None, source: str = r"C:\Downloads\Nice Commercial [abc].mp4",
             raw: dict | None = None, finished: bool = True, clips=((0.0, 5.0, 1.0), (5.0, 9.0, 8.0)),
             captions=((0.0, 2.0, "chocolate is"), (2.0, 4.0, "so good")), gpu: str = "NVIDIA GeForce RTX 5080",
             fast: bool = True, result: str = "PASS", code: str = "0123456789abcdef0123") -> Path:
    """A finished run folder of the tool: 1_edit.xml, 2_captions.srt, extras\\cutlist.json (raw / competitor /
    provenance), extras\\media (raw.mp4, competitor_ref.mp4), the logs and report.md."""
    r = dict(RAW, **(raw or {}))
    media = d / "extras" / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "raw.mp4").write_bytes(b"r" * 3000)
    (media / "competitor_ref.mp4").write_bytes(b"c" * 1000)
    cl = {"version": 1,
          "competitor": {"file": "media/competitor_ref.mp4", "file_abs": str(media / "competitor_ref.mp4") if raw_abs
                         is None else raw_abs.replace("raw.mp4", "competitor_ref.mp4"),
                         "source_path": r"C:\Downloads\The Short [xyz].mp4", "width": 1080, "height": 1920,
                         "fps": "30/1", "duration_s": 20.0},
          "raw": {"file": "media/raw.mp4", "file_abs": raw_abs or str(media / "raw.mp4"), "source_path": source,
                  "width": r["w"], "height": r["h"], "fps": f"{int(r['fps'] * 1000)}/1000", "duration_s": r["dur"]},
          "segments": [],
          "provenance": {"tool": "match_cuts", "version": "0.1.0", "code_hash": code,
                         "analysis_params": {"seed": 1}}}
    (d / "extras" / "cutlist.json").write_text(json.dumps(cl), encoding="utf-8")
    (d / "1_edit.xml").write_text(_xmeml(list(clips)), encoding="utf-8")
    (d / "2_captions.srt").write_text(_srt(list(captions)), encoding="utf-8")
    (d / "extras" / "match_cuts.log").write_text(
        "2026-10-18 01:36:19,000 INFO match_cuts match_cuts 0.1.0: competitor=x raw=y\n"
        f"2026-10-18 01:36:22,354 INFO match_cuts time estimate: the whole run about 4 min ({gpu}, "
        f"{'--fast' if fast else 'thorough'}, competitor 24 s, RAW 1 min; from 1 earlier run(s) on this machine)\n",
        encoding="utf-8")
    (d / "extras" / "batch_console.log").write_text(
        f"python.exe -m match_cuts --competitor c.mp4 --raw r.mp4 --run-dir {d} --premiere"
        f"{' --fast' if fast else ''} --speed 100\n\nmatch_cuts result: {result}\n", encoding="utf-8")
    if finished:
        (d / "extras" / "report.md").write_text("# report\n", encoding="utf-8")
    return d


def user_project(path: Path, raw_path: str, *, history: list[str] | None = None, raw: dict | None = None,
                 clips=((0.0, 5.0, 1.0), (5.0, 9.0, 8.0)), captions=((0.0, 2.0, "chocolate is"), (2.0, 4.0, "so good")),
                 extra_media: list[str] | None = None) -> Path:
    """A fixed project: one sequence playing ``raw_path`` on V1 and A1 (``clips``: (start, end, RAW in)), its
    captions on C1, and other pictures (``extra_media``: the frame PNG ...) on V2."""
    r = dict(RAW, **(raw or {}))
    media = [{"path": raw_path, "history": history or [], **r}]
    v2 = []
    for k, p in enumerate(extra_media or []):
        media.append({"path": p, "w": 1080, "h": 1920, "fps": 60.0, "dur": 43200.0})
        v2.append((0.0, 9.0, k + 1, 0.0))
    v1 = [(a, b, 0, r0) for a, b, r0 in clips]
    seq = {"name": "Recreated Edit (Premiere)", "fps": 30.0, "video": [v1] + ([v2] if v2 else []), "audio": [v1],
           "captions": list(captions)}
    return fake_prproj(path, media, [seq])


# ---------------------------------------------------------------------------------------------------------------------
# learn_hub: standing rules, wishes, the learn queue, tonight's prompt
# ---------------------------------------------------------------------------------------------------------------------

def test_standing_rules_are_saved_in_a_file_and_read_back_without_the_header(tmp_path):
    hub = tmp_path / "Projects"
    assert H.read_rules(hub) == ""                                         # no file yet
    assert H.save_rules(hub, "Never touch my audio.\nAlways keep the headline.\n") == ""
    text = (hub / H.RULES_FILE).read_text(encoding="utf-8")
    assert text.startswith("# Standing rules") and "Never touch my audio." in text
    assert H.read_rules(hub) == "Never touch my audio.\nAlways keep the headline."
    assert H.save_rules(hub, "") == "" and H.read_rules(hub) == ""


def test_requests_md_starts_with_night_11s_two_later_items_word_for_word(tmp_path):
    doc = REPO / "docs" / "night11_prompt.md"
    if doc.is_file():                                                      # the user's own words, from the prompt
        later = doc.read_text(encoding="utf-8").split("## Later", 1)[1]
        bullets = [ln[2:].strip() for ln in later.splitlines() if ln.startswith("- ")]
        assert list(H.SEED_WISHES) == bullets
    hub = tmp_path / "Projects"
    assert H.add_wish(hub, "A dark mode for the board", computer="laptop") == ""
    text = (hub / H.WISHES_FILE).read_text(encoding="utf-8")
    assert text.startswith("# Wishes for future nights")
    for w in H.SEED_WISHES:
        assert w in text                                                   # word for word, bold marks and all
    ws = H.read_wishes(hub)
    assert [w.text for w in ws] == list(H.SEED_WISHES) + ["A dark mode for the board"]   # newest last
    assert ws[-1].computer == "laptop" and len(ws[-1].date) == 16 and not ws[-1].done
    assert ws[0].date == "2026-10-18"
    assert H.add_wish(hub, "   ") != ""                                    # an empty wish: said, not saved
    assert len(H.read_wishes(hub)) == 3


def test_a_start_leaves_a_requests_md_that_is_there_untouched(tmp_path):
    """ensure_wishes runs at every start, on both computers: an existing requests.md is not rewritten (its time
    stays), and a change that changes nothing writes nothing."""
    hub = tmp_path / "hub"
    assert H.ensure_wishes(hub) == ""
    f = hub / H.WISHES_FILE
    old = time.time() - 3600
    os.utime(f, (old, old))
    assert H.ensure_wishes(hub) == ""
    assert f.stat().st_mtime == pytest.approx(old, abs=1)
    assert H.save_rules(hub, "keep the cut points") == ""
    r = hub / H.RULES_FILE
    os.utime(r, (old, old))
    assert H.save_rules(hub, "keep the cut points") == ""                  # the same rules again: no write
    assert r.stat().st_mtime == pytest.approx(old, abs=1)


def test_a_wish_of_several_lines_and_a_bullet_written_by_hand_are_read(tmp_path):
    hub = tmp_path / "hub"
    assert H.add_wish(hub, "First line\nsecond line", computer="PC") == ""
    with (hub / H.WISHES_FILE).open("a", encoding="utf-8") as f:
        f.write("- [x] 2026-10-19 09:00 · PC: done already\n- my own idea, typed in Notepad\n")
    ws = H.read_wishes(hub)
    assert ws[2].text == "First line\nsecond line"
    assert ws[3].done and ws[3].text == "done already"
    assert ws[4].text == "my own idea, typed in Notepad" and not ws[4].done


def test_the_learn_queue_keeps_one_checkbox_line_per_project(tmp_path):
    hub = tmp_path / "Shared" / "Projects"
    a = H.QueueItem("video13", prproj=str(hub / "2026-10-18_a" / "fixed.prproj"), run=str(hub / "2026-10-18_a" / "edit"),
                    raw="Nice Commercial [abc].mp4", competitor="The Short [xyz].mp4", date="2026-10-18 21:05",
                    computer="laptop", copies=r"finished\video13\video13_fixed.prproj, output\laptop_video13")
    b = H.QueueItem("video14", prproj=str(tmp_path / "Desktop" / "x.prproj"), run=str(tmp_path / "Shared" / "b" / "edit"),
                    computer="PC")
    assert H.queue_add(hub, a) == "" and H.queue_add(hub, b) == ""
    lines = [ln for ln in (hub / H.QUEUE_FILE).read_text(encoding="utf-8").splitlines() if ln.startswith("- [")]
    assert len(lines) == 2
    assert lines[0] == ("- [ ] video13 | prproj: .\\2026-10-18_a\\fixed.prproj | run: .\\2026-10-18_a\\edit | "
                        "RAW: Nice Commercial [abc].mp4 | competitor: The Short [xyz].mp4 | date: 2026-10-18 21:05 | "
                        "computer: laptop | copies: finished\\video13\\video13_fixed.prproj, output\\laptop_video13")
    assert "run: ..\\b\\edit" in lines[1]                                  # next to the hub: ..\
    assert str(tmp_path / "Desktop" / "x.prproj") in lines[1]              # elsewhere: in full
    items = H.queue_items(hub)
    assert [i.name for i in items] == ["video13", "video14"]
    assert items[0].prproj == str(hub / "2026-10-18_a" / "fixed.prproj") and items[0].raw == "Nice Commercial [abc].mp4"
    assert items[1].date and items[1].computer == "PC"                    # the date filled in
    # the same project again (waiting): replaced in place, not doubled
    assert H.queue_add(hub, H.QueueItem("video13", prproj=a.prproj, run=a.run, note="dropped again")) == ""
    assert [i.name for i in H.queue_items(hub)] == ["video13", "video14"]
    assert H.queue_items(hub)[0].note == "dropped again"
    # learned: ticked, no longer waiting
    assert H.queue_done(hub, "video13") == ""
    assert [i.name for i in H.queue_items(hub)] == ["video14"]
    done = [i for i in H.queue_items(hub, include_done=True) if i.done]
    assert [i.name for i in done] == ["video13"] and done[0].learned
    assert "- [x] video13 | prproj: .\\2026-10-18_a\\fixed.prproj" in (hub / H.QUEUE_FILE).read_text(encoding="utf-8")
    assert "no waiting project" in H.queue_done(hub, "video99")
    # learned before, queued again: a new line at the end
    assert H.queue_add(hub, H.QueueItem("video13", prproj=a.prproj, run=a.run)) == ""
    assert [i.name for i in H.queue_items(hub, include_done=True)] == ["video13", "video14", "video13"]


def test_queue_paths_are_right_on_the_other_computer(tmp_path):
    pc_hub = tmp_path / "D" / "Shared" / "Projects"                        # the PC's D:\Shared\Projects
    laptop_hub = tmp_path / "net" / "Shared" / "Projects"                  # the same folder seen from the laptop
    item = H.QueueItem("video13", prproj=str(laptop_hub / "p" / "fixed.prproj"), run=str(laptop_hub.parent / "q" / "edit"))
    assert H.queue_add(laptop_hub, item) == ""
    pc_hub.mkdir(parents=True)
    (pc_hub / H.QUEUE_FILE).write_bytes((laptop_hub / H.QUEUE_FILE).read_bytes())   # one file, two paths to it
    got = H.queue_items(pc_hub)[0]
    assert got.prproj == str(pc_hub / "p" / "fixed.prproj") and got.run == str(pc_hub.parent / "q" / "edit")
    assert H.for_pc(r"\\192.168.101.11\Shared\Projects\p\fixed.prproj") == r"D:\Shared\Projects\p\fixed.prproj"
    assert H.for_pc(r"\\192.168.101.11\SharedX\a") == r"\\192.168.101.11\SharedX\a"
    assert H.for_pc(r"C:\x\y") == r"C:\x\y"
    assert H.portable(r"D:\Projects\a\b.prproj", r"D:\Projects") == ".\\a\\b.prproj"
    assert H.portable(r"D:\Other\b.prproj", r"D:\Projects") == r"D:\Other\b.prproj"   # never ..\ out of a drive root
    assert H.portable(r"E:\x.prproj", r"D:\Shared\Projects") == r"E:\x.prproj"


def test_a_hand_edited_queue_line_is_still_read(tmp_path):
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / H.QUEUE_FILE).write_text("# Learn queue\n\nsome words\n- [ ]   video20 |run: .\\a\\edit|  prproj: C:\\x.prproj"
                                    " | colour: red\n- [X] video21\n", encoding="utf-8")
    items = H.queue_items(hub, include_done=True)
    assert [(i.name, i.done) for i in items] == [("video20", False), ("video21", True)]
    assert items[0].prproj == r"C:\x.prproj" and items[0].extra == {"colour": "red"}


def test_the_hub_files_never_raise_when_the_folder_cannot_be_used(tmp_path):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("x")
    hub = blocker / "Projects"                                             # a file where the folder should be
    assert H.save_rules(hub, "x") != ""
    assert H.add_wish(hub, "x", computer="PC") != ""
    assert H.queue_add(hub, H.QueueItem("video1")) != ""
    assert H.read_rules(hub) == "" and H.read_wishes(hub) == [] and H.queue_items(hub) == []
    assert "cannot be reached" in H.hub_status(blocker / "a" / "b")
    assert H.hub_status(tmp_path) == "" and H.hub_status(tmp_path / "new") == ""


def test_the_next_night_comes_after_the_newest_report(tmp_path):
    (tmp_path / "reports").mkdir()
    assert H.next_night(tmp_path) == 1
    for n in ("night9.md", "night10.md", "overnight2.md", "night10-notes.md"):
        (tmp_path / "reports" / n).write_text("x")
    assert H.next_night(tmp_path) == 11
    assert H.last_report(tmp_path) == "night10.md"
    (tmp_path / "reports" / "day11.md").write_text("x")                    # day 12 was a day run: reports\day12.md
    assert H.next_night(tmp_path) == 12 and H.last_report(tmp_path) == "day11.md"
    assert "reports\\day11.md" in H.tonight_prompt(tmp_path / "hub", tmp_path)
    assert H.next_night(REPO) >= 11                                        # this repository


def test_tonight_prompt_has_the_start_the_rules_every_queued_project_every_wish_and_the_finish(tmp_path):
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    (repo / "reports" / "night11.md").write_text("x")
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/claude/new-session-ol4era\n")
    hub = tmp_path / "Projects"
    assert H.save_rules(hub, "Never change the audio levels.") == ""
    assert H.add_wish(hub, "A Stats tab for my channels", computer="PC") == ""
    assert H.add_wish(hub, "Faster refine step", computer="laptop") == ""
    assert H.queue_add(hub, H.QueueItem("video13", prproj=str(hub / "p1" / "fixed.prproj"), run=str(hub / "p1" / "edit"),
                                        raw="Nice [abc].mp4", competitor="Short [xyz].mp4", computer="laptop",
                                        date="2026-10-18 21:05",
                                        copies=r"finished\video13\video13_fixed.prproj, output\laptop_video13")) == ""
    assert H.queue_add(hub, H.QueueItem("video14", prproj=r"C:\Users\gvida\Desktop\chocolate.prproj",
                                        run=r"\\192.168.101.11\Shared\2026-10-18_x\edit", computer="PC")) == ""
    assert H.queue_add(hub, H.QueueItem("video9", prproj=r"C:\old.prproj", run=r"C:\old")) == ""
    assert H.queue_done(hub, "video9") == ""
    p = H.tonight_prompt(hub, repo)
    assert p.startswith("# Night 12 — MovieRecaps (14 hours, PC)")
    assert "Branch: `claude/new-session-ol4era`" in p
    assert "`shutdown /a`, then `shutdown /s /t 54000`" in p and "`shutdown /a`, then `shutdown /s /t 120`" in p
    assert "Read `reports\\night11.md`" in p and "Write `reports\\night12.md`" in p
    assert "Commit and push after every finished section" in p and "Keep all existing tests passing" in p
    assert "Never change the audio levels." in p
    for name, path in (("video13", str(hub / "p1" / "fixed.prproj")), ("video13", str(hub / "p1" / "edit")),
                       ("video14", r"C:\Users\gvida\Desktop\chocolate.prproj"),
                       ("video14", r"D:\Shared\2026-10-18_x\edit")):           # the laptop's path as the PC sees it
        assert f"**{name}**" in p and f"`{path}`" in p
    assert "Nice [abc].mp4" in p and "Short [xyz].mp4" in p and "output\\laptop_video13" in p
    assert "video9" not in p                                               # learned already
    assert "Learn **only**: captions (words, timing, look), cuts, and video position/scale." in p
    assert ("**Ignore:** the headline PNG (it's the same in all videos), memes, music, all audio, pitch, and my trims "
            "at cut points and at the ending (I do those by hand).") in p
    assert "before → after" in p
    for w in list(H.SEED_WISHES) + ["A Stats tab for my channels", "Faster refine step"]:
        assert w in p
    order = [p.index(x) for x in ("## 0. Start", "## 1. Standing rules", "## 2. Learn from my fixes", "## 3. My wishes",
                                  "## 4. Finish")]
    assert order == sorted(order)
    empty = H.tonight_prompt(tmp_path / "empty_hub", repo)                 # nothing queued: start and finish only
    assert "## 0. Start" in empty and "## 1. Finish" in empty and "Learn from my fixes" not in empty


# ---------------------------------------------------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------------------------------------------------

def test_next_free_name_counts_finished_output_test_cases_and_the_queue(tmp_path):
    repo = tmp_path / "repo"
    assert K.next_free_name(repo) == "video1"
    for d in ([f"finished/video{n}" for n in range(1, 13)] + ["finished/mike005", "output/video1-2",
                                                              "output/laptop_video9", "tests/real/video10-fixed",
                                                              "tests/real/video017-fixed", "tests/real/video021-fixed",
                                                              "output/Video30extra"]):
        (repo / d).mkdir(parents=True)
    assert K.next_free_name(repo) == "video13"                             # video017 / video021: the batch's numbers
    (repo / "output" / "laptop_video14").mkdir()
    assert K.next_free_name(repo) == "video15"
    hub = tmp_path / "hub"
    assert H.queue_add(hub, H.QueueItem("video15", computer="laptop")) == ""
    assert K.next_free_name(repo, hub) == "video16"                        # queued on the other computer
    assert K.next_free_name(repo, hub, prefix="mike") == "mike006"         # a padded series stays padded


def test_next_free_name_of_this_repository():
    name = K.next_free_name()
    n = int(name[len("video"):])
    assert name.startswith("video") and n >= 13
    assert not (REPO / "finished" / name).exists() and not (REPO / "output" / f"laptop_{name}").exists()
    if (REPO / "finished" / "video12").is_dir() and not any(
            (REPO / d / f"{p}video{k}").exists() for d in ("finished", "output") for p in ("", "laptop_")
            for k in range(13, 40)):
        assert name == "video13"                                           # today: finished holds video1 ... video12


def test_auto_names(tmp_path):
    repo = tmp_path / "repo"
    (repo / "finished" / "video3").mkdir(parents=True)
    fixed = repo / "finished" / "video3" / "video3_fixed.prproj"
    assert K.auto_name(fixed, repo=repo) == "video3"                       # already at the learn place
    assert K.auto_name(tmp_path / "video11_fixed.prproj", repo=repo) == "video11"
    assert K.auto_name(tmp_path / "laptop013_fixed.prproj", repo=repo) == "laptop013"
    assert K.auto_name(tmp_path / "fixed.prproj", repo=repo) == "video4"   # the next free one
    assert K.auto_name(tmp_path / "chocolate.prproj", repo=repo) == "video4"
    assert K.auto_name(tmp_path / "fixed.prproj", repo / "output" / "laptop_video7", repo=repo) == "video7"
    hub = tmp_path / "hub"
    assert H.queue_add(hub, H.QueueItem("video4", prproj=str(tmp_path / "fixed.prproj"), computer="PC")) == ""
    assert K.auto_name(tmp_path / "fixed.prproj", repo=repo, queue_folder=hub) == "video4"   # dropped again
    assert K.auto_name(tmp_path / "other.prproj", repo=repo, queue_folder=hub) == "video5"


# ---------------------------------------------------------------------------------------------------------------------
# finding the run
# ---------------------------------------------------------------------------------------------------------------------

def test_the_run_is_found_from_the_media_the_project_plays(tmp_path):
    run = fake_run(tmp_path / "output" / "video31")
    pr = user_project(tmp_path / "Desktop" / "mine.prproj", str(run / "extras" / "media" / "raw.mp4"),
                      extra_media=[str(run / "extras" / "media" / "frame.png")])
    cands = K.find_run(pr, [tmp_path / "output"], repo=tmp_path)
    assert cands and cands[0].run_dir == run and "exact" in cands[0].ways and cands[0].score >= 100
    assert "your project plays its media" in cands[0].how and cands[0].finished
    assert cands[0].sequence.startswith("'Recreated Edit (Premiere)'") and cands[0].played == 1.0


def test_a_run_moved_from_the_laptop_is_found_by_its_folder_and_its_own_cut_list(tmp_path):
    laptop_run = ELSEWHERE + r"\input\downloads\2026-10-18_test-x\edit"
    shared = tmp_path / "Shared"
    proj = shared / "2026-10-18_test-x"
    run = fake_run(proj / "edit", raw_abs=laptop_run + r"\extras\media\raw.mp4")
    (proj / "project.json").write_text("{}", encoding="utf-8")
    pr = user_project(tmp_path / "Desktop" / "chocolate.prproj", laptop_run + r"\extras\media\raw.mp4")
    s = K.search_run(pr, [shared], repo=tmp_path)
    assert s.candidates[0].run_dir == run and s.candidates[0].project_folder == proj
    assert {"origin", "folder"} <= set(s.candidates[0].ways) and "exact" not in s.candidates[0].ways
    assert "copied or moved here since" in s.candidates[0].how and s.problems == []


def test_a_relinked_raw_and_the_laptops_old_path_in_the_media_history(tmp_path):
    laptop_run = ELSEWHERE + r"\output\Video1-2"
    out = tmp_path / "output"
    run = fake_run(out / "laptop_video12", raw_abs=laptop_run + r"\extras\media\raw.mp4",
                   source=r"\\192.168.101.11\Shared\Tmrw\Video1\office episode.mp4", raw={"w": 1920, "h": 1080,
                                                                                          "dur": 1331.08})
    other = fake_run(out / "video1-2", source=r"C:\input\videos\video1\raw.mp4", raw={"w": 1912, "h": 1072, "dur": 716.5})
    pr = user_project(tmp_path / "finished" / "video12" / "video12_fixed.prproj",
                      r"D:\Shared\Tmrw\Video1\office episode.mp4", raw={"w": 1920, "h": 1080, "dur": 1331.08},
                      history=[r"\\?\C:\Users\arnas\Desktop\e\Copied_guess_POPW\raw.mp4"],
                      extra_media=[laptop_run + r"\extras\media\frame.png"])
    s = K.search_run(pr, [out], repo=tmp_path)
    assert [c.run_dir for c in s.candidates] == [run]                      # the same-name folder holds another video
    assert {"origin", "source", "name"} <= set(s.candidates[0].ways)
    assert any(r.startswith(K.show(other, tmp_path)) and "another video" in r and "1912x1072" in r for r in s.rejected)
    # the relinked path's history: a project whose media were relinked away from the run (MediaFileHistory)
    pr2 = user_project(tmp_path / "d2" / "x.prproj", r"E:\somewhere\renamed.mp4", raw={"w": 1920, "h": 1080,
                                                                                        "dur": 1331.08},
                       history=[laptop_run + r"\extras\media\raw.mp4"])
    s2 = K.search_run(pr2, [out], repo=tmp_path)
    assert s2.candidates and s2.candidates[0].run_dir == run and "origin" in s2.candidates[0].ways
    assert any("another name" in n for n in s2.candidates[0].notes)        # renamed.mp4: by its size, rate, length


def test_a_fixed_prproj_saved_in_a_project_folder_finds_its_edit(tmp_path):
    proj = tmp_path / "Projects" / "2026-10-18_nice"
    run = fake_run(proj / "edit", raw_abs=ELSEWHERE + r"\input\downloads\2026-10-18_nice\edit\extras\media\raw.mp4",
                   source=ELSEWHERE + r"\input\downloads\2026-10-18_nice\Nice Commercial [abc].mp4")
    fake_run(proj / "edit-2", finished=False,
             source=ELSEWHERE + r"\input\downloads\2026-10-18_nice\Nice Commercial [abc].mp4")
    (proj / "project.json").write_text("{}", encoding="utf-8")
    pr = user_project(proj / "fixed.prproj", r"D:\nowhere\Nice Commercial [abc].mp4")
    cands = K.find_run(pr, [], repo=tmp_path)                              # no roots: only the project.json way
    assert cands[0].run_dir == run and cands[0].ways == ["project"] and cands[0].project_folder == proj
    assert cands[1].run_dir == proj / "edit-2" and not cands[1].finished and cands[1].score < cands[0].score
    assert any("has not finished" in n for n in cands[1].notes)


def test_the_name_finds_output_laptop_name(tmp_path):
    run = fake_run(tmp_path / "output" / "laptop_video77", raw_abs=r"C:\other-lc\run\extras\media\raw.mp4",
                   source=r"C:\other-lc\src.mp4")
    pr = user_project(tmp_path / "x" / "video77_fixed.prproj", r"C:\nowhere-lc\raw.mp4")
    cands = K.find_run(pr, [tmp_path / "output"], repo=tmp_path)
    assert [c.run_dir for c in cands] == [run] and cands[0].ways == ["name"] and cands[0].score == 60
    pr2 = user_project(tmp_path / "x" / "video7_fixed.prproj", r"C:\nowhere-lc\raw.mp4")   # video7 is not video77
    assert K.find_run(pr2, [tmp_path / "output"], repo=tmp_path) == []


def test_copies_of_one_run_are_one_find_and_the_copy_at_the_learn_place_comes_first(tmp_path):
    out = tmp_path / "output"
    orig = fake_run(out / "Super Troopers (2001)")
    copy = out / "laptop_video31"
    import shutil
    shutil.copytree(orig, copy)                                            # byte for byte: the same cut list
    pr = user_project(tmp_path / "finished" / "video31" / "video31_fixed.prproj", str(orig / "extras" / "media" / "raw.mp4"))
    cands = K.find_run(pr, [out], repo=tmp_path)
    assert [c.run_dir for c in cands] == [copy, orig]
    assert cands[0].score == cands[1].score and cands[0].same_as == [orig] and cands[1].same_as == [copy]
    assert any("nothing to copy" in n for n in cands[0].notes)


def test_a_run_whose_edit_plays_other_moments_of_the_same_raw_is_not_taken(tmp_path):
    out = tmp_path / "output"
    good = fake_run(out / "run-a")
    fake_run(out / "laptop_video40", clips=((0.0, 5.0, 20.0), (5.0, 9.0, 27.0)))   # the same RAW, other moments
    pr = user_project(tmp_path / "x" / "video40_fixed.prproj", str(good / "extras" / "media" / "raw.mp4"))
    s = K.search_run(pr, [out], repo=tmp_path)
    assert [c.run_dir for c in s.candidates] == [good]
    assert any("laptop_video40" in r and "plays only 0 %" in r for r in s.rejected)


def test_runs_in_batch_folders_are_searched_too(tmp_path):
    run = fake_run(tmp_path / "output" / "batch" / "001", raw_abs=ELSEWHERE + r"\output\batch\001\extras\media\raw.mp4")
    pr = user_project(tmp_path / "p.prproj", ELSEWHERE + r"\output\batch\001\extras\media\raw.mp4")
    assert K.find_run(pr, [tmp_path / "output"], repo=tmp_path)[0].run_dir == run
    index, notes = K.run_index([tmp_path / "output", tmp_path / "missing"])
    assert index == [(run, None)] and notes == []                          # a missing root is skipped quietly


def test_nothing_found_or_a_bad_project_is_said_in_plain_words(tmp_path):
    pr = user_project(tmp_path / "p.prproj", r"C:\nowhere-lc\raw.mp4")
    s = K.search_run(pr, [tmp_path], repo=tmp_path)
    assert s.candidates == [] and s.problems[0] == K.NO_RUN
    assert "pick it here" in K.NO_RUN and "can't find the run" in K.NO_RUN
    bad = tmp_path / "bad.prproj"
    bad.write_bytes(b"not a premiere project at all")
    s = K.search_run(bad, [tmp_path], repo=tmp_path)
    assert s.candidates == [] and any("I can't read bad.prproj" in p for p in s.problems)
    s = K.search_run(tmp_path / "gone.prproj", [tmp_path], repo=tmp_path)
    assert any("gone.prproj: not there" in p for p in s.problems)
    pr3 = user_project(tmp_path / "q.prproj", ELSEWHERE + r"\output\video99\extras\media\raw.mp4")
    s = K.search_run(pr3, [tmp_path], repo=tmp_path)
    assert any("not on this computer" in n for n in s.notes)               # the media point to the other computer


def test_default_roots():
    roots = K.default_roots(shared=r"D:\Shared\Projects", repo=REPO)
    assert roots[0] == REPO / "output" and Path(r"D:\Shared\Projects") in roots and Path(r"D:\Shared") in roots
    assert len({str(r).lower() for r in roots}) == len(roots)
    assert Path("D:\\") not in K.default_roots(shared=r"D:\Projects", repo=REPO)   # never a whole drive
    laptop = K.default_roots(shared=r"\\192.168.101.11\Shared\Projects", repo=REPO)
    assert Path(r"\\192.168.101.11\Shared") in laptop


# ---------------------------------------------------------------------------------------------------------------------
# a dropped folder, the watcher
# ---------------------------------------------------------------------------------------------------------------------

def test_a_dropped_project_folder_gives_its_newest_finished_edit_and_its_fix(tmp_path):
    proj = tmp_path / "2026-10-18_x"
    fake_run(proj / "edit")
    newest = fake_run(proj / "edit-2")
    fake_run(proj / "edit-3", finished=False)
    (proj / "project.json").write_text("{}", encoding="utf-8")
    assert K.run_of_folder(proj) == newest
    assert K.run_of_folder(newest) == newest                               # a run folder: itself
    assert K.run_of_folder(tmp_path / "empty") is None
    assert K.prproj_of_folder(proj) is None
    old = user_project(proj / "edit" / "video_fixed.prproj", "x")
    os.utime(old, (time.time() - 100, time.time() - 100))
    new = user_project(proj / "fixed.prproj", "x")
    user_project(proj / "draft.prproj", "x")
    assert K.prproj_of_folder(proj) == new                                 # the newest fixed one
    one = tmp_path / "finished" / "video5"
    pr = user_project(one / "video5_fixed.prproj", str(newest / "extras" / "media" / "raw.mp4"))
    assert K.prproj_of_folder(one) == pr
    assert K.run_of_folder(one, [tmp_path]) == newest                      # a folder with one .prproj: its run


def test_a_new_fixed_prproj_in_a_project_is_offered_once(tmp_path):
    root = tmp_path / "Projects"
    old = time.time() - 120
    a = user_project(root / "p1" / "fixed.prproj", "x")
    b = user_project(root / "p2" / "edit" / "video_fixed.prproj", "x")
    c = user_project(root / "p3" / "edit-2" / "my fixed2.prproj", "x")
    user_project(root / "p4" / "draft.prproj", "x")                        # not a fix
    user_project(root / "p5" / "deep" / "fixed.prproj", "x")               # not where a fix is saved
    for f, t in ((a, old), (b, old + 10), (c, old + 20)):
        os.utime(f, (t, t))
    fresh = user_project(root / "p6" / "fixed.prproj", "x")                # still being saved: next time
    state = tmp_path / "state" / "learn_seen.json"
    got = K.new_fixed([root, tmp_path / "missing"], state)
    assert got == [(root / "p3", c), (root / "p2", b), (root / "p1", a)]   # newest first
    assert K.mark_seen(state, a) == ""
    assert (root / "p1", a) not in K.new_fixed([root], state)
    a.write_bytes(a.read_bytes() + b"\0")                                  # saved again: offered again
    os.utime(a, (old + 30, old + 30))
    assert K.new_fixed([root], state)[0] == (root / "p1", a)
    hub = tmp_path / "hub"
    assert H.queue_add(hub, H.QueueItem("video13", prproj=str(b), computer="PC")) == ""
    assert (root / "p2", b) not in K.new_fixed([root], state, queue_folder=hub)   # queued already
    assert (root / "p6", fresh) in K.new_fixed([root], state, settle_s=0)
    assert K.mark_seen(state, tmp_path / "gone.prproj").startswith("gone.prproj: ")   # said, not raised


# ---------------------------------------------------------------------------------------------------------------------
# the form, the copies, the queue
# ---------------------------------------------------------------------------------------------------------------------

def test_dropping_only_the_fixed_prproj_fills_the_whole_form(tmp_path):
    repo = tmp_path / "repo"
    (repo / "finished" / "video12").mkdir(parents=True)
    run = fake_run(tmp_path / "Shared" / "2026-10-18_x" / "edit")
    (run.parent / "project.json").write_text("{}", encoding="utf-8")
    pr = user_project(run.parent / "fixed.prproj", str(run / "extras" / "media" / "raw.mp4"))
    f = K.new_form([pr], [tmp_path / "Shared"], repo=repo, check=True)
    assert f.prproj == pr and f.run == run and f.project_folder == run.parent
    assert f.name == "video13" and f.name_auto
    assert f.plan.project_to == repo / "finished" / "video13" / "video13_fixed.prproj"
    assert f.plan.run_to == repo / "output" / "laptop_video13"
    assert f.card is not None and f.card.ok and f.ready and f.problems == []
    g = K.new_form([run.parent], [tmp_path / "Shared"], repo=repo, check=False)   # the project folder dropped
    assert (g.prproj, g.run, g.project_folder) == (pr, run, run.parent) and g.card is None and g.ready


def test_every_drop_makes_a_new_form_and_says_what_is_missing(tmp_path):
    repo = tmp_path / "repo"
    run = fake_run(tmp_path / "out" / "r1")
    pr = user_project(tmp_path / "a" / "video50_fixed.prproj", str(run / "extras" / "media" / "raw.mp4"))
    first = K.new_form([pr], [tmp_path / "out"], repo=repo, check=False)
    assert first.ready and first.name == "video50"
    lost = user_project(tmp_path / "b" / "fixed.prproj", r"C:\nowhere-lc\raw.mp4")
    second = K.new_form([lost], [tmp_path / "out"], repo=repo, check=False)
    assert second.run is None and second.plan is None and not second.ready      # nothing of the first one sticks
    assert K.NO_RUN in second.problems
    nothing = K.new_form([tmp_path / "out" / "r1" / "1_edit.xml"], repo=repo)
    assert nothing.problems[0] == K.NO_PRPROJ and not nothing.ready
    both = K.new_form([lost, run], repo=repo, check=False)                 # the run dropped with it: taken as it is
    assert both.run == run and both.run_how == "you dropped it"


def test_a_picked_run_and_a_typed_name_rebuild_the_form(tmp_path):
    repo = tmp_path / "repo"
    run = fake_run(tmp_path / "out" / "r1")
    lost = user_project(tmp_path / "b" / "fixed.prproj", ELSEWHERE + r"\output\zz\extras\media\raw.mp4")
    f = K.new_form([lost], [tmp_path / "nowhere"], repo=repo, check=False)
    assert not f.ready and K.NO_RUN in f.problems
    g = K.rebuild(f, run=run, check=True, repo=repo)
    assert g.run == run and g.run_how == "you picked it" and g.ready and g.card.ok
    h = K.rebuild(g, name="chocolate", check=False, repo=repo)
    assert h.name == "chocolate" and not h.name_auto and h.plan.run_to == repo / "output" / "laptop_chocolate"
    assert K.rebuild(h, check=False, repo=repo).name == "chocolate"      # a typed name stays


def test_learning_copies_to_the_learn_place_and_queues_the_project(tmp_path):
    repo = tmp_path / "repo"
    run = fake_run(tmp_path / "Shared" / "2026-10-18_x" / "edit")
    pr = user_project(tmp_path / "Shared" / "2026-10-18_x" / "fixed.prproj", str(run / "extras" / "media" / "raw.mp4"))
    pl = K.plan(pr, run, "video13", root=repo)
    hub = tmp_path / "Shared" / "Projects"
    seen = []
    assert K.copy_and_queue(pl, lambda a, b: seen.append((a, b)), hub=hub,
                            item=K.queue_item(pl, computer="laptop")) == ""
    assert pl.project_to.read_bytes() == pr.read_bytes() and (pl.run_to / "1_edit.xml").is_file()
    assert os.path.samefile(pl.run_to / "extras" / "media" / "raw.mp4", run / "extras" / "media" / "raw.mp4")  # linked
    assert seen[-1][0] == seen[-1][1]
    it = H.queue_items(hub)[0]
    assert (it.name, it.prproj, it.run, it.computer) == ("video13", str(pr), str(run), "laptop")
    assert (it.raw, it.competitor) == ("Nice Commercial [abc].mp4", "The Short [xyz].mp4")
    assert it.copies == r"finished\video13\video13_fixed.prproj, output\laptop_video13" and it.date
    line = [ln for ln in (hub / H.QUEUE_FILE).read_text(encoding="utf-8").splitlines() if ln.startswith("- [ ]")][0]
    assert "prproj: ..\\2026-10-18_x\\fixed.prproj" in line and "run: ..\\2026-10-18_x\\edit" in line
    # the night-9 way still works: no hub, nothing queued; the same place: nothing copied
    pl2 = K.plan(pl.project_to, pl.run_to, "video13", root=repo)
    assert K.copy(pl2) == "" and len(H.queue_items(hub)) == 1


def test_a_copy_that_cannot_be_made_is_said_in_plain_words(tmp_path):
    pr = user_project(tmp_path / "fixed.prproj", "x")
    pl = K.plan(pr, tmp_path / "gone_run", "video13", root=tmp_path / "repo")
    msg = K.copy_and_queue(pl, hub=tmp_path / "hub")
    assert msg and "Traceback" not in msg
    assert H.queue_items(tmp_path / "hub") == []                           # nothing queued


# ---------------------------------------------------------------------------------------------------------------------
# the check card
# ---------------------------------------------------------------------------------------------------------------------

def _clip(t0, t1, r0, r1=None, dx=None, scale=None):
    return SimpleNamespace(t0=t0, t1=t1, r0=r0, r1=r0 + (t1 - t0) if r1 is None else r1, dx=dx, scale=scale)


def test_cuts_moved_added_removed_trimmed_and_the_ending():
    tool = [_clip(0, 3, 10), _clip(3, 5, 20), _clip(5, 8, 30), _clip(8, 9, 40), _clip(9, 12, 50), _clip(12, 14, 60)]
    user = [_clip(0, 3, 10),                     # the same
            _clip(3, 5.2, 20),                   # its end 0.2 s later: a trim at the cut (by hand)
            _clip(5.2, 8.2, 30),                 # (the tool's 40-41 s left out)
            _clip(8.2, 9.2, 50), _clip(9.2, 10.7, 51.5),   # a cut of yours inside the take: added
            _clip(10.7, 13.7, 61)]               # comes in 1 s later: moved; ends 2 s later: the ending
    cc = C.cut_changes(tool, user)
    assert (cc["same"], cc["trimmed"], cc["moved"], cc["removed"], cc["added"]) == (2, 1, 1, 0, 1)
    assert (cc["clips_removed"], cc["removed_s"], cc["clips_added"]) == (1, 1.0, 0)
    assert cc["end"] == 2.0 and cc["start"] == 0.0
    assert C.cut_line(cc, "picture") == "1 moved, 1 added, 0 removed; 1 clip of the tool's left out (1.0 s of the RAW)"
    joined = C.cut_changes([_clip(0, 3, 10), _clip(3, 5, 13.1)], [_clip(0, 5, 10, 15.1)])   # you play on: removed
    assert (joined["removed"], joined["added"], joined["moved"]) == (1, 0, 0)
    late = C.cut_changes([_clip(0, 3, 10)], [_clip(0, 3, 10.8)])          # the edit's start counts as a cut point
    assert late["moved"] == 1


def test_position_moved_or_zoomed():
    tool = [_clip(0, 2, 10, dx=0.0, scale=100.0), _clip(2, 4, 20, dx=0.0, scale=100.0),
            _clip(4, 6, 30, dx=50.0, scale=100.0), _clip(6, 8, 40, dx=0.0, scale=100.0),
            _clip(8, 10, 50, dx=None, scale=None)]
    user = [_clip(0, 2, 10, dx=120.0, scale=100.0), _clip(2, 4, 20, dx=0.0, scale=106.0),
            _clip(4, 6, 30, dx=60.0, scale=101.0), _clip(6, 8, 40, dx=0.0, scale=100.0),
            _clip(8, 10, 50, dx=0.0, scale=100.0)]
    pc = C.position_changes(tool, user)
    assert (pc["clips"], pc["changed"], pc["moved"], pc["zoomed"], pc["unknown"]) == (4, 2, 1, 1, 1)
    assert C.position_line(pc) == ("2 of 4 clips moved or zoomed (1 moved sideways, 1 zoomed); 1 clip not compared "
                                   "(keyframed, or without a known position)")


def test_captions_words_grouping_timing_spelling_and_the_ending():
    m = [_clip(0, 10, 100)]
    tool = [(0.0, 1.0, "hello there"), (1.0, 2.0, "general kenobi"), (2.0, 3.0, "you are"), (3.0, 4.0, "a bold one"),
            (4.0, 5.0, "*...*"), (5.0, 6.0, "back away"), (6.0, 7.0, "I will deal with"),
            (7.0, 8.0, "this jedi slime myself")]
    user = [(0.0, 1.0, "Hello there"),             # spelling
            (1.0, 2.3, "general kenobi"),          # timing: its end 0.3 s later
            (2.3, 4.0, "you are a bold one"),      # grouping: two joined
            (4.0, 5.0, "*claps hands*"),           # words: the placeholder written
            (5.0, 6.0, "back off"),                # words
            (6.0, 7.0, "I will deal with"),        # the same
            (7.0, 8.6, "this Jedi slime myself")]  # spelling; its end is the ending (by hand)
    cc = C.caption_changes(tool, user, m, m, tool_end=8.0)
    assert {k: cc[k] for k in ("captions", "changed", "words", "grouping", "timing", "spelling", "removed")} == \
        {"captions": 7, "changed": 6, "words": 2, "grouping": 1, "timing": 1, "spelling": 2, "removed": 0}
    assert C.caption_line(cc, "the caption track C1") == ("6 of 7 changed (2 words, 1 grouping, 1 timing, 2 spellings);"
                                                          " look: the caption track C1")
    gone = C.caption_changes(tool, user[:6], m, m, tool_end=8.0)
    assert gone["removed"] == 1                                            # the tool's last caption deleted


def test_a_cut_moved_earlier_does_not_make_every_later_caption_moved():
    tool_map = [_clip(0, 5, 100), _clip(5, 10, 200)]
    user_map = [_clip(0, 4.5, 100), _clip(4.5, 9.5, 200)]                 # 0.5 s cut out of the first clip
    tool = [(1.0, 2.0, "early words"), (6.0, 7.0, "later words"), (8.0, 9.0, "more later words")]
    user = [(1.0, 2.0, "early words"), (5.5, 6.5, "later words"), (7.5, 8.9, "more later words")]
    cc = C.caption_changes(tool, user, tool_map, user_map, tool_end=10.0)
    assert cc["timing"] == 0 and cc["changed"] == 0
    moved = [(1.0, 2.0, "early words"), (5.8, 6.5, "later words"), (7.5, 8.9, "more later words")]
    assert C.caption_changes(tool, moved, tool_map, user_map, tool_end=10.0)["timing"] == 1


def test_a_repeated_chorus_pairs_with_the_chorus_at_the_same_moment():
    a = "chocolate is scrunchous when it crunches"
    tool = [(0.0, 5.0, "chocolate is scrumptious scrunchous when it crunches"), (20.0, 25.0, "chocolate is scrunchous "
                                                                                       "when when it crunches"),
            (40.0, 45.0, a)]
    user = [(0.0, 5.0, a), (20.0, 25.0, a), (40.0, 45.0, a)]
    m = [_clip(0, 50, 0)]
    cc = C.caption_changes(tool, user, m, m, tool_end=50.0)
    assert (cc["words"], cc["removed"], cc["changed"]) == (2, 0, 2)       # the first two corrected, the third the same
    tw, ts = C._words(tool)
    uw, us = C._words(user)
    u2t = C.align_words(tw, C._word_times(tool, ts, m), uw, C._word_times(user, us, m))
    assert u2t[us[2][0]:us[2][1]] == list(range(ts[2][0], ts[2][1]))       # the third chorus: the tool's third


def test_the_check_card_of_a_fake_fix(tmp_path):
    run = fake_run(tmp_path / "Shared" / "2026-10-18_x" / "edit", gpu="NVIDIA GeForce RTX 4060 Laptop GPU",
                   clips=((0.0, 5.0, 1.0), (5.0, 9.0, 8.0), (9.0, 12.0, 20.0)),
                   captions=((0.0, 2.0, "chocolate is"), (2.0, 4.0, "so good"), (5.0, 7.0, "that's why"),
                             (9.0, 11.0, "I love it")))
    pr = user_project(tmp_path / "Desktop" / "chocolate.prproj", str(run / "extras" / "media" / "raw.mp4"),
                      clips=((0.0, 5.0, 1.0), (5.0, 8.0, 9.0), (8.0, 11.5, 20.0)),
                      captions=((0.0, 2.0, "Chocolate is"), (2.0, 4.0, "so good"), (5.0, 7.0, "that's why"),
                                (8.0, 11.5, "I love Nestle")),
                      extra_media=[str(run / "extras" / "media" / "frame.png")])
    card = C.check(pr, run)
    assert card.ok, card.problems
    assert card.raw.startswith("Nice Commercial [abc].mp4 — 640x480, 30 fps, 30.7 s")
    assert card.competitor.startswith("The Short [xyz].mp4 — 1080x1920, 30 fps, 20.0 s")
    assert "on the laptop (NVIDIA GeForce RTX 4060 Laptop GPU)" in card.tool_version and "Fast" in card.tool_version
    assert "its own checks: PASS" in card.tool_version and "made 18 Oct 01:36" in card.tool_version
    assert "tool 0.1.0, code 01234567 -- other code than this computer's tool now" in card.tool_version
    assert "chocolate.prproj" in card.fixed_version and "sequence 'Recreated Edit (Premiere)'" in card.fixed_version
    assert "3 clips of the RAW, 4 captions" in card.fixed_version
    assert card.captions == "2 of 4 changed (1 word, 1 spelling); look: the caption track C1"
    assert card.cuts == "1 moved, 0 added, 0 removed"                      # 8.0 -> 9.0 s: a cut moved 1 s
    assert card.position.startswith("not compared (3 clips")               # no Motion in the fakes
    assert any("frame.png" in i for i in card.ignored) and any("all audio" in i for i in card.ignored)
    assert card.details["raw_name"] == "Nice Commercial [abc].mp4" and card.seconds < 30
    assert all(line for line in card.lines())


def test_the_check_never_raises(tmp_path):
    card = C.check(tmp_path / "gone.prproj", tmp_path / "gone_run")
    assert card.problems and "not a run folder" in card.problems[0]
    run = fake_run(tmp_path / "run", finished=False)
    bad = tmp_path / "bad.prproj"
    bad.write_bytes(b"\x1f\x8bnot really gzip")
    card = C.check(bad, run)
    assert any("has not finished" in p for p in card.problems) and any("I can't read bad.prproj" in p for p in card.problems)
    other = user_project(tmp_path / "other.prproj", r"C:\x\other.mp4", raw={"w": 1280, "h": 720, "dur": 99.0})
    card = C.check(other, fake_run(tmp_path / "run2"))
    assert any("does not play this run's RAW" in p for p in card.problems)


def test_the_tools_code_fingerprint_is_the_pipelines():
    from match_cuts.pipeline import code_hash
    assert C.tool_code_hash() == code_hash()


# ---------------------------------------------------------------------------------------------------------------------
# the three real fixes (read only; skipped when they are not on this computer)
# ---------------------------------------------------------------------------------------------------------------------

REAL = {
    "video11": (REPO / "finished" / "video11" / "video11_fixed.prproj", REPO / "output" / "laptop_video11"),
    "video12": (REPO / "finished" / "video12" / "video12_fixed.prproj", REPO / "output" / "laptop_video12"),
    "chocolate": (Path(r"C:\Users\gvida\Desktop\chocolate.prproj"),
                  Path(r"D:\Shared\2026-10-18_where-the-nestle-crunch-meme-originated\edit")),
}


@pytest.mark.parametrize("name", sorted(REAL))
def test_find_run_finds_the_real_fixes_runs(name):
    prproj, run = REAL[name]
    if not prproj.is_file() or not run.is_dir():
        pytest.skip(f"{name}'s files are not on this computer")
    t0 = time.time()
    s = K.search_run(prproj, K.default_roots(shared=r"D:\Shared\Projects"))
    print(f"{name}: {time.time() - t0:.2f} s, {[str(c.run_dir) for c in s.candidates]}")
    assert s.candidates and s.candidates[0].run_dir == run, (s.problems, s.rejected)
    assert s.candidates[0].finished and (s.candidates[0].played or 0) > 0.8


@pytest.mark.parametrize("name", sorted(REAL))
def test_the_check_card_of_the_real_fixes(name):
    prproj, run = REAL[name]
    if not prproj.is_file() or not run.is_dir():
        pytest.skip(f"{name}'s files are not on this computer")
    card = C.check(prproj, run)
    print(f"{name}: {card.seconds} s\n  " + "\n  ".join(card.lines()))
    assert card.ok, card.problems
    assert card.raw and card.competitor and card.tool_version and card.fixed_version
    assert " of " in card.captions and " moved, " in card.cuts and card.position
    assert "made " in card.tool_version and ("the PC" in card.tool_version or "the laptop" in card.tool_version)
