"""Torrent downloads (day 12, section 6c) -- Qt-free.

The engine is aria2c (https://github.com/aria2/aria2, a small portable program driven over its local JSON-RPC):
libtorrent, the engine qBittorrent uses, has no wheel for this venv's Python 3.14 (PyPI: up to 3.13), so the spec's
fallback is used. ``setup_app.py`` puts ``aria2c.exe`` into ``tools\\app\\bin``.

The steps, as the app shows them:

1. ``TorrentSession.probe(source)``: a magnet link (its file list is fetched from the peers, "metadata") or a
   .torrent file -> ``Meta``: the name, the files with their sizes, the total, the seeders seen, the BIGGEST VIDEO.
2. ``check_space(meta, indexes, save_dir)``: the chosen files + a margin must fit on the save folder's disk.
3. ``start(meta, indexes, save_dir)`` -> a job id. Only the chosen files are downloaded.
4. ``poll(job_id)`` / ``poll_all()``: live seeders, peers, speed, % done, time left, a 0-5 health value; ``pause`` /
   ``resume`` / ``remove``. A job that has had no one to download from for ``stall_minutes`` says so in plain words.
5. Finished: sharing stops (unless Settings -> "Keep sharing after a download"), the status says ``finished`` with the
   chosen video's path.

Every job is kept in ``<app data>\\torrents\\jobs.json`` with a copy of its .torrent, so after an app restart
``restore()`` gives them back to aria2c, which continues from its own ``.aria2`` progress files next to the download.
aria2c stops by itself when the app's process ends (``--stop-with-process``).

Errors are ``TorrentError`` with a message in plain words (a broken magnet, no one sharing, the disk full).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import machine as M

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".ts", ".m2ts", ".webm", ".flv", ".mpg", ".mpeg",
              ".vob", ".divx", ".ogv", ".3gp"}
PC_DIR = Path(r"D:\Shared\Downloads\Torrents")
LAPTOP_DIR = Path(rf"\\{M.PC_ADDRESS}\{M.SHARE}\Downloads\Torrents")
EXE_ENV = "MOVIERECAPS_ARIA2C"          # a different aria2c.exe (the tests)
STATE_ENV = "MOVIERECAPS_TORRENTS"      # a different state folder (the tests)
MARGIN_MIN = 512 * 1024 ** 2            # free space kept after a download: 512 MB ...
MARGIN_SHARE = 0.03                     # ... or 3 % of what is downloaded, whichever is more
PROBE_MINUTES = 5.0                     # a magnet's file list: how long to try before "no one is sharing"
STALL_MINUTES = 10.0                    # a running download with no one to download from


class TorrentError(Exception):
    """A torrent problem, said in plain words (the message is shown as it is)."""


# ---------------------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------------------

def human_size(n: float | int | None) -> str:
    """1.2 GB, 640 MB, 12 KB (base 1024, as Windows shows sizes)."""
    if n is None:
        return "?"
    n = float(n)
    for unit, k in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= k:
            v = n / k
            return f"{v:.2f} {unit}" if unit in ("TB", "GB") else f"{v:.0f} {unit}"
    return f"{int(n)} B"


def human_speed(bps: float | int | None) -> str:
    if not bps:
        return "0 MB/s"
    bps = float(bps)
    if bps >= 1024 ** 2:
        return f"{bps / 1024 ** 2:.1f} MB/s"
    return f"{bps / 1024:.0f} KB/s"


def human_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    s = int(round(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{(s + 59) // 60} min"
    h, m = divmod((s + 59) // 60, 60)
    return f"{h} h {m:02d} min"


def health_bars(seeders: int | None) -> int:
    """0-5 bars from the seeders: none 0, 1 -> 1, 2-4 -> 2, 5-14 -> 3, 15-49 -> 4, 50+ -> 5."""
    s = int(seeders or 0)
    if s <= 0:
        return 0
    for bars, top in ((1, 1), (2, 4), (3, 14), (4, 49)):
        if s <= top:
            return bars
    return 5


def is_video(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def default_dir(kind: str | None = None, settings: Any = None) -> Path:
    """Where torrents are saved: Settings -> "Save torrents to", else D:\\Shared\\Downloads\\Torrents on the PC and
    the same folder over the network on the laptop."""
    s = settings
    if s is None:
        try:
            from . import settings as S
            s = S.load()
        except Exception:  # noqa: BLE001 - the defaults
            s = None
    t = (getattr(s, "torrent_dir", "") or "").strip() if s is not None else ""
    if t:
        return Path(os.path.expandvars(os.path.expanduser(t)))
    k = kind or (M.which(s) if s is not None else M.which())
    return LAPTOP_DIR if k == M.LAPTOP else PC_DIR


def keep_sharing(settings: Any = None) -> bool:
    s = settings
    if s is None:
        try:
            from . import settings as S
            s = S.load()
        except Exception:  # noqa: BLE001
            return False
    return bool(getattr(s, "torrent_keep_sharing", False))


def state_dir() -> Path:
    """The jobs list and the .torrent copies: MOVIERECAPS_TORRENTS, else <app data>\\torrents."""
    env = os.environ.get(STATE_ENV, "").strip()
    if env:
        return Path(env)
    from . import settings as S
    return S.app_dir() / "torrents"


def free_space(folder: str | Path) -> int | None:
    """Free bytes on the disk of ``folder`` (or of its nearest existing parent); None when it cannot be read (a
    network folder that is not reachable)."""
    p = Path(folder)
    for q in (p, *p.parents):
        try:
            if q.exists():
                return int(shutil.disk_usage(q).free)
        except OSError:
            continue
    return None


def drive_name(folder: str | Path) -> str:
    """'D:' or '\\\\192.168.101.11\\Shared' -- how the free space is named ("free on D:")."""
    d = Path(folder).drive
    return d or str(folder)


def margin(size: int) -> int:
    return int(max(MARGIN_MIN, size * MARGIN_SHARE))


# ---------------------------------------------------------------------------------------------------------------------
# bencode, .torrent files, magnet links
# ---------------------------------------------------------------------------------------------------------------------

def bencode(v: Any) -> bytes:
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, int):
        return b"i%de" % v
    if isinstance(v, str):
        v = v.encode("utf-8")
    if isinstance(v, (bytes, bytearray)):
        return b"%d:" % len(v) + bytes(v)
    if isinstance(v, (list, tuple)):
        return b"l" + b"".join(bencode(x) for x in v) + b"e"
    if isinstance(v, dict):
        items = sorted(((k.encode("utf-8") if isinstance(k, str) else k), x) for k, x in v.items())
        return b"d" + b"".join(bencode(k) + bencode(x) for k, x in items) + b"e"
    raise TypeError(f"cannot bencode {type(v).__name__}")


def _bdecode(data: bytes, i: int, spans: dict | None, depth: int) -> tuple[Any, int]:
    c = data[i:i + 1]
    if c == b"i":
        j = data.index(b"e", i)
        return int(data[i + 1:j]), j + 1
    if c == b"l":
        i += 1
        out = []
        while data[i:i + 1] != b"e":
            v, i = _bdecode(data, i, None, depth + 1)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        d: dict = {}
        while data[i:i + 1] != b"e":
            k, i = _bdecode(data, i, None, depth + 1)
            start = i
            v, i = _bdecode(data, i, None, depth + 1)
            if spans is not None and depth == 0:
                spans[k] = (start, i)
            d[k] = v
        return d, i + 1
    if c.isdigit():
        j = data.index(b":", i)
        n = int(data[i:j])
        if j + 1 + n > len(data):
            raise ValueError("cut short")
        return data[j + 1:j + 1 + n], j + 1 + n
    raise ValueError(f"bad byte at {i}")


def bdecode(data: bytes, spans: dict | None = None) -> Any:
    v, _ = _bdecode(data, 0, spans, 0)
    return v


@dataclass
class TFile:
    index: int          # 0-based, in the torrent's order
    path: str           # inside the torrent ("Subs/English.srt")
    size: int

    @property
    def video(self) -> bool:
        return is_video(self.path)


@dataclass
class Meta:
    name: str
    info_hash: str                      # 40 hex, lower case
    files: list[TFile]
    torrent: bytes = field(default=b"", repr=False)       # the .torrent's bytes
    trackers: list[str] = field(default_factory=list)
    seeders: int | None = None          # seen while the file list was read; None = not known yet
    single: bool = False                # one file, no folder

    @property
    def total(self) -> int:
        return sum(f.size for f in self.files)

    def biggest_video(self) -> int | None:
        """The index of the largest video file (the one preselected, "BIGGEST VIDEO"); the largest file when there
        is no video."""
        vids = [f for f in self.files if f.video] or list(self.files)
        if not vids:
            return None
        return max(vids, key=lambda f: (f.size, -f.index)).index

    def size_of(self, indexes: Iterable[int]) -> int:
        ix = set(indexes)
        return sum(f.size for f in self.files if f.index in ix)

    def disk_path(self, save_dir: str | Path, index: int) -> Path:
        """Where aria2c writes file ``index``: <save>\\<file> for one file, <save>\\<name>\\<path> for a folder (the
        names as the torrent gives them -- aria2c may escape some characters on disk; TorrentSession asks it for
        the real paths). TorrentError when the torrent's names lead outside the save folder ("..", a drive, a full
        path)."""
        f = self.files[index]
        base = Path(save_dir)
        parts = [p for p in f.path.split("/") if p]
        out = base.joinpath(*parts) if self.single else base.joinpath(_safe(self.name), *parts)
        if not inside(base, out):
            raise TorrentError("This torrent's file names point outside the save folder -- it is not safe to "
                               "download.")
        return out

    def chosen_video(self, indexes: Iterable[int]) -> int | None:
        ix = sorted(set(indexes))
        pick = [self.files[i] for i in ix if 0 <= i < len(self.files)]
        vids = [f for f in pick if f.video] or pick
        return max(vids, key=lambda f: (f.size, -f.index)).index if vids else None


def _safe(name: str) -> str:
    return name


_WIN_BAD = set('"*:<>?\\|')


def aria2_escape(part: str) -> str:
    """One name of a torrent's path as aria2c writes it on Windows (its util::escapePath): control characters and
    the characters Windows forbids in names ( " * : < > ? \\ | ) become %XX."""
    return "".join(f"%{ord(c):02X}" if ord(c) < 0x20 or ord(c) == 0x7F or c in _WIN_BAD else c for c in part)


def aria2_path(meta: "Meta", save_dir: str | Path, index: int) -> Path:
    """Where aria2c itself writes file ``index`` on Windows (names escaped like aria2c does: 'a:b' -> 'a%3Ab')."""
    f = meta.files[index]
    parts = [aria2_escape(p) for p in f.path.split("/") if p]
    base = Path(save_dir)
    return base.joinpath(*parts) if meta.single else base.joinpath(aria2_escape(meta.name), *parts)


def control_file(meta: "Meta", save_dir: str | Path) -> Path:
    """aria2c's progress file of a download: <save>\\<file>.aria2 for one file, <save>\\<name>.aria2 for a folder."""
    base = Path(save_dir)
    top = aria2_escape(meta.files[0].path if meta.single and meta.files else meta.name)
    return base / f"{top}.aria2"


def old_name(path: str | Path) -> Path:
    """'<name> (old)<ext>' next to it -- '(old 2)', '(old 3)' ... when that is taken too."""
    p = Path(path)
    k = 1
    while True:
        cand = p.with_name(f"{p.stem} (old){p.suffix}" if k == 1 else f"{p.stem} (old {k}){p.suffix}")
        if not cand.exists():
            return cand
        k += 1


def _pieces(meta: "Meta") -> tuple[int, bytes]:
    """(piece length, the 20-byte SHA-1 of every piece) of the torrent; (0, b'') when it cannot be read."""
    try:
        info = bdecode(meta.torrent)[b"info"]
        return int(info[b"piece length"]), bytes(info[b"pieces"])
    except Exception:  # noqa: BLE001 - no piece list: nothing can be checked here
        return 0, b""


SAMPLE_PIECES = 32                      # pieces read to tell whether a file on disk is this torrent's (a part of it)
SAMPLE_BYTES = 256 * 1024 ** 2          # ... at most this much read


def file_matches(meta: "Meta", index: int, path: str | Path, size: int | None = None) -> bool | None:
    """Is the file at ``path`` (``size`` bytes of it, from its start) torrent file ``index`` or a part of it? Up to
    SAMPLE_PIECES of the pieces that lie whole inside it are read and compared with the torrent's own SHA-1s:
    True at the first one that matches, False when none does, None when no piece lies whole inside it (too short
    to tell). A part-downloaded file has holes (zeros) where pieces are missing: one right piece is enough."""
    plen, hashes = _pieces(meta)
    if plen <= 0 or not hashes:
        return None
    off = sum(f.size for f in meta.files[:index])
    total = meta.total
    have = int(size if size is not None else Path(path).stat().st_size)
    end = off + min(have, meta.files[index].size)
    first = -(-off // plen)
    cands = [k for k in range(first, len(hashes) // 20) if min((k + 1) * plen, total) <= end]
    if not cands:
        return None
    n = min(len(cands), SAMPLE_PIECES, max(1, SAMPLE_BYTES // plen))
    pick = sorted({cands[round(i * (len(cands) - 1) / max(1, n - 1))] for i in range(n)})
    try:
        with open(path, "rb") as fh:
            for k in pick:
                a = k * plen
                fh.seek(a - off)
                chunk = fh.read(min((k + 1) * plen, total) - a)
                if hashlib.sha1(chunk).digest() == hashes[20 * k:20 * k + 20]:
                    return True
    except OSError:
        return None
    return False


def inside(base: str | Path, path: str | Path) -> bool:
    """``path`` is inside the folder ``base`` (after "..", drive letters and full paths are resolved): a torrent's
    names never lead out of the save folder."""
    try:
        b = os.path.normcase(os.path.abspath(str(base)))
        p = os.path.normcase(os.path.abspath(os.path.join(str(base), str(path))))
        return p != b and os.path.commonpath([b, p]) == b
    except (TypeError, ValueError, OSError):
        return False


def _text(b: Any) -> str:
    if isinstance(b, bytes):
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1")
    return str(b)


def read_torrent(data: bytes | str | Path) -> Meta:
    """A .torrent's name, files and sizes (bytes, or the path of the file). TorrentError when it is not one."""
    if isinstance(data, (str, Path)):
        try:
            data = Path(data).read_bytes()
        except OSError as e:
            raise TorrentError(f"The .torrent file could not be read: {e.strerror or e}") from e
    spans: dict = {}
    try:
        top = bdecode(data, spans)
        info = top[b"info"]
        name = _text(info.get(b"name.utf-8", info.get(b"name", b"")))
    except Exception as e:  # noqa: BLE001
        raise TorrentError("This is not a working .torrent file — it is broken or only part of it was "
                           "downloaded. Download the .torrent again.") from e
    if b"info" not in spans:
        raise TorrentError("This .torrent file has no file list. Download the .torrent again.")
    s, e = spans[b"info"]
    ih = hashlib.sha1(data[s:e]).hexdigest()
    files: list[TFile] = []
    single = False
    try:
        if b"files" in info:
            for i, f in enumerate(info[b"files"]):
                parts = f.get(b"path.utf-8", f.get(b"path", []))
                files.append(TFile(i, "/".join(_text(p) for p in parts), int(f.get(b"length", 0))))
        elif b"length" in info:
            single = True
            files.append(TFile(0, name, int(info[b"length"])))
    except Exception as e:  # noqa: BLE001 - a file list that is not one: the same plain words as a broken file
        raise TorrentError("This is not a working .torrent file — its file list is broken. Download the .torrent "
                           "again.") from e
    if not files and b"files" not in info:
        raise TorrentError("This .torrent uses a newer format (v2 only) that the app cannot read yet.")
    trackers: list[str] = []
    if b"announce" in top:
        trackers.append(_text(top[b"announce"]))
    for tier in top.get(b"announce-list", []) or []:
        for t in tier if isinstance(tier, list) else [tier]:
            t = _text(t)
            if t not in trackers:
                trackers.append(t)
    return Meta(name=name or ih, info_hash=ih, files=files, torrent=bytes(data), trackers=trackers, single=single)


@dataclass
class Magnet:
    info_hash: str
    name: str
    trackers: list[str]
    uri: str


_B32 = re.compile(r"^[A-Z2-7]{32}$")


def parse_magnet(text: str) -> Magnet:
    """A magnet link's file ID (btih), name and trackers. TorrentError in plain words when it is broken."""
    t = (text or "").strip()
    if not t.lower().startswith("magnet:?"):
        raise TorrentError("This is not a magnet link — it should start with \"magnet:?\". Copy the whole link "
                           "again.")
    q = urllib.parse.parse_qs(t[len("magnet:?"):], keep_blank_values=True)
    ih = ""
    for xt in q.get("xt", []):
        if xt.lower().startswith("urn:btih:"):
            h = xt[9:].strip()
            if re.fullmatch(r"[0-9a-fA-F]{40}", h):
                ih = h.lower()
            elif _B32.match(h.upper()):
                ih = base64.b32decode(h.upper()).hex()
    if not ih:
        raise TorrentError("This magnet link is broken — its file ID is missing or cut short. Copy the whole link "
                           "again.")
    name = (q.get("dn") or [""])[0].strip()
    return Magnet(ih, name or ih, [x for x in q.get("tr", []) if x], t)


def kind_of(source: str | Path) -> str:
    """"magnet", "torrent" (a .torrent file) or "" (neither)."""
    s = str(source).strip().strip('"')
    if s.lower().startswith("magnet:"):
        return "magnet"
    if s.lower().endswith(".torrent"):
        return "torrent"
    return ""


def make_torrent(path: str | Path, trackers: Iterable[str] = (), piece_length: int = 16 * 1024,
                 name: str | None = None) -> bytes:
    """A .torrent of one file or a folder (the self-check and the tests make their own tiny ones)."""
    p = Path(path)
    if p.is_file():
        entries = [(p, [])]
    else:
        entries = [(f, list(f.relative_to(p).parts)) for f in sorted(p.rglob("*")) if f.is_file()]
    pieces = bytearray()
    buf = b""
    files = []
    for f, parts in entries:
        files.append({"length": f.stat().st_size, "path": parts})
        with open(f, "rb") as fh:
            while True:
                chunk = fh.read(piece_length - len(buf))
                if not chunk:
                    break
                buf += chunk
                if len(buf) == piece_length:
                    pieces += hashlib.sha1(buf).digest()
                    buf = b""
    if buf:
        pieces += hashlib.sha1(buf).digest()
    info: dict = {"name": name or p.name, "piece length": piece_length, "pieces": bytes(pieces)}
    if p.is_file():
        info["length"] = files[0]["length"]
    else:
        info["files"] = files
    top: dict = {"info": info, "created by": "MovieRecaps"}
    tr = list(trackers)
    if tr:
        top["announce"] = tr[0]
        top["announce-list"] = [[t] for t in tr]
    return bencode(top)


# ---------------------------------------------------------------------------------------------------------------------
# seeders from the trackers ("scrape": how many have the whole file, as qBittorrent shows before a download)
# ---------------------------------------------------------------------------------------------------------------------

def _scrape_http(url: str, ih: bytes, timeout: float) -> int | None:
    u = urllib.parse.urlsplit(url)
    path = u.path
    head, _, last = path.rpartition("/")
    if not last.startswith("announce"):
        return None
    path = f"{head}/scrape{last[len('announce'):]}"
    q = (u.query + "&" if u.query else "") + "info_hash=" + urllib.parse.quote_from_bytes(ih)
    full = urllib.parse.urlunsplit((u.scheme, u.netloc, path, q, ""))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(full, headers={"User-Agent": "MovieRecaps"}), timeout=timeout) as r:
        d = bdecode(r.read(1 << 20))
    f = (d.get(b"files") or {}).get(ih)
    return int(f.get(b"complete", 0)) if isinstance(f, dict) else None


def _scrape_udp(url: str, ih: bytes, timeout: float) -> int | None:
    import struct
    u = urllib.parse.urlsplit(url)
    if not u.hostname or not u.port:
        return None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        addr = (socket.gethostbyname(u.hostname), u.port)
        tid = secrets.randbits(32)
        s.sendto(struct.pack(">QII", 0x41727101980, 0, tid), addr)
        r = s.recv(64)
        action, rtid, conn = struct.unpack(">IIQ", r[:16])
        if action != 0 or rtid != tid:
            return None
        tid = secrets.randbits(32)
        s.sendto(struct.pack(">QII", conn, 2, tid) + ih, addr)
        r = s.recv(64)
        action, rtid = struct.unpack(">II", r[:8])
        if action != 2 or rtid != tid or len(r) < 20:
            return None
        return int(struct.unpack(">I", r[8:12])[0])
    finally:
        s.close()


def scrape_seeders(info_hash: str, trackers: Iterable[str], timeout: float = 6.0) -> int | None:
    """The most seeders any tracker reports (asked all at once, ``timeout`` seconds at most); None when none
    answered."""
    ih = bytes.fromhex(info_hash)
    best: list[int] = []
    lock = threading.Lock()

    def one(url: str) -> None:
        try:
            n = _scrape_udp(url, ih, timeout) if url.lower().startswith("udp://") else \
                _scrape_http(url, ih, timeout) if url.lower().startswith(("http://", "https://")) else None
        except Exception:  # noqa: BLE001 - a tracker that does not answer
            n = None
        if n is not None:
            with lock:
                best.append(n)
    threads = [threading.Thread(target=one, args=(t,), daemon=True) for t in list(dict.fromkeys(trackers))[:12]]
    for t in threads:
        t.start()
    end = time.monotonic() + timeout + 1
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    with lock:
        return max(best) if best else None


# ---------------------------------------------------------------------------------------------------------------------
# aria2c: the program and its JSON-RPC
# ---------------------------------------------------------------------------------------------------------------------

class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def find_aria2c() -> Path | None:
    """MOVIERECAPS_ARIA2C, tools\\app\\bin\\aria2c.exe, next to the venv's python, or on PATH."""
    env = os.environ.get(EXE_ENV, "").strip()
    if env and Path(env).is_file():
        return Path(env)
    here = Path(__file__).resolve().parents[2] / "bin" / "aria2c.exe"
    for p in (here, Path(sys.executable).parent / "aria2c.exe"):
        if p.is_file():
            return p
    w = shutil.which("aria2c")
    return Path(w) if w else None


ARIA2_URL = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
ARIA2_ZIP_SHA256 = "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288"


def install_aria2c(dest: Path | None = None, log: Callable[[str], None] = print) -> Path:
    """Download aria2c 1.37.0 (64-bit Windows) from its official GitHub release, check the zip's SHA-256 and put
    aria2c.exe into tools\\app\\bin (setup_app.py runs this when it is missing)."""
    import io
    import zipfile
    dest = dest or Path(__file__).resolve().parents[2] / "bin"
    log("downloading aria2c (the torrent engine, 2.4 MB) ...")
    opener = urllib.request.build_opener()
    with opener.open(urllib.request.Request(ARIA2_URL, headers={"User-Agent": "MovieRecaps"}), timeout=120) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != ARIA2_ZIP_SHA256:
        raise TorrentError("The downloaded aria2c is not the expected file (its checksum differs) -- not installed.")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next(n for n in z.namelist() if n.endswith("/aria2c.exe") or n == "aria2c.exe")
        dest.mkdir(parents=True, exist_ok=True)
        tmp = dest / "aria2c.exe.part"
        tmp.write_bytes(z.read(name))
        os.replace(tmp, dest / "aria2c.exe")
    return dest / "aria2c.exe"


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


class Aria2:
    """One aria2c started by this app, spoken to over http://127.0.0.1:<port>/jsonrpc with a random secret."""

    def __init__(self, folder: Path, exe: Path | None = None, listen_port: int | None = None,
                 extra_args: Iterable[str] = ()):
        self.folder = Path(folder)
        self.exe = exe
        self.listen_port = listen_port
        self.extra_args = list(extra_args)
        self.port = 0
        self.secret = ""
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.running():
                return
            exe = self.exe or find_aria2c()
            if exe is None:
                raise TorrentError("The torrent program (aria2c) is missing. Run this once: "
                                   ".venv\\Scripts\\python tools\\app\\setup_app.py")
            self.folder.mkdir(parents=True, exist_ok=True)
            self.port = _free_port()
            self.secret = secrets.token_hex(16)
            lp = self.listen_port
            args = [str(exe), "--enable-rpc=true", f"--rpc-listen-port={self.port}", "--rpc-listen-all=false",
                    f"--rpc-secret={self.secret}", f"--stop-with-process={os.getpid()}",
                    "--continue=true", "--file-allocation=none", "--auto-save-interval=15",
                    "--bt-save-metadata=false", "--bt-detach-seed-only=true",
                    "--enable-dht=true", "--bt-enable-lpd=true", "--enable-peer-exchange=true",
                    "--max-concurrent-downloads=8", "--summary-interval=0", "--console-log-level=error",
                    "--check-certificate=false", "--disable-ipv6=false",
                    "--allow-overwrite=false", "--auto-file-renaming=false",     # never truncate a file there
                    f"--log={self.folder / 'aria2.log'}", "--log-level=notice",
                    f"--dir={self.folder}"]
            if lp:
                args += [f"--listen-port={lp}", f"--dht-listen-port={lp}"]
            args += self.extra_args
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                if (self.folder / "aria2.log").stat().st_size > 5 * 1024 ** 2:
                    (self.folder / "aria2.log").unlink()
            except OSError:
                pass
            self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, creationflags=flags)
            t0 = time.monotonic()
            while True:
                try:
                    self._post("aria2.getVersion", [], timeout=1.0)
                    return
                except (OSError, RpcError):
                    if self.proc.poll() is not None:
                        raise TorrentError("The torrent program (aria2c) did not start. Run setup_app.py again.")
                    if time.monotonic() - t0 > 15:
                        self.stop()
                        raise TorrentError("The torrent program (aria2c) did not answer. Try again.")
                    time.sleep(0.1)

    def _post(self, method: str, params: list, timeout: float = 10.0) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": "mr", "method": method,
                           "params": [f"token:{self.secret}", *params]}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/jsonrpc", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                d = json.loads(e.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                raise RpcError(-1, str(e)) from e
        if "error" in d:
            raise RpcError(int(d["error"].get("code", -1)), str(d["error"].get("message", "")))
        return d.get("result")

    def call(self, method: str, *params: Any) -> Any:
        if not self.running():
            self.start()
        return self._post(method, list(params))

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.poll() is None:
                try:
                    self._post("aria2.shutdown", [], timeout=2.0)
                    self.proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        self.proc = None


# aria2's error codes (its manual, "EXIT STATUS"; the same numbers are a download's errorCode) -> what happened, what
# to do -- in plain words. {folder} is the save folder, {drive} its drive. Resume = the card's button (the New video
# row's Try again resumes the same download).
RESUME = "then press Resume"
ARIA2_ERRORS: dict[int, tuple[str, str]] = {
    1: ("The torrent program hit a problem it could not name", f"Press Resume to try again; if it stops again, "
                                                               f"Remove the download and add the torrent again."),
    2: ("The other computers stopped answering (a time-out)", f"Check the internet connection, {RESUME}."),
    3: ("The file was not found on the other side (it is not shared any more)",
        "Try again later, or use another torrent."),
    4: ("The file was not found on the other side, again and again (it is not shared any more)",
        "Try again later, or use another torrent."),
    5: ("The download was too slow, so it was stopped", "Press Resume; if it stays slow, use a torrent with more "
                                                        "seeders."),
    6: ("A network problem stopped the download", f"Check the internet connection, {RESUME}."),
    7: ("The torrent program was closed before the download finished", "Press Resume: it goes on from where it was."),
    8: ("The other side cannot go on with a half download", "Remove the download and add the torrent again."),
    9: ("The disk is full{free}", f"Free some space on {{drive}} (Board → Clean up), {RESUME}."),
    10: ("The progress file next to the download (.aria2) belongs to another torrent with the same name",
         "Move the old file and its .aria2 file out of {folder} (or Remove this download), then add the torrent "
         "again."),
    11: ("This file is already being downloaded by another download in the list",
         "Wait for that one to finish, or Remove one of the two."),
    12: ("This torrent is already downloading in the list", "Use the one already in the list, or Remove one of the "
                                                            "two."),
    13: ("The video is already in the download folder, but its progress file (.aria2) is missing, so it could not be "
         "checked", "Press Resume: the app checks the file against the torrent first (the right parts are kept)."),
    14: ("A file with the same name is in the way and could not be renamed",
         f"Move or rename it in {{folder}}, {RESUME}."),
    15: ("The file already in the download folder could not be opened (another program has it open, or it is "
         "read-only)", f"Close the program that uses it (Premiere, a video player), {RESUME}."),
    16: ("The download's file could not be made in {folder} (no permission, or the folder is read-only)",
         f"Check that the folder can be written (and the network, for a shared folder), {RESUME}."),
    17: ("The download could not be written to {folder} (a disk or network error)",
         f"Check the disk (and the network, for a shared folder), {RESUME}."),
    18: ("The download's folder could not be made in {folder}",
         f"Check that the folder can be written (and the network, for a shared folder), {RESUME}."),
    19: ("The trackers' addresses could not be looked up (no internet, or a name-lookup problem)",
         f"Check the internet connection, {RESUME}."),
    20: ("The download list (Metalink) is broken", "Add the torrent again."),
    21: ("An FTP server refused a command", "Try again later."),
    22: ("A web server answered in a way that could not be read", "Try again later."),
    23: ("A web address led to too many other addresses", "Use another link."),
    24: ("A web server asked for a login", "Use another link."),
    25: ("This .torrent file is broken", "Download the .torrent again."),
    26: ("This .torrent file is broken or not complete", "Download the .torrent again."),
    27: ("This magnet link is broken", "Copy the whole link again."),
    28: ("The app gave the torrent program a setting it does not know (an old aria2c?)",
         r"Run setup_app.py again (in PowerShell: .venv\Scripts\python tools\app\setup_app.py), then press Resume."),
    29: ("The other side is busy (overloaded or in maintenance)", "Try again later."),
    30: ("The app and the torrent program could not understand each other", "Restart the app, then press Resume."),
    31: ("The torrent program hit a problem it could not name", "Press Resume to try again."),
    32: ("The downloaded file did not pass its check (its checksum is wrong)",
         "Press Resume to download the bad parts again; if it happens again, use another torrent."),
}
# what aria2c's errorMessage says (the Windows cause it quotes) wins over the bare code -> the code it means
_EXISTS_RX = re.compile(r"control file\s*\(\*\.aria2\) does not exist|exists, but a control file", re.I)
_ARIA2_MESSAGES: list[tuple[re.Pattern, int | str]] = [
    (re.compile(r"not enough (?:disk )?space|no space left|disk (?:is )?full|there is not enough space", re.I), 9),
    (_EXISTS_RX, 13),
    (re.compile(r"being used by another process|locked a portion of the file", re.I), "busy"),
    (re.compile(r"network path was not found|network name is no longer available|unexpected network error|"
                r"network name cannot be found|semaphore timeout", re.I), "network"),
    (re.compile(r"access is denied|permission denied|read-only", re.I), "denied"),
    (re.compile(r"cannot find the path specified|no such file or directory", re.I), "path"),
    (re.compile(r"already registered|same info ?hash", re.I), 12),
    (re.compile(r"piece length", re.I), 10),
    (re.compile(r"bad magnet|magnet uri", re.I), 27),
    (re.compile(r"bencode|bad \.torrent|torrent file", re.I), 26),
    (re.compile(r"name resolution|could not resolve|getaddrinfo", re.I), 19),
    (re.compile(r"timeout|timed out", re.I), 2),
]
_ARIA2_CAUSE = {
    "busy": ("Windows did not let the download open its file in {folder}: another program has it open",
             f"Close the program that uses it (Premiere, a video player, an Explorer preview), {RESUME}."),
    "network": ("The shared folder {folder} could not be reached (the network connection dropped)",
                f"Check that the PC is on and the shared folder opens in Explorer, {RESUME}."),
    "denied": ("Windows did not allow writing the download into {folder} (no permission, or it is read-only)",
               f"Choose another folder in Settings → \"Save torrents to\" (or fix the folder's permissions), {RESUME}."),
    "path": ("The download folder {folder} is not there any more (moved, renamed, or a network folder that dropped)",
             f"Check the folder (Settings → \"Save torrents to\"), {RESUME}."),
}


def plain_aria2(code: int | None, message: str = "", folder: str | Path | None = None) -> str:
    """One aria2c error (its errorCode 1-32 and errorMessage) as what happened + what to do, in plain words -- never
    aria2c's own sentence. The Windows cause it quotes (no space, a file in use, no permission, a network folder
    gone) wins over the bare code."""
    msg = str(message or "")
    key: int | str | None = None
    for rx, k in _ARIA2_MESSAGES:
        if rx.search(msg):
            key = k
            break
    if key is None:
        try:
            key = int(code or 0) or None
        except (TypeError, ValueError):
            key = None
    where = str(folder) if folder else "the download folder"
    drive = drive_name(folder) if folder else "the disk"
    if key == 9:
        free = free_space(folder) if folder else None
        what, todo = ARIA2_ERRORS[9]
        return (what.format(free=f" (only {human_size(free)} free)" if free is not None else "") + ". " +
                todo.format(drive=drive))
    if isinstance(key, str):
        what, todo = _ARIA2_CAUSE[key]
    elif key in ARIA2_ERRORS:
        what, todo = ARIA2_ERRORS[key]
    else:
        what, todo = ("The download stopped" + (f" (aria2 error {code})" if code else ""), "Press Resume to try "
                                                                                          "again.")
    return f"{what.format(folder=where)}. {todo.format(folder=where, drive=drive)}"


_OLD_STOPPED = "The download stopped: "         # day 12's fallback: "The download stopped: <aria2c's message>. ..."


def _exists_error(st: dict) -> bool:
    """aria2c's "File exists, but a control file (*.aria2) does not exist" (error 13)."""
    try:
        code = int(st.get("errorCode", 0) or 0)
    except (TypeError, ValueError):
        code = 0
    return code == 13 or bool(_EXISTS_RX.search(str(st.get("errorMessage", "") or "")))


# ---------------------------------------------------------------------------------------------------------------------
# the session: probe, start, poll, pause / resume / remove, restore after a restart
# ---------------------------------------------------------------------------------------------------------------------

STATUS_KEYS = ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "uploadSpeed", "connections",
               "numSeeders", "errorCode", "errorMessage", "seeder", "infoHash", "followedBy", "files", "dir",
               "verifiedLength", "verifyIntegrityPending"]
CHECK_BPS = 20 * 1024 ** 2              # day 13: a check of a file already there reads at least this fast (a network
#                                         folder): until then no peers and no progress is not "no one sharing"


@dataclass
class Job:
    id: str                         # aria2's gid (16 hex), kept across restarts
    name: str
    info_hash: str
    indexes: list[int]
    save_dir: str
    video: str                      # the chosen video's full path
    size: int                       # bytes chosen
    added: float = field(default_factory=time.time)
    state: str = "downloading"      # downloading | paused | stalled | finished | failed | removed
    finished_at: float | None = None
    error: str = ""
    sharing: bool = False           # Settings -> keep sharing, when it started
    done: int = 0                   # bytes, last seen
    seeders_max: int = 0
    note: str = ""                  # day 13: what happened to files already in the folder, in plain words
    checked: bool = False           # added with check-integrity (its files were there without their .aria2)
    rechecked: bool = False         # aria2c said "File exists, but a control file does not exist": added again
    fetched: bool = False           # something was downloaded (peers / speed seen) -- else a check found it complete
    renamed: str = ""               # the "(old)" renames of the note, kept when the note changes


class TorrentSession:
    """All torrent jobs of this app. ``engine`` is anything with ``call(method, *params)`` (an ``Aria2``; the
    tests pass a fake). Thread-safe: the window polls it from a background thread."""

    def __init__(self, folder: str | Path | None = None, engine: Any = None, settings: Any = None,
                 clock: Callable[[], float] = time.monotonic, stall_minutes: float = STALL_MINUTES,
                 probe_minutes: float = PROBE_MINUTES, sleep: Callable[[float], None] = time.sleep,
                 free: Callable[[Path], int | None] = free_space,
                 scrape: Callable[[str, list], int | None] = scrape_seeders):
        self.folder = Path(folder) if folder else state_dir()
        self.engine = engine if engine is not None else Aria2(self.folder)
        self.settings = settings
        self.clock = clock
        self.sleep = sleep
        self.free = free
        self.scrape = scrape
        self.stall_minutes = stall_minutes
        self.probe_minutes = probe_minutes
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._last_alive: dict[str, float] = {}     # job id -> clock() when it last had someone / made progress
        self._restored = False
        self._load()

    # -- the jobs file -------------------------------------------------------------------------------------------------

    @property
    def jobs_file(self) -> Path:
        return self.folder / "jobs.json"

    def _torrent_file(self, info_hash: str) -> Path:
        return self.folder / f"{info_hash}.torrent"

    def _load(self) -> None:
        try:
            d = json.loads(self.jobs_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for j in d.get("jobs", []) if isinstance(d, dict) else []:
            try:
                job = Job(**{k: v for k, v in j.items() if k in Job.__dataclass_fields__})
            except TypeError:
                continue
            if job.error.startswith(_OLD_STOPPED):       # day 12's words kept aria2c's own sentence: said plainly
                job.error = plain_aria2(None, job.error[len(_OLD_STOPPED):], job.save_dir)
            self._jobs[job.id] = job

    def _save(self) -> None:
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            data = {"jobs": [asdict(j) for j in self._jobs.values() if j.state != "removed"]}
            tmp = self.jobs_file.with_name(f".jobs.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.jobs_file)

    def jobs(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.state != "removed"]

    def job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    # -- before a download ---------------------------------------------------------------------------------------------

    def probe(self, source: str | Path, cancel: threading.Event | None = None,
              on_wait: Callable[[float, int], None] | None = None) -> Meta:
        """The file list of a magnet link (fetched from the peers) or a .torrent file. Blocks: run it in the
        background. A magnet that nobody shares for ``probe_minutes`` -> TorrentError in plain words."""
        k = kind_of(source)
        if k == "torrent":
            meta = read_torrent(Path(str(source).strip().strip('"')))
            meta.seeders = self._scrape(meta.info_hash, meta.trackers)
            return meta
        meta = self._probe_magnet(source, cancel, on_wait)
        sc = self._scrape(meta.info_hash, meta.trackers)
        if sc is not None or meta.seeders:
            meta.seeders = max(sc or 0, meta.seeders or 0)
        else:
            meta.seeders = None
        return meta

    def _scrape(self, info_hash: str, trackers: list[str]) -> int | None:
        if not trackers:
            return None
        try:
            return self.scrape(info_hash, trackers)
        except Exception:  # noqa: BLE001
            return None

    def _probe_magnet(self, source: str | Path, cancel: threading.Event | None,
                      on_wait: Callable[[float, int], None] | None) -> Meta:
        k = kind_of(source)
        if k != "magnet":
            raise TorrentError("This is not a magnet link or a .torrent file.")
        mg = parse_magnet(str(source))
        tmp = self.folder / "probe"         # short: aria2c cannot write paths over 260 characters
        tmp.mkdir(parents=True, exist_ok=True)
        saved = tmp / f"{mg.info_hash}.torrent"
        try:
            saved.unlink()
        except OSError:
            pass
        try:
            gid = self.engine.call("aria2.addUri", [mg.uri], {"dir": str(tmp), "bt-metadata-only": "true",
                                                              "bt-save-metadata": "true", "follow-torrent": "false"})
        except RpcError as e:
            raise TorrentError(_plain_rpc(e, "magnet")) from e
        t0 = self.clock()
        seen = 0
        complete_since: float | None = None
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise TorrentError("Cancelled.")
                try:
                    st = self.engine.call("aria2.tellStatus", gid, STATUS_KEYS)
                except RpcError:
                    st = {"status": "removed"}
                seen = max(seen, int(st.get("numSeeders", 0) or 0), self._peer_seeders(gid))
                if saved.is_file() and st.get("status") in ("complete", "removed", "error", "active"):
                    try:
                        meta = read_torrent(saved.read_bytes())
                    except TorrentError:
                        meta = None
                    if meta is not None:
                        try:
                            saved.unlink()
                        except OSError:
                            pass
                        meta.trackers = meta.trackers or mg.trackers
                        meta.seeders = seen
                        if mg.name and mg.name != mg.info_hash and not meta.name:
                            meta.name = mg.name
                        return meta
                if st.get("status") == "error":
                    raise TorrentError(_plain_status(st, self.folder))
                if st.get("status") == "complete" and complete_since is None:
                    complete_since = self.clock()
                if complete_since is not None and self.clock() - complete_since > 5 and not saved.is_file():
                    raise TorrentError(f"The file list arrived but could not be saved in {tmp}. Check that the "
                                       "folder can be written, then try again.")
                waited = self.clock() - t0
                if on_wait is not None:
                    on_wait(waited, int(st.get("connections", 0) or 0))
                if waited > self.probe_minutes * 60:
                    raise TorrentError(no_one_sharing(self.probe_minutes))
                self.sleep(0.5)
        finally:
            for m in ("aria2.forceRemove", "aria2.removeDownloadResult"):
                try:
                    self.engine.call(m, gid)
                except Exception:  # noqa: BLE001
                    pass

    def _peer_seeders(self, gid: str) -> int:
        """Seeders among the peers aria2c is talking to (they say so in their first message), 0 when unknown."""
        try:
            peers = self.engine.call("aria2.getPeers", gid) or []
        except Exception:  # noqa: BLE001
            return 0
        return sum(1 for p in peers if str(p.get("seeder", "")).lower() == "true")

    def peek_seeders(self, meta: Meta, seconds: float = 20.0, cancel: threading.Event | None = None) -> int:
        """Seeders of a .torrent file's download, seen within ``seconds`` (asks the trackers and the DHT the way a
        magnet does; nothing is downloaded but the file list)."""
        uri = f"magnet:?xt=urn:btih:{meta.info_hash}" + "".join(
            "&tr=" + urllib.parse.quote(t, safe="") for t in meta.trackers)
        tmp = self.folder / "probe"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            gid = self.engine.call("aria2.addUri", [uri], {"dir": str(tmp), "bt-metadata-only": "true",
                                                           "bt-save-metadata": "false",
                                                           "follow-torrent": "false"})
        except RpcError:
            return 0
        t0, seen = self.clock(), 0
        try:
            while self.clock() - t0 < seconds:
                if cancel is not None and cancel.is_set():
                    break
                try:
                    st = self.engine.call("aria2.tellStatus", gid, STATUS_KEYS)
                except RpcError:
                    break
                seen = max(seen, int(st.get("numSeeders", 0) or 0), self._peer_seeders(gid))
                if st.get("status") in ("complete", "error", "removed") or seen >= 50:
                    break
                self.sleep(0.5)
        finally:
            for m in ("aria2.forceRemove", "aria2.removeDownloadResult"):
                try:
                    self.engine.call(m, gid)
                except Exception:  # noqa: BLE001
                    pass
        meta.seeders = max(meta.seeders or 0, seen)
        return seen

    def check_space(self, meta: Meta, indexes: Iterable[int], save_dir: str | Path) -> int:
        """The bytes free on the save folder's disk; TorrentError in plain words when the chosen files + a margin
        do not fit (or nothing is chosen, or the folder cannot be reached). What is already in the folder (day 13:
        checked in place, not downloaded again) is not counted; nothing left to download needs no space."""
        ix = [i for i in indexes if 0 <= i < len(meta.files)]
        if not ix:
            raise TorrentError("Tick at least one file to download.")
        need = max(0, meta.size_of(ix) - self._already_there(meta, ix, save_dir))
        free = self.free(Path(save_dir))
        if free is None:
            raise TorrentError(f"The save folder {save_dir} cannot be reached. Check the network or choose another "
                               "folder in Settings.")
        if need and need + margin(need) > free:
            raise TorrentError(f"Not enough free space on {drive_name(save_dir)}: the chosen files need "
                               f"{human_size(need)} (+{human_size(margin(need))} to spare), but only "
                               f"{human_size(free)} is free. Free some space or choose fewer files.")
        return free

    @staticmethod
    def _already_there(meta: Meta, indexes: Iterable[int], save_dir: str | Path) -> int:
        """Bytes of the chosen files already in the save folder (a download that finished before, or a part of it):
        they are checked in place, not downloaded again (day 13) -- the free-space check does not count them. A file
        bigger than the torrent's counts 0 (it is renamed "(old)" and the video fetched fresh)."""
        have = 0
        for i in indexes:
            try:
                p = aria2_path(meta, save_dir, i)
                if p.is_file():
                    s = p.stat().st_size
                    have += s if s <= meta.files[i].size else 0
            except (OSError, IndexError):
                continue
        return have

    # -- the download --------------------------------------------------------------------------------------------------

    def _options(self, job: Job, paused: bool = False, check: bool = False) -> dict:
        o = {"dir": job.save_dir, "select-file": ",".join(str(i + 1) for i in sorted(job.indexes)),
             "gid": job.id, "continue": "true", "allow-overwrite": "false", "auto-file-renaming": "false"}
        if job.sharing:
            o["seed-ratio"] = "1.0"
        else:
            o["seed-time"] = "0"
        if check:                         # day 13: files there without their .aria2 -- checked, never truncated
            o["check-integrity"] = "true"
            o["bt-hash-check-seed"] = "true" if job.sharing else "false"     # complete: finished, no sharing
        if paused:
            o["pause"] = "true"
        return o

    def _prepare_existing(self, job: Job, meta: Meta, real: dict[int, Path] | None = None) -> bool:
        """Files of this torrent already in the save folder WITHOUT aria2c's progress file (.aria2): a download that
        finished before (aria2c deletes its .aria2 then -- the next Short of the same film), or one whose .aria2 was
        lost. aria2c alone would stop with "File exists, but a control file (*.aria2) does not exist". Instead
        (day 13): a chosen file that cannot be this torrent's -- bigger than its file, or none of its pieces right
        (file_matches) -- is renamed "<name> (old)<ext>" and downloaded fresh (aria2c would truncate it); everything
        else is checked against the torrent (check-integrity): the right pieces are kept, only the missing ones are
        downloaded, a complete file is finished at once. A finished file is never truncated or overwritten.
        ``real``: aria2c's own paths (index -> path) when known. -> True when aria2c must check; ``job.note`` says
        what happens, in plain words. Reads the disk (maybe a network folder): call it from a worker thread."""
        base = Path(job.save_dir)
        real = real or {}
        try:
            if control_file(meta, base).is_file():
                return False                 # aria2c goes on from its own progress file
        except OSError:
            return False
        renamed: list[tuple[Path, Path, str]] = []
        kept: list[Path] = []
        for i in range(len(meta.files)):
            p = real.get(i) or aria2_path(meta, base, i)
            try:
                if not p.is_file():
                    continue
                size = p.stat().st_size
            except OSError:
                continue
            if i not in job.indexes:
                kept.append(p)               # not chosen: left as it is (aria2c checks the pieces it shares)
                continue
            want = meta.files[i].size
            why = ""
            if size > want:
                why = f"it is bigger ({human_size(size)}) than the torrent's file ({human_size(want)})"
            elif size > 0:
                ok = file_matches(meta, i, p, size)
                if ok is False:
                    why = "none of the parts checked match the torrent"
                elif ok is None and size != want:
                    why = "it is too short to be checked"
            if not why:
                kept.append(p)
                continue
            new = old_name(p)
            try:
                os.replace(p, new)
            except OSError as e:
                raise TorrentError(f'"{p.name}" is already in the download folder but is not this torrent\'s file '
                                   f"({why}), and it could not be renamed ({e.strerror or e}). Move it out of "
                                   f"{p.parent}, then press Resume.") from e
            renamed.append((p, new, why))
        notes = [f'An older file "{p.name}" was in the download folder but is not this torrent\'s file ({why}): it '
                 f'was renamed to "{new.name}" and the video is downloaded fresh.' for p, new, why in renamed]
        if renamed:
            job.renamed = " ".join(x for x in (job.renamed, *notes) if x)
        if kept:
            notes.append("The video was already in the download folder (without its progress file), so it is "
                         "checked against the torrent first: the right parts are kept and only what is missing is "
                         "downloaded.")
        if notes:
            job.note = " ".join(notes)
        job.checked = job.checked or bool(kept)
        return bool(kept)

    def start(self, meta: Meta, indexes: Iterable[int], save_dir: str | Path | None = None) -> str:
        """Start downloading the chosen files (0-based ``indexes``). Checks the free space first. -> the job id."""
        save_dir = Path(save_dir) if save_dir else default_dir(settings=self.settings)
        ix = sorted({int(i) for i in indexes if 0 <= int(i) < len(meta.files)})
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise TorrentError(f"The save folder {save_dir} cannot be made: {e.strerror or e}. Choose another "
                               "folder in Settings.") from e
        self.check_space(meta, ix, save_dir)
        if not meta.torrent:
            raise TorrentError("The file list is missing — add the magnet link again.")
        with self._lock:
            for j in self._jobs.values():
                if j.info_hash == meta.info_hash and j.state in ("downloading", "paused", "stalled"):
                    raise TorrentError("This torrent is already downloading.")
        self.folder.mkdir(parents=True, exist_ok=True)
        self._torrent_file(meta.info_hash).write_bytes(meta.torrent)
        vi = meta.chosen_video(ix)
        for i in ix:
            meta.disk_path(save_dir, i)          # TorrentError: a name that leads outside the save folder
        job = Job(id=secrets.token_hex(8), name=meta.name, info_hash=meta.info_hash, indexes=ix,
                  save_dir=str(save_dir), video=str(meta.disk_path(save_dir, vi)) if vi is not None else "",
                  size=meta.size_of(ix), sharing=keep_sharing(self.settings), seeders_max=int(meta.seeders or 0))
        self._add(job, meta.torrent)
        with self._lock:
            self._jobs[job.id] = job
            self._last_alive[job.id] = self.clock()
        self._save()
        return job.id

    def _add(self, job: Job, torrent: bytes, paused: bool = False, check: bool = False,
             real: dict[int, Path] | None = None) -> None:
        """Give aria2c the job (its files already in the folder prepared first: _prepare_existing)."""
        try:
            meta = read_torrent(torrent)
        except TorrentError:
            meta = None
        if meta is not None:
            check = self._prepare_existing(job, meta, real) or check
        b64 = base64.b64encode(torrent).decode("ascii")
        try:
            self.engine.call("aria2.addTorrent", b64, [], self._options(job, paused, check))
        except RpcError as e:
            if "already" in e.message.lower() or e.code == 12:
                return
            raise TorrentError(_plain_rpc(e, "add")) from e

    def _check_again(self, j: Job) -> bool:
        """aria2c stopped with "File exists, but a control file (*.aria2) does not exist" (error 13: a file this app
        did not see -- a name it escapes another way): the file is prepared as on a start (_prepare_existing, with
        aria2c's own paths) and the job is given back with check-integrity. Once per job. -> it was added again."""
        if j.rechecked:
            return False
        j.rechecked = True
        real = self._real_paths(j)
        try:
            data = self._torrent_file(j.info_hash).read_bytes()
        except OSError:
            return False
        try:
            self.engine.call("aria2.removeDownloadResult", j.id)
        except Exception:  # noqa: BLE001 - already gone
            pass
        try:
            self._add(j, data, check=True, real=real)
        except TorrentError as e:
            j.state, j.error = "failed", str(e)
            return True
        self._last_alive[j.id] = self.clock()
        return True

    def restore(self) -> list[str]:
        """After an app restart: give aria2c back every job that was not finished (paused ones stay paused). ->
        their ids. aria2c continues from the progress files next to the download."""
        out = []
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.state in ("downloading", "paused", "stalled")]
        for j in jobs:
            try:
                st = self.engine.call("aria2.tellStatus", j.id, ["gid", "status"])
                if st.get("status") in ("active", "waiting", "paused"):
                    out.append(j.id)
                    continue
            except RpcError:
                pass
            except TorrentError as e:
                j.error = str(e)
                continue
            tf = self._torrent_file(j.info_hash)
            try:
                data = tf.read_bytes()
            except OSError:
                j.state, j.error = "failed", "The saved .torrent is missing — add the magnet link again."
                continue
            try:
                self._add(j, data, paused=j.state == "paused")
            except TorrentError as e:
                j.state, j.error = "failed", str(e)
                continue
            self._last_alive[j.id] = self.clock()
            out.append(j.id)
        self._restored = True
        self._save()
        return out

    def pause(self, job_id: str) -> None:
        j = self._need(job_id)
        try:
            self.engine.call("aria2.forcePause", j.id)
        except RpcError:
            pass
        j.state = "paused"
        self._save()

    def resume(self, job_id: str) -> None:
        """Resume a paused (or stalled, or failed) job; a failed one is checked for free space again first."""
        j = self._need(job_id)
        if j.state == "finished":
            return
        try:
            st = self.engine.call("aria2.tellStatus", j.id, ["gid", "status"])
            status = st.get("status")
        except RpcError:
            status = ""
        if status in ("paused", "waiting"):
            self.engine.call("aria2.unpause", j.id)
        elif status != "active":
            for m in ("aria2.removeDownloadResult",):
                try:
                    self.engine.call(m, j.id)
                except RpcError:
                    pass
            free = self.free(Path(j.save_dir))
            left = max(0, j.size - j.done)
            if free is not None and left + margin(left) > free:
                raise TorrentError(f"Not enough free space on {drive_name(j.save_dir)}: {human_size(left)} still "
                                   f"to download, only {human_size(free)} free.")
            try:
                data = self._torrent_file(j.info_hash).read_bytes()
            except OSError as e:
                raise TorrentError("The saved .torrent is missing — add the magnet link again.") from e
            self._add(j, data)
        j.state, j.error = "downloading", ""
        self._last_alive[j.id] = self.clock()
        self._save()

    def remove(self, job_id: str, delete_files: bool | None = None) -> None:
        """Stop and forget a job. An unfinished job's part-downloaded files are deleted (``delete_files``: None =
        only when it did not finish); a finished download is never deleted."""
        j = self._need(job_id)
        real = self._real_paths(j) if j.state != "finished" else {}     # asked before aria2c forgets the job
        for m in ("aria2.forceRemove", "aria2.removeDownloadResult"):
            try:
                self.engine.call(m, j.id)
            except Exception:  # noqa: BLE001
                pass
        unfinished = j.state != "finished"
        if delete_files is None:
            delete_files = unfinished
        if delete_files and unfinished:
            self._delete_parts(j, real)
        j.state = "removed"
        with self._lock:
            self._jobs.pop(j.id, None)
            others = any(x.info_hash == j.info_hash for x in self._jobs.values())
        if not others:
            try:
                self._torrent_file(j.info_hash).unlink()
            except OSError:
                pass
        self._save()

    def _real_paths(self, j: Job) -> dict[int, Path]:
        """aria2c's own paths of this job's chosen files (0-based index -> path; only those inside the save
        folder). Empty when aria2c cannot say (the job is gone)."""
        try:
            files = self.engine.call("aria2.getFiles", j.id) or []
        except Exception:  # noqa: BLE001 - not known to aria2c (any more)
            return {}
        out = {}
        for f in files:
            try:
                i = int(f.get("index")) - 1
                path = str(f.get("path") or "")
            except (TypeError, ValueError, AttributeError):
                continue
            if i in j.indexes and path and not path.startswith("[METADATA]") and inside(j.save_dir, path):
                out[i] = Path(os.path.abspath(os.path.join(j.save_dir, path)))
        return out

    def _guess(self, j: Job, i: int) -> str:
        try:
            meta = read_torrent(self._torrent_file(j.info_hash).read_bytes())
            return str(meta.disk_path(j.save_dir, i))
        except (OSError, TorrentError, IndexError):
            return ""

    def _delete_parts(self, j: Job, real: dict[int, Path] | None = None) -> None:
        """The part-downloaded files of THIS job's chosen files (aria2c's own paths when known, else the torrent's
        names) and its .aria2 progress file -- never a file another known job of the same torrent and folder uses,
        never anything outside the save folder. Unselected files are not touched (aria2c leaves at most small
        piece-overlap stubs of them)."""
        try:
            meta = read_torrent(self._torrent_file(j.info_hash).read_bytes())
        except (OSError, TorrentError):
            return
        base = Path(j.save_dir)
        here = os.path.normcase(os.path.abspath(j.save_dir))
        with self._lock:
            others = [x for x in self._jobs.values() if x.id != j.id and x.state != "removed"
                      and x.info_hash == j.info_hash and os.path.normcase(os.path.abspath(x.save_dir)) == here]
        theirs = {i for x in others for i in x.indexes}
        real = real or {}
        paths: list[Path] = []
        for i in j.indexes:
            if i in theirs or not 0 <= i < len(meta.files):
                continue
            for p in (real.get(i), None):
                try:
                    p = p or meta.disk_path(base, i)
                except TorrentError:
                    continue
                if inside(base, p) and p not in paths:
                    paths.append(p)
        ctl = base / (meta.files[0].path if meta.single else meta.name)
        if not others and inside(base, ctl):
            paths.append(Path(str(ctl) + ".aria2"))
        # give aria2c a moment to close the files it had open
        for _ in range(20):
            busy = False
            for p in paths:
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
                except PermissionError:
                    busy = True
                except OSError:
                    pass
            if not busy:
                break
            self.sleep(0.1)
        if not meta.single:
            top = base / meta.name
            for d in sorted((p for p in top.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)) \
                    if top.is_dir() else []:
                try:
                    d.rmdir()
                except OSError:
                    pass
            try:
                top.rmdir()
            except OSError:
                pass

    def _need(self, job_id: str) -> Job:
        j = self._jobs.get(job_id)
        if j is None:
            raise TorrentError("This download is no longer in the list.")
        return j

    # -- live numbers --------------------------------------------------------------------------------------------------

    def poll(self, job_id: str) -> dict:
        """The job's live numbers (one quick call to aria2c). Also notices finished / stalled / failed jobs."""
        j = self._need(job_id)
        if j.state in ("finished", "removed"):
            return self._status(j, {})
        try:
            st = self.engine.call("aria2.tellStatus", j.id, STATUS_KEYS)
        except RpcError:
            st = {"status": "missing"}
        except TorrentError as e:
            return self._status(j, {}, error=str(e))
        return self._update(j, st)

    def poll_all(self) -> list[dict]:
        return [self.poll(j.id) for j in self.jobs()]

    def _update(self, j: Job, st: dict) -> dict:
        now = self.clock()
        status = st.get("status", "")
        total = int(st.get("totalLength", 0) or 0)
        done = int(st.get("completedLength", 0) or 0)
        conns = int(st.get("connections", 0) or 0)
        seeds = int(st.get("numSeeders", 0) or 0)
        changed = False
        verifying = "verifiedLength" in st or str(st.get("verifyIntegrityPending", "")).lower() == "true"
        if done > j.done or conns > 0 or verifying:          # day 13: checking the file already there is progress
            self._last_alive[j.id] = now
        if conns > 0 or int(st.get("downloadSpeed", 0) or 0) > 0:
            j.fetched = True
        if done != j.done:
            j.done = done
        j.seeders_max = max(j.seeders_max, seeds)
        complete = (status == "complete") or (total > 0 and done >= total and st.get("seeder") == "true")
        if complete and j.state != "finished":
            j.state, j.finished_at, j.error = "finished", time.time(), ""
            if j.checked:                        # day 13: what the check of the files already there found
                said = ("The video was already complete in the download folder: it was checked against the torrent "
                        "and every part is right, so nothing was downloaded again." if not j.fetched else
                        "The video was already partly in the download folder: the right parts were kept and only "
                        "the missing parts were downloaded.")
                j.note = " ".join(x for x in (j.renamed, said) if x)
            real = self._real_paths(j)
            vi = next((i for i in real if self._guess(j, i) == j.video), None)
            if vi is None and len(real) == 1:
                vi = next(iter(real))
            if vi is not None:
                j.video = str(real[vi])          # where aria2c really wrote it (it escapes ':' and the like)
            j.done = max(j.done, j.size)
            changed = True
            sharing = j.sharing and keep_sharing(self.settings)
            if not sharing:
                for m in ("aria2.forceRemove", "aria2.removeDownloadResult"):
                    try:
                        self.engine.call(m, j.id)
                    except Exception:  # noqa: BLE001
                        pass
        elif status == "error" and j.state != "failed":
            if not (_exists_error(st) and self._check_again(j)):   # day 13: "File exists, but a control file ..."
                j.state, j.error = "failed", _plain_status(st, Path(j.save_dir))
            changed = True
        elif status == "paused" and j.state not in ("paused", "stalled"):
            j.state = "paused"
            changed = True
        elif status == "active" and j.state in ("paused",):
            j.state = "downloading"
            changed = True
        elif status == "missing" and j.state in ("downloading", "stalled"):
            # aria2c lost it (it was restarted): add it again
            try:
                self._add(j, self._torrent_file(j.info_hash).read_bytes())
            except (OSError, TorrentError) as e:
                j.state, j.error = "failed", str(e) if isinstance(e, TorrentError) else \
                    "The saved .torrent is missing — add the magnet link again."
                changed = True
        if j.state == "downloading" and status == "active":
            alive = self._last_alive.setdefault(j.id, now)
            limit = self.stall_minutes * 60
            if j.checked and not j.fetched:      # an older aria2c says nothing while it checks: time for the check
                limit += j.size / CHECK_BPS
            if now - alive > limit:
                j.state, j.error = "stalled", no_one_sharing(self.stall_minutes)
                try:
                    self.engine.call("aria2.forcePause", j.id)
                except Exception:  # noqa: BLE001
                    pass
                changed = True
        if changed:
            self._save()
        return self._status(j, st)

    def _status(self, j: Job, st: dict, error: str = "") -> dict:
        size = j.size or int(st.get("totalLength", 0) or 0)
        done = j.size if j.state == "finished" else int(st.get("completedLength", j.done) or 0)
        speed = int(st.get("downloadSpeed", 0) or 0) if j.state == "downloading" else 0
        seeds = int(st.get("numSeeders", 0) or 0)
        conns = int(st.get("connections", 0) or 0)
        peers = max(0, conns - seeds)
        share = max(0.0, min(1.0, (done / size) if size else (1.0 if j.state == "finished" else 0.0)))
        eta = (size - done) / speed if speed > 0 and size > done else (0.0 if j.state == "finished" else None)
        return {"id": j.id, "name": j.name, "state": j.state, "share": max(0.0, min(1.0, share)),
                "percent": int(share * 100) if j.state != "finished" else 100,
                "done": done, "size": size, "speed": speed, "speed_text": human_speed(speed),
                "seeders": seeds, "peers": peers, "health": health_bars(seeds if j.state != "finished" else
                                                                       j.seeders_max),
                "eta": eta, "eta_text": human_eta(eta), "video": j.video, "save_dir": j.save_dir,
                "error": error or j.error, "sharing": bool(j.sharing and st.get("seeder") == "true"),
                "will_share": bool(j.sharing), "note": j.note,
                "added": j.added, "finished_at": j.finished_at}

    def close(self) -> None:
        stop = getattr(self.engine, "stop", None)
        if callable(stop):
            stop()


def no_one_sharing(minutes: float) -> str:
    m = int(round(minutes))
    return (f"No one is sharing this file right now — tried for {m} minute{'s' if m != 1 else ''}. Try again "
            "later or use another link.")


def _plain_rpc(e: RpcError, what: str) -> str:
    msg = (e.message or "").lower()
    if what == "magnet" or "magnet" in msg:
        return "This magnet link is broken — copy the whole link again."
    if "torrent" in msg or "bencode" in msg:
        return "This .torrent file is broken — download it again."
    if "not unique" in msg or "already" in msg:
        return "This torrent is already in the torrent program's list. Remove it from the list, then add it again."
    return (f"The torrent program refused this download ({e.message or 'no reason given'}). Press Resume to try "
            "again; if it happens again, restart the app.")


def _plain_status(st: dict, folder: Path) -> str:
    """A download that stopped with an error, in plain words with what to do (plain_aria2)."""
    return plain_aria2(st.get("errorCode"), str(st.get("errorMessage", "") or ""), folder)
