"""20 Nov (your job 4: "Now a paused step starts again from zero. Make an edit ... go on from where it stopped, also
after the app or the PC was closed in the middle"): the long steps keep their finished work on disk as they go.

The two long steps of an edit (S5.2 visual search, S5.3 refine: 80 % of an edit, 30+ minutes on a long RAW) do
their work as many small tasks (``visual_match.parallel_map``: one competitor frame, one track ...). Their stage
result is only written when the whole step is done, so a pause (the app kills the tool), the app closed or the PC
turned off threw that step away, and ``--resume`` began it again from zero.

Now, inside a step that is opened as a ``scope`` (the stage's own cache key: the same inputs, settings and code),
every finished task's result is appended to a file in the cache (``cache/partial/<stage>-<key>/``) a moment after
it arrives. When the step runs again -- Resume, Try again after the PC was off, any run of the same video with the
same code -- each ``parallel_map`` call first reads the results it has, and only the missing tasks run. A task's
result never depends on which worker ran it, in which order or with which others (``parallel_map``: every task
seeded the same, one thread each: bit for bit the same on every path), so the step's result is the same as a run
without a pause, byte for byte. A call is known by its function, seed, the exact bytes of its tasks and how many
such calls came before it in the step; anything else (other code: another stage key) is another folder. A record
cut off by the kill is dropped when it is read. When the step's result is in the cache, its folder goes.

``MATCH_CUTS_CHECKPOINTS=0`` switches it off."""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
import shutil
import struct
import threading
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

log = logging.getLogger("match_cuts")

ENV = "MATCH_CUTS_CHECKPOINTS"
FLUSH_S = 2.0                           # results waiting in memory are written at least this often
FLUSH_N = 64                            # ... or when this many wait
_HEAD = struct.Struct("<QI")            # a record: its length, its crc32, then the pickled [(index, result), ...]
_STACK: list["Scope"] = []
_LOCK = threading.Lock()


def enabled() -> bool:
    return os.environ.get(ENV, "1").strip() not in ("0", "off", "no", "false")


class Call:
    """One ``parallel_map`` call's results on disk: ``load`` what an earlier run kept, ``add`` new ones."""

    def __init__(self, path: Path):
        self.path = path
        self._buf: list[tuple[int, Any]] = []
        self._last = time.monotonic()
        self.loaded = 0
        self.added = 0

    def load(self) -> dict[int, Any]:
        out: dict[int, Any] = {}
        good = 0
        try:
            data = self.path.read_bytes()
        except OSError:
            return out
        pos = 0
        while pos + _HEAD.size <= len(data):
            n, crc = _HEAD.unpack_from(data, pos)
            body = data[pos + _HEAD.size:pos + _HEAD.size + n]
            if len(body) != n or zlib.crc32(body) != crc:
                break                                   # (cut off by the kill: the rest is computed again)
            try:
                for i, r in pickle.loads(body):
                    out[int(i)] = r
            except Exception:  # noqa: BLE001 - an unreadable record: from here on computed again
                break
            pos += _HEAD.size + n
            good = pos
        if good < len(data):                            # the cut-off tail goes, new records follow the good ones
            try:
                with open(self.path, "r+b") as f:
                    f.truncate(good)
            except OSError:
                pass
        self.loaded = len(out)
        return out

    def add(self, i: int, r: Any) -> None:
        self._buf.append((int(i), r))
        self.added += 1
        if len(self._buf) >= FLUSH_N or time.monotonic() - self._last >= FLUSH_S:
            self.flush()

    def flush(self) -> None:
        self._last = time.monotonic()
        if not self._buf:
            return
        try:
            body = pickle.dumps(self._buf, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as e:  # noqa: BLE001 - a result that cannot be kept: this call is not kept
            log.debug("checkpoint %s: not kept (%s: %s)", self.path.name, type(e).__name__, e)
            self._buf = []
            return
        self._buf = []
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "ab") as f:
                f.write(_HEAD.pack(len(body), zlib.crc32(body)) + body)
                f.flush()
        except OSError as e:
            log.debug("checkpoint %s: not written (%s)", self.path.name, e)


class Scope:
    """The checkpoints of one step (a stage with its cache key)."""

    def __init__(self, folder: Path, name: str):
        self.dir = Path(folder)
        self.name = name
        self._seen: dict[str, int] = {}
        self.loaded = 0
        self.total = 0

    def call(self, fn_name: str, seed: int, items: Sequence[Any]) -> Call | None:
        try:
            blob = pickle.dumps(list(items), protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:  # noqa: BLE001 - tasks that cannot be written cannot be known again: not kept
            return None
        h = hashlib.blake2b(blob, digest_size=16)
        h.update(f"|{fn_name}|{int(seed)}|{len(items)}".encode())
        sig = h.hexdigest()
        with _LOCK:
            k = self._seen.get(sig, 0)
            self._seen[sig] = k + 1
        short = "".join(c for c in fn_name.rsplit(".", 1)[-1] if c.isalnum() or c == "_")[:24]
        return Call(self.dir / f"{short}-{sig[:20]}-{k}.bin")


def current() -> Scope | None:
    return _STACK[-1] if _STACK else None


def call_for(fn: Any, seed: int, items: Sequence[Any]) -> Call | None:
    """The kept results of this ``parallel_map`` call in the step now running (None: no step keeps them)."""
    s = current()
    if s is None or not items:
        return None
    name = f"{getattr(fn, '__module__', '')}.{getattr(fn, '__qualname__', getattr(fn, '__name__', 'fn'))}"
    return s.call(name, seed, items)


@contextmanager
def scope(cache_root: str | os.PathLike | None, stage: str, key: str, drop: bool = True) -> Iterator[Scope | None]:
    """The step ``stage`` (cache key ``key``) keeps its finished tasks under ``cache_root``/partial while it runs;
    on success its folder goes (the stage's own cache holds the result) -- with ``drop=False`` it stays until the
    caller has written that result (``drop_folder``)."""
    if not enabled() or cache_root is None or not key:
        yield None
        return
    s = Scope(folder(cache_root, stage, key), stage)
    _STACK.append(s)
    ok = False
    try:
        yield s
        ok = True
    finally:
        _STACK.remove(s)
        if s.loaded:
            log.info("%s: %d of %d tasks were done before the pause -- taken from the checkpoint, not computed "
                     "again", stage, s.loaded, s.total)
        if ok and drop:
            shutil.rmtree(s.dir, ignore_errors=True)


def folder(cache_root: str | os.PathLike, stage: str, key: str) -> Path:
    return Path(cache_root) / "partial" / f"{stage}-{key}"[:120]


def drop_folder(cache_root: str | os.PathLike | None, stage: str, key: str) -> None:
    """The step's result is in its cache now: its kept tasks go."""
    if cache_root is not None and key:
        shutil.rmtree(folder(cache_root, stage, key), ignore_errors=True)
