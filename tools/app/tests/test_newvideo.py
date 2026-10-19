"""Day 12, the "New video" form's logic (core.newvideo), the real titles (core.titles: the vision model is never
loaded -- a fake reader), turning the PC off (core.shutdown: never really), the default template / Change headline
and the Just cuts result (core.flow)."""
from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "match_cuts"))

from mr_app.core import newvideo as NV  # noqa: E402
from mr_app.core import projects as P  # noqa: E402
from mr_app.core import shutdown as SD  # noqa: E402
from mr_app.core import titles as TT  # noqa: E402


@pytest.mark.parametrize("text,kind", [
    ("https://youtu.be/AbCdEf12345", NV.YOUTUBE),
    ("https://www.youtube.com/watch?v=AbCdEf12345", NV.YOUTUBE),
    ("youtube.com/shorts/Zx9Yw8Vu7Ts", NV.YOUTUBE),
    ("magnet:?xt=urn:btih:EXAMPLE&dn=Example.Movie.2016.1080p", NV.TORRENT),
    (r"C:\Users\me\Downloads\Example.Movie.torrent", NV.TORRENT),
    ("https://example.com/files/movie.torrent?x=1", NV.TORRENT),
    (r'"D:\Shared\RAW\episode 3.mkv"', NV.FILE),
    ("https://www.instagram.com/reel/C0abcdEFgh/", NV.LINK),
    ("https://www.tiktok.com/@someone/video/7312345678901234567", NV.LINK),
    ("", NV.NONE), ("   ", NV.NONE), ("hello there", NV.NONE),
])
def test_what_was_pasted_is_recognised_by_itself(text, kind):
    assert NV.kind_of(text) == kind


def test_will_make_says_what_it_makes_before_anything_is_pressed():
    yt, mg, fl = "https://youtu.be/AbCdEf12345", "magnet:?xt=urn:btih:X", r"D:\raw.mp4"
    comp = "https://youtube.com/shorts/Zx9Yw8Vu7Ts"
    assert NV.will_make_text(yt, comp) == "Full Match cuts edit"
    assert NV.will_make_text(mg, comp) == "Full Match cuts edit"
    assert NV.will_make_text(yt, "") == "Just cuts (every scene change)"
    assert NV.will_make_text(fl, "") == "Just cuts (every scene change)"
    assert NV.will_make(NV.TORRENT, NV.NONE) == NV.NEEDS_COMP          # a magnet alone: not allowed
    assert NV.will_make_text("", comp) == "—"


def test_batch_lines_are_pairs_and_each_problem_names_its_line():
    pairs, probs = NV.parse_batch(
        "https://youtu.be/AbCdEf12345 | https://youtube.com/shorts/Zx9Yw8Vu7Ts\n"
        "\n"
        "# a comment\n"
        "https://youtu.be/BbCdEf12345\n"
        "magnet:?xt=urn:btih:X\n"
        "nonsense | https://youtube.com/shorts/Zx9Yw8Vu7Ts\n"
        "a | b | c\n")
    assert [(p.raw[-11:], p.competitor[-11:], p.line) for p in pairs] == [
        ("AbCdEf12345", "Zx9Yw8Vu7Ts", 1), ("BbCdEf12345", "", 4)]
    assert any(p.startswith("line 5:") and "competitor" in p for p in probs)
    assert any(p.startswith("line 6:") for p in probs) and any(p.startswith("line 7:") for p in probs)


def test_automatic_and_the_channel_are_remembered_in_the_settings_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_SETTINGS", str(tmp_path / "settings.json"))
    assert NV.automatic() is False                                  # OFF by default
    NV.set_automatic(True)
    NV.set_last_channel("LouLaughs")
    assert NV.automatic() is True
    assert NV.last_channel(["Flick707", "LouLaughs", "MikeMemesYou"]) == "LouLaughs"
    assert NV.last_channel(["Flick707"]) == "Flick707"              # gone: the first one
    d = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert d["newvideo_automatic"] is True and d["newvideo_channel"] == "LouLaughs"


