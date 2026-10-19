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
        """Where aria2c writes file ``index``: <save>\\<file> for one file, <save>\\<name>\\<path> for a folder."""
        f = self.files[index]
        base = Path(save_dir)
        parts = [p for p in f.path.split("/") if p]
        return base.joinpath(*parts) if self.single else base.joinpath(_safe(self.name), *parts)

    def chosen_video(self, indexes: Iterable[int]) -> int | None:
        ix = sorted(set(indexes))
        pick = [self.files[i] for i in ix if 0 <= i < len(self.files)]
        vids = [f for f in pick if f.video] or pick
        return max(vids, key=lambda f: (f.size, -f.index)).index if vids else None


def _safe(name: str) -> str:
    return name


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
                    "--bt-save-metadata=false", "--bt-remove-unselected-file=true", "--bt-detach-seed-only=true",
                    "--enable-dht=true", "--bt-enable-lpd=true", "--enable-peer-exchange=true",
                    "--max-concurrent-downloads=8", "--summary-interval=0", "--console-log-level=error",
                    "--check-certificate=false", "--disable-ipv6=false",
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


# aria2's error codes -> plain words
_ARIA2_ERRORS = {
    9: "disk", 17: "io", 16: "create", 18: "folder", 26: "corrupt", 27: "magnet", 12: "duplicate",
    13: "exists", 2: "timeout", 6: "network",
}


# ---------------------------------------------------------------------------------------------------------------------
# the session: probe, start, poll, pause / resume / remove, restore after a restart
# ---------------------------------------------------------------------------------------------------------------------

STATUS_KEYS = ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "uploadSpeed", "connections",
               "numSeeders", "errorCode", "errorMessage", "seeder", "infoHash", "followedBy", "files", "dir"]


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
        do not fit (or nothing is chosen, or the folder cannot be reached)."""
        ix = [i for i in indexes if 0 <= i < len(meta.files)]
        if not ix:
            raise TorrentError("Tick at least one file to download.")
        need = meta.size_of(ix)
        free = self.free(Path(save_dir))
        if free is None:
            raise TorrentError(f"The save folder {save_dir} cannot be reached. Check the network or choose another "
                               "folder in Settings.")
        if need + margin(need) > free:
            raise TorrentError(f"Not enough free space on {drive_name(save_dir)}: the chosen files need "
                               f"{human_size(need)} (+{human_size(margin(need))} to spare), but only "
                               f"{human_size(free)} is free. Free some space or choose fewer files.")
        return free

    # -- the download --------------------------------------------------------------------------------------------------

    def _options(self, job: Job, paused: bool = False) -> dict:
        o = {"dir": job.save_dir, "select-file": ",".join(str(i + 1) for i in sorted(job.indexes)),
             "gid": job.id, "bt-remove-unselected-file": "true", "continue": "true"}
        if job.sharing:
            o["seed-ratio"] = "1.0"
        else:
            o["seed-time"] = "0"
        if paused:
            o["pause"] = "true"
        return o

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
        job = Job(id=secrets.token_hex(8), name=meta.name, info_hash=meta.info_hash, indexes=ix,
                  save_dir=str(save_dir), video=str(meta.disk_path(save_dir, vi)) if vi is not None else "",
                  size=meta.size_of(ix), sharing=keep_sharing(self.settings), seeders_max=int(meta.seeders or 0))
        self._add(job, meta.torrent)
        with self._lock:
            self._jobs[job.id] = job
            self._last_alive[job.id] = self.clock()
        self._save()
        return job.id

    def _add(self, job: Job, torrent: bytes, paused: bool = False) -> None:
        b64 = base64.b64encode(torrent).decode("ascii")
        try:
            self.engine.call("aria2.addTorrent", b64, [], self._options(job, paused))
        except RpcError as e:
            if "already" in e.message.lower() or e.code == 12:
                return
            raise TorrentError(_plain_rpc(e, "add")) from e

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
        for m in ("aria2.forceRemove", "aria2.removeDownloadResult"):
            try:
                self.engine.call(m, j.id)
            except Exception:  # noqa: BLE001
                pass
        unfinished = j.state != "finished"
        if delete_files is None:
            delete_files = unfinished
        if delete_files and unfinished:
            self._delete_parts(j)
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

    def _delete_parts(self, j: Job) -> None:
        try:
            meta = read_torrent(self._torrent_file(j.info_hash).read_bytes())
        except (OSError, TorrentError):
            return
        base = Path(j.save_dir)
        ctl = base / (meta.files[0].path if meta.single else meta.name)
        paths = [meta.disk_path(base, i) for i in range(len(meta.files))]
        # give aria2c a moment to close the files it had open
        for _ in range(20):
            busy = False
            for p in paths + [Path(str(ctl) + ".aria2")]:
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
        if done > j.done or conns > 0:
            self._last_alive[j.id] = now
        if done != j.done:
            j.done = done
        j.seeders_max = max(j.seeders_max, seeds)
        complete = (status == "complete") or (total > 0 and done >= total and st.get("seeder") == "true")
        if complete and j.state != "finished":
            j.state, j.finished_at, j.error = "finished", time.time(), ""
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
            if now - alive > self.stall_minutes * 60:
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
                "will_share": bool(j.sharing),
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
    return f"The torrent could not be added ({e.message})."


def _plain_status(st: dict, folder: Path) -> str:
    try:
        code = int(st.get("errorCode", 0) or 0)
    except ValueError:
        code = 0
    msg = str(st.get("errorMessage", "") or "")
    kind = _ARIA2_ERRORS.get(code, "")
    if kind == "disk" or "space" in msg.lower():
        free = free_space(folder)
        tail = f" (only {human_size(free)} free)" if free is not None else ""
        return f"The disk is full{tail}. Free some space on {drive_name(folder)}, then press Resume."
    if kind in ("io", "create", "folder"):
        return f"The download could not be written to {folder}. Check the folder (and the network for a shared " \
               f"folder), then press Resume."
    if kind == "corrupt":
        return "This .torrent file is broken — download it again."
    if kind == "magnet":
        return "This magnet link is broken — copy the whole link again."
    if kind in ("timeout", "network"):
        return "The network stopped answering. Check the internet connection, then press Resume."
    return f"The download stopped: {msg or 'unknown problem'}. Press Resume to try again."
