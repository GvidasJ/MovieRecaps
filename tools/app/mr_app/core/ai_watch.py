"""AI WATCH (day 16, task 5): a public clip watched by an AI that sees the picture AND hears the sound -- NexLev's
"watch ... and ask" (Instagram / TikTok / YouTube), else Algrow's ``analyze_video`` -- asked for every spoken line,
the on-screen text, the actors and the setting, and its best guess of the film or show (+ season / episode) with how
sure it is. Find source (core.find_source) then checks that guess.

* **The keys** are the Windows user environment variables NEXLEV_API_KEY and ALGROW_API_KEY of this PC (read from
  the process's environment, else HKCU\\Environment). A key is never written anywhere: not in a file, a log, an
  answer or an error text (``_scrub``). Logs only say "key found" / "key not found".
* **Both services are called as MCP servers over HTTP** (JSON-RPC: initialize, then tools/call), the key as a Bearer
  token: https://prod.dashboard.nexlev.io/api/mcp and https://mcp.algrow.online/mcp.
* **Credits:** each watch costs credits (Algrow: 1 per 4 minutes of video). A clip is never watched twice: the answer
  is cached by its link (``%APPDATA%\\MovieRecaps\\ai_watch.json``), also after a restart. Algrow's
  ``fetch_transcript`` (YouTube transcripts) is free.

Everything here waits on the network (a watch takes up to a few minutes): call it from a worker thread.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

NEXLEV_URL = "https://prod.dashboard.nexlev.io/api/mcp"
ALGROW_URL = "https://mcp.algrow.online/mcp"
NEXLEV_KEY, ALGROW_KEY = "NEXLEV_API_KEY", "ALGROW_API_KEY"
CACHE_FILE = "ai_watch.json"
WATCH_TIMEOUT_S = 420.0
CREDITS_NOTE = "uses NexLev / Algrow credits (each clip is watched once, then remembered)"
NEXLEV_TOOLS = {"Instagram": "watch_instagram_video_and_ask", "TikTok": "watch_tiktok_video_and_ask",
                "YouTube": "watch_youtube_video_and_ask"}

PROMPT = (
    "Watch this whole clip: the picture AND the sound. Answer with ONE JSON object and nothing else, in this form:\n"
    '{"spoken_lines": ["every spoken line, word for word, in order"], "on_screen_text": ["every text shown"], '
    '"actors": ["actors you recognise"], "characters": ["character names said or shown"], '
    '"setting": "where it happens, in a few words", "title": "the film or TV show this clip is cut from", '
    '"year": 2012, "kind": "movie" or "show", "season": null, "episode": null, "episode_title": "", '
    '"sure": 0, "why": "what makes you think it is that one"}\n'
    "Rules: the title is the original film or TV show the scene comes from (not the channel or the account). For a TV "
    "show give the season and episode numbers and the episode's title when you know them. \"sure\" is 0-100: how "
    "sure you are of the title (and of the episode, for a show). When you do not know, give \"title\": \"\" and "
    "\"sure\": 0 -- never guess wildly.")

_lock = threading.Lock()


# ---------------------------------------------------------------------------------------------------------------------
# the keys (never written anywhere)
# ---------------------------------------------------------------------------------------------------------------------

def key(name: str) -> str:
    """The key in this process's environment, else the Windows user environment ('' when there is none)."""
    v = os.environ.get(name, "")
    if v:
        return v
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0] or "")
    except (OSError, ImportError):
        return ""


def key_status() -> dict[str, str]:
    """{'NexLev': 'key found' | 'key not found', 'Algrow': ...} -- the only thing ever said about the keys."""
    return {"NexLev": "key found" if key(NEXLEV_KEY) else "key not found",
            "Algrow": "key found" if key(ALGROW_KEY) else "key not found"}


def _scrub(text: Any) -> str:
    """A text with every key taken out (an error message may echo a request)."""
    s = str(text or "")
    for n in (NEXLEV_KEY, ALGROW_KEY):
        k = key(n)
        if k:
            s = s.replace(k, "***")
    return s


# ---------------------------------------------------------------------------------------------------------------------
# MCP over HTTP
# ---------------------------------------------------------------------------------------------------------------------

class ServiceError(RuntimeError):
    pass


def _parse(body: str) -> dict:
    """A JSON-RPC answer: plain JSON or a server-sent-event stream (its last data: line)."""
    body = body.strip()
    if body.startswith("{"):
        return json.loads(body)
    last = None
    for line in body.splitlines():
        if line.startswith("data:"):
            try:
                last = json.loads(line[5:].strip())
            except ValueError:
                continue
    if last is None:
        raise ServiceError("the service's answer could not be read")
    return last


