"""Day 17, task 3: the SceneProof song's real Clip Gain (+5 / -1 dB) and Volume 8.5 dB written into the saved
Premiere project (core.song_gain), and Open in Premiere doing it: before opening a saved project, and after you saved
the imported edit (Premiere closed and opened again). Premiere itself is faked here.

Run: python -m pytest tools/app/tests/test_song_gain_day17.py"""
from __future__ import annotations

import gzip
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import premiere as PR  # noqa: E402
from mr_app.core import song_gain as G  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SONG = "One Piece OST - Mother Sea (First Part) Extended Version.mp3"


def _xml(path: Path) -> Path:
    clips = "".join(
        f'<clipitem id="clipitem-music-{k}"><name>{SONG}</name><start>{s}</start><comments><mastercomment1>MUSIC'
        f"</mastercomment1><mastercomment2>{SONG} from 0.52 s, Volume {g + 8.5:+g} dB (your Clip Gain {g:+g} + "
        f'Volume 8.5 dB) -- drop the preset "CorrectVolume" on it</mastercomment2></comments></clipitem>'
        for k, (s, g) in enumerate([(0, 5.0), (295, -1.0)], 1))
    path.write_text(f"<xmeml><sequence><media><audio><track/><track>{clips}</track></audio></media></sequence>"
                    "</xmeml>", encoding="utf-8")
    return path


def _clip(n: int, start: int, level: float) -> str:
    """One song clip on the timeline as Premiere 2024 saves it (the objects day 16's saved project has)."""
    return f"""
\t<AudioClipTrackItem ObjectID="{n}" ClassID="x" Version="9">
\t\t<ClipTrackItem Version="8">
\t\t\t<ComponentOwner Version="1">
\t\t\t\t<Components ObjectRef="{n + 1}"/>
\t\t\t</ComponentOwner>
\t\t\t<TrackItem Version="3">
\t\t\t\t<Start>{start}</Start>
\t\t\t</TrackItem>
\t\t\t<SubClip ObjectRef="{n + 2}"/>
\t\t</ClipTrackItem>
\t</AudioClipTrackItem>
\t<AudioComponentChain ObjectID="{n + 1}" ClassID="x" Version="3">
\t\t<ComponentChain Version="3">
\t\t\t<Components Version="1">
\t\t\t\t<Component Index="0" ObjectRef="{n + 3}"/>
\t\t\t</Components>
\t\t</ComponentChain>
\t</AudioComponentChain>
\t<SubClip ObjectID="{n + 2}" ClassID="x" Version="5">
\t\t<Clip ObjectRef="{n + 4}"/>
\t</SubClip>
\t<AudioFilterComponent ObjectID="{n + 3}" ClassID="x" Version="4">
\t\t<AudioComponent Version="3">
\t\t\t<Component Version="6">
\t\t\t\t<Params Version="1">
\t\t\t\t\t<Param Index="0" ObjectRef="{n + 5}"/>
\t\t\t\t\t<Param Index="1" ObjectRef="{n + 6}"/>
\t\t\t\t</Params>
\t\t\t\t<DisplayName>Volume</DisplayName>
\t\t\t</Component>
\t\t</AudioComponent>
\t</AudioFilterComponent>
\t<AudioClip ObjectID="{n + 4}" ClassID="x" Version="8">
\t\t<Clip Version="18">
\t\t\t<Source ObjectRef="900"/>
\t\t</Clip>
\t\t<SecondaryContents Version="1">
\t\t\t<SecondaryContentItem Index="0" ObjectRef="1"/>
\t\t</SecondaryContents>
\t\t<AudioChannelLayout>[{{"channellabel":0}}]</AudioChannelLayout>
\t</AudioClip>
\t<AudioComponentParam ObjectID="{n + 5}" ClassID="x" Version="9">
\t\t<Name>Bypass</Name>
\t</AudioComponentParam>
\t<AudioComponentParam ObjectID="{n + 6}" ClassID="x" Version="9">
\t\t<Name>Level</Name>
\t\t<StartKeyframe>-91445760000000000,{level:.12f},0,0,0,0,0,0</StartKeyframe>
\t\t<CurrentValue>{level:.17g}</CurrentValue>
\t</AudioComponentParam>"""


def _project(path: Path) -> Path:
    text = ('<?xml version="1.0" encoding="UTF-8" ?>\n<PremiereData Version="3">'
            + _clip(100, 0, G.level_value(13.5)) + _clip(200, 3125402280000, G.level_value(7.5)) + f"""
\t<AudioMediaSource ObjectID="900" ClassID="x" Version="2">
\t\t<MediaSource Version="4">
\t\t\t<Media ObjectURef="m-1"/>
\t\t</MediaSource>
\t</AudioMediaSource>
\t<Media ObjectUID="m-1" ClassID="x" Version="30">
\t\t<ActualMediaFilePath>C:\\run\\edit\\extras\\media\\{SONG}</ActualMediaFilePath>
\t</Media>
</PremiereData>
""")
    path.write_bytes(gzip.compress(text.encode("utf-8")))
    return path


