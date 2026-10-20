"""Night 11, item 5: the audio chain on every A1 clip (export_xml_edl's audio chain section). What an FCP7 XML can bring
into Premiere Pro 2024 (tested there): your Audio Gain "Normalize All Peaks to 0 dB" (each clip's own gain, measured on
the sound it plays, as Volume stages of at most +15 dB), Volume 0 dB, Panner 0 -- in this order, nothing else but its
speed, no keyframes; the Hard Limiter and Amplify +4.5 dB come from the preset (Premiere cannot import them)."""
from __future__ import annotations

import copy
import math
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import export_xml_edl as ex
from match_cuts import report
from match_cuts.config import Config
from match_cuts.model import Segment

from test_keep_speed import PAN, cutlist

SR = 48000
DUR_S = 30.0


def _media(tmp_path: Path) -> tuple[Path, np.ndarray]:
    """A 30 s stereo WAV whose LEFT channel (what Premiere's mono A1 clip plays) gets louder every second (0.02 x
    (second + 1)), is very quiet from 20 to 26 s (-40 dBFS: a +40 dB normalisation, three Volumes) and silent from 26 s;
    its right channel is loud all along (it must not count). Returns (path, the samples as written: 16 bit)."""
    import soundfile as sf
    t = np.arange(int(SR * DUR_S)) / SR
    amp = 0.02 * (1.0 + np.floor(t))
    amp[(t >= 20.0) & (t < 26.0)] = 0.01
    amp[t >= 26.0] = 0.0
    left = amp * np.sin(2 * np.pi * 440.0 * t)
    right = 0.9 * np.sin(2 * np.pi * 330.0 * t)
    path = tmp_path / "raw.wav"
    sf.write(str(path), np.stack([left, right], 1).astype(np.float32), SR, subtype="PCM_16")
    y, _ = sf.read(str(path), dtype="float32")
    return path, y


def _cutlist(path: Path):
    """test_keep_speed's cut list (S01 / S02 at 125 %, S03 at 100 %) on the WAV, plus S04 in its quiet part and S05 in
    its silence."""
    cl = cutlist(extra=[
        Segment(id=4, type="raw", comp_in=700, comp_out=790, raw_in_seconds=21.0, speed=1.0, flip_h=False,
                confidence=.98, transform=dict(PAN)),
        Segment(id=5, type="raw", comp_in=790, comp_out=850, raw_in_seconds=27.0, speed=1.0, flip_h=False,
                confidence=.98, transform=dict(PAN))], frames=850)
    for seg, raw_s in zip(cl.segments, (2.0, 9.0, 17.0)):
        seg.raw_in_seconds = raw_s
    cl.raw = dict(cl.raw, file=str(path), file_abs=str(path), frames=int(DUR_S * 24000 / 1001))
    return cl


def _write(tmp_path: Path, k: float = 1.0, rate: Fraction | None = None, name: str = "1_edit.xml") -> tuple[Path, dict]:
    path, _ = _media(tmp_path)
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    xml = tmp_path / name
    res = ex.write_premiere_xml(_cutlist(path), xml, cfg, None, speed=k, seq_rate=rate)
    return xml, res


def _played_peak(ci: ET.Element, fps: Fraction, y: np.ndarray) -> float:
    lo, hi = ex.remap_decode(int(ci.findtext("in")), int(ci.findtext("out")), ex._remap_speed(ci))
    s0, s1 = int(math.floor(lo / float(fps) * SR)), int(math.ceil(hi / float(fps) * SR))
    return float(np.abs(y[s0:s1, 0]).max())


@pytest.mark.parametrize("k, rate", [(1.0, None), (1.25, None), (1.0, Fraction(24000, 1001)),
                                     (1.25, Fraction(24000, 1001))])
