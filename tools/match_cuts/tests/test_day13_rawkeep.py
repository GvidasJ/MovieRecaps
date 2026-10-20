"""Day 13, keep the original RAW (edit_media.py): a Premiere run's V1 plays the ORIGINAL RAW (hard-linked / copied into
extras/media as raw.<ext>, an .mkv remuxed), A1 the same file's sound or -- 5.1, a sound that starts late, an .mkv --
raw_stereo.wav made with the analysis copy's own audio steps (sample-exact); an original Premiere cannot place frame
for frame (VFR ...) plays a CRF 17 slow copy. 1_edit.xml then names two files of the same RAW and its checks (XML
LINK / SYNC / AUDIO) understand that; the analysis copy goes at the end; Learn and the batch still find the RAW."""
from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from match_cuts import batch, conform, edit_media, learn, pipeline, probe, stereo
from match_cuts import export_xml_edl as ex
from match_cuts.common import ffmpeg_bin
from match_cuts.config import Config
from match_cuts.keep_speed import keep_speed
from match_cuts.model import StreamInfo

from test_keep_speed import cutlist

SR = 48000
MIX_51 = "pan=5.1|FL=0.5*c0|FR=0.4*c0|FC=0.3*c0|LFE=0.2*c0|BL=0.1*c0|BR=0.1*c0"


def _video(path: Path, seconds: float = 3.0, channels: int = 6, delay: float = 0.0, vfr: bool = False,
           vcodec: str = "libx264") -> Path:
    """A small test RAW: 24000/1001 fps testsrc2, pink noise (5.1 with a gain per channel, or stereo), the sound
    ``delay`` s after the picture; ``vfr``: frames 0 / 10 / 20 ms late in turn (a variable frame rate)."""
    mix = MIX_51 if channels == 6 else "pan=stereo|FL=0.5*c0|FR=0.4*c0"
    vf = ["-vf", "setpts=N*1001/24000/TB+0.01*mod(N\\,3)/TB", "-fps_mode", "passthrough"] if vfr else []
    first = path.with_name(path.stem + ".0" + path.suffix) if delay else path
    cmd = [ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24000/1001",
           "-f", "lavfi", "-i", f"anoisesrc=color=pink:sample_rate={SR}:amplitude=0.5:seed=7",
           "-filter_complex", f"[1:a]{mix}[a]", "-map", "0:v", "-map", "[a]", "-t", str(seconds), *vf,
           "-c:v", vcodec, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(first)]
    subprocess.run(cmd, check=True)
    if delay:                          # the sound starts later in the container (an empty edit), as in Borat's RAW
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(first), "-itsoffset", f"{delay}", "-i",
                        str(first), "-map", "0:v", "-map", "1:a", "-c", "copy", str(path)], check=True)
        first.unlink()
    return path


def _prepared(tmp_path: Path, src: Path, codec: str = "h264") -> SimpleNamespace:
    out = tmp_path / "run" / "extras"
    cfg = Config(out_dir=str(out), work_dir=str(tmp_path / "work"), premiere=True)
    cfg.conform_codec = codec
    info = probe.probe(str(src), "raw", str(cfg.work_dir), decode=True)
    conf = conform.conform(info, "raw", cfg)
    analysed = probe.probe(conf.path, "raw", str(cfg.work_dir), decode=True)
    res = edit_media.prepare(info, analysed, conf, cfg, cfg.media_dir)
    return SimpleNamespace(cfg=cfg, info=info, conf=conf, analysed=analysed, res=res, media=Path(cfg.media_dir))


def _pcm(path: Path) -> np.ndarray:
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "2", "-f", "f32le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.float32).reshape(-1, 2)


# ---------------------------------------------------------------------------------------------------------------------
# what plays where (no files)
# ---------------------------------------------------------------------------------------------------------------------

def _info(**kw) -> StreamInfo:
    base = dict(path="x.mp4", role="raw", container="mp4", vcodec="hevc", pix_fmt="yuv420p10le", nb_frames=100,
                fps=Fraction(24000, 1001), display_width=1920, display_height=1040, has_audio=True, a_channels=6)
    base.update(kw)
    return StreamInfo(**base)


