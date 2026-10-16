"""action_captions.py: the ``*...*`` placeholders (a silence over ~1 s where you write the action) filled with a short
action in your style -- *claps hands*, *enters the room*, *laughs hard* -- as a SUGGESTION (night 9).

Six frames of what the edit shows during the placeholder (the RAW, through 1_edit.xml) and the line said just before
and just after it go to a vision model, which answers in your style -- the rules taken from your own action captions
(caption_actions.txt: the action captions of your finished projects and SRTs; ``learn`` adds the new ones of every
fix): 1-4 words, lower case, a verb in its -s form first, a feeling as one word. The answer is tidied to it (no
subject, no full stop, between asterisks). When the model is unsure -- it says so, its answer is no action or a
generic one (GENERIC: 'looks up', 'stares ahead'), its words were unlikely to it, or, asked again in other words
whether its suggestion is what the pictures show (CHECKS), it says no -- the placeholder stays. The suggestions are
listed under "Captions worth a look" ("a suggested action -- check it"), in the report and in captions.json
``actions``. ``--no-action-captions`` keeps every placeholder.

The model is a plug-in (ActionModel: ``describe(images, examples, context) -> (text, confidence)``): ``local`` runs
Qwen3-VL-2B (Apache-2.0; ``big``: the 4B) on this computer's graphics card -- free, nothing leaves the PC; an online
model is one more class in MODELS, chosen with ``--action-model`` (it gets your action captions as ``examples``).
Answers are cached (the RAW, the frames' times, the model, the prompt, the context).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

import numpy as np

EXAMPLES_FILE = Path(__file__).resolve().parents[1] / "caption_actions.txt"
MODEL_ID = "Qwen/Qwen3-VL-2B-Instruct"         # --action-model local (the default; 4.3 GB): every computer the same
MODEL_BIG = "Qwen/Qwen3-VL-4B-Instruct"        # --action-model big (8.9 GB): as often right on your 47 moments
#                                                 (night 9: 48 % of its suggestions right or close, the 2B's 48 %), but
#                                                 twice the memory -- next to a second run on the 16 GB card it spilled
#                                                 into shared memory: 9 min for 3 placeholders
FRAMES = 6                   # pictures of the placeholder's stretch (spread over it, the edges left out)
FRAME_W = 640                # each picture at most this wide
MIN_CONFIDENCE = 0.5         # the answer's words this likely to the model (mean token probability), else unsure
MIN_CHECK = 0.5              # asked again (CHECKS), the model's yes against its no at least this each time, else unsure
MAX_WORDS = 4                # your action captions: 1-4 words (*gets closer to friends*)
MAX_CHARS = 24               # ... the hard cap of every caption (captions.HARD_CAP)
PROMPT_VERSION = 5
NOT_ACTIONS = {"unsure", "unknown", "nothing", "none", "no action", "n/a", "na", "unclear", "nothing happens",
               "no", "yes", "person", "someone", "people", "man", "woman", "a person", "the person", "video", "scene",
               "image", "picture", "frame", "frames"}
SOUNDS = {"um", "umm", "ummm", "uh", "oh", "ah", "hmm"}        # your sound captions: not actions to see
# answers that say nothing a caption needs (the model's fallback when it sees no clear action): unsure
GENERIC = {"looks up", "looks down", "looks around", "looks away", "looks ahead", "looks forward", "looks at camera",
           "looks at the camera", "stares ahead", "stares blankly", "stares forward", "stares at camera",
           "stares at the camera", "stares at something", "stares at wall", "stares at the wall", "gazes ahead",
           "watches", "smiles at camera", "smiles at the camera", "gestures with hands", "gestures", "leans forward",
           "leans back", "leans in", "leans over", "reaches out", "extends hand", "extends hand forward",
           "raises hand", "stands still", "sits still", "stands", "sits", "points at camera", "points at the camera",
           "talks", "speaks", "listens", "waits", "moves", "turns around", "looks"}

# Your style as rules (from your action captions: 1-4 words, lower case, a verb in its -s form first, no subject, no
# full stop; a feeling as one word). The list of your captions itself is NOT in the local model's prompt: the small
# model then copied an example instead of looking (night 9: 'starts spraying' for 15 of your 46 moments); it is
# kept for a stronger (online) model -- ``examples`` of describe().
PROMPT = (
    "These pictures are frames of one short moment of a video, in order.{context} Look at what the main person "
    "does.\nDescribe it in 2 to 4 words, lower case, starting with a verb ending in -s, the way a caption describes an "
    "action (the form is like \"opens the door\" or \"sits down\").\nIf the person only shows a feeling, answer with "
    "one word for the feeling. If nothing clear happens, answer: unsure.\nAnswer with the words only.")
CONTEXT = " Just before it, someone says: \"{before}\". Just after it: \"{after}\"."
# The suggestion checked: the model asked twice, in other words, whether it is what the pictures show -- both answers
# must be yes. Night 9, on your 47 moments, the 2B's 27 suggestions -> 11: wrong 13 -> 2 (its fallback when lost,
# 'sits down', 6 -> 0), right or close 11 -> 8 (lost: 'walks in', and actions the frames do not show yet -- 'leaves
# the room' while the party still stands, 'puts out the candle' before it is out).
CHECKS = (
    "These pictures are frames of one short moment of a video, in order. Does the main person in them {action}? "
    "Answer yes or no.",
    "These pictures are frames of one short moment of a video, in order. Is this a good short caption for what "
    "happens: \"{action}\"? Answer yes or no.")


# ---------------------------------------------------------------------------------------------------------------------
# your examples
# ---------------------------------------------------------------------------------------------------------------------

def read_examples(path: Path | None = None) -> list[str]:
    """Your action captions (caption_actions.txt, one per line, '#' comments) without the asterisks."""
    p = Path(path or EXAMPLES_FILE)
    out: list[str] = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.split("#", 1)[0].strip().strip("*").strip()
            if ln and ln.lower() not in SOUNDS and ln not in out:
                out.append(ln)
    except OSError:
        pass
    return out


def action_texts(srt_text: str) -> list[str]:
    """The *...* captions of an SRT's text (your action captions), the placeholder left out."""
    out = []
    for ln in srt_text.splitlines():
        s = ln.strip()
        if len(s) > 2 and s.startswith("*") and s.endswith("*") and s != "*...*":
            out.append(s.strip("*").strip())
    return out


