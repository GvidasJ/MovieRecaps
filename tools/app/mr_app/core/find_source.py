"""FIND SOURCE (night 15, task 12): which movie or episode a competitor's clip comes from -- only its name (and season /
episode), not the part or the frame. You download the RAW yourself.

What is used (no login, no cookies, no channel of yours):

1. **the post's text** -- the title, the description and the tags of the link (yt-dlp reads them without
   downloading); a file's own name;
2. **your RAWs**: the movies and episodes this computer already has (the projects' RAWs in the shared folder, the cases
   in D:\\Shared\\laptop_cases) -- a name the post's text says (or a show it says, with the episode the file is) is a
   guess; then **the clip's sound** is looked for in that RAW (the tool's own quick sound check, match_cuts.pair_check):
   found = "sure";
3. **public title lists** (TVmaze for shows, IMDb's search suggestions for both; no key) for a name the text says that
   is not on this computer: the show or film it is, with its year -- "check" (only the text says it).

How sure (``Answer.verdict``): SURE (the sound found in your RAW: 90 %+), CHECK (the text names it; the second guess
shown when there is one), NOT FOUND (with the reason, in plain words). The burned-in text and the speech are not used
yet: no service matches them without a login (said in the reason when nothing else found it).
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

SURE, CHECK, NOT_FOUND = "sure", "check", "not found"
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".ts"}
MIN_RAW_BYTES = 50 * 1024 ** 2              # a file this big or bigger can be a RAW (a clip is a few MB)
HTTP_TIMEOUT_S = 8.0
HISTORY_FILE = "find_source.json"
HISTORY_MAX = 200
TEXT_PICKS = 4                              # your RAWs the text names: the sound looked for in this many at most ...
SCAN_MAX = 12                               # ... then (nothing found) in this many of your other RAWs, episodes first,
SCAN_BUDGET_S = 240.0                       #     for this long at most
UA = {"User-Agent": "MovieRecaps/1 (find source)"}

_QUALITY = re.compile(r"\b(?:2160p|1080p|720p|480p|4k|uhd|bluray|blu-ray|brrip|bdrip|web-?dl|webrip|web|hdtv|dvdrip|"
                      r"x264|x265|h ?264|h ?265|hevc|aac\d?(?: ?\d ?\d)?|ddp?\d?(?: \d)?|10bit|proper|extended|remux|"
                      r"yts\w*|yify|bone|rarbg|psa|lama|flux|amzn|nf|atvp|dsnp|hmax|sub|ch|silence)\b", re.I)
_SE = re.compile(r"(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})(?!\d)", re.I)
_X = re.compile(r"(?<![0-9])(\d{1,2})x(\d{2,3})(?![0-9])")
_YEAR = re.compile(r"(?<![0-9])(19[2-9]\d|20[0-4]\d)(?![0-9])")
_LINK = re.compile(r"^https?://", re.I)


# ---------------------------------------------------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------------------------------------------------

def norm(text: str) -> str:
    """Letters and digits only, lower case, accents off: 'American Dad!' -> 'americandad'."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in t if ch.isalnum() and ord(ch) < 0x2000).lower()


@dataclass
class Title:
    """A movie or an episode, as a file name or a list says it."""
    name: str                                   # "American Dad!" / "Dr. Dolittle 2"
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str = ""
    kind: str = "movie"                         # "movie" | "episode" | "show" (a show, the episode not known)

    def text(self) -> str:
        if self.season is not None and self.episode is not None:
            return f"{self.name} — S{self.season:02d}E{self.episode:02d}"
        if self.kind == "show":
            return self.name
        return f"{self.name} ({self.year})" if self.year else self.name

    def search_words(self) -> str:
        """What to type in a torrent search: 'American Dad S03E02 1080p', 'Dr Dolittle 2 2001 1080p'."""
        base = re.sub(r"[^\w\s]", " ", self.name)
        if self.season is not None and self.episode is not None:
            base += f" S{self.season:02d}E{self.episode:02d}"
        elif self.year:
            base += f" {self.year}"
        return " ".join(base.split()) + " 1080p"


def _tidy(s: str) -> str:
    s = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", s)
    s = s.replace("_", " ")
    s = re.sub(r"(?<=[^\s.])\.(?=[^\s.])", " ", s)           # dots between words: Dr..Dolittle.2 -> Dr. Dolittle 2
    s = re.sub(r"\.{2,}", ". ", s)
    return " ".join(s.split())


