"""FIND SOURCE'S PROOF (day 17, task 2): the AI's guess is never trusted alone -- the episode especially (day 16: it
was "100 % sure" of 3 wrong episode numbers; day 17's history: Everybody Hates Chris S03E11 "Sure" for S03E07).

For one watched clip (core.ai_watch.Watch: its spoken lines, its top 3 guesses):

1. **The spoken lines are searched for.** 2-3 distinctive lines (the longest with the rarest words), each with the
   show's name, in Algrow's ``youtube_search`` (free): clips of the scene are often titled with the show and the
   episode. The best few short results get their transcript (Algrow's free ``fetch_transcript``); a transcript with
   the clip's exact words (4 words in a row, or a whole short line) is **the same scene**.
2. **For a series, every episode summary of the show (TVmaze) is compared with the clip**: its lines, characters,
   setting and what the AI said happens. The words a summary shares with the clip count by how rare they are in the
   show's summaries (a name every summary has counts nothing).
3. **The episodes are ranked** by: the episode named in the title of a clip whose transcript has the clip's words
   (the strongest), named in a search result's title, the summary score, and the AI's guesses (weak: a number
   alone).
4. **Sure only when proven** (``SURE_RULES``): an exact line match in a transcript of a clip whose title names the
   episode (or, for a film, the film); or the best summary is the episode a search result's title names; or the
   best summary clearly beats the others AND it is one of the AI's own guesses. Otherwise Check, with the top 3.

Every search and transcript is cached by its text / video (``%APPDATA%\\MovieRecaps\\source_proof.json``) and TVmaze's
episode lists too: nothing is asked twice. Never raises: a service that is away gives no evidence (said in
``tried``).
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

CACHE_FILE = "source_proof.json"
LINES_SEARCHED = 3
TRANSCRIPTS_PER_SEARCH = 3
SHORT_VIDEO_S = 900                     # a clip of a scene (a whole episode's transcript is not a "scene" proof)
SUMMARY_LEAD = 1.6                      # the best summary this many times the second's score: "clearly"
SUMMARY_MIN = 4.0                       # ... and at least this score
NGRAM = 4
NONE = "-"                              # remembered for a video with no transcript (asked once)
TOP_RESULTS = 3                         # a line's search: its first results count as clips of that line

STOP = set("""a about above after again against all am an and any are aren't as at be because been before being below
between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each few for
from further get gets got had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself
him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself just let's like me more most
mustn't my myself no nor not now of off oh ok okay on once only or other ought our ours ourselves out over own really
right same say said says see she she'd she'll she's should shouldn't so some such than that that's the their theirs
them themselves then there there's these they they'd they'll they're they've this those through to too under until
up us very was wasn't we we'd we'll we're we've well were weren't what what's when when's where where's which while
who who's whom why why's will with won't would wouldn't yeah yes you you'd you'll you're you've your yours yourself
yourselves gonna wanna gotta hey know think go going come one two want look man guy guys thing things back time
episode season scene clip show series""".split())

_lock = threading.Lock()


def words(text: str) -> list[str]:
    """Lower-case words (apostrophes kept inside: "don't")."""
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", (text or "").lower().replace("’", "'"))


def content(text: str) -> list[str]:
    return [w for w in words(text) if w not in STOP and len(w) >= 3]


def stems(text: str) -> set[str]:
    """The content words for comparing with a summary: "Mark's" -> "mark", "babysitting" / "babysitter" -> "babysit"
    (the first 7 letters)."""
    out = set()
    for w in content(text):
        w = re.sub(r"'s$", "", w).replace("'", "")
        if len(w) >= 3 and w not in STOP:
            out.add(w[:7])
    return out


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


# ---------------------------------------------------------------------------------------------------------------------
# the cache (searches, transcripts, episode lists)
# ---------------------------------------------------------------------------------------------------------------------

def _cache_path() -> Path:
    from .settings import app_dir
    return app_dir() / CACHE_FILE


def _load() -> dict:
    try:
        d = json.loads(_cache_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember(key: str, fn: Callable[[], Any]) -> Any:
    """``fn()`` once per key, ever (an empty answer -- a service away -- is not remembered)."""
    d = _load()
    if key in d:
        return d[key]
    v = fn()
    if v:
        with _lock:
            d = _load()
            d[key] = v
            p = _cache_path()
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, p)
            except OSError:
                pass
    return v


# ---------------------------------------------------------------------------------------------------------------------
# the services (fakes go into ``Services`` in the tests)
# ---------------------------------------------------------------------------------------------------------------------

def _algrow(tool: str, args: dict, timeout: float = 90.0) -> str:
    from .ai_watch import ALGROW_KEY, ALGROW_URL, Mcp, key
    if not key(ALGROW_KEY):
        return ""
    try:
        return Mcp(ALGROW_URL, ALGROW_KEY, timeout=timeout).call(tool, args, timeout)
    except Exception:  # noqa: BLE001 - away: no evidence
        return ""


def yt_search(query: str) -> list[dict]:
    """Algrow's youtube_search (free): [{video_id, title, channel_name, duration_seconds}] of the videos."""
    def ask() -> list[dict]:
        t = _algrow("youtube_search", {"query": query[:200], "limit": 10, "type": "video"})
        try:
            d = json.loads(t[t.index("{"):]) if t else {}
        except ValueError:
            return []
        return [{"video_id": r.get("video_id"), "title": r.get("title") or "", "channel": r.get("channel_name") or "",
                 "secs": r.get("duration_seconds")} for r in d.get("results") or []
                if isinstance(r, dict) and r.get("type", "video") == "video" and r.get("video_id")]
    return _remember("yt:" + query.lower().strip(), ask) or []


