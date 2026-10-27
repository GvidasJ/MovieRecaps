"""Run 18, task 1: every A1 voice clip of the saved Premiere project gets its own real Clip Gain (its peak to 0 dB), only
its built-in Volume at 0 dB (the XML's extra Volume effects removed), then the Hard Limiter (Input Boost 2 dB) and
Amplify +3 dB of your CorrectVolume preset (core.a1_chain, through core.song_gain.fix). Premiere is not used here.

Run: python -m pytest tools/app/tests/test_a1_chain_run18.py"""
from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import a1_chain as A  # noqa: E402
from mr_app.core import premiere as PR  # noqa: E402
from mr_app.core import song_gain as G  # noqa: E402


@pytest.fixture(autouse=True)
def _a1_chain_on(monkeypatch):
    """27 Oct: A1's chain is OFF by default (A1 left at 0 dB); these tests check it switched on."""
    monkeypatch.setenv("MOVIERECAPS_A1_CHAIN", "1")


TPF = 8475667200            # ticks per frame at 29.97 fps (254016000000 * 1001 / 30000)
MEDIA = "raw_stereo.wav"


def _xml(path: Path, a1: list[tuple[int, float]]) -> Path:
    clips = "".join(
        f'<clipitem id="clipitem-a{k}"><name>{MEDIA}</name><start>{s}</start><file id="file-a"><name>{MEDIA}</name>'
        f"</file><comments><mastercomment1>S0{k}</mastercomment1><mastercomment2>audio chain: Audio Gain {g:+.2f} dB"
        f" (Normalize All Peaks to 0 dB: its peak {-g:.2f} dBFS; in Premiere a Volume), Volume 0 dB, Panner 0."
        "</mastercomment2></comments></clipitem>" for k, (s, g) in enumerate(a1, 1))
    path.write_text("<xmeml><sequence><rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><media><audio><track>"
                    f"{clips}</track></audio></media></sequence></xmeml>", encoding="utf-8")
    return path


def _param(n: int, name: str, value: float) -> str:
    return f"""
\t<AudioComponentParam ObjectID="{n}" ClassID="x" Version="9">
\t\t<Name>{name}</Name>
\t\t<StartKeyframe>-91445760000000000,{value:.12f},0,0,0,0,0,0</StartKeyframe>
\t\t<CurrentValue>{value:.17g}</CurrentValue>
\t</AudioComponentParam>"""


def _volume(n: int, cid: int, intrinsic: bool, level: float) -> str:
    return f"""
\t<AudioFilterComponent ObjectID="{n}" ClassID="x" Version="4">
\t\t<AudioComponent Version="3">
\t\t\t<Component Version="6">
\t\t\t\t<Params Version="1">
\t\t\t\t\t<Param Index="0" ObjectRef="{n + 1}"/>
\t\t\t\t\t<Param Index="1" ObjectRef="{n + 2}"/>
\t\t\t\t</Params>
\t\t\t\t<ID>{cid}</ID>
\t\t\t\t<DisplayName>Volume</DisplayName>
\t\t\t\t<Intrinsic>{'true' if intrinsic else 'false'}</Intrinsic>
\t\t\t</Component>
\t\t\t<ChannelType>0</ChannelType>
\t\t</AudioComponent>
\t\t<FilterMatchName>Internal Volume Mono</FilterMatchName>
\t</AudioFilterComponent>""" + _param(n + 1, "Bypass", 0) + _param(n + 2, "Level", level)


