"""Real titles from the competitor's video (day 12, section 7) -- "Video by darkhumor" is useless:

* **Title from the post**: the post's own title / caption as yt-dlp reads it (Instagram, TikTok, YouTube: the title,
  else the description's first line), cleaned: no #hashtags, @names, links or emoji spam, and short;
* **Headline on the video**: the headline written ON the competitor's video, read from several frames of its first
  ~3 seconds by the vision model the tool already has (Qwen3-VL, match_cuts.action_captions.LocalVLM) -- its line
  break kept as two lines, readable emoji kept. The model runs on the graphics card, so it runs in its own process
  (``python -m mr_app.core.titles <video>``): the app never loads it, never freezes, and the memory is freed after;
* the project folder is named from the headline (else the post title) instead of "video-by-<user>" -- short and
  safe for Windows; both are saved in project.json ("post_title", "video_headline") and in titles_found.txt.

title.txt is NOT used for these two: it holds YOUR headline (or your upload title), and the board and the shared
folder read it back as yours -- writing the competitor's titles there would overwrite yours.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable, Sequence

TITLES_TXT = "titles_found.txt"
MAX_LEN = 90
FRAME_TIMES = (0.4, 1.1, 1.9, 2.8)          # the first ~3 seconds, several frames
READ_TIMEOUT_S = 600.0
NOTHING = "nothing readable"

_GENERIC = re.compile(r"^\s*(?:video|reel|post|photo|clip|short|tiktok|instagram|youtube|facebook)s?"
                      r"(?:\s+(?:by|from|of)\s+\S+)?\s*$|^\s*\S+\s+on\s+(?:tiktok|instagram|youtube)\b"
                      r"|^\s*(?:untitled|no title)\s*$", re.I)
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_TAG = re.compile(r"(?<![\w&])#[\wÀ-￿_]+", re.U)
_HANDLE = re.compile(r"(?<![\w.])@[\w.]+", re.U)


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF or cp in (0x200D, 0xFE0F)
            or 0x1F1E6 <= cp <= 0x1F1FF)


def _limit_emoji(text: str, keep: int = 3) -> str:
    """Emoji spam out: at most ``keep`` emoji in all, never more than 2 in a row."""
    out, total, run = [], 0, 0
    for ch in text:
        if _is_emoji(ch):
            if ord(ch) in (0x200D, 0xFE0F):              # a joiner / variation: kept with the emoji before it
                if out and run:
                    out.append(ch)
                continue
            run += 1
            total += 1
            if run > 2 or total > keep:
                continue
            out.append(ch)
        else:
            if not ch.isspace():
                run = 0
            out.append(ch)
    return "".join(out)


def _cut(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len + 1]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > max_len * 0.5 else cut[:max_len]).rstrip(" ,.;:-–—|") + "…"


def clean(text: str | None, max_len: int = MAX_LEN) -> str:
    """A post's title / caption made short and plain: no links, #hashtags, @names, emoji spam; one line; at most
    ``max_len`` characters (cut at a word)."""
    t = unicodedata.normalize("NFC", text or "")
    t = _URL.sub(" ", t)
    t = _TAG.sub(" ", t)
    t = _HANDLE.sub(" ", t)
    t = re.sub(r"\s*\|\s*$", "", t)
    first = next((ln for ln in t.splitlines() if re.search(r"\w", ln)), "")
    t = " ".join(first.split())
    t = _limit_emoji(t)
    t = re.sub(r"\s+([,.!?])", r"\1", t).strip(" -–—|:,;")
    return _cut(t, max_len)


def useless(text: str | None) -> bool:
    """A title that says nothing: empty, "Video by darkhumor", "Reel", "xyz on TikTok", an id, no letters."""
    t = (text or "").strip()
    if not t or _GENERIC.search(t):
        return True
    letters = sum(1 for c in t if c.isalpha())
    if letters < 3:
        return True
    return bool(re.fullmatch(r"[\w-]{6,40}", t) and not re.search(r"\s", t) and re.search(r"\d", t))


def post_title(info: dict | None) -> str:
    """The post's own title from yt-dlp's info (title, else the description's first line, else the full title),
    cleaned; '' when none says anything."""
    info = info or {}
    for key in ("title", "description", "fulltitle", "alt_title"):
        c = clean(str(info.get(key) or ""))
        if c and not useless(c):
            return c
    return ""


# ---------------------------------------------------------------------------------------------------------------------
# the headline written on the video (the vision model, in its own process)
# ---------------------------------------------------------------------------------------------------------------------

PROMPT = ("These are frames from the first seconds of a short video. Copy the headline text that is written on the "
          "video (the big caption, usually at the top), exactly as written. Write each of its lines on its own line, "
          "broken where the video breaks it. Keep its emoji. Write it once and do not add anything else. If there "
          "is no headline written on the video, answer NONE.")


def tidy_headline(answer: str | None) -> str:
    """The model's answer -> the headline: quotes and labels off, at most two lines (the break kept), '' for NONE."""
    t = unicodedata.normalize("NFC", answer or "").strip().strip('"“”\'`').strip()
    t = re.sub(r"^(?:headline|text|answer)\s*:\s*", "", t, flags=re.I)
    if not t or re.fullmatch(r"(?i)none\.?|no headline\.?|n/?a", t):
        return ""
    t = _drop_account(t)
    lines = [" ".join(_destutter(_HANDLE.sub(" ", x)).split()) for x in t.splitlines()]
    lines = [x.strip('"“”') for x in lines if x and re.search(r"\w", x) and not re.fullmatch(r"(?i)none\.?", x)]
    # the model going on after the headline ("... like this\nThis", the one real run on finished\video3): a last line
    # that repeats the start of the text before it, or is one word of it, is dropped
    while len(lines) >= 2 and _fold(lines[-1]):
        before = " ".join(_fold(x) for x in lines[:-1])
        last = _fold(lines[-1])
        if before.startswith(last) or (len(last.split()) == 1 and last in before.split() and
                                       len(before.split()) >= 3):
            lines.pop()
        else:
            break
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    out = "\n".join(_limit_emoji(x, keep=4) for x in lines)
    return out if re.search(r"[^\W\d_]", out) else ""


def _fold(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (text or "").lower()).split())