def transcript(video_id: str) -> str:
    """Algrow's fetch_transcript (free): the video's spoken text ('' when it has none)."""
    def ask() -> str:
        t = _algrow("fetch_transcript", {"video_url": video_id})
        try:
            d = json.loads(t[t.index("["):]) if t and "[" in t else None
        except ValueError:
            d = None
        if isinstance(d, list) and d and isinstance(d[0], dict):
            t = str(d[0].get("transcript") or "")
        elif t.lstrip().startswith(("{", "[")):
            return ""
        return NONE if not t.strip() or t.strip().lower().startswith("no transcript") else t
    got = _remember("tr:" + video_id, ask) or ""
    return "" if got == NONE or got.strip().lower().startswith("no transcript") else got


def _get_json(url: str) -> Any:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "MovieRecaps/1 (find source)"})
    with urllib.request.urlopen(req, timeout=15) as r:              # noqa: S310 - TVmaze's public https API
        return json.loads(r.read().decode("utf-8", "replace"))


def episodes(show: str) -> dict:
    """TVmaze: {"name": the show's name, "eps": [{season, number, name, summary}]} ({} when not found)."""
    def ask() -> dict:
        try:
            s = _get_json("https://api.tvmaze.com/singlesearch/shows?q=" + urllib.parse.quote(show))
            eps = _get_json(f"https://api.tvmaze.com/shows/{s['id']}/episodes")
        except Exception:  # noqa: BLE001
            return {}
        return {"name": s.get("name") or show,
                "eps": [{"season": e.get("season"), "number": e.get("number"), "name": e.get("name") or "",
                         "summary": re.sub(r"<[^>]+>", "", e.get("summary") or "")} for e in eps
                        if e.get("season") and e.get("number")]}
    return _remember("tvm:" + norm(show), ask) or {}


@dataclass
class Services:
    search: Callable[[str], list] = yt_search
    transcript: Callable[[str], str] = transcript
    episodes: Callable[[str], dict] = episodes


# ---------------------------------------------------------------------------------------------------------------------
# the evidence
# ---------------------------------------------------------------------------------------------------------------------

def distinctive(lines: list[str], k: int = LINES_SEARCHED) -> list[str]:
    """The clip's ``k`` most searchable lines: 4+ content words, rare words first (a line said once in a film is
    better than "What?")."""
    scored = []
    seen = set()
    for ln in lines or []:
        ln = str(ln).strip()
        c = content(ln)
        if len(words(ln)) < 4 or len(c) < 2 or norm(ln) in seen:
            continue
        seen.add(norm(ln))
        scored.append((len(c) + 0.3 * sum(len(w) > 6 for w in c) - 0.15 * max(0, len(words(ln)) - 16), ln))
    return [ln for _s, ln in sorted(scored, key=lambda t: -t[0])[:k]]


