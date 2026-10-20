"""Free space -- ONE place for every estimate of what a step writes (night 10; day 13: "a converted feature film can
need 15 GB+ -- if there isn't enough, don't start"), and the free space the top strip shows.

What a Match cuts run writes (day 13, the Premiere-only runs that keep the original RAW):

* into its run folder's extras\\media: the RAW and the competitor -- a hard link when the run folder is on their own
  drive (no extra space), else a copy (another drive, a network folder, a USB drive);
* a TEMPORARY analysis copy when the tool cannot read the RAW as it is (x265 / HEVC, 10-bit, a variable frame rate,
  MKV / WebM, AC-3 / DTS sound ...: libx264 CRF 12, ~25 Mbit/s for 1080p -- Borat 84 min x265 3.7 GB -> 15 GB),
  deleted at the end: it counts only while the run lasts, so the runs of one queue need the biggest one once;
* what Premiere plays (match_cuts.edit_media, the "keep the original RAW" change): the original itself (above); an
  .mp4 remux of it (about its size) for an MKV or a picture that starts late; a CRF 17 copy (~0.6 x the analysis copy)
  when the picture cannot be played as it is (a variable frame rate, rotation, interlacing, WebM / VP9 ...); and a
  stereo WAV for A1 (16-bit 48 kHz: 0.7 GB an hour) when the sound is 5.1, not AAC / PCM, starts late, or V1 is a copy;
* the tool's saved analysis of the RAW in work\\cache (the picture proxy up to 3 GB, the RAW index and its sound:
  about 6 GB for a feature film -- measured on this PC's work\\cache) and the run's own files;
* plus a safety margin (an estimate is not a measurement).

A download needs its file (yt-dlp's size, else its bitrate x its length, else a guess from its length and height)
and -- when the picture is VP9 / AV1 -- the converted copy next to it until it is checked; a conversion needs the new
file next to the original; a torrent its chosen files (core.torrent checks that itself before the start).

The numbers are the constants below: change them here. Every function that reads a disk may wait for the network:
call them from a worker thread (``free_within`` gives up on a folder that does not answer)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence

# -- the numbers (change them here) ------------------------------------------------------------------------------------
GB = 1024 ** 3
MB = 1024 ** 2
RUN_FILES = 1 * GB                 # a run's own files (previews, the compare video, the cut list ...) -- generous
RESERVE = 512 * MB                 # always left free (the tool's own margin for a copy: conform.COPY_RESERVE)
SAFETY_SHARE = 0.10                # + 10 % of every estimate, at least RESERVE
# the tool's temporary analysis copy (libx264 CRF 12 veryfast): Borat 84 min x265 1920x1040 3.7 GB -> 15.0 GB =
# 23.9 Mbit/s (24.8 for a full 1080p picture); Rush Hour 98 min -> 8 GB = 11 Mbit/s -- the higher one, scaled with the
# picture's size (4K: 4 x)
ANALYSIS_MBIT_1080P = 25.0
ANALYSIS_MAX_SCALE = 4.0
ANALYSIS_CONTAINERS = {"mp4", "mov"}            # what the tool reads as it is (match_cuts.probe.ae_issues) ...
ANALYSIS_VIDEO = {"h264", "prores"}             # ... 8-bit 4:2:0 H.264 or ProRes ...
ANALYSIS_AUDIO = {"aac"}                        # ... with AAC or PCM sound, a constant frame rate, square pixels,
START_TOL_S = 1e-3                              # ... starting at 0 (match_cuts.probe.START_TOL_S)
# what Premiere plays (match_cuts.edit_media): the original when it can; a CRF 17 slow copy when its picture cannot
# be played as it is (+5 CRF ~ half the bitrate of the analysis copy's CRF 12, rounded up); a stereo WAV for A1
DELIVERY_SHARE = 0.6
PREMIERE_PIX = {"h264": ("yuv420p", "yuvj420p", "yuv420p10le"), "hevc": ("yuv420p", "yuvj420p", "yuv420p10le"),
                "prores": None}                 # (edit_media.PREMIERE_PIX: None = any)
PREMIERE_CONTAINERS = {"mp4", "mov", "m4v"}
WAV_BYTES_S = 48000 * 2 * 2                     # 16-bit PCM, 48 kHz, stereo
# the tool's saved analysis of one RAW (work\cache; this PC, day 13: proxies up to 3.2 GB, a feature film's RAW index
# 2.3 GB, its sound 0.4 GB)
CACHE_FIXED = 300 * MB             # the small stages (frame map, layout, people, captions ...)
CACHE_PROXY_MAX = 3 * GB           # the picture proxy: at most the tool's proxy_budget_bytes
CACHE_PROXY_FRAME = 640 * 360      # its bytes per RAW frame below that (640 px wide, grey)
CACHE_PER_MIN = 30 * MB            # the RAW index and the sound, per minute of RAW
# a download whose size yt-dlp does not say: Mbit/s by picture height (YouTube's usual rates, rounded up)
GUESS_MBIT = ((480, 1.5), (720, 3.0), (1080, 6.0), (1440, 12.0), (2160, 25.0), (100000, 50.0))
GUESS_AUDIO_KBIT = 160
DOWNLOAD_KEEP = 1.1                # a download kept as it is (H.264 / HEVC): its size + 10 %
DOWNLOAD_CONVERT = 4.0             # VP9 / AV1: the original + an H.264 up to ~3 x as big, side by side until checked
CONVERT_REWRAP = 1.05              # the app's conversion (core.convert): rewrap = the same size again ...
CONVERT_ENCODE = 3.0               # ... encode = up to 3 x the original (a 1.08 GB 4K VP9 became 2.9 GB)
# the top strip
LOW = 30 * GB                      # amber under 30 GB free
VERY_LOW = 10 * GB                 # red under 10 GB free
REFRESH_MS = 120_000               # read again every 2 minutes (and when a run, a download or a conversion ends)
FREE_TIMEOUT_S = 8.0               # a network folder that does not answer in 8 s: "not reachable"
PROBE_TIMEOUT_S = 60.0
PROBE_MIN_BYTES = 1 * MB           # smaller files are not read with ffprobe (no real video is that small)
SAFE = {".mp4", ".mov", ".m4v"}    # (night 10) the extensions the tool may place unchanged -- the fallback rule
CREATE_NO_WINDOW = 0x08000000


# -- drives --------------------------------------------------------------------------------------------------------------

def existing(p: Path) -> Path:
    """``p`` or its nearest folder that exists (a run folder is made at the start)."""
    p = Path(p)
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def same_drive(a: Path, b: Path) -> bool:
    """True when ``a`` and ``b`` are on one volume (a hard link can work there)."""
    try:
        return os.stat(existing(a)).st_dev == os.stat(existing(b)).st_dev
    except OSError:
        return False


def drive_of(p: Path | str) -> str:
    """'C:', 'D:' or '\\\\pc\\Shared' from the path's text alone (never touches the disk: a dead network path)."""
    d = os.path.splitdrive(os.path.abspath(str(p)))[0]
    return d.rstrip("\\/") or str(Path(p).anchor or p)