def test_every_a1_clip_carries_the_chain_with_its_own_normalisation(tmp_path, k, rate):
    xml, res = _write(tmp_path, k, rate)
    _, y = _media(tmp_path)
    root = ET.parse(xml).getroot()
    seq = root.find("sequence")
    fps = ex._xml_rate(seq)
    items = seq.findall("media/audio/track/clipitem")
    assert len(items) >= 5
    split = 0
    for ci in items:
        assert ci.findtext("enabled") == "TRUE" and ci.find(".//keyframe") is None
        chain = ex.audio_chain_of(ci)
        speeds = [x for x in chain if x[0] == "speed"]
        assert len(speeds) == (1 if abs(ex._remap_speed(ci) - 1.0) > 1e-9 else 0)    # its speed stays
        body = [x for x in chain if x[0] != "speed"]
        assert body[-2:] == [("volume", 1.0), ("pan", 0.0)]                          # Volume 0 dB, Panner 0
        stages = [v for kind, v in body[:-2]]
        assert stages and all(kind == "gain" for kind, _ in body[:-2])
        assert all(0 < v <= ex.GAIN_STAGE_MAX for v in stages) and len(set(stages)) == 1
        peak = _played_peak(ci, fps, y)
        want = ex.normalize_gain_db(peak)                                           # 0 dB for the silent S05
        assert sum(ex.factor_db(v) for v in stages) == pytest.approx(want, abs=0.01)
        assert len(stages) == max(1, math.ceil(want / ex.GAIN_STAGE_SPLIT_DB - 1e-9))
        assert want > 5.0 or peak == 0.0                                             # the loud right channel never counts
        split += len(stages) > 1
        note = ci.findtext("comments/mastercomment2")
        assert "apply the preset" in note and "don't use Audio Gain > Normalize again" in note
    assert split >= 1                                                                # S04: +40 dB in three Volumes
    assert ex.premiere_audio_problems(xml) == []
    chain = res["audio_chain"]
    assert len(chain) == len(items) and all(c["read"] for c in chain)
    assert any(c["stages"] == 3 and c["gain_db"] == pytest.approx(40.0, abs=0.05) for c in chain)
    assert any(c["peak_db"] is None and c["gain_db"] == 0.0 for c in chain)           # the silent one


def test_the_base_and_the_final_xml_both_pass_and_validate(tmp_path):
    path, _ = _media(tmp_path)
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    cl = _cutlist(path)
    base, final = tmp_path / "edit_60fps.xml", tmp_path / "1_edit.xml"
    ex.write_premiere_xml(cl, base, cfg)
    ex.write_premiere_xml(cl, final, cfg, None, speed=1.25, seq_rate=Fraction(24000, 1001))
    v = ex.validate_premiere_exports(cl, base, None, cfg)
    assert v["audio_problems"] == []
    assert not [p for p in ex.final_xml_problems(base, final, 1.25, None, cl.raw_fps) if p.startswith("XML AUDIO")]


def test_normalisation_stages():
    assert ex.normalize_stages(13.13) == [round(10 ** (13.13 / 20), 6)]
    two = ex.normalize_stages(18.42)
    assert len(two) == 2 and two[0] == two[1] and sum(ex.factor_db(v) for v in two) == pytest.approx(18.42, abs=1e-4)
    assert len(ex.normalize_stages(59.94)) == 4 and len(ex.normalize_stages(96.0)) == 7
    assert ex.normalize_stages(14.99) == [round(10 ** (14.99 / 20), 6)]               # the most one stage takes
    assert all(v <= ex.GAIN_STAGE_MAX for g in (14.99, 15.0, 29.98, 44.0, 96.0) for v in ex.normalize_stages(g))
    assert ex.normalize_stages(-0.07)[0] < 1.0 and ex.normalize_stages(0.0) == [1.0]
    assert ex.normalize_gain_db(None) == 0.0 and ex.normalize_gain_db(0.0) == 0.0
    assert ex.normalize_gain_db(1e-9) == ex.NORMALIZE_MAX_DB                            # Premiere's +96 dB at most
    assert ex.normalize_gain_db(0.5) == pytest.approx(6.0206, abs=1e-4)


def test_a1_peaks_reads_the_first_channel_over_the_played_source(tmp_path):
    path, y = _media(tmp_path)
    fps = Fraction(60)
    items = [{"in": 120, "out": 300}, {"in": 300, "out": 120}, {"in": 1260, "out": 1500}, {"in": 1600, "out": 1700}]
    got = ex.a1_peaks(str(path), items, fps)
    assert got[0] == pytest.approx(float(np.abs(y[2 * SR:5 * SR, 0]).max()), abs=1e-7)    # 2-5 s: 0.02 x 5
    assert got[1] == got[0]                                                                # either direction
    assert got[2] == pytest.approx(0.01, abs=2e-4) and got[3] == 0.0                       # quiet / silent
    assert ex.a1_peaks(str(path), items, fps, track=2)[0] == pytest.approx(0.9, abs=1e-3)  # the right channel
    assert ex.a1_peaks(str(tmp_path / "missing.wav"), items, fps) == [None] * 4