def test_the_headline_keeps_the_break_you_typed_and_at_most_two_lines():
    assert NV.headline_text("The fact that prison changed  \n him this much is hilarious 🔒😭") == \
        "The fact that prison changed\nhim this much is hilarious 🔒😭"
    assert NV.headline_text("one\ntwo\nthree") == "one\ntwo three"
    assert NV.headline_text("no break at all 😭") == "no break at all 😭"
    assert NV.one_line("a\nb") == "a b"


@pytest.mark.parametrize("err,words", [
    (OSError(errno.ENOSPC, "No space left on device"), NV.DISK_FULL),
    ("not enough free space on D: ...", NV.DISK_FULL),
    ("[ WARN:0@1.2] global cap.cpp:123 OpenCV warning\nRAW: this video is private", "RAW: this video is private"),
    (KeyError(), "something went wrong (KeyError) -- press Try again"),
    ("", "it stopped without saying why"),
])
def test_errors_come_out_in_plain_words_never_a_library_warning(err, words):
    assert NV.plain_error(err).startswith(words)


def test_classify_names_the_plain_cases():
    assert NV.classify("no peers for 20 minutes") == NV.NO_PEERS
    assert NV.classify("this video is private") == NV.PRIVATE
    assert NV.classify("the link is wrong: no video") == NV.BROKEN_LINK


def test_newest_on_top_running_then_waiting_then_finished():
    class R:
        def __init__(self, name, g, added):
            self.name, self.g, self.added = name, g, added

        def group(self):
            return self.g
    rows = [R("old done", NV.FINISHED, 1), R("old wait", NV.WAITING, 2), R("run", NV.RUNNING, 3),
            R("new wait", NV.WAITING, 4), R("new done", NV.FINISHED, 5)]
    assert [r.name for r in NV.order(rows)] == ["run", "new wait", "old wait", "new done", "old done"]


# --- titles ---

def test_the_post_title_is_cleaned_of_tags_names_links_and_emoji_spam():
    raw = ("When he finally found out 😭😭😭😭😭 #funny #movie @someone https://t.co/xyz\n"
           "second line #fyp")
    assert TT.clean(raw) == "When he finally found out 😭😭"
    long = "word " * 40
    assert len(TT.clean(long)) <= TT.MAX_LEN + 1 and TT.clean(long).endswith("…")


def test_a_useless_title_is_skipped_for_the_description():
    assert TT.useless("Video by darkhumor") and TT.useless("Reel") and TT.useless("") and TT.useless("😭😭")
    assert TT.useless("darkhumor on TikTok")
    assert not TT.useless("The moment he realised")
    info = {"title": "Video by darkhumor", "description": "The moment he realised it was over 💀 #fyp #movie"}
    assert TT.post_title(info) == "The moment he realised it was over 💀"
    assert TT.post_title({"title": "Video by x", "description": "#fyp #viral"}) == ""


def test_the_headline_on_the_video_keeps_its_line_break(tmp_path, monkeypatch):
    monkeypatch.setattr(TT, "READER", lambda video: '"The fact that prison changed\nhim this much is hilarious 🔒😭"')
    assert TT.read_on_video(tmp_path / "c.mp4") == "The fact that prison changed\nhim this much is hilarious 🔒😭"
    monkeypatch.setattr(TT, "READER", lambda video: "NONE")
    assert TT.read_on_video(tmp_path / "c.mp4") == ""

    def broken(video):
        raise RuntimeError("CUDA out of memory")
    monkeypatch.setattr(TT, "READER", broken)
    assert TT.read_on_video(tmp_path / "c.mp4") == ""                # a help, never a stop


def test_the_child_reader_answers_one_json_line_with_a_fake_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(TT, "frames", lambda video: [object(), object()])

    class Fake:
        def ask(self, images, prompt):
            assert "line break" in prompt and len(images) == 2
            return "Headline: line one\nline two 😭"
    assert TT.read_in_this_process(tmp_path / "c.mp4", Fake()) == "line one\nline two 😭"