def _drop_account(text: str) -> str:
    """A tweet-style headline ("History Exposed ✔ / @HistoryyExposed / In 1996, ..."): the account's name and @handle
    above the text are not the headline -- dropped (e2e run, day 12: the model copied them as line 1)."""
    m = _HANDLE.search(text)
    if not m:
        return text
    before = text[:m.start()].strip()
    if not before or (len(before.split()) <= 4 and not re.search(r"[.!?,:;]", before) and "\n" not in before):
        return text[m.end():].strip()
    return text


def _destutter(line: str) -> str:
    """The small model's stutter ("had had exactly one one second of screen time time"): a word said twice in a row
    once."""
    out: list[str] = []
    for w in line.split(" "):
        if out and _fold(w) and _fold(w) == _fold(out[-1]) and not re.search(r"\d", w):
            out[-1] = w if len(w) > len(out[-1]) else out[-1]           # "time time." keeps the stop
            continue
        out.append(w)
    return " ".join(out)


def _emoji_of(text: str) -> str:
    return "".join(ch for ch in text if _is_emoji(ch))


def settle(model_text: str, ocr_lines: Sequence[str] | None) -> str:
    """The headline on the video from both readers: the vision model's answer and the title check's OCR lines.
    When both read the same text (close, not equal: the 2B model stutters -- "Tobeyey Maguire had had") the OCR's
    words win and the model's emoji are kept at the end; then broken where the video breaks it (``with_break``).
    The model alone / the OCR alone when the other read nothing; the model's text as it is when they differ (a
    different text, e.g. the OCR read a caption)."""
    import difflib
    ocr = tidy_headline(" ".join(" ".join(x.split()) for x in (ocr_lines or []) if x and x.strip()))
    if not model_text:
        return tidy_headline("\n".join(ocr_lines)) if ocr_lines and len(ocr_lines) == 2 else ocr
    if not ocr:
        return model_text
    a, b = _fold(model_text), _fold(ocr)
    if a != b and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8:
        emo = _emoji_of(model_text)
        fixed = ocr if not emo or emo in ocr else f"{ocr} {emo}"
        return with_break(fixed, ocr_lines) if "\n" not in fixed else fixed
    return with_break(model_text, ocr_lines)


def with_break(text: str, lines: Sequence[str] | None) -> str:
    """The model's headline on one line + the OCR's lines of it (title_check: where the video breaks it) -> the same
    text broken where the video breaks it (the emoji kept where the model put them). Unchanged when it has a break
    already, when the OCR did not read two lines, or when their words do not match."""
    if not text or "\n" in text or not lines or len(lines) != 2:
        return text
    n = len(_fold(lines[0]).split())
    toks = text.split(" ")
    seen = 0
    for i, tok in enumerate(toks):
        if _fold(tok):
            seen += 1
        if seen == n:
            head, tail = " ".join(toks[:i + 1]), " ".join(toks[i + 1:])
            if tail.strip() and _fold(head) == _fold(lines[0]):
                return head + "\n" + tail
            return text
    return text


