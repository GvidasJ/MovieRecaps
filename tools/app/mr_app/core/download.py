"""DOWNLOAD: the competitor's and the RAW's videos from their links, with yt-dlp (``python -m yt_dlp`` of the venv,
run hidden as a subprocess, so Stop can kill it and everything it started).

* ``probe_link(url)`` reads a link without downloading (``-J``): title, id, length, size, a Short or not, and the
  format the download will take -- or a plain-words error (private video, region blocked, the link is wrong ...).
* ``choose_format(formats)`` is the format choice, explicit and testable (not a yt-dlp format string): the best
  H.264 picture (most pixels, then fps, then bitrate) with the best AAC sound; a VP9 / AV1 picture only when it is
  strictly bigger than every H.264 one (more pixels, or as many with a higher fps) -- CONVERT turns it into H.264
  afterwards; SDR before HDR; the original-language sound track before dubbed ones; a format with picture and sound
  in one only when nothing better is there.
* ``download(url, folder, role)`` downloads exactly that choice (yt-dlp reads the probe's own JSON again:
  ``--load-info-json``), reports progress over the picture and the sound together, writes yt-dlp's whole output to
  the log and returns where the file went (yt-dlp's name: ``<title> [<id>].mp4``; nothing is renamed -- the longer
  video is the RAW, the tool's own rule).
* ``take_local(file, folder)``: a RAW file dropped instead of a link goes into the project folder -- a hard link
  when the folder is on the same drive (instant, no space used), else a copy.
* ``update_ytdlp()``: YouTube changes often and an old yt-dlp then fails, so it is updated at most once a day.
* ``download_project(project)``: both files of a project (projects.py), the step's state written to project.json.

Progress callbacks are ``progress(fraction 0..1, text)``, called from the thread that runs the download. Cancel is a
``threading.Event``: set, the download stops within a fraction of a second, its process tree is killed, its partial
files are removed and ``Cancelled`` is raised."""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import parse_qs, urlparse

from . import paths
from .run import CREATE_NO_WINDOW, kill_tree

Progress = Callable[[float, str], None]

PROBE_TIMEOUT_S = 120.0
UPDATE_TIMEOUT_S = 180.0
UPDATE_FILE = "ytdlp_last_update.txt"
UPDATE_RETRY_S = 3600.0          # a failed update check (offline) is tried again after an hour
PROBE_FRESH_S = 1800.0           # a probe younger than this is downloaded without reading the link again
OUT_TEMPLATE = "%(title).80B [%(id)s].%(ext)s"
NODE_MIN = (22, 0, 0)            # yt-dlp's oldest supported Node.js

H264 = ("avc1", "avc3", "h264")
HEVC = ("hev1", "hvc1", "hevc", "h265")
VP9 = ("vp09", "vp9")
AV1 = ("av01", "av1")
VP8 = ("vp8", "vp08")
AUDIO_KINDS = {"mp4a": "aac", "aac": "aac", "opus": "opus", "mp3": "mp3", "vorbis": "vorbis", "ac-3": "ac3",
               "ac3": "ac3", "ec-3": "eac3", "eac3": "eac3", "flac": "flac", "alac": "alac"}
PRETTY = {"h264": "H.264", "hevc": "HEVC", "vp9": "VP9", "av1": "AV1", "vp8": "VP8", "aac": "AAC", "opus": "Opus",
          "mp3": "MP3", "vorbis": "Vorbis", "ac3": "AC-3", "eac3": "E-AC-3", "flac": "FLAC", "alac": "ALAC"}
MP4_VIDEO = {"h264", "hevc", "av1"}                     # merged into .mp4 with these; VP9 and others -> .mkv
MP4_AUDIO = {"aac", "mp3", "opus", "ac3", "eac3", "flac", "alac", None}


class DownloadError(Exception):
    """A download that did not work: ``plain`` says why in plain words, ``detail`` is yt-dlp's own error line."""

    def __init__(self, plain: str, detail: str = ""):
        super().__init__(plain)
        self.plain = plain
        self.detail = detail


class Cancelled(Exception):
    """Stop was pressed (the cancel event was set)."""


# ---------------------------------------------------------------------------------------------------------------------
# errors in plain words
# ---------------------------------------------------------------------------------------------------------------------

PRIVATE = "this video is private: only its owner (and people they shared it with) can watch it"
MEMBERS = "this video is for the channel's paying members only"
REGION = "this video is blocked in your country (region blocked)"
BOT = ("YouTube asks this computer to sign in to prove it is not a bot (it does this after many downloads from one "
       "internet connection): wait an hour or two and try again")
AGE = ("this video is age-restricted: YouTube shows it only to signed-in adults, so it cannot be downloaded "
       "without signing in")
LIVE = "this is a live stream or a premiere that has not finished yet: download it after it has ended"
RATE = "YouTube is limiting downloads from this computer for a while: wait about an hour and try again"
DRM = "this video is copy-protected (DRM): it cannot be downloaded"
REMOVED = "this video is not available: it was removed or made unavailable (or the link is wrong)"
WRONG_LINK = "the link is wrong: there is no video at it (check that you copied the whole link)"
NO_JS = ("the downloader needs deno (a small JavaScript program) to read YouTube and did not find it: install deno "
         "and try again")
OLD_YTDLP = ("YouTube refused the download -- the downloader (yt-dlp) is probably out of date: update it (the app "
             "does this once a day) and try again")
NETWORK = "no internet, or the connection dropped: check the internet connection and try again"
DISK_FULL = "the disk is full: free some space and try again"
NO_FFMPEG = "FFmpeg is missing (it joins the picture and the sound): install it and try again"
PLAYLIST = "this link is a playlist or a channel, not one video: open the video and copy its own link"

