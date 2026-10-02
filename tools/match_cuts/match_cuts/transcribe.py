"""Word-level transcription for the captions (captions.py): faster-whisper with word timestamps.

faster-whisper (CTranslate2) installs with pip alone on Windows and runs on the CPU; WhisperX's forced alignment
needs torch + torchaudio and a matching alignment model, which is a much heavier and more fragile install there.
faster-whisper's word timestamps come from Whisper's own cross-attention alignment. Its built-in voice-activity
filter keeps Whisper from inventing words over music and silence. The model is downloaded once (Hugging Face cache)
on first use. Speaker changes are not detected (no diarisation).
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import numpy as np

from .captions import Word, clean_text
from .common import log

SR = 16000
_MODELS: dict[str, Any] = {}
CACHE_VERSION = 1      # the cached rows are raw Whisper output (words_from_rows joins them)


def available() -> str | None:
    """None when faster-whisper can be imported, else the reason (with the pip command)."""
    try:
        import faster_whisper  # noqa: F401
    except Exception as e:  # noqa: BLE001 - any import failure means: not usable here
        return f"faster-whisper is not installed ({type(e).__name__}: {e}); install it with `pip install faster-whisper`"
    return None


def _model(name: str) -> Any:
    if name not in _MODELS:
        from faster_whisper import WhisperModel
        log.info("captions: loading the %s transcription model (downloaded once on first use)", name)
        _MODELS[name] = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=max(1, os.cpu_count() or 1))
    return _MODELS[name]


def resample(y: np.ndarray, sr: int) -> np.ndarray:
    y = np.asarray(y, np.float32)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if int(sr) == SR or not len(y):
        return np.ascontiguousarray(y)
    from scipy.signal import resample_poly
    from math import gcd
    g = gcd(int(sr), SR)
    return np.ascontiguousarray(resample_poly(y, SR // g, int(sr) // g).astype(np.float32))


def audio_key(y: np.ndarray, model: str, language: str | None) -> str:
    h = hashlib.sha1(np.ascontiguousarray(y, np.float32).tobytes())
    h.update(f"{model}|{language}|{CACHE_VERSION}".encode())
    return h.hexdigest()[:24]


def transcribe_words(y: np.ndarray, sr: int, model: str = "small.en", language: str | None = "en",
                     cache: Any = None) -> list[Word]:
    """Words with start / end (s, sample 0 = t 0) and probability. Cached in WORK_DIR by audio content + model."""
    y16 = resample(y, sr)
    if not len(y16) or float(np.max(np.abs(y16))) < 1e-4:
        return []
    key = audio_key(y16, model, language)
    path = cache.path("captions_asr", key, ".json") if cache is not None else None
    if path is not None and path.exists():
        rows = json.loads(path.read_text(encoding="utf-8"))
    else:
        segments, _info = _model(model).transcribe(
            y16, language=language or None, word_timestamps=True, vad_filter=True, beam_size=5,
            condition_on_previous_text=False)
        rows = []
        for seg in segments:
            for w in seg.words or []:
                rows.append({"word": w.word, "start": float(w.start), "end": float(w.end),
                             "prob": float(getattr(w, "probability", 1.0))})
        if path is not None:
            from .common import atomic_write_text
            atomic_write_text(path, json.dumps(rows, ensure_ascii=False, indent=0))
    return words_from_rows(rows)


def words_from_rows(rows: list[dict]) -> list[Word]:
    """Cleaned words. A piece Whisper emits without a leading space that starts with a hyphen, an apostrophe or a
    separator continues the previous word ("X" + "-Force" -> "X-Force", "15" + ",000" -> "15,000")."""
    words: list[Word] = []
    for r in rows:
        piece = str(r["word"])
        if words and piece and piece[0] in "-'\u2019,.:/" :
            w = words[-1]
            raw = w.raw + piece.strip()
            words[-1] = Word(clean_text(raw), w.start, max(w.end, float(r["end"])), min(w.prob, float(r["prob"])), raw)
            continue
        text = clean_text(piece)
        if not any(ch.isalnum() for ch in text):
            continue
        words.append(Word(text, float(r["start"]), max(float(r["end"]), float(r["start"])), float(r["prob"]),
                          piece.strip()))
    return words