def parse_name(file_name: str) -> Title | None:
    """A RAW's file name -> the title it is ('American Dad! - S03E02 - The American Dad After School Special - [HDTV]'
    -> American Dad! S03E02 'The American Dad After School Special'; 'Dr..Dolittle.2.2001.1080p.BluRay' -> Dr. Dolittle
    2 (2001)); None for a name that says nothing ('scene', 'raw')."""
    stem = Path(file_name).stem
    stem = re.sub(r"^_?\d*FIXED", "", stem)
    s = _tidy(stem)
    m = _SE.search(s) or _X.search(s)
    if m:
        show = s[: m.start()].strip(" -–.()")
        after = _QUALITY.split(s[m.end():], maxsplit=1)[0]
        after = re.sub(r"\(.*$", "", after).strip(" -–.()")
        show = _YEAR.sub("", show).strip(" -–.()") or show
        if not norm(show):
            return None
        return Title(show, None, int(m.group(1)), int(m.group(2)), after, "episode")
    y = None
    for my in _YEAR.finditer(s):
        if my.start() > 0:
            y = my
            break
    if y is not None:
        name = s[: y.start()].strip(" -–.()")
        if norm(name):
            return Title(name, int(y.group(1)), kind="movie")
    name = _QUALITY.split(s, maxsplit=1)[0].strip(" -–.()")
    if len(norm(name)) < 4 or norm(name) in ("scene", "raw", "rawtest", "competitor", "video"):
        return None
    return Title(name, None, kind="movie")


# ---------------------------------------------------------------------------------------------------------------------
# your RAWs (the library)
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class LibItem:
    title: Title
    file: str                                   # the RAW's name (as you downloaded it)
    sound: str = ""                             # a file of it to check the sound in ("" = none here)


def library(roots: Iterable[Path] | None = None) -> list[LibItem]:
    """The movies and episodes this computer has: each project's RAW (project.json; its sound: the run's own copy or
    sound file) and every big video in the case folders (D:\\Shared\\laptop_cases)."""
    from . import projects as P
    from .settings import load as load_settings, save_root
    out: list[LibItem] = []
    seen: set[str] = set()
    if roots is None:
        try:
            hub = Path(save_root(load_settings()))
        except Exception:  # noqa: BLE001
            hub = Path(r"D:\Shared\Projects")
        roots = [hub, Path(r"D:\Shared\laptop_cases")]
    for root in roots:
        try:
            folders = [p for p in Path(root).iterdir() if p.is_dir()]
        except OSError:
            continue
        for folder in folders:
            files: list[tuple[str, str]] = []
            pj = folder / P.FILE
            if pj.is_file():
                try:
                    raw = (P.read_json(pj).get("raw") or {}).get("file") or ""
                except Exception:  # noqa: BLE001
                    raw = ""
                if raw:
                    media = folder / "edit" / "extras" / "media"
                    snd = next((str(media / n) for n in ("raw_stereo.wav", "raw_stereo.mp4", "raw.wav")
                                if (media / n).is_file()), "")
                    if not snd:
                        snd = next((str(p) for p in sorted(media.glob("raw*")) if p.suffix.lower() in VIDEO_EXTS), "")
                    if not snd and (folder / raw).is_file():
                        snd = str(folder / raw)
                    files.append((raw, snd))
            try:
                for f in folder.iterdir():
                    if f.suffix.lower() in VIDEO_EXTS and f.is_file() and f.stat().st_size >= MIN_RAW_BYTES:
                        files.append((f.name, str(f)))
            except OSError:
                pass
            for name, snd in files:
                t = parse_name(name)
                if t is None:
                    continue
                key = norm(t.text())
                if key in seen:
                    for it in out:                             # one per title; keep one with a sound to check
                        if norm(it.title.text()) == key and not it.sound and snd:
                            it.sound = snd
                    continue
                seen.add(key)
                out.append(LibItem(t, name, snd))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the post's text
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Clip:
    source: str                                 # the link or the file, as given
    name: str = ""                              # what to call it: "Reel DdVZePAga2I", "tiktok 7357048330179841323"
    platform: str = ""                          # YouTube / Instagram / TikTok / file
    length: float | None = None
    text: str = ""                              # the post's title, description and tags (or the file's name)


def platform_of(source: str) -> str:
    s = source.lower()
    if "tiktok" in s:
        return "TikTok"
    if "instagram" in s:
        return "Instagram"
    if "youtu" in s:
        return "YouTube"
    return "file" if not _LINK.match(source) else "link"


