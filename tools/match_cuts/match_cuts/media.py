"""Frame-accurate media access (DESIGN.md §2.1).

Never use OpenCV CAP_PROP_POS_FRAMES seeking or duration*fps for anything frame-accurate.
``VideoReader`` decodes sequentially with PyAV and indexes frames by presentation timestamp:

    index = round((pts - origin_pts) * time_base * fps)

where origin_pts is the video stream start_time (0 for every file we analyse, because conform
guarantees it). Seeking is only used to jump to a keyframe *before* the wanted range; frames
are then decoded forward and selected by their PTS-derived index.
"""
from __future__ import annotations

import math
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np

from .common import ffmpeg_bin, log, parse_fps


def _resize(img: np.ndarray, size: tuple[int, int] | None, interp: int | None) -> np.ndarray:
    if size is None or (img.shape[1], img.shape[0]) == tuple(size):
        return img
    import cv2
    return cv2.resize(img, tuple(int(v) for v in size), interpolation=cv2.INTER_AREA if interp is None else interp)


class VideoReader:
    """Sequential, PTS-indexed frame reader.

    fmt: 'bgr24' (H x W x 3 uint8, OpenCV order), 'rgb24', or 'gray' (H x W uint8).
    size: optional (w, h) output size (cv2.resize, INTER_AREA by default -> corner-aligned).
    display: apply rotation side-data (multiples of 90°) and SAR -> square pixels so frames are
             in display orientation. Rotation is applied *before* resizing; ``size`` refers to the
             display-oriented frame.
    """

    def __init__(self, path: str | Path, fps: Fraction | str | None = None, stream_index: int = 0,
                 rotation: int = 0, sar: Fraction | None = None, threads: bool = True):
        import av

        self.path = str(path)
        self._av = av
        self.container = av.open(self.path)
        self.stream = self.container.streams.video[stream_index]
        if threads:
            self.stream.thread_type = "AUTO"
        self.time_base = Fraction(self.stream.time_base.numerator, self.stream.time_base.denominator)
        st = self.stream.start_time
        self.origin_pts = int(st) if st is not None else 0
        if fps is None:
            rate = self.stream.average_rate or self.stream.guessed_rate or self.stream.base_rate
            fps = Fraction(rate.numerator, rate.denominator)
        self.fps = parse_fps(fps)
        self.rotation = int(rotation) % 360
        self.sar = Fraction(sar) if sar else Fraction(1)
        self.width = self.stream.codec_context.width
        self.height = self.stream.codec_context.height

    # -- helpers ---------------------------------------------------------------------------
    def index_of_pts(self, pts: int) -> int:
        return int(math.floor(float((pts - self.origin_pts) * self.time_base * self.fps) + 0.5))

    def _frame_pts(self, frame) -> int | None:
        pts = frame.pts
        if pts is None:
            pts = getattr(frame, "dts", None)
        return pts

    def _convert(self, frame, fmt: str, size, interp) -> np.ndarray:
        img = frame.to_ndarray(format=fmt)
        if self.rotation:
            # self.rotation = CLOCKWISE degrees to apply for display (StreamInfo.rotation, i.e.
            # (-displaymatrix_rotation) % 360 as ffmpeg's autorotate does); np.rot90 k>0 is CCW.
            k = {90: -1, 180: 2, 270: 1}[self.rotation]
            img = np.ascontiguousarray(np.rot90(img, k))
        if self.sar != 1:
            import cv2
            h, w = img.shape[:2]
            if self.sar > 1:
                img = cv2.resize(img, (int(round(w * float(self.sar))), h), interpolation=cv2.INTER_CUBIC)
            else:
                img = cv2.resize(img, (w, int(round(h / float(self.sar)))), interpolation=cv2.INTER_CUBIC)
        return _resize(img, size, interp)

    def _seek_before(self, index: int) -> None:
        if index <= 0:
            self.container.seek(self.origin_pts if self.origin_pts else 0, stream=self.stream,
                                backward=True, any_frame=False)
            return
        # target slightly before the wanted frame start so the keyframe found precedes it
        t = Fraction(index, 1) / self.fps - Fraction(1, 2) / self.fps
        target = self.origin_pts + int(math.floor(t / self.time_base))
        self.container.seek(max(target, 0), stream=self.stream, backward=True, any_frame=False)

    # -- public API ------------------------------------------------------------------------
    def frames(self, start: int = 0, stop: int | None = None, fmt: str = "bgr24",
               size: tuple[int, int] | None = None, interp: int | None = None) -> Iterator[tuple[int, np.ndarray]]:
        """Yield (index, image) for every decoded frame with start <= index < stop, in order."""
        attempt_start = start
        for attempt in range(3):
            self._seek_before(attempt_start)
            first = True
            last = None
            for frame in self.container.decode(self.stream):
                pts = self._frame_pts(frame)
                if pts is None:
                    continue
                idx = self.index_of_pts(pts)
                if first:
                    first = False
                    if idx > start and attempt_start > 0 and attempt < 2:
                        # seek overshot the wanted range -> retry from further back / from 0
                        attempt_start = max(0, attempt_start - int(self.fps * 10)) if attempt == 0 else 0
                        break
                if idx < start:
                    continue
                if stop is not None and idx >= stop:
                    return
                if last is not None and idx <= last:
                    log.warning("%s: non-increasing frame index %d after %d (duplicate PTS?) - skipped",
                                self.path, idx, last)
                    continue
                if last is not None and idx != last + 1:
                    log.warning("%s: frame index gap %d -> %d (VFR or dropped frames)", self.path, last, idx)
                last = idx
                yield idx, self._convert(frame, fmt, size, interp)
            else:
                return
        raise RuntimeError(f"could not seek to frame {start} in {self.path}")

    def get(self, index: int, fmt: str = "bgr24", size=None, interp=None) -> np.ndarray:
        for idx, img in self.frames(index, index + 1, fmt, size, interp):
            if idx == index:
                return img
        raise IndexError(f"frame {index} not found in {self.path}")

    def get_many(self, indices: Sequence[int], fmt: str = "bgr24", size=None, interp=None,
                 gap: int | None = None) -> dict[int, np.ndarray]:
        """Fetch several frames with one sequential pass per cluster of nearby indices."""
        want = sorted(set(int(i) for i in indices))
        out: dict[int, np.ndarray] = {}
        if not want:
            return out
        gap = int(gap if gap is not None else max(30, int(self.fps * 4)))
        runs: list[list[int]] = [[want[0]]]
        for i in want[1:]:
            if i - runs[-1][-1] <= gap:
                runs[-1].append(i)
            else:
                runs.append([i])
        for run in runs:
            wanted = set(run)
            for idx, img in self.frames(run[0], run[-1] + 1, fmt, size, interp):
                if idx in wanted:
                    out[idx] = img
        missing = set(want) - set(out)
        if missing:
            raise IndexError(f"frames {sorted(missing)[:10]} not found in {self.path}")
        return out

    def close(self) -> None:
        try:
            self.container.close()
        except Exception:  # pragma: no cover
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def decode_pts(path: str | Path, stream_index: int = 0) -> tuple[np.ndarray, Fraction, int | None]:
    """Full decode pass returning (pts array int64 in stream time_base, time_base, start_time)."""
    import av

    with av.open(str(path)) as c:
        s = c.streams.video[stream_index]
        s.thread_type = "AUTO"
        tb = Fraction(s.time_base.numerator, s.time_base.denominator)
        pts = []
        for frame in c.decode(s):
            p = frame.pts if frame.pts is not None else getattr(frame, "dts", None)
            if p is not None:
                pts.append(int(p))
        return np.asarray(pts, dtype=np.int64), tb, s.start_time


