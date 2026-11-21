"""21 Nov (The Weirdest Gym Goal In Its Always Sunny, the laptop: PICTURE NOT FOUND, 3 % of frames): a 480p DVD RAW
(720x480, SAR 8:9 in the HEVC stream and the mp4) was converted to 720x608 (SAR 64/81) instead of 720x540. The stretch
check read the RAW's frames at their stored shape (1.5 wide, not the 4:3 shown), measured a "stretch" of 8:9 next to a
competitor that shows the picture right, and S2 multiplied it onto the real 8:9. Now the check sees the RAW as shown."""
from __future__ import annotations

import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from match_cuts import stretch as ST

from test_night15_stretch import _pair, _scene  # noqa: E402


def _encode(frames: list[np.ndarray], path: Path, sar: str, codec: list[str]) -> None:
    h, w = frames[0].shape
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r",
                        "24000/1001", "-i", "-", "-vf", f"setsar={sar}", *codec, str(path)],
                       input=b"".join(f.tobytes() for f in frames), capture_output=True)
    assert p.returncode == 0, p.stderr.decode(errors="replace")[-400:]


@pytest.fixture(scope="module")
def dvd(tmp_path_factory):
    """A DVD-shaped HEVC-in-MP4 RAW (720x480 stored, SAR 8:9: the 640x480 picture it shows, 9/8 wider stored) and a
    competitor that shows that picture at its true 4:3 shape (square pixels, 960x720)."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("no ffmpeg")
    import cv2
    d = tmp_path_factory.mktemp("dvd")
    shown = [_scene(seed=k, w=640, h=480) for k in range(6)]          # one picture a second
    per_s = 24
    stored = [cv2.resize(s, (720, 480), interpolation=cv2.INTER_AREA) for s in shown for _ in range(per_s)]
    comp = [cv2.resize(s, (960, 720), interpolation=cv2.INTER_CUBIC) for s in shown for _ in range(per_s)]
    hevc = ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error", "-tag:v", "hvc1"]
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    codec = hevc if "libx265" in probe else ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    _encode(stored, d / "raw_dvd.mp4", "8/9", codec)
    _encode(comp, d / "comp.mp4", "1", ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p"])
    return d


def _check(d: Path, monkeypatch) -> dict:
    monkeypatch.setattr(ST, "RAW_NEIGHBOURS", (0.0,))
    pair = _pair([(float(k), float(k)) for k in range(4)])          # (the middles: k + 1.5 s, the same picture)
    return ST.detect(str(d / "comp.mp4"), str(d / "raw_dvd.mp4"), pair)


def test_the_probe_reads_the_dvd_pixel_aspect(dvd):
    assert ST._sar(str(dvd / "raw_dvd.mp4")) == Fraction(8, 9)
    assert ST._sar(str(dvd / "comp.mp4")) == 1


def test_a_dvd_raw_shown_right_is_not_stretched(dvd, monkeypatch):
    res = _check(dvd, monkeypatch)
    assert res["stretch"] is None, res["why"]
    assert res["frames"] and all(abs(f["ratio"] - 1.0) < 0.03 for f in res["frames"]), res["frames"]


def test_read_at_the_stored_shape_it_was_doubled(dvd, monkeypatch):
    """What the laptop did until today (the frames at their stored shape): 8:9 measured, 64/81 given to S2."""
    ST._sar.cache_clear()
    monkeypatch.setattr(ST, "_sar", lambda path: Fraction(1))
    res = _check(dvd, monkeypatch)
    assert res["stretch"] == Fraction(8, 9), res["why"]
    from match_cuts.probe import StreamInfo
    info = StreamInfo(path="raw.mp4", role="raw")
    info.width, info.height, info.sar, info.rotation = 720, 480, Fraction(8, 9), 0
    out = ST.apply_to(info, res["stretch"])
    assert out.sar == Fraction(64, 81) and (out.display_width, out.display_height) == (720, 608)


def test_a_real_stretch_of_a_dvd_raw_is_still_found(dvd, monkeypatch, tmp_path):
    """A competitor that pulls the DVD's 4:3 picture out to 16:9: still 4:3 measured (now on top of the shown shape)."""
    import cv2
    shown = [_scene(seed=k, w=640, h=480) for k in range(6)]
    wide = [cv2.resize(s, (1280, 720), interpolation=cv2.INTER_CUBIC) for s in shown for _ in range(24)]
    _encode(wide, tmp_path / "comp.mp4", "1", ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p"])
    (tmp_path / "raw_dvd.mp4").write_bytes((dvd / "raw_dvd.mp4").read_bytes())
    res = _check(tmp_path, monkeypatch)
    assert res["stretch"] == Fraction(4, 3), res["why"]


def test_the_check_reads_frames_at_the_shown_shape(dvd):
    img = ST._frame(str(dvd / "raw_dvd.mp4"), 1.0, 960)
    assert img is not None and img.shape == (720, 960)                # 4:3 as shown (stored 1.5: 960x640)
