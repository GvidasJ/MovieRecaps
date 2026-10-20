"""A real check that the torrent engine works on this computer -- with nothing from the internet: a tiny file of
random bytes is made into a .torrent here, one aria2c shares it, and the app's own session fetches its file list over
a magnet link and downloads it from that aria2c, all on 127.0.0.1 through a tiny tracker run here.

    .venv\\Scripts\\python -m mr_app.core.torrent_selftest      (from tools\\app)

The tests run the same (tests/test_torrent.py, skipped when aria2c.exe is missing)."""
from __future__ import annotations

import hashlib
import os
import socket
import struct
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import torrent as T

# day 13: an aria2c of a check or a test listens on 127.0.0.1 only -- no IPv6, no DHT, no local discovery -- so
# Windows never asks its firewall question ("allow public and private networks to access aria2c.exe?"); it popped
# up during the tests and blocked every click. The app's own aria2c is not changed (it needs the internet).
LOCAL_ONLY = ["--interface=127.0.0.1", "--disable-ipv6=true", "--enable-dht=false", "--bt-enable-lpd=false"]


class LocalTracker:
    """A minimal BitTorrent HTTP tracker on 127.0.0.1: every peer that announces is told about the others."""

    def __init__(self):
        peers: dict[bytes, dict[tuple[str, int], float]] = {}
        lock = threading.Lock()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                u = urllib.parse.urlsplit(self.path)
                q = urllib.parse.parse_qs(u.query, keep_blank_values=True, encoding="latin-1")
                ih = (q.get("info_hash") or [""])[0].encode("latin-1")
                port = int((q.get("port") or ["0"])[0] or 0)
                ip = self.client_address[0]
                left = int((q.get("left") or ["1"])[0] or 1)
                with lock:
                    bucket = peers.setdefault(ih, {})
                    if u.path.endswith("/scrape"):
                        body = T.bencode({"files": {ih: {"complete": sum(1 for v in bucket.values() if v),
                                                         "incomplete": sum(1 for v in bucket.values() if not v),
                                                         "downloaded": 0}}})
                        others = None
                    else:
                        if (q.get("event") or [""])[0] == "stopped":
                            bucket.pop((ip, port), None)
                        elif port:
                            bucket[(ip, port)] = left == 0
                        others = [(a, p) for (a, p) in bucket if (a, p) != (ip, port)]
                if others is not None:
                    compact = b"".join(socket.inet_aton(a) + struct.pack(">H", p) for a, p in others)
                    body = T.bencode({"interval": 5, "min interval": 2, "complete": len(bucket), "incomplete": 0,
                                      "peers": compact})
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/announce"
        self._t = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._t.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def run(folder: str | Path | None = None, size: int = 300 * 1024, timeout: float = 90.0,
        log=print) -> Path:
    """Seed a tiny file from one local aria2c and download it with a TorrentSession. -> the downloaded file.
    Raises AssertionError / TorrentError when something does not work."""
    base = Path(folder) if folder else Path(tempfile.mkdtemp(prefix="mr_torrent_check_"))
    seed_dir, seed_state, get_dir, get_state = (base / n for n in ("seed", "seed_state", "get", "get_state"))
    for d in (seed_dir, seed_state, get_dir, get_state):
        d.mkdir(parents=True, exist_ok=True)
    data = os.urandom(size)
    src = seed_dir / "tiny_check_video.mp4"
    src.write_bytes(data)
    tracker = LocalTracker()
    seeder = T.Aria2(seed_state, listen_port=T._free_port(), extra_args=list(LOCAL_ONLY))
    getter = T.Aria2(get_state, listen_port=T._free_port(), extra_args=LOCAL_ONLY + ["--bt-tracker-interval=2"])
    try:
        tor = T.make_torrent(src, [tracker.url])
        meta0 = T.read_torrent(tor)
        log(f"made {src.name} ({T.human_size(size)}) -> torrent {meta0.info_hash}")
        import base64
        seeder.start()
        seeder.call("aria2.addTorrent", base64.b64encode(tor).decode(), [],
                    {"dir": str(seed_dir), "check-integrity": "true", "seed-ratio": "0.0",
                     "bt-tracker-interval": "2"})
        sess = T.TorrentSession(get_state, engine=getter, probe_minutes=timeout / 60)
        magnet = f"magnet:?xt=urn:btih:{meta0.info_hash}&dn=tiny&tr={urllib.parse.quote(tracker.url, safe='')}"
        t0 = time.monotonic()
        meta = sess.probe(magnet)
        log(f"file list over the magnet in {time.monotonic() - t0:.1f} s: "
            f"{[(f.path, f.size) for f in meta.files]}, seeders seen {meta.seeders}")
        assert meta.info_hash == meta0.info_hash
        assert meta.biggest_video() == 0
        assert (meta.seeders or 0) >= 1, f"the tracker should report the seeder, not {meta.seeders}"
        jid = sess.start(meta, [0], get_dir)
        st = sess.poll(jid)
        while st["state"] not in ("finished", "failed", "stalled"):
            if time.monotonic() - t0 > timeout:
                raise AssertionError(f"not finished in {timeout:.0f} s: {st}")
            time.sleep(0.3)
            st = sess.poll(jid)
        assert st["state"] == "finished", st
        out = Path(st["video"])
        got = out.read_bytes()
        assert hashlib.sha1(got).digest() == hashlib.sha1(data).digest(), "the downloaded bytes differ"
        log(f"downloaded {out} in {time.monotonic() - t0:.1f} s, the bytes match; sharing stopped: "
            f"{not st['sharing']}")
        return out
    finally:
        getter.stop()
        seeder.stop()
        tracker.close()


if __name__ == "__main__":
    run()
