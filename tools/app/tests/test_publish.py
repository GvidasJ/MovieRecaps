"""Publishing finished projects to the shared folder (mr_app.core.publish): only the finished parts, links on the same
volume and checked copies on another (the volume check monkeypatched), the XMLs' media paths pointing into the
published copy (and nothing else in them changed), project.json last and merged, the titles, the queue for a shared
folder that is away, never hanging on it. Run: python -m pytest tools/app/tests/test_publish.py"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import locks as L      # noqa: E402
from mr_app.core import projects as P   # noqa: E402
from mr_app.core import publish as PB   # noqa: E402

HEADLINE = "When she finally\ngot the part \U0001F480\U0001F62D"
OUTSIDE = "file://localhost/D%3A/Music/song%20one.wav"


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(tmp_path / "settings.json"))


def xml_bytes(media: Path) -> bytes:
    """An edit XML like the tool's: the four media in extras\\media, one file elsewhere, other text around them."""
    u = {n: PB.file_url(str(media / n)) for n in ("raw.mp4", "competitor_ref.mp4", "frame.png", "watermark.png")}
    return ('<?xml version="1.0" encoding="UTF-8"?>\r\n<!DOCTYPE xmeml>\r\n<xmeml version="5"><sequence>'
            '<name>x &amp; y \u00e4 \U0001F600</name>\n'
            f'<file id="file-raw"><name>raw.mp4</name><pathurl>{u["raw.mp4"]}</pathurl></file>\n'
            f'<file id="file-competitor"><pathurl>{u["competitor_ref.mp4"]}</pathurl></file>\n'
            f'<clipitem><file id="file-raw"/></clipitem><file id="file-frame"><pathurl>{u["frame.png"]}</pathurl>'
            f'</file>\n<file id="file-watermark"><pathurl>{u["watermark.png"]}</pathurl></file>\n'
            f'<file id="music"><pathurl>{OUTSIDE}</pathurl></file>\n'
            '<comment>Gr\u00f6\u00dfe "quoted" \'single\'</comment>\n</sequence></xmeml>\n').encode("utf-8")