def test_the_issues_say_what_premiere_can_take_as_it_is():
    k = edit_media.issue_kind
    assert k("vcodec: hevc is not H.264/ProRes (HEVC import needs AE newer than CC 2019)") == "codec"
    assert k("start_time: audio stream starts at 0.020000 s") == "sound"
    assert k("start_time: audio starts +20.000 ms relative to video frame 0") == "sound"
    assert k("edit_list: audio edit list has 2 entries incl. 1 empty edit(s) (dwell / start offset)") == "sound"
    assert k("acodec: ac3 is not AAC/PCM") == "sound"
    assert k("container: mkv is not mp4/mov") == "container"
    for issue in ("vfr: PTS jitter 2.007 frames at nominal 24000/1001", "start_time: video stream starts at 0.5 s",
                  "edit_list: video edit list hides 3 of 30 stored frames", "rotation: display rotation 90 deg",
                  "sar: non-square pixels (SAR 4/3)", "odd_dims: 1919x1080", "interlaced: field order tt"):
        assert k(issue) == "picture", issue


def test_borat_plays_the_original_and_a_vfr_or_rotated_raw_a_converted_copy():
    borat = ["vcodec: hevc is not H.264/ProRes", "start_time: audio stream starts at 0.020000 s",
             "edit_list: audio edit list has 2 entries incl. 1 empty edit(s)"]
    pc = edit_media.picture_choice
    assert pc(_info(ae_issues=borat), _info(), True)["how"] == "original"
    assert pc(_info(), _info(), False)["how"] == "analysis"                   # an AE-safe RAW: as before
    assert pc(_info(ae_issues=borat + ["vfr: PTS jitter 0.5 frames"]), _info(), True)["how"] == "convert"
    assert pc(_info(ae_issues=["rotation: display rotation 90 deg"]), _info(), True)["how"] == "convert"
    late = ["vcodec: hevc", "start_time: video stream starts at 0.021000 s",
            "edit_list: video edit list has 2 entries incl. 1 empty edit(s) (dwell / start offset)"]
    assert pc(_info(ae_issues=late), _info(), True)["how"] == "remux"           # Family Guy x265: moved to 0
    assert pc(_info(ae_issues=late + ["vfr: PTS jitter 0.5 frames"]), _info(), True)["how"] == "convert"
    assert pc(_info(vcodec="prores", pix_fmt="yuv422p10le", container="mov", ae_issues=late[1:]), _info(),
              True)["how"] == "convert"                                         # ProRes: no .mp4 remux
    mkv = _info(container="mkv", vcodec="h264", pix_fmt="yuv420p", ae_issues=["container: mkv is not mp4/mov"])
    assert pc(mkv, _info(), True)["how"] == "remux"
    webm = _info(container="webm", vcodec="vp9", pix_fmt="yuv420p", ae_issues=["container: webm is not mp4/mov"])
    assert pc(webm, _info(), True)["how"] == "convert"
    assert pc(_info(vcodec="av1", ae_issues=["vcodec: av1"]), _info(), True)["how"] == "convert"
    assert pc(_info(vcodec="h264", pix_fmt="yuv444p", ae_issues=["vcodec: H.264 with pix_fmt yuv444p"]), _info(),
              True)["how"] == "convert"
    other = pc(_info(ae_issues=borat), _info(nb_frames=99), True)              # not frame for frame the analysed copy
    assert other["how"] == "convert" and "frame for frame" in other["why"]


def test_a1_plays_the_file_itself_only_for_plain_stereo_sound():
    sc = edit_media.sound_choice
    assert sc(_info(a_channels=2, ae_issues=[]), "analysis")["how"] == "same"
    assert sc(_info(a_channels=6), "analysis")["how"] == "wav"                       # 5.1
    late = _info(a_channels=2, ae_issues=["start_time: audio stream starts at 0.020000 s"])
    assert sc(late, "original")["how"] == "wav"                                       # the late start on A1's own file
    assert sc(_info(a_channels=2), "remux")["how"] == "wav"                            # the remux has no sound
    assert sc(_info(a_channels=2), "convert")["how"] == "wav"
    assert sc(_info(has_audio=False), "original")["how"] == "none"


# ---------------------------------------------------------------------------------------------------------------------
# the real step on small files
# ---------------------------------------------------------------------------------------------------------------------

