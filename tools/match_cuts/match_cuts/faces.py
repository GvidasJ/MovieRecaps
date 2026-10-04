"""Where the main person's face is in the RAW (raw_only.py, and export_xml_edl._settle_framing when the RAW's people
were not analysed): YuNet (people.detect -- OpenCV's FaceDetectorYN, a modern CNN face detector, MIT licence,
``face_models/face_detection_yunet_2023mar.onnx``; it replaced OpenCV's Haar cascades). Frames come from PyAV
(media.VideoReader), so any RAW codec works. The main face of a frame is the largest face whose centre the current
framing shows inside the window (the person the competitor framed), else the largest face; over several frames the
median of their centres is used.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

import numpy as np

from .common import log

_CACHE: dict[tuple, list[tuple[float, float]]] = {}


def detect_faces(img: np.ndarray) -> list[tuple[float, float]]:
    """(centre x, width) of the faces in a frame (BGR, or grey), in its own pixels."""
    import cv2
    from .people import detect
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return [((x0 + x1) / 2.0, float(x1 - x0)) for x0, y0, x1, y1, _ in detect(img)]


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
            from .media import VideoReader
            with VideoReader(p) as rd:
                try:
                    imgs = rd.get_many(need, fmt="bgr24")
                except IndexError:                   # past the end: read the ones that exist
                    imgs = {}
                    for i in need:
                        try:
                            imgs[i] = rd.get(i, fmt="bgr24")
                        except Exception:  # noqa: BLE001 - undecodable frame: no face there
                            pass
            for i in need:
                img = imgs.get(i)
                _CACHE[key + (i,)] = [] if img is None else detect_faces(img)
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
