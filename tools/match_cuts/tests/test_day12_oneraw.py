"""Day 12, one RAW file per run: a 5.1 RAW's run folder keeps ONE RAW file -- raw_stereo.<ext>, the picture copied as
it is (the very same video packets) and one stereo AAC track -- which V1 and A1 of 1_edit.xml play; the analysis copy
(raw.<ext> / raw_ae.<ext>) is removed when the run is done (stereo.one_raw_file, pipeline.one_raw_file). A stereo RAW
stays as it is. Learn still finds the RAW of such a run (learn.run_media, learn.played_raw_name)."""
from __future__ import annotations

import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from match_cuts import export_xml_edl as ex
from match_cuts import learn, pipeline, stereo
from match_cuts.common import ffmpeg_bin, ffprobe_bin
from match_cuts.config import Config
from match_cuts.keep_speed import keep_speed

from test_keep_speed import cutlist


def _video(path: Path, channels: int = 6, seconds: float = 2.0) -> Path:
    layout = {6: "5.1", 2: "stereo"}[channels]
    pan = "pan=5.1|FL=c0|FR=c0|FC=c0|LFE=c0|BL=c0|BR=c0" if channels == 6 else "pan=stereo|FL=c0|FR=c0"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24000/1001",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-filter_complex", f"[1:a]{pan}[a]",
                    "-map", "0:v", "-map", "[a]", "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-channel_layout", layout, str(path)], check=True)
    return path


def _packets(path: Path, sel: str) -> list[str]:
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", sel, "-show_packets", "-show_data_hash", "MD5",
                        "-show_entries", "packet=pts,dts,duration,size,flags,data_hash", "-of", "compact", str(path)],
                       capture_output=True, text=True, check=True)
    return r.stdout.splitlines()


def _start(path: Path, sel: str) -> float:
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", sel, "-show_entries", "stream=start_time",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return float(json.loads(r.stdout)["streams"][0]["start_time"])


@pytest.fixture(scope="module")
def five_one(tmp_path_factory):
    d = tmp_path_factory.mktemp("oneraw")
    src = _video(d / "raw.mp4")
    one = stereo.stereo_copy(src, d / "raw_stereo.mp4")
    return SimpleNamespace(dir=d, src=src, one=Path(one["path"]))


# ---------------------------------------------------------------------------------------------------------------------
# the one file
# ---------------------------------------------------------------------------------------------------------------------

def test_the_one_file_has_the_very_same_video_packets_and_stereo_aac_starting_with_the_picture(five_one):
    a, b = _packets(five_one.src, "v:0"), _packets(five_one.one, "v:0")
    assert a and a == b                                   # pts, dts, duration, size, flags and the bytes (MD5)
    assert stereo.audio_layout(five_one.one) == {"channels": 2, "layout": "stereo"}
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                        "-of", "csv=p=0", str(five_one.one)], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "aac"
    assert _start(five_one.one, "a:0") == pytest.approx(_start(five_one.src, "a:0"), abs=1e-3)
    assert _start(five_one.one, "v:0") == pytest.approx(_start(five_one.src, "v:0"), abs=1e-6)


def test_a_stereo_raw_stays_as_it_is(tmp_path):
    src = _video(tmp_path / "raw.mp4", channels=2)
    media = tmp_path / "media"
    media.mkdir()
    ctx = SimpleNamespace(raw_conform=SimpleNamespace(path=str(src)), cfg=SimpleNamespace(media_dir=media,
                                                                                          out_dir=tmp_path))
    exl = SimpleNamespace(raw={"has_audio": True, "audio_channels": 2, "file": "media/raw.mp4"})
    assert pipeline.stereo_for_premiere(ctx, exl) is exl
    assert list(media.iterdir()) == []                    # no second file
    assert pipeline.one_raw_file(SimpleNamespace(cfg=SimpleNamespace(premiere=True), stereo={},
                                                 raw_conform=SimpleNamespace(path=str(src)))) == {}
    assert src.is_file()


def test_1_edit_xml_plays_the_one_file_on_v1_and_a1(tmp_path, five_one):
    media = tmp_path / "media"
    media.mkdir()
    one = media / "raw_stereo.mp4"
    os.link(five_one.one, one)
    kc, _ = keep_speed(cutlist())
    kc.raw = stereo.stereo_block(dict(kc.raw, audio_channels=6), one, tmp_path)
    assert kc.raw["file_rel"] == "media/raw_stereo.mp4" and kc.raw["audio_channels"] == 2
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, xml, Config(out_dir=str(tmp_path), premiere=True))
    root = ET.parse(xml).getroot()
    urls = {f.get("id"): f.findtext("pathurl") for f in root.iter("file") if f.findtext("pathurl")}
    seq = root.find("sequence")
    for kind in ("video", "audio"):
        track = seq.findall(f"media/{kind}/track")[0]
        items = track.findall("clipitem")
        assert items, kind
        for ci in items:
            fid = ci.find("file").get("id")
            assert urls[fid].endswith("/media/raw_stereo.mp4"), (kind, urls[fid])
    assert stereo.xml_media_names(xml) >= {"media/raw_stereo.mp4"}
    assert "media/raw.mp4" not in stereo.xml_media_names(xml)


# ---------------------------------------------------------------------------------------------------------------------
# the analysis copy goes when the run is done
# ---------------------------------------------------------------------------------------------------------------------