def add_examples(texts: Sequence[str], video: str, path: Path | None = None) -> list[str]:
    """learn: your new action captions appended to caption_actions.txt (the ones not there yet)."""
    p = Path(path or EXAMPLES_FILE)
    have = {x.lower() for x in read_examples(p)}
    new = []
    for t in texts:
        t = t.strip().strip("*").strip()
        if t and t.lower() not in have and t.lower() not in SOUNDS:
            new.append(t)
            have.add(t.lower())
    if new:
        old = p.read_text(encoding="utf-8") if p.is_file() else ""
        with p.open("a", encoding="utf-8", newline="\n") as f:
            if old and not old.endswith("\n"):
                f.write("\n")
            f.write(f"# from {video}\n" + "".join(f"*{t}*\n" for t in new))
    return new


def pick_examples(examples: Sequence[str], n: int = 30) -> list[str]:
    """A fixed, varied set for the prompt (short ones first, every first word once)."""
    seen, out = set(), []
    for e in sorted(examples, key=lambda s: (len(s.split()), s.lower())):
        w = e.split()[0].lower()
        if w in seen:
            continue
        seen.add(w)
        out.append(e)
        if len(out) >= n:
            break
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the answer in your style
# ---------------------------------------------------------------------------------------------------------------------

def tidy(answer: str) -> str | None:
    """The model's answer as your action caption ('*claps hands*'), or None when it is no action (unsure, empty, a
    sentence, a thing on its own)."""
    a = (answer or "").strip().splitlines()[0] if (answer or "").strip() else ""
    a = re.sub(r"(?i)^(caption|answer)\s*:\s*", "", a).strip()
    a = a.strip().strip("\"'`*").strip()
    a = re.sub(r"[.!?;:]+$", "", a).strip().strip("*").strip()
    a = re.sub(r"\s+", " ", a)
    if not a or not re.search(r"[A-Za-z]", a):
        return None
    low = a.lower()
    if low in NOT_ACTIONS or low.startswith(("unsure", "i cannot", "i can't", "there is", "the image", "the frames",
                                             "this is", "a video", "it is")):
        return None
    words = low.split()
    if words and words[0] in ("he", "she", "they", "it", "the", "a", "an") and len(words) > 1:
        words = words[1:]                          # "he claps hands" -> "claps hands" (yours have no subject)
    if not words or len(words) > MAX_WORDS or "," in low:
        return None
    text = " ".join(words)
    if len(text) + 2 > MAX_CHARS or text in GENERIC:
        return None
    return f"*{text}*"