def drive_name(p: Path) -> str:
    """'C:', 'D:' or '\\\\pc\\Shared' -- how the warning names a drive."""
    d = os.path.splitdrive(os.path.abspath(existing(p)))[0]
    return d or str(existing(p).anchor or p)


def size(p: Path | None) -> int:
    try:
        return int(Path(p).stat().st_size) if p is not None else 0
    except OSError:
        return 0


def free_bytes(p: Path) -> int | None:
    try:
        return int(shutil.disk_usage(existing(p)).free)
    except OSError:
        return None


def usage_within(p: Path | str, timeout_s: float = FREE_TIMEOUT_S) -> tuple[int, int] | None:
    """(free, total) bytes of ``p``'s drive, or None when it cannot be read within ``timeout_s`` (a network folder
    that is not there can take 20 s to say so: the read goes on in a thread of its own and is forgotten)."""
    box: dict = {}

    def read() -> None:
        try:
            u = shutil.disk_usage(existing(Path(p)))
            box["u"] = (int(u.free), int(u.total))
        except (OSError, ValueError):
            box["u"] = None
    t = threading.Thread(target=read, daemon=True, name="free-space")
    t.start()
    t.join(timeout_s)
    return box.get("u")


def free_within(p: Path | str, timeout_s: float = FREE_TIMEOUT_S) -> int | None:
    u = usage_within(p, timeout_s)
    return u[0] if u else None