def test_a_51_raw_whose_sound_starts_late_plays_the_original_and_a_sample_exact_stereo_wav(tmp_path):
    # (Borat's sound starts 20 ms late; ffmpeg's own muxer folds a shift shorter than AAC's 1024 priming samples into
    # the stream, so the test file's sound starts 100 ms late -- the same empty edit in the container)
    src = _video(tmp_path / "Borat 51 late.mp4", delay=0.1)
    r = _prepared(tmp_path, src)
    assert any("audio" in i for i in r.info.ae_issues) and r.conf.conformed
    pic, snd = r.res["picture"], r.res["sound"]
    assert pic["how"] == "original" and pic["name"] == "raw.mp4" and pic["placed"] == "hardlink"
    assert os.path.samefile(pic["path"], src)                                  # the very file: no extra space
    late = int(round(r.info.av_offset * SR))                                  # the container's start (~78 ms here)
    assert late > 1000 and snd["how"] == "wav" and snd["name"] == "raw_stereo.wav"
    assert f"adelay=delays={late}S" in snd["filter"]                            # the analysis copy's own step
    assert snd["sync"]["ok"] and all(p.get("lag") == 0 for p in snd["sync"]["points"] if "lag" in p)
    info = edit_media.wav_info(snd["path"])
    assert info["codec"] == "pcm_s16le" and info["channels"] == 2 and info["rate"] == SR and info["start"] == 0.0
    w, a = _pcm(Path(snd["path"])), _pcm(Path(r.conf.path))
    assert abs(len(w) - len(a)) <= 1024                                        # as long as the analysed sound
    assert float(np.abs(w[:late - 100]).max()) < 1e-4 < float(np.abs(w[late + 200:late + 1200]).max())  # silence first
    # the mix: left = FL + 0.707 FC, right = FR + 0.707 FC (no surround, no LFE), from the original's decoded channels
    g = 10 ** (float(snd["gain_db"]) / 20)
    r6 = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(src), "-map", "0:a:0", "-f", "f32le", "-"],
                        capture_output=True, check=True)
    six = np.frombuffer(r6.stdout, np.float32).reshape(-1, 6)                 # FL FR FC LFE BL BR
    k0, n = late + 2000, SR // 2
    left, right = (six[2000:2000 + n, 0] + 0.7071 * six[2000:2000 + n, 2]) * g, \
        (six[2000:2000 + n, 1] + 0.7071 * six[2000:2000 + n, 2]) * g
    assert float(np.abs(w[k0:k0 + n, 0] - left).max()) < 2e-4 and float(np.abs(w[k0:k0 + n, 1] - right).max()) < 2e-4
    assert g <= 1.0
    # against the ORIGINAL's own sound on the picture's clock (its sound starts late): lag 0 at three places
    for t in (0.5, 1.5, 2.5):
        x = edit_media.decode_window(src, t - late / SR, 0.4, "pan=stereo|c0=FL+0.7071*FC|c1=FR+0.7071*FC")
        y = edit_media.decode_window(snd["path"], t, 0.4)
        lag, corr = edit_media.best_lag(x, y, 2400)
        assert lag == 0 and corr > 0.99, (t, lag, corr)
    assert "V1 plays the original RAW" in r.res["line"] and "raw_stereo.wav" in r.res["line"]


def test_a_stereo_raw_that_needs_no_conversion_plays_as_before(tmp_path):
    src = _video(tmp_path / "raw in.mp4", channels=2)
    r = _prepared(tmp_path, src)
    assert not r.conf.conformed
    assert r.res["picture"]["how"] == "analysis" and r.res["sound"]["how"] == "same"
    assert not (r.media / "raw_stereo.wav").exists() and not (r.media / "raw_premiere.mp4").exists()


def test_an_mkv_is_remuxed_frame_for_frame_and_its_sound_goes_to_the_wav(tmp_path):
    mp4 = _video(tmp_path / "a.mp4", channels=2)
    mkv = tmp_path / "Episode.mkv"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(mp4), "-c", "copy", str(mkv)], check=True)
    r = _prepared(tmp_path, mkv)
    pic = r.res["picture"]
    assert pic["how"] == "remux" and pic["name"] == "raw.mp4"
    assert edit_media.frames_of(pic["path"]) == r.info.nb_frames == r.analysed.nb_frames
    assert stereo.audio_layout(pic["path"]) == {}                                # the picture only
    assert r.res["sound"]["how"] == "wav" and r.res["sound"]["sync"]["ok"]