def test_the_clip_gain_and_volume_8_5_db_go_into_the_saved_project(tmp_path):
    xml, proj = _xml(tmp_path / "edit.xml"), _project(tmp_path / "edit.prproj")
    assert G.pieces(xml) == (SONG, [(0, 5.0, 8.5), (295, -1.0, 8.5)])
    before = G._read(proj)                                                         # noqa: SLF001
    assert G.needs_fix(proj, xml)
    assert G.fix(proj, xml) == f"the song's Clip Gain +5 dB / -1 dB and Volume 8.5 dB written into {proj.name}"
    after = G._read(proj)                                                          # noqa: SLF001
    # your song.prproj's values exactly: Gain 1.7782794237136841 (+5 dB) and 0.89125096797943115 (-1 dB)
    assert "<Gain>1.7782794237136841</Gain>" in after and "<Gain>0.89125096797943115</Gain>" in after
    assert after.count(f"<CurrentValue>{G.level_value(8.5):.17g}</CurrentValue>") == 2
    assert not G.needs_fix(proj, xml) and G.fix(proj, xml) == ""                   # once only
    assert (tmp_path / ("edit" + G.BACKUP)).read_bytes() == gzip.compress(before.encode("utf-8"))
    changed = [ln for ln in after.splitlines() if ln not in before.splitlines()]
    assert len(changed) == 6                                                        # 2 Gains + 2 x (keyframe, value)


def test_the_levels_premiere_stores():
    assert G.level_value(13.5) == pytest.approx(0.84139502, abs=1e-6)           # day 16's saved +13.5 dB
    assert G.level_value(8.5) == pytest.approx(0.47315126, abs=1e-6)
    assert G.gain_value(5) == 1.7782794237136841 and G.gain_value(-1) == 0.89125096797943115


def test_an_edit_without_a_song_is_left_alone(tmp_path):
    xml = tmp_path / "e.xml"
    xml.write_text("<xmeml><sequence/></xmeml>", encoding="utf-8")
    proj = _project(tmp_path / "p.prproj")
    assert not G.needs_fix(proj, xml) and G.fix(proj, xml) == "" and PR._song_later(Path("x.exe"), proj, xml) == ""


class _Fake:
    """Premiere faked: its state() and the closing / opening the watch does."""

    def __init__(self, proj: Path):
        self.proj, self.running, self.changed, self.closed, self.opened = proj, True, True, 0, []

    def state(self):
        if not self.running:
            return PR.State()
        return PR.State(running=True, title=f"Adobe Premiere Pro 2024 - {self.proj}{' *' if self.changed else ''}",
                        project=self.proj, pid=1, hwnd=5)


def test_after_your_save_premiere_closes_the_gain_is_written_and_it_opens_again(tmp_path, monkeypatch):
    xml, proj = _xml(tmp_path / "edit.xml"), _project(tmp_path / "edit.prproj")
    fake = _Fake(proj)
    ticks = {"n": 0}

    def sleep(_s):
        ticks["n"] += 1
        if ticks["n"] == 3:                                   # you press Ctrl+S: saved, the "*" gone
            fake.changed = False
            st = os.stat(proj)
            os.utime(proj, (st.st_atime, st.st_mtime + 5))
    monkeypatch.setattr(PR, "state", fake.state)
    monkeypatch.setattr(PR, "_sleep", sleep)

    def close(st):
        assert G.needs_fix(proj, xml)                         # nothing written while Premiere holds it
        fake.running = False
        fake.closed += 1
        return True
    monkeypatch.setattr(PR, "_close", close)

    def launch(exe, *a):
        fake.opened.append(a)
        fake.running = True
    monkeypatch.setattr(PR, "_launch", launch)
    out = PR.song_watch(Path("premiere.exe"), proj, xml, timeout=1e9)
    assert fake.closed == 1 and fake.opened == [(proj,)] and not G.needs_fix(proj, xml)
    assert "Clip Gain +5 dB / -1 dB" in out and "opened it again" in out


def test_another_project_or_no_save_writes_nothing_now(tmp_path, monkeypatch):
    xml, proj = _xml(tmp_path / "edit.xml"), _project(tmp_path / "edit.prproj")
    other = _Fake(tmp_path / "other.prproj")
    monkeypatch.setattr(PR, "state", other.state)
    monkeypatch.setattr(PR, "_sleep", lambda _s: None)
    monkeypatch.setattr(PR, "_close", lambda st: pytest.fail("never closed for another project"))
    assert "another project" in PR.song_watch(Path("p.exe"), proj, xml, timeout=1e9) and G.needs_fix(proj, xml)
    # Premiere closed by you after saving: written, not opened again
    gone = _Fake(proj)
    gone.running = False
    monkeypatch.setattr(PR, "state", gone.state)
    st = os.stat(proj)
    os.utime(proj, (st.st_atime, st.st_mtime + 5))
    monkeypatch.setattr(PR, "_launch", lambda *a: pytest.fail("not opened again"))
    assert "Clip Gain" in PR.song_watch(Path("p.exe"), proj, xml, timeout=1e9, t_import=0.0)
    assert not G.needs_fix(proj, xml)


def test_a_saved_project_gets_it_before_premiere_opens_it(tmp_path):
    xml, proj = _xml(tmp_path / "edit.xml"), _project(tmp_path / "edit.prproj")
    assert "written into" in PR.song_before_open(proj, xml) and not G.needs_fix(proj, xml)
    assert PR.song_before_open(proj, xml) == ""


T16 = ROOT / "work" / "t16" / "song" / "edit"


@pytest.mark.skipif(not (T16 / "edit_edit.prproj").is_file(), reason="day 16's saved SceneProof project is not here")
def test_day16s_real_saved_project(tmp_path):
    proj = tmp_path / "edit_edit.prproj"
    shutil.copyfile(T16 / "edit_edit.prproj", proj)
    assert G.needs_fix(proj, T16 / "1_edit.xml")
    assert "Clip Gain +5 dB / -1 dB" in G.fix(proj, T16 / "1_edit.xml")
    t = G._read(proj)                                                              # noqa: SLF001
    assert "<Gain>1.7782794237136841</Gain>" in t and "<Gain>0.89125096797943115</Gain>" in t
    assert ROOT.joinpath("assets", "premiere", "CorrectVolume.prfpset").read_bytes() == \
        ROOT.joinpath("docs", "sceneproof", "CorrectVolume.prfpset").read_bytes()