# (pattern, words): the first rule that matches yt-dlp's ERROR lines wins (then its WARNING lines, then all of it)
RULES: list[tuple[str, str]] = [
    (r"no space left on device|not enough space on the disk|errno 28\b|winerror 112\b|disk (?:is )?full", DISK_FULL),
    (r"private video|this video is private|video is private", PRIVATE),
    (r"members[- ]only|join this channel|channel'?s members|available to members", MEMBERS),
    (r"not made this video available in your country|not available in your country|available from your location"
     r"|geo[- ]?restrict|blocked it in your country|in your country on copyright", REGION),
    (r"not a bot|captcha", BOT),
    (r"age[- ]restrict|confirm your age|inappropriate for some users|age[_ ]verification|age[_ ]check", AGE),
    (r"live event will begin|premieres? in \d|premiere will begin|will begin (?:shortly|in a few)"
     r"|live stream recording is not available|is_upcoming|stream (?:is )?offline|not currently live", LIVE),
    (r"rate-limited|rate limited|too many requests|http error 429|isn.t available, try again later", RATE),
    (r"\bdrm\b", DRM),
    (r"has been removed|removed by the uploader|no longer available|has been terminated|removed for violating"
     r"|copyright claim|video unavailable|this video is unavailable|this video is not available", REMOVED),
    (r"unsupported url|is not a valid url|invalid url|incomplete youtube id|looks truncated|forget to quote"
     r"|http error 404|404: not found|does not exist|unable to recognize tab page|failed to resolve url"
     r"|redirected to youtube\.com home page|video url was given without video id|no video formats found", WRONG_LINK),
    (r"no supported javascript runtime|javascript runtime could be found", NO_JS),
    (r"you have requested merging .*ffmpeg|ffmpeg (?:is )?not (?:found|installed)|ffprobe (?:is )?not found",
     NO_FFMPEG),
    (r"http error 403|403: forbidden|\bnsig\b|n challenge|signature (?:extraction|solving) failed"
     r"|requested format is not available|only images are available|cannot identify player|unable to extract",
     OLD_YTDLP),
    (r"getaddrinfo failed|errno 1100[12]|failed to resolve|name or service not known|name resolution"
     r"|network is unreachable|no route to host|connection refused|timed out|connection reset|connection aborted"
     r"|remotedisconnected|incompleteread|winerror 100(?:50|51|53|54|60|61|65)|failed to establish a new connection"
     r"|unable to connect|ssl:|eof occurred in violation|max retries exceeded|did not get any data blocks"
     r"|giving up after|transporterror|urlopen error", NETWORK),
]
RULES_BY_WORDS = {w: p for p, w in RULES}


def _short_error(line: str) -> str:
    """yt-dlp's error line without its prefixes and advice: 'ERROR: [youtube] abc: Some thing. Use --cookies ...'
    -> 'Some thing'."""
    t = re.sub(r"^\s*(?:ERROR|WARNING):\s*", "", line.strip())
    t = re.sub(r"^\[[^\]]+\]\s*(?:[0-9A-Za-z_-]+:\s+)?", "", t)               # '[youtube] <id>: '
    t = re.split(r"\.?\s+(?:Use --|See {1,2}https?://|Also see|Please report|Confirm you are on)", t)[0]
    t = re.sub(r"\s*\(caused by .*$", "", t).strip()
    return (t[:180] + " ...") if len(t) > 180 else t


def plain_error(text: str) -> str:
    """yt-dlp's output after a failure -> one line in plain words (module RULES), else its last ERROR line, short."""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    errors = [l for l in lines if l.lstrip().startswith("ERROR")]
    warnings = [l for l in lines if l.lstrip().startswith("WARNING")]
    for chunk in ("\n".join(errors), "\n".join(warnings), "\n".join(lines)):
        if not chunk:
            continue
        low = chunk.lower()
        for pat, words in RULES:
            if re.search(pat, low):
                if words == OLD_YTDLP and re.search(RULES_BY_WORDS[NO_JS], (text or "").lower()):
                    return NO_JS                                   # the formats are missing because deno is
                return words
    last = errors[-1] if errors else (lines[-1] if lines else "")
    short = _short_error(last)
    return f"the download did not work: {short}" if short else "the download did not work (see the log)"


def link_problem(url: str) -> str | None:
    """What is plainly wrong with a link before yt-dlp is asked (None: looks fine)."""
    u = (url or "").strip()
    if not u:
        return "no link: paste the video's link"
    if not re.match(r"^https?://", u, re.I):
        return f"the link is wrong: '{u[:60]}' is not a web address (it should start with https://)"
    try:
        p = urlparse(u)
    except ValueError:
        return "the link is wrong: it is not a web address"
    host = (p.hostname or "").lower()
    if not host or "." not in host:
        return "the link is wrong: it has no web site in it"
    parts = [x for x in p.path.split("/") if x]
    if host.endswith("youtu.be") or "youtube" in host:
        vid = None
        if host.endswith("youtu.be"):
            vid = parts[0] if parts else ""
        elif parts[:1] == ["watch"]:
            vid = parse_qs(p.query).get("v", [""])[0]
        elif parts[:1] and parts[0] in ("shorts", "live", "embed", "v"):
            vid = parts[1] if len(parts) > 1 else ""
        elif parts[:1] and (parts[0].startswith("@") or parts[0] in ("channel", "c", "user", "playlist")):
            return PLAYLIST
        if vid is not None and not re.fullmatch(r"[0-9A-Za-z_-]{11}", vid):
            return (f"the link is wrong: a YouTube video's id has 11 letters and digits, this one is '{vid[:30]}'"
                    if vid else "the link is wrong: it has no video id in it")
    return None


def clean_link(url: str) -> str:
    """Spaces off; a link without https:// gets it ('youtube.com/watch?v=...')."""
    u = (url or "").strip().strip('"').strip("'").strip()
    if u and not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I) and re.match(r"^[\w-]+(\.[\w-]+)+/", u):
        u = "https://" + u
    return u


# ---------------------------------------------------------------------------------------------------------------------
# the format choice
# ---------------------------------------------------------------------------------------------------------------------