# ---------------------------------------------------------------------------------------------------------------------
# the models (plug-ins)
# ---------------------------------------------------------------------------------------------------------------------

class ActionModel(Protocol):
    name: str

    def describe(self, images: Sequence[np.ndarray], examples: Sequence[str], context: str = "") -> tuple[str, float]:
        """(the answer, its confidence 0-1) for RGB pictures (uint8, H x W x 3) of one moment; ``examples``: your
        action captions (the style), ``context``: what is said just before and after it. A model may also have
        ``check(images, action) -> 0-1``: how sure it is, asked again, that the action is what the pictures show
        (LocalVLM.check); a model without it: its suggestions are not checked."""
        ...


class LocalVLM:
    """MODEL_ID on this computer (GPU when there is one): greedy, the answer's mean token probability as confidence."""

    def __init__(self, model_id: str = MODEL_ID):
        self.name = model_id
        self._m = None
        self._p = None
        self.device = "cpu"

    def _load(self) -> None:
        """The model, its tokenizer, its image processor (the plain one: no torchvision needed) and its chat template --
        the model's own processor is not used: it wants a video processor, which needs torchvision."""
        if self._m is not None:
            return
        import torch
        from huggingface_hub import hf_hub_download
        from transformers import AutoImageProcessor, AutoModelForImageTextToText, AutoTokenizer
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        self._tok = AutoTokenizer.from_pretrained(self.name)
        self._img = AutoImageProcessor.from_pretrained(self.name, use_fast=False)
        tmpl = Path(hf_hub_download(self.name, "chat_template.json")).read_text(encoding="utf-8")
        self._tmpl = json.loads(tmpl)["chat_template"]
        self._m = AutoModelForImageTextToText.from_pretrained(self.name, dtype=dtype).to(self.device).eval()
        self._p = True

    def inputs(self, images: Sequence[Any], text: str) -> dict:
        """The model's inputs for pictures and a question, as its processor makes them: the chat template, each image
        placeholder repeated once per image token (its patch grid / merge size squared), the pixels."""
        msgs = [{"role": "user", "content": [{"type": "image"} for _ in images] + [{"type": "text", "text": text}]}]
        prompt = self._tok.apply_chat_template(msgs, chat_template=self._tmpl, tokenize=False,
                                               add_generation_prompt=True)
        img = self._img(images=list(images), return_tensors="pt")
        merge = int(self._img.merge_size) ** 2
        for thw in img["image_grid_thw"]:
            prompt = prompt.replace("<|image_pad|>", "<|placeholder|>" * int(thw.prod().item() // merge), 1)
        prompt = prompt.replace("<|placeholder|>", "<|image_pad|>")
        enc = self._tok(prompt, return_tensors="pt")
        return {**enc, "pixel_values": img["pixel_values"], "image_grid_thw": img["image_grid_thw"]}

    def describe(self, images: Sequence[np.ndarray], examples: Sequence[str], context: str = "") -> tuple[str, float]:
        import torch
        from PIL import Image
        self._load()
        pics = [Image.fromarray(np.asarray(im)) for im in images]
        inputs = {k: v.to(self.device) for k, v in self.inputs(pics, PROMPT.format(context=context)).items()}
        with torch.inference_mode():
            out = self._m.generate(**inputs, max_new_tokens=16, do_sample=False, return_dict_in_generate=True,
                                   output_scores=True)
        n_in = inputs["input_ids"].shape[1]
        seq = out.sequences[0][n_in:]
        text = self._tok.decode(seq, skip_special_tokens=True).strip()
        probs = []
        for tok, sc in zip(seq.tolist(), out.scores):
            if tok in (self._tok.eos_token_id, getattr(self._tok, "pad_token_id", None)):
                continue
            probs.append(torch.softmax(sc[0].float(), dim=-1)[tok].item())
        conf = float(np.mean(probs)) if probs else 0.0
        return text, conf

    def check(self, images: Sequence[np.ndarray], action: str) -> float:
        """The model asked again, in other words (CHECKS), whether ``action`` ('claps hands') is what the pictures
        show: the smaller of its two yes-against-no probabilities of the answer's first word."""
        import torch
        from PIL import Image
        self._load()
        pics = [Image.fromarray(np.asarray(im)) for im in images]
        ids = lambda ws: {self._tok.encode(w, add_special_tokens=False)[0] for w in ws}   # noqa: E731
        yes_ids, no_ids = ids(("Yes", "yes", " Yes", " yes")), ids(("No", "no", " No", " no"))
        got = []
        for q in CHECKS:
            inputs = {k: v.to(self.device) for k, v in self.inputs(pics, q.format(action=action)).items()}
            with torch.inference_mode():        # (generate, one word: it makes the pictures' position ids itself)
                out = self._m.generate(**inputs, max_new_tokens=1, do_sample=False, return_dict_in_generate=True,
                                       output_scores=True)
            pr = torch.softmax(out.scores[0][0].float(), dim=-1)
            y, n = sum(pr[i].item() for i in yes_ids), sum(pr[i].item() for i in no_ids)
            got.append(y / (y + n) if y + n > 0 else 0.0)
        return float(min(got))

    def close(self) -> None:
        if self._m is not None:
            import gc
            import torch
            self._m = None
            self._p = None
            self._tok = self._img = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()


MODELS: dict[str, Callable[[], Any]] = {"local": LocalVLM}     # an online model: add its class here (--action-model)
#                                                                 ("big": MODEL_BIG; a Hugging Face id: that model)


def make_model(name: str) -> Any:
    import os
    if name in ("", "none", "off") or os.environ.get("MATCH_CUTS_NO_ACTION_MODEL", "") not in ("", "0"):
        return None                                   # (the unit tests: no 4 GB model per run)
    if name == "local":
        return LocalVLM(MODEL_ID)
    if name == "big":
        return LocalVLM(MODEL_BIG)
    if name in MODELS:
        return MODELS[name]()
    if "/" in name:                                   # another Hugging Face model id, run locally
        return LocalVLM(name)
    raise ValueError(f"--action-model {name}: not one of {', '.join(MODELS)} (or a Hugging Face model id)")


# ---------------------------------------------------------------------------------------------------------------------
# the frames a placeholder shows
# ---------------------------------------------------------------------------------------------------------------------

def frame_times(a: int, b: int, fps: Fraction, n: int = FRAMES) -> list[float]:
    """n sequence times (s) spread over frames [a, b): the middles of n equal parts."""
    t0, t1 = a / float(fps), b / float(fps)
    return [t0 + (k + 0.5) * (t1 - t0) / n for k in range(n)]


def xml_media(xml: Path) -> Path | None:
    """The RAW file 1_edit.xml plays (its first video <pathurl> that is not the frame PNG)."""
    import urllib.parse
    import xml.etree.ElementTree as ET
    for el in ET.parse(xml).getroot().iter("pathurl"):
        u = urllib.parse.urlparse(el.text or "")
        p = urllib.parse.unquote(u.path)
        if re.match(r"^/[A-Za-z]:", p):
            p = p[1:]
        if not p.lower().endswith((".png", ".jpg", ".jpeg")):
            return Path(p)
    return None


def raw_frames(raw: Path, times_s: Sequence[float]) -> list[np.ndarray]:
    """RGB pictures of the RAW at these seconds (at most FRAME_W wide)."""
    import cv2
    cap = cv2.VideoCapture(str(raw))
    out = []
    try:
        for t in times_s:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t)) * 1000.0)
            ok, f = cap.read()
            if not ok:
                continue
            h, w = f.shape[:2]
            if w > FRAME_W:
                f = cv2.resize(f, (FRAME_W, int(round(h * FRAME_W / w))), interpolation=cv2.INTER_AREA)
            out.append(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()
    return out


# ---------------------------------------------------------------------------------------------------------------------
# a run's placeholders
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Suggestion:
    start: int
    end: int
    text: str | None              # '*claps hands*', None: the placeholder stays
    answer: str
    confidence: float
    why: str = ""
    check: float | None = None    # the model's yes when asked again (CHECKS); None: not asked


def decide(answer: str, confidence: float, check: float | None = None) -> tuple[str | None, str]:
    """(the suggestion in your style, '') -- or (None, why the placeholder stays): no clear action (none, unsure, a
    generic one: tidy), its words unlikely to the model, or the model doubting it when asked again (``check``)."""
    text = tidy(answer)
    if not text:
        return None, "no clear action"
    if confidence < MIN_CONFIDENCE:
        return None, "the model was unsure"
    if check is not None and check < MIN_CHECK:
        return None, f"the model doubted it when asked again (yes {check:.2f})"
    return text, ""


def context_of(before: str, after: str) -> str:
    """The prompt's words about what is said around the moment ('' when nothing is)."""
    b, a = " ".join(str(before or "").split()), " ".join(str(after or "").split())
    return CONTEXT.format(before=b or "-", after=a or "-") if (b or a) else ""


def suggest(spots: Sequence[tuple[int, int]], fps: Fraction, xml: Path, model: Any, examples: Sequence[str],
            cache: Any = None, raw: Path | None = None, contexts: Sequence[str] | None = None) -> list[Suggestion]:
    """A suggestion for each placeholder [a, b) (sequence frames of 1_edit.xml at ``fps``); ``contexts``: for each,
    what is said around it (context_of)."""
    from .caption_score import Timeline
    from .common import file_hash
    tl = Timeline.from_xml(xml)
    raw = raw or xml_media(xml)
    ex = pick_examples(examples)
    out = []
    code = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12]
    raw_hash = file_hash(raw) if raw is not None and raw.is_file() else ""
    for n, (a, b) in enumerate(spots):
        ctx_text = contexts[n] if contexts is not None and n < len(contexts) else ""
        ts = []
        for t in frame_times(a, b, fps):
            m = tl.at(t + 1e-4)
            if m is not None and m[0] == "raw":
                ts.append(round(float(m[1]), 3))
        if raw is None or not raw.is_file() or len(ts) < 2:
            out.append(Suggestion(a, b, None, "", 0.0, "the edit shows no RAW picture there"))
            continue

        def ask(ts=ts, ctx_text=ctx_text) -> dict:
            imgs = raw_frames(raw, ts)
            if len(imgs) < 2:
                return {"answer": "", "confidence": 0.0, "why": "the RAW's frames could not be read"}
            ans, conf = model.describe(imgs, ex, ctx_text)
            chk = None
            if decide(ans, conf)[0] and callable(getattr(model, "check", None)):   # a suggestion: asked again
                chk = round(float(model.check(imgs, decide(ans, conf)[0].strip("*"))), 4)
            return {"answer": ans, "confidence": round(float(conf), 4), "check": chk, "why": ""}
        if cache is not None and raw_hash:
            # the key holds this module's own code fingerprint (``code``), the model, the prompt and its examples --
            # not common.STAGE_CODE: that is for the analysis stages, and editing common.py recomputes them all
            key = hashlib.sha1(json.dumps(["action_captions", raw_hash, ts, model.name, PROMPT, list(ex), ctx_text,
                                           list(CHECKS), PROMPT_VERSION, code]).encode("utf-8")).hexdigest()[:40]
            got = cache.json("action_captions", key, ask)
        else:
            got = ask()
        ans, conf = str(got.get("answer") or ""), float(got.get("confidence") or 0.0)
        chk = None if got.get("check") is None else float(got["check"])
        text, why = decide(ans, conf, chk)
        out.append(Suggestion(a, b, text, ans, conf, got.get("why") or why, chk))
    return out