def read_clip(source: str, cancel: threading.Event | None = None) -> Clip:
    """The clip's post text (a link: yt-dlp, nothing downloaded, no cookies) or a file's own name and length."""
    source = source.strip()
    c = Clip(source=source, platform=platform_of(source))
    if _LINK.match(source):
        from . import download as D
        li = D.probe_link(source, cancel=cancel)
        info = li.info or {}
        kind = "Reel" if c.platform == "Instagram" else ("Short" if c.platform == "YouTube" else c.platform)
        c.name = f"{kind} {li.id}" if li.id else (li.title or source)
        c.length = li.duration
        tags = " ".join(f"#{t}" for t in (info.get("tags") or [])[:40])
        c.text = "\n".join(x for x in (li.title, info.get("description") or "", tags, info.get("track") or "") if x)
    else:
        p = Path(source)
        c.name = p.stem
        m = re.search(r"\[([\w-]{11})\]", p.stem)                 # yt-dlp's name: '<title> [DdVZePAga2I]'
        t = re.match(r"tiktok_[^_]*_(\d{8,})", p.stem, re.I)
        if t:
            c.name, c.platform = f"TikTok {t.group(1)}", "TikTok (file)"
        elif m:
            c.name, c.platform = f"Reel {m.group(1)}", "file"
        c.text = p.stem.replace("_", " ")
        try:
            from match_cuts.cli import quick_probe
            c.length = float(quick_probe(p)["duration"])
        except Exception:  # noqa: BLE001
            c.length = None
        side = p.with_suffix(".txt")                    # a post text saved next to the file (the Download tab's)
        for t in (side, p.parent / "titles_found.txt", p.parent / "title.txt"):
            try:
                if t.is_file():
                    c.text += "\n" + t.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                pass
    return c


