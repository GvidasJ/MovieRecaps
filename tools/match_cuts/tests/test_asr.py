"""asr.py / align.py / transcribe.py (task 4): the speech engines' common layer, the GPU -> CPU fallback, the
fallback model, forced-alignment helpers and the transcription cache -- with fake engines (no model download)."""
from __future__ import annotations

import numpy as np
import pytest

from match_cuts import align, asr, transcribe
from match_cuts.captions import Word

SR = 16000


class FakeEngine(asr.Engine):
    """Fails to load / run on the GPU when told to; returns fixed words."""
    loads: list[str] = []

    def __init__(self, fail_load=False, fail_run=False, timed=True):
        super().__init__()
        self.name, self.timed = "fake", timed
        self.fail_load, self.fail_run = fail_load, fail_run

    def load(self, device):
        FakeEngine.loads.append(device)
        if device == "cuda" and self.fail_load:
            raise RuntimeError("CUDA out of memory")
        self.model, self.device = object(), device

    def run(self, y16, hints):
        if self.device == "cuda" and self.fail_run:
            raise RuntimeError("CUBLAS_STATUS_NOT_SUPPORTED")
        return [Word("hello", 0.1, 0.4, 0.9, "Hello,"), Word("there", 0.5, 0.8, 0.8, "there.")]


@pytest.fixture
def fake(monkeypatch):
    FakeEngine.loads = []
    made = {}

    def factory(**kw):
        def make():
            made["e"] = FakeEngine(**kw)
            return made["e"]
        monkeypatch.setitem(asr.FACTORIES, "fake", make)
        monkeypatch.setattr(asr, "torch_cuda", lambda: True)
        asr.unload_all()
        return made
    yield factory
    asr.unload_all()


def test_gpu_failure_falls_back_to_the_cpu_and_says_so(fake):
    fake(fail_load=True)
    res = asr.transcribe(np.zeros(SR, np.float32), "fake")
    assert FakeEngine.loads == ["cuda", "cpu"] and res.device == "cpu"
    assert res.note.startswith("ran on the CPU: the GPU failed to load it (RuntimeError: CUDA out of memory")
    asr.unload_all()
    fake(fail_run=True)
    res = asr.transcribe(np.zeros(SR, np.float32), "fake")
    assert res.device == "cpu" and "failed while transcribing" in res.note


def test_gpu_when_it_works(fake):
    fake()
    res = asr.transcribe(np.zeros(SR, np.float32), "fake")
    assert res.device == "cuda" and res.note == "" and [w.text for w in res.words] == ["hello", "there"]
    again = asr.transcribe(np.zeros(SR, np.float32), "fake")          # kept loaded: no second load
    assert FakeEngine.loads == ["cuda"] and again.load_seconds == 0.0


def test_join_tokens_builds_words():
    toks = [{"token": "De", "start": 0.08, "end": 0.24}, {"token": "ad", "start": 0.24, "end": 0.32},
            {"token": "pool", "start": 0.32, "end": 0.56}, {"token": " the", "start": 1.12, "end": 1.28},
            {"token": " X", "start": 1.28, "end": 1.36}, {"token": "-", "start": 1.36, "end": 1.36},
            {"token": "Force", "start": 1.44, "end": 1.76}, {"token": ",", "start": 1.76, "end": 1.76}]
    ws = asr._join_tokens(toks)
    assert [(w.raw, w.start, w.end) for w in ws] == [("Deadpool", 0.08, 0.56), ("the", 1.12, 1.28),
                                                      ("X-Force,", 1.28, 1.76)]


def test_quiet_splits_cut_at_the_quietest_moment():
    y = np.random.default_rng(0).normal(0, 0.2, 70 * SR).astype(np.float32)
    y[int(25.0 * SR):int(25.3 * SR)] = 0.0                           # a pause near the end of the first 28 s
    parts = asr.quiet_splits(y, 28.0)
    assert len(parts) == 3 and parts[0][0] == 0 and parts[-1][1] == len(y)
    assert 25.0 * SR <= parts[0][1] <= 25.3 * SR
    assert all(b - a <= 28 * SR for a, b in parts)


def test_engine_names_and_cohere_gate(monkeypatch):
    assert isinstance(asr.engine("large-v3"), asr.FasterWhisper) and asr.engine("canary-qwen-2.5b").timed is False
    assert isinstance(asr.engine("tiny.en"), asr.FasterWhisper)       # any faster-whisper model
    import huggingface_hub

    class GatedRepoError(Exception):
        pass

    def gated(*a, **k):
        raise GatedRepoError("401 Client Error")
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", gated)
    assert "gated on Hugging Face" in asr.CohereTranscribe().available()


# ---- forced alignment helpers ---------------------------------------------------------------------------------------