def test_a_picture_that_starts_late_is_remuxed_to_start_at_its_first_frame(tmp_path):
    # the x265 web Family Guy RAWs: the video track starts 21 ms late (an empty edit) -- Premiere might place frame k
    # half a frame late; the remux of the same packets starts at frame 0, as the analysis copy does
    a = _video(tmp_path / "a.mp4", channels=6)
    src = tmp_path / "Family Guy S09E11.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-itsoffset", "0.05", "-i", str(a), "-i", str(a),
                    "-map", "0:v", "-map", "1:a", "-c", "copy", str(src)], check=True)
    r = _prepared(tmp_path, src)
    assert any(i.startswith("start_time: video") for i in r.info.ae_issues), r.info.ae_issues
    pic = r.res["picture"]
    assert pic["how"] == "remux" and pic["name"] == "raw.mp4" and pic["placed"] == "remux"
    assert not os.path.samefile(pic["path"], src)                              # a new file: its packets moved to 0
    got = probe.probe(pic["path"], "raw", str(tmp_path / "work"), decode=True)
    assert got.nb_frames == r.info.nb_frames == r.analysed.nb_frames and abs(got.v_start_time) < 1e-3
    assert not [i for i in got.ae_issues if edit_media.issue_kind(i) == "picture"]
    snd = r.res["sound"]
    assert snd["how"] == "wav" and "atrim=start_sample=" in snd["filter"] and snd["sync"]["ok"]
    chk = edit_media.same_pictures(r.analysed.path, pic["path"], int(r.analysed.nb_frames))
    assert chk["ok"], chk["why"]                                               # frame k is the analysed frame k


def test_a_vfr_raw_plays_a_crf_17_slow_copy_and_says_why(tmp_path):
    src = _video(tmp_path / "phone.mp4", channels=2, vfr=True)
    r = _prepared(tmp_path, src)
    assert r.info.vfr
    pic = r.res["picture"]
    assert pic["how"] == "convert" and pic["name"] == edit_media.CONVERTED_NAME and "vfr" in r.res["fallback"]
    assert pic["encoder"] == "H.264 CRF 17, preset slow" and len(pic["check"]) >= 4
    assert edit_media.frames_of(pic["path"]) == r.analysed.nb_frames
    assert stereo.audio_layout(pic["path"]) == {}
    assert r.res["sound"]["how"] == "wav" and r.res["sound"]["sync"]["ok"]
    assert "converted copy" in r.res["line"] and "vfr" in r.res["line"]
    plan = conform.plan_transcode(r.info, "raw", edit_media.delivery_cfg(r.cfg))
    assert "-crf" in plan["vargs"] and plan["vargs"][plan["vargs"].index("-crf") + 1] == "17"
    assert plan["vargs"][plan["vargs"].index("-preset") + 1] == "slow"
    assert r.cfg.conform_h264_crf == 12                                          # the analysis copy's own settings stay


def test_the_sync_check_catches_a_shifted_sound(tmp_path):
    src = _video(tmp_path / "s.mp4", channels=2)
    wav = tmp_path / "shifted.wav"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(src), "-map", "0:a:0", "-af",
                    "adelay=delays=48S:all=1", "-c:a", "pcm_s16le", "-ar", str(SR), str(wav)], check=True)
    chk = edit_media.check_sync(wav, src, "", [0.5, 1.5])
    assert not chk["ok"] and "48 samples" in chk["why"]
    assert edit_media.sync_points(5028.0) == [5.0, 2514.0, 5018.0]
    assert edit_media.sync_points(3.0) == [0.75]


# ---------------------------------------------------------------------------------------------------------------------
# 1_edit.xml with two files of the same RAW, and its checks
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def two_files(tmp_path):
    media = tmp_path / "extras" / "media"
    media.mkdir(parents=True)
    pic = _video(media / "raw.mp4", seconds=2.0)
    wav = media / "raw_stereo.wav"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(pic), "-map", "0:a:0", "-af",
                    "pan=stereo|c0=FL+0.7071*FC|c1=FR+0.7071*FC", "-c:a", "pcm_s16le", str(wav)], check=True)
    kc, _ = keep_speed(cutlist())
    res = {"picture": {"how": "original", "path": str(pic)},
           "sound": {"how": "wav", "path": str(wav)}}
    kc.raw = edit_media.premiere_block(dict(kc.raw, audio_channels=6), res, tmp_path / "extras")
    assert kc.raw["file_abs"] == "/abs/media/raw.mp4"                       # the analysed copy, as the export reads
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, xml, Config(out_dir=str(tmp_path / "extras"), premiere=True))
    return SimpleNamespace(dir=tmp_path, pic=pic, wav=wav, xml=xml, cl=kc)