def _clip(n: int, start_frame: int, extra_db: list[float]) -> tuple[str, str]:
    """One A1 clip as Premiere 2024 saves the XML's import: its built-in Volume 0 dB and one extra Volume per Gain
    filter (Level 1.0 = +15 dB). (the track item's id, the objects)"""
    comps = [(n + 10, 1, True, G.level_value(0))] + [(n + 20 + 10 * i, 2 + i, False, G.level_value(d))
                                                      for i, d in enumerate(extra_db)]
    refs = "".join(f'\n\t\t\t\t<Component Index="{i}" ObjectRef="{c[0]}"/>' for i, c in enumerate(comps))
    text = f"""
\t<AudioClipTrackItem ObjectID="{n}" ClassID="x" Version="9">
\t\t<ClipTrackItem Version="8">
\t\t\t<ComponentOwner Version="1">
\t\t\t\t<Components ObjectRef="{n + 1}"/>
\t\t\t</ComponentOwner>
\t\t\t<TrackItem Version="3">
\t\t\t\t<Start>{start_frame * TPF}</Start>
\t\t\t</TrackItem>
\t\t\t<SubClip ObjectRef="{n + 2}"/>
\t\t</ClipTrackItem>
\t</AudioClipTrackItem>
\t<AudioComponentChain ObjectID="{n + 1}" ClassID="x" Version="3">
\t\t<ComponentChain Version="3">
\t\t\t<Components Version="1">{refs}
\t\t\t</Components>
\t\t</ComponentChain>
\t\t<ChannelType>0</ChannelType>
\t</AudioComponentChain>
\t<SubClip ObjectID="{n + 2}" ClassID="x" Version="5">
\t\t<Clip ObjectRef="{n + 3}"/>
\t</SubClip>
\t<AudioClip ObjectID="{n + 3}" ClassID="x" Version="8">
\t\t<Clip Version="18">
\t\t\t<Source ObjectRef="900"/>
\t\t</Clip>
\t\t<SecondaryContents Version="1">
\t\t\t<SecondaryContentItem Index="0" ObjectRef="1"/>
\t\t</SecondaryContents>
\t\t<AudioChannelLayout>[{{"channellabel":0}}]</AudioChannelLayout>
\t</AudioClip>""" + "".join(_volume(*c) for c in comps)
    return str(n), text


def _project(path: Path, tracks: list[list[tuple[int, list[float]]]]) -> Path:
    body, n = "", 1000
    for t, clips in enumerate(tracks):
        items = []
        for start, extra in clips:
            k, text = _clip(n, start, extra)
            items.append(k)
            body += text
            n += 100
        refs = "".join(f'\n\t\t\t<TrackItem Index="{i}" ObjectRef="{k}"/>' for i, k in enumerate(items))
        body += f"""
\t<AudioClipTrack ObjectUID="track-{t}" ClassID="x" Version="2">
\t\t<TrackItems Version="1">{refs}
\t\t</TrackItems>
\t</AudioClipTrack>"""
    text = ('<?xml version="1.0" encoding="UTF-8" ?>\n<PremiereData Version="3">' + body + f"""
\t<AudioMediaSource ObjectID="900" ClassID="x" Version="2">
\t\t<MediaSource Version="4">
\t\t\t<Media ObjectURef="m-1"/>
\t\t</MediaSource>
\t</AudioMediaSource>
\t<Media ObjectUID="m-1" ClassID="x" Version="30">
\t\t<ActualMediaFilePath>C:\\run\\edit\\extras\\media\\{MEDIA}</ActualMediaFilePath>
\t</Media>
</PremiereData>
""")
    path.write_bytes(gzip.compress(text.encode("utf-8")))
    return path


def _chains(proj: Path) -> list[dict]:
    text = G._read(proj)                                                            # noqa: SLF001
    sp = A.spans(text)
    out = []
    for c in A.clips_of(text, MEDIA):
        comps = A.chain_components(text, c["chain"], sp)
        gain = re.search(r"<Gain>([^<]*)</Gain>", text[sp[c["audioclip"]][0]:sp[c["audioclip"]][1]])
        vals = {comp["name"]: {k: A.param_value(text, sp, p) for k, p in comp["params"].items()} for comp in comps}
        out.append({"names": [comp["name"] for comp in comps], "gain": float(gain.group(1)) if gain else None,
                    "vals": vals})
    return out


A1 = [(0, 12.4), (90, 5.5), (200, 15.2)]


