"""asr_bench.py: compare the speech-recognition engines (asr.py) on the answer-key videos of the test library --
``python -m match_cuts.asr_bench [--engines a,b] [--out DIR]``.

For every case with an answer key (testcases.py) the audio the user captioned is rebuilt: the RAW's audio along the
timeline answer.srt is timed on. Each engine transcribes it (on the GPU), and is measured against the user's
captions:

* **word errors** (WER): wrong + extra + missing words / the user's words -- ``plain`` (lower case, punctuation
  dropped: what the captions would show) and ``normalised`` (Whisper's English normaliser on both sides: spelling
  variants, numbers and "gonna" / "going to" style differences forgiven -- the hearing alone);
* **timing**: each caption's first word, where the engine (or forced alignment, align.py) puts it against where the
  user started the caption: the median and 90th percentile of the difference, and the share within one and two
  frames of the 60 fps sequence;
* **speed**: seconds of audio per second of work, on the GPU (model loading apart).
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from . import asr, testcases
from .caption_score import Timeline, load_captions, norm_words, word_edits
from .captions import Word

SR = 16000
FRAME_S = 1 / 60.0
ENGINES = ["small.en", "medium.en", "large-v3", "large-v3-turbo", "parakeet-tdt-0.6b-v2", "parakeet-tdt-0.6b-v3",
           "canary-qwen-2.5b", "cohere-transcribe"]


def _normaliser():
    try:
        from whisper_normalizer.english import EnglishTextNormalizer
        n = EnglishTextNormalizer()
        return lambda s: n(s).split()
    except Exception:  # noqa: BLE001 - not installed: the plain words
        return norm_words


def key_audio(case: testcases.Case, cache: Path) -> tuple[np.ndarray, Timeline]:
    """The audio the user captioned: the RAW (and, on another video's stretch, the competitor) along the key's
    timeline, 16 kHz mono, t = 0 at the key's 0."""
    from .media import extract_audio
    tl = testcases.answer_timeline(case)
    srcs: dict[str, np.ndarray] = {}
    for kind, path in (("raw", case.raw), ("comp", case.competitor)):
        if any(p.kind == kind for p in tl.pieces):
            f = cache / f"{case.name}_{kind}16k.npy"
            if not f.is_file():
                f.parent.mkdir(parents=True, exist_ok=True)
                np.save(f, extract_audio(str(path), sr=SR, mono=True).astype(np.float32))
            srcs[kind] = np.load(f)
    n = int(np.ceil(tl.duration * SR))
    y = np.zeros(n, np.float32)
    for p in tl.pieces:
        src = srcs[p.kind]
        a, b = int(round(p.t0 * SR)), int(round(p.t1 * SR))
        s0 = int(round(p.src * SR))
        piece = src[max(0, s0): max(0, s0) + (b - a)]
        y[a:a + len(piece)] = piece                      # a later piece overwrites an overlap (as Timeline.at)
    return y, tl


def reference(case: testcases.Case) -> list[dict]:
    """The user's captions: [{text, start, words}] (``*sounds*`` left out)."""
    out = []
    for c in load_captions(case.answer_srt):
        t = c.text.replace("\n", " ").strip()
        if t.startswith("*") and t.endswith("*"):
            continue
        out.append({"text": t, "start": c.start, "end": c.end, "words": norm_words(t)})
    return out


def timing_errors(ref: Sequence[dict], words: Sequence[Word], cuts: Sequence[float] = (),
                  at_cut: bool | None = False) -> list[float]:
    """For each reference caption whose first word the engine heard (matched in order): engine start - caption start
    (seconds). ``at_cut``: False -- only captions that do not start on a cut of the key's timeline (``cuts``; one that
    does starts there, whenever its word begins), True -- only those, None -- all."""
    rw, owner = [], []
    for i, r in enumerate(ref):
        for k, w in enumerate(r["words"]):
            rw.append(w)
            owner.append((i, k))
    hw, hidx = [], []
    for j, w in enumerate(words):
        for t in norm_words(w.raw or w.text):
            hw.append(t)
            hidx.append(j)
    sm = SequenceMatcher(None, rw, hw, autojunk=False)
    errs = []
    for a, b, n in sm.get_matching_blocks():
        for d in range(n):
            i, k = owner[a + d]
            on_cut = any(abs(ref[i]["start"] - c) <= FRAME_S + 1e-6 for c in cuts)
            if k == 0 and (at_cut is None or on_cut == at_cut):
                errs.append(words[hidx[b + d]].start - ref[i]["start"])
    return errs


def timing_stats(errs: Sequence[float]) -> dict:
    if not errs:
        return {"n": 0}
    ab = sorted(abs(e) for e in errs)
    return {"n": len(errs), "median_ms": round(1000 * statistics.median(ab), 1),
            "p90_ms": round(1000 * ab[min(len(ab) - 1, int(0.9 * len(ab)))], 1),
            "bias_ms": round(1000 * statistics.median(errs), 1),
            "within_1f": round(100 * sum(e <= FRAME_S + 1e-6 for e in ab) / len(ab), 1),
            "within_2f": round(100 * sum(e <= 2 * FRAME_S + 1e-6 for e in ab) / len(ab), 1)}


def wer(ref_words: Sequence[str], hyp_words: Sequence[str]) -> dict:
    s, i, d = word_edits(list(ref_words), list(hyp_words))
    n = max(1, len(ref_words))
    return {"wer": round(100 * (s + i + d) / n, 2), "sub": s, "ins": i, "del": d, "n": len(ref_words)}


def run(engines: Sequence[str], cases: Sequence[testcases.Case], out: Path, hints: Sequence[str] = (),
        align_words: bool = True) -> dict:
    from . import align
    norm = _normaliser()
    cache = out / "audio"
    data = {"cases": {}, "engines": {}, "gpu": asr.gpu_name()}
    audio = {}
    for c in cases:
        y, tl = key_audio(c, cache)
        ref = reference(c)
        cuts = sorted({round(p.t0, 4) for p in tl.pieces} - {0.0})
        audio[c.name] = (y, ref, cuts)
        data["cases"][c.name] = {"seconds": round(len(y) / SR, 2), "captions": len(ref),
                                 "words": sum(len(r["words"]) for r in ref)}
    for name in engines:
        eng = asr.engine(name)
        why = eng.available()
        row: dict[str, Any] = {"available": why is None, "why": why, "cases": {}}
        data["engines"][name] = row
        if why:
            print(f"{name}: not run -- {why}", flush=True)
            continue
        try:
            asr.transcribe(np.zeros(SR, np.float32), name, hints)          # load + warm up (kernels compiled)
        except Exception as e:  # noqa: BLE001
            row.update(available=False, why=f"{type(e).__name__}: {str(e)[:300]}")
            print(f"{name}: failed -- {row['why']}", flush=True)
            asr.unload_all()
            continue
        tot_s = tot_a = 0.0
        all_plain, all_norm = ([], []), ([], [])
        errs_raw, errs_al, errs_on = [], [], []
        for cname, (y, ref, cuts) in audio.items():
            res = asr.transcribe(y, name, hints)
            tot_s += res.seconds
            tot_a += len(y) / SR
            hyp_text = " ".join(w.raw or w.text for w in res.words)
            ref_text = " ".join(r["text"] for r in ref)
            rp, hp = [w for r in ref for w in r["words"]], norm_words(hyp_text)
            rn, hn = norm(ref_text), norm(hyp_text)
            all_plain[0].extend(rp), all_plain[1].extend(hp)
            all_norm[0].extend(rn), all_norm[1].extend(hn)
            e_raw = timing_errors(ref, res.words, cuts) if res.timed else []
            cr = {"device": res.device, "note": res.note, "seconds": round(res.seconds, 3),
                  "plain": wer(rp, hp), "normalised": wer(rn, hn), "text": hyp_text,
                  "timing_engine": timing_stats(e_raw)}
            errs_raw += e_raw
            if align_words and align.available() is None and res.words:
                aw, ainfo = align.align(y, res.words, timed=res.timed)
                e_al = timing_errors(ref, aw, cuts)
                ow = align.refine_onsets(y, aw)
                e_on = timing_errors(ref, ow, cuts)
                cr["timing_at_cuts"] = timing_stats(timing_errors(ref, ow, cuts, at_cut=True))
                errs_al += e_al
                errs_on += e_on
                cr.update(timing_aligned=timing_stats(e_al), timing_onsets=timing_stats(e_on), align=ainfo)
            row["cases"][cname] = cr
            print(f"{name} {cname}: {res.device} {res.seconds:.2f}s  WER plain {cr['plain']['wer']}% "
                  f"norm {cr['normalised']['wer']}%  timing {cr['timing_engine'].get('median_ms')} / "
                  f"aligned {cr.get('timing_aligned', {}).get('median_ms')} / onsets "
                  f"{cr.get('timing_onsets', {}).get('median_ms')} ms", flush=True)
        row["device"] = res.device
        row["load_s"] = round(res.load_seconds, 1)
        row["speed_x"] = round(tot_a / max(1e-6, tot_s), 1)
        row["plain"] = wer(*all_plain)
        row["normalised"] = wer(*all_norm)
        row["timing_engine"] = timing_stats(errs_raw)
        row["timing_aligned"] = timing_stats(errs_al)
        row["timing_onsets"] = timing_stats(errs_on)
        asr.unload_all()
    (out / "asr_bench.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    (out / "asr_bench.md").write_text(table(data), encoding="utf-8")
    return data


def table(data: dict) -> str:
    cases = list(data["cases"])
    head = ["engine", "device", "WER plain", "WER normalised"] + [f"{c} (plain)" for c in cases] + \
           ["timing: engine", "timing: aligned", "timing: aligned + onsets", "speed (x real time)"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]

    def tm(t: dict) -> str:
        if not t or not t.get("n"):
            return "-"
        return f"{t['median_ms']:.0f} ms ({t['within_1f']:.0f} % in 1 frame, {t['within_2f']:.0f} % in 2)"

    for name, r in data["engines"].items():
        if not r.get("available"):
            lines.append(f"| {name} | not run: {r.get('why')} |" + " |" * (len(head) - 2))
            continue
        cells = [name, r.get("device", ""), f"{r['plain']['wer']:.1f} %", f"{r['normalised']['wer']:.1f} %"]
        cells += [f"{r['cases'][c]['plain']['wer']:.1f} %" for c in cases]
        cells += [tm(r.get("timing_engine")), tm(r.get("timing_aligned")), tm(r.get("timing_onsets")),
                  f"{r['speed_x']:.0f}x"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m match_cuts.asr_bench")
    ap.add_argument("--engines", default=",".join(ENGINES))
    ap.add_argument("--cases", default="")
    ap.add_argument("--out", default=str(testcases.REPO / "work" / "asr_bench"))
    ap.add_argument("--hints", action="store_true", help="give the engines caption_allowlist.txt as hints")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cases = [c for c in testcases.cases([n for n in a.cases.split(",") if n] or None) if c.has_key]
    hints: list[str] = []
    if a.hints:
        from .caption_rules import read_allowlist
        hints = read_allowlist()
    t = time.time()
    data = run([e for e in a.engines.split(",") if e], cases, out, hints)
    print(table(data))
    print(f"took {time.time() - t:.0f} s; {out / 'asr_bench.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