def line_hits(lines: list[str], text: str) -> int:
    """How many of the clip's lines are in ``text`` word for word (4 words in a row; a 3-word line whole)."""
    hay = " " + " ".join(words(text)) + " "
    n = 0
    for ln in lines or []:
        w = words(ln)
        if len(w) < 3:
            continue
        grams = [w] if len(w) < NGRAM else [w[i:i + NGRAM] for i in range(len(w) - NGRAM + 1)]
        if any(" " + " ".join(g) + " " in hay for g in grams if len(content(" ".join(g))) >= 1):
            n += 1
    return n


def episode_named(text: str, eps: list[dict]) -> dict | None:
    """The episode a video title names: its title (5+ letters) or "S03E07" / "3x07" / "season 3 episode 7"."""
    t = norm(text)
    best = None
    for e in eps:
        n = norm(e["name"])
        if len(n) >= 5 and n in t and (best is None or len(n) > len(norm(best["name"]))):
            best = e
    if best is not None:
        return best
    m = (re.search(r"(?<![a-z0-9])s(\d{1,2})\s*e(\d{1,3})(?!\d)", text or "", re.I)
         or re.search(r"(?<![0-9])(\d{1,2})x(\d{2})(?![0-9])", text or "")
         or re.search(r"season\s*(\d{1,2})\D{1,12}episode\s*(\d{1,3})", text or "", re.I))
    if m:
        s, n2 = int(m.group(1)), int(m.group(2))
        for e in eps:
            if e["season"] == s and e["number"] == n2:
                return e
    return None


def summary_ranking(eps: list[dict], clues: str) -> list[tuple[float, dict]]:
    """[(score, episode)] best first: the clue words each summary has, weighted by how rare they are among the
    show's summaries (idf)."""
    docs = [stems(e["name"] + " " + e["summary"]) for e in eps]
    n = len(docs) or 1
    df: dict[str, int] = {}
    for d in docs:
        for w in d:
            df[w] = df.get(w, 0) + 1
    clue = stems(clues)
    avg = sum(len(d) for d in docs) / n or 1.0
    out = []
    for e, d in zip(eps, docs):
        # BM25's length rule: a long summary has more words to share by chance (k 1.2, b 0.75)
        s = sum(math.log(n / df[w]) for w in clue & d if df.get(w)) * 2.2 / (1 + 1.2 * (0.25 + 0.75 * len(d) / avg))
        out.append((round(s, 3), e))
    return sorted(out, key=lambda t: -t[0])


@dataclass
class Proof:
    name: str = ""                      # the film / show
    season: int | None = None
    episode: int | None = None
    episode_title: str = ""
    kind: str = ""                      # movie / episode / show
    sure: bool = False
    how: str = ""                       # what proves it (plain words)
    others: list = field(default_factory=list)      # the top 3: [{text, season, episode, episode_title, why}]
    tried: list = field(default_factory=list)       # what was looked at (said when nothing is found)
    scene_titles: list = field(default_factory=list)  # titles of clips whose transcript has the clip's words
    titled: bool = False                # a film: YouTube clips of these lines are titled with it


def _ep_text(show: str, e: dict) -> str:
    return f"{show} — S{int(e['season']):02d}E{int(e['number']):02d} \"{e['name']}\""