def vkind(f: dict) -> str | None:
    """The picture's codec of a yt-dlp format: h264 / hevc / vp9 / av1 / vp8 / other / unknown; None: no picture."""
    v = f.get("vcodec")
    if v == "none":
        return None
    c = (v or "").lower()
    if not c:
        return "unknown"
    for kind, names in (("h264", H264), ("hevc", HEVC), ("vp9", VP9), ("av1", AV1), ("vp8", VP8)):
        if c.startswith(names):
            return kind
    return "other"


def akind(f: dict) -> str | None:
    """The sound's codec: aac / opus / mp3 / ... / other / unknown; None: no sound."""
    a = f.get("acodec")
    if a == "none":
        return None
    c = (a or "").lower()
    if not c:
        return "unknown"
    for prefix, kind in AUDIO_KINDS.items():
        if c.startswith(prefix):
            return kind
    return "other"


def _pixels(f: dict) -> int:
    w, h = f.get("width") or 0, f.get("height") or 0
    if w and h:
        return int(w) * int(h)
    return int(h) * int(h) * 16 // 9 if h else 0


def _fps(f: dict) -> float:
    return float(f.get("fps") or 0.0)


def _rate(f: dict) -> float:
    return float(f.get("vbr") or f.get("tbr") or f.get("abr") or 0.0)


def _proto_rank(f: dict) -> int:
    p = (f.get("protocol") or "").lower()
    if p in ("https", "http") or p.startswith("http_dash"):
        return 2                     # one file (or DASH): an HLS playlist's BANDWIDTH is a peak, not comparable
    if "m3u8" in p:
        return 1
    return 0


def _sdr(f: dict) -> bool:
    return (f.get("dynamic_range") or "SDR").upper() == "SDR"


def _doubtful(f: dict) -> bool:
    """AI-upscaled, damaged, missing a token: used only when nothing else is there."""
    note = (f.get("format_note") or "").lower()
    fid = str(f.get("format_id") or "").lower()
    return (fid.endswith("-sr") or "upscaled" in note or "damaged" in note or "missing pot" in note
            or (f.get("preference") is not None and f.get("preference") <= -10))


def _prefer(cands: list[dict], bad: Callable[[dict], bool]) -> list[dict]:
    good = [c for c in cands if not bad(c)]
    return good or cands


def _vkey(f: dict) -> tuple:
    return (_pixels(f), _fps(f), _proto_rank(f), _rate(f))


def _akey(f: dict) -> tuple:
    descriptive = "descriptive" in (f.get("format_note") or "").lower()
    drc = str(f.get("format_id") or "").lower().endswith("-drc") or "drc" in (f.get("format_note") or "").lower()
    return (not descriptive, f.get("language_preference") or 0, not drc, _proto_rank(f), _rate(f))


def _bigger(a: dict, b: dict) -> bool:
    """a's picture strictly bigger than b's: more pixels, or as many and a clearly higher frame rate."""
    return _pixels(a) > _pixels(b) or (_pixels(a) == _pixels(b) and _fps(a) > _fps(b) + 1.0)


def _vdesc(f: dict) -> str:
    bits = [PRETTY.get(vkind(f) or "", vkind(f) or "?")]
    if f.get("width") and f.get("height"):
        bits.append(f"{f['width']}x{f['height']}")
    elif f.get("height"):
        bits.append(f"{f['height']}p")
    if _fps(f):
        bits.append(f"{_fps(f):g} fps")
    if _rate(f):
        bits.append(f"({_rate(f) / 1000:.1f} Mb/s)")
    if not _sdr(f):
        bits.append(str(f.get("dynamic_range")))
    return " ".join(bits)


def _adesc(f: dict) -> str:
    bits = [PRETTY.get(akind(f) or "", akind(f) or "?")]
    if _rate(f):
        bits.append(f"{_rate(f):.0f} kb/s")
    if (f.get("audio_channels") or 0) > 2:
        bits.append(f"{f['audio_channels']} channels")
    if (f.get("language_preference") or 0) >= 10 and f.get("language"):
        bits.append(f"({f['language']}, original)")
    return " ".join(bits)


@dataclass
class Choice:
    """The formats a download takes."""
    spec: str                         # yt-dlp's -f: '299+140', or one format id
    why: str                          # plain words
    video: dict | None = None         # the picture's format (or the one with both)
    audio: dict | None = None         # the sound's format (None: in the video's format, or none)
    merge_ext: str = "mp4"            # 'mp4', or 'mkv' when the codecs do not go into mp4 cleanly

    @property
    def parts(self) -> list[dict]:
        return [f for f in (self.video, self.audio) if f]

    @property
    def video_kind(self) -> str | None:
        return vkind(self.video) if self.video else None

    @property
    def audio_kind(self) -> str | None:
        if self.audio:
            return akind(self.audio)
        return akind(self.video) if self.video else None