def make_edit(folder: Path, name: str, raw: Path, comp: Path, finished: bool = True) -> Path:
    e = folder / name
    media = e / "extras" / "media"
    media.mkdir(parents=True)
    os.link(raw, media / "raw.mp4")                         # the tool links on the same drive
    os.link(comp, media / "competitor_ref.mp4")
    (media / "frame.png").write_bytes(b"F" * 5000)
    (media / "watermark.png").write_bytes(b"W" * 3000)
    (media / ".conform.json").write_text("{}", encoding="utf-8")
    (e / "extras" / "debug").mkdir()
    (e / "extras" / "debug" / "layout.png").write_bytes(b"L" * 2000)
    (e / "extras" / "compare.mp4").write_bytes(os.urandom(50_000))
    x = xml_bytes(media)
    (e / "1_edit.xml").write_bytes(x)
    (e / "extras" / "edit_60fps.xml").write_bytes(x)
    (e / f"{folder.name}_{name}.xml").write_bytes(x)       # the copy "Open in Premiere" makes
    (e / "2_captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    if finished:
        (e / "extras" / "report.md").write_text("# report\n", encoding="utf-8")
    return e


def make_project(root: Path, edits: tuple[str, ...] = ("edit",), finished: bool = True,
                 upload_title: str | None = None) -> Path:
    p = P.create(root, competitor_url="https://www.youtube.com/shorts/Gym6CmGTOOE",
                 raw_url="https://www.youtube.com/watch?v=0jeDip0_0j0", name="the part is hers")
    f = p.folder
    comp = f / "the part is hers [Gym6CmGTOOE].mp4"
    comp.write_bytes(os.urandom(300_000))
    raw = f / "Dakota Johnson [0jeDip0_0j0].mp4"
    raw.write_bytes(os.urandom(700_000))
    (f / "template.png").write_bytes(b"PNG" * 1000)
    (f / "logs").mkdir()
    (f / "logs" / "download_raw.log").write_text("downloaded\n", encoding="utf-8")
    (f / ".edit.app.stdout").write_text("console", encoding="utf-8")   # working files: never published
    for e in edits:
        make_edit(f, e, raw, comp, finished)
    fields = dict(competitor={"file": comp.name}, raw={"file": raw.name}, channel="Flick707", title=HEADLINE)
    if upload_title is not None:
        fields["upload_title"] = upload_title
    p.update(**fields)
    for s in ("download", "convert", "template"):
        p.set_step(s, "done")
    p.set_step("edit", "done", result="PASS", run_dir=str(f / edits[-1]))
    PB.write_title_txt(f, p.reload())
    return f


def tree(folder: Path) -> set[str]:
    return {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()}


@pytest.fixture
def setup(tmp_path):
    local = tmp_path / "input" / "downloads"
    shared = tmp_path / "Shared" / "Projects"
    shared.parent.mkdir(parents=True)                           # D:\Shared is there, Projects not yet
    return make_project(local), local, shared


# ---------------------------------------------------------------------------------------------------------------------
# what goes, and how
# ---------------------------------------------------------------------------------------------------------------------

def test_only_the_finished_parts_go_linked_on_the_same_volume(setup):
    src, local, shared = setup
    res = PB.publish(src, shared)
    dest = shared / src.name
    assert res.dest == dest and res.edits == ["edit"]
    want = {x for x in tree(src) if x.startswith("edit") or x.startswith("logs")} | {"template.png", "title.txt",
                                                                                     "project.json"}
    assert tree(dest) == want                                   # no originals, no .app.stdout, no lock left
    assert not any(n.endswith(".mp4") for n in os.listdir(dest))
    for rel in want - {"project.json", "title.txt"}:
        a, b = src / rel, dest / rel
        if rel.endswith(".xml"):
            assert not os.path.samefile(a, b)                   # written anew, never a link
        else:
            assert os.path.samefile(a, b), rel                  # a hard link: same volume
    assert res.rewritten == 4 and res.linked == len(want) - 5 and res.copied == 0 and res.bytes == 0
    assert res.files == len(want)
    assert res.summary().startswith(f"published to {dest} (edit): {len(want)} files -- ")


def test_another_volume_gets_checked_copies_with_their_times(setup, monkeypatch):
    src, local, shared = setup
    monkeypatch.setattr(PB, "same_volume", lambda a, b: False)
    monkeypatch.setattr(PB, "CHUNK", 64 * 1024)
    seen = []
    res = PB.publish(src, shared, progress=lambda f, t: seen.append((f, t)))
    dest = shared / src.name
    copied = [r for r in tree(dest) if not r.endswith(".xml") and r not in ("project.json", "title.txt")]
    assert res.copied == len(copied) and res.linked == 0
    assert res.bytes == sum((src / r).stat().st_size for r in copied)
    for r in copied:
        a, b = (src / r).stat(), (dest / r).stat()
        assert (dest / r).read_bytes() == (src / r).read_bytes() and b.st_mtime_ns == a.st_mtime_ns
        assert not os.path.samefile(src / r, dest / r)
    assert not [p for p in dest.rglob("*.part")]
    assert seen[-1][0] == 1.0 and seen[-1][1] == res.summary()
    fr = [f for f, _ in seen]
    assert fr == sorted(fr) and any("publishing" in t and "%" in t for _, t in seen)


def test_publishing_again_is_cheap(setup, monkeypatch):
    src, local, shared = setup
    monkeypatch.setattr(PB, "same_volume", lambda a, b: False)
    first = PB.publish(src, shared)
    again = PB.publish(src, shared)
    assert again.copied == 0 and again.linked == 0 and again.rewritten == 0 and again.bytes == 0
    assert again.skipped == first.files - 1 == again.files - 1  # all there (project.json is always written)


def test_the_xml_media_paths_point_into_the_published_copy_and_nothing_else_changes(setup):
    src, local, shared = setup
    res = PB.publish(src, shared)
    dest = shared / src.name
    for rel in ("edit/1_edit.xml", "edit/extras/edit_60fps.xml", f"edit/{src.name}_edit.xml"):
        old, new = (src / rel).read_bytes(), (dest / rel).read_bytes()
        expect = old
        for n in ("raw.mp4", "competitor_ref.mp4", "frame.png", "watermark.png"):
            a = PB.file_url(str(src / "edit" / "extras" / "media" / n)).encode()
            b = PB.file_url(str(dest / "edit" / "extras" / "media" / n)).encode()
            assert a in old and b not in old
            expect = expect.replace(a, b)
        assert new == expect                                    # only the four pathurls changed, byte for byte
        assert OUTSIDE.encode() in new
        assert (dest / rel).stat().st_mtime_ns == (src / rel).stat().st_mtime_ns
    assert old == xml_bytes(src / "edit" / "extras" / "media")  # the local edit is untouched
    assert any("left as it is" in n and "song one.wav" in n for n in res.notes)


def test_a_network_destination_gives_the_servers_url():
    src_edit = Path(r"C:\Users\gvida\Desktop\bh\MovieRecaps\input\downloads\2026-10-18_x y\edit")
    dest_edit = Path(r"\\192.168.101.11\Shared\Projects\2026-10-18_x y\edit")
    text = f"<a><pathurl>{PB.file_url(str(src_edit / 'extras' / 'media' / 'raw.mp4'))}</pathurl></a>"
    new, n, outside = PB.rewrite_xml(text, src_edit, dest_edit)
    assert n == 1 and not outside
    assert new == "<a><pathurl>file://192.168.101.11/Shared/Projects/2026-10-18_x%20y/edit/extras/media/raw.mp4"\
                  "</pathurl></a>"
    assert PB.url_to_path("file://192.168.101.11/Shared/Projects/2026-10-18_x%20y/edit/extras/media/raw.mp4") == \
        r"\\192.168.101.11\Shared\Projects\2026-10-18_x y\edit\extras\media\raw.mp4"
    assert PB.url_to_path("file://localhost/C%3A/a%20b/c.mp4") == r"C:\a b\c.mp4"
    assert PB.url_to_path("file:///C:/a/c.mp4") == r"C:\a\c.mp4"
    assert PB.url_to_path("https://example.com/x") is None


def test_the_url_function_is_match_cuts_own():
    from match_cuts.export_xml_edl import _file_url
    for p in (r"D:\Shared\Projects\a&b #1\edit\extras\media\raw.mp4", r"\\192.168.101.11\Shared\x y\r.mp4",
              r"C:\Users\gvida\\u00fc \U0001F600.mp4"):
        assert PB.file_url(p) == _file_url(p)


def test_a_project_moved_since_its_run_is_published_with_the_right_paths(setup):
    src, local, shared = setup
    e = src / "edit"
    old_media = Path(r"C:\Users\someone\old place") / src.name / "edit" / "extras" / "media"
    for x in (e / "1_edit.xml", e / "extras" / "edit_60fps.xml"):
        x.write_bytes(xml_bytes(old_media))                    # the run was made in another folder
    PB.publish(src, shared)
    got = (shared / src.name / "edit" / "1_edit.xml").read_text(encoding="utf-8")
    assert PB.file_url(str(shared / src.name / "edit" / "extras" / "media" / "raw.mp4")) in got
    assert "old%20place" not in got


def test_relink_points_an_edit_at_its_media_as_this_computer_sees_it(setup):
    src, local, shared = setup
    e = src / "edit"
    theirs = Path(r"\\192.168.101.11\Shared\Projects") / src.name / "edit" / "extras" / "media"
    (e / "1_edit.xml").write_bytes(xml_bytes(theirs))          # published from the other computer
    t0 = (e / "1_edit.xml").stat().st_mtime_ns
    assert PB.relink_edit(e) == 4
    assert (e / "1_edit.xml").read_bytes() == xml_bytes(e / "extras" / "media")
    assert (e / "1_edit.xml").stat().st_mtime_ns == t0       # its time kept
    assert PB.relink_edit(e) == 0                              # right already


# ---------------------------------------------------------------------------------------------------------------------
# project.json: last, merged, both copies know
# ---------------------------------------------------------------------------------------------------------------------

def test_project_json_is_written_last(setup, monkeypatch):
    src, local, shared = setup
    dest = shared / src.name
    real = P.write_json

    def check(path, data, *a, **k):
        if Path(path).parent == dest and Path(path).name == P.FILE:
            assert {"edit/1_edit.xml", "edit/extras/report.md", "template.png", "logs/download_raw.log",
                    "title.txt"} <= {x.replace("\\", "/") for x in tree(dest)}
        return real(path, data, *a, **k)
    monkeypatch.setattr(P, "write_json", check)
    PB.publish(src, shared)
    assert (dest / P.FILE).is_file()


def test_a_publish_that_breaks_half_way_leaves_no_project_json(setup, monkeypatch):
    src, local, shared = setup
    monkeypatch.setattr(PB, "same_volume", lambda a, b: False)
    calls = []
    real = PB._copy

    def flaky(s, d, on_bytes, cancel):
        calls.append(1)
        if len(calls) == 3:
            raise PermissionError(13, "Access is denied", str(d))
        return real(s, d, on_bytes, cancel)
    monkeypatch.setattr(PB, "_copy", flaky)
    with pytest.raises(PB.PublishError) as e:
        PB.publish(src, shared)
    assert "may not write to the shared folder" in str(e.value)
    dest = shared / src.name
    assert dest.is_dir() and not (dest / P.FILE).exists() and not (dest / L.LOCK_FILE).exists()
    monkeypatch.setattr(PB, "_copy", real)
    res = PB.publish(src, shared)                              # again: it goes on where it stopped
    assert res.skipped >= 2 and (dest / P.FILE).is_file()


def test_both_copies_record_where_it_was_published(setup):
    src, local, shared = setup
    PB.publish(src, shared)
    dest = shared / src.name
    for d in (P.load(src).data, P.load(dest).data):
        pub = d["published"]
        assert pub["to"] == str(dest) and pub["computer"] in ("pc", "laptop") and pub["at"]
    d = P.load(dest).data
    assert d["steps"]["edit"]["run_dir"] == str(dest / "edit")  # the published copy's own edit folder
    assert P.load(src).data["steps"]["edit"]["run_dir"] == str(src / "edit")


def test_the_shared_board_state_is_never_lost(setup):
    src, local, shared = setup
    PB.publish(src, shared)
    dest = shared / src.name
    time.sleep(1.1)
    other = P.load(dest)                                        # the other computer moves the card, pins it ...
    other.update(board={"column": "rendered", "moved_at": P.now(), "dates": {"rendered": P.now()},
                        "pinned": True, "not_same": ["abc"]}, rendered_on="pc")
    time.sleep(1.1)
    make_edit(src, "edit-2", src / "Dakota Johnson [0jeDip0_0j0].mp4", src / "the part is hers [Gym6CmGTOOE].mp4")
    P.load(src).set_step("edit", "done", result="OK with a note", run_dir=str(src / "edit-2"))
    P.load(src).update(board={"dates": {"link": "2026-10-18T01:00:00+03:00"}, "not_same": ["xyz"]})
    res = PB.publish(src, shared)
    assert res.edits == ["edit", "edit-2"] and (dest / "edit-2" / "1_edit.xml").is_file()
    d = P.load(dest).data
    b = d["board"]
    assert b["column"] == "rendered" and b["pinned"] is True and "rendered" in b["dates"]
    assert b["dates"]["link"] == "2026-10-18T01:00:00+03:00" and b["not_same"] == ["xyz", "abc"]
    assert d["rendered_on"] == "pc"                             # a field only the shared copy has
    assert d["steps"]["edit"]["result"] == "OK with a note"     # the newer step
    assert d["steps"]["edit"]["run_dir"] == str(dest / "edit-2")


def test_merge_rules():
    local = {"created": "2026-10-18T02:00:00+03:00", "updated": "2026-10-18T05:00:00+03:00", "channel": "Lou",
             "steps": {"edit": {"state": "done", "finished": "2026-10-18T05:00:00+03:00"},
                       "convert": {"state": "done", "finished": "2026-10-18T02:10:00+03:00"}},
             "board": {"pinned": True, "dates": {"link": "a"}}}
    shared = {"created": "2026-10-18T02:00:00+03:00", "updated": "2026-10-18T06:00:00+03:00", "channel": "Mike",
              "steps": {"convert": {"state": "done", "finished": "2026-10-18T03:00:00+03:00", "again": True},
                        "upload": {"x": 1}}, "board": {"column": "fixed", "dates": {"fixed": "b"}}, "only": 1}
    m = PB.merge_project(local, shared)
    assert m["channel"] == "Mike"                               # the shared copy changed last
    assert m["steps"]["edit"] == local["steps"]["edit"] and m["steps"]["convert"]["again"] is True
    assert m["steps"]["upload"] == {"x": 1} and m["only"] == 1
    assert m["board"] == {"pinned": True, "column": "fixed", "dates": {"link": "a", "fixed": "b"}}
    assert PB.merge_project(local, None) == local and PB.merge_project(local, None) is not local


# ---------------------------------------------------------------------------------------------------------------------
# where it goes
# ---------------------------------------------------------------------------------------------------------------------

def test_another_project_with_the_same_folder_name_is_not_mixed_in(setup):
    src, local, shared = setup
    theirs = shared / src.name
    theirs.mkdir(parents=True)
    P.write_json(theirs / P.FILE, {**P._new_data(src.name, None, None, None), "created": "2020-01-01T00:00:00+02:00"})
    res = PB.publish(src, shared)
    assert res.dest == shared / f"{src.name}-2" and any("another project called" in n for n in res.notes)
    assert P.load(theirs).data["created"] == "2020-01-01T00:00:00+02:00" and not (theirs / "edit").exists()
    again = PB.publish(src, shared)                             # remembered: the same place again
    assert again.dest == shared / f"{src.name}-2" and again.copied == again.linked == 0


def test_a_project_already_in_the_shared_folder_is_not_copied_onto_itself(tmp_path):
    root = tmp_path / "Shared" / "Projects"
    src = make_project(root)
    res = PB.publish(src, root)
    assert res.dest == src and res.files == 1 and "already" in res.notes[0]
    assert P.load(src).data["published"]["to"] == str(src)


def test_nothing_to_publish_without_a_finished_edit(tmp_path):
    src = make_project(tmp_path / "local", finished=False)
    with pytest.raises(PB.NothingToPublish):
        PB.publish(src, tmp_path / "Shared")
    with pytest.raises(PB.NothingToPublish):
        PB.publish_or_queue(src, tmp_path / "Shared")
    assert PB.pending(tmp_path / "local") == []
    with pytest.raises(PB.PublishError):
        PB.publish(tmp_path, tmp_path / "Shared")              # not a project


def test_a_newer_file_in_the_shared_copy_is_kept(setup, monkeypatch):
    src, local, shared = setup
    monkeypatch.setattr(PB, "same_volume", lambda a, b: False)
    PB.publish(src, shared)
    log = shared / src.name / "logs" / "download_raw.log"
    log.write_text("downloaded\nrendered on the PC\n", encoding="utf-8")
    later = time.time() + 60
    os.utime(log, (later, later))
    res = PB.publish(src, shared)
    assert log.read_text(encoding="utf-8").endswith("rendered on the PC\n") and res.kept == 1
    assert any(n.startswith("kept 1 file(s)") and "download_raw.log" in n for n in res.notes)


def test_the_other_computers_lock_on_the_shared_copy_stops_it(setup):
    from mr_app.core import machine as M
    src, local, shared = setup
    dest = shared / src.name
    dest.mkdir(parents=True)
    kind = "laptop" if M.which() == "pc" else "pc"              # the other computer, wherever this runs
    other = L.WorkLock(dest, "render", me={"computer": kind, "host": "Gvidas", "id": "OTHER", "user": "r"},
                       pid=1234).acquire()
    try:
        with pytest.raises(L.LockedError) as e:
            PB.publish(src, shared)
        assert str(e.value).startswith(f"the {M.label(kind)} is working on this project since")
        res, note = PB.publish_or_queue(src, shared, local)
        assert res is None and note.startswith(f"saved on this computer for now -- the {M.label(kind)} is working")
        assert [x["folder"] for x in PB.pending(local)] == [str(src)]
    finally:
        other.release()
    assert [r.dest for r in PB.retry_pending(local, shared)] == [dest]
    assert PB.pending(local) == [] and not (local / PB.PENDING_FILE).exists()


def test_cancel_stops_at_once_and_leaves_no_half_files(setup, monkeypatch):
    src, local, shared = setup
    monkeypatch.setattr(PB, "same_volume", lambda a, b: False)
    monkeypatch.setattr(PB, "CHUNK", 16 * 1024)
    stop = threading.Event()
    with pytest.raises(PB.Cancelled):                           # Stop pressed while the first file is copied
        PB.publish(src, shared, progress=lambda f, t: stop.set() if t.startswith("publishing") else None,
                   cancel=stop)
    dest = shared / src.name
    assert not list(dest.rglob("*.part")) and not (dest / P.FILE).exists() and not (dest / L.LOCK_FILE).exists()


# ---------------------------------------------------------------------------------------------------------------------
# the titles
# ---------------------------------------------------------------------------------------------------------------------

def test_title_txt_is_the_upload_title_when_set_else_the_headline(tmp_path):
    shared = tmp_path / "Shared"
    a = make_project(tmp_path / "a")
    assert (a / "title.txt").read_bytes() == HEADLINE.encode("utf-8")      # as typed, its line break kept
    PB.publish(a, shared)
    assert (shared / a.name / "title.txt").read_bytes() == HEADLINE.encode("utf-8")
    b = make_project(tmp_path / "b", upload_title="She finally got it #shorts")
    assert (b / "title.txt").read_text(encoding="utf-8") == "She finally got it #shorts"
    PB.publish(b, shared / "b")
    d = P.load(shared / "b" / b.name).data
    assert (d["title"], d["upload_title"]) == (HEADLINE, "She finally got it #shorts")
    assert (shared / "b" / b.name / "title.txt").read_text(encoding="utf-8") == "She finally got it #shorts"


def test_a_title_txt_edited_by_hand_goes_into_project_json(tmp_path):
    src = make_project(tmp_path / "local", upload_title="Upload one")
    (src / "title.txt").write_bytes("\ufeffUpload two \u00e9\r\n".encode("utf-8"))   # Notepad: a BOM, CRLF
    PB.publish(src, tmp_path / "Shared")
    d = P.load(tmp_path / "Shared" / src.name).data
    assert d["upload_title"] == "Upload two \u00e9" and d["title"] == HEADLINE
    assert (tmp_path / "Shared" / src.name / "title.txt").read_text(encoding="utf-8") == "Upload two \u00e9"


def test_the_title_changed_last_wins_between_the_two_copies(tmp_path):
    shared = tmp_path / "Shared"
    src = make_project(tmp_path / "local", upload_title="First upload title")
    PB.publish(src, shared)
    dest = shared / src.name
    later = time.time() + 30                                   # the other computer renames the upload later
    d = P.load(dest).data
    d["upload_title"] = "Their newer upload title"
    P.write_json(dest / P.FILE, d)
    PB.write_title_txt(dest, d)
    os.utime(dest / "title.txt", (later, later))
    PB.publish(src, shared)
    assert P.load(dest).data["upload_title"] == "Their newer upload title"
    assert (dest / "title.txt").read_text(encoding="utf-8") == "Their newer upload title"
    later2 = later + 60                                        # now this computer changes it, later still
    p = P.load(src)
    p.update(upload_title="Mine, newest")
    PB.write_title_txt(src, p.data)
    os.utime(src / "title.txt", (later2, later2))
    PB.publish(src, shared)
    assert P.load(dest).data["upload_title"] == "Mine, newest"
    assert (dest / "title.txt").read_text(encoding="utf-8") == "Mine, newest"


def test_title_txt_text_rule():
    assert PB.title_txt_text({"title": "H", "upload_title": "U"}) == "U"
    assert PB.title_txt_text({"title": "H", "upload_title": "  "}) == "H"
    assert PB.title_txt_text({"title": "H"}) == "H" and PB.title_txt_text({}) == ""


# ---------------------------------------------------------------------------------------------------------------------
# the shared folder away
# ---------------------------------------------------------------------------------------------------------------------

def test_an_unreachable_shared_folder_raises_at_once(setup):
    src, local, shared = setup
    gone = local.parent / "no drive" / "nothing here" / "Projects"
    t0 = time.time()
    with pytest.raises(PB.SharedUnreachable) as e:
        PB.publish(src, gone)
    assert time.time() - t0 < 2 and "cannot be reached" in str(e.value)
    assert not gone.parent.exists()                            # nothing made there


def test_reachable_never_waits_longer_than_its_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(PB, "_answers", lambda root: time.sleep(3) or True)   # a sleeping PC's network path
    t0 = time.time()
    assert PB.reachable(tmp_path, timeout_s=0.3) is False
    assert time.time() - t0 < 1.0
    monkeypatch.undo()
    assert PB.reachable(tmp_path) is True and PB.reachable(tmp_path / "new") is True   # made at the first publish
    assert PB.reachable(tmp_path / "a" / "b") is False


def test_why_the_shared_folder_cannot_be_used_in_plain_words(monkeypatch, tmp_path):
    assert PB.why_unreachable(tmp_path) == "" and PB.why_unreachable(tmp_path / "new") == ""
    assert PB.why_unreachable(tmp_path / "a" / "b") == f"{tmp_path / 'a'} is not there"
    share = Path(r"\\192.168.101.11\Shared\Projects")

    def denied(p):
        raise PermissionError(13, "Access is denied", str(p))
    monkeypatch.setattr(PB, "_stat", denied)                    # this Windows user may not open the share
    why = PB.why_unreachable(share)
    assert why.startswith(r"this Windows user may not open \\192.168.101.11\Shared\Projects (access denied")

    def gone(p):
        raise OSError(0, "The network path was not found", str(p), 53)   # what os.stat raises: WinError 53
    with pytest.raises(FileNotFoundError):
        gone(share)
    monkeypatch.setattr(PB, "_stat", gone)
    assert "is not found on the network" in PB.why_unreachable(share)
    monkeypatch.setattr(PB, "_why_not", lambda root: time.sleep(3) or "")
    t0 = time.time()
    assert PB.why_unreachable(share, timeout_s=0.2).endswith("does not answer (the PC asleep or off, or no network)")
    assert time.time() - t0 < 1.0


def test_call_with_timeout():
    assert PB.call_with_timeout(lambda x: x * 2, 1.0, 21) == 42
    with pytest.raises(ZeroDivisionError):
        PB.call_with_timeout(lambda: 1 / 0, 1.0)
    with pytest.raises(TimeoutError):
        PB.call_with_timeout(time.sleep, 0.1, 2)


def test_the_queue_publishes_once_the_folder_is_back(setup, monkeypatch):
    src, local, shared = setup
    away = local.parent / "PC asleep" / "x" / "Projects"
    res, note = PB.publish_or_queue(src, away, local)
    assert res is None and "cannot be reached" in note and note.startswith("saved on this computer for now")
    entry = PB.queue_pending(local, src, away, "again")         # a second time: still one entry
    q = PB.pending(local)
    assert len(q) == 1 and q[0]["folder"] == str(src) and q[0]["why"] == "again" and entry["name"] == src.name
    assert PB.retry_pending(local, away) == [] and len(PB.pending(local)) == 1   # still away: nothing happens
    gone = make_project(local / "elsewhere")
    PB.queue_pending(local, gone, away, "x")
    import shutil
    shutil.rmtree(gone)                                         # a queued project deleted meanwhile
    done = PB.retry_pending(local, shared)                      # the shared folder answers (the setting now)
    assert [r.dest for r in done] == [shared / src.name] and PB.pending(local) == []
    assert not (local / PB.PENDING_FILE).exists() and (shared / src.name / P.FILE).is_file()


def test_a_broken_queue_file_is_kept_aside(setup):
    src, local, shared = setup
    (local / PB.PENDING_FILE).write_text("{oops", encoding="utf-8")
    assert PB.pending(local) == []
    PB.queue_pending(local, src, shared, "why")
    assert (local / (PB.PENDING_FILE + ".bad")).read_text(encoding="utf-8") == "{oops"
    assert [x["name"] for x in PB.pending(local)] == [src.name]


def test_plain_sizes():
    assert PB.plain_size(0) == "0 bytes" and PB.plain_size(1) == "1 byte" and PB.plain_size(512) == "512 bytes"
    assert PB.plain_size(1288490189) == "1.2 GB" and PB.plain_size(3400000) == "3.2 MB"
    assert PB.plain_size(150 * 1024 ** 3) == "150 GB" and PB.plain_size(2048) == "2.0 KB"