def test_v1_plays_the_original_and_a1_its_sound_file(two_files):
    root = ET.parse(two_files.xml).getroot()
    defs = {f.get("id"): f for f in root.iter("file") if f.findtext("pathurl")}
    assert set(defs) == {"file-raw", ex.SOUND_FILE_ID}
    assert defs["file-raw"].findtext("pathurl").endswith("/extras/media/raw.mp4")
    assert defs["file-raw"].findtext("media/audio/channelcount") == "6"          # the original's own sound, as it is
    snd = defs[ex.SOUND_FILE_ID]
    assert snd.findtext("pathurl").endswith("/extras/media/raw_stereo.wav") and snd.find("media/video") is None
    assert snd.findtext("media/audio/channelcount") == "2" and snd.findtext("duration") == defs["file-raw"].findtext(
        "duration")
    seq = root.find("sequence")
    v1 = seq.findall("media/video/track")[0].findall("clipitem")
    a1 = seq.findall("media/audio/track")[0].findall("clipitem")
    assert v1 and a1
    assert {c.find("file").get("id") for c in v1} == {"file-raw"}
    assert {c.find("file").get("id") for c in a1} == {ex.SOUND_FILE_ID}
    assert {c.findtext("masterclipid") for c in a1} == {ex.SOUND_MASTERCLIP}
    assert {c.findtext("name") for c in a1} == {"raw_stereo.wav"}
    assert stereo.xml_media_names(two_files.xml) == {"media/raw.mp4", "media/raw_stereo.wav"}


def test_the_hard_checks_pass_on_two_files_of_the_same_raw(two_files):
    assert ex.premiere_link_problems(two_files.xml)[0] == []
    assert ex.premiere_sync_problems(two_files.xml)[0] == []
    assert ex.sound_file_problems(two_files.xml) == []
    assert ex.premiere_audio_problems(two_files.xml) == []                       # the gain measured on the WAV
    assert ex.premiere_item_problems(two_files.xml) == []


def _edit(xml: Path, old: str, new: str, count: int = 1) -> None:
    t = xml.read_text(encoding="utf-8")
    assert old in t
    xml.write_text(t.replace(old, new, count), encoding="utf-8")


def test_the_checks_catch_a_sound_that_is_not_the_raws(two_files):
    _edit(two_files.xml, f'<file id="{ex.SOUND_FILE_ID}" />', '<file id="file-music" />')
    probs = ex.premiere_sync_problems(two_files.xml)[0]
    assert any("neither the RAW nor its sound file" in p for p in probs)


def test_the_checks_catch_a_sound_file_with_a_picture_another_rate_or_missing(two_files):
    root = ET.parse(two_files.xml).getroot()
    snd = next(f for f in root.iter("file") if f.get("id") == ex.SOUND_FILE_ID and f.findtext("pathurl"))
    ET.SubElement(snd.find("media"), "video")
    snd.find("rate/timebase").text = "25"
    snd.find("rate/ntsc").text = "FALSE"
    ET.ElementTree(root).write(two_files.xml, encoding="unicode")
    probs = ex.sound_file_problems(two_files.xml)
    assert any("has a picture" in p for p in probs) and any("drift" in p for p in probs)
    two_files.wav.unlink()
    assert any("is not there" in p for p in ex.sound_file_problems(two_files.xml))


def test_an_xml_of_one_file_has_no_sound_file_problems(tmp_path):
    kc, _ = keep_speed(cutlist())
    xml = tmp_path / "1_edit.xml"
    ex.write_premiere_xml(kc, xml, Config(out_dir=str(tmp_path), premiere=True))
    assert ex.sound_file_problems(xml) == []
    root = ET.parse(xml).getroot()
    assert {f.get("id") for f in root.iter("file")} == {"file-raw"}              # as before day 13


# ---------------------------------------------------------------------------------------------------------------------
# the end of the run, Learn, the batch, the summary
# ---------------------------------------------------------------------------------------------------------------------