def pick_formats(formats: list[dict]) -> Choice:
    """The choice (module docstring) from yt-dlp's format list; DownloadError when there is no picture."""
    usable = [f for f in formats or [] if not f.get("has_drm") and (f.get("protocol") or "") != "mhtml"
              and (f.get("ext") or "") != "mhtml" and "storyboard" not in (f.get("format_note") or "")]
    if not usable and any(f.get("has_drm") for f in formats or []):
        raise DownloadError(DRM)
    videos = [f for f in usable if vkind(f) is not None and akind(f) is None]
    audios = [f for f in usable if vkind(f) is None and akind(f) is not None]
    muxed = [f for f in usable if vkind(f) is not None and akind(f) is not None]
    if not videos and not muxed:
        raise DownloadError("there is no video at this link (only sound or pictures)" if audios else WRONG_LINK)

    def best_picture(cands: list[dict]) -> tuple[dict, str]:
        cands = _prefer(cands, _doubtful)
        if any(_sdr(c) for c in cands):
            cands = [c for c in cands if _sdr(c)]                  # SDR before HDR
        h264 = [c for c in cands if vkind(c) == "h264"]
        other = [c for c in cands if vkind(c) in ("vp9", "av1", "hevc")]
        rest = [c for c in cands if c not in h264 and c not in other]
        best_h = max(h264, key=_vkey) if h264 else None
        best_o = max(other, key=_vkey) if other else None
        if best_h is not None and (best_o is None or not _bigger(best_o, best_h)):
            note = (f" -- the {PRETTY[vkind(best_o)]} version is not bigger" if best_o is not None else "")
            return best_h, note
        if best_o is not None:
            note = (f" -- bigger than any H.264 (best H.264: {_vdesc(best_h)}); converted to H.264 afterwards"
                    if best_h is not None else " -- no H.264 version; converted to H.264 afterwards")
            return best_o, note
        return max(rest, key=_vkey), " -- converted to H.264 afterwards"

    if videos:
        video, vnote = best_picture(videos)
        if muxed:
            m, mnote = best_picture(muxed)
            if _bigger(m, video):
                return Choice(str(m["format_id"]), f"{_vdesc(m)} with its sound in one{mnote}", video=m,
                              merge_ext="mp4")
        if not audios:
            return Choice(str(video["format_id"]), f"{_vdesc(video)}, no separate sound{vnote}", video=video,
                          merge_ext="mp4" if vkind(video) in MP4_VIDEO else "mkv")
        aac = [a for a in audios if akind(a) == "aac"]
        pool = _prefer(aac or audios, _doubtful)
        audio = max(pool, key=_akey)
        anote = "" if aac else f" (no AAC sound: {PRETTY.get(akind(audio) or '', 'this one')}, made AAC afterwards)"
        ext = "mp4" if vkind(video) in MP4_VIDEO and akind(audio) in MP4_AUDIO else "mkv"
        return Choice(f"{video['format_id']}+{audio['format_id']}", f"{_vdesc(video)} + {_adesc(audio)}{anote}{vnote}",
                      video=video, audio=audio, merge_ext=ext)
    m, mnote = best_picture(muxed)
    return Choice(str(m["format_id"]), f"{_vdesc(m)} with its sound in one{mnote}", video=m, merge_ext="mp4")


def choose_format(formats: list[dict]) -> tuple[str, str]:
    """(yt-dlp's -f for the choice, why in plain words) -- pick_formats."""
    c = pick_formats(formats)
    return c.spec, c.why


# ---------------------------------------------------------------------------------------------------------------------
# yt-dlp, hidden
# ---------------------------------------------------------------------------------------------------------------------

def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


@lru_cache(maxsize=1)
def js_runtime() -> tuple[str, str] | None:
    """('deno', its path) when deno is there (on PATH, in ~/.deno/bin, or in the venv -- pip's 'deno' package), else
    ('node', path) for Node.js 22 or newer, else None (yt-dlp then misses most YouTube formats)."""
    venv_scripts = Path(paths.python_exe()).parent
    for cand in (shutil.which("deno"), str(Path.home() / ".deno" / "bin" / "deno.exe"),
                 str(venv_scripts / "deno.exe")):
        if cand and Path(cand).is_file():
            return "deno", cand
    node = shutil.which("node")
    if node:
        try:
            out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20,
                                 creationflags=CREATE_NO_WINDOW).stdout
            if _version_tuple(out) >= NODE_MIN:
                return "node", node
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _env() -> dict:
    return dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1", NO_COLOR="1")


def base_command() -> list[str]:
    """``python -m yt_dlp`` of the venv with what every call needs: no config files of the user, no colours, the JS
    runtime YouTube needs, FFmpeg's place."""
    cmd = [str(paths.python_exe()), "-m", "yt_dlp", "--ignore-config", "--color", "never", "--no-playlist",
           "--socket-timeout", "30"]
    js = js_runtime()
    if js is not None:
        cmd += ["--js-runtimes", f"{js[0]}:{js[1]}"]
    ff = shutil.which("ffmpeg")
    if ff:
        cmd += ["--ffmpeg-location", ff]
    return cmd


