"""pair_check.py: does this competitor come from this RAW at all? A quick look -- seconds, not minutes -- before the long
run (night 11). Until now a wrong pair was found only after the whole visual search (pipeline.stop_if_not_in_raw),
often 20 minutes later.

The sound first (fast, robust): both decoded at 8 kHz mono; every 20 ms the energy of 12 frequency bands (150 Hz -
3.8 kHz), its change over time (the onsets: speech, cuts, effects), normalised per file. Short pieces of the
competitor's sound (3 s, spread over it, the busiest part of each stretch) are each compared with the WHOLE RAW at
every 20 ms step (normalised cross-correlation, one FFT pass over the RAW for all pieces). A piece is found when its
best place clearly stands out (far above the correlation's spread at every other place, and well above its second
best place); a piece that stands out less is weak evidence. Too little at the normal speed: the pieces are tried
again as if sped up 125 % (pitch kept, or pitch up).

The picture when the sound does not find it (no sound, music or a voice over it): 16 frames of the competitor at
low resolution, the part of the frame that moves (the video window under a headline), each with SIFT points, looked
for among the RAW's keyframes (decoded alone -- ``-skip_frame nokey``, at most one a second -- in parallel pieces of
the RAW, in the background while the sound is checked, their points once the sound did not find it; wider the more
the competitor zooms in): the keyframes most of its points point to are checked with a geometric test (one-to-one
matches, one scale + position, no rotation, the matched points spread over the picture), the frame mirrored too. A
frame with fewer agreeing points is weak evidence.

A WRONG PAIR is called only when the evidence clearly says so (``decide``): the picture looked at and not one frame
even weakly found, and the sound failing (nothing clearly found -- pieces only almost found do not count, nor one
piece among 7 or more: another episode of the same show shares its jingle) -- or the picture not there and nothing of
the sound even almost found. Anything in between lets the run go on -- a true pair (mirrored, zoomed, cropped, sped
up, captions and music on top) must never be stopped. Videos too short or too small to judge are not judged. The
answer is cached per pair (paths, sizes, times) in the work folder: a re-run is instant. CPU only (numpy / scipy /
OpenCV / ffmpeg). Measured on 65 real pairs (night 11): every true pair went on, every crossed pair (other movies,
other episodes of the same show) was a WRONG PAIR; a 90-minute RAW takes about 4.5 s for a true pair, 16 s for a wrong
one.
"""
from __future__ import annotations

import concurrent.futures as cf
import dataclasses
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .common import ffmpeg_bin, log

VERSION = 2

# --- sound ---------------------------------------------------------------------------------------------------------
SR = 8000                     # Hz, mono
HOP = 160                     # 20 ms
NFFT = 256                    # 32 ms
FEAT_HZ = SR // HOP           # 50 feature frames a second
BANDS = 12                    # log-spaced bands BAND_LO .. BAND_HI
BAND_LO, BAND_HI = 150.0, 3800.0
WIN_S = 3.0                   # one piece of the competitor's sound
MAX_WINDOWS = 16
MIN_WINDOWS = 4
SPEEDS = ((1.25, "tempo"), (1.25, "tape"))     # tried when the normal speed finds too little
SILENT_RMS = 1e-4             # the competitor's sound quieter than this (-80 dBFS): no sound to compare
WIN_Z, WIN_NCC, WIN_RATIO = 9.0, 0.30, 1.25    # a piece found: its best place this many spreads above the median,
#                                                this correlation, this many times its second best place (+-1 s out)
CLEAR_RATIO, CLEAR_NCC = 1.8, 0.5               # ... or a place this unique with this correlation (the correlation
#                                                of a sound full of similar bursts spreads wide: z stays low)
WEAK_Z, WEAK_NCC, WEAK_RATIO = 5.5, 0.20, 1.25  # weak evidence
# measured (night 11, 65 pairs): the 447 pieces of crossed pairs reached at most a correlation of 0.40, z 7.9, a ratio
# of 1.36; the true pairs' pieces: a median of 0.77, z 13.2, a ratio of 2.84
EXCL_S = 1.0
# --- picture -------------------------------------------------------------------------------------------------------
COMP_FRAMES = 16
COMP_W = 360                  # px: the competitor's frames
RAW_W = 448                   # px: the RAW's keyframes (at least) ...
RAW_W_MAX = 832               # ... wider when the competitor zooms in (its window shows a part of the RAW) ...
RAW_PX_PER_WIN = 300          # ... so that the part it shows is about this many px wide
RAW_MEMORY = 320 << 20        # the keyframes' pixels at most this many bytes (a long RAW: narrower)
KEYFRAME_GAP_S = 1.0          # at most one keyframe a second (an all-keyframe file: ProRes, MJPEG ...)
RAW_FEATURES = 250
COMP_FEATURES = 600
FLANN_TREES, FLANN_CHECKS = 2, 32         # the RAW points' index (each competitor point's 3 nearest vote)
CANDIDATES = 6                # keyframes checked per competitor frame
MIN_INLIERS, MIN_INLIER_SHARE, MIN_SPREAD = 8, 0.25, 0.15      # a frame found ...
WEAK_INLIERS, WEAK_INLIER_SHARE, WEAK_SPREAD = 6, 0.20, 0.10   # ... weak evidence
# measured (night 11, 65 pairs): not one of the 365 frames of crossed pairs had 6 agreeing points with a plausible
# geometry; 77 % of the true pairs' frames had
MAX_ROT_DEG = 10.0
SCALE_RANGE = (0.12, 6.0)     # RAW keyframe px per competitor px
MIN_POINTS = 25               # a competitor frame with fewer SIFT points is not usable
SEGMENT_MIN_S = 60.0          # the RAW's keyframes: parallel pieces of at least this many seconds ...
MAX_SEGMENTS = 16             # ... at most this many
# --- decisions -----------------------------------------------------------------------------------------------------
FOUND, NOT_FOUND, UNCLEAR, ABSENT = "found", "not found", "unclear", "absent"
MIN_SIDE = 96                 # px: a smaller picture (a test pattern, a thumbnail) is not judged
MIN_COMP_S, MIN_RAW_S = 2.0, 5.0     # s: shorter videos are not judged either (too little to be sure)
TIMEOUT_S = 600.0             # ffmpeg on a slow network drive
NO_WINDOW = 0x08000000 if os.name == "nt" else 0              # no console window from the app