def extract_audio(path: str | Path, sr: int = 16000, mono: bool = True,
                  offset_s: float = 0.0, stream_index: int = 0) -> np.ndarray:
    """Decode audio with ffmpeg to float32 at ``sr`` Hz. Shape (N,) if mono else (N, C).

    offset_s = audio_start_time - video_start_time (from probe). Positive -> audio starts late:
    zeros are prepended so sample 0 corresponds to video t = 0; negative -> leading samples dropped.
    Returns an empty array if the file has no audio.
    """
    cmd = [ffmpeg_bin(), "-v", "error", "-nostdin", "-i", str(path), "-map", f"0:a:{stream_index}?", "-vn",
           "-f", "f32le", "-acodec", "pcm_f32le", "-ar", str(sr)]
    if mono:
        cmd += ["-ac", "1"]
    cmd += ["-"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"audio extraction failed for {path}: {res.stderr.decode(errors='replace')[-2000:]}")
    y = np.frombuffer(res.stdout, dtype=np.float32)
    if not mono:
        ch = _audio_channels(path, stream_index)
        y = y.reshape(-1, max(1, ch))
    n_off = int(round(offset_s * sr))
    if n_off > 0:
        pad = np.zeros((n_off,) + y.shape[1:], np.float32)
        y = np.concatenate([pad, y])
    elif n_off < 0:
        y = y[-n_off:]
    return np.ascontiguousarray(y)


def _audio_channels(path: str | Path, stream_index: int = 0) -> int:
    import json
    from .common import ffprobe_bin

    res = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", f"a:{stream_index}",
                          "-show_entries", "stream=channels", "-of", "json", str(path)], capture_output=True, text=True)
    try:
        return int(json.loads(res.stdout)["streams"][0]["channels"])
    except Exception:
        return 1


class FFmpegWriter:
    """Write raw BGR frames to an ffmpeg process (H.264 yuv420p, CRF <= 16, +faststart by default)."""

    def __init__(self, path: str | Path, width: int, height: int, fps: Fraction, crf: int = 14,
                 preset: str = "medium", pix_fmt_in: str = "bgr24", extra_out: Sequence[str] = (),
                 codec_args: Sequence[str] | None = None):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        codec = list(codec_args) if codec_args is not None else [
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        cmd = [ffmpeg_bin(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix_fmt_in,
               "-s", f"{width}x{height}", "-r", f"{Fraction(fps).numerator}/{Fraction(fps).denominator}",
               "-i", "-", *codec, *extra_out, self.path]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.size = (width, height)

    def write(self, img: np.ndarray) -> None:
        assert img.shape[1] == self.size[0] and img.shape[0] == self.size[1], (img.shape, self.size)
        self.proc.stdin.write(np.ascontiguousarray(img).tobytes())

    def close(self) -> None:
        self.proc.stdin.close()
        err = self.proc.stderr.read().decode(errors="replace")
        rc = self.proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg writer failed ({rc}) for {self.path}: {err[-3000:]}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