def margin(n: int) -> int:
    """The safety margin on top of an estimate of ``n`` bytes."""
    return int(max(RESERVE, SAFETY_SHARE * max(0, n)))


def gb(n: float | int | None) -> str:
    """'15.2 GB', '850 MB', '2.6 TB' (binary units, as Explorer shows them)."""
    if n is None:
        return "?"
    n = float(n)
    if n >= 1024 * GB:
        return f"{n / (1024 * GB):.1f} TB"
    if n >= GB:
        return f"{n / GB:.1f} GB"
    return f"{max(1, round(n / MB))} MB"


def is_space_error(text: str) -> bool:
    """Does a stop reason say the disk is full (the app then offers Clean up)?"""
    t = (text or "").lower()
    return any(k in t for k in ("not enough free space", "disk is full", "no space left", "not enough room"))


# -- what a video is (one quick ffprobe call) ---------------------------------------------------------------------------

@dataclass
class Media:
    """What an estimate needs to know about a video."""
    path: str
    size: int
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    frames: int = 0
    vcodec: str = ""
    bit_depth: int = 8
    pix_fmt: str = ""
    container: str = ""
    acodecs: tuple[str, ...] = ()
    channels: int = 0
    vfr: bool = False
    sar_ok: bool = True
    rotation: int = 0
    v_start: float = 0.0            # the picture's first time (s)
    a_start: float = 0.0            # the sound's first time (s)
    interlaced: bool = False


_PROBED: dict = {}


def _frac(s: Any) -> float:
    try:
        f = Fraction(str(s))
        return float(f) if f > 0 else 0.0
    except (ValueError, ZeroDivisionError, TypeError):
        return 0.0


def probe(path: Path | str, timeout_s: float = PROBE_TIMEOUT_S) -> Media | None:
    """The video's codec, size, length, sound (one ffprobe call, nothing decoded; cached by the file's size and
    time); None when it cannot be read (not a video, ffprobe missing)."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    if st.st_size < PROBE_MIN_BYTES:                      # no real video is this small: not worth an ffprobe call
        return None
    key = (str(p), st.st_size, st.st_mtime_ns)
    if key in _PROBED:
        return _PROBED[key]
    exe = os.environ.get("FFPROBE", "ffprobe")
    try:
        r = subprocess.run([exe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(p)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s,
                           creationflags=CREATE_NO_WINDOW)
        js = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        js = {}
    streams = js.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    if v is None:
        _PROBED[key] = None
        return None
    fmt = js.get("format") or {}
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    try:
        dur = float(v.get("duration") or fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    fps, avg = _frac(v.get("r_frame_rate")), _frac(v.get("avg_frame_rate"))
    pix = v.get("pix_fmt") or ""
    depth = 10 if any(x in pix for x in ("10", "12", "16")) else 8
    try:
        depth = int(v.get("bits_per_raw_sample") or depth)
    except (TypeError, ValueError):
        pass
    try:
        frames = int(v.get("nb_frames") or 0)
    except (TypeError, ValueError):
        frames = 0
    name, ext = fmt.get("format_name") or "", p.suffix.lower()
    container = ("mov" if ext == ".mov" else "mp4") if ("mp4" in name or "mov" in name) else \
        ("webm" if ext == ".webm" else "mkv") if ("matroska" in name or "webm" in name) else \
        (name.split(",")[0] or ext.lstrip("."))
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rot = int(round(abs(float(sd["rotation"])))) % 360
            except (TypeError, ValueError):
                pass
    sar = v.get("sample_aspect_ratio") or "1:1"

    def start(s: dict) -> float:
        try:
            return float(s.get("start_time") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    m = Media(str(p), int(st.st_size), dur, int(v.get("width") or 0), int(v.get("height") or 0), fps or avg,
              frames or int(dur * (fps or avg or 0)), str(v.get("codec_name") or ""), depth, pix, container,
              tuple(str(a.get("codec_name") or "") for a in auds), max([int(a.get("channels") or 0) for a in auds]
                                                                      or [0]),
              bool(fps and avg and abs(fps - avg) / fps > 0.002), sar in ("1:1", "0:1", "N/A"), rot,
              start(v), start(auds[0]) if auds else 0.0,
              (v.get("field_order") or "progressive") not in ("progressive", "unknown"))
    _PROBED[key] = m
    return m


# -- what a run writes ----------------------------------------------------------------------------------------------

def analysis_copy_why(m: Media | None) -> str:
    """'' when the matching tool reads the RAW as it is; else why it makes its temporary analysis copy (plain words) --
    the tool's own rule (match_cuts.probe.ae_issues) as far as one quick look can tell."""
    if m is None:
        return ""
    why = []
    if m.vcodec not in ANALYSIS_VIDEO:
        why.append({"hevc": "x265 / HEVC", "av1": "AV1", "vp9": "VP9"}.get(m.vcodec, m.vcodec.upper() or "its codec"))
    elif m.vcodec == "h264" and (m.bit_depth > 8 or (m.pix_fmt and m.pix_fmt not in ("yuv420p", "yuvj420p"))):
        why.append(f"{m.bit_depth}-bit" if m.bit_depth > 8 else m.pix_fmt)
    if m.container not in ANALYSIS_CONTAINERS:
        why.append(m.container.upper() or "its file type")
    if m.vfr:
        why.append("a variable frame rate")
    if abs(m.v_start) > START_TOL_S:
        why.append("a picture that starts late")
    bad_audio = sorted({a.upper() for a in m.acodecs if a and a not in ANALYSIS_AUDIO and not a.startswith("pcm_")})
    if bad_audio:
        why.append(f"{'/'.join(bad_audio)} sound")
    elif m.acodecs and abs(m.a_start - m.v_start) > START_TOL_S:
        why.append("sound that starts late")
    if m.rotation or not m.sar_ok or m.width % 2 or m.height % 2 or m.interlaced:
        why.append("its picture shape")
    return ", ".join(why)