def _run_capture(cmd: list[str], timeout_s: float, cancel: threading.Event | None = None) -> tuple[int, str, str]:
    """Run hidden, collect stdout and stderr; the timeout or ``cancel`` kills the whole process tree."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", env=_env(),
                            creationflags=CREATE_NO_WINDOW)
    box: dict[str, str] = {}
    t = threading.Thread(target=lambda: box.update(zip(("out", "err"), proc.communicate())), daemon=True)
    t.start()
    t0 = time.time()
    while t.is_alive():
        t.join(0.2)
        if cancel is not None and cancel.is_set():
            kill_tree(proc.pid)
            t.join(10)
            raise Cancelled()
        if time.time() - t0 > timeout_s:
            kill_tree(proc.pid)
            t.join(10)
            raise DownloadError(f"the link did not answer within {timeout_s / 60:.0f} minutes (slow internet?)",
                                "timeout")
    return proc.returncode, box.get("out", ""), box.get("err", "")


# yt-dlp itself is replaced by an update: downloads wait while it is updated, an update waits for no download
_cv = threading.Condition()
_active = 0
_updating = False


@contextmanager
def _using_ytdlp() -> Iterator[None]:
    global _active
    with _cv:
        while _updating:
            _cv.wait(1.0)
        _active += 1
    try:
        yield
    finally:
        with _cv:
            _active -= 1
            _cv.notify_all()


# ---------------------------------------------------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class LinkInfo:
    """What a link holds (``probe_link``)."""
    url: str
    title: str
    id: str
    duration: float | None
    width: int | None                 # of the format the download takes
    height: int | None
    webpage_url: str
    is_short: bool
    uploader: str = ""
    live_status: str = ""
    extractor: str = ""
    format_spec: str = ""             # what the download takes ...
    why: str = ""                     # ... and why, in plain words
    warnings: list[str] = field(default_factory=list)
    probed_at: float = 0.0
    info: dict = field(default_factory=dict, repr=False)   # yt-dlp's whole JSON (the formats' links expire in hours)


def _warnings(err: str) -> list[str]:
    return [l.strip() for l in (err or "").splitlines() if l.lstrip().startswith("WARNING")]


def probe_link(url: str, cancel: threading.Event | None = None, timeout_s: float = PROBE_TIMEOUT_S) -> LinkInfo:
    """Read a link without downloading (``yt-dlp -J``): DownloadError in plain words when it cannot be downloaded
    (module RULES), also for a playlist or a live stream that is still running."""
    url = clean_link(url)
    problem = link_problem(url)
    if problem:
        raise DownloadError(problem)
    with _using_ytdlp():
        code, out, err = _run_capture([*base_command(), "--flat-playlist", "-J", url], timeout_s, cancel)
    if code != 0 or not out.strip():
        raise DownloadError(plain_error(err + "\n" + out), _last_error(err))
    try:
        info = json.loads(out)
    except json.JSONDecodeError as e:
        raise DownloadError("the downloader gave an answer that cannot be read (see the log)", str(e)) from e
    return link_info(url, info, _warnings(err))


def link_info(url: str, info: dict, warnings: list[str] | None = None) -> LinkInfo:
    """yt-dlp's JSON of a link -> LinkInfo (with the format choice); DownloadError for what cannot be downloaded."""
    if info.get("_type") in ("playlist", "multi_video") or (info.get("entries") and not info.get("formats")):
        raise DownloadError(PLAYLIST)
    live = info.get("live_status") or ""
    if live == "is_live":
        raise DownloadError("this is a live stream that is still running: download it after it has ended")
    if live == "is_upcoming":
        raise DownloadError(LIVE)
    if live == "post_live":
        raise DownloadError("the live stream has just ended and YouTube is still preparing the video: try again in "
                            "a few hours")
    choice = pick_formats(info.get("formats") or ([info] if info.get("url") else []))
    v = choice.video or {}
    warn = list(warnings or [])
    why = choice.why
    if any("formats may be missing" in w.lower() for w in warn):
        why += " (yt-dlp warned that some formats may be missing: update yt-dlp)"
    page = info.get("webpage_url") or url
    return LinkInfo(
        url=url, title=info.get("title") or info.get("id") or "video", id=str(info.get("id") or ""),
        duration=float(info["duration"]) if info.get("duration") else None,
        width=v.get("width") or info.get("width"), height=v.get("height") or info.get("height"),
        webpage_url=page, is_short=info.get("media_type") == "short" or "/shorts/" in page or "/shorts/" in url,
        uploader=info.get("uploader") or info.get("channel") or "", live_status=live,
        extractor=info.get("extractor_key") or info.get("extractor") or "", format_spec=choice.spec, why=why,
        warnings=warn, probed_at=time.time(), info=info)


def _last_error(text: str) -> str:
    errs = [l.strip() for l in (text or "").splitlines() if l.lstrip().startswith("ERROR")]
    return errs[-1] if errs else ""


# ---------------------------------------------------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class DownloadResult:
    path: Path
    title: str
    id: str
    width: int | None
    height: int | None
    vcodec: str | None
    acodec: str | None
    fps: float | None
    duration: float | None
    format_id: str
    why: str
    size: int = 0
    seconds: float = 0.0
    url: str = ""
    webpage_url: str = ""


def size_text(n: float) -> str:
    """12345678 -> '12.3 MB'; 1.5e9 -> '1.50 GB'."""
    n = float(n or 0)
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    return f"{n / 1e3:.0f} kB"


def clock(s: float | None) -> str:
    """14 -> '0:14', 3725 -> '1:02:05'."""
    s = int(round(max(0.0, float(s or 0))))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class Tracker:
    """Progress over all parts of one download (the picture and the sound): bytes done / bytes expected, where a
    part's size is yt-dlp's (exact or estimated) once it reports it, the probe's before. The speed is the bytes of
    the last SPEED_WINDOW_S seconds (YouTube sends in bursts: yt-dlp's own speed jumps between 3 and 40 MB/s), so
    the time left does not jump about."""
    SPEED_WINDOW_S = 6.0

    def __init__(self, parts: dict[str, float], clock_fn: Callable[[], float] = time.monotonic):
        self.total = {k: float(v or 0) for k, v in parts.items()}
        self.done = {k: 0.0 for k in parts}
        self.speed = 0.0
        self._clock = clock_fn
        self._seen: list[tuple[float, float]] = []          # (time, bytes done in all) of the last few seconds

    def update(self, fid: str, status: str, done: float | None, total: float | None, estimate: float | None,
               speed: float | None) -> None:
        fid = fid if fid in self.total else (next(iter(self.total)) if len(self.total) == 1 else fid)
        size = total or estimate or self.total.get(fid) or 0.0
        self.total[fid] = float(size)
        self.done[fid] = float(done if done is not None else self.done.get(fid, 0.0))
        if status == "finished":
            self.done[fid] = self.total[fid] = float(done or size or self.done[fid])
        now = self._clock()
        self._seen.append((now, sum(self.done.values())))
        while len(self._seen) > 2 and now - self._seen[1][0] >= self.SPEED_WINDOW_S:
            self._seen.pop(0)
        (t0, b0), (t1, b1) = self._seen[0], self._seen[-1]
        if t1 - t0 >= 1.0 and b1 > b0:
            self.speed = (b1 - b0) / (t1 - t0)
        elif speed and not self.speed:
            self.speed = float(speed)                       # the first second: yt-dlp's own

    def fraction(self) -> float:
        tot = sum(self.total.values())
        return min(0.99, sum(self.done.values()) / tot) if tot > 0 else 0.0

    def text(self) -> str:
        """'34 % -- 12.3 MB of 80.1 MB, 4.1 MB/s, about 0:14 left'."""
        tot, done = sum(self.total.values()), sum(self.done.values())
        bits = [f"{self.fraction() * 100:.0f} % -- {size_text(done)} of {size_text(tot)}"]
        if self.speed > 0:
            bits.append(f"{size_text(self.speed)}/s")
            if tot > done:
                bits.append(f"about {clock((tot - done) / self.speed)} left")
        return ", ".join(bits)


