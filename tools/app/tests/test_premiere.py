"""Open in Premiere (core/premiere.py, night 11): every way Premiere can be found -- not running, on its Home screen, a
project open (another one, or the run's own) -- with Premiere's process and window title played by a fake, and the
run's own project made from the empty project Premiere Pro 2024 made (assets/premiere/empty_project.prproj)."""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import paths, premiere  # noqa: E402

EXE = Path(r"D:\Adobe\Adobe Premiere Pro 2024\Adobe Premiere Pro.exe")


class FakePremiere:
    """Premiere as the module sees it: state() from what it was handed (a .prproj opens -- unless ``takes`` is False --
    an .xml goes into the open project: its title gets the "*")."""

    def __init__(self, running: bool = False, project: Path | None = None, takes: bool = True,
                 imports: bool = True, message: str | None = None, window: bool = True):
        self.running, self.project, self.takes, self.imports = running, project, takes, imports
        self.message, self.window, self.changed = message, window, False
        self.handed: list[Path] = []

    def state(self) -> premiere.State:
        if not self.running:
            return premiere.State()
        if not self.window:
            return premiere.State(running=True, pid=4242, dialogs=[self.message] if self.message else [])
        title = "Adobe Premiere Pro 2024"
        if self.project is not None:
            title += f" - {self.project}" + (" *" if self.changed else "")
        return premiere.State(running=True, title=title, project=premiere.project_in(title), pid=4242, hwnd=7,
                              dialogs=[self.message] if self.message else [])

    def launch(self, exe: Path, *files: Path) -> None:
        assert exe == EXE
        f = Path(files[0])
        self.handed.append(f)
        if f.suffix == ".prproj":
            self.running = True
            if self.takes:
                self.project, self.changed = f, False
        elif f.suffix == ".xml" and self.project is not None and self.imports:
            self.changed = True