def analysis_copy_bytes(m: Media) -> int:
    """The temporary analysis copy's size: ANALYSIS_MBIT_1080P scaled with the picture's size, never less than the
    RAW itself."""
    px = max(1, m.width * m.height) / (1920 * 1080)
    rate = ANALYSIS_MBIT_1080P * 1e6 / 8 * min(ANALYSIS_MAX_SCALE, max(0.25, px))
    return int(max(m.size, rate * max(0.0, m.duration)))


def premiere_picture(m: Media | None) -> tuple[str, str]:
    """What V1 plays (match_cuts.edit_media.picture_choice, as far as one quick look can tell): ('original', '') --
    the RAW itself, linked; ('remux', why) -- its picture moved into an .mp4 (about its size); ('convert', why) -- a
    CRF 17 copy, when Premiere cannot play the picture as it is."""
    if m is None or not analysis_copy_why(m):
        return "original", ""
    if m.vfr or m.rotation or not m.sar_ok or m.width % 2 or m.height % 2 or m.interlaced:
        return "convert", "its picture cannot be played frame for frame as it is"
    allowed = PREMIERE_PIX.get(m.vcodec, ())
    if m.vcodec not in PREMIERE_PIX or (allowed is not None and m.pix_fmt not in allowed):
        return "convert", f"Premiere cannot open {m.vcodec.upper() or 'this'} video as it is"
    if abs(m.v_start) > START_TOL_S and m.vcodec in ("h264", "hevc"):
        return "remux", "its picture starts late"
    if m.container in PREMIERE_CONTAINERS:
        return "original", ""
    if m.container == "mkv" and m.vcodec in ("h264", "hevc"):
        return "remux", "an MKV file"
    return "convert", f"Premiere cannot open .{m.container or '?'} files"


def premiere_copy_bytes(m: Media, how: str) -> int:
    """V1's own copy: a remux is about the RAW's size, a CRF 17 copy DELIVERY_SHARE of the analysis copy."""
    return int(m.size) if how == "remux" else int(DELIVERY_SHARE * analysis_copy_bytes(m)) if how == "convert" else 0


