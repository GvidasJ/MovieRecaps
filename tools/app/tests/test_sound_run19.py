"""Run 19, task 2 -- no temporary extra Volume effects. 1_edit.xml brings no Gain filter any more, so right after the
import every A1 clip has only its own Volume (0 dB) and plays at the RAW's own level; the app's save step (Open in
Premiere -> Ctrl+S -> Premiere closed, the project written, opened again) gives it its Clip Gain, the Hard Limiter and
Amplify. The save step holds on as long as Premiere runs (another project open meanwhile, Premiere not closing at once),
and the row says plainly when the sound isn't set yet. Premiere itself is faked here.

Run: python -m pytest tools/app/tests/test_sound_run19.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import premiere as PR  # noqa: E402
from mr_app.core import song_gain as G  # noqa: E402

from test_a1_chain_run18 import A1, _chains, _project, _xml  # noqa: E402
from test_song_gain_day17 import _Fake  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")
from test_board_ui import qapp  # noqa: E402,F401 - the board tests' QApplication (a fixture)


@pytest.fixture(autouse=True)
def _a1_chain_on(monkeypatch):
    """27 Oct: A1's chain is OFF by default (A1 left at 0 dB); these tests check it switched on."""
    monkeypatch.setenv("MOVIERECAPS_A1_CHAIN", "1")



@pytest.fixture(autouse=True)
def _clean_state():
    PR.SOUND.clear()
    PR._PROJ_OF.clear()                                         # noqa: SLF001
    yield
    PR.SOUND.clear()
    PR._PROJ_OF.clear()                                         # noqa: SLF001


def _run19(tmp_path: Path) -> tuple[Path, Path]:
    """An edit and its project as Premiere saves a run-19 import: every A1 clip with only its own Volume (0 dB)."""
    return _xml(tmp_path / "e.xml", A1), _project(tmp_path / "e.prproj", [[(0, []), (90, []), (200, [])]])


def _saved(proj: Path, seconds: float = 5.0) -> None:
    st = os.stat(proj)
    os.utime(proj, (st.st_atime, st.st_mtime + seconds))


def test_a_run19_import_has_one_volume_and_gets_its_sound_on_save(tmp_path):
    xml, proj = _run19(tmp_path)
    before = _chains(proj)
    assert all(c["names"] == ["Volume"] and c["gain"] is None for c in before)        # one Volume, at 0 dB
    assert all(abs(c["vals"]["Volume"]["Level"] - G.level_value(0)) < 1e-6 for c in before)
    assert G.holds_edit_sound(proj, xml) and G.needs_fix(proj, xml)
    said = G.fix(proj, xml)
    assert "extra Volume" not in said and "own Clip Gain (+5.50 to +15.20 dB)" in said
    after = _chains(proj)
    for ch, (_s, g) in zip(after, A1):
        assert ch["names"] == ["Volume", "Hard Limiter", "Amplify"]
        assert ch["gain"] == pytest.approx(10 ** (g / 20), rel=1e-6)
    assert not G.needs_fix(proj, xml)


def test_the_watch_waits_out_another_project_then_sets_the_sound(tmp_path, monkeypatch):
    xml, proj = _run19(tmp_path)
    fake = _Fake(proj)
    other = tmp_path / "yours.prproj"
    seen = {"n": 0, "notes": []}

    def state():
        st = fake.state()
        if seen["n"] < 4 and st.running:                       # first your own project is open in Premiere ...
            return PR.State(running=True, title=f"Adobe Premiere Pro 2024 - {other}", project=other, pid=1, hwnd=5)
        return st

    def sleep(_s):
        seen["n"] += 1
        seen["notes"].append(dict(PR.SOUND.get(PR._key(proj), {})))           # noqa: SLF001
        if seen["n"] == 6:                                     # ... then this one, and you press Ctrl+S
            fake.changed = False
            _saved(proj)
    monkeypatch.setattr(PR, "state", state)
    monkeypatch.setattr(PR, "_sleep", sleep)

    def close(_st):
        fake.running = False
        fake.closed += 1
        return True
    monkeypatch.setattr(PR, "_close", close)
    monkeypatch.setattr(PR, "_launch", lambda exe, *a: (fake.opened.append(a), setattr(fake, "running", True)))
    monkeypatch.setattr(PR, "_wait", lambda cond, s: fake.state())
    out = PR.song_watch(Path("premiere.exe"), proj, xml, timeout=1e9)
    assert fake.closed == 1 and fake.opened == [(proj,)] and not G.needs_fix(proj, xml)
    assert "the sound is set" in out and PR.SOUND[PR._key(proj)]["state"] == PR.SET      # noqa: SLF001
    texts = [n.get("text", "") for n in seen["notes"]]
    assert any("another project is open in Premiere (yours.prproj)" in t for t in texts)  # said while it waited
    assert all(t.startswith(PR.NOT_SET_YET) for t in texts if t)