def test_speakable_spells_numbers():
    assert align.spell(25) == "twenty five" and align.spell(1999) == "one thousand nine hundred ninety nine"
    assert align.speakable("25") == "twenty five"
    assert align.speakable("1999s") == "nineteen ninety nines"          # as said: "the nineties"
    assert align.speakable("X-Force,") == "x force" and align.speakable("I’m") == "i'm"
    assert align.speakable("50%") == "fifty percent" and align.speakable("$4,000") == "four thousand"


def test_phrases_split_at_pauses():
    ws = [Word("a", 0.0, 0.2), Word("b", 0.25, 0.4), Word("c", 1.0, 1.2), Word("d", 1.3, 1.5)]
    assert align.phrases(ws) == [[0, 1], [2, 3]]


def test_refine_onsets_moves_a_word_after_a_pause_onto_its_first_sound():
    y = np.zeros(2 * SR, np.float32)
    y[int(1.03 * SR):int(1.4 * SR)] = 0.3 * np.sin(np.arange(int(0.37 * SR)) * 0.3)    # the word starts at 1.03 s
    ws = [Word("x", 0.2, 0.5), Word("y", 1.06, 1.4)]                                  # aligned 30 ms late
    out = align.refine_onsets(y, ws)
    assert out[1].start == pytest.approx(1.03, abs=0.006) and out[0].start == 0.2


# ---- transcribe.py: the default, the fallback, the cache ------------------------------------------------------------

class Cache:
    def __init__(self, root):
        self.root = root

    def path(self, stage, key, ext):
        p = self.root / stage / f"{key}{ext}"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def test_transcribe_words_falls_back_to_the_earlier_default(tmp_path, monkeypatch):
    calls = []

    def fake_transcribe(y16, name, hints=(), device="auto", keep_loaded=True):
        calls.append((name, tuple(hints)))
        if name == "large-v3":
            raise RuntimeError("no such model here")
        return asr.Result([Word("hi", 0.1, 0.3, 0.9, "hi")], True, "cpu", 0.1, 0.0, "", name)
    monkeypatch.setattr(asr, "transcribe", fake_transcribe)
    monkeypatch.setattr(align, "available", lambda: "no torchaudio here")
    transcribe.LOG.clear()
    y = np.random.default_rng(1).normal(0, 0.1, SR).astype(np.float32)
    ws = transcribe.transcribe_words(y, SR, "large-v3", "en", Cache(tmp_path), hints=["MCU", "MCU", "AI"])
    assert [w.text for w in ws] == ["hi"] and calls == [("large-v3", ("MCU", "AI")), ("small.en", ("MCU", "AI"))]
    assert transcribe.LOG[-1]["model"] == "small.en" and "large-v3 could not run here" in transcribe.LOG[-1]["note"]
    assert any("small.en instead" in line for line in transcribe.summary())
    again = transcribe.transcribe_words(y, SR, "large-v3", "en", Cache(tmp_path), hints=["MCU", "AI"])
    assert [w.text for w in again] == ["hi"] and len(calls) == 2         # the cache: no second run


def test_summary_names_the_gpu(monkeypatch):
    monkeypatch.setattr(asr, "gpu_name", lambda: "NVIDIA GeForce RTX 5080")
    transcribe.LOG.clear()
    transcribe.LOG.append({"model": "large-v3", "asked": "large-v3", "device": "cuda", "note": "", "audio_s": 30.0,
                           "seconds": 1.5, "aligned": "cuda", "align_error": None})
    assert transcribe.summary() == ["large-v3 on the GPU (NVIDIA GeForce RTX 5080): 1 piece(s), 30 s of audio in 1.5 s; "
                                    "words timed by forced alignment on the GPU"]
    transcribe.LOG.clear()


def test_a_word_that_kept_its_own_time_goes_back_in_the_spoken_order():
    from match_cuts import align as A
    from match_cuts.captions import Word
    ws = [Word("team", 1.022, 1.303, 0.9, "team,"), Word("the", 1.595, 1.704, 0.9, "the"),
          Word("X-Force", 1.4, 2.16, 0.9, "X-Force.")]
    out = A._in_order(ws, [1])                                 # its alignment failed: Whisper's time, too late
    assert (out[1].start, out[1].end) == pytest.approx((1.303, 1.4))
    assert out[0] is ws[0] and out[2] is ws[2]
    assert A._in_order(ws, [0]) == ws                          # in its place already: untouched


def test_the_summary_says_when_the_words_came_from_the_cache(monkeypatch):
    from match_cuts import transcribe as T
    monkeypatch.setattr(T, "LOG", [{"model": "large-v3", "asked": "large-v3", "device": "cache", "note": "",
                                    "audio_s": 30.0, "load_s": 0.0, "seconds": 0.0, "aligned": None,
                                    "align_error": None}])
    assert T.summary() == ["large-v3: 1 piece(s), 30 s of audio, from the cache (transcribed by an earlier run with "
                           "the same audio, model and hints)"]