def frames(video: str | Path, times_s: Sequence[float] = FRAME_TIMES, width: int = 720) -> list:
    """RGB pictures of the video at these seconds (at most ``width`` wide; times past its end left out)."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    out = []
    try:
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        dur = n / fps if fps else 0.0
        for t in times_s:
            if dur and t > dur:
                t = max(0.0, dur * 0.8)
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t)) * 1000.0)
            ok, f = cap.read()
            if not ok:
                continue
            h, w = f.shape[:2]
            if w > width:
                f = cv2.resize(f, (width, int(round(h * width / w))), interpolation=cv2.INTER_AREA)
            out.append(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()
    return out


def read_in_this_process(video: str | Path, model: Any = None) -> str:
    """The headline on the video, read here (the child process): the frames, then the model (LocalVLM, loaded here;
    ``model`` for a test: anything with ``ask(images, prompt) -> str``)."""
    pics = frames(video)
    if not pics:
        return ""
    if model is not None:
        return tidy_headline(model.ask(pics, PROMPT))
    import torch
    from PIL import Image

    from match_cuts.action_captions import LocalVLM
    vlm = LocalVLM()
    vlm._load()                                                       # noqa: SLF001 - the tool's own loader
    try:
        ins = {k: v.to(vlm.device) for k, v in vlm.inputs([Image.fromarray(p) for p in pics], PROMPT).items()}
        with torch.inference_mode():
            out = vlm._m.generate(**ins, max_new_tokens=60, do_sample=False)     # noqa: SLF001
        text = vlm._tok.decode(out[0][ins["input_ids"].shape[1]:], skip_special_tokens=True)  # noqa: SLF001
    finally:
        vlm.close()
    return tidy_headline(text)


def _child_command(video: Path) -> list[str]:
    from . import paths
    return [str(paths.python_exe()), "-m", "mr_app.core.titles", str(video)]


def _run_child(video: Path, timeout_s: float = READ_TIMEOUT_S, cancel: Any = None) -> str:
    """The reader in its own process (the model on the graphics card, freed when it ends); its JSON answer."""
    from . import paths
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[2]),
                                           str(paths.repo() / "tools" / "match_cuts"),
                                           os.environ.get("PYTHONPATH", "")]))
    r = subprocess.run(_child_command(Path(video)), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout_s, env=env, creationflags=0x08000000 if os.name == "nt" else 0)
    for line in reversed((r.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            d = json.loads(line)
            if d.get("error"):
                raise RuntimeError(d["error"])
            return str(d.get("headline") or "")
    raise RuntimeError("the headline reader said nothing" + (f": {r.stderr.strip()[-200:]}" if r.stderr else ""))


READER: Callable[[Path], str] = _run_child          # tests put a fake here: they never load the model


def read_on_video(video: str | Path) -> str:
    """The headline written on the video ('' when there is none or it cannot be read; never raises)."""
    try:
        return tidy_headline(READER(Path(video)))
    except Exception:  # noqa: BLE001 - a help, never a stop
        return ""


# ---------------------------------------------------------------------------------------------------------------------
# the project: names, project.json, titles_found.txt
# ---------------------------------------------------------------------------------------------------------------------

def name_from(headline: str | None, post: str | None) -> str:
    """The project's short name: from the headline on the video, else the post's title; '' when neither has
    letters (then the old name stays)."""
    from .projects import short_name
    for t in (headline, post):
        s = short_name(" ".join((t or "").split()))
        if s and len(s) >= 3:
            return s
    return ""


def save(project: Any, post: str | None, headline: str | None) -> None:
    """Both titles into project.json ("post_title", "video_headline") and titles_found.txt (plain words when nothing
    could be read)."""
    project.update(post_title=post or "", video_headline=headline or "")
    lines = [f"Title from the post: {post or NOTHING}",
             "Headline on the video:" + ("\n" + headline if headline else f" {NOTHING}")]
    try:
        (Path(project.folder) / TITLES_TXT).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def rename_folder(project: Any, headline: str | None, post: str | None) -> tuple[Any, str]:
    """The project folder named from the headline / post title (date kept: '2026-10-19_<name>', -2 ... when taken),
    when its name came from a useless title or a link id. Returns (the project -- reloaded when moved, what was
    done in plain words)."""
    from . import projects as P
    folder = Path(project.folder)
    want = name_from(headline, post)
    if not want:
        return project, "no title could be read from the post or the video -- the folder keeps its name"
    day = folder.name.split("_", 1)[0] if re.match(r"^\d{4}-\d{2}-\d{2}_", folder.name) else ""
    base = f"{day}_{want}" if day else want
    if folder.name == base or folder.name.startswith(base + "-"):
        return project, "the folder already has this name"
    k = 1
    while True:
        new = folder.with_name(base if k == 1 else f"{base}-{k}")
        if not new.exists():
            break
        k += 1
    try:
        os.replace(folder, new)
    except OSError as e:
        return project, f"the folder could not be renamed ({e.strerror or e}) -- it keeps its name"
    p = P.load(new)
    p.update(name=new.name, old_name=folder.name)
    return p, f"named after the {'headline on the video' if name_from(headline, None) else 'post title'}"


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m mr_app.core.titles VIDEO``: one JSON line {"headline": ...} (or {"error": ...})."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(json.dumps({"error": "give the video"}))
        return 2
    try:
        h = read_in_this_process(argv[0])
        print(json.dumps({"headline": h}, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001 - said to the app
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