def stereo_bytes(m: Media | None, picture: str = "original") -> int:
    """A1's stereo WAV (match_cuts.edit_media.sound_choice): when the sound is 5.1, not AAC / PCM, starts late, or V1
    plays a copy (``picture`` remux / convert); 0 when V1's own sound is played."""
    if m is None or not m.acodecs:
        return 0
    plain = (m.channels <= 2 and all(a in ANALYSIS_AUDIO or a.startswith("pcm_") for a in m.acodecs if a)
             and abs(m.a_start - m.v_start) <= START_TOL_S)
    if plain and picture == "original":
        return 0
    return int(m.duration * WAV_BYTES_S)


def cache_bytes(m: Media | None, raw_size: int = 0) -> int:
    """The tool's saved analysis of a RAW (work\\cache): the proxy (its frames, at most CACHE_PROXY_MAX), the RAW
    index and the sound (per minute) and the small stages. Unreadable: about the RAW's size, at most a feature film's."""
    if m is None or m.duration <= 0:
        return CACHE_FIXED + int(min(CACHE_PROXY_MAX, raw_size))
    frames = m.frames or int(m.duration * (m.fps or 25.0))
    proxy = min(CACHE_PROXY_MAX, frames * CACHE_PROXY_FRAME)
    return int(CACHE_FIXED + proxy + CACHE_PER_MIN * m.duration / 60.0)


@dataclass
class Part:
    """One thing a step writes."""
    folder: Path                    # a folder on the drive it goes to
    bytes: int
    what: str                       # plain words: "a temporary copy of the RAW for the analysis (x265 / HEVC)"
    temp: bool = False              # deleted at the end of the run: needed only while it runs

    @property
    def drive(self) -> str:
        return drive_of(self.folder)


def _work_dir() -> Path:
    from . import paths
    return paths.work_dir()


def media_parts(m: Media, folder: Path, who: str = "") -> list[Part]:
    """What a run makes from its RAW in ``folder``: the temporary analysis copy, V1's own copy (a remux or a CRF 17
    copy) and A1's stereo WAV (module docstring)."""
    out = []
    why = analysis_copy_why(m)
    if why:
        out.append(Part(folder, analysis_copy_bytes(m), f"a temporary copy of the RAW for {who or 'the'} analysis "
                                                        f"({why}; deleted at the end)", temp=True))
    how, hwhy = premiere_picture(m)
    if how == "remux":
        out.append(Part(folder, premiere_copy_bytes(m, how), f"the RAW moved into an .mp4 for Premiere ({hwhy})"))
    elif how == "convert":
        out.append(Part(folder, premiere_copy_bytes(m, how), f"a copy of the RAW for Premiere ({hwhy}; H.264 CRF 17)"))
    st = stereo_bytes(m, how)
    if st:
        out.append(Part(folder, st, "the RAW's stereo sound for Premiere (WAV)"))
    return out


def run_parts(competitor: Path | None, raw: Path, run_dir: Path, work_dir: Path | None = None,
              raw_media: Media | None = None, look: bool = True) -> list[Part]:
    """What one Match cuts run writes, and where (module docstring). ``look``: read the RAW with ffprobe (else only
    its size and name)."""
    run_dir, raw = Path(run_dir), Path(raw)
    parts = [Part(run_dir, RUN_FILES, "the run's own files")]
    for role, f in (("RAW", raw), ("competitor", competitor)):
        if f is None:
            continue
        if not same_drive(Path(f), run_dir):
            n = size(Path(f))
            parts.append(Part(run_dir, n, f"a copy of the {role} ({gb(n)}: another drive, it cannot be linked)"))
    m = raw_media if raw_media is not None else (probe(raw) if look else None)
    if m is not None:
        parts += media_parts(m, run_dir)
    elif raw.suffix.lower() not in SAFE:                   # unreadable now: by its name (night 10's rule)
        n = size(raw)
        parts.append(Part(run_dir, 2 * n, f"the RAW's converted copy (~{gb(2 * n)})", temp=True))
    if competitor is not None and Path(competitor).suffix.lower() not in SAFE:
        n = size(Path(competitor))
        parts.append(Part(run_dir, 2 * n, f"the competitor's converted copy (~{gb(2 * n)})"))
    parts.append(Part(Path(work_dir or _work_dir()), cache_bytes(m, size(raw)),
                      "the tool's saved analysis of the RAW (work\\cache)"))
    return parts