def _run_folder(tmp_path: Path, five_one, link: bool) -> SimpleNamespace:
    run = tmp_path / "edit"
    media = run / "extras" / "media"
    media.mkdir(parents=True)
    analysed = media / "raw.mp4"
    if link:
        os.link(five_one.src, analysed)
    else:
        analysed.write_bytes(five_one.src.read_bytes())
    one = media / "raw_stereo.mp4"
    one.write_bytes(five_one.one.read_bytes())
    xml = run / "1_edit.xml"
    xml.write_text(f"<xmeml><file id='f1'><pathurl>{ex._file_url(str(one))}</pathurl></file></xmeml>",
                   encoding="utf-8")
    return SimpleNamespace(run=run, media=media, analysed=analysed, one=one, xml=xml)


def test_the_analysis_copy_is_removed_when_1_edit_xml_plays_the_one_file(tmp_path, five_one):
    r = _run_folder(tmp_path, five_one, link=False)
    res = stereo.one_raw_file(r.analysed, r.one, [r.xml], five_one.src)
    assert res["removed"] and not r.analysed.exists() and r.one.is_file()
    assert res["freed"] == res["bytes"] > 0


def test_a_hard_link_of_the_raw_is_removed_too_but_frees_nothing(tmp_path, five_one):
    r = _run_folder(tmp_path, five_one, link=True)
    res = stereo.one_raw_file(r.analysed, r.one, [r.xml], five_one.src)
    assert res["removed"] and res["freed"] == 0 and five_one.src.is_file()


def test_the_analysis_copy_stays_when_something_plays_it_or_it_is_the_input(tmp_path, five_one):
    r = _run_folder(tmp_path, five_one, link=False)
    other = r.run / "extras" / "edit_59.94fps.xml"
    other.write_text(f"<xmeml><pathurl>{ex._file_url(str(r.analysed))}</pathurl></xmeml>", encoding="utf-8")
    res = stereo.one_raw_file(r.analysed, r.one, [r.xml, other], five_one.src)
    assert not res["removed"] and "edit_59.94fps.xml" in res["why"] and r.analysed.is_file()
    res = stereo.one_raw_file(r.analysed, r.one, [r.xml], r.analysed)          # a run made from its own media
    assert not res["removed"] and r.analysed.is_file()
    r.one.unlink()
    res = stereo.one_raw_file(r.analysed, r.one, [r.xml], five_one.src)
    assert not res["removed"] and r.analysed.is_file()


def test_the_analysis_copy_stays_when_the_one_file_is_another_video(tmp_path, five_one):
    r = _run_folder(tmp_path, five_one, link=False)
    _video(r.one, channels=2, seconds=1.0)
    res = stereo.one_raw_file(r.analysed, r.one, [r.xml], five_one.src)
    assert not res["removed"] and "frames" in res["why"] and r.analysed.is_file()


def test_the_pipeline_removes_it_at_the_end_of_a_premiere_run_only(tmp_path, five_one):
    r = _run_folder(tmp_path, five_one, link=False)
    rec = []
    ctx = SimpleNamespace(cfg=SimpleNamespace(premiere=False, deliver=r.run, out_dir=r.run / "extras",
                                              raw=str(five_one.src)),
                          stereo={"path": str(r.one)}, raw_conform=SimpleNamespace(path=str(r.analysed)),
                          dlog=SimpleNamespace(record=lambda *a, **k: rec.append((a, k))))
    assert pipeline.one_raw_file(ctx) == {} and r.analysed.is_file()          # After Effects imports the copy
    ctx.cfg.premiere = True
    res = pipeline.one_raw_file(ctx)
    assert res["removed"] and not r.analysed.exists() and rec and rec[0][0] == ("premiere", "one_raw_file")


# ---------------------------------------------------------------------------------------------------------------------
# Learn still finds the RAW
# ---------------------------------------------------------------------------------------------------------------------

def test_learn_finds_the_raw_of_a_run_whose_analysis_copy_was_removed(tmp_path, five_one):
    r = _run_folder(tmp_path, five_one, link=False)
    rf = learn.run_files(r.run)
    assert rf is not None
    cl = {"raw": {"file": "media/raw.mp4", "file_abs": str(r.analysed), "source_path": str(tmp_path / "gone.mp4"),
                  "duration_s": 2.0}}
    assert learn.run_media(rf, cl, "raw") == r.media / "raw.mp4"                # old run: both files
    r.analysed.unlink()
    assert learn.run_media(rf, cl, "raw") == r.one                              # new run: the one file
    cl["raw"]["duration_s"] = 50.0
    assert learn.run_media(rf, cl, "raw") is None                               # another video: not it


def test_learn_takes_the_name_the_project_plays_the_raw_under():
    def pr(*names):
        return SimpleNamespace(sequences=[SimpleNamespace(items=[SimpleNamespace(media=f"D:\\x\\edit\\extras\\media\\{n}")
                                                                 for n in names])])
    assert learn.played_raw_name(pr("raw_stereo.mp4"), "raw_ae.mp4", "raw_ae.mp4") == "raw_stereo.mp4"
    assert learn.played_raw_name(pr("raw_ae.mp4", "raw_stereo.mp4"), "raw_ae.mp4") == "raw_ae.mp4"
    assert learn.played_raw_name(pr("raw.mp4"), "raw.mp4") == "raw.mp4"
    assert learn.played_raw_name(pr("other.mp4"), "raw.mp4") == "raw.mp4"
