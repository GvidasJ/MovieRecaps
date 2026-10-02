"""Where the main person's face is in the RAW (--premiere: a clip whose framing cannot be copied from the competitor
is framed with the main face at the centre of the template window, export_xml_edl._settle_framing).

OpenCV's Haar cascades (frontal, then profile both ways), vendored in ``face_models/`` because OpenCV 5 wheels no
longer ship them; loaded from memory so a non-ASCII install path cannot break OpenCV's file reader on Windows.
Frames come from PyAV (media.VideoReader), so any RAW codec works. The main face of a frame is the largest face
whose centre the current framing shows inside the window (the person the competitor framed), else the largest
face; over several frames the median of their centres is used.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

import numpy as np

from .common import log

MODEL_DIR = Path(__file__).resolve().parent / "face_models"
DETECT_W = 960                       # frames are searched at this width (a face of >= 60 px in a 1920-wide RAW)
_CASCADES: dict[str, object] = {}
_CACHE: dict[tuple, list[tuple[float, float]]] = {}


def _cascade(name: str):
    if name not in _CASCADES:
        import cv2
        c = cv2.CascadeClassifier()
        text = (MODEL_DIR / name).read_text(encoding="utf-8")
        fs = cv2.FileStorage(text, cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY)
        _CASCADES[name] = c if c.read(fs.getFirstTopLevelNode()) and not c.empty() else None
    return _CASCADES[name]


def detect_faces(gray: np.ndarray) -> list[tuple[float, float]]:
    """(centre x, width) of the faces in a grayscale frame, in its own pixels: frontal faces, else profiles."""
    import cv2
    g = cv2.equalizeHist(gray)
    m = max(24, int(round(g.shape[1] / 32)))
    out: list[tuple[float, float]] = []
    front = _cascade("haarcascade_frontalface_alt2.xml")
    if front is not None:
        out = [(x + w / 2.0, float(w)) for x, y, w, h in front.detectMultiScale(g, 1.1, 4, minSize=(m, m))]
    prof = _cascade("haarcascade_profileface.xml") if not out else None
    if prof is not None:
        W = g.shape[1]
        out = [(x + w / 2.0, float(w)) for x, y, w, h in prof.detectMultiScale(g, 1.1, 4, minSize=(m, m))]
        out += [(W - (x + w / 2.0), float(w)) for x, y, w, h in
                prof.detectMultiScale(cv2.flip(g, 1), 1.1, 4, minSize=(m, m))]
    return out


def main_face_x(video: str | Path, raw_fps: float, times_s: Sequence[float], view: tuple[float, float] | None = None
                ) -> tuple[float | None, int]:
    """(median RAW x of the main face over the frames at times_s, how many of them showed one). The main face of a
    frame: the largest face centred inside ``view`` (the RAW x range the window shows), else the largest one.
    (None, 0) when the video cannot be read or no face is found."""
    p = Path(video)
    if not p.is_file() or not times_s:
        return None, 0
    try:
        st = p.stat()
        idx = sorted({max(0, int(math.floor(float(t) * float(raw_fps) + 1e-6))) for t in times_s})
        key = (str(p), st.st_size, st.st_mtime_ns)
        need = [i for i in idx if key + (i,) not in _CACHE]
        if need:
            import cv2
            from .media import VideoReader
            with VideoReader(p) as rd:
                try:
                    imgs = rd.get_many(need, fmt="gray")
                except IndexError:                   # past the end: read the ones that exist
                    imgs = {}
                    for i in need:
                        try:
                            imgs[i] = rd.get(i, fmt="gray")
                        except Exception:  # noqa: BLE001 - undecodable frame: no face there
                            pass
            for i in need:
                img = imgs.get(i)
                if img is None:
                    _CACHE[key + (i,)] = []
                    continue
                k = DETECT_W / float(img.shape[1])
                small = cv2.resize(img, (DETECT_W, int(round(img.shape[0] * k))), interpolation=cv2.INTER_AREA)
                _CACHE[key + (i,)] = [(x / k, w / k) for x, w in detect_faces(small)]
        xs = []
        for i in idx:
            faces = _CACHE.get(key + (i,)) or []
            inside = [f for f in faces if view is None or view[0] <= f[0] <= view[1]]
            pick = inside or faces
            if pick:
                xs.append(max(pick, key=lambda f: f[1])[0])
    except Exception as e:  # noqa: BLE001 - no face position: the caller keeps the framing it has
        log.info("faces: %s: %s: %s", p.name, type(e).__name__, e)
        return None, 0
    return (float(np.median(xs)), len(xs)) if xs else (None, 0)