def test_each_a1_clip_gets_its_own_clip_gain_one_volume_then_hard_limiter_and_amplify(tmp_path):
    xml = _xml(tmp_path / "e.xml", A1)
    # as Premiere imports today's XML: one extra Volume per clip, two stacked +7.6 dB for the clip over +15 dB
    proj = _project(tmp_path / "e.prproj", [[(0, [12.4]), (90, [5.5]), (200, [7.6, 7.6])]])
    assert A.a1_pieces(xml)[2] == A1 and G.has_work(xml)
    assert G.needs_fix(proj, xml)
    said = G.fix(proj, xml)
    assert said == ("A1: 3 clips with their own Clip Gain (+5.50 to +15.20 dB), Volume 0 dB (4 extra Volume effects "
                    "removed), Hard Limiter (Input Boost 2 dB) + Amplify +3 dB written into e.prproj")
    chains = _chains(proj)
    for ch, (_s, g) in zip(chains, A1):
        assert ch["gain"] == pytest.approx(10 ** (g / 20), rel=1e-6)                # its own peak to 0 dB
        assert 20 * __import__("math").log10(ch["gain"]) == pytest.approx(g, abs=0.1)
        assert ch["names"] == ["Volume", "Hard Limiter", "Amplify"]                 # exactly one Volume, in this order
        assert ch["vals"]["Volume"]["Level"] == pytest.approx(G.level_value(0), abs=1e-6)      # 0 dB
        assert ch["vals"]["Hard Limiter"]["Input Boost"] == pytest.approx(0.68, abs=1e-6)       # 2.00 dB
        assert ch["vals"]["Hard Limiter"]["Look-Ahead Time"] == pytest.approx(0.133333, abs=1e-5)  # 7 ms
        assert ch["vals"]["Hard Limiter"]["Release Time"] == pytest.approx(0.375)                   # 100 ms
        assert ch["vals"]["Amplify"]["Gain"] == pytest.approx(0.6875)                              # +3.00 dB
    assert not G.needs_fix(proj, xml) and G.fix(proj, xml) == ""                     # once only
    text = G._read(proj)                                                            # noqa: SLF001
    assert text.count("<DisplayName>Volume</DisplayName>") == 3                     # the removed ones are gone
    ids = re.findall(r'ObjectID="(\d+)"', text)
    assert len(ids) == len(set(ids))                                                # every new object its own id
    refs = set(re.findall(r'ObjectRef="(\d+)"', text))
    assert refs <= set(ids) | {"1"}                                                 # nothing points at a removed one


def test_the_effects_are_your_correctvolume_presets_mono_ones():
    hl = A.template(A.HARD_LIMITER_MATCH, 0)[0][1]
    amp = A.template(A.AMPLIFY_MATCH, 0)[0][1]
    assert "<DisplayName>Hard Limiter</DisplayName>" in hl and "<DisplayName>Amplify</DisplayName>" in amp
    assert 'Checksum="2507923162"' in hl and 'Checksum="4263480046"' in amp       # your Premiere-saved values
    yours = A.PRESET.parents[2] / "docs" / "sceneproof" / "CorrectVolume.prfpset"      # (on the PC only: not in git)
    assert not yours.is_file() or A.PRESET.read_bytes() == yours.read_bytes()


def test_an_edit_imported_twice_gets_it_on_both_and_a_project_not_matching_is_left_alone(tmp_path):
    xml = _xml(tmp_path / "e.xml", A1)
    proj = _project(tmp_path / "e.prproj", [[(0, [12.4]), (90, [5.5]), (200, [15.0])]] * 2)
    assert "A1: 6 clips" in G.fix(proj, xml)
    assert all(c["names"] == ["Volume", "Hard Limiter", "Amplify"] for c in _chains(proj))
    other = _project(tmp_path / "o.prproj", [[(0, [12.4]), (95, [5.5]), (200, [15.0])]])  # a clip moved: not the edit
    before = other.read_bytes()
    assert not G.needs_fix(other, xml)
    assert "not written" in G.fix(other, xml) and other.read_bytes() == before


def test_an_effect_already_there_is_not_added_twice(tmp_path):
    xml = _xml(tmp_path / "e.xml", A1)
    proj = _project(tmp_path / "e.prproj", [[(0, [12.4]), (90, [5.5]), (200, [15.0])]])
    G.fix(proj, xml)
    # you set one clip's Clip Gain back by hand: only the gain is written again, no second Hard Limiter / Amplify
    text = G._read(proj).replace(f"<Gain>{G.gain_text(5.5)}</Gain>", "<Gain>1</Gain>", 1)  # noqa: SLF001
    proj.write_bytes(gzip.compress(text.encode("utf-8")))
    assert G.needs_fix(proj, xml)
    G.fix(proj, xml)
    assert [c["names"] for c in _chains(proj)] == [["Volume", "Hard Limiter", "Amplify"]] * 3


def test_open_in_premiere_watches_the_save_for_any_edit_with_a1_clips(tmp_path, monkeypatch):
    xml = _xml(tmp_path / "e.xml", A1)
    proj = _project(tmp_path / "e.prproj", [[(0, [12.4]), (90, [5.5]), (200, [15.0])]])
    started = []
    monkeypatch.setattr(PR.threading, "Thread", lambda target, name, daemon: type(
        "T", (), {"start": lambda self: started.append(name)})())
    words = PR._song_later(Path("p.exe"), proj, xml)                                # noqa: SLF001
    assert started == ["song_gain"] and "A1's Clip Gain, Hard Limiter and Amplify" in words
    assert "song" not in words.replace("song_gain", "")
    PR._SONG_WATCHING.clear()                                                       # noqa: SLF001