PROGRESS_TEMPLATE = ("download:MRPROG %(info.format_id)s|%(progress.status)s|%(progress.downloaded_bytes)s|"
                     "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s")


def _num(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_progress(line: str) -> tuple[str, str, float | None, float | None, float | None, float | None] | None:
    """'MRPROG 137|downloading|1024|2048|NA|512.5' -> ('137', 'downloading', 1024.0, 2048.0, None, 512.5)."""
    if not line.startswith("MRPROG "):
        return None
    bits = line[7:].strip().split("|")
    if len(bits) < 6:
        return None
    return bits[0], bits[1], _num(bits[2]), _num(bits[3]), _num(bits[4]), _num(bits[5])


def _expected(f: dict, duration: float | None) -> float:
    if f.get("filesize") or f.get("filesize_approx"):
        return float(f.get("filesize") or f.get("filesize_approx"))
    rate = f.get("tbr") or f.get("vbr") or f.get("abr")
    return float(rate) * 125.0 * float(duration) if rate and duration else 0.0


class _Log:
    """The step's log (appended; nothing when there is no path)."""

    def __init__(self, path: str | os.PathLike | None):
        self.f = None
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.f = open(path, "a", encoding="utf-8", errors="replace")

    def write(self, line: str) -> None:
        """One line, with the date and time first (night 11: dates on everything, the logs too)."""
        if self.f is not None:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            self.f.write("".join(f"{stamp}  {ln}\n" for ln in line.rstrip("\r\n").split("\n")))
            self.f.flush()

    def close(self) -> None:
        if self.f is not None:
            self.f.close()
            self.f = None


def _remove(paths_: list[Path], tries: int = 30) -> list[Path]:
    """Delete files (a file a killed program still holds is tried again for a few seconds); what is left."""
    left = list(paths_)
    for _ in range(tries):
        still = []
        for p in left:
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                still.append(p)
        left = still
        if not left:
            break
        time.sleep(0.1)
    return left


def _new_files(folder: Path, before: set[str], vid: str) -> list[Path]:
    """The files this download made (its name tag '[id]' in them, not there before) -- never another download's
    files in the same folder."""
    tag = f"[{vid}]"
    return [folder / n for n in os.listdir(folder) if n not in before and tag in n and (folder / n).is_file()]


def download_command(info_json: Path, dest: Path, choice: Choice) -> list[str]:
    """yt-dlp's command line that downloads ``choice`` of the probed link (its JSON) into ``dest``."""
    return [*base_command(), "--load-info-json", str(info_json), "-f", choice.spec,
            "--merge-output-format", choice.merge_ext, "-P", str(dest), "-o", OUT_TEMPLATE, "--windows-filenames",
            "--no-mtime", "--newline", "--progress", "--no-quiet",
            "--progress-delta", "0.5", "--progress-template", PROGRESS_TEMPLATE,
            "--print", "after_move:MRFILE %(filepath)s", "--retries", "10", "--fragment-retries", "10"]


def download(url: str, dest_dir: str | os.PathLike, role: str, progress: Progress | None = None,
             cancel: threading.Event | None = None, log_path: str | os.PathLike | None = None, *,
             probed: LinkInfo | None = None) -> DownloadResult:
    """Download the link's chosen formats (pick_formats) into ``dest_dir`` as ``<title> [<id>].mp4`` (.mkv for VP9;
    CONVERT makes it MP4). ``role``: 'competitor' or 'raw' (names the temp files); ``probed``: a fresh probe_link
    of the same link (saves reading it again). Raises DownloadError (plain words) or Cancelled."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    log = _Log(log_path)
    t0 = time.time()
    say = progress or (lambda f, t: None)
    log.write(f"--- {datetime.now().isoformat(timespec='seconds')} download {role}: {url}")
    try:
        say(0.0, "reading the link")
        if probed is not None and probed.info and time.time() - probed.probed_at < PROBE_FRESH_S:
            li = probed
        else:
            li = probe_link(url, cancel=cancel)
        choice = pick_formats(li.info.get("formats") or ([li.info] if li.info.get("url") else []))
        log.write(f"title: {li.title} [{li.id}] -- {li.webpage_url}")
        log.write(f"chosen: -f {choice.spec} ({choice.why}); merged into .{choice.merge_ext}")
        for w in li.warnings:
            log.write(w)
        info_json = dest / f".{role}_{re.sub(r'[^0-9A-Za-z_-]', '_', li.id) or 'video'}.info.json"
        info_json.write_text(json.dumps(li.info, ensure_ascii=False), encoding="utf-8")
        tracker = Tracker({str(f["format_id"]): _expected(f, li.duration) for f in choice.parts})
        before = set(os.listdir(dest))
        cmd = download_command(info_json, dest, choice)
        log.write("command: " + subprocess.list2cmdline(cmd))
        try:
            with _using_ytdlp():
                final, lines, code = _run_download(cmd, tracker, say, cancel, log)
        except Cancelled:
            left = _remove(_new_files(dest, before, li.id))
            log.write("stopped: partial files removed" + (f" (still there: {left})" if left else ""))
            raise
        finally:
            _remove([info_json])
        if code != 0 or final is None or not final.is_file():
            _remove([p for p in _new_files(dest, before, li.id) if p != final])
            text = "\n".join(lines)
            plain = plain_error(text) if code != 0 else "the download ended without a file (see the log)"
            log.write(f"FAILED (exit {code}): {plain}")
            raise DownloadError(plain, _last_error(text))
        leftovers = [p for p in _new_files(dest, before, li.id) if p != final]
        _remove(leftovers)                               # the picture and sound parts after the merge, if any
        v, a = choice.video or {}, choice.audio or {}
        res = DownloadResult(
            path=final, title=li.title, id=li.id, width=v.get("width"), height=v.get("height"),
            vcodec=v.get("vcodec"), acodec=(a or v).get("acodec"), fps=v.get("fps"), duration=li.duration,
            format_id=choice.spec, why=choice.why, size=final.stat().st_size, seconds=round(time.time() - t0, 1),
            url=url, webpage_url=li.webpage_url)
        say(1.0, f"downloaded -- {size_text(res.size)} in {clock(res.seconds)}")
        log.write(f"done: {final} ({size_text(res.size)}, {res.seconds:.1f} s)")
        return res
    except DownloadError as e:
        log.write(f"ERROR in plain words: {e.plain}" + (f" | {e.detail}" if e.detail else ""))
        raise
    finally:
        log.close()


def _run_download(cmd: list[str], tracker: Tracker, say: Progress, cancel: threading.Event | None,
                  log: _Log) -> tuple[Path | None, list[str], int]:
    """Run yt-dlp, read its lines (progress -> ``say``, everything -> the log); (the final file, the other lines,
    the exit code). Cancel: the process tree is killed and Cancelled raised."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", env=_env(), bufsize=1,
                            creationflags=CREATE_NO_WINDOW)
    q: queue.Queue = queue.Queue()

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            q.put(line)
        q.put(None)
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    final: Path | None = None
    lines: list[str] = []
    last_say = 0.0
    while True:
        if cancel is not None and cancel.is_set():
            kill_tree(proc.pid)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            th.join(5)
            raise Cancelled()
        try:
            line = q.get(timeout=0.2)
        except queue.Empty:
            continue
        if line is None:
            break
        line = line.rstrip("\r\n")
        p = parse_progress(line)
        if p is not None:
            tracker.update(*p)
            if time.time() - last_say >= 0.25 or p[1] == "finished":
                say(tracker.fraction(), tracker.text())
                last_say = time.time()
            continue
        log.write(line)
        if line.startswith("MRFILE "):
            final = Path(line[7:].strip())
            continue
        if "[Merger]" in line or "[VideoRemuxer]" in line or "[FixupM3u8]" in line:
            say(0.99, "joining the picture and the sound")
        lines.append(line)
        del lines[:-400]
    code = proc.wait()
    th.join(5)
    return final, lines, code