class Mcp:
    """One service's session: ``call(tool, args)`` -> the tool's text."""

    def __init__(self, url: str, key_name: str, timeout: float = WATCH_TIMEOUT_S):
        self.url, self.key_name, self.timeout = url, key_name, timeout
        self.sid: str | None = None
        self._id = 0

    def _post(self, body: dict, timeout: float) -> tuple[int, dict, str]:
        k = key(self.key_name)
        if not k:
            raise ServiceError(f"{self.key_name}: key not found")
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "Authorization": "Bearer " + k}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 - the two fixed https hosts
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ServiceError(_scrub(f"no answer ({getattr(e, 'reason', e)})")) from None

    def _rpc(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        self._id += 1
        st, h, t = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}, timeout)
        if st in (401, 403):
            raise ServiceError("the key was not accepted")
        if st != 200:
            raise ServiceError(_scrub(f"HTTP {st}: {t[:200]}"))
        if method == "initialize":
            self.sid = h.get("mcp-session-id") or self.sid
        d = _parse(t)
        if d.get("error"):
            raise ServiceError(_scrub(str(d["error"].get("message") or d["error"])[:300]))
        return d.get("result") or {}

    def start(self) -> None:
        if self.sid is not None:
            return
        self._rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "MovieRecaps", "version": "16"}})
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, 30.0)
        except ServiceError:
            pass

    def call(self, tool: str, args: dict, timeout: float | None = None) -> str:
        self.start()
        res = self._rpc("tools/call", {"name": tool, "arguments": args}, timeout or self.timeout)
        text = "\n".join(str(c.get("text") or "") for c in res.get("content") or [] if isinstance(c, dict))
        if res.get("isError"):
            raise ServiceError(_scrub(text[:300] or "the tool said it failed"))
        return text


# ---------------------------------------------------------------------------------------------------------------------
# the watch
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Watch:
    link: str
    by: str = ""                         # "NexLev" / "Algrow" / "" (not watched)
    title: str = ""
    year: int | None = None
    kind: str = ""                       # movie / show
    season: int | None = None
    episode: int | None = None
    episode_title: str = ""
    sure: int = 0                        # 0..100, the AI's own
    why: str = ""
    lines: list = field(default_factory=list)
    on_screen: list = field(default_factory=list)
    actors: list = field(default_factory=list)
    characters: list = field(default_factory=list)
    setting: str = ""
    error: str = ""                      # why nothing was watched (plain words, no key)
    at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Watch":
        names = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in names})

    def guess(self) -> str:
        if not self.title:
            return ""
        t = self.title + (f" ({self.year})" if self.year and self.kind != "show" else "")
        if self.season and self.episode:
            t += f" — S{int(self.season):02d}E{int(self.episode):02d}"
        return t


def _int(v: Any) -> int | None:
    try:
        n = int(str(v).strip())
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def parse_answer(text: str, link: str, by: str) -> Watch:
    """The AI's text -> a Watch (its JSON object; anything around it ignored)."""
    w = Watch(link, by)
    m = re.search(r"\{.*\}", text or "", re.S)
    d: dict = {}
    if m:
        raw = m.group(0)
        for cand in (raw, raw[: raw.rfind("}") + 1]):
            try:
                d = json.loads(cand)
                break
            except ValueError:
                continue
    for wrap in ("analysis_text", "analysis", "result", "text"):    # Algrow: {"analysis_text": "```json {...}```"}
        if isinstance(d.get(wrap), str) and "title" not in d:
            return parse_answer(d[wrap], link, by)
    if not d or not any(k in d for k in ("title", "spoken_lines", "sure")):
        w.error = "the AI's answer had no readable result"
        w.why = (text or "")[:300]
        return w
    w.title = str(d.get("title") or "").strip()
    w.year = _int(d.get("year"))
    w.kind = "show" if str(d.get("kind") or "").lower().startswith(("show", "tv", "series")) else (
        "movie" if w.title else "")
    w.season, w.episode = _int(d.get("season")), _int(d.get("episode"))
    w.episode_title = str(d.get("episode_title") or "").strip()
    try:
        w.sure = max(0, min(100, int(float(d.get("sure") or 0))))
    except (TypeError, ValueError):
        w.sure = 0
    w.why = str(d.get("why") or "")[:400]
    w.lines = [str(x) for x in d.get("spoken_lines") or [] if str(x).strip()][:80]
    w.on_screen = [str(x) for x in d.get("on_screen_text") or [] if str(x).strip()][:40]
    w.actors = [str(x) for x in d.get("actors") or [] if str(x).strip()][:12]
    w.characters = [str(x) for x in d.get("characters") or [] if str(x).strip()][:12]
    w.setting = str(d.get("setting") or "")[:200]
    return w