def prove(w: Any, services: Services | None = None) -> Proof:
    """The evidence for the watched clip ``w`` (an ai_watch.Watch with its ``guesses``), module docstring."""
    sv = services or Services()
    pr = Proof()
    guesses = [g for g in (getattr(w, "guesses", None) or []) if isinstance(g, dict) and g.get("title")]
    if getattr(w, "title", "") and not any(norm(g["title"]) == norm(w.title) and g.get("season") == w.season and
                                           g.get("episode") == w.episode for g in guesses):
        guesses.insert(0, {"title": w.title, "year": w.year, "kind": w.kind, "season": w.season,
                           "episode": w.episode, "episode_title": w.episode_title, "why": w.why})
    show = guesses[0]["title"] if guesses else ""
    lines = [str(x) for x in (getattr(w, "lines", None) or [])]
    # 1. the lines searched for, the best short results' transcripts read
    results: list[dict] = []
    per_query: list[list] = []
    scene: list[dict] = []
    looked: set[str] = set()
    for ln in distinctive(lines):
        q = f"{show} {ln}".strip()
        pr.tried.append(f"searched YouTube for \"{q[:80]}\"")
        res = sv.search(q) or []
        results += res
        per_query.append(res[:TOP_RESULTS])
        k = 0
        for r in res:
            if k >= TRANSCRIPTS_PER_SEARCH:
                break
            vid = r.get("video_id")
            if not vid or vid in looked or (r.get("secs") or 0) > SHORT_VIDEO_S:
                continue
            looked.add(vid)
            k += 1
            hits = line_hits(lines, sv.transcript(vid) or "")
            if hits >= (2 if len([x for x in lines if len(words(x)) >= 3]) >= 3 else 1):
                scene.append(dict(r, hits=hits))
    pr.scene_titles = [r["title"] for r in scene]
    if looked:
        pr.tried.append(f"read {len(looked)} transcripts ({len(scene)} with the clip's own words)")
    if not show:
        # no title from the AI: a clip of the very scene names it ("... | The Office")
        for r in scene:
            pr.how = f"a YouTube clip with the clip's words is titled \"{r['title']}\""
            pr.name, pr.kind = "", "unknown"
            break
        return pr
    is_show = any(str(g.get("kind") or "").lower().startswith(("show", "tv", "series", "episode")) or g.get("season")
                  for g in guesses)
    in_titles = [r for r in results if norm(show) and norm(show) in norm(r["title"])]
    scene_named = [r for r in scene if norm(show) in norm(r["title"])]
    if not is_show:                       # a film: the scene's clips naming it are the proof
        g = guesses[0]
        pr.name, pr.kind = g["title"], "movie"
        if scene_named:
            pr.sure = pr.titled = True
            pr.how = f"a YouTube clip with the clip's own words is titled \"{scene_named[0]['title'][:80]}\""
        elif in_titles:
            pr.titled = True
            pr.how = f"YouTube clips of these lines are titled with it (\"{in_titles[0]['title'][:80]}\")"
        pr.others = [{"text": f"{x['title']}" + (f" ({x['year']})" if x.get("year") else ""), "why": x.get("why", "")}
                     for x in guesses[:3]]
        return pr
    # 2. a series: TVmaze's episode summaries
    lst = sv.episodes(show) or {}
    eps = lst.get("eps") or []
    pr.name, pr.kind = lst.get("name") or show, "show"
    if not eps:
        pr.tried.append(f"TVmaze has no episode list for {show}")
        pr.others = [{"text": f"{x['title']}" + (f" — S{int(x['season']):02d}E{int(x['episode']):02d}"
                                                if x.get("season") and x.get("episode") else ""),
                      "season": x.get("season"), "episode": x.get("episode"),
                      "episode_title": x.get("episode_title") or "", "why": x.get("why", "")} for x in guesses[:3]]
        return pr
    pr.tried.append(f"compared the clip with {len(eps)} episode summaries of {pr.name} (TVmaze)")
    clues = " ".join(lines + list(getattr(w, "characters", None) or []) + [getattr(w, "setting", "") or ""]
                     + [str(g.get("why") or "") for g in guesses] + list(getattr(w, "on_screen", None) or []))
    ranking = summary_ranking(eps, clues)
    by_key = {(e["season"], e["number"]): e for e in eps}
    score: dict[tuple, float] = {}
    why: dict[tuple, list] = {}

    def add(e: dict, s: float, reason: str) -> None:
        k = (e["season"], e["number"])
        score[k] = score.get(k, 0.0) + s
        why.setdefault(k, []).append(reason)

    best_s = ranking[0][0] if ranking else 0.0
    for s, e in ranking[:5]:
        if s > 0 and best_s > 0:
            add(e, 1.5 * s / best_s, f"its summary fits the clip (score {s:.1f})")
    scene_eps = []
    for r in scene:
        e = episode_named(r["title"], eps)
        if e is not None:
            scene_eps.append((e, r))
            add(e, 3.0, f"a YouTube clip with the clip's own words is titled \"{r['title'][:70]}\"")
    title_eps = []
    lines_naming: dict[tuple, int] = {}           # episode -> how many different lines' searches name it on top
    named_by: dict[tuple, str] = {}
    for res in per_query:
        here = set()
        for r in res:
            e = episode_named(r["title"], eps)
            # (the show's name in the title, or a long episode title: the search was for this show already)
            if e is not None and (norm(show) in norm(r["title"]) or len(norm(e["name"])) >= 12):
                here.add((e["season"], e["number"]))
                named_by.setdefault((e["season"], e["number"]), r["title"])
        for k in here:
            lines_naming[k] = lines_naming.get(k, 0) + 1
    for k, n in lines_naming.items():
        title_eps.append(by_key[k])
        add(by_key[k], 1.0 + 0.8 * (n - 1), f"YouTube clips of {n} of its lines are titled \"{named_by[k][:70]}\""
            if n > 1 else f"a YouTube clip of these lines is titled \"{named_by[k][:70]}\"")
    ai_eps = []
    for i, g in enumerate(guesses[:3]):
        e = None
        if g.get("episode_title"):
            e = next((x for x in eps if norm(x["name"]) == norm(g["episode_title"])), None)
        if e is None and g.get("season") and g.get("episode"):
            e = by_key.get((int(g["season"]), int(g["episode"])))
        if e is not None:
            ai_eps.append(e)
            add(e, 0.7 if i == 0 else 0.4, f"the AI's guess {i + 1}" + (f" ({g['why'][:80]})" if g.get("why") else ""))
        t = norm(g.get("episode_title") or "")
        if len(t) >= 6:                  # a near title: the AI's "Spaced Out" for "Totally Spaced Out"
            for x in eps:
                n = norm(x["name"])
                if x is not e and n != t and (t in n or (len(n) >= 6 and n in t)):
                    add(x, 0.5, f"its title is close to the AI's \"{g['episode_title'][:40]}\"")
    if not score:
        pr.tried.append("no episode fits: no summary shares the clip's words, no clip is titled with one")
        return pr
    top = sorted(score, key=lambda k: -score[k])
    if ai_eps and (ai_eps[0]["season"], ai_eps[0]["number"]) not in top[:3]:     # the AI's own guess is always
        top = top[:2] + [(ai_eps[0]["season"], ai_eps[0]["number"])]             # among the 3 shown
    best = by_key[top[0]]
    pr.season, pr.episode, pr.episode_title, pr.kind = best["season"], best["number"], best["name"], "episode"
    pr.others = [{"text": _ep_text(pr.name, by_key[k]), "season": k[0], "episode": k[1],
                  "episode_title": by_key[k]["name"], "why": "; ".join(why[k][:3])} for k in top[:3]]
    # 3. sure only when proven
    second_s = ranking[1][0] if len(ranking) > 1 else 0.0
    summary_best = ranking[0][1] if ranking and best_s > 0 else None
    clear = summary_best is not None and best_s >= SUMMARY_MIN and best_s >= SUMMARY_LEAD * max(second_s, 0.1)
    if any(e is best for e, _r in scene_eps):
        r = next(r for e, r in scene_eps if e is best)
        pr.sure, pr.how = True, f"a YouTube clip with the clip's own words is titled \"{r['title'][:80]}\""
    elif lines_naming.get(top[0], 0) >= 2:
        pr.sure, pr.how = True, (f"the top YouTube clips of {lines_naming[top[0]]} of its lines are titled "
                                 f"\"{named_by[top[0]][:80]}\"")
    elif summary_best is best and any(e is best for e in title_eps):
        pr.sure, pr.how = True, "its summary fits the clip best, and a YouTube clip of these lines is titled with it"
    elif clear and summary_best is best and any(e is best for e in ai_eps):
        pr.sure, pr.how = True, "its summary fits the clip clearly best, and it is one of the AI's own guesses"
    else:
        pr.how = "; ".join(why[top[0]][:3])
    return pr