def rewrite_srt(srt: Path, filled: dict[tuple[int, int], str], fps: Fraction) -> int:
    """2_captions.srt with the placeholders at (start ms, end ms) replaced by their suggestion; how many."""
    from .captions import frame_ms
    text = srt.read_text(encoding="utf-8")
    blocks = re.split(r"(\r?\n\r?\n)", text)
    n = 0
    want = {(frame_ms(a, fps), frame_ms(b, fps)): s for (a, b), s in filled.items()}
    for i, bl in enumerate(blocks):
        lines = bl.splitlines()
        if len(lines) >= 3 and "-->" in lines[1] and lines[2].strip() == "*...*":
            m = re.match(r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)", lines[1].strip())
            if not m:
                continue
            g = [int(x) for x in m.groups()]
            s_ms = ((g[0] * 60 + g[1]) * 60 + g[2]) * 1000 + g[3]
            e_ms = ((g[4] * 60 + g[5]) * 60 + g[6]) * 1000 + g[7]
            new = want.get((s_ms, e_ms))
            if new:
                lines[2] = new
                blocks[i] = "\n".join(lines) + ("\n" if bl.endswith("\n") else "")
                n += 1
    srt.write_text("".join(blocks), encoding="utf-8")
    return n


def fill_run(ctx: Any) -> dict:
    """The run's placeholders (ctx.captions, written 2_captions.srt) filled with suggestions; captions.json and the
    SRT rewritten. Returns {model, suggested: [...], kept: [...], error?}."""
    from .captions import ms_tc, frame_ms
    from .common import dump_json, log
    from .run_folders import CAPTIONS_SRT, EDIT_XML
    cfg = ctx.cfg
    cap = ctx.captions or {}
    fps = Fraction(str(cap.get("fps") or 60))
    spots = [(int(c["start"]), int(c["end"])) for c in cap.get("captions") or [] if c.get("mode") == "placeholder"]
    info: dict = {"model": str(getattr(cfg, "action_model", "local") or "local"), "suggested": [], "kept": []}
    if not spots:
        return info
    xml = cfg.deliver / EDIT_XML
    srt = cfg.deliver / CAPTIONS_SRT
    model = make_model(info["model"])
    if model is None:
        return info
    info["model"] = model.name
    try:                                   # the caption step's speech models are done: their memory for this one
        from . import align, asr
        asr.unload_all()
        align.unload()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()           # (and what this run's process keeps cached: the other runs see it free)
    except Exception:  # noqa: BLE001 - only memory
        pass
    caps = sorted(cap.get("captions") or [], key=lambda c: int(c["start"]))
    said = [c for c in caps if c.get("mode") not in ("placeholder", "action")
            and not str(c.get("text", "")).startswith("*")]
    contexts = []
    for a, b in spots:                     # the line said just before the placeholder and the one just after it
        before = [c for c in said if int(c["end"]) <= a]
        after = [c for c in said if int(c["start"]) >= b]
        contexts.append(context_of(before[-1]["text"] if before else "", after[0]["text"] if after else ""))
    try:
        got = suggest(spots, fps, xml, model, read_examples(), getattr(ctx, "cache", None), contexts=contexts)
    finally:
        close = getattr(model, "close", None)
        if close is not None:
            close()
    filled = {(s.start, s.end): s.text for s in got if s.text}
    for s in got:
        row = {"start_tc": ms_tc(frame_ms(s.start, fps)), "end_tc": ms_tc(frame_ms(s.end, fps)), "text": s.text,
               "answer": s.answer, "confidence": round(s.confidence, 3), "why": s.why,
               "check": None if s.check is None else round(s.check, 3)}
        (info["suggested"] if s.text else info["kept"]).append(row)
    if filled and srt.is_file():
        rewrite_srt(srt, filled, fps)
        for c in cap.get("captions") or []:
            t = filled.get((int(c["start"]), int(c["end"])))
            if c.get("mode") == "placeholder" and t:
                c.update(text=t, mode="action", suggested=True)
        cap["placeholders"] = [c for c in cap.get("captions") or [] if c.get("mode") == "placeholder"]
    cap["actions"] = info
    dump_json(cap, cfg.debug_dir / "captions.json")
    log.info("captions: %d of %d *...* placeholder(s) filled with a suggested action (%s): %s", len(filled),
             len(spots), info["model"], "; ".join(f"{r['start_tc']} {r['text']} ({r['confidence']:.2f})"
                                                  for r in info["suggested"]) or "none sure enough")
    return info