# ---------------------------------------------------------------------------------------------------------------------
# a RAW file dropped instead of a link
# ---------------------------------------------------------------------------------------------------------------------

def cannot_link(src: Path, dest_dir: Path) -> str | None:
    """Why a hard link from ``src`` into ``dest_dir`` cannot work before trying: 'another drive (D: and C:)',
    'a network folder'; None: try it."""
    a, b = os.path.splitdrive(str(src.resolve()))[0], os.path.splitdrive(str(dest_dir.resolve()))[0]
    if a.startswith("\\\\") or b.startswith("\\\\"):
        return "a network folder"
    if a.lower() != b.lower():
        return f"another drive ({a} and {b})"
    return None


def take_local(file: str | os.PathLike, project_folder: str | os.PathLike, progress: Progress | None = None,
               cancel: threading.Event | None = None, log_path: str | os.PathLike | None = None) -> Path:
    """A dropped RAW file into the project folder under its own name: a hard link when the folder is on the same
    drive (instant, no space used; the file you dropped stays where it is), else a copy -- the log says
    'copied, not linked: <reason>'. A file already in the folder is used as it is."""
    src, dest_dir = Path(file), Path(project_folder)
    if not src.is_file():
        raise DownloadError(f"{src.name} is not there any more")
    dest_dir.mkdir(parents=True, exist_ok=True)
    say = progress or (lambda f, t: None)
    log = _Log(log_path)
    try:
        if src.parent.resolve() == dest_dir.resolve():
            log.write(f"{src.name}: already in the project folder")
            say(1.0, "already in the project folder")
            return src
        dest = dest_dir / src.name
        k = 2
        while dest.exists():
            try:
                if os.path.samefile(dest, src):
                    say(1.0, "already in the project folder")
                    return dest
            except OSError:
                pass
            dest = dest_dir / f"{src.stem}-{k}{src.suffix}"
            k += 1
        reason = cannot_link(src, dest_dir)
        if reason is None:
            try:
                os.link(src, dest)
                log.write(f"linked (no copy): {src} -> {dest}")
                say(1.0, "linked into the project folder (no copy needed)")
                return dest
            except OSError as e:
                reason = e.strerror or str(e)
        log.write(f"copied, not linked: {reason}")
        _copy(src, dest, say, cancel)
        log.write(f"copied: {src} -> {dest} ({size_text(dest.stat().st_size)})")
        return dest
    finally:
        log.close()


def _copy(src: Path, dest: Path, say: Progress, cancel: threading.Event | None) -> None:
    total = src.stat().st_size
    part = dest.with_name(dest.name + ".part")
    done, t0, last = 0, time.time(), 0.0
    try:
        with src.open("rb") as a, part.open("wb") as b:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = a.read(8 << 20)
                if not chunk:
                    break
                b.write(chunk)
                done += len(chunk)
                if time.time() - last >= 0.25:
                    speed = done / max(1e-6, time.time() - t0)
                    say(min(0.99, done / total) if total else 0.0,
                        f"copying {done * 100 / max(1, total):.0f} % -- {size_text(done)} of {size_text(total)}, "
                        f"{size_text(speed)}/s, about {clock((total - done) / max(1.0, speed))} left")
                    last = time.time()
        shutil.copystat(src, part)
        os.replace(part, dest)
        say(1.0, f"copied -- {size_text(total)}")
    except BaseException:
        _remove([part])
        raise


# ---------------------------------------------------------------------------------------------------------------------
# keeping yt-dlp new
# ---------------------------------------------------------------------------------------------------------------------