def need_bytes(competitor: Path, raw: Path, run_dir: Path) -> tuple[int, str]:
    """(bytes the run needs on its folder's drive with the margin, why in plain words) -- night 10's call."""
    parts = [p for p in run_parts(Path(competitor), Path(raw), Path(run_dir)) if p.drive == drive_of(run_dir)
             and "work\\cache" not in p.what]
    n = sum(p.bytes for p in parts)
    extra = [p.what for p in parts if p.what != "the run's own files"]
    return n + margin(n), (", ".join(extra) + " and the run's own files") if extra else "the run's own files"


@dataclass
class DriveNeed:
    """What the steps about to start need on one drive."""
    drive: str
    folder: Path
    keep: int = 0                   # bytes that stay
    temp: int = 0                   # the biggest temporary part (runs go one after another)
    lines: list | None = None       # [(name, bytes, what)]

    @property
    def total(self) -> int:
        n = self.keep + self.temp
        return n + margin(n)


def per_drive(items: Iterable[tuple[str, Sequence[Part]]]) -> list[DriveNeed]:
    """The parts of several steps [(name, parts)] added up per drive: what stays adds up, the temporary parts of
    steps that run one after another count once (the biggest)."""
    out: dict[str, DriveNeed] = {}
    for name, parts in items:
        temps: dict[str, int] = {}
        for p in parts:
            d = out.setdefault(p.drive, DriveNeed(p.drive, p.folder, lines=[]))
            if p.temp:
                temps[p.drive] = temps.get(p.drive, 0) + p.bytes
            else:
                d.keep += p.bytes
            d.lines.append((name, p.bytes, p.what))
        for drive, t in temps.items():
            out[drive].temp = max(out[drive].temp, t)
    return list(out.values())


def shortfalls(needs: Iterable[DriveNeed], timeout_s: float = FREE_TIMEOUT_S,
               action: str = "free some space (Board -> Clean up), then press Start again",
               most: int | None = None) -> list[str]:
    """Plain words for every drive without room: 'not enough free space on C: -- about 25.3 GB is needed there,
    only 9.2 GB is free (video1: a temporary copy of the RAW ... 15.2 GB; ...) -- free some space ...'. ``most``:
    only the biggest parts (a short line for a row). A drive that cannot be read is not called full."""
    out = []
    for d in needs:
        free = free_within(d.folder, timeout_s)
        if free is None or free >= d.total:
            continue
        names: dict[str, list] = {}
        for name, n, what in d.lines or []:
            names.setdefault(name, []).append((n, what))
        bits = []
        for name, items in names.items():
            items.sort(key=lambda x: -x[0])
            items = [x for x in items if x[0] >= 50 * MB][:most]
            detail = "; ".join(f"{what} {gb(n)}" for n, what in items)
            bits.append(f"{name}: {detail}" if name and detail else detail)
        detail = " | ".join(b for b in bits if b)
        out.append(f"not enough free space on {d.drive} -- about {gb(d.total)} is needed there, only {gb(free)} is "
                   f"free{f' ({detail})' if detail else ''} -- {action}")
    return out


def problems(runs: Sequence[tuple[str, Path, Path, Path]], work_dir: Path | None = None) -> list[str]:
    """Plain-words warnings for the runs [(name, competitor, raw, run folder)] about to start (night 10's call; day
    13: with the analysis copy, what Premiere plays, the tool's saved analysis and the margin)."""
    items = [(name, run_parts(Path(comp), Path(raw), Path(run_dir), work_dir))
             for name, comp, raw, run_dir in runs if comp is not None and raw is not None]
    return shortfalls(per_drive(items))


def run_problem(name: str, competitor: Path | None, raw: Path, run_dir: Path, work_dir: Path | None = None) -> str:
    """'' when one run fits, else why not (the queue asks this again just before each run starts)."""
    probs = shortfalls(per_drive([("", run_parts(competitor, raw, run_dir, work_dir))]),
                       action="free some space (Board -> Clean up), then press Start again", most=3)
    return "; ".join(probs)


# -- downloads, conversions --------------------------------------------------------------------------------------------

def guess_bytes(duration: float | None, height: int | None) -> int:
    """A download whose size nobody says: GUESS_MBIT by picture height x its length, + the sound."""
    if not duration:
        return 0
    h = int(height or 1080)
    mbit = next(r for top, r in GUESS_MBIT if h <= top)
    return int(duration * (mbit * 1e6 + GUESS_AUDIO_KBIT * 1e3) / 8)


