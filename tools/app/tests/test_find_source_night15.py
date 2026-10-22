"""Night 15, task 12: "Find source" -- which movie or episode each clip comes from (core.find_source), many at once on
its own page (tools.find_source_tool): the post's text, your RAWs (the clip's sound in them = sure), public lists."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mr_app.core import find_source as FS


@pytest.mark.parametrize("name, want", [
    ("American Dad! - S03E02 - The American Dad After School Special - [HDTV-1080p]h265 AAC.mp4",
     ("American Dad!", None, 3, 2, "The American Dad After School Special", "episode")),
    ("Dr..Dolittle.2.2001.1080p.BluRay.x264.AAC5.1-[YTS.GG - YTS.BZ].mp4", ("Dr. Dolittle 2", 2001, None, None, "", "movie")),
    ("The Suite Life of Zack and Cody - 1x10 - Cody Goes to Camp.mp4",
     ("The Suite Life of Zack and Cody", None, 1, 10, "Cody Goes to Camp", "episode")),
    ("Family Guy S08E11 Dial Meg for Murder 1080p DSNP WEB-DL DDP5 1 H 264-FLUX.mp4",
     ("Family Guy", None, 8, 11, "Dial Meg for Murder", "episode")),
    ("Borat (2006) (1080p BluRay x265 Silence).mp4", ("Borat", 2006, None, None, "", "movie")),
    ("_222FIXEDLittle Britain S01E01 DVDRip HEVC H265 BONE.mp4", ("Little Britain", None, 1, 1, "", "episode")),
])
def test_a_raws_name_says_what_it_is(name, want):
    t = FS.parse_name(name)
    assert (t.name, t.year, t.season, t.episode, t.episode_title, t.kind) == want


def test_names_that_say_nothing_are_no_title():
    assert FS.parse_name("scene.mp4") is None and FS.parse_name("raw.mp4") is None


def _lib(tmp_path):
    snd = tmp_path / "s.wav"
    snd.write_bytes(b"x")
    names = ["American Dad! - S03E02 - The American Dad After School Special.mp4", "Family Guy - S11E10 - Brian's Play.mp4",
             "Family Guy S08E11 Dial Meg for Murder 1080p.mp4", "Dr..Dolittle.2.2001.1080p.mp4",
             "Blue Mountain State - S03E05 - Training Day.mp4"]
    return [FS.LibItem(FS.parse_name(n), n, str(snd)) for n in names]


def _clip(text, name="Reel X"):
    return FS.Clip(source=f"https://www.instagram.com/reel/{name}/", name=name, platform="Instagram", length=27.8,
                   text=text)


def _run(monkeypatch, tmp_path, text, sound_in=(), named_online=None):
    lib = _lib(tmp_path)
    monkeypatch.setattr(FS, "read_clip", lambda s, cancel=None: _clip(text))
    monkeypatch.setattr(FS, "_clip_file", lambda c, w, cancel: tmp_path / "clip.mp4")
    checked = []

    def sound(clip_file, raw_sound, _lib=lib):
        checked.append(raw_sound)
        return 0.86 if any(n in raw_sound for n in sound_in) else 0.0
    for it in lib:                                       # each RAW its own sound file name (its title)
        it.sound = it.file
    monkeypatch.setattr(FS, "_sound_found", sound)
    monkeypatch.setattr(FS, "online", lambda c, ep=None: named_online.get(FS.norm(c)) if named_online else None)
    return FS.find("x", lib), checked


def test_the_text_names_it_and_the_sound_is_in_your_raw_sure(monkeypatch, tmp_path):
    a, checked = _run(monkeypatch, tmp_path, "This hilarious scene from American Dad! captures Steve…", ["American Dad"])
    assert a.verdict == FS.SURE and a.title.text() == "American Dad! — S03E02" and a.sure >= 0.9
    assert "sound found in your RAW" in a.how and len(checked) == 1
    assert a.line(1) == "1. Reel X — American Dad! — S03E02 — sure 98 %"


def test_nothing_in_the_text_the_sound_in_another_raw_finds_it(monkeypatch, tmp_path):
    a, checked = _run(monkeypatch, tmp_path, "The fact that this is Jack Reacher still cracks me up.", ["Blue Mountain"])
    assert a.verdict == FS.SURE and a.title.text() == "Blue Mountain State — S03E05"
    assert checked and "Blue Mountain" in checked[-1]


def test_a_show_whose_episodes_here_are_all_wrong_is_the_show_only(monkeypatch, tmp_path):
    a, _c = _run(monkeypatch, tmp_path, "Family Guy follows the dysfunctional Griffin family in Quahog")
    assert a.verdict == FS.CHECK and a.title.text() == "Family Guy" and a.title.kind == "show"
    assert "not one of your 2 (S11E10, S08E11)" in a.how


def test_the_text_names_it_but_the_sound_is_not_in_your_raw_check_with_the_second(monkeypatch, tmp_path):
    a, _c = _run(monkeypatch, tmp_path, "Eddie Murphy in Dr. Dolittle 2 talking to the bear")
    assert a.verdict == FS.CHECK and a.title.text() == "Dr. Dolittle 2 (2001)" and 0.6 <= a.sure < 0.9
    assert "its sound was not found in your RAW of it" in a.how


def test_a_name_not_on_this_computer_from_the_public_lists(monkeypatch, tmp_path):
    t = FS.Title("Superbad", 2007)
    a, _c = _run(monkeypatch, tmp_path, "Funniest scene from Superbad #superbad #comedy", named_online={"superbad": t})
    assert a.verdict == FS.CHECK and a.title is t and "post's text names it" in a.how


def test_nothing_found_says_why(monkeypatch, tmp_path):
    a, _c = _run(monkeypatch, tmp_path, "wait for it 😂 #fyp #viral")
    assert a.verdict == FS.NOT_FOUND and a.title is None and "names no movie or show" in a.reason
    assert a.line(3).startswith("3. Reel X — not found (")


def test_candidates_from_the_post_text():
    c = FS.candidates("Best scene from Dr. Dolittle 2 (2001) lol #eddiemurphy #fyp")
    assert "Dr. Dolittle 2" in c[0] and "eddiemurphy" in c and "fyp" not in c
    assert FS.episode_in("Family Guy S08E11 scene") == (8, 11) and FS.episode_in("no episode") is None


def test_the_history_keeps_the_last_list_after_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    a = FS.Answer(_clip("x"), FS.Title("American Dad!", None, 3, 2, "After School", "episode"), FS.SURE, 0.98, "sound")
    b = FS.Answer(_clip("y", "Reel Y"), None, FS.NOT_FOUND, 0.0, reason="no text")
    FS.save_history([a, b])
    h = FS.load_history()
    back = [FS.Answer.from_dict(d) for d in h["last"]]
    assert [x.line(k) for k, x in enumerate(back, 1)] == [a.line(1), b.line(2)] and len(h["runs"]) == 1
    assert FS.copy_all(back).splitlines()[0] == "1. Reel X — American Dad! — S03E02 — sure 98 %"
    assert FS.split_input(" a \n\n b\na\n") == ["a", "b"]
    assert FS.Title("American Dad!", None, 3, 2).search_words() == "American Dad S03E02 1080p"
    assert FS.Title("Dr. Dolittle 2", 2001).search_words() == "Dr Dolittle 2 2001 1080p"


def test_the_page_finds_many_at_once_rows_fill_in_and_copy_all(tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from mr_app.tools import find_source_tool as T
    from mr_app.ui import motion as Mo
    monkeypatch.setenv("MOVIERECAPS_APPDATA", str(tmp_path / "appdata"))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    Mo.set_enabled(False)
    monkeypatch.setattr(FS, "library", lambda roots=None: [])
    at_once, live = [], [0]
    import threading
    lock = threading.Lock()

    def fake_find(s, lib, cancel=None, say=None, work=None):
        with lock:
            live[0] += 1
            at_once.append(live[0])
        time.sleep(0.3)
        with lock:
            live[0] -= 1
        ok = "dad" in s
        return FS.Answer(FS.Clip(s, name=s[-6:], platform="Instagram"),
                         FS.Title("American Dad!", None, 3, 2, kind="episode") if ok else None,
                         FS.SURE if ok else FS.NOT_FOUND, 0.97 if ok else 0.0, "sound" if ok else "",
                         reason="" if ok else "no text")
    monkeypatch.setattr(FS, "find", fake_find)
    toasts = []
    page = T.FindSourcePage(SimpleNamespace(toast=lambda t, kind="ok": toasts.append(t)))
    try:
        page.box.setPlainText("\n".join([f"https://x/dad{k}" for k in range(6)] + ["https://x/none"]))
        assert page.b_find.text() == "Find all (7)"
        page.find_all()
        t0 = time.time()
        while page.running and time.time() - t0 < 20:
            app.processEvents()
            time.sleep(0.02)
        assert not page.running and len(page.rows) == 7 and len(page.answers) == 7
        assert max(at_once) == T.AT_ONCE                              # five side by side, the rest waited
        assert page.rows[0].src.text() == "American Dad! — S03E02" and page.rows[0].sure.text() == "Sure 97 %"
        assert page.rows[6].sure.text() == "Not found" and page.rows[6].sure_small.text() == "no text"
        page.copy_all()
        text = QtWidgets.QApplication.clipboard().text().splitlines()
        assert len(text) == 7 and text[0].startswith("1. ") and "sure 97 %" in text[0] and "not found" in text[6]
        again = T.FindSourcePage(SimpleNamespace(toast=lambda t, kind="ok": None))   # the app opened again
        assert len(again.rows) == 7 and again.rows[0].src.text() == "American Dad! — S03E02"
        assert again.box.toPlainText().splitlines()[0] == "https://x/dad0"
        again.deleteLater()
    finally:
        Mo.set_enabled(True)
        page.deleteLater()
        app.processEvents()