def test_the_folder_is_named_from_the_headline_and_both_titles_are_saved(tmp_path):
    p = P.create(tmp_path, competitor_url="https://www.instagram.com/reel/C0abcdEFgh/", name=None)
    p.update(post_title="", generic_name=True)
    TT.save(p, "The moment he realised", "The fact that prison changed\nhim this much 🔒😭")
    p2, note = TT.rename_folder(p, "The fact that prison changed\nhim this much 🔒😭", "The moment he realised")
    assert p2.folder.name.endswith("_the-fact-that-prison-changed-him-this")
    assert p2.folder.is_dir() and not p.folder.exists()
    assert "headline" in note
    d = json.loads((p2.folder / P.FILE).read_text(encoding="utf-8"))
    assert d["video_headline"].startswith("The fact") and d["post_title"] == "The moment he realised"
    assert d["name"] == p2.folder.name
    txt = (p2.folder / TT.TITLES_TXT).read_text(encoding="utf-8")
    assert "Title from the post: The moment he realised" in txt and "him this much" in txt


def test_nothing_readable_keeps_the_old_name_and_says_so(tmp_path):
    p = P.create(tmp_path, competitor_url="https://www.instagram.com/reel/C0abcdEFgh/")
    p2, note = TT.rename_folder(p, "", "")
    assert p2.folder == p.folder and "keeps its name" in note
    TT.save(p, "", "")
    assert "nothing readable" in (p.folder / TT.TITLES_TXT).read_text(encoding="utf-8")


# --- turning the PC off ---

def test_the_pc_is_never_turned_off_while_premiere_has_unsaved_work_or_a_render_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(SD, "SHUTDOWN", lambda: calls.append("off"))
    monkeypatch.setattr(SD, "CHECKS", [("Premiere has unsaved work", lambda: True), ("render", lambda: False)])
    assert SD.turn_off() == ["Premiere has unsaved work"] and calls == []
    monkeypatch.setattr(SD, "CHECKS", [("Premiere has unsaved work", lambda: False), ("render", lambda: True)])
    assert SD.turn_off() == ["render"] and calls == []
    monkeypatch.setattr(SD, "CHECKS", [("x", lambda: False)])
    assert SD.turn_off() == [] and calls == ["off"]


def test_premiere_unsaved_is_its_titles_star(monkeypatch):
    from mr_app.core import premiere
    monkeypatch.setattr(premiere, "state", lambda: premiere.State(running=True, title="Adobe Premiere Pro 2024 - "
                                                                  r"C:\x\edit.prproj *"))
    assert SD.premiere_unsaved() is True
    monkeypatch.setattr(premiere, "state", lambda: premiere.State(running=True, title="Adobe Premiere Pro 2024 - "
                                                                  r"C:\x\edit.prproj", dialogs=["Encoding edit"]))
    assert SD.premiere_unsaved() is False and SD.premiere_rendering() is True


# --- flow: the default template, Change headline, Just cuts' result ---

def test_the_default_template_is_the_psd_as_it_is_and_change_headline_swaps_it_into_the_edit(tmp_path):
    from mr_app.core import flow
    if flow.find_channel("Flick707") is None:
        pytest.skip("no channel templates in this checkout")
    p = P.create(tmp_path, competitor_url="https://youtu.be/AbCdEf12345", raw_url="https://youtu.be/BbCdEf12345")
    p.update(channel="Flick707")
    res = flow.default_template_step(p)
    png = p.folder / flow.TEMPLATE_PNG
    assert res["default"] and png.is_file()
    assert P.load(p.folder).step("template")["default"] is True
    from PIL import Image
    with Image.open(png) as im:
        assert im.size == (1080, 1920)
    media = p.folder / "edit" / "extras" / "media"                  # an edit made with it
    media.mkdir(parents=True)
    (p.folder / "edit" / "1_edit.xml").write_text("<xmeml/>", encoding="utf-8")
    (media / "frame.png").write_bytes(png.read_bytes())
    before = (media / "frame.png").read_bytes()
    out = flow.change_headline(P.load(p.folder), "When she finally\ngot the part 💀😭")
    assert out["swapped_into"] == ["edit"]
    assert (media / "frame.png").read_bytes() == png.read_bytes() != before
    st = P.load(p.folder).step("template")
    assert st["default"] is False and st["title"].startswith("When she")