def format_bytes(f: dict, duration: float | None) -> float:
    """One yt-dlp format's size: filesize / filesize_approx, else its bitrate x the length; 0 when unknown."""
    if f.get("filesize") or f.get("filesize_approx"):
        return float(f.get("filesize") or f.get("filesize_approx"))
    rate = f.get("tbr") or f.get("vbr") or f.get("abr")
    return float(rate) * 125.0 * float(duration) if rate and duration else 0.0


def download_bytes(li: Any) -> int:
    """The room a link needs (download.LinkInfo): the chosen formats' size -- about 4 x that when the picture is VP9
    / AV1 and will be converted (the original and a visually lossless H.264 side by side until the check), else
    1.1 x; a guess from the length and height when yt-dlp says neither size nor bitrate."""
    info = dict(getattr(li, "info", None) or {})
    dur = getattr(li, "duration", None)
    try:
        from . import download as D
        ch = D.pick_formats(info.get("formats") or ([info] if info.get("url") else []))
        parts, kind = ch.parts, ch.video_kind
    except Exception:  # noqa: BLE001 - nothing readable: the guess
        parts, kind = [], None
    n = sum(format_bytes(f, dur) for f in parts)
    if n <= 0:
        n = guess_bytes(dur, getattr(li, "height", None))
    return int(n * (DOWNLOAD_KEEP if kind in ("h264", "hevc") else DOWNLOAD_CONVERT))


def convert_need(size_in: int, how: str) -> int:
    """Bytes the app's conversion needs next to the original (core.convert's plan: keep / rewrap / encode)."""
    n = int(size_in * {"rewrap": CONVERT_REWRAP, "encode": CONVERT_ENCODE}.get(how, 0.0))
    return n + margin(n) if n else 0


def convert_problem(src_size: int, how: str, dest_dir: Path, name: str = "the video") -> str:
    """'' when the conversion fits on ``dest_dir``'s drive, else why not in plain words."""
    need = convert_need(src_size, how)
    if not need:
        return ""
    free = free_within(dest_dir)
    if free is None or free >= need:
        return ""
    what = "copying it into MP4" if how == "rewrap" else "converting it to H.264"
    return (f"not enough free space on {drive_of(dest_dir)} -- {what} needs about {gb(need)} next to the "
            f"original ({name}: {gb(src_size)}), only {gb(free)} is free -- free some space (Board -> Clean up) "
            "and try again")


def after_convert(m: Media | None, how: str) -> Media | None:
    """The RAW as the edit will get it after the app's own conversion (core.convert): rewrap -> the same picture in an
    .mp4 (the sound AAC when it was not); encode -> H.264 (10-bit stays 10-bit) with AAC, a constant frame rate,
    starting at 0, about CONVERT_ENCODE x the size."""
    if m is None or how not in ("rewrap", "encode"):
        return m
    from dataclasses import replace
    aac = tuple("aac" for _ in m.acodecs)
    if how == "rewrap":
        return replace(m, container="mp4", acodecs=aac, v_start=0.0, a_start=0.0)
    ten = m.bit_depth > 8
    return replace(m, container="mp4", vcodec="h264", pix_fmt="yuv420p10le" if ten else "yuv420p", acodecs=aac,
                   vfr=False, v_start=0.0, a_start=0.0, size=int(m.size * CONVERT_ENCODE))