def _run_folder(tmp_path: Path) -> SimpleNamespace:
    run = tmp_path / "edit"
    media = run / "extras" / "media"
    media.mkdir(parents=True)
    src = _video(tmp_path / "Borat.mp4", seconds=2.0, delay=0.02)
    pic = media / "raw.mp4"
    os.link(src, pic)
    ae = media / "raw_ae.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-i", str(src), "-c:v", "libx264", "-c:a", "aac", str(ae)],
                   check=True)
    wav = media / "raw_stereo.wav"
    wav.write_bytes(b"RIFF")
    xml = run / "1_edit.xml"
    xml.write_text(f"<xmeml><file id='file-raw'><pathurl>{ex._file_url(str(pic))}</pathurl></file>"
                   f"<file id='{ex.SOUND_FILE_ID}'><pathurl>{ex._file_url(str(wav))}</pathurl></file></xmeml>",
                   encoding="utf-8")
    (run / "extras" / "report.md").write_text("done", encoding="utf-8")
    return SimpleNamespace(run=run, media=media, src=src, pic=pic, ae=ae, wav=wav, xml=xml)


def test_the_analysis_copy_goes_at_the_end_of_a_premiere_run(tmp_path):
    r = _run_folder(tmp_path)
    rec = []
    ctx = SimpleNamespace(cfg=SimpleNamespace(premiere=True, deliver=r.run, out_dir=r.run / "extras", raw=str(r.src)),
                          edit_media={"picture": {"path": str(r.pic)}},
                          raw_conform=SimpleNamespace(path=str(r.ae), conformed=True),
                          dlog=SimpleNamespace(record=lambda *a, **k: rec.append((a, k))))
    res = pipeline.drop_analysis_copy(ctx)
    assert res["removed"] and not r.ae.exists() and r.pic.is_file() and r.src.is_file()
    assert rec and rec[0][0] == ("premiere", "drop_analysis_copy")


def test_the_analysis_copy_stays_when_an_xml_plays_it_or_the_run_is_not_premiere(tmp_path):
    r = _run_folder(tmp_path)
    ctx = SimpleNamespace(cfg=SimpleNamespace(premiere=False, deliver=r.run, out_dir=r.run / "extras", raw=str(r.src)),
                          edit_media={"picture": {"path": str(r.pic)}},
                          raw_conform=SimpleNamespace(path=str(r.ae), conformed=True),
                          dlog=SimpleNamespace(record=lambda *a, **k: None))
    assert pipeline.drop_analysis_copy(ctx) == {} and r.ae.is_file()            # After Effects imports the copy
    ctx.cfg.premiere = True
    other = r.run / "extras" / "edit_59.94fps.xml"
    other.write_text(f"<xmeml><pathurl>{ex._file_url(str(r.ae))}</pathurl></xmeml>", encoding="utf-8")
    res = pipeline.drop_analysis_copy(ctx)
    assert not res["removed"] and "edit_59.94fps.xml" in res["why"] and r.ae.is_file()
    ctx.raw_conform.conformed = False                                            # the analysis copy IS the RAW
    assert pipeline.drop_analysis_copy(ctx) == {}


def test_learn_finds_the_original_raw_once_the_analysis_copy_is_gone(tmp_path):
    r = _run_folder(tmp_path)
    rf = learn.run_files(r.run)
    cl = {"raw": {"file": "media/raw_ae.mp4", "file_abs": str(r.ae), "source_path": str(tmp_path / "gone.mp4"),
                  "duration_s": 2.0}}
    assert learn.run_media(rf, cl, "raw") == r.ae                                # still there: the analysed file
    r.ae.unlink()
    assert learn.run_media(rf, cl, "raw") == r.pic                               # the original V1 plays
    cl["raw"]["duration_s"] = 50.0
    assert learn.run_media(rf, cl, "raw") is None                                # another video: not it


def test_learn_reads_the_cuts_of_a1_from_the_sound_file():
    def it(kind, name, start, end, src):
        return SimpleNamespace(kind=kind, media=f"D:\\x\\edit\\extras\\media\\{name}", enabled=True, track=1,
                               start=start, end=end, src_in=src, speed=1.0, keyframed=False, position=(0.5, 0.5),
                               scale=100.0)
    PR = learn.PR
    seq = SimpleNamespace(items=[it(PR.VIDEO, "raw.mp4", 0.0, 2.0, 10.0), it(PR.AUDIO, "raw_stereo.wav", 0.0, 2.0, 10.0)],
                          hidden=set(), width=1080, height=1920)
    pr = SimpleNamespace(sequences=[seq])
    assert learn.played_raw_name(pr, "raw_ae.mp4") == "raw.mp4"
    assert learn.played_sound_name(pr, "raw.mp4") == "raw_stereo.wav"
    assert learn.played_sound_name(SimpleNamespace(sequences=[SimpleNamespace(items=[
        it(PR.AUDIO, "raw_stereo.mp4", 0, 1, 0)])]), "raw_stereo.mp4") == "raw_stereo.mp4"   # an older run: one file