@dataclass
class Side:
    """One piece of evidence (the sound or the picture): found / not found / unclear / absent, how much of it."""
    state: str
    tested: int = 0
    found: int = 0
    why: str = ""
    seconds: float = 0.0
    where: list = field(default_factory=list)          # RAW times (s) where the found pieces / frames are
    details: dict = field(default_factory=dict)
    weak: int = 0                                      # pieces / frames with weak evidence (the found ones included)

    @property
    def share(self) -> float | None:
        return self.found / self.tested if self.tested else None

    def text(self, what: str, unit: str, why: bool = True) -> str:
        """'sound: 9 of 12 pieces found (75 %)' (+ ' -- why' when not found)."""
        if self.state == ABSENT:
            return f"{what}: {self.why or 'nothing to compare'}"
        s = f"{what}: {self.found} of {self.tested} {unit} found ({100 * (self.share or 0):.0f} %)"
        return s + (f" -- {self.why}" if why and self.why and self.state != FOUND else "")


@dataclass
class Result:
    """The answer: ``wrong`` (a WRONG PAIR), the evidence, the time it took."""
    wrong: bool
    sound: Side
    picture: Side | None
    seconds: float
    cached: bool = False
    competitor: str = ""
    raw: str = ""

    def summary(self) -> str:
        """The numbers in one line (the run's log)."""
        bits = [self.sound.text("sound", "pieces")]
        bits.append(self.picture.text("picture", "frames") if self.picture is not None else
                    "picture: not needed (the sound decided)")
        return "; ".join(bits) + f" ({self.seconds:.1f} s" + (", from the cache" if self.cached else "") + ")"

    def message(self) -> str:
        """The WRONG PAIR stop, in plain words: what was found, the files, what to do (one line, kept short: the
        batch's summary and the app show it as it is)."""
        comp, raw = Path(self.competitor).name, Path(self.raw).name
        sides = [self.sound.text("sound", "pieces", why=False)]
        if self.picture is not None:
            sides.append(self.picture.text("picture", "frames", why=False))
        return ("WRONG PAIR: This competitor doesn't seem to come from this RAW (" + "; ".join(sides)
                + f"; checked in {self.seconds:.0f} s). Is the RAW the episode or movie it was cut from? Another episode "
                f"of the same show or another version of the movie looks like this too. Competitor: {comp}; RAW: {raw}. "
                "To run it anyway: --force-inputs.")

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["sound"]["share"] = self.sound.share
        if self.picture is not None:
            d["picture"]["share"] = self.picture.share
        return d

    @staticmethod
    def from_dict(d: dict) -> "Result":
        names = {f.name for f in dataclasses.fields(Side)}

        def side(x: dict | None) -> Side | None:
            return Side(**{k: v for k, v in x.items() if k in names}) if x else None
        return Result(bool(d["wrong"]), side(d["sound"]), side(d.get("picture")), float(d["seconds"]),
                      True, str(d.get("competitor", "")), str(d.get("raw", "")))


# =====================================================================================================================
# decoding
# =====================================================================================================================

def _run(cmd: list[str], timeout: float = TIMEOUT_S) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)


AUDIO_PIECE_S = 900.0         # a longer file's sound is decoded in pieces of about this length, all at once


def _audio_piece(path: str | Path, a: float, dur: float | None, timeout: float) -> bytes | None:
    cmd = [ffmpeg_bin(), "-nostdin", "-v", "error"] + (["-ss", f"{a:.3f}"] if a > 0 else []) + ["-i", str(path)]
    cmd += (["-t", f"{dur:.3f}"] if dur else []) + ["-map", "0:a:0?", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SR),
                                                    "-f", "f32le", "pipe:1"]
    r = _run(cmd, timeout)
    return r.stdout if r.returncode == 0 else None


def on_network(path: str | Path) -> bool:
    """A file on a network folder (\\\\server\\share\\..., or a drive letter mapped to one): read once, in one stream
    (Wi-Fi is the limit there, not the processor)."""
    p = os.path.abspath(str(path))
    if p.startswith(("\\\\", "//")):
        return True
    if os.name == "nt" and len(p) >= 2 and p[1] == ":":
        try:
            import ctypes
            return ctypes.windll.kernel32.GetDriveTypeW(p[:2] + "\\") == 4          # DRIVE_REMOTE
        except Exception:  # noqa: BLE001
            return False
    return False


def decode_audio(path: str | Path, duration: float | None = None, timeout: float = TIMEOUT_S) -> np.ndarray | None:
    """The first audio stream as 8 kHz mono float32 (a long file on a local disk in parallel pieces: the decoder alone
    is single threaded); None without one (or when it cannot be read)."""
    n = int(min(8, math.ceil(float(duration or 0.0) / AUDIO_PIECE_S))) if duration and not on_network(path) else 1
    if n <= 1:
        parts = [_audio_piece(path, 0.0, None, timeout)]
    else:
        edges = np.linspace(0.0, float(duration), n + 1)
        with cf.ThreadPoolExecutor(max_workers=n) as ex:
            parts = list(ex.map(lambda k: _audio_piece(path, float(edges[k]), None if k == n - 1 else
                                                      float(edges[k + 1] - edges[k]), timeout), range(n)))
    if any(p is None for p in parts) or not any(parts):
        return None
    y = np.frombuffer(b"".join(parts), np.float32)
    return y if len(y) >= SR else None


def probe(path: str | Path) -> dict:
    from .cli import quick_probe
    return quick_probe(path)


def _gray_frames(out: bytes, w: int, h: int) -> np.ndarray:
    n = len(out) // (w * h)
    return np.frombuffer(out[:n * w * h], np.uint8).reshape(n, h, w)