def new_video_problem(links: Sequence[Any], raw_file: Path | None, root: Path, convert_how: str = "",
                      work_dir: Path | None = None, makes_edit: bool = True, sizer: Any = None) -> str:
    """'' when a New video fits, else why not in plain words: its downloads (``sizer``, default download_bytes) or
    the RAW file copied into the project folder when it is on another drive, their conversions and -- ``makes_edit``
    -- the run that makes its edit (the tool's saved analysis, its analysis copy of a RAW file it cannot read as it
    is). Nothing known about the files (no size, no length): not checked (as before)."""
    root = Path(root)
    sizer = sizer or download_bytes
    parts = [Part(root, int(sizer(li) or 0), "the download" + (f" of {getattr(li, 'title', '')[:40]}"
                                                                if getattr(li, "title", "") else ""))
             for li in links if li is not None]
    m = None
    if raw_file is not None:
        raw_file = Path(raw_file)
        n = size(raw_file)
        if not same_drive(raw_file, root):
            parts.append(Part(root, n, "the RAW copied into the project folder (another drive)"))
        conv = int(n * {"encode": CONVERT_ENCODE, "rewrap": CONVERT_REWRAP}.get(convert_how, 0.0))
        if conv:
            parts.append(Part(root, conv, "the RAW's conversion for Premiere"))
        m = after_convert(probe(raw_file), convert_how)
    if not any(p.bytes for p in parts) and (raw_file is None or not size(raw_file)):
        return ""
    if makes_edit:
        dur = max([float(getattr(li, "duration", 0) or 0) for li in links if li is not None] or [0.0])
        if m is not None:
            parts += media_parts(m, root, "the tool's")
            parts.append(Part(Path(work_dir or _work_dir()), cache_bytes(m, m.size),
                              "the tool's saved analysis (work\\cache)"))
        else:
            fake = Media("", 0, dur, 1920, 1080, 25.0) if dur else None
            parts.append(Part(Path(work_dir or _work_dir()), cache_bytes(fake, size(raw_file) if raw_file else 0),
                              "the tool's saved analysis (work\\cache)"))
        parts.append(Part(root, RUN_FILES, "the edit's own files"))
    probs = shortfalls(per_drive([("", parts)]), action="free some space (Board -> Clean up) and press Try again",
                       most=2)
    return "; ".join(probs)


# -- the top strip -----------------------------------------------------------------------------------------------------

def strip_folders(save_root: Path | str | None = None, local_root: Path | str | None = None) -> list[tuple[str, str]]:
    """[(name, a folder there)] the top strip shows: C: (Windows, the project, the tool's work files) and the
    drives of this computer's projects folder and of "Save projects to" when they are other ones (D:, the laptop's
    \\\\192.168.101.11\\Shared). From the paths' text alone (the disks are read later, in the background)."""
    from . import paths
    system = os.environ.get("SystemDrive", "C:") or "C:"
    out: list[tuple[str, str]] = [(drive_of(system + "\\"), system + "\\")]
    for f in (paths.repo(), local_root or paths.projects_root(), save_root):
        if not f:
            continue
        d = drive_of(f)
        if d.upper() not in {x.upper() for x, _ in out}:
            out.append((d, str(f)))
    return out


@dataclass
class DriveFree:
    name: str
    free: int | None                # None: could not be read (a network folder that is away)
    total: int | None = None

    @property
    def level(self) -> str:
        """'ok', 'low' (amber, under 30 GB), 'very low' (red, under 10 GB) or 'unknown'."""
        if self.free is None:
            return "unknown"
        return "very low" if self.free < VERY_LOW else "low" if self.free < LOW else "ok"

    def text(self) -> str:
        return f"{self.name} {gb(self.free)}" if self.free is not None else f"{self.name} not reachable"


def read_strip(folders: Sequence[tuple[str, str]], timeout_s: float = FREE_TIMEOUT_S) -> list[DriveFree]:
    """The free space of the strip's drives (each read in its own thread: a dead network folder gives up after
    ``timeout_s`` without holding up the others)."""
    res: dict[int, Any] = {}

    def one(i: int, folder: str) -> None:
        res[i] = usage_within(folder, timeout_s)
    threads = [threading.Thread(target=one, args=(i, f), daemon=True) for i, (_n, f) in enumerate(folders)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout_s + 1.0)
    out = []
    for i, (name, _f) in enumerate(folders):
        u = res.get(i)
        out.append(DriveFree(name, u[0] if u else None, u[1] if u else None))
    return out


def summary(paths_: Iterable[Path]) -> str:
    """'C: 64.2 GB free, D: 2619.8 GB free' for the drives of ``paths_``."""
    seen, bits = set(), []
    for p in paths_:
        d = drive_name(Path(p))
        if d in seen:
            continue
        seen.add(d)
        f = free_bytes(Path(p))
        bits.append(f"{d} {f / GB:.1f} GB free" if f is not None else f"{d} ?")
    return ", ".join(bits)