def test_premiere_not_closing_is_tried_again_after_your_next_save(tmp_path, monkeypatch):
    xml, proj = _run19(tmp_path)
    fake = _Fake(proj)
    fake.changed = False
    _saved(proj)
    tries = {"n": 0, "ticks": 0}

    def sleep(_s):
        tries["ticks"] += 1
        if tries["ticks"] == 8:                                # you answered the message and pressed Ctrl+S again
            _saved(proj, 10.0)
    monkeypatch.setattr(PR, "state", fake.state)
    monkeypatch.setattr(PR, "_sleep", sleep)

    def close(_st):
        tries["n"] += 1
        if tries["n"] == 1:
            return False                                       # a message in Premiere: it did not close
        fake.running = False
        return True
    monkeypatch.setattr(PR, "_close", close)
    monkeypatch.setattr(PR, "_launch", lambda exe, *a: setattr(fake, "running", True))
    monkeypatch.setattr(PR, "_wait", lambda cond, s: fake.state())
    out = PR.song_watch(Path("premiere.exe"), proj, xml, timeout=1e9, t_import=0.0)
    assert tries["n"] == 2 and "the sound is set" in out and not G.needs_fix(proj, xml)


def test_premiere_closed_before_saving_says_the_sound_is_not_set(tmp_path, monkeypatch):
    xml = _xml(tmp_path / "e.xml", A1)
    proj = _project(tmp_path / "e.prproj", [])                 # the project as it was before the import: no edit
    fake = _Fake(proj)
    fake.running = False
    monkeypatch.setattr(PR, "state", fake.state)
    monkeypatch.setattr(PR, "_sleep", lambda _s: None)
    monkeypatch.setattr(PR, "_close", lambda st: pytest.fail("not closed: it is closed already"))
    out = PR.song_watch(Path("p.exe"), proj, xml, timeout=1e9)
    assert out.startswith(PR.NOT_SET_YET) and "closed before the project was saved" in out
    assert PR.SOUND[PR._key(proj)]["state"] == PR.NOT_SET and not G.holds_edit_sound(proj, xml)   # noqa: SLF001


def test_the_row_line_says_it_plainly(qapp, tmp_path):
    from mr_app.ui.sound_line import SoundLine
    xml, proj = _run19(tmp_path)
    line = SoundLine()
    try:
        line.set_xml(xml)
        assert line.isHidden()                                 # not opened in Premiere: nothing to say
        PR._PROJ_OF[PR._key(xml)] = PR._key(proj)              # noqa: SLF001 - what Open in Premiere records
        PR._sound(PR._key(proj), PR.WAITING, PR.NOT_SET_YET)   # noqa: SLF001
        line.refresh()
        assert not line.isHidden() and line.text() == "the sound isn't set yet: press Ctrl+S in Premiere"
        PR._done_or_not(PR._key(proj), G.fix(proj, xml), proj, xml, "")       # noqa: SLF001 - the save step ran
        line.refresh()
        assert line.text().startswith("✓ the sound is set: A1: 3 clips with their own Clip Gain")
    finally:
        line.deleteLater()


def test_open_in_premiere_into_another_project_says_the_sound_is_yours_to_set(tmp_path, monkeypatch):
    xml, proj = _run19(tmp_path)
    other = tmp_path / "yours.prproj"
    monkeypatch.setattr(PR, "find", lambda: Path("premiere.exe"))
    monkeypatch.setattr(PR, "named_copy", lambda x: x)
    monkeypatch.setattr(PR, "saved_own_project", lambda d: None)
    monkeypatch.setattr(PR, "project_for", lambda x: proj)
    monkeypatch.setattr(PR, "state", lambda: PR.State(running=True, title=f"Adobe Premiere Pro 2024 - {other}",
                                                      project=other, pid=1, hwnd=5))
    monkeypatch.setattr(PR, "_import", lambda exe, x, st, p: (st, True))
    monkeypatch.setattr(PR, "_front", lambda hwnd: None)
    said = PR._open(xml, 1.0)                                                  # noqa: SLF001
    assert "imported into yours.prproj" in said
    note = PR.sound_note(xml)
    assert note["state"] == PR.NOT_SET and "went into yours.prproj" in note["text"]
    assert "Audio Gain > Normalize All Peaks to 0 dB" in note["text"]