def norm_link(link: str) -> str:
    """The cache key: the link without its query / fragment and its trailing slash, the Instagram /reel/ and /p/ the
    same (one clip = one key)."""
    s = (link or "").strip().split("#")[0].split("?")[0].rstrip("/")
    s = re.sub(r"^https?://(www\.|m\.)?", "", s, flags=re.I)
    m = re.match(r"instagram\.com/(?:[^/]+/)?(?:reel|reels|p|tv)/([\w-]+)", s, re.I)    # (the codes keep their case)
    if m:
        return f"instagram:{m.group(1)}"
    m = re.search(r"tiktok\.com/.*?/video/(\d+)", s, re.I)
    if m:
        return f"tiktok:{m.group(1)}"
    m = re.search(r"(?:youtube\.com/(?:shorts/|watch\?v=|embed/)|youtu\.be/)([\w-]{11})", link or "", re.I)
    if m:
        return f"youtube:{m.group(1)}"
    return s.lower()


def platform(link: str) -> str:
    k = norm_link(link)
    return {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}.get(k.split(":")[0], "")


def _cache_path() -> Path:
    from .settings import app_dir
    return app_dir() / CACHE_FILE


def _load() -> dict:
    try:
        d = json.loads(_cache_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def cached(link: str) -> Watch | None:
    d = _load().get(norm_link(link))
    return Watch.from_dict(d) if isinstance(d, dict) else None


def _store(w: Watch) -> None:
    with _lock:
        d = _load()
        d[norm_link(w.link)] = w.to_dict()
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)


def _nexlev(link: str, pf: str) -> str:
    args: dict[str, Any] = {"prompt": PROMPT}
    if pf == "YouTube":
        args["videoUrl"] = link
    else:
        args["videoUrl"] = link
    return Mcp(NEXLEV_URL, NEXLEV_KEY).call(NEXLEV_TOOLS[pf], args)


def _algrow(link: str) -> str:
    return Mcp(ALGROW_URL, ALGROW_KEY).call("analyze_video", {"video_url": link, "prompt": PROMPT,
                                                               "media_resolution": "low"})


def watch(link: str, say=None, nexlev=None, algrow=None) -> Watch:
    """The clip watched (the cache first: never twice). NexLev, else Algrow. Never raises (``error`` says why).
    ``nexlev`` / ``algrow``: the callers (tests put fakes here)."""
    say = say or (lambda _t: None)
    c = cached(link)
    if c is not None and (c.by or c.error.startswith("not a public")):
        return c
    pf = platform(link)
    if not pf:
        return Watch(link, error="not a public Instagram / TikTok / YouTube link: the AI can't watch it")
    errors = []
    nx = nexlev or (lambda u: _nexlev(u, pf))
    for name, fn in (("NexLev", nx), ("NexLev", nx), ("Algrow", algrow or _algrow)):   # (NexLev once more first:
        if name == "NexLev" and errors and "key" in errors[-1]:                       #  5 at once, it can fail)
            continue
        if not key(NEXLEV_KEY if name == "NexLev" else ALGROW_KEY) and (nexlev is None and algrow is None):
            errors.append(f"{name}: key not found")
            continue
        try:
            say(f"{name}'s AI is watching the clip (picture and sound) ...")
            text = fn(link)
            w = parse_answer(text, link, name)
            if w.error:
                errors.append(f"{name}: {w.error}")
                continue
            w.at = time.strftime("%Y-%m-%dT%H:%M:%S")
            _store(w)
            return w
        except Exception as e:  # noqa: BLE001 - the next service, else said
            errors.append(f"{name}: {_scrub(e)}"[:200])
    return Watch(link, error="; ".join(errors) or "no service could watch it")


def transcript(video: str) -> str:
    """A YouTube video's transcript (Algrow's free fetch_transcript); '' when there is none. Never raises."""
    try:
        return Mcp(ALGROW_URL, ALGROW_KEY, timeout=90.0).call("fetch_transcript", {"video_url": video})
    except Exception:  # noqa: BLE001
        return ""
