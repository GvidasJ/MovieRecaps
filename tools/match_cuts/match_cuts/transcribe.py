"""Word-level transcription for the captions (captions.py), the speech-safe cuts and the silence removal: the speech
model (asr.py: OpenAI Whisper large-v3 by default, on the GPU), then forced alignment (align.py) for word timings to
the frame.

The default is the most accurate engine of the comparison on the answer-key videos (reports/task-4.md): Whisper
large-v3 through faster-whisper -- 4 % word errors against the user's own captions, where the earlier default
small.en made 8 %. Its own word timings are coarse (cross-attention, ~90 ms off), so every word is then aligned to
the audio (wav2vec2 CTC, ~35 ms) and a word after a pause starts on its first sound. ``hints`` (caption_allowlist.txt
and the learned glossary: names, rare words) go to the model as hot words. If the default cannot run here (not
installed, the download failed, no memory), FALLBACK -- the earlier default, small.en -- is used and the run's
summary says so (``LOG``). Results are cached in WORK_DIR by audio content, engine, hints and alignment.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Sequence

import numpy as np

from .captions import Word, clean_text
from .common import log

SR = 16000
DEFAULT_MODEL = "large-v3"
FALLBACK = "small.en"          # the earlier default: used when the default cannot run here
CACHE_VERSION = 3              # 2: final words (engine + forced alignment), not raw Whisper rows
LOG: list[dict] = []           # one row per transcription actually run this process (the run summary's GPU line)


def available() -> str | None:
    """None when a speech model can run here, else the reason (with the pip command)."""
    try:
        import faster_whisper  # noqa: F401
    except Exception as e:  # noqa: BLE001 - any import failure means: not usable here
        return f"faster-whisper is not installed ({type(e).__name__}: {e}); install it with `pip install faster-whisper`"
    return None


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


def audio_key(y: np.ndarray, model: str, language: str | None, extra: str = "") -> str:
    h = hashlib.sha1(np.ascontiguousarray(y, np.float32).tobytes())
    h.update(f"{model}|{language}|{CACHE_VERSION}|{extra}".encode())
    return h.hexdigest()[:24]


def transcribe_words(y: np.ndarray, sr: int, model: str = DEFAULT_MODEL, language: str | None = "en",
                     cache: Any = None, hints: Sequence[str] = (), aligned: bool = True) -> list[Word]:
    """Words with start / end (s, sample 0 = t 0) and probability: the engine ``model`` (asr.py; the GPU when there
    is one), timed by forced alignment (``aligned``). Cached in WORK_DIR by audio content + engine + hints."""
    from . import align as A, asr
    y16 = resample(y, sr)
    if not len(y16) or float(np.max(np.abs(y16))) < 1e-4:
        return []
    hints = [h for h in dict.fromkeys(str(h).strip() for h in hints) if h]
    do_align = aligned and A.available() is None
    key = audio_key(y16, model, language, f"{'aligned' if do_align else 'raw'}|{','.join(hints)}")
    path = cache.path("captions_asr", key, ".json") if cache is not None else None
    if path is not None and path.exists():
        LOG.append({"model": model, "asked": model, "device": "cache", "note": "", "audio_s": round(len(y16) / SR, 2),
                    "load_s": 0.0, "seconds": 0.0, "aligned": None, "align_error": None})
        return [Word(**w) for w in json.loads(path.read_text(encoding="utf-8"))]
    t = time.perf_counter()
    used, note = model, ""
    try:
        res = asr.transcribe(y16, model, hints)
    except Exception as e:  # noqa: BLE001 - the default cannot run here: the fallback, said in the summary
        if model == FALLBACK:
            raise
        log.warning("captions: %s could not run (%s: %s) -- %s instead", model, type(e).__name__, e, FALLBACK)
        note = f"{model} could not run here ({type(e).__name__}: {str(e)[:160]}) -- {FALLBACK} instead"
        used = FALLBACK
        res = asr.transcribe(y16, FALLBACK, hints)
    words = list(res.words)
    ainfo: dict = {}
    if do_align and words:
        try:
            words, ainfo = A.align(y16, words, timed=res.timed)
            words = A.refine_onsets(y16, words)
        except Exception as e:  # noqa: BLE001 - the engine's own timings then
            ainfo = {"error": f"{type(e).__name__}: {e}"}
            log.warning("captions: forced alignment failed (%s): the speech model's own word timings", e)
    LOG.append({"model": used, "asked": model, "device": res.device, "note": "; ".join(x for x in (note, res.note) if x),
                "audio_s": round(len(y16) / SR, 2), "load_s": round(res.load_seconds, 2),
                "seconds": round(time.perf_counter() - t - res.load_seconds, 2),
                "aligned": ainfo.get("device") if ainfo and "error" not in ainfo else None,
                "align_error": ainfo.get("error") if ainfo else None})
    if path is not None:
        from .common import atomic_write_text
        atomic_write_text(path, json.dumps([w.__dict__ for w in words], ensure_ascii=False, indent=0))
    return words


def score_texts(y16: np.ndarray, texts: Sequence[str], model: str = DEFAULT_MODEL, cache: Any = None
                ) -> list[float]:
    """asr.score_texts (16 kHz mono): each text's log-likelihood as what y16 says, by ``model``. Cached in WORK_DIR
    by audio content + engine + texts."""
    from . import asr
    key = audio_key(y16, model, "en", "score|" + "\n".join(texts))
    path = cache.path("captions_score", key, ".json") if cache is not None else None
    if path is not None and path.exists():
        return [float(x) for x in json.loads(path.read_text(encoding="utf-8"))]
    out = [float(x) for x in asr.score_texts(y16, list(texts), model)]
    if path is not None:
        from .common import atomic_write_text
        atomic_write_text(path, json.dumps(out))
    return out


def words_from_rows(rows: list[dict]) -> list[Word]:
    """Cleaned words. A piece Whisper emits without a leading space that starts with a hyphen, an apostrophe or a
    separator continues the previous word ("X" + "-Force" -> "X-Force", "15" + ",000" -> "15,000")."""
    words: list[Word] = []
    for r in rows:
        piece = str(r["word"])
        if words and piece and piece[0] in "-'’,.:/" :
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


def summary() -> list[str]:
    """The run summary's speech-recognition lines: which engine ran where (GPU / CPU), how fast, aligned or not, and
    any fallback -- from LOG (transcripts an earlier run made, from the cache, said as such)."""
    if not LOG:
        return []
    from .asr import gpu_name
    out = []
    by: dict[tuple, list[dict]] = {}
    for r in LOG:
        by.setdefault((r["model"], r["device"]), []).append(r)
    for (model, dev), rows in by.items():
        a = sum(r["audio_s"] for r in rows)
        s = sum(r["seconds"] for r in rows)
        load = sum(r.get("load_s") or 0.0 for r in rows)
        if dev == "cache":
            out.append(f"{model}: {len(rows)} piece(s), {a:.0f} s of audio, from the cache (transcribed by an earlier "
                       f"run with the same audio, model and hints)")
            continue
        where = f"the GPU ({gpu_name() or 'CUDA'})" if dev == "cuda" else "the CPU"
        al = {r["aligned"] for r in rows if r["aligned"]}
        out.append(f"{model} on {where}: {len(rows)} piece(s), {a:.0f} s of audio in {s:.1f} s"
                   + (f" (+{load:.0f} s loading the model)" if load >= 1 else "")
                   + (f"; words timed by forced alignment on {'the GPU' if 'cuda' in al else 'the CPU'}" if al
                      else "; the model's own word timings"))
    for r in LOG:
        if r["note"]:
            out.append(r["note"])
        if r.get("align_error"):
            out.append(f"forced alignment failed: {r['align_error']}")
    return list(dict.fromkeys(out))