@pytest.fixture
def fake(monkeypatch):
    clock = {"t": 0.0}
    shown: list[Path] = []
    box = {"premiere": FakePremiere()}
    monkeypatch.setattr(premiere, "find", lambda: EXE)
    monkeypatch.setattr(premiere, "state", lambda: box["premiere"].state())
    monkeypatch.setattr(premiere, "_launch", lambda exe, *f: box["premiere"].launch(exe, *f))
    monkeypatch.setattr(premiere, "_show_folder", shown.append)
    monkeypatch.setattr(premiere, "_front", lambda hwnd: None)
    monkeypatch.setattr(premiere, "_clock", lambda: clock["t"])
    monkeypatch.setattr(premiere, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(premiere, "_HANDED", {})

    class Box:
        def use(self, p: FakePremiere) -> FakePremiere:
            box["premiere"] = p
            return p

        @property
        def p(self) -> FakePremiere:
            return box["premiere"]

        shown_folders = shown
        time = clock
    return Box()


def _run(tmp_path: Path, name: str = "video10") -> Path:
    run = tmp_path / name
    run.mkdir(parents=True)
    xml = run / "1_edit.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    os.utime(xml, (1_700_000_000, 1_700_000_000))                 # the run's time: before any project here
    return xml


def _saved_project(path: Path, mtime: float | None = None) -> None:
    """A project as Premiere saves it after the import: gzip XML with a sequence in it."""
    path.write_bytes(gzip.compress(b'<PremiereData><Sequence ObjectUID="075829a3-cd1a-442e">'
                                   b"<Name>Recreated Edit (Premiere)</Name></Sequence></PremiereData>"))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


# ---------------------------------------------------------------------------------------------------------------------
# (2) Premiere not running
# ---------------------------------------------------------------------------------------------------------------------

def test_not_running_premiere_starts_with_the_runs_own_project_then_imports_the_edit(tmp_path, fake):
    xml = _run(tmp_path)
    msg = premiere.open_xml(xml)
    run = xml.parent
    proj, copy = run / "video10_edit.prproj", run / "video10_edit.xml"
    assert proj.read_bytes() == premiere.template().read_bytes()          # Premiere's empty project, byte for byte
    assert fake.p.handed == [proj, copy]                                  # started WITH the project, then the edit
    assert fake.p.project == proj and fake.p.changed
    assert msg == ("Premiere Pro started with video10_edit.prproj (next to the edit) and imported the edit into it "
                   "-- save it (Ctrl+S)")
    assert fake.shown_folders == []


def test_a_projects_edit_gets_the_projects_name(tmp_path, fake):
    proj_dir = tmp_path / "2026-10-18_a-lot-to-catch-up-on"
    (proj_dir / "edit-2").mkdir(parents=True)
    (proj_dir / "project.json").write_text(json.dumps({}), encoding="utf-8")
    xml = proj_dir / "edit-2" / "1_edit.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    premiere.open_xml(xml)
    assert fake.p.handed[0].name == "2026-10-18_a-lot-to-catch-up-on_edit-2.prproj"
    assert fake.p.handed[1].name == "2026-10-18_a-lot-to-catch-up-on_edit-2.xml"


def test_a_saved_project_opens_as_it_is_nothing_imported_twice(tmp_path, fake):
    xml = _run(tmp_path)
    proj = xml.parent / "video10_edit.prproj"
    _saved_project(proj, mtime=1_800_000_000)                             # saved after the run
    msg = premiere.open_xml(xml)
    assert fake.p.handed == [proj]                                        # no XML handed over
    assert msg == "Premiere Pro started with video10_edit.prproj (next to the edit) as you saved it -- the edit is in it already"
    assert premiere.holds_edit(proj)


def test_a_project_never_saved_after_the_import_gets_the_edit_again(tmp_path, fake):
    xml = _run(tmp_path)
    proj = xml.parent / "video10_edit.prproj"
    proj.write_bytes(premiere.template().read_bytes())                    # made here before, closed without saving
    os.utime(proj, (1_800_000_000, 1_800_000_000))
    msg = premiere.open_xml(xml)
    assert fake.p.handed == [proj, xml.parent / "video10_edit.xml"]
    assert "imported the edit into it" in msg and "made again" not in msg


def test_an_edit_made_again_after_the_last_save_is_imported_once_more(tmp_path, fake):
    xml = _run(tmp_path)
    proj = xml.parent / "video10_edit.prproj"
    _saved_project(proj, mtime=1_600_000_000)                             # saved BEFORE the edit was made again
    msg = premiere.open_xml(xml)
    assert fake.p.handed == [proj, xml.parent / "video10_edit.xml"]
    assert "imported the edit made again (one more sequence) into it" in msg


def test_premiere_not_opening_the_project_in_time(tmp_path, fake):
    xml = _run(tmp_path)
    fake.use(FakePremiere(takes=False))
    msg = premiere.open_xml(xml, wait_s=60)
    assert fake.p.handed == [xml.parent / "video10_edit.prproj"]          # the edit never handed to nothing
    assert msg.startswith("Premiere Pro did not open video10_edit.prproj in time")
    assert fake.shown_folders == [xml.parent]
    assert fake.time["t"] >= 60


# ---------------------------------------------------------------------------------------------------------------------
# (3) Premiere on its Home screen
# ---------------------------------------------------------------------------------------------------------------------

def test_home_screen_the_project_is_handed_over_then_the_edit(tmp_path, fake):
    xml = _run(tmp_path)
    fake.use(FakePremiere(running=True))
    msg = premiere.open_xml(xml)
    proj = xml.parent / "video10_edit.prproj"
    assert fake.p.handed == [proj, xml.parent / "video10_edit.xml"]
    assert msg == ("Premiere Pro opened video10_edit.prproj (next to the edit) and imported the edit into it "
                   "-- save it (Ctrl+S)")


def test_home_screen_that_does_not_take_the_project_gets_no_edit_and_a_plain_sentence(tmp_path, fake):
    xml = _run(tmp_path)
    fake.use(FakePremiere(running=True, takes=False))
    msg = premiere.open_xml(xml, wait_s=40)
    assert fake.p.handed == [xml.parent / "video10_edit.prproj"]          # never the XML to the Home screen
    assert msg == premiere.HOME.format(name="video10_edit.prproj")
    assert msg.startswith("Premiere Pro is on its Home screen") and "press Open in Premiere again" in msg
    assert fake.shown_folders == [xml.parent]


def test_a_message_box_ends_the_wait_and_is_named(tmp_path, fake):
    xml = _run(tmp_path)
    fake.use(FakePremiere(running=True, takes=False, message="Open Project"))
    msg = premiere.open_xml(xml, wait_s=600)
    assert msg.startswith("Premiere Pro shows a message ('Open Project') instead of opening video10_edit.prproj")
    assert premiere.DIALOG_S <= fake.time["t"] < 600                      # not the whole wait
    assert len(fake.p.handed) == 1


def test_a_progress_box_is_not_a_message():
    st = premiere.State(running=True, title="Adobe Premiere Pro 2024", dialogs=["Opening project: video10_edit"])
    assert st.message is None
    assert premiere.State(running=True, dialogs=["Import Files", "Open Project"]).message == "Open Project"


# ---------------------------------------------------------------------------------------------------------------------
# (1) a project open in Premiere
# ---------------------------------------------------------------------------------------------------------------------

def test_another_project_open_gets_the_edit(tmp_path, fake):
    xml = _run(tmp_path)
    other = tmp_path / "theirs" / "644.prproj"
    fake.use(FakePremiere(running=True, project=other))
    msg = premiere.open_xml(xml)
    assert fake.p.handed == [xml.parent / "video10_edit.xml"]
    assert not (xml.parent / "video10_edit.prproj").exists()              # nothing made: it went into theirs
    assert msg == "The edit is imported into 644.prproj, the project open in Premiere -- save it there"


def test_the_runs_own_project_open_with_the_edit_from_here_gets_nothing_again(tmp_path, fake):
    xml = _run(tmp_path)
    first = premiere.open_xml(xml)                                        # started, imported (not saved yet)
    assert "imported the edit" in first
    again = premiere.open_xml(xml)                                        # pressed again: its own project is open
    assert fake.p.handed == [xml.parent / "video10_edit.prproj", xml.parent / "video10_edit.xml"]
    assert again == "video10_edit.prproj is open in Premiere already, with the edit"


def test_the_runs_own_project_open_with_the_import_not_saved_gets_nothing_again(tmp_path, fake):
    """The app started again while Premiere still shows the run's project with the import not saved ("*"): the file
    holds no sequence yet and nothing was handed over from this app -- the "*" says the edit is in."""
    xml = _run(tmp_path)
    proj = xml.parent / "video10_edit.prproj"
    proj.write_bytes(premiere.template().read_bytes())
    p = fake.use(FakePremiere(running=True, project=proj))
    p.changed = True
    assert premiere.open_xml(xml) == "video10_edit.prproj is open in Premiere already, with the edit"
    assert fake.p.handed == []


def test_the_runs_own_project_open_without_the_edit_gets_it(tmp_path, fake):
    xml = _run(tmp_path)
    proj = xml.parent / "video10_edit.prproj"
    proj.write_bytes(premiere.template().read_bytes())                    # opened by hand: still empty
    fake.use(FakePremiere(running=True, project=proj))
    msg = premiere.open_xml(xml)
    assert fake.p.handed == [xml.parent / "video10_edit.xml"]
    assert msg == "video10_edit.prproj is open in Premiere: the edit is imported into it -- save it (Ctrl+S)"


def test_the_runs_own_project_open_and_saved_with_the_edit_gets_nothing_again(tmp_path, fake):
    xml = _run(tmp_path)
    proj = xml.parent / "video10_edit.prproj"
    _saved_project(proj, mtime=1_800_000_000)
    fake.use(FakePremiere(running=True, project=proj))
    assert premiere.open_xml(xml) == "video10_edit.prproj is open in Premiere already, with the edit"
    assert fake.p.handed == []


def test_an_import_premiere_does_not_show_is_said(tmp_path, fake):
    xml = _run(tmp_path)
    fake.use(FakePremiere(running=True, project=tmp_path / "x.prproj", imports=False))
    msg = premiere.open_xml(xml)
    assert msg.endswith("-- look at Premiere: it did not show the import yet")
    fake.use(FakePremiere(running=True, project=tmp_path / "x.prproj", imports=False, message="Translation Report"))
    assert premiere.open_xml(xml).endswith("-- Premiere shows a message ('Translation Report'): answer it")


# ---------------------------------------------------------------------------------------------------------------------
# Premiere starting, not there
# ---------------------------------------------------------------------------------------------------------------------

def test_premiere_running_without_a_window(tmp_path, fake):
    xml = _run(tmp_path)
    fake.use(FakePremiere(running=True, window=False))
    msg = premiere.open_xml(xml, wait_s=30)
    assert msg == "Premiere Pro runs but shows no window -- wait for it (or close it), then press Open in Premiere again"
    assert fake.p.handed == []


def test_premiere_starting_is_waited_for(tmp_path, fake, monkeypatch):
    xml = _run(tmp_path)
    p = fake.use(FakePremiere(running=True, window=False))
    real_sleep = premiere._sleep

    def sleep(s):
        real_sleep(s)
        if fake.time["t"] >= 10:
            p.window = True                                               # its Home screen came up
    monkeypatch.setattr(premiere, "_sleep", sleep)
    msg = premiere.open_xml(xml)
    assert msg.startswith("Premiere Pro opened video10_edit.prproj")


def test_premiere_not_installed_opens_the_run_folder(tmp_path, fake, monkeypatch):
    xml = _run(tmp_path)
    monkeypatch.setattr(premiere, "find", lambda: None)
    msg = premiere.open_xml(xml)
    assert msg.startswith("Premiere Pro was not found on this computer") and fake.shown_folders == [xml.parent]


def test_a_folder_the_project_cannot_be_made_in(tmp_path, fake, monkeypatch):
    xml = _run(tmp_path)
    monkeypatch.setattr(premiere, "template", lambda: tmp_path / "nowhere" / "empty_project.prproj")
    msg = premiere.open_xml(xml)
    assert msg.startswith("video10_edit.prproj could not be made next to the edit")
    assert fake.p.handed == [] and fake.shown_folders == [xml.parent]


# ---------------------------------------------------------------------------------------------------------------------
# the pieces
# ---------------------------------------------------------------------------------------------------------------------

def test_the_project_a_window_title_names():
    assert premiere.project_in("Adobe Premiere Pro 2024") is None                       # its Home screen
    assert premiere.project_in(None) is None
    t = r"Adobe Premiere Pro 2024 - C:\Users\gvida\run\video10_edit.prproj *"
    assert premiere.project_in(t) == Path(r"C:\Users\gvida\run\video10_edit.prproj")
    t = r"Adobe Premiere Pro 2024 - D:\AE FILES\a - b\644.prproj"                         # " - " in the path
    assert premiere.project_in(t) == Path(r"D:\AE FILES\a - b\644.prproj")
    t = r"Adobe Premiere Pro 2024 - \\192.168.101.11\Shared\Projects\x\edit\x_edit.prproj"
    assert premiere.project_in(t) == Path(r"\\192.168.101.11\Shared\Projects\x\edit\x_edit.prproj")
    assert premiere.same_file(r"\\?\C:\A\b.PRPROJ", r"c:\a\B.prproj")


def test_holds_edit_and_needs_import(tmp_path):
    empty, saved, junk = tmp_path / "e.prproj", tmp_path / "s.prproj", tmp_path / "j.prproj"
    empty.write_bytes(premiere.template().read_bytes())
    _saved_project(saved, mtime=1_800_000_000)
    junk.write_bytes(b"\x1f\x8b not gzip")
    xml = tmp_path / "x.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    os.utime(xml, (1_700_000_000, 1_700_000_000))
    assert not premiere.holds_edit(empty) and premiere.holds_edit(saved)
    assert premiere.holds_edit(junk)                                      # unreadable: never imported into
    assert premiere.needs_import(empty, xml) and not premiere.needs_import(saved, xml)
    os.utime(xml, (1_900_000_000, 1_900_000_000))
    assert premiere.needs_import(saved, xml)                              # the edit made again after the save


def test_the_template_is_premieres_own_empty_2024_project():
    t = premiere.template()
    assert t == paths.assets_dir() / "premiere" / "empty_project.prproj" and t.is_file()
    raw = t.read_bytes()
    assert raw[:2] == b"\x1f\x8b" and b"\x00" in raw[:8000]               # gzip: git keeps it byte for byte
    text = gzip.decompress(raw).decode("utf-8")
    assert re.search(r"<MZ\.BuildVersion\.Created>24\.0\.0x58 ", text)    # made by Premiere Pro 2024 (24.0.0 build 58)
    assert "<MZ.Project.GUID>" in text
    assert "<Sequence ObjectUID=" not in text and "<ClipProjectItem" not in text and "<BinProjectItem" not in text


def test_state_reads_this_computer():
    st = premiere.state()                                                 # whatever runs here: no exception
    assert isinstance(st, premiere.State) and isinstance(st.running, bool)
    assert premiere.window_up() in (True, False)
    if not st.running:
        assert st.title is None and st.project is None