def episode_in(text: str) -> tuple[int, int] | None:
    m = _SE.search(text or "") or _X.search(text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def named_in(text: str, lib: list[LibItem]) -> list[LibItem]:
    """The library's titles the post's text names (the show / film name inside the text, letters only, so
    "#americandad" and "American Dad!" both count), the longest names first."""
    hay = norm(text)
    hits = [it for it in lib if len(norm(it.title.name)) >= 4 and norm(it.title.name) in hay]
    return sorted(hits, key=lambda it: -len(norm(it.title.name)))


_FROM = re.compile(r"(?:from|in|scene of|clip of|on)\s+(?:the\s+)?(?:movie|film|show|series|tv show\s+)?\s*"
                   r"([A-Z][\w'’:&!.-]*(?:\s+(?:[A-Z0-9][\w'’:&!.-]*|of|the|and|a|in|to|on))*)")
_HASH = re.compile(r"#(\w{4,})")
_NOT_TITLES = {"fyp", "foryou", "foryoupage", "viral", "funny", "comedy", "movie", "movies", "film", "shorts", "reels",
               "tiktok", "trending", "explore", "scene", "scenes", "clip", "clips", "edit", "edits", "meme", "memes",
               "funnyvideos", "moviescene", "moviescenes", "moviemoments", "cinema", "netflix", "lol", "hilarious"}


def candidates(text: str) -> list[str]:
    """Names the post's text may be a title in ('from American Dad', 'Dr. Dolittle 2 (2001)', '#americandad')."""
    out: list[str] = []
    for m in _FROM.finditer(text or ""):
        c = m.group(1).strip(" .!-")
        if len(c) >= 3 and norm(c) not in _NOT_TITLES:
            out.append(c)
    for m in re.finditer(r"([A-Z][\w'’:&.!-]*(?:\s+[\w'’:&.!-]+){0,5})\s*\((19[2-9]\d|20[0-4]\d)\)", text or ""):
        out.append(f"{m.group(1)} ({m.group(2)})")
    for m in _HASH.finditer(text or ""):
        if norm(m.group(1)) not in _NOT_TITLES:
            out.append(m.group(1))
    seen, uniq = set(), []
    for c in out:
        if norm(c) not in seen:
            seen.add(norm(c))
            uniq.append(c)
    return uniq[:6]


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:       # noqa: S310 - fixed public https hosts
        return json.loads(r.read().decode("utf-8", "replace"))


def online(name: str, ep: tuple[int, int] | None = None) -> Title | None:
    """A name looked up in the public lists: IMDb's search suggestions (a film or a show, its year); TVmaze for a
    show's episode title. None when nothing close is found."""
    q = re.sub(r"\((\d{4})\)", r" \1", name).strip()
    if not norm(q):
        return None
    try:
        first = norm(q)[0]
        d = _get(f"https://v2.sg.media-imdb.com/suggestion/{first}/{urllib.parse.quote(q.lower())}.json")
    except Exception:  # noqa: BLE001 - no network: nothing from the lists
        return None
    for it in d.get("d") or []:
        qid = it.get("qid") or ""
        if qid not in ("movie", "tvSeries", "tvMiniSeries", "tvMovie"):
            continue
        title = it.get("l") or ""
        if not title or not (norm(title) in norm(q) or norm(q.split(" (")[0]) in norm(title)):
            continue
        year = it.get("y")
        if qid.startswith("tv") and qid != "tvMovie":
            t = Title(title, year, kind="show")
            if ep is not None:
                t.season, t.episode, t.kind = ep[0], ep[1], "episode"
                try:
                    show = _get(f"https://api.tvmaze.com/singlesearch/shows?q={urllib.parse.quote(title)}")
                    e = _get(f"https://api.tvmaze.com/shows/{show['id']}/episodebynumber?season={ep[0]}&number={ep[1]}")
                    t.episode_title = e.get("name") or ""
                except Exception:  # noqa: BLE001
                    pass
            return t
        return Title(title, year, kind="movie")
    return None


# ---------------------------------------------------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Answer:
    clip: Clip
    title: Title | None = None
    verdict: str = NOT_FOUND
    sure: float = 0.0                            # 0..1
    how: str = ""                               # what found it: "your RAW's sound", "the post's text" ...
    second: Title | None = None
    reason: str = ""                            # NOT FOUND: why, in plain words
    raw_file: str = ""                          # your RAW it was found in

    def comes_from(self) -> str:
        return self.title.text() if self.title is not None else "—"

    def line(self, k: int) -> str:
        """'1. Reel DdVZePAga2I — Dr. Dolittle 2 (2001) — sure 97 %' (Copy all)."""
        if self.verdict == NOT_FOUND or self.title is None:
            return f"{k}. {self.clip.name} — not found ({self.reason})"
        word = "sure" if self.verdict == SURE else "check"
        extra = f" (or {self.second.text()})" if self.second is not None and self.verdict == CHECK else ""
        return f"{k}. {self.clip.name} — {self.comes_from()}{extra} — {word} {round(100 * self.sure)} %"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Answer":
        t = d.get("title")
        s = d.get("second")
        return cls(Clip(**d["clip"]), Title(**t) if t else None, d.get("verdict", NOT_FOUND), float(d.get("sure", 0)),
                   d.get("how", ""), Title(**s) if s else None, d.get("reason", ""), d.get("raw_file", ""))


def _sound_found(clip_file: Path, raw_sound: str) -> float | None:
    """The share of the clip's sound pieces found in that RAW (the tool's quick sound check); None: not checked."""
    try:
        from match_cuts import pair_check as PC
        cy = PC.decode_audio(clip_file)
        ry = PC.decode_audio(raw_sound)
        side = PC.sound_check(cy, ry)
    except Exception:  # noqa: BLE001
        return None
    if side.state == PC.FOUND:
        return float(side.share or 0.0)
    return 0.0


def find(source: str, lib: list[LibItem] | None = None, work: Path | None = None,
         cancel: threading.Event | None = None, say: Callable[[str], None] | None = None) -> Answer:
    """Which movie / episode one clip comes from (module docstring). Never raises: a clip that can not be read is NOT
    FOUND with why."""
    say = say or (lambda _t: None)
    try:
        say("reading the post")
        clip = read_clip(source, cancel)
    except Exception as e:  # noqa: BLE001
        from . import download as D
        msg = D.plain_error(str(e)) if hasattr(D, "plain_error") else str(e)
        return Answer(Clip(source=source, name=source[-40:], platform=platform_of(source)),
                      reason=f"the link could not be read: {msg}"[:200])
    lib = library() if lib is None else lib
    ep = episode_in(clip.text)
    named = named_in(clip.text, lib)
    # the library's titles the text names: the episode it says first, then the show's other files
    picks: list[LibItem] = []
    for it in named:
        t = it.title
        if ep is not None and t.season == ep[0] and t.episode == ep[1]:
            picks.insert(0, it)
        else:
            picks.append(it)
    ans = Answer(clip)
    clip_file = _clip_file(clip, work, cancel) if any(it.sound for it in lib) else None
    ruled_out: list[LibItem] = []
    t_scan = time.monotonic()

    def try_sound(items: list[LibItem], limit: int, budget_s: float | None = None) -> bool:
        for it in items:
            if clip_file is None or len(ruled_out) >= limit:
                return False
            if budget_s is not None and time.monotonic() - t_scan > budget_s:
                return False
            if not it.sound or it in ruled_out:
                continue
            say(f"looking for the clip's sound in {it.title.text()}")
            share = _sound_found(clip_file, it.sound)
            if share is not None and share >= 0.5:
                ans.title, ans.verdict, ans.raw_file = it.title, SURE, it.file
                ans.sure = min(0.99, 0.90 + 0.09 * share)
                ans.how = f"its sound found in your RAW ({round(100 * share)} % of it)" + (
                    "; the post's text names it" if it in named else "")
                return True
            if share is not None:
                ruled_out.append(it)
        return False

    found = try_sound(picks, TEXT_PICKS)
    if not found:                                  # the last try: the clip's sound in your other RAWs
        rest = [it for it in lib if it not in picks]
        rest.sort(key=lambda it: (it.title.kind != "episode", it.title.text()))
        found = try_sound(rest, TEXT_PICKS + SCAN_MAX, SCAN_BUDGET_S)
    if not found and picks:
        best = picks[0]
        out = [it for it in ruled_out if it in picks]
        if best.title.kind == "episode" and ep is None and len(out) == len(picks):
            # every episode of the show here checked: not one of them -- the show, not an episode of yours
            ans.title = Title(best.title.name, kind="show")
            ans.verdict, ans.sure = CHECK, 0.6
            eps = ", ".join(f"S{it.title.season:02d}E{it.title.episode:02d}" for it in out)
            ans.how = f"the post's text names the show; the episode is not one of your {len(out)} ({eps}) -- " \
                      f"its sound is not in them"
        else:
            ans.title, ans.verdict, ans.sure = best.title, CHECK, 0.75 if ep is not None else 0.65
            ans.raw_file = best.file
            if len(picks) > 1:
                ans.second = picks[1].title
            ans.how = "the post's text names it" + (
                " -- its sound was not found in your RAW of it" if best in ruled_out else "")
    if ans.title is None:
        say("looking the name up")
        for c in candidates(clip.text):
            t = online(c, ep)
            if t is not None:
                if ans.title is None:
                    ans.title, ans.verdict, ans.sure = t, CHECK, 0.6 if ep is not None or t.kind == "movie" else 0.5
                    ans.how = f"the post's text names it (\"{c}\")"
                elif ans.second is None and norm(t.name) != norm(ans.title.name):
                    ans.second = t
                    break
    if ans.title is None:
        ans.reason = ("the post's text names no movie or show I know"
                      if clip.text.strip() else "the post has no text to read") + \
                     " -- the burned-in text and the speech can not be looked up without a login"
    return ans


def _clip_file(clip: Clip, work: Path | None, cancel: threading.Event | None) -> Path | None:
    """The clip itself (a link: downloaded once into work\\find_source)."""
    if not _LINK.match(clip.source):
        p = Path(clip.source)
        return p if p.is_file() else None
    from . import download as D, paths
    dest = Path(work or paths.work_dir()) / "find_source"
    try:
        return Path(D.download(clip.source, dest, "competitor", cancel=cancel).path)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------------------------------------------------
# history (survives a restart)
# ---------------------------------------------------------------------------------------------------------------------

def history_path() -> Path:
    from .settings import app_dir
    return app_dir() / HISTORY_FILE


def load_history() -> dict:
    """{'last': [answers of the last list], 'runs': [{'at', 'answers'}, ...newest first]}."""
    try:
        d = json.loads(history_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"last": [], "runs": []}
    except (OSError, ValueError):
        return {"last": [], "runs": []}


def save_history(answers: list[Answer]) -> None:
    d = load_history()
    rows = [a.to_dict() for a in answers]
    d["last"] = rows
    d.setdefault("runs", []).insert(0, {"at": datetime.now().isoformat(timespec="seconds"), "answers": rows})
    d["runs"] = d["runs"][:HISTORY_MAX]
    p = history_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
        import os
        os.replace(tmp, p)
    except OSError:
        pass


def copy_all(answers: list[Answer]) -> str:
    return "\n".join(a.line(k) for k, a in enumerate(answers, 1))


def split_input(text: str) -> list[str]:
    """The pasted box: one link or file per line (blank lines and spaces around them dropped)."""
    out = []
    for ln in (text or "").splitlines():
        s = ln.strip().strip('"')
        if s and s not in out:
            out.append(s)
    return out