def test_the_batch_never_hands_on_a_converted_copy_when_the_edit_plays_the_original(tmp_path):
    r = _run_folder(tmp_path)
    assert batch.converted_raw(r.run) is None                                    # 1_edit.xml plays raw.mp4 + the WAV
    old = r.run / "1_edit.xml"
    old.write_text(f"<xmeml><pathurl>{ex._file_url(str(r.ae))}</pathurl></xmeml>", encoding="utf-8")
    assert batch.converted_raw(r.run) == r.ae                                     # a run before day 13


def test_premiere_media_falls_back_to_the_old_way_when_the_step_fails(monkeypatch, tmp_path):
    warned, called = [], []
    ctx = SimpleNamespace(raw_conform=SimpleNamespace(path="x", conformed=True), raw_input=object(), raw_info=object(),
                          cfg=SimpleNamespace(media_dir=tmp_path, out_dir=tmp_path), warn=warned.append,
                          dlog=SimpleNamespace(record=lambda *a, **k: None))

    def boom(*a, **k):
        raise conform.NoSpaceError("not enough free space to copy Borat.mp4")
    monkeypatch.setattr(edit_media, "prepare", boom)
    monkeypatch.setattr(pipeline, "stereo_for_premiere", lambda c, e: called.append(e) or e)
    exl = SimpleNamespace(raw={"file": "media/raw_ae.mp4"})
    assert pipeline.premiere_media(ctx, exl) is exl and called == [exl]
    assert warned and "not enough free space" in warned[0] and "as before day 13" in warned[0]


def test_premiere_media_says_why_a_converted_copy_plays(monkeypatch, tmp_path):
    warned = []
    ctx = SimpleNamespace(raw_conform=SimpleNamespace(path="x", conformed=True), raw_input=object(), raw_info=object(),
                          cfg=SimpleNamespace(media_dir=tmp_path, out_dir=tmp_path), warn=warned.append,
                          dlog=SimpleNamespace(record=lambda *a, **k: None), edit_media={})
    res = {"picture": {"how": "convert", "why": "vfr: PTS jitter 0.5 frames", "path": str(tmp_path / "raw_premiere.mp4"),
                       "name": "raw_premiere.mp4", "encoder": "H.264 CRF 17, preset slow"},
           "sound": {"how": "wav", "why": "V1 plays a copy without sound", "path": str(tmp_path / "raw_stereo.wav"),
                     "name": "raw_stereo.wav"}, "fallback": "vfr: PTS jitter 0.5 frames"}
    res["line"] = edit_media.summary_line(res)
    monkeypatch.setattr(edit_media, "prepare", lambda *a, **k: res)
    exl = SimpleNamespace(raw={"file": "media/raw_ae.mp4", "audio_channels": 2, "frames": 10})
    out = pipeline.premiere_media(ctx, exl)
    assert out.raw["picture_file_abs"].endswith("raw_premiere.mp4")
    assert out.raw["sound_file_abs"].endswith("raw_stereo.wav")
    assert out.raw["picture_audio_channels"] == 0 and exl.raw["file"] == "media/raw_ae.mp4"   # the input untouched
    assert out.raw["file"] == "media/raw_ae.mp4"          # what the export still reads (faces): the analysed copy
    assert ctx.edit_media is res
    assert warned and "CRF 17" in warned[0] and "vfr" in warned[0]
    md = "\n".join(edit_media.describe_for_report(res))
    assert "Why not the original RAW" in md and "CRF 17" in md


def test_the_summary_says_which_raw_files_premiere_plays():
    from match_cuts import cli
    line = "V1 plays the original RAW (extras\\media\\raw.mp4, a hard link: no extra space); A1 plays raw_stereo.wav"
    text = cli.format_summary({"criteria": {}, "checks": {}, "paths": {}, "headline": "PASS",
                               "checklist": {"broll": [], "spots": [], "captions": [], "raw_media": [line]}}, ".")
    assert f"  The RAW in Premiere: {line}" in text
    assert f"  The RAW in Premiere: {line}" in batch.hand_checks(text)