def test_just_cuts_result_reads_its_json_and_says_a_stop_in_plain_words(tmp_path):
    from mr_app.core import flow
    run = tmp_path / "cuts"
    (run / "extras").mkdir(parents=True)
    assert flow.just_cuts_result(run, 2, "[ WARN:0] global something.cpp OpenCV\nmatch_cuts: raw file not found: x"
                                 )["failures"][0] == "raw file not found: x"
    (run / "1_edit.xml").write_text("<xmeml/>", encoding="utf-8")
    (run / "extras" / "just_cuts.json").write_text(json.dumps({"cuts": 37, "clips": 38, "errors": []}),
                                                  encoding="utf-8")
    r = flow.just_cuts_result(run, 0)
    assert r["label"] == "PASS" and r["cuts"] == 37
    assert flow.just_cuts_result(run, 1)["label"] == "OK with a note"
    cmd = flow.just_cuts_command(Path("py"), Path("raw.mp4"), run, Path("w"), Path("t.png"), None)
    assert cmd[1:3] == ["-m", "match_cuts.just_cuts"] and "--frame" in cmd and "--watermark" not in cmd


def test_the_just_cuts_step_makes_the_edit_with_the_channels_template_end_to_end(tmp_path):
    """The real tool on a short synthetic RAW (three shots and a 0.2 s flash): the cuts' run folder, the template
    as its frame, the result in project.json."""
    import subprocess
    from mr_app.core import flow
    from match_cuts.common import ffmpeg_bin
    if flow.find_channel("Flick707") is None:
        pytest.skip("no channel templates in this checkout")
    p = P.create(tmp_path, raw_url="https://youtu.be/AbCdEf12345")
    raw = p.folder / "raw.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:d=2",
                    "-f", "lavfi", "-i", "color=c=red:size=640x360:rate=30:d=2",
                    "-f", "lavfi", "-i", "color=c=blue:size=640x360:rate=30:d=0.2",
                    "-f", "lavfi", "-i", "smptebars=size=640x360:rate=30:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6.2",
                    "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0,format=yuv420p[v]",
                    "-map", "[v]", "-map", "4:a", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(raw)], check=True)
    p.update(raw={"file": "raw.mp4"}, channel="Flick707", title="When she finally\ngot the part 💀😭", kind="just_cuts")
    said = []
    res = flow.just_cuts_step(P.load(p.folder), lambda r, s, t: said.append(t), work=tmp_path / "work")
    assert res["label"] == "PASS", res
    assert res["cuts"] == 2 and Path(res["run_dir"]).name == "cuts"
    assert (p.folder / "cuts" / "1_edit.xml").is_file() and (p.folder / "cuts" / "extras" / "media" / "frame.png").is_file()
    d = P.load(p.folder).data
    assert d["steps"]["edit"]["state"] == "done" and d["steps"]["edit"]["kind"] == "just_cuts"
    assert d["steps"]["template"]["state"] == "done"
    assert said and said[0].startswith("making your channel's template")


def test_a_project_without_a_competitor_is_just_cuts(tmp_path):
    from mr_app.core import flow
    p = flow.new_project("", "https://youtu.be/AbCdEf12345", root=tmp_path, just_cuts=True)
    assert flow.roles_of(p) == ("raw",) and p.data["kind"] == "just_cuts"
    with pytest.raises(flow.StepError):
        flow.new_project("", "https://youtu.be/AbCdEf12345", root=tmp_path)
    f = tmp_path / "comp.mp4"
    f.write_bytes(b"x")
    q = flow.new_project("", "https://youtu.be/AbCdEf12345", root=tmp_path, competitor_file=f)
    assert flow.roles_of(q) == ("competitor", "raw")
    assert os.path.samefile(q.data["competitor"]["source_file"], f)
