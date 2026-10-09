"""people.py: who is in the picture and who is speaking (the Premiere export's framing check, task 2).

Where the edit plays the RAW (every clip's source range, +- MARGIN_S), every 1/25 s (ANALYSIS_FPS, the rate the
active-speaker model was trained at):

* **Faces**: YuNet (OpenCV's FaceDetectorYN; Wu et al. 2023, MIT licence; ``face_models/face_detection_yunet_2023mar
  .onnx``) on the frame scaled to DETECT_W px wide -- a modern CNN detector replacing the Haar cascades; it runs on
  the CPU (a few ms a frame). Faces under MIN_SCORE confidence are dropped.
* **Tracks**: a face continues from frame to frame where the boxes overlap (IoU >= TRACK_IOU), across up to
  TRACK_GAP missed frames (filled in linearly), never across a shot change of the RAW. A track shorter than
  MIN_TRACK frames is noise. A face much smaller than the biggest one of its shot that never speaks (a poster or a
  photo in the background: the Zendaya interview's Spider-Man poster) is no person.
* **Speaking**: Light-ASD (asd_model.py; Liao et al. CVPR 2023, MIT licence, weights fine-tuned on TalkSet) scores,
  for every track over its whole length, how well its mouth moves with the RAW's sound: 112x112 grey face crops and
  13 MFCCs, a speaking logit per frame (> 0: speaking), averaged over 1-6 s windows and smoothed over +-2 frames --
  as the authors' own demo does. PyTorch on the GPU (CUDA) when there is one, else the CPU; without PyTorch nobody
  is scored and the biggest face is taken as the speaker (said in the summary).

``People.speaker(t0, t1, speech)`` -- who speaks in a clip: the track that is the top-scoring speaking face on the
most frames of its speech (the clip's dominant speaker); unclear (no face scores above 0 there) -> the biggest face.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .common import log

ANALYSIS_FPS = 25                  # frames per second analysed (Light-ASD's rate)
DETECT_W = 960                     # faces are found on the frame scaled to this width
MIN_SCORE = 0.6                    # YuNet confidence below this: no face
TRACK_IOU = 0.3                    # a face continues where its box overlaps the last one this much ...
TRACK_GAP = 10                     # ... after at most this many frames without it
MIN_TRACK = 5                      # frames: a shorter track is noise
BACKGROUND_FRAC = 0.5              # a face under this x the height of its shot's biggest that never speaks: no person
CROP_SCALE = 0.4                   # Light-ASD's face crop around the box (its demo's cropScale)
SPEAKING = 0.0                     # a speaking logit above this: speaking
MARGIN_S = 3.0                     # analysed this far around every clip: the speaker model's context, and the
                                   # speech-safe cuts may let a clip play on that far (speech.py)
FRAME_BUDGET_BYTES = 1 << 30      # decoded RAW frames held at once (4K: ~42 analysis frames, 1.7 s); a longer
                                   # stretch is analysed a chunk at a time (decoded twice: faces, then their crops)
MIN_COVERED = 0.5                  # a clip is judged on its analysed frames when at least this share of it is
VERSION = 1
MODEL_DIR = Path(__file__).resolve().parent / "face_models"
YUNET = "face_detection_yunet_2023mar.onnx"
ASD_WEIGHTS = "light_asd_talkset.model"


@dataclass
class Track:
    """One face over consecutive analysis frames: ``k`` the frame indices (time k / ANALYSIS_FPS s on the RAW), its
    box per frame (x0, y0, x1, y1 RAW px; gaps filled in), whether the detector found it there, its speaking
    logit per frame (nan: not scored)."""
    id: int
    k: np.ndarray
    box: np.ndarray
    found: np.ndarray
    score: np.ndarray

    @property
    def height(self) -> float:
        return float(np.median(self.box[:, 3] - self.box[:, 1]))

    def at(self, k: int) -> int | None:
        i = int(k - self.k[0])
        return i if 0 <= i < len(self.k) else None


@dataclass
class People:
    """The faces and speaking scores of the RAW where the edit plays it."""
    tracks: list[Track] = field(default_factory=list)
    windows: list[tuple[float, float]] = field(default_factory=list)     # RAW seconds analysed
    asd: str = "none"                                                    # how speaking was scored
    raw_wh: tuple[int, int] = (0, 0)

    def frames(self, t0: float, t1: float) -> np.ndarray:
        """The analysis frames inside RAW [t0, t1) (s); a stretch shorter than one of them: the nearest one."""
        a, b = sorted((t0, t1))
        ks = np.arange(int(math.ceil(a * ANALYSIS_FPS - 1e-9)), int(math.ceil(b * ANALYSIS_FPS - 1e-9)))
        return ks if len(ks) else np.array([int(round((a + b) / 2.0 * ANALYSIS_FPS))])

    def analysed(self, ks: np.ndarray) -> np.ndarray:
        """The frames of ks inside the stretches analysed."""
        t = ks / float(ANALYSIS_FPS)
        m = np.zeros(len(ks), bool)
        for w0, w1 in self.windows:
            m |= (t >= w0 - 1e-6) & (t < w1 + 1e-6)
        return ks[m]

    def covered(self, t0: float, t1: float) -> bool:
        """At least MIN_COVERED of RAW [t0, t1) was analysed (a clip that plays on past the stretches looked at is
        judged on the part that was)."""
        ks = self.frames(t0, t1)
        return bool(len(ks)) and len(self.analysed(ks)) >= MIN_COVERED * len(ks)

    def present(self, ks: np.ndarray) -> list[Track]:
        return [t for t in self.tracks if np.any((ks >= t.k[0]) & (ks <= t.k[-1]))]

    def speaker(self, t0: float, t1: float, speech: Sequence[tuple[float, float]]) -> dict:
        """Who speaks in RAW [t0, t1): {'track', 'how' ('speaker' | 'biggest face' | 'a person' | 'nobody'),
        'speaking' (any speech there), 'ks' (the frames that matter: the speech's, else all), 'share'}."""
        ks = self.analysed(self.frames(t0, t1))
        if not len(ks):
            return {"track": None, "how": "nobody", "speaking": False, "ks": ks, "share": 0.0}
        sp = np.array([any(a <= k / ANALYSIS_FPS < b for a, b in speech) for k in ks], bool)
        talk = ks[sp] if sp.any() else ks
        tracks = self.present(ks)
        if not tracks:
            return {"track": None, "how": "nobody", "speaking": bool(sp.any()), "ks": talk, "share": 0.0}
        biggest = max(tracks, key=lambda t: t.height)
        if not sp.any():
            return {"track": biggest, "how": "a person", "speaking": False, "ks": talk, "share": 0.0}
        wins: dict[int, int] = {}
        for k in talk:
            best = None
            for t in tracks:
                i = t.at(int(k))
                if i is not None and np.isfinite(t.score[i]) and t.score[i] > SPEAKING:
                    if best is None or t.score[i] > best[0]:
                        best = (float(t.score[i]), t.id)
            if best is not None:
                wins[best[1]] = wins.get(best[1], 0) + 1
        if wins:
            tid = max(wins, key=lambda i: (wins[i], -i))
            t = next(t for t in tracks if t.id == tid)
            return {"track": t, "how": "speaker", "speaking": True, "ks": talk, "share": wins[tid] / len(talk)}
        return {"track": biggest, "how": "biggest face", "speaking": True, "ks": talk, "share": 0.0}


# ---------------------------------------------------------------------------------------------------------------------
# faces + tracks
# ---------------------------------------------------------------------------------------------------------------------

_DET: dict[tuple[int, int], Any] = {}


def detector(w: int, h: int) -> Any:
    """YuNet for frames w x h, loaded from memory (a non-ASCII install path cannot break OpenCV's file reader)."""
    import cv2
    if (w, h) not in _DET:
        model = np.frombuffer((MODEL_DIR / YUNET).read_bytes(), np.uint8)
        _DET[(w, h)] = cv2.FaceDetectorYN.create("onnx", model, np.zeros(0, np.uint8), (w, h), MIN_SCORE, 0.3, 5000)
    return _DET[(w, h)]


def detect(img: np.ndarray) -> list[tuple[float, float, float, float, float]]:
    """(x0, y0, x1, y1, confidence) of the faces in a BGR frame, in its own pixels."""
    import cv2
    H, W = img.shape[:2]
    dw = min(DETECT_W, W)
    dh = int(round(H * dw / W))
    small = img if dw == W else cv2.resize(img, (dw, dh), interpolation=cv2.INTER_AREA)
    _, f = detector(dw, dh).detect(small)
    k = W / float(dw)
    return [] if f is None else [(x * k, y * k, (x + w) * k, (y + h) * k, float(r[-1]))
                                 for x, y, w, h, *r in f.tolist()]


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def track(dets: dict[int, list], breaks: Sequence[int] = ()) -> list[Track]:
    """Face tracks from {frame: [(x0, y0, x1, y1, conf)]}: greedy IoU matching frame to frame, never across a frame
    in ``breaks`` (a shot change: the first frame of a new shot), gaps filled in linearly."""
    brk = sorted(set(int(b) for b in breaks))
    live: list[dict] = []
    done: list[dict] = []
    import bisect
    for k in sorted(dets):
        seg = bisect.bisect_right(brk, k)
        keep = []
        for t in live:
            (keep if (k - t["k"][-1] <= TRACK_GAP and t["seg"] == seg) else done).append(t)
        live = keep
        pairs = sorted(((_iou(t["box"][-1], d), ti, di) for ti, t in enumerate(live) for di, d in enumerate(dets[k])),
                       reverse=True)
        used_t, used_d = set(), set()
        for v, ti, di in pairs:
            if v < TRACK_IOU or ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            live[ti]["k"].append(k)
            live[ti]["box"].append(dets[k][di][:4])
        for di, d in enumerate(dets[k]):
            if di not in used_d:
                live.append({"k": [k], "box": [d[:4]], "seg": seg})
    out = []
    for t in sorted(done + live, key=lambda t: (t["k"][0], t["box"][0][0])):
        if len(t["k"]) < MIN_TRACK:
            continue
        ks = np.arange(t["k"][0], t["k"][-1] + 1)
        bx = np.asarray(t["box"], float)
        full = np.stack([np.interp(ks, t["k"], bx[:, i]) for i in range(4)], 1)
        found = np.isin(ks, t["k"])
        out.append(Track(len(out), ks, full, found, np.full(len(ks), np.nan)))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# active speaker (Light-ASD)
# ---------------------------------------------------------------------------------------------------------------------

def mfcc(sig: np.ndarray) -> np.ndarray:
    """13 MFCCs every 10 ms of 16 kHz audio at int16 scale -- python_speech_features.mfcc(sig, 16000, numcep=13,
    winlen=0.025, winstep=0.010) as Light-ASD was trained with (26 mel filters, 512-point FFT, pre-emphasis 0.97,
    lifter 22, frame energy as coefficient 0)."""
    from scipy.fftpack import dct
    s = np.asarray(sig, np.float64)
    if len(s) < 2:
        s = np.zeros(400)
    s = np.append(s[0], s[1:] - 0.97 * s[:-1])
    flen, fstep, nfft = 400, 160, 512
    n = 1 if len(s) <= flen else 1 + int(math.ceil((len(s) - flen) / fstep))
    pad = np.concatenate([s, np.zeros((n - 1) * fstep + flen - len(s))])
    frames = pad[np.arange(flen)[None, :] + (np.arange(n) * fstep)[:, None]]
    pspec = (np.abs(np.fft.rfft(frames, nfft)) ** 2) / nfft
    energy = pspec.sum(1)
    energy = np.where(energy == 0, np.finfo(float).eps, energy)
    mel = np.linspace(0, 2595 * np.log10(1 + 8000 / 700.0), 28)
    b = np.floor((nfft + 1) * (700 * (10 ** (mel / 2595.0) - 1)) / 16000)
    fb = np.zeros((26, nfft // 2 + 1))
    for j in range(26):
        for i in range(int(b[j]), int(b[j + 1])):
            fb[j, i] = (i - b[j]) / (b[j + 1] - b[j])
        for i in range(int(b[j + 1]), int(b[j + 2])):
            fb[j, i] = (b[j + 2] - i) / (b[j + 2] - b[j + 1])
    feat = pspec @ fb.T
    feat = np.log(np.where(feat == 0, np.finfo(float).eps, feat))
    feat = dct(feat, type=2, axis=1, norm="ortho")[:, :13]
    feat *= 1 + 11.0 * np.sin(np.pi * np.arange(13) / 22.0)
    feat[:, 0] = np.log(energy)
    return feat


def _crop_geometry(t: Track) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(half size, centre y, centre x) of track t's crop per frame: its box median-smoothed over 13 frames."""
    from scipy.signal import medfilt
    b = t.box
    s = np.maximum(b[:, 3] - b[:, 1], b[:, 2] - b[:, 0]) / 2
    y = (b[:, 1] + b[:, 3]) / 2
    x = (b[:, 0] + b[:, 2]) / 2
    ksz = min(13, len(s) if len(s) % 2 else len(s) - 1)
    if ksz >= 3:
        s, y, x = medfilt(s, ksz), medfilt(y, ksz), medfilt(x, ksz)
    return s, y, x


def _crop(img: np.ndarray, s: float, y: float, x: float) -> np.ndarray | None:
    """One 112x112 grey crop (face_crops)."""
    import cv2
    cs = CROP_SCALE
    bs = max(1.0, float(s))
    bsi = int(bs * (1 + 2 * cs))
    fr = cv2.copyMakeBorder(img, bsi, bsi, bsi, bsi, cv2.BORDER_CONSTANT, value=(110, 110, 110))
    my, mx = y + bsi, x + bsi
    face = fr[max(0, int(my - bs)):int(my + bs * (1 + 2 * cs)), max(0, int(mx - bs * (1 + cs))):int(mx + bs * (1 + cs))]
    if face.size == 0:
        return None
    g = cv2.cvtColor(cv2.resize(face, (224, 224)), cv2.COLOR_BGR2GRAY)
    return g[56:168, 56:168]


def face_crops(frames: dict[int, np.ndarray], t: Track, out: np.ndarray | None = None) -> np.ndarray:
    """Light-ASD's input: per frame of the track a 112x112 grey crop around its (median-smoothed) box, the box
    widened by CROP_SCALE and reaching further down (the mouth and chin), as the authors' demo crops. ``out``: the
    crops so far, filled in for the frames given (the chunked analysis: a chunk of frames at a time)."""
    s, y, x = _crop_geometry(t)
    out = np.zeros((len(t.k), 112, 112), np.uint8) if out is None else out
    for i, k in enumerate(t.k):
        img = frames.get(int(k))
        if img is None:
            continue
        c = _crop(img, s[i], y[i], x[i])
        if c is not None:
            out[i] = c
    return out


_ASD: dict[str, Any] = {}


def asd_model() -> tuple[Any, str] | None:
    """(Light-ASD on the GPU when PyTorch has CUDA, else the CPU; 'cuda' / 'cpu'), or None without PyTorch."""
    if "model" not in _ASD:
        try:
            import torch
            from .asd_model import LightASD
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            _ASD["model"] = (LightASD().load(str(MODEL_DIR / ASD_WEIGHTS)).to(dev), dev)
        except Exception as e:  # noqa: BLE001 - no PyTorch / no weights: no speaker scores
            log.info("people: active speaker detection unavailable (%s: %s)", type(e).__name__, e)
            _ASD["model"] = None
    return _ASD["model"]


def speaking_scores(model: Any, dev: str, audio16: np.ndarray, frames: dict[int, np.ndarray] | None, t: Track,
                    crops: np.ndarray | None = None) -> np.ndarray:
    """Light-ASD's speaking logit for every frame of track t (averaged over 1-6 s windows, smoothed +-2 frames), from
    the frames or the track's face crops made already (``crops``)."""
    import torch
    v = face_crops(frames or {}, t) if crops is None else crops
    t0 = t.k[0] / ANALYSIS_FPS
    n = len(t.k)
    a0 = int(round(t0 * 16000))
    seg = np.asarray(audio16[max(0, a0):max(0, a0) + int(round(n / ANALYSIS_FPS * 16000))], np.float64) * 32768.0
    a = mfcc(seg)
    length = min((a.shape[0] - a.shape[0] % 4) / 100.0, n / ANALYSIS_FPS)
    a = a[:int(round(length * 100))]
    v = v[:int(round(length * ANALYSIS_FPS))]
    if len(v) < 1 or len(a) < 4:
        return np.full(n, np.nan)
    runs = []
    with torch.no_grad():
        for dur in (1, 2, 3, 4, 5, 6):
            sc: list[float] = []
            for i in range(int(math.ceil(length / dur))):
                ia = a[i * dur * 100:(i + 1) * dur * 100]
                iv = v[i * dur * ANALYSIS_FPS:(i + 1) * dur * ANALYSIS_FPS]
                m = min(len(ia) // 4, len(iv))
                if m < 1:
                    continue
                ta = torch.tensor(ia[:4 * m], dtype=torch.float32, device=dev).unsqueeze(0)
                tv = torch.tensor(iv[:m], dtype=torch.float32, device=dev).unsqueeze(0)
                sc.extend(model.scores(ta, tv).float().cpu().numpy().tolist())
            runs.append(sc)
    m = min(len(r) for r in runs)
    sc = np.mean([r[:m] for r in runs], 0)
    sm = np.array([np.mean(sc[max(0, i - 2):i + 3]) for i in range(len(sc))])
    out = np.full(n, np.nan)
    out[:len(sm)] = sm
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the analysis
# ---------------------------------------------------------------------------------------------------------------------

def windows_of(ranges_s: Sequence[tuple[float, float]], dur: float) -> list[tuple[float, float]]:
    """The RAW stretches analysed: every range +- MARGIN_S, merged, on whole analysis frames."""
    rs = sorted((max(0.0, min(a, b) - MARGIN_S), min(dur, max(a, b) + MARGIN_S)) for a, b in ranges_s)
    out: list[list[float]] = []
    for a, b in rs:
        if out and a <= out[-1][1] + 1.0 / ANALYSIS_FPS:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(math.floor(a * ANALYSIS_FPS) / ANALYSIS_FPS, math.ceil(b * ANALYSIS_FPS) / ANALYSIS_FPS) for a, b in out]


def analyse(video: str, raw_fps: float, ranges_s: Sequence[tuple[float, float]], audio16: np.ndarray | None,
            shot_changes_s: Sequence[float] = (), cache: Any = None, file_hash: str = "") -> People:
    """People of the RAW ``video`` in ``ranges_s`` (seconds): faces, tracks and speaking scores (module docstring).
    Cached by the file's hash, the stretches and this module's settings."""
    from .media import VideoReader
    dur = float("inf")
    wins = windows_of(ranges_s, dur)
    if not wins:
        return People()

    def compute() -> dict:
        import time
        t0 = time.time()
        res: dict = {"tracks": [], "windows": wins, "asd": "none", "raw_wh": [0, 0]}
        asd = asd_model() if audio16 is not None and len(audio16) else None
        res["asd"] = f"Light-ASD on {asd[1]}" if asd else "none (no PyTorch / weights): the biggest face speaks"
        brk_all = [int(round(c * ANALYSIS_FPS)) for c in shot_changes_s]
        tid = 0
        with VideoReader(video) as rd:
            res["raw_wh"] = [int(rd.width), int(rd.height)]
            per_frame = max(1, int(rd.width) * int(rd.height) * 3)
            chunk = max(ANALYSIS_FPS, int(FRAME_BUDGET_BYTES // per_frame))     # analysis frames held at once

            def load(ks: list[int]) -> dict[int, np.ndarray]:
                idx = {k: int(round(k / ANALYSIS_FPS * raw_fps)) for k in ks}
                try:
                    got = rd.get_many(sorted(set(idx.values())), fmt="bgr24")
                except IndexError:
                    got = {}
                    for j in sorted(set(idx.values())):
                        try:
                            got[j] = rd.get(j, fmt="bgr24")
                        except Exception:  # noqa: BLE001 - past the end: no frame
                            pass
                return {k: got[idx[k]] for k in ks if idx[k] in got}
            for a, b in wins:
                ks = list(range(int(round(a * ANALYSIS_FPS)), int(round(b * ANALYSIS_FPS))))
                parts = [ks[i:i + chunk] for i in range(0, len(ks), chunk)] or [[]]
                frames = load(ks) if len(parts) == 1 else None             # a short stretch: decoded once
                dets: dict[int, list] = {}
                for part in parts:                    # the faces of every frame (a chunk of frames at a time)
                    fr = frames if frames is not None else load(part)
                    inside = set(part)
                    dets.update({k: [d for d in detect(img) if d[4] >= MIN_SCORE] for k, img in fr.items()
                                 if k in inside})
                    del fr
                trs = track(dets, [c for c in brk_all if ks[0] < c <= ks[-1]])
                if asd is not None and trs:
                    crops = {id(t): np.zeros((len(t.k), 112, 112), np.uint8) for t in trs}
                    for part in parts:                # the face crops: the frames decoded again, a chunk at a time
                        fr = frames if frames is not None else load(part)
                        for t in trs:
                            face_crops(fr, t, crops[id(t)])
                        del fr
                    for t in trs:
                        t.score = speaking_scores(asd[0], asd[1], audio16, None, t, crops[id(t)])
                # no person: a face much smaller than its shot's biggest that never speaks (a poster, a photo)
                big = max((t.height for t in trs), default=0.0)
                keep = [t for t in trs if t.height >= BACKGROUND_FRAC * big or
                        (np.isfinite(t.score).any() and np.nanmax(t.score) > SPEAKING + 1.0)]
                for t in keep:
                    res["tracks"].append({"id": tid, "k0": int(t.k[0]), "box": np.round(t.box, 1).tolist(),
                                          "found": t.found.astype(int).tolist(),
                                          "score": [None if not np.isfinite(s) else round(float(s), 3) for s in t.score]})
                    tid += 1
                del frames
        log.info("people: %d faces tracked in %d stretch(es) of the RAW, speaking scored by %s (%.1f s)",
                 len(res["tracks"]), len(wins), res["asd"], time.time() - t0)
        return res
    if cache is not None and file_hash:
        from .common import stage_key
        key = stage_key("people", file_hash, wins, list(shot_changes_s), DETECT_W, MIN_SCORE, TRACK_IOU, TRACK_GAP,
                        MIN_TRACK, BACKGROUND_FRAC, CROP_SCALE, MARGIN_S, VERSION, YUNET, ASD_WEIGHTS)
        d = cache.json("people", key, compute)
    else:
        d = compute()
    tracks = []
    for t in d["tracks"]:
        bx = np.asarray(t["box"], float)
        tracks.append(Track(int(t["id"]), np.arange(int(t["k0"]), int(t["k0"]) + len(bx)), bx,
                            np.asarray(t["found"], bool),
                            np.array([np.nan if s is None else float(s) for s in t["score"]])))
    return People(tracks, [tuple(w) for w in d["windows"]], str(d["asd"]), tuple(d["raw_wh"]))
