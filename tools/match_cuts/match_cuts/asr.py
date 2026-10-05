"""asr.py: the speech-recognition engines for the captions -- on the GPU (NVIDIA CUDA) when there is one.

Every engine turns 16 kHz mono audio into words (captions.Word: text, start, end, probability):

* ``small.en`` / ``medium.en`` / ``large-v3`` / ``large-v3-turbo`` -- OpenAI Whisper through faster-whisper
  (CTranslate2; on the GPU it needs NVIDIA's cuBLAS 12 / cuDNN 9 DLLs, the ``nvidia-cublas-cu12`` and
  ``nvidia-cudnn-cu12`` packages); word timings from Whisper's own cross-attention;
* ``parakeet-tdt-0.6b-v3`` -- NVIDIA Parakeet TDT v3 through Hugging Face Transformers; token timings (80 ms steps);
* ``parakeet-tdt-0.6b-v2`` -- NVIDIA Parakeet TDT v2 (English) through NVIDIA NeMo; word timings (80 ms steps);
* ``canary-qwen-2.5b`` -- NVIDIA Canary-Qwen 2.5B through NeMo (a speech encoder feeding Qwen3-1.7B): text only, no
  timings (align.py times its words), at most ~40 s at a time (split at the quietest moments);
* ``cohere-transcribe`` -- Cohere Transcribe through Transformers: gated on Hugging Face (its terms accepted and
  ``hf auth login`` first).

``transcribe(y16, name, hints)`` runs one; ``hints`` (caption_allowlist.txt: names, rare words) go to Whisper as hot
words. The GPU is used when torch / CTranslate2 see one; if loading or running there
fails, the engine runs again on the CPU and Result.note says so (the run's summary shows it).
"""
from __future__ import annotations

import gc
import glob
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from .captions import Word, clean_text

SR = 16000
CHUNK_S = 28.0                  # Canary-Qwen hears at most ~40 s at once: longer audio is split near this length


@dataclass
class Result:
    words: list[Word]
    timed: bool                   # the words carry the engine's own timings (False: align.py must time them)
    device: str                   # "cuda" | "cpu"
    seconds: float                # inference wall time (model loading excluded)
    load_seconds: float = 0.0
    note: str = ""                # e.g. "ran on the CPU: <why the GPU failed>"
    engine: str = ""


_DLLS_DONE = False