def test_a_long_clip_is_read_in_pieces(tmp_path, monkeypatch):
    path, y = _media(tmp_path)
    monkeypatch.setattr(ex, "_PEAK_WINDOW_S", 2.0)                  # pieces of 2 s (normally 120 s)
    monkeypatch.setattr(ex, "_AUDIO_WINDOWS", {})
    got = ex.a1_peaks(str(path), [{"in": 30, "out": 1170}, {"in": 1500, "out": 1800}], Fraction(60))
    assert got[0] == pytest.approx(float(np.abs(y[SR // 2:int(19.5 * SR), 0]).max()), abs=1e-7)   # 0.5-19.5 s: 0.40
    assert got[1] == pytest.approx(float(np.abs(y[25 * SR:30 * SR, 0]).max()), abs=1e-7)  # 25-30 s: quiet, silent
    assert got[1] == pytest.approx(0.01, abs=2e-4)
    assert len(ex._AUDIO_WINDOWS) >= 2                              # several decodes, kept for the check


def test_unreadable_sound_keeps_0_db_and_says_so(tmp_path):
    path, _ = _media(tmp_path)
    cl = _cutlist(path)
    cl.raw = dict(cl.raw, file_abs=str(tmp_path / "gone.wav"))
    cfg = Config(out_dir=str(tmp_path), premiere=True)
    xml = tmp_path / "1_edit.xml"
    res = ex.write_premiere_xml(cl, xml, cfg)
    assert any("could not be read" in w for w in res["warnings"])
    assert all(not c["read"] and c["gain_db"] == 0.0 for c in res["audio_chain"])
    for ci in ET.parse(xml).getroot().findall("sequence/media/audio/track/clipitem"):
        assert [x for x in ex.audio_chain_of(ci) if x[0] != "speed"] == [("gain", 1.0), ("volume", 1.0), ("pan", 0.0)]
        assert "could not be read" in ci.findtext("comments/mastercomment2")
    assert ex.premiere_audio_problems(xml) == []                       # nothing to measure against: not judged


# ---------------------------------------------------------------------------------------------------------------------
# the hard check catches every way the chain can go wrong
# ---------------------------------------------------------------------------------------------------------------------

def _broken(tmp_path: Path, change, index: int = 0) -> list[str]:
    xml, _ = _write(tmp_path)
    tree = ET.parse(xml)
    ci = tree.getroot().findall("sequence/media/audio/track/clipitem")[index]
    change(ci)
    bad = tmp_path / "bad.xml"
    tree.write(bad)
    return ex.premiere_audio_problems(bad)


def _filters(ci):
    return ci.findall("filter")


def test_the_check_catches_a_missing_chain(tmp_path):
    def strip(ci):
        for f in _filters(ci):
            if f.find("effect/effectid").text != "timeremap":
                ci.remove(f)
    probs = _broken(tmp_path, strip)
    assert len(probs) == 1 and "its chain is no effect" in probs[0] and "Volume 0 dB" in probs[0]


def test_the_check_catches_a_reordered_chain(tmp_path):
    def swap(ci):                                   # Audio Pan before Audio Levels
        fs = [f for f in _filters(ci) if f.find("effect/effectid").text in ("audiolevels", "audiopan")]
        ci.remove(fs[1])
        ci.insert(list(ci).index(fs[0]), fs[1])
    probs = _broken(tmp_path, swap)
    assert len(probs) == 1 and "audiopan 0, audiolevels +0.00 dB" in probs[0] and "in this order" in probs[0]


def test_the_check_catches_wrong_values(tmp_path):
    def loud(ci):                                   # Volume +6 dB
        ci.find("filter/effect[effectid='audiolevels']/parameter/value").text = "2"
    assert any("Volume +6.02 dB (want 0 dB" in p for p in _broken(tmp_path, loud))

    def panned(ci):
        ci.find("filter/effect[effectid='audiopan']/parameter/value").text = "-0.3"
    assert any("Panner -0.3 (want 0" in p for p in _broken(tmp_path, panned))

    def hung(ci):                                   # Premiere's import HANGS on a pan with parameter id "level"
        ci.find("filter/effect[effectid='audiopan']/parameter/parameterid").text = "level"
    assert any("Panner (no value)" in p for p in _broken(tmp_path, hung))

    def over(ci):                                   # one stage over Premiere's +15 dB
        ci.find(f"filter/effect[effectid='{ex.AUDIO_GAIN_ID}']/parameter/value").text = "6"
    probs = _broken(tmp_path, over)
    assert any("over +15 dB" in p for p in probs)
    assert any("Normalize All Peaks to 0 dB wants" in p for p in probs)          # and not this clip's gain

    def wrong_gain(ci):                             # a stage of the right form, the wrong gain for this clip
        ci.find(f"filter/effect[effectid='{ex.AUDIO_GAIN_ID}']/parameter/value").text = "2"
    probs = _broken(tmp_path, wrong_gain)
    assert len(probs) == 1 and "its Audio Gain is +6.02 dB, but the loudest sample it plays is at" in probs[0]


def test_the_check_catches_unequal_or_needless_stages(tmp_path):
    def two(ci):                                    # one stage split in two unequal ones
        f = next(f for f in _filters(ci) if f.find("effect/effectid").text == ex.AUDIO_GAIN_ID)
        g = copy.deepcopy(f)
        v = float(f.find("effect/parameter/value").text)
        f.find("effect/parameter/value").text = ex._fmt(v / 2)
        g.find("effect/parameter/value").text = "2"
        ci.insert(list(ci).index(f) + 1, g)
    probs = _broken(tmp_path, two, index=2)         # S03: +8 dB, one stage
    assert any("stages differ" in p for p in probs) and any("split into 2 stages" in p for p in probs)


def test_the_check_catches_an_extra_effect_and_a_stand_in(tmp_path):
    def extra(ci):                                  # anything else: Premiere's own effect name / another FCP filter
        f = ET.SubElement(ci, "filter")
        e = ET.SubElement(f, "effect")
        ET.SubElement(e, "effectid").text = "Hard Limiter"
    assert any("Hard Limiter on it" in p for p in _broken(tmp_path, extra))

    def stand_in(ci):                               # a Volume +4.5 dB standing in for Amplify, after the Volume
        f = next(f for f in _filters(ci) if f.find("effect/effectid").text == ex.AUDIO_GAIN_ID)
        g = copy.deepcopy(f)
        g.find("effect/parameter/value").text = "1.678804"
        pan = next(f for f in _filters(ci) if f.find("effect/effectid").text == "audiopan")
        ci.insert(list(ci).index(pan), g)
    probs = _broken(tmp_path, stand_in)
    assert len(probs) == 1 and "its chain is Gain" in probs[0] and "Gain +4.50 dB, audiopan 0" in probs[0]

    def doubled(ci):                                # a second Audio Levels (Premiere keeps only the last one)
        f = next(f for f in _filters(ci) if f.find("effect/effectid").text == "audiolevels")
        ci.insert(list(ci).index(f) + 1, copy.deepcopy(f))
    assert any("audiolevels +0.00 dB, audiolevels +0.00 dB" in p for p in _broken(tmp_path, doubled))


def test_the_check_catches_keyframes_and_a_switched_off_clip(tmp_path):
    def keyed(ci):
        p = ci.find("filter/effect[effectid='audiolevels']/parameter")
        kf = ET.SubElement(p, "keyframe")
        ET.SubElement(kf, "when").text = "0"
        ET.SubElement(kf, "value").text = "0"
    assert any("keyframes on it" in p for p in _broken(tmp_path, keyed))

    def off(ci):
        ci.find("enabled").text = "FALSE"
    assert any("switched off" in p for p in _broken(tmp_path, off))


def test_two_speed_filters_are_one_too_many(tmp_path):
    def twice(ci):
        f = next(f for f in _filters(ci) if f.find("effect/effectid").text == "timeremap")
        ci.insert(list(ci).index(f), copy.deepcopy(f))
    assert any("two speed filters" in p for p in _broken(tmp_path, twice))      # S01 plays at 125 %


def test_media_urls_read_back():
    assert ex._url_path(ex._file_url("C:\\Users\\x\\My Run\\raw.mp4")) == "C:/Users/x/My Run/raw.mp4"
    assert ex._url_path(ex._file_url("\\\\192.168.101.11\\Shared\\a b\\raw.mp4")) == \
        "\\\\192.168.101.11\\Shared\\a b\\raw.mp4"
    assert ex._url_path("") == "" and ex._url_path("http://x/y") == ""


# ---------------------------------------------------------------------------------------------------------------------
# the preset with the two effects an XML cannot carry (made with Premiere's own Save / Export Preset; verified there:
# imported from this file and dropped on all A1 clips of a real run -> Hard Limiter, Amplify after the normalisation)
# ---------------------------------------------------------------------------------------------------------------------

HL_MATCH = "e0b23f05-f1a7-4ef7-9b50-7ec3e3002058"
AMP_MATCH = "f14fee7d-fe5f-4202-b790-f53209e56411"


def test_the_preset_file_holds_exactly_the_hard_limiter_and_amplify():
    import base64
    import re
    path = Path(__file__).resolve().parents[3] / ex.PRESET_FILE.replace("\\", "/")
    assert path.is_file(), path
    root = ET.parse(path).getroot()
    by_id = {e.get("ObjectID"): e for e in root if e.get("ObjectID")}
    items = [e for e in root if e.tag == "TreeItem"]
    assert [e.findtext("TreeItemBase/Name") for e in items] == [ex.PRESET_NAME]
    data = by_id[items[0].find("TreeItemBase/Data").get("ObjectRef")]
    presets = [by_id[f.get("ObjectRef")] for f in data.findall("FilterPresets/FilterPreset")]
    assert {p.findtext("FilterMatchName") for p in presets} == {HL_MATCH, AMP_MATCH}      # nothing else
    states = {}
    for p in presets:                       # the variant saved with values (mono: what Premiere makes of A1)
        comp = by_id[p.find("Component").get("ObjectRef")]
        od = (comp.findtext("OpaqueData") or "").strip()
        floats = [float(x) for x in re.findall(r"<float>([^<]*)</float>", base64.b64decode(od).decode())] if od else []
        if "ParamList" in (base64.b64decode(od).decode() if od else ""):
            states[p.findtext("FilterMatchName")] = floats
    hl, amp = states[HL_MATCH], states[AMP_MATCH]
    # Hard Limiter: Maximum Amplitude 1 (0 dB), Input Boost 0.666667 (0 dB), Look-Ahead 0.133333 (7 ms), Release 0.375
    # (100 ms), Link Channels 1 (1.00), Decay to Ceiling 1, Limit True Peak 0 -- as Premiere's panel showed them
    assert hl[:7] == pytest.approx([1.0, 0.666667, 0.133333, 0.375, 1.0, 1.0, 0.0], abs=1e-6)
    # Amplify: one channel, linked sliders, Gain 0.6875 = (3.0 + 96) / 144: +3.0 dB on its -96 .. +48 dB scale (day 13;
    # night 11's +4.5 dB was 0.697917) -- the preset made again with Premiere's own Save Preset + Export Preset
    assert ex.AMPLIFY_DB == 3.0 and ex.AMPLIFY == "Amplify +3.0 dB"
    assert amp[0] == pytest.approx(0.03125) and amp[2] == pytest.approx((ex.AMPLIFY_DB + 96.0) / 144.0, abs=1e-6)
    descs = {p.findtext("Description") for p in presets}
    assert len(descs) == 1 and "Amplify +3.0 dB" in descs.pop()


# ---------------------------------------------------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------------------------------------------------

def test_the_report_names_the_chain_and_the_preset(tmp_path):
    what = dict((n, w) for n, _, w in report.PREMIERE_CHECKS)["XML AUDIO"]
    assert "Normalize All Peaks to 0 dB" in what and "Volume 0 dB" in what and "Panner 0" in what
    _, res = _write(tmp_path)
    lines = "\n".join(report._audio_chain_lines(SimpleNamespace(premiere_xml=res)))
    assert ex.PRESET_NAME in lines and ex.PRESET_FILE in lines and "LoudVOGOOOD" in lines
    assert "Don't use Audio Gain > Normalize again" in lines and "trim" in lines
    assert "need more than +15 dB" in lines
    assert report._audio_chain_lines(SimpleNamespace(premiere_xml=None)) == []