def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def frame_at(path: str | Path, t: float, w: int, h: int) -> np.ndarray | None:
    """One frame at ``t`` seconds as a ``w`` x ``h`` gray picture (None: none there)."""
    r = _run([ffmpeg_bin(), "-nostdin", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-map", "0:v:0",
              "-frames:v", "1", "-vf", f"scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
             120.0)
    if r.returncode != 0 or len(r.stdout) < w * h:
        return None
    return _gray_frames(r.stdout, w, h)[0]


def competitor_frames(path: str | Path, info: dict) -> tuple[list[float], list[np.ndarray], int, int]:
    """COMP_FRAMES frames spread over the competitor (each seek in its own ffmpeg, all at once): (times, gray frames
    COMP_W wide, width, height)."""
    dc = float(info.get("duration") or 0.0)
    cw, ch = COMP_W, _even(COMP_W * info["height"] / info["width"])
    times = [dc * (0.03 + 0.94 * (i + 0.5) / COMP_FRAMES) for i in range(COMP_FRAMES)]
    with cf.ThreadPoolExecutor(max_workers=COMP_FRAMES) as ex:
        got = list(ex.map(lambda t: frame_at(path, t, cw, ch), times))
    keep = [(t, f) for t, f in zip(times, got) if f is not None]
    return [t for t, _ in keep], [f for _, f in keep], cw, ch


_PTS = re.compile(rb"pts_time:\s*(-?[\d.]+)")


class Keyframes:
    """The RAW's keyframes (decoded alone: ``-skip_frame nokey``, at most one per KEYFRAME_GAP_S) as gray ``w`` x ``h``
    pictures and their times (s), decoded in parallel pieces of the RAW (the decoder alone is single threaded) in the
    background: ``start()``, then ``result()`` -- or ``cancel()`` when they are not needed
    after all (the sound decided)."""

    def __init__(self, path: str | Path, duration: float, w: int, h: int):
        self.path, self.duration, self.w, self.h = str(path), float(duration), int(w), int(h)
        n = int(max(1, min(MAX_SEGMENTS, (os.cpu_count() or 4) // 2, math.ceil(self.duration / SEGMENT_MIN_S))))
        self.edges = np.linspace(0.0, self.duration, n + 1)
        self.procs: list[subprocess.Popen] = []
        self.threads: list[threading.Thread] = []
        self.out: dict[int, tuple[bytes, bytes]] = {}
        self.error = ""

    def _cmd(self, k: int) -> list[str]:
        a, b = float(self.edges[k]), float(self.edges[k + 1])
        cmd = [ffmpeg_bin(), "-nostdin", "-v", "info", "-hide_banner", "-skip_frame", "nokey"]
        cmd += ["-ss", f"{a:.3f}"] if a > 0 else []
        cmd += ["-i", self.path]
        cmd += ["-t", f"{b - a:.3f}"] if k < len(self.edges) - 2 else []
        vf = (f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{KEYFRAME_GAP_S})',"
              f"scale={self.w}:{self.h}:flags=area,showinfo")
        return cmd + ["-map", "0:v:0", "-an", "-sn", "-dn", "-vf", vf, "-fps_mode", "passthrough", "-f", "rawvideo",
                      "-pix_fmt", "gray", "pipe:1"]

    def start(self) -> "Keyframes":
        for k in range(len(self.edges) - 1):
            try:
                p = subprocess.Popen(self._cmd(k), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            except OSError as e:
                self.error = f"ffmpeg could not start: {e}"
                break
            self.procs.append(p)
            th = threading.Thread(target=lambda k=k, p=p: self.out.__setitem__(k, p.communicate()), daemon=True)
            th.start()
            self.threads.append(th)
        return self

    def cancel(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass

    def result(self, timeout: float = TIMEOUT_S) -> tuple[np.ndarray, np.ndarray]:
        t_end = time.time() + timeout
        for th in self.threads:
            th.join(max(0.0, t_end - time.time()))
        if any(th.is_alive() for th in self.threads):
            self.cancel()
            self.error = self.error or "the RAW's keyframes took too long"
        frames, times = [], []
        for k in range(len(self.edges) - 1):
            so, se = self.out.get(k, (b"", b""))
            f = _gray_frames(so or b"", self.w, self.h)
            ts = np.array([float(x) for x in _PTS.findall(se or b"")], np.float64) + float(self.edges[k])
            if len(ts) < len(f):           # no time for some: spread over the piece
                ts = np.linspace(float(self.edges[k]), float(self.edges[k + 1]), max(1, len(f)), endpoint=False)
            if len(f):
                frames.append(f)
                times.append(ts[:len(f)])
        if not frames:
            return np.zeros((0, self.h, self.w), np.uint8), np.zeros(0)
        return np.concatenate(frames), np.concatenate(times)


def keyframes(path: str | Path, duration: float, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Keyframes(...) decoded now: (pictures, times)."""
    return Keyframes(path, duration, w, h).start().result()


def raw_width(raw_info: dict, box: tuple[int, int, int, int]) -> int:
    """How wide the RAW's keyframes are decoded: RAW_W, wider when the competitor's window is narrower than the RAW's
    picture (it shows a part of it, zoomed in: that part about RAW_PX_PER_WIN px wide), narrower for a long RAW
    (RAW_MEMORY)."""
    rw, rh = float(raw_info.get("width") or 16), float(raw_info.get("height") or 9)
    bw, bh = max(1, box[2] - box[0]), max(1, box[3] - box[1])
    zoom = max(1.0, (rw / rh) / (bw / bh))
    w = RAW_W if zoom <= 1.0 else int(16 * round(RAW_PX_PER_WIN * zoom / 16))
    w = int(min(RAW_W_MAX, max(RAW_W, w)))
    n_max = max(1.0, float(raw_info.get("duration") or 0.0) / max(KEYFRAME_GAP_S, 2.5))   # keyframes: every 2.5 s+
    while w > RAW_W and n_max * w * (w * rh / rw) > RAW_MEMORY:
        w -= 16
    return w


# =====================================================================================================================
# the sound
# =====================================================================================================================

def _band_matrix() -> np.ndarray:
    """(NFFT//2+1, BANDS) summing matrix of log-spaced bands (each at least one FFT bin)."""
    freqs = np.arange(NFFT // 2 + 1) * SR / NFFT
    edges = BAND_LO * (BAND_HI / BAND_LO) ** (np.arange(BANDS + 1) / BANDS)
    m = np.zeros((len(freqs), BANDS), np.float32)
    for b in range(BANDS):
        sel = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if not sel.any():
            sel = np.zeros(len(freqs), bool)
            sel[int(np.argmin(np.abs(freqs - 0.5 * (edges[b] + edges[b + 1]))))] = True
        m[sel, b] = 1.0
    return m


def band_energies(y: np.ndarray, chunk: int = 1 << 16) -> np.ndarray:
    """(frames, BANDS) log band energies every 20 ms (frame i centred on i / 50 s)."""
    if len(y) < NFFT:
        return np.zeros((0, BANDS), np.float32)
    pad = np.concatenate([np.zeros(NFFT // 2, np.float32), np.asarray(y, np.float32), np.zeros(NFFT, np.float32)])
    n = 1 + (len(y) - 1) // HOP
    win = np.hanning(NFFT).astype(np.float32)
    import scipy.fft as sfft
    bm = _band_matrix()
    out = np.empty((n, BANDS), np.float32)
    for a in range(0, n, chunk):
        b = min(n, a + chunk)
        idx = np.arange(a, b)[:, None] * HOP + np.arange(NFFT)[None, :]
        spec = sfft.rfft(pad[idx] * win, axis=1, workers=-1)
        p = (spec.real ** 2 + spec.imag ** 2).astype(np.float32)
        out[a:b] = p @ bm
    # a floor 60 dB under the loud parts: digital silence (a file's start, a pause between scenes) is not an onset
    floor = max(1e-12, float(np.percentile(out.max(axis=1), 99)) * 1e-6)
    return np.log10(out + floor)


EDGE = int(0.5 * FEAT_HZ)     # a file's first and last half second: no onset (every file starts with one)


def onset_features(L: np.ndarray) -> np.ndarray:
    """The change of the log band energies over time (+-1 frame), each band normalised over the file; nothing in the
    file's first and last half second."""
    if len(L) < 2 * EDGE + 3:
        return np.zeros((len(L), L.shape[1] if L.ndim == 2 else BANDS), np.float32)
    d = np.zeros_like(L)
    d[1:-1] = L[2:] - L[:-2]
    d[:EDGE] = 0.0
    d[-EDGE:] = 0.0
    inner = d[EDGE:-EDGE]
    sd = inner.std(axis=0)
    sd[sd < 1e-6] = 1.0
    out = (d - inner.mean(axis=0)) / sd
    out[:EDGE] = 0.0
    out[-EDGE:] = 0.0
    return out.astype(np.float32)


class Correlator:
    """The RAW's features ready for many normalised cross-correlations (overlap-save FFT blocks): ``ncc(window)`` is the
    window's correlation at every RAW position."""

    def __init__(self, raw_f: np.ndarray, win: int, block: int = 8192):
        import scipy.fft as sfft
        self.sfft = sfft
        self.win = int(win)
        self.n = len(raw_f)
        self.L = block
        self.H = block - self.win + 1
        self.lags = max(0, self.n - self.win + 1)
        nb = max(1, math.ceil(self.lags / self.H))
        padded = np.zeros((nb * self.H + self.L, raw_f.shape[1]), np.float32)
        padded[:self.n] = raw_f
        blocks = np.stack([padded[k * self.H:k * self.H + self.L] for k in range(nb)])        # (nb, L, B)
        self.R = sfft.rfft(blocks, axis=1, workers=-1)                                        # (nb, L/2+1, B)
        # the RAW's energy around its own mean over each window position (cumulative sums)
        c1 = np.concatenate([np.zeros((1, raw_f.shape[1])), np.cumsum(raw_f, axis=0, dtype=np.float64)])
        c2 = np.concatenate([np.zeros((1, raw_f.shape[1])), np.cumsum(raw_f.astype(np.float64) ** 2, axis=0)])
        s1 = c1[self.win:self.win + self.lags] - c1[:self.lags]
        s2 = c2[self.win:self.win + self.lags] - c2[:self.lags]
        var = (s2 - s1 * s1 / self.win).sum(axis=1)
        self.den = np.sqrt(np.maximum(var, 0.0))

    def ncc(self, w: np.ndarray) -> np.ndarray:
        """Normalised cross-correlation of the window ``w`` (win, B) at every RAW position (lags,)."""
        w = np.asarray(w, np.float32)
        w = w - w.mean(axis=0)
        nw = float(np.sqrt((w.astype(np.float64) ** 2).sum()))
        if nw < 1e-9 or not self.lags:
            return np.zeros(self.lags, np.float32)
        # the correlation is the convolution with the reversed window: lag j of block k sits at index j + win - 1
        W = self.sfft.rfft(w[::-1], n=self.L, axis=0)                                         # (L/2+1, B)
        prod = np.einsum("kfb,fb->kf", self.R, W)
        full = self.sfft.irfft(prod, n=self.L, axis=1, workers=-1)[:, self.win - 1:self.win - 1 + self.H]
        num = full.reshape(-1)[:self.lags]
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(self.den > 1e-6, num / (nw * self.den), 0.0)
        return out.astype(np.float32)


def _peak_stats(c: np.ndarray) -> dict:
    """The best place of a correlation curve and how much it stands out."""
    if not len(c):
        return {"ncc": 0.0, "z": 0.0, "ratio": 0.0, "lag": 0}
    j = int(np.argmax(c))
    p = float(c[j])
    sub = c[::max(1, len(c) // 20000)]                     # the spread from a sample of the places: as good, faster
    med = float(np.median(sub))
    mad = float(np.median(np.abs(sub - med))) * 1.4826 or 1e-6
    ex = int(round(EXCL_S * FEAT_HZ))
    rest = np.concatenate([c[:max(0, j - ex)], c[j + ex + 1:]])
    p2 = float(rest.max()) if len(rest) else 0.0
    return {"ncc": round(p, 4), "z": round((p - med) / mad, 2), "ratio": round(p / p2, 3) if p2 > 1e-6 else 99.0,
            "lag": j, "second": round(p2, 4)}


def window_found(st: dict) -> bool:
    return (st["z"] >= WIN_Z and st["ncc"] >= WIN_NCC and st["ratio"] >= WIN_RATIO) or         (st["ratio"] >= CLEAR_RATIO and st["ncc"] >= CLEAR_NCC)


def window_weak(st: dict) -> bool:
    return st["z"] >= WEAK_Z and st["ncc"] >= WEAK_NCC and st["ratio"] >= WEAK_RATIO


def _windows(feat: np.ndarray, energy: np.ndarray, win: int, k_max: int) -> list[int]:
    """Start frames of up to ``k_max`` pieces: the competitor in equal stretches, in each the busiest piece (the most
    change) that is not silent."""
    n = len(feat)
    if n < win:
        return [0] if n >= win // 2 else []
    k = int(max(MIN_WINDOWS, min(k_max, n // win)))
    act = np.abs(feat).sum(axis=1)
    loud = energy.max(axis=1)
    floor = float(np.percentile(loud, 90)) - 3.0            # log10 units: 30 dB under the loud parts
    cs = np.concatenate([[0.0], np.cumsum(act)])
    score = cs[win:] - cs[:-win]                            # activity of the piece starting at each frame
    lc = np.concatenate([[0.0], np.cumsum(loud > floor)])
    voiced = (lc[win:] - lc[:-win]) / win
    score = np.where(voiced >= 0.3, score, -1.0)
    if len(score) > 2 * EDGE:                               # not in the file's first / last half second
        score[:EDGE] = -1.0
        score[len(score) - EDGE:] = -1.0
    edges = np.linspace(0, len(score), k + 1).astype(int)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        j = a + int(np.argmax(score[a:b]))
        if score[j] > 0 and all(abs(j - o) >= win // 2 for o in out):
            out.append(j)
    return out


def _stretch(feat: np.ndarray, v: float) -> np.ndarray:
    """Features of a sound sped up by ``v`` (pitch kept) brought back to the RAW's timing: ``v`` times as many frames."""
    n = len(feat)
    m = int(round(n * v))
    if n < 2 or m < 2:
        return feat
    x = np.linspace(0, n - 1, m)
    i = np.floor(x).astype(int)
    f = (x - i)[:, None].astype(np.float32)
    i2 = np.minimum(i + 1, n - 1)
    return feat[i] * (1 - f) + feat[i2] * f


def _score(stats: list[dict]) -> tuple[int, int]:
    return sum(x["found"] for x in stats), sum(x["weak"] for x in stats)


def sound_check(comp_y: np.ndarray | None, raw_y: np.ndarray | None) -> Side:
    """Is the competitor's sound in the RAW's sound (module docstring)?"""
    t0 = time.perf_counter()
    if comp_y is None or raw_y is None:
        who = "the competitor has no sound" if comp_y is None else "the RAW has no sound"
        return Side(ABSENT, why=who, seconds=round(time.perf_counter() - t0, 2))
    rms = float(np.sqrt(np.mean(np.square(comp_y[:: 7], dtype=np.float64))))
    if rms < SILENT_RMS:
        return Side(ABSENT, why="the competitor's sound is silent", seconds=round(time.perf_counter() - t0, 2))
    win = int(round(WIN_S * FEAT_HZ))
    raw_L = band_energies(raw_y)
    raw_f = onset_features(raw_L)
    if len(raw_f) < win + 2:
        return Side(ABSENT, why="the RAW's sound is too short", seconds=round(time.perf_counter() - t0, 2))
    corr = Correlator(raw_f, win)
    comp_L = band_energies(comp_y)
    hyps: list[tuple[str, np.ndarray, np.ndarray]] = [("1.00", onset_features(comp_L), comp_L)]
    tried: dict[str, list[dict]] = {}
    best: list[dict] = []
    best_name = "1.00"
    for name, feat, energy in hyps:
        stats = []
        for s in _windows(feat, energy, win, MAX_WINDOWS):
            st = _peak_stats(corr.ncc(feat[s:s + win]))
            st.update(at=round(s / FEAT_HZ, 2), raw_at=round(st["lag"] / FEAT_HZ, 2), found=window_found(st),
                      weak=window_weak(st))
            stats.append(st)
        tried[name] = stats
        if not best or _score(stats) > _score(best):
            best, best_name = stats, name
        if _score(stats)[0] >= 2:
            break
        if name == "1.00":                  # too little at the normal speed: as if sped up 125 %
            for v, how in SPEEDS:
                if how == "tempo":
                    f2, e2 = _stretch(feat, v), _stretch(energy, v)
                else:
                    try:
                        from scipy.signal import resample_poly
                        y2 = resample_poly(comp_y, int(round(100 * v)), 100).astype(np.float32)
                    except Exception:  # noqa: BLE001
                        continue
                    e2 = band_energies(y2)
                    f2 = onset_features(e2)
                hyps.append((f"{v:.2f} {how}", f2, e2))
    n = len(best)
    k, weak = _score(best)
    if n == 0:
        return Side(ABSENT, why="the competitor's sound has no usable piece (silence or a steady noise)",
                    seconds=round(time.perf_counter() - t0, 2), details={"tried": tried})
    if k >= 2 and (k >= 3 or k / n >= 0.15):
        state = FOUND
    elif weak == 0 and n >= 3:
        state = NOT_FOUND
    else:
        state = UNCLEAR
    why = {FOUND: f"found as if sped up {best_name}" if best_name != "1.00" else "",
           NOT_FOUND: "none of it is in the RAW's sound",
           UNCLEAR: f"too little to be sure ({weak} of {n} pieces almost found)"}[state]
    return Side(state, n, k, why, round(time.perf_counter() - t0, 2), sorted(x["raw_at"] for x in best if x["found"]),
                {"speed": best_name, "tried": tried}, weak)


# =====================================================================================================================
# the picture
# =====================================================================================================================

def moving_box(frames: np.ndarray, thr: float = 14.0, share: float = 0.2) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of the part of the competitor's frame that changes between its frames: the video window (a
    headline card and its borders stay still). The whole frame when nothing clear stands out."""
    n, h, w = frames.shape
    if n < 3:
        return 0, 0, w, h
    sd = frames.astype(np.float32).std(axis=0)
    mv = sd > thr
    cols = np.flatnonzero(mv.mean(axis=0) > share)
    rows = np.flatnonzero(mv.mean(axis=1) > share)
    if not len(cols) or not len(rows):
        return 0, 0, w, h
    x0, x1, y0, y1 = int(cols[0]), int(cols[-1]) + 1, int(rows[0]), int(rows[-1]) + 1
    if (x1 - x0) * (y1 - y0) < 0.08 * w * h or (x1 - x0) < 0.3 * w:
        return 0, 0, w, h
    m = 4
    return max(0, x0 - m), max(0, y0 - m), min(w, x1 + m), min(h, y1 + m)


def _sift(n: int):
    import cv2
    return cv2.SIFT_create(nfeatures=n, contrastThreshold=0.03)


def _features(img: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """(points (k, 2) float32, descriptors (k, 128) float32) of a gray picture."""
    import cv2
    kp, des = _sift(n).detectAndCompute(img, None)
    if des is None or not kp:
        return np.zeros((0, 2), np.float32), np.zeros((0, 128), np.float32)
    return cv2.KeyPoint_convert(kp).astype(np.float32).reshape(-1, 2), des.astype(np.float32)


def _verify(qp: np.ndarray, qd: np.ndarray, kp: np.ndarray, kd: np.ndarray, qw: int, qh: int) -> dict:
    """The geometric test of one competitor frame against one RAW keyframe: ratio-tested one-to-one matches, one
    similarity transform (RANSAC), how many points agree, the rotation, the scale, how widely the agreeing points
    spread over the competitor's frame."""
    import cv2
    out = {"inliers": 0, "matches": 0}
    if len(qd) < 4 or len(kd) < 4:
        return out
    mm = cv2.BFMatcher(cv2.NORM_L2).knnMatch(qd, kd, k=2)
    good: dict[int, Any] = {}
    for m in mm:                                  # Lowe's ratio test, then one query point per RAW point (the best)
        if len(m) == 2 and m[0].distance < 0.8 * m[1].distance:
            g = good.get(m[0].trainIdx)
            if g is None or m[0].distance < g.distance:
                good[m[0].trainIdx] = m[0]
    out["matches"] = len(good)
    if len(good) < 6:
        return out
    ms = sorted(good.values(), key=lambda m: (m.queryIdx, m.trainIdx))
    src = np.array([qp[m.queryIdx] for m in ms], np.float32)
    dst = np.array([kp[m.trainIdx] for m in ms], np.float32)
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0, maxIters=2000,
                                         confidence=0.995, refineIters=10)
    if M is None or inl is None:
        return out
    inl = inl.reshape(-1).astype(bool)
    s = float(math.hypot(M[0, 0], M[1, 0]))
    rot = float(math.degrees(math.atan2(M[1, 0], M[0, 0])))
    pts = src[inl]
    spread = (float(np.ptp(pts[:, 0]) / max(1, qw)), float(np.ptp(pts[:, 1]) / max(1, qh))) if len(pts) else (0.0, 0.0)
    out.update(inliers=int(inl.sum()), scale=round(s, 3), rot=round(rot, 1), spread=[round(x, 3) for x in spread])
    return out


def _plausible(v: dict) -> bool:
    return abs(v.get("rot", 99.0)) <= MAX_ROT_DEG and SCALE_RANGE[0] <= v.get("scale", 0.0) <= SCALE_RANGE[1]


def frame_found(v: dict) -> bool:
    sx, sy = v.get("spread") or (0.0, 0.0)
    return (v.get("inliers", 0) >= MIN_INLIERS and v["inliers"] >= MIN_INLIER_SHARE * max(1, v.get("matches", 0))
            and _plausible(v) and sx >= MIN_SPREAD and sy >= MIN_SPREAD)


def frame_weak(v: dict) -> bool:
    sx, sy = v.get("spread") or (0.0, 0.0)
    return (v.get("inliers", 0) >= WEAK_INLIERS and v["inliers"] >= WEAK_INLIER_SHARE * max(1, v.get("matches", 0))
            and _plausible(v) and sx >= WEAK_SPREAD and sy >= WEAK_SPREAD)


def _candidates(flann, owner: np.ndarray, qd: np.ndarray, splits: list[int]) -> list[list[int]]:
    """For each query picture (its descriptors ``qd[splits[i]:splits[i+1]]``): the keyframes its points point to most
    (each point's 3 nearest RAW points vote for their keyframes), best first. One FLANN search for all of them."""
    out: list[list[int]] = []
    if not len(qd):
        return [[] for _ in range(len(splits) - 1)]
    knn = flann.knnMatch(qd, k=3)
    for a, b in zip(splits[:-1], splits[1:]):
        votes: dict[int, float] = {}
        for ms in knn[a:b]:
            seen = set()
            for r, m in enumerate(ms):
                o = int(owner[m.trainIdx])
                if o not in seen:
                    seen.add(o)
                    votes[o] = votes.get(o, 0.0) + 1.0 / (1 + r)
        out.append(sorted(votes, key=lambda o: (-votes[o], o))[:CANDIDATES])
    return out


def _rank(v: dict) -> tuple:
    return v.get("found", False), v.get("weak", False), v.get("inliers", 0)


class RawPoints:
    """The RAW's keyframes' SIFT points and their index (FLANN), made in the background: the keyframes are decoded
    while the sound is checked, their points once ``go()`` says they are needed (the sound did not find the pair) --
    ``cancel()`` when the sound decided; ``result()`` waits for them."""

    def __init__(self, frames_of: Callable[[], tuple[np.ndarray, np.ndarray]], workers: int | None = None,
                 decode_after_go: bool = False):
        self.frames_of = frames_of
        self.decode_after_go = decode_after_go          # a RAW on a network folder: read only when needed
        self.workers = workers or min(16, os.cpu_count() or 4)
        self.cancelled = threading.Event()
        self.started = threading.Event()
        self.th = threading.Thread(target=self._run, daemon=True)
        self.kf = self.kt = None
        self.kfeat: list = []
        self.owner = None
        self.flann = None
        self.times: dict = {}
        self.error = ""

    def start(self) -> "RawPoints":
        self.th.start()
        return self

    def go(self) -> None:
        self.started.set()

    def cancel(self) -> None:
        self.cancelled.set()
        self.started.set()

    def _run(self) -> None:
        import cv2
        t0 = time.perf_counter()
        try:
            if self.decode_after_go:
                self.started.wait()
                if self.cancelled.is_set():
                    return
            self.kf, self.kt = self.frames_of()
            self.times["decode_s"] = round(time.perf_counter() - t0, 2)
            self.started.wait()
            if self.cancelled.is_set():
                return
            feats: list = []
            n0 = cv2.getNumThreads()
            cv2.setNumThreads(1)                                  # one thread per picture, the pictures side by side
            try:
                with cf.ThreadPoolExecutor(max_workers=self.workers) as ex:
                    for a in range(0, len(self.kf), 64):          # in batches: a cancel stops it soon
                        if self.cancelled.is_set():
                            return
                        feats += list(ex.map(lambda im: _features(im, RAW_FEATURES), self.kf[a:a + 64]))
            finally:
                cv2.setNumThreads(n0)
            self.kfeat = feats
            self.times["sift_s"] = round(time.perf_counter() - t0, 2)
            sizes = np.array([len(d) for _, d in feats], np.int64)
            if sizes.sum() < 50 or self.cancelled.is_set():
                return
            self.owner = np.repeat(np.arange(len(feats)), sizes)
            index = np.concatenate([d for _, d in feats if len(d)]).astype(np.float32)
            cv2.setRNGSeed(12345)
            fl = cv2.FlannBasedMatcher({"algorithm": 1, "trees": FLANN_TREES}, {"checks": FLANN_CHECKS})
            fl.add([index])
            fl.train()
            self.flann = fl
            self.times["index_s"] = round(time.perf_counter() - t0, 2)
        except Exception as e:  # noqa: BLE001 - the picture check says so
            self.error = f"{type(e).__name__}: {e}"

    def result(self, timeout: float = TIMEOUT_S) -> "RawPoints":
        self.th.join(timeout)
        return self


def picture_check(times: list[float], frames: list[np.ndarray], box: tuple[int, int, int, int],
                  raw: RawPoints, extra: dict | None = None) -> Side:
    """Are the competitor's frames (``frames`` at ``times``; its moving part ``box``) among the RAW's keyframes (their
    points ``raw``) (module docstring)?"""
    import cv2
    t0 = time.perf_counter()
    kf, kt, kfeat, owner, flann = raw.kf, raw.kt, raw.kfeat, raw.owner, raw.flann
    det = dict(extra or {})
    det.update(box=list(box), keyframes=int(len(kf)) if kf is not None else 0,
               keyframe_w=int(kf.shape[2]) if kf is not None and len(kf) else 0, **raw.times)
    if raw.error:
        det["raw_points_error"] = raw.error
    if len(frames) < 3:
        return Side(ABSENT, why="the competitor's frames cannot be read", details=det)
    if kf is None or not len(kf):
        return Side(ABSENT, why="the RAW's keyframes cannot be read", details=det)
    if flann is None:
        return Side(ABSENT, why="the RAW's keyframes have no detail to compare", details=det)
    x0, y0, x1, y1 = box
    n0 = cv2.getNumThreads()
    cv2.setNumThreads(1)
    try:
        workers = min(16, os.cpu_count() or 4)
        crops = [np.ascontiguousarray(f[y0:y1, x0:x1]) for f in frames]
        res: list[dict] = [{"at": round(t, 2), "inliers": 0, "usable": False} for t in times]

        def verify(job: tuple) -> dict:
            i, mirrored, qp, qd, cands, qw, qh = job
            cv2.setRNGSeed(1000 + i)                         # RANSAC: the same answer whichever thread runs it
            best: dict = {"inliers": 0}
            for o in cands:
                v = _verify(qp, qd, kfeat[o][0], kfeat[o][1], qw, qh)
                v.update(keyframe=int(o), raw_at=round(float(kt[o]), 2), mirrored=mirrored)
                v["found"], v["weak"] = frame_found(v), frame_weak(v)
                if _rank(v) > _rank(best):
                    best = v
            return best

        for mirrored in (False, True):                       # the frames as they are, then the ones not found mirrored
            todo = [i for i, r in enumerate(res) if not r.get("found")]
            if not todo:
                break
            imgs = [np.ascontiguousarray(crops[i][:, ::-1]) if mirrored else crops[i] for i in todo]
            with cf.ThreadPoolExecutor(max_workers=workers) as ex:
                feats = list(ex.map(lambda im: _features(im, COMP_FEATURES), imgs))
            use = [(i, im, f) for i, im, f in zip(todo, imgs, feats) if len(f[1]) >= MIN_POINTS]
            for i, _, _ in use:
                res[i]["usable"] = True
            if not use:
                continue
            splits = [0]
            for _, _, f in use:
                splits.append(splits[-1] + len(f[1]))
            cands = _candidates(flann, owner, np.concatenate([f[1] for _, _, f in use]), splits)
            jobs = [(i, mirrored, f[0], f[1], c, im.shape[1], im.shape[0]) for (i, im, f), c in zip(use, cands)]
            with cf.ThreadPoolExecutor(max_workers=workers) as ex:
                got_v = list(ex.map(verify, jobs))
            for (i, *_), v in zip(jobs, got_v):
                if _rank(v) > _rank(res[i]):
                    res[i].update(v)
    finally:
        cv2.setNumThreads(n0)
    usable = [r for r in res if r.get("usable")]
    found = [r for r in usable if r.get("found")]
    weak = [r for r in usable if r.get("weak") or r.get("found")]
    n, k, w = len(usable), len(found), len(weak)
    secs = round(time.perf_counter() - t0, 2)
    det["frames"] = res
    if n < 3:
        return Side(ABSENT, n, k, "the competitor's frames have too little detail to compare", secs, details=det, weak=w)
    if k >= 2 and (k >= 3 or k / n >= 0.15):
        state = FOUND
    elif w == 0 and n >= 6:
        state = NOT_FOUND
    else:
        state = UNCLEAR
    why = {FOUND: "", NOT_FOUND: "none of its frames is in the RAW",
           UNCLEAR: f"too little to be sure ({w} of {n} frames almost found)"}[state]
    return Side(state, n, k, why, secs, sorted(r["raw_at"] for r in found), det, w)


# =====================================================================================================================
# the whole check
# =====================================================================================================================

def sound_fails(s: Side) -> bool:
    """The sound says "not from this RAW": nothing found, or not there -- or at most one piece of many found (another
    episode of the same show shares its jingle, its theme, its laugh track) with nothing else even almost found."""
    if s.state in (NOT_FOUND, ABSENT):
        return True
    return s.state == UNCLEAR and (s.found == 0 or (s.found == 1 and s.tested >= 7 and s.weak <= 1))


def decide(sound: Side, picture: Side | None) -> bool:
    """WRONG PAIR only when the evidence clearly says so: the picture looked at and not one of its frames even almost
    found, and the sound failing (sound_fails) -- or the picture not there and nothing of the sound even almost found.
    Never on the sound alone, never when both are not there."""
    if picture is None or sound.state == FOUND or picture.state in (FOUND, UNCLEAR):
        return False
    if picture.state == NOT_FOUND:
        return sound_fails(sound)
    return sound.state == NOT_FOUND                     # the picture not there: the sound must show nothing at all


def cache_key(comp: str | Path, raw: str | Path) -> str:
    parts = [f"v{VERSION}"]
    for p in (comp, raw):
        q = Path(p)
        st = q.stat()
        parts += [os.path.normcase(str(q.resolve())), str(st.st_size), str(st.st_mtime_ns)]
    return hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=12).hexdigest()


def cache_path(work_dir: str | Path, key: str) -> Path:
    return Path(work_dir) / "cache" / "pair_check" / f"{key}.json"


def check(comp: str | Path, raw: str | Path, work_dir: str | Path | None = None,
          progress: Callable[[str], None] | None = None, picture: str = "auto") -> Result:
    """Does ``comp`` come from ``raw``? The sound first; the picture only when the sound does not find it
    (``picture``: "auto"; "always" also looks at it then -- the validation). The answer is read from / written to
    ``work_dir``'s cache (None: not cached)."""
    say = progress or (lambda s: None)
    path = None
    if work_dir is not None and picture == "auto":
        try:
            path = cache_path(work_dir, cache_key(comp, raw))
            if path.is_file():
                res = Result.from_dict(json.loads(path.read_text(encoding="utf-8")))
                res.cached = True
                return res
        except (OSError, ValueError, KeyError, TypeError):
            pass
    t0 = time.perf_counter()
    ci, ri = probe(comp), probe(raw)
    small = min(int(ci.get("width") or 0), int(ci.get("height") or 0)) < MIN_SIDE or \
        min(int(ri.get("width") or 0), int(ri.get("height") or 0)) < MIN_SIDE
    if small or float(ci.get("duration") or 0.0) < MIN_COMP_S or float(ri.get("duration") or 0.0) < MIN_RAW_S:
        why = "too small a picture to compare" if small else "too short to compare"
        return Result(False, Side(ABSENT, why=f"not checked: {why}"), None, round(time.perf_counter() - t0, 2), False,
                      str(comp), str(raw))
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        say("the sound")
        fa = ex.submit(decode_audio, comp, float(ci.get("duration") or 0.0))
        fb = ex.submit(decode_audio, raw, float(ri.get("duration") or 0.0))
        # meanwhile: the competitor's frames, its moving part, and -- in the background --
        # the RAW's keyframes and their points: needed only when the sound does not decide (else stopped)
        times, frames, cw, ch = competitor_frames(comp, ci)
        box = moving_box(np.stack(frames)) if len(frames) >= 3 else (0, 0, cw, ch)
        rw = raw_width(ri, box)
        rh = _even(rw * ri["height"] / ri["width"])
        kfr = Keyframes(raw, float(ri["duration"]), rw, rh)
        if on_network(raw):                     # read again only when the sound does not decide
            pts = RawPoints(lambda: kfr.start().result(), decode_after_go=True).start()
        else:
            kfr.start()
            pts = RawPoints(kfr.result).start()
        try:
            comp_y, raw_y = fa.result(), fb.result()
            sound = sound_check(comp_y, raw_y)
            pic = None
            if sound.state != FOUND or picture == "always":
                say("the picture")
                t1 = time.perf_counter()
                pts.go()
                pts.result()
                pic = picture_check(times, frames, box, pts, {"wait_s": round(time.perf_counter() - t1, 2),
                                                               "keyframes_error": kfr.error})
                pic.seconds = round(time.perf_counter() - t1, 2)
        finally:
            pts.cancel()
            kfr.cancel()
    res = Result(False, sound, pic, round(time.perf_counter() - t0, 2), False, str(comp), str(raw))
    res.wrong = decide(sound, pic if sound.state != FOUND else None)
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            d = res.to_dict()
            for side in ("sound", "picture"):
                if d.get(side):
                    d[side]["details"] = _small(d[side].get("details") or {})
            tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(d, indent=1, default=float), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass
    return res


def _small(details: dict) -> dict:
    """What the cache keeps of a side's details (the numbers, not every frame)."""
    out = {k: v for k, v in details.items() if k not in ("tried", "frames")}
    if "tried" in details:
        out["tried"] = {k: [{x: s[x] for x in ("at", "raw_at", "ncc", "z", "ratio", "found", "weak")} for s in v]
                        for k, v in details["tried"].items()}
    if "frames" in details:
        out["frames"] = [{x: f.get(x) for x in ("at", "raw_at", "inliers", "matches", "scale", "rot", "spread",
                                                "mirrored", "found", "weak") if x in f} for f in details["frames"]]
    return out


def stop_if_wrong_pair(ctx: Any) -> Result | None:
    """pipeline.run's quick check before anything is converted (night 11): the numbers go into the run's log either
    way; a WRONG PAIR stops the run (pipeline.WrongPair, its message starts with "WRONG PAIR"). --force-inputs: not
    checked. A check that cannot run (a broken file, ffmpeg missing) never stops the run: S2 says what is wrong."""
    cfg = ctx.cfg
    if getattr(cfg, "force_inputs", False) or not str(getattr(cfg, "competitor", "") or ""):
        log.info("pair check: skipped (%s)", "--force-inputs" if getattr(cfg, "force_inputs", False) else
                 "no competitor")
        return None
    try:
        res = check(cfg.competitor, cfg.raw, getattr(cfg, "work_dir", None),
                    progress=lambda s: log.info("pair check: %s ...", s))
    except Exception as e:  # noqa: BLE001 - the long run finds out itself
        log.warning("pair check: could not run (%s: %s) -- the run goes on", type(e).__name__, e)
        return None
    log.info("pair check: %s -> %s", res.summary(), "WRONG PAIR" if res.wrong else "goes on")
    try:
        ctx.dlog.record("inputs", "pair_check", wrong=res.wrong, cached=res.cached, seconds=res.seconds,
                        sound={"state": res.sound.state, "found": res.sound.found, "weak": res.sound.weak,
                               "tested": res.sound.tested, "where": res.sound.where[:8]},
                        picture=None if res.picture is None else
                        {"state": res.picture.state, "found": res.picture.found, "weak": res.picture.weak,
                         "tested": res.picture.tested, "where": res.picture.where[:8]})
    except Exception:  # noqa: BLE001 - the decision log is a help
        pass
    if res.wrong:
        from .pipeline import WrongPair
        msg = res.message()
        log.error("STOPPED: %s", msg)
        raise WrongPair(msg)
    return res