def cuda_dlls() -> list[str]:
    """Make NVIDIA's pip-installed CUDA 12 DLLs (cuBLAS, cuDNN; ``site-packages/nvidia/*/bin``) findable on Windows:
    CTranslate2 (faster-whisper) loads them by name. Returns the folders added."""
    global _DLLS_DONE
    if _DLLS_DONE or os.name != "nt":
        return []
    _DLLS_DONE = True
    added = []
    for base in {sys.prefix, os.path.dirname(os.path.dirname(os.__file__))}:
        for d in glob.glob(os.path.join(base, "Lib", "site-packages", "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except (OSError, AttributeError):
                continue
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            added.append(d)
    return added


def torch_cuda() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - no torch: no GPU for the torch engines
        return False


def gpu_name() -> str:
    try:
        import torch
        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else ""
    except Exception:  # noqa: BLE001
        return ""


def _free() -> None:
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------------------------------------------------
# engines
# ---------------------------------------------------------------------------------------------------------------------

class Engine:
    name = ""
    timed = True                  # gives word timings

    def __init__(self) -> None:
        self.model: Any = None
        self.device = ""

    def available(self) -> str | None:
        return None

    def load(self, device: str) -> None:
        raise NotImplementedError

    def run(self, y16: np.ndarray, hints: Sequence[str]) -> list[Word]:
        raise NotImplementedError

    def score(self, y16: np.ndarray, texts: Sequence[str]) -> list[float]:
        raise RuntimeError(f"{self.name} cannot score a text against audio")

    def unload(self) -> None:
        self.model = None
        self.device = ""
        _free()


class FasterWhisper(Engine):
    def __init__(self, model_id: str):
        super().__init__()
        self.name = self.model_id = model_id

    def available(self) -> str | None:
        try:
            import faster_whisper  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return f"faster-whisper is not installed ({type(e).__name__}: {e}): pip install faster-whisper"
        return None

    def load(self, device: str) -> None:
        if device == "cuda":
            cuda_dlls()
        from faster_whisper import WhisperModel
        self.model = WhisperModel(self.model_id, device=device, compute_type="float16" if device == "cuda" else "int8",
                                  cpu_threads=max(1, os.cpu_count() or 1))
        self.device = device

    def run(self, y16: np.ndarray, hints: Sequence[str]) -> list[Word]:
        from .transcribe import words_from_rows
        hot = ", ".join(dict.fromkeys(h for h in hints if h)) or None
        segments, _info = self.model.transcribe(
            np.ascontiguousarray(y16, np.float32), language="en", word_timestamps=True, vad_filter=True, beam_size=5, condition_on_previous_text=False, hotwords=hot)
        rows = [{"word": w.word, "start": float(w.start), "end": float(w.end),
                 "prob": float(getattr(w, "probability", 1.0))} for s in segments for w in (s.words or [])]
        return words_from_rows(rows)

    def score(self, y16: np.ndarray, texts: Sequence[str]) -> list[float]:
        """Each text's summed token log-probability as the transcript of y16 (at most 30 s), teacher-forced."""
        from faster_whisper.audio import pad_or_trim
        from faster_whisper.tokenizer import Tokenizer
        m = self.model
        tok = Tokenizer(m.hf_tokenizer, m.model.is_multilingual, task="transcribe", language="en")
        feats = m.feature_extractor(np.ascontiguousarray(y16, np.float32))[..., :-1]
        n = min(int(feats.shape[-1]), int(m.feature_extractor.nb_max_frames))
        enc = m.encode(pad_or_trim(feats))
        out = []
        for t in texts:
            ids = tok.encode(" " + str(t).strip())
            if not ids:
                out.append(0.0)
                continue
            p = np.asarray(m.model.align(enc, tok.sot_sequence, [ids], n)[0].text_token_probs, float)
            out.append(float(np.log(np.maximum(p, 1e-9)).sum()))
        return out


def _join_tokens(tokens: Sequence[dict]) -> list[Word]:
    """[{token, start, end}] (sub-word pieces; a piece starting with a space starts a word) -> words."""
    words: list[list] = []
    for t in tokens:
        piece = str(t["token"])
        if not piece.strip():
            continue
        if words and not piece.startswith(" "):        # a piece without a space continues the word ("X" "-" "F")
            words[-1][0] += piece.strip()
            words[-1][2] = max(words[-1][2], float(t["end"]))
        else:
            words.append([piece.strip(), float(t["start"]), float(t["end"])])
    out = []
    for raw, s, e in words:
        text = clean_text(raw)
        if any(ch.isalnum() for ch in text):
            out.append(Word(text, s, max(e, s), 1.0, raw))
    return out


class ParakeetHF(Engine):
    name = "parakeet-tdt-0.6b-v3"

    def __init__(self, repo: str = "nvidia/parakeet-tdt-0.6b-v3"):
        super().__init__()
        self.repo = repo

    def available(self) -> str | None:
        try:
            import librosa  # noqa: F401 - the feature extractor needs it
            import transformers  # noqa: F401
            from transformers import ParakeetForTDT  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return f"needs transformers>=5 and librosa ({type(e).__name__}: {e})"
        return None

    def load(self, device: str) -> None:
        import torch
        from transformers import AutoProcessor, ParakeetForTDT
        self.proc = AutoProcessor.from_pretrained(self.repo)
        dtype = torch.bfloat16 if device == "cuda" else torch.float32
        self.model = ParakeetForTDT.from_pretrained(self.repo, dtype=dtype).to(device).eval()
        self.dtype, self.device = dtype, device

    def run(self, y16: np.ndarray, hints: Sequence[str]) -> list[Word]:
        import torch
        inp = self.proc(np.asarray(y16, np.float32), sampling_rate=SR, return_tensors="pt").to(self.device)
        inp["input_features"] = inp["input_features"].to(self.dtype)
        with torch.inference_mode():
            out = self.model.generate(**inp, return_dict_in_generate=True)
        _text, ts = self.proc.decode(out.sequences, durations=out.durations, skip_special_tokens=True)
        return _join_tokens([t for t in ts[0] if t["token"] not in ("<blank>", "<pad>")])


class NemoParakeet(Engine):
    name = "parakeet-tdt-0.6b-v2"

    def __init__(self, repo: str = "nvidia/parakeet-tdt-0.6b-v2"):
        super().__init__()
        self.repo = repo

    def available(self) -> str | None:
        try:
            import nemo.collections.asr  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return f"NVIDIA NeMo is not installed ({type(e).__name__}: {str(e)[:120]})"
        return None

    def load(self, device: str) -> None:
        import torch
        import nemo.collections.asr as nemo_asr
        self.model = nemo_asr.models.ASRModel.from_pretrained(self.repo, map_location=torch.device(device)).eval()
        self.device = device

    def run(self, y16: np.ndarray, hints: Sequence[str]) -> list[Word]:
        import torch
        with torch.inference_mode():
            out = self.model.transcribe([np.asarray(y16, np.float32)], timestamps=True, batch_size=1, verbose=False)
        hyp = out[0] if isinstance(out, list) else out[0][0]
        rows = (getattr(hyp, "timestamp", None) or {}).get("word") or []
        words = []
        for r in rows:
            text = clean_text(str(r["word"]))
            if any(ch.isalnum() for ch in text):
                words.append(Word(text, float(r["start"]), max(float(r["end"]), float(r["start"])), 1.0,
                                  str(r["word"])))
        return words


def quiet_splits(y16: np.ndarray, max_s: float = CHUNK_S, sr: int = SR) -> list[tuple[int, int]]:
    """[a, b) sample ranges of at most max_s seconds, split at the quietest 50 ms of the last third of each."""
    n = len(y16)
    step = int(0.05 * sr)
    out, a = [], 0
    while n - a > int(max_s * sr):
        lo, hi = a + int(max_s * sr * 0.66), a + int(max_s * sr)
        win = np.asarray(y16[lo:hi], np.float32)
        k = len(win) // step
        rms = np.sqrt(np.mean(win[:k * step].reshape(k, step) ** 2, axis=1)) if k else np.zeros(1)
        cut = lo + int(np.argmin(rms)) * step + step // 2
        out.append((a, cut))
        a = cut
    out.append((a, n))
    return out


class CanaryQwen(Engine):
    name = "canary-qwen-2.5b"
    timed = False

    def __init__(self, repo: str = "nvidia/canary-qwen-2.5b"):
        super().__init__()
        self.repo = repo

    def available(self) -> str | None:
        try:
            from nemo.collections.speechlm2.models import SALM  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return f"NVIDIA NeMo (speechlm2) is not installed ({type(e).__name__}: {str(e)[:120]})"
        return None

    def load(self, device: str) -> None:
        import torch
        from nemo.collections.speechlm2.models import SALM
        m = SALM.from_pretrained(self.repo)
        self.model = m.to(device).to(torch.bfloat16 if device == "cuda" else torch.float32).eval()
        self.device = device

    def run(self, y16: np.ndarray, hints: Sequence[str]) -> list[Word]:
        import torch
        words: list[Word] = []
        prompt = [[{"role": "user", "content": f"Transcribe the following: {self.model.audio_locator_tag}"}]]
        for a, b in quiet_splits(y16):
            piece = np.asarray(y16[a:b], np.float32)
            if len(piece) < SR // 10 or float(np.max(np.abs(piece))) < 1e-4:
                continue
            audios = torch.from_numpy(piece)[None].to(self.device)
            lens = torch.tensor([len(piece)], device=self.device)
            with torch.inference_mode():
                ids = self.model.generate(prompts=prompt, audios=audios, audio_lens=lens,
                                          max_new_tokens=int(20 + 8 * len(piece) / SR))
            text = self.model.tokenizer.ids_to_text(ids[0].cpu())
            t0, t1 = a / SR, b / SR
            toks = [t for t in str(text).split() if any(ch.isalnum() for ch in t)]
            for i, tok in enumerate(toks):          # untimed: spread over the piece until align.py times them
                s = t0 + (t1 - t0) * i / max(1, len(toks))
                words.append(Word(clean_text(tok), s, s, 1.0, tok))
        return words


class CohereTranscribe(Engine):
    name = "cohere-transcribe"
    timed = False
    repo = "CohereLabs/cohere-transcribe-03-2026"

    def available(self) -> str | None:
        try:
            from transformers import AutoProcessor  # noqa: F401
            from huggingface_hub import hf_hub_download
            hf_hub_download(self.repo, "config.json")
        except Exception as e:  # noqa: BLE001
            if "Gated" in type(e).__name__ or "401" in str(e) or "403" in str(e):
                return (f"gated on Hugging Face: accept its terms at https://huggingface.co/{self.repo} and log in "
                        "with `hf auth login`, then run again")
            return f"{type(e).__name__}: {str(e)[:160]}"
        return None

    def load(self, device: str) -> None:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
        self.proc = AutoProcessor.from_pretrained(self.repo)
        dtype = torch.bfloat16 if device == "cuda" else torch.float32
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(self.repo, dtype=dtype).to(device).eval()
        self.dtype, self.device = dtype, device

    def run(self, y16: np.ndarray, hints: Sequence[str]) -> list[Word]:
        import torch
        words: list[Word] = []
        for a, b in quiet_splits(y16):
            piece = np.asarray(y16[a:b], np.float32)
            inp = self.proc(piece, sampling_rate=SR, return_tensors="pt").to(self.device)
            for k, v in list(inp.items()):
                if hasattr(v, "dtype") and v.dtype.is_floating_point:
                    inp[k] = v.to(self.dtype)
            with torch.inference_mode():
                ids = self.model.generate(**inp, max_new_tokens=int(20 + 8 * len(piece) / SR))
            text = self.proc.batch_decode(ids, skip_special_tokens=True)[0]
            t0, t1 = a / SR, b / SR
            toks = [t for t in str(text).split() if any(ch.isalnum() for ch in t)]
            for i, tok in enumerate(toks):
                s = t0 + (t1 - t0) * i / max(1, len(toks))
                words.append(Word(clean_text(tok), s, s, 1.0, tok))
        return words


FACTORIES = {
    "small.en": lambda: FasterWhisper("small.en"),
    "medium.en": lambda: FasterWhisper("medium.en"),
    "large-v3": lambda: FasterWhisper("large-v3"),
    "large-v3-turbo": lambda: FasterWhisper("large-v3-turbo"),
    "parakeet-tdt-0.6b-v3": ParakeetHF,
    "parakeet-tdt-0.6b-v2": NemoParakeet,
    "canary-qwen-2.5b": CanaryQwen,
    "cohere-transcribe": CohereTranscribe,
}
_LOADED: dict[str, Engine] = {}


def engine(name: str) -> Engine:
    if name not in FACTORIES:
        return FasterWhisper(name)               # any other faster-whisper model name
    return FACTORIES[name]()


def available(name: str) -> str | None:
    return engine(name).available()


def _ensure(name: str, device: str = "auto") -> tuple[Engine, str, float]:
    """Engine ``name`` loaded -- the GPU when there is one, the CPU after a GPU failure (one model in the GPU's
    memory at a time): (the engine, a note when it fell back, the seconds loading took)."""
    eng = _LOADED.get(name)
    want = ("cuda" if (torch_cuda() or _ct2_cuda(name)) else "cpu") if device == "auto" else device
    note = ""
    load_s = 0.0
    if eng is None or (device != "auto" and eng.device != want):
        for other in list(_LOADED):                # one model in the GPU's memory at a time
            _LOADED.pop(other).unload()
        eng = engine(name)
        why = eng.available()
        if why:
            raise RuntimeError(f"{name}: {why}")
        t = time.perf_counter()
        try:
            eng.load(want)
        except Exception as e:  # noqa: BLE001 - the GPU failed: the CPU, said clearly
            if want != "cuda":
                raise
            note = f"ran on the CPU: the GPU failed to load it ({type(e).__name__}: {str(e)[:160]})"
            eng.unload()
            eng.load("cpu")
        load_s = time.perf_counter() - t
    return eng, note, load_s


def transcribe(y16: np.ndarray, name: str, hints: Sequence[str] = (), device: str = "auto",
               keep_loaded: bool = True) -> Result:
    """Words of y16 (16 kHz mono) by engine ``name``; the GPU when there is one, the CPU after a GPU failure."""
    eng, note, load_s = _ensure(name, device)
    t = time.perf_counter()
    try:
        words = eng.run(y16, hints)
    except Exception as e:  # noqa: BLE001
        if eng.device != "cuda":
            raise
        note = f"ran on the CPU: the GPU failed while transcribing ({type(e).__name__}: {str(e)[:160]})"
        eng.unload()
        eng.load("cpu")
        t = time.perf_counter()
        words = eng.run(y16, hints)
    secs = time.perf_counter() - t
    if keep_loaded:
        _LOADED[name] = eng
    else:
        eng.unload()
    return Result(words, eng.timed, eng.device, secs, load_s, note, name)


def score_texts(y16: np.ndarray, texts: Sequence[str], name: str, device: str = "auto", keep_loaded: bool = True
                ) -> list[float]:
    """How likely engine ``name`` finds each text as what is said in y16 (16 kHz mono, at most 30 s): its tokens'
    summed log-probability, teacher-forced -- for choosing between two readings of the same audio. The Whisper
    engines only (the others raise)."""
    eng, _note, _load_s = _ensure(name, device)
    try:
        return eng.score(y16, texts)
    finally:
        if keep_loaded:
            _LOADED[name] = eng
        else:
            eng.unload()


def _ct2_cuda(name: str) -> bool:
    if not isinstance(engine(name), FasterWhisper):
        return False
    try:
        cuda_dlls()
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def unload_all() -> None:
    for other in list(_LOADED):
        _LOADED.pop(other).unload()


@dataclass
class Spec:
    """What the end summary says about the speech recognition of a run."""
    engine: str
    device: str
    gpu: str = ""
    notes: list[str] = field(default_factory=list)