def installed_version(python: str | os.PathLike | None = None) -> str:
    """yt-dlp's version in the venv ('2026.08.19'), '' when it is not installed."""
    py = str(python or paths.python_exe())
    try:
        r = subprocess.run([py, "-c", "import importlib.metadata as m; print(m.version('yt-dlp'))"],
                           capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _last_check(path: Path) -> tuple[float, bool]:
    """(when, it worked) of the last update check, from work\\ytdlp_last_update.txt."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        return datetime.fromisoformat(lines[0].strip()).timestamp(), lines[1].strip() == "ok"
    except (OSError, ValueError, IndexError):
        return 0.0, False


def update_ytdlp(force: bool = False, work: str | os.PathLike | None = None,
                 timeout_s: float = UPDATE_TIMEOUT_S) -> str:
    """``pip install -U "yt-dlp[default]"`` into the venv, at most once a day (an hour after a failed try; the last
    check is in work\\ytdlp_last_update.txt), quiet. Never raises: offline, the installed one stays. Not while a
    download runs (yt-dlp's files are replaced). Returns what happened, in plain words."""
    global _updating
    stamp = Path(work or paths.work_dir()) / UPDATE_FILE
    when, ok = _last_check(stamp)
    if not force:
        if ok and datetime.fromtimestamp(when).date() == datetime.now().date():
            return f"yt-dlp was checked today already ({installed_version() or 'not installed'})"
        if not ok and when and time.time() - when < UPDATE_RETRY_S:
            return f"yt-dlp: the last check failed less than an hour ago ({installed_version() or 'not installed'})"
    with _cv:
        if _active:
            return "yt-dlp not checked now: a download is running (it is checked next time)"
        _updating = True
    try:
        py = str(paths.python_exe())
        before = installed_version(py)
        cmd = [py, "-m", "pip", "install", "-U", "yt-dlp[default]", "--disable-pip-version-check", "--no-input",
               "-q", "--retries", "1", "--timeout", "15"]
        try:
            code, out, err = _run_capture(cmd, timeout_s)
        except DownloadError:
            code, out, err = -1, "", "timed out"
        after = installed_version(py) or before
        worked = code == 0
        if not worked:
            msg = (f"could not check for a newer yt-dlp (no internet?): keeping {before or 'none'}"
                   if before else "yt-dlp is not installed and could not be installed (no internet?)")
        elif after != before:
            msg = f"yt-dlp updated: {before or 'none'} -> {after}"
        else:
            msg = f"yt-dlp is up to date ({after})"
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(f"{datetime.now().astimezone().isoformat(timespec='seconds')}\n"
                             f"{'ok' if worked else 'failed'}\n{msg}\n{(err or out).strip()[-600:]}\n",
                             encoding="utf-8")
        except OSError:
            pass
        return msg
    except Exception as e:  # noqa: BLE001 - never raises
        return f"could not check for a newer yt-dlp: {e}"
    finally:
        with _cv:
            _updating = False
            _cv.notify_all()


# ---------------------------------------------------------------------------------------------------------------------
# both files of a project
# ---------------------------------------------------------------------------------------------------------------------

def download_project(project, progress: Callable[[str, float, str], None] | None = None,
                     cancel: threading.Event | None = None, roles: tuple[str, ...] = ("competitor", "raw")) -> dict:
    """The project's competitor and RAW into its folder, one after the other (the competitor first: it is short and
    gives the title): from their links, the RAW from its dropped file when it has no link. project.json gets the
    download step (running -> done / failed with the reason in plain words) and each file's name, title and id.
    ``progress(role, fraction, text)``. Returns {role: DownloadResult or the dropped file's Path}."""
    from . import projects as P
    out: dict = {}
    project.set_step("download", P.RUNNING)
    details: dict = {}
    role = ""
    try:
        c_url, r_url = (project.data.get("competitor") or {}).get("url"), (project.data.get("raw") or {}).get("url")
        if c_url and r_url and P.link_id(c_url) and P.link_id(c_url) == P.link_id(r_url):
            raise DownloadError("the competitor's link and the RAW's link are the same video: paste the RAW's own "
                                "link")
        for role in roles:
            entry = project.data.get(role) or {}
            say = (lambda f, t, r=role: progress(r, f, t)) if progress else None
            log = project.log_path("download", role)
            have = project.file(role)
            if have is not None and have.is_file() and (
                    (entry.get("url") and entry.get("id") and entry["id"] == P.link_id(entry["url"]))
                    or (not entry.get("url") and entry.get("source_file"))):
                if say:                                     # run again (e.g. after the other file failed)
                    say(1.0, "already in the project folder")
                details[role] = dict(project.step("download").get(role) or {}, file=have.name)
                out[role] = have
                continue
            if entry.get("url"):
                res = download(entry["url"], project.folder, role, progress=say, cancel=cancel, log_path=log)
                project.update(**{role: {"title": res.title, "id": res.id, "file": res.path.name,
                                         "url": entry["url"]}})
                details[role] = {"file": res.path.name, "format": res.format_id, "why": res.why,
                                 "size": res.size, "seconds": res.seconds, "width": res.width,
                                 "height": res.height, "duration": res.duration}
                out[role] = res
            elif role == "raw" and entry.get("source_file"):
                p = take_local(entry["source_file"], project.folder, progress=say, cancel=cancel, log_path=log)
                project.update(raw={"file": p.name, "title": entry.get("title") or Path(p).stem})
                details[role] = {"file": p.name, "from": entry["source_file"]}
                out[role] = p
            else:
                raise DownloadError(f"no {'RAW' if role == 'raw' else 'competitor'}: paste its link"
                                    + (" or drop its file" if role == "raw" else ""))
        cs, rs = ((details.get(r) or {}).get("duration") for r in ("competitor", "raw"))
        if cs and rs and cs > rs:
            details["note"] = (f"the competitor ({clock(cs)}) is longer than the RAW ({clock(rs)}): were the links "
                               "swapped? (the tool takes the longer video as the RAW)")
        project.set_step("download", P.DONE, **details)
        return out
    except Cancelled:
        project.set_step("download", P.FAILED, error="stopped", **details)
        raise
    except DownloadError as e:
        who = {"raw": "RAW: ", "competitor": "competitor: "}.get(role, "")
        project.set_step("download", P.FAILED, error=f"{who}{e.plain}", **details)
        raise
