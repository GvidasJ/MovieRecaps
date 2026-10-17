"""DOWNLOAD (mr_app.core.download): the format choice from fake format lists, yt-dlp's real error messages in plain
words, the progress over picture + sound, a stand-in yt-dlp for the download / Stop / clean-up, a dropped RAW file,
the daily yt-dlp update, a project's download step. Nothing here goes to the internet.
Run: python -m pytest tools/app/tests/test_download.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mr_app.core import download as D, projects as P   # noqa: E402


def V(fid, vcodec, w, h, fps=30, tbr=1000.0, proto="https", ext="mp4", dr="SDR", **kw):
    return dict(format_id=str(fid), vcodec=vcodec, acodec="none", width=w, height=h, fps=fps, tbr=tbr,
                protocol=proto, ext=ext, dynamic_range=dr, filesize=int(tbr * 125 * 60), **kw)


def A(fid, acodec, abr=128.0, proto="https", ext="m4a", lang_pref=-1, note="", **kw):
    return dict(format_id=str(fid), vcodec="none", acodec=acodec, abr=abr, tbr=abr, protocol=proto, ext=ext,
                language_preference=lang_pref, format_note=note, filesize=int(abr * 125 * 60), **kw)


STORYBOARD = dict(format_id="sb0", vcodec="none", acodec="none", ext="mhtml", protocol="mhtml",
                  format_note="storyboard")


# ---------------------------------------------------------------------------------------------------------------------
# the format choice
# ---------------------------------------------------------------------------------------------------------------------

def test_a_short_with_h264_and_vp9_at_the_same_size_takes_h264_and_aac():
    fmts = [STORYBOARD, A("139", "mp4a.40.5", 49), A("140", "mp4a.40.2", 130), A("251", "opus", 127, ext="webm"),
            A("140-drc", "mp4a.40.2", 130, note="medium, DRC"),
            V("298", "avc1.640020", 720, 1280, 60, 2140), V("299", "avc1.64002A", 1080, 1920, 60, 3987),
            V("312", "avc1.64002A", 1080, 1920, 60, 4866, proto="m3u8_native"),     # HLS: its peak rate
            V("303", "vp9", 1080, 1920, 60, 4124, ext="webm"), V("399", "av01.0.09M.08", 1080, 1920, 60, 2692)]
    spec, why = D.choose_format(fmts)
    assert spec == "299+140"
    assert why.startswith("H.264 1080x1920 60 fps") and "AAC 130 kb/s" in why and "not bigger" in why
    assert D.pick_formats(fmts).merge_ext == "mp4"


def test_a_4k_video_whose_h264_stops_at_1080p_takes_the_4k_vp9_or_av1():
    fmts = [A("140", "mp4a.40.2", 129), A("251", "opus", 135, ext="webm"),
            V("137", "avc1.640028", 1920, 1080, 30, 2505), V("248", "vp9", 1920, 1080, 30, 1525, ext="webm"),
            V("271", "vp9", 2560, 1440, 30, 4787, ext="webm"), V("313", "vp9", 3840, 2160, 30, 12514, ext="webm"),
            V("401", "av01.0.12M.08", 3840, 2160, 30, 5254)]
    c = D.pick_formats(fmts)
    assert c.spec == "313+140" and c.video_kind == "vp9" and c.merge_ext == "mkv"
    assert "bigger than any H.264" in c.why and "1920x1080" in c.why
    no_vp9 = [f for f in fmts if f["format_id"] not in ("271", "313")]
    c = D.pick_formats(no_vp9)
    assert c.spec == "401+140" and c.video_kind == "av1" and c.merge_ext == "mp4"   # AV1 goes into mp4


def test_the_same_size_at_a_higher_frame_rate_counts_as_bigger():
    fmts = [A("140", "mp4a.40.2"), V("137", "avc1.640028", 1920, 1080, 30, 4000),
            V("303", "vp9", 1920, 1080, 60, 4100, ext="webm")]
    assert D.choose_format(fmts)[0] == "303+140"
    fmts[2]["fps"] = 30.5                                                # 30 vs 30.5: not clearly higher
    assert D.choose_format(fmts)[0] == "137+140"


def test_no_aac_takes_the_opus_sound():
    fmts = [A("249", "opus", 50, ext="webm"), A("251", "opus", 130, ext="webm"),
            V("137", "avc1.640028", 1920, 1080, 30, 2500)]
    c = D.pick_formats(fmts)
    assert c.spec == "137+251" and c.audio_kind == "opus" and "no AAC sound" in c.why
    assert c.merge_ext == "mp4"                                         # H.264 + Opus: mp4 keeps the H.264 times


def test_sdr_before_hdr_and_hdr_only_when_there_is_nothing_else():
    fmts = [A("140", "mp4a.40.2"), V("137", "avc1.640028", 1920, 1080),
            V("337", "vp09.02.51.10", 3840, 2160, 60, 30000, ext="webm", dr="HDR10"),
            V("315", "vp9", 3840, 2160, 60, 20000, ext="webm")]
    assert D.choose_format(fmts)[0] == "315+140"
    hdr_only = [A("140", "mp4a.40.2"), V("337", "vp09.02.51.10", 3840, 2160, 60, 30000, ext="webm", dr="HDR10")]
    spec, why = D.choose_format(hdr_only)
    assert spec == "337+140" and "HDR10" in why


def test_the_original_language_sound_before_a_dubbed_one_and_no_drc():
    fmts = [V("137", "avc1.640028", 1920, 1080),
            A("140-0", "mp4a.40.2", 129.5, language="en-US", lang_pref=-1, note="English (US), medium"),
            A("140-1", "mp4a.40.2", 129.4, language="de-DE", lang_pref=10,
              note="German (DE) original (default), medium"),
            A("140-1-drc", "mp4a.40.2", 129.4, language="de-DE", lang_pref=10, note="German (DE) original, DRC"),
            A("139-1", "mp4a.40.5", 48, language="de-DE", lang_pref=10)]
    c = D.pick_formats(fmts)
    assert c.spec == "137+140-1" and "de-DE, original" in c.why


def test_https_before_hls_of_the_same_picture():
    fmts = [A("140", "mp4a.40.2"), V("299", "avc1.64002A", 1920, 1080, 60, 3987),
            V("312", "avc1.64002A", 1920, 1080, 60, 4866, proto="m3u8_native")]
    assert D.choose_format(fmts)[0] == "299+140"


def test_ai_upscaled_and_damaged_formats_only_when_nothing_else():
    fmts = [A("140", "mp4a.40.2"), V("136", "avc1.4d401f", 1280, 720),
            V("137-sr", "avc1.640028", 1920, 1080, format_note="1080p, AI-upscaled")]
    assert D.choose_format(fmts)[0] == "136+140"
    assert D.choose_format([A("140", "mp4a.40.2"), fmts[2]])[0] == "137-sr+140"


def test_a_format_with_picture_and_sound_in_one_when_nothing_else():
    fmts = [STORYBOARD, dict(format_id="18", vcodec="avc1.42001E", acodec="mp4a.40.2", width=640, height=360,
                             fps=30, tbr=500, protocol="https", ext="mp4")]
    c = D.pick_formats(fmts)
    assert c.spec == "18" and c.audio is None and "with its sound in one" in c.why
    tiktok = [dict(format_id="h264_540p", vcodec="h264", acodec="aac", width=576, height=1024, tbr=1000,
                   protocol="https", ext="mp4"),
              dict(format_id="bytevc1_1080p", vcodec="h265", acodec="aac", width=1080, height=1920, tbr=900,
                   protocol="https", ext="mp4")]
    assert D.choose_format(tiktok)[0] == "bytevc1_1080p"                 # HEVC 1080p is bigger than H.264 540p
    unknown = [dict(format_id="0", url="https://x/video.mp4", ext="mp4", width=1280, height=720)]
    assert D.choose_format(unknown)[0] == "0"                           # a site that names no codec


def test_no_picture_and_drm_are_said_in_plain_words():
    with pytest.raises(D.DownloadError, match="only sound"):
        D.choose_format([A("140", "mp4a.40.2")])
    with pytest.raises(D.DownloadError) as e:
        D.choose_format([dict(V("137", "avc1.640028", 1920, 1080), has_drm=True)])
    assert e.value.plain == D.DRM
    with pytest.raises(D.DownloadError):
        D.choose_format([STORYBOARD])


def test_the_probe_json_gives_the_link_info():
    info = {"id": "4v3FsM0BzWQ", "title": "The Funniest Tough Guy of All Time \U0001F602", "duration": 37,
            "media_type": "short", "webpage_url": "https://www.youtube.com/shorts/4v3FsM0BzWQ",
            "uploader": "Lou Laughs", "live_status": "not_live", "extractor_key": "Youtube",
            "formats": [A("140", "mp4a.40.2"), V("299", "avc1.64002A", 1080, 1920, 60, 3987)]}
    li = D.link_info("https://www.youtube.com/shorts/4v3FsM0BzWQ", info)
    assert (li.title, li.id, li.duration, li.width, li.height, li.is_short) == \
        ("The Funniest Tough Guy of All Time \U0001F602", "4v3FsM0BzWQ", 37.0, 1080, 1920, True)
    assert li.format_spec == "299+140" and li.uploader == "Lou Laughs"
    for live, words in (("is_live", "still running"), ("is_upcoming", "has not finished"),
                        ("post_live", "still preparing")):
        with pytest.raises(D.DownloadError, match=words):
            D.link_info("u", dict(info, live_status=live))
    with pytest.raises(D.DownloadError) as e:
        D.link_info("u", {"_type": "playlist", "entries": [{"id": "x"}], "title": "Shorts"})
    assert e.value.plain == D.PLAYLIST
    li = D.link_info("u", dict(info, media_type="video", webpage_url="https://www.youtube.com/watch?v=4v3FsM0BzWQ"),
                     ["WARNING: [youtube] 4v3FsM0BzWQ: n challenge solving failed: Some formats may be missing."])
    assert not li.is_short and "update yt-dlp" in li.why


# ---------------------------------------------------------------------------------------------------------------------
# errors in plain words (yt-dlp's real messages: tonight's runs and its source, yt_dlp/extractor/youtube/_video.py,
# extractor/common.py, utils/_utils.py)
# ---------------------------------------------------------------------------------------------------------------------

COOKIES = (" Use --cookies-from-browser or --cookies for the authentication. See  https://github.com/yt-dlp/yt-dlp/"
           "wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for how to manually pass cookies. Also see  https://github.com/"
           "yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies  for tips on effectively exporting YouTube cookies")


@pytest.mark.parametrize("text, words", [
    ("ERROR: [youtube] yZIXLfi8CZQ: Private video", D.PRIVATE),
    ("ERROR: [youtube] yZIXLfi8CZQ: Private video. Sign in if you've been granted access to this video." + COOKIES,
     D.PRIVATE),
    ("ERROR: [youtube] sJL6WA-aGkQ: The uploader has not made this video available in your country", D.REGION),
    ("ERROR: [youtube] sJL6WA-aGkQ: Video unavailable. The uploader has not made this video available in your "
     "country", D.REGION),
    ("ERROR: [Niconico] sm9: This video is not available from your location due to geo restriction", D.REGION),
    ("ERROR: Unsupported URL: https://example.com/", D.WRONG_LINK),
    ("WARNING: [generic] Falling back on generic information extractor\nERROR: Unsupported URL: https://www.google."
     "com/httpservice/retry/enablejs?sei=6QPTasvjHtOqxc8P0bjG2As", D.WRONG_LINK),
    ("ERROR: [generic] 'notaurl' is not a valid URL", D.WRONG_LINK),
    ("ERROR: [youtube:truncated_id] N_708QY7Ob: Incomplete YouTube ID N_708QY7Ob. URL https://www.youtube.com/"
     "watch?v=N_708QY7Ob looks truncated.", D.WRONG_LINK),
    ("ERROR: [generic] Unable to download webpage: HTTP Error 404: Not Found (caused by <HTTPError 404: Not "
     "Found>)", D.WRONG_LINK),
    ("ERROR: [youtube:tab] The channel/playlist does not exist and the URL redirected to youtube.com home page",
     D.WRONG_LINK),
    ("ERROR: [youtube] thisisnotva: This video is unavailable", D.REMOVED),
    ("ERROR: [youtube] DJztXj2GPfk: Video unavailable", D.REMOVED),
    ("ERROR: [youtube] 6SJNVb0GnPI: This video has been removed for violating YouTube's policy on hate speech. "
     "Learn more about combating hate speech in your country.", D.REMOVED),
    ("ERROR: [youtube] abcdefghijk: Video unavailable. This video is no longer available because the YouTube "
     "account associated with this video has been terminated.", D.REMOVED),
    ("ERROR: [youtube] abcdefghijk: Video unavailable. This video has been removed by the uploader", D.REMOVED),
    ("ERROR: [youtube] abcdefghijk: Sign in to confirm your age. This video may be inappropriate for some users."
     + COOKIES, D.AGE),
    ("ERROR: [youtube] abcdefghijk: Sign in to confirm you\u2019re not a bot." + COOKIES, D.BOT),
    ("ERROR: [youtube] abcdefghijk: Sign in to confirm you're not a bot. This helps protect our community.", D.BOT),
    ("ERROR: [youtube] abcdefghijk: Join this channel to get access to members-only content like this video, and "
     "other exclusive perks.", D.MEMBERS),
    ("ERROR: [youtube] abcdefghijk: This video is available to this channel's members on level: Fan (or any higher "
     "level). Join this channel to get access to members-only content and other exclusive perks.", D.MEMBERS),
    ("ERROR: [youtube] abcdefghijk: This live event will begin in 3 hours.", D.LIVE),
    ("ERROR: [youtube] abcdefghijk: This live event will begin in a few moments.", D.LIVE),
    ("ERROR: [youtube] abcdefghijk: Premieres in 10 hours", D.LIVE),
    ("ERROR: [youtube] abcdefghijk: This content isn't available, try again later. The current session has been "
     "rate-limited by YouTube for up to an hour.", D.RATE),
    ("ERROR: [youtube] abcdefghijk: This video is DRM protected", D.DRM),
    ("WARNING: [youtube] No supported JavaScript runtime could be found. Only deno is enabled by default; to use "
     "another runtime add  --js-runtimes RUNTIME[:PATH]  to your command/config.\nERROR: [youtube] abcdefghijk: "
     "Requested format is not available. Use --list-formats for a list of available formats", D.NO_JS),
    ("WARNING: [youtube] abcdefghijk: n challenge solving failed: Some formats may be missing. Ensure you have a "
     "supported JavaScript runtime and challenge solver script distribution installed.\nERROR: [youtube] "
     "abcdefghijk: Requested format is not available. Use --list-formats for a list of available formats",
     D.OLD_YTDLP),
    ("WARNING: [youtube] abcdefghijk: Signature solving failed: Some formats may be missing.\nERROR: [youtube] "
     "abcdefghijk: Only images are available for download. use --list-formats to see them", D.OLD_YTDLP),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", D.OLD_YTDLP),
    ("ERROR: [youtube] abcdefghijk: Unable to download API page: <urlopen error [Errno 11001] getaddrinfo failed> "
     "(caused by TransportError('<urlopen error [Errno 11001] getaddrinfo failed>'))", D.NETWORK),
    ("ERROR: [youtube] abcdefghijk: Unable to download API page: The read operation timed out (caused by "
     "TransportError('The read operation timed out'))", D.NETWORK),
    ("[download] Got error: ('Connection aborted.', ConnectionResetError(10054, 'An existing connection was "
     "forcibly closed by the remote host', None, 10054, None))\nERROR: fragment 3 not found, unable to continue",
     D.NETWORK),
    ("ERROR: unable to write data: [Errno 28] No space left on device", D.DISK_FULL),
    ("ERROR: Unable to rename file: [WinError 112] There is not enough space on the disk: 'a.part' -> 'a'",
     D.DISK_FULL),
    ("ERROR: You have requested merging of multiple formats but ffmpeg is not installed. Aborting due to "
     "--abort-on-error", D.NO_FFMPEG),
])
def test_ytdlp_errors_in_plain_words(text, words):
    assert D.plain_error(text) == words


def test_an_unknown_error_is_its_last_error_line_short():
    assert D.plain_error("ERROR: Postprocessing: Conversion failed!") == \
        "the download did not work: Postprocessing: Conversion failed!"
    assert D.plain_error("ERROR: [youtube] abcdefghijk: Something odd happened. Use --cookies to fix it") == \
        "the download did not work: Something odd happened"
    assert D.plain_error("") == "the download did not work (see the log)"


@pytest.mark.parametrize("url, words", [
    ("https://www.youtube.com/watch?v=thisisnotvalid", "11 letters and digits, this one is 'thisisnotvalid'"),
    ("https://www.youtube.com/watch?v=N_708QY7Ob", "this one is 'N_708QY7Ob'"),
    ("https://youtu.be/", "no video id"),
    ("notaurl", "not a web address"),
    ("", "no link"),
    ("https://www.youtube.com/@LouLaughs/shorts", "playlist or a channel"),
    ("https://www.youtube.com/playlist?list=PLabc", "playlist or a channel"),
])
def test_links_that_are_plainly_wrong_are_said_before_ytdlp_is_asked(url, words):
    assert words in D.link_problem(D.clean_link(url))


@pytest.mark.parametrize("url", ["https://www.youtube.com/shorts/4v3FsM0BzWQ", "https://youtu.be/w2fdx42TqPg?si=x",
                                 "https://www.youtube.com/watch?v=4v3FsM0BzWQ&list=PLx&index=2",
                                 "https://www.tiktok.com/@x/video/7234567890123", "https://example.com/"])
def test_good_looking_links_pass(url):
    assert D.link_problem(url) is None


def test_a_link_without_https_gets_it():
    assert D.clean_link("  youtube.com/watch?v=hkFYOzYx_6M ") == "https://youtube.com/watch?v=hkFYOzYx_6M"
    assert D.clean_link('"https://youtu.be/w2fdx42TqPg"') == "https://youtu.be/w2fdx42TqPg"


# ---------------------------------------------------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------------------------------------------------

def test_progress_lines_and_the_share_over_picture_and_sound():
    assert D.parse_progress("MRPROG 137|downloading|1024|2048|NA|512.5") == \
        ("137", "downloading", 1024.0, 2048.0, None, 512.5)
    assert D.parse_progress("[download] 10%") is None
    now = [0.0]
    t = D.Tracker({"137": 100e6, "140": 10e6}, clock_fn=lambda: now[0])   # the probe's sizes
    t.update("137", "downloading", 4.1e6, 100e6, None, 4.1e6)
    assert t.fraction() == pytest.approx(4.1 / 110) and t.speed == 4.1e6  # the first second: yt-dlp's speed
    for k in range(1, 12):                                                # 4 MB/s on average, in bursts
        now[0] = float(k)
        t.update("137", "downloading", 4.1e6 + 4e6 * k, 100e6, None, 40e6 if k % 2 else 1e6)
    assert t.speed == pytest.approx(4e6)                                  # not 40 or 1: the last 6 s
    assert t.fraction() == pytest.approx(48.1 / 110)
    t.update("137", "finished", 100e6, 100e6, None, None)
    t.update("140", "downloading", 2e6, None, 8e6, 4.1e6)                 # only an estimate: it replaces the probe's
    assert t.fraction() == pytest.approx(102 / 108)
    t.speed = 3e6
    assert t.text() == "94 % -- 102.0 MB of 108.0 MB, 3.0 MB/s, about 0:02 left"
    t.update("140", "finished", 8e6, None, 8e6, None)
    assert t.fraction() == 0.99                                           # 1.0 only when the file is there
    assert D.size_text(1.5e9) == "1.50 GB" and D.size_text(12.34e6) == "12.3 MB" and D.clock(3725) == "1:02:05"


def test_the_download_command_line():
    c = D.Choice("299+140", "why", merge_ext="mp4")
    cmd = D.download_command(Path("x.info.json"), Path("C:/a folder"), c)
    s = " ".join(cmd)
    assert cmd[1:3] == ["-m", "yt_dlp"] and "--ignore-config" in cmd and "--no-playlist" in cmd
    for opt in ("--load-info-json x.info.json", "-f 299+140", "--merge-output-format mp4", "--windows-filenames",
                "--no-mtime", "--newline", "--progress-delta 0.5", "--no-quiet"):
        assert opt in s
    assert cmd[cmd.index("-o") + 1] == "%(title).80B [%(id)s].%(ext)s"
    assert cmd[cmd.index("--print") + 1] == "after_move:MRFILE %(filepath)s"
    assert cmd[cmd.index("--progress-template") + 1].startswith("download:MRPROG %(info.format_id)s|")
    js = D.js_runtime()
    if js is not None:
        assert cmd[cmd.index("--js-runtimes") + 1] == f"{js[0]}:{js[1]}"


# ---------------------------------------------------------------------------------------------------------------------
# a stand-in yt-dlp: the download, Stop, the clean-up
# ---------------------------------------------------------------------------------------------------------------------

FAKE_YTDLP = r'''
import json, os, sys, time
args = sys.argv[1:]
opt = lambda name: args[args.index(name) + 1]
info = json.load(open(opt("--load-info-json"), encoding="utf-8"))
dest, mode = opt("-P"), info.get("_mode", "ok")
open(os.path.join(dest, "fake.pid"), "w").write(str(os.getpid()))
base = os.path.join(dest, "%s [%s]" % (info["title"], info["id"]))
if mode == "private":
    print("[youtube] Extracting URL", flush=True)
    print("ERROR: [youtube] %s: Private video. Sign in if you've been granted access to this video" % info["id"],
          flush=True)
    sys.exit(1)
parts = opt("-f").split("+")
for k, fid in enumerate(parts):
    total = 400000 * (k + 1)
    part = "%s.f%s.mp4.part" % (base, fid)
    with open(part, "wb") as f:
        for i in range(1, 5):
            f.write(b"x" * (total // 4)); f.flush()
            print("MRPROG %s|downloading|%d|%d|NA|250000.0" % (fid, total * i // 4, total), flush=True)
            if mode == "hang":
                time.sleep(60)
    os.replace(part, part[:-5])
    print("MRPROG %s|finished|%d|%d|NA|NA" % (fid, total, total), flush=True)
print('[Merger] Merging formats into "%s.mp4"' % base, flush=True)
open(base + ".mp4", "wb").write(b"merged")
for fid in parts:
    os.remove("%s.f%s.mp4" % (base, fid))
print("MRFILE %s.mp4" % base, flush=True)
'''


@pytest.fixture
def fake_ytdlp(tmp_path, monkeypatch):
    script = tmp_path / "fake_ytdlp.py"
    script.write_text(FAKE_YTDLP, encoding="utf-8")
    monkeypatch.setattr(D, "base_command", lambda: [sys.executable, str(script)])

    def probed(mode="ok"):
        info = {"id": "4v3FsM0BzWQ", "title": "Tough Guy \U0001F602", "duration": 37.0, "_mode": mode,
                "webpage_url": "https://www.youtube.com/shorts/4v3FsM0BzWQ", "media_type": "short",
                "formats": [A("140", "mp4a.40.2"), V("299", "avc1.64002A", 1080, 1920, 60)]}
        info["formats"][0]["filesize"], info["formats"][1]["filesize"] = 800000, 400000
        return D.link_info("https://www.youtube.com/shorts/4v3FsM0BzWQ", info)
    return probed


def test_a_download_reports_progress_and_returns_the_file(tmp_path, fake_ytdlp):
    dest = tmp_path / "2026-10-17_tough guy"
    seen = []
    res = D.download("https://www.youtube.com/shorts/4v3FsM0BzWQ", dest, "competitor",
                     progress=lambda f, t: seen.append((f, t)), log_path=dest / "logs" / "download_competitor.log",
                     probed=fake_ytdlp())
    assert res.path == dest / "Tough Guy \U0001F602 [4v3FsM0BzWQ].mp4" and res.path.read_bytes() == b"merged"
    assert (res.title, res.id, res.format_id, res.width, res.height) == ("Tough Guy \U0001F602", "4v3FsM0BzWQ",
                                                                         "299+140", 1080, 1920)
    fracs = [f for f, _ in seen]
    assert fracs[0] == 0.0 and fracs[-1] == 1.0 and fracs == sorted(fracs)
    assert any(t == "joining the picture and the sound" for _, t in seen)
    mid = [t for f, t in seen if 0 < f < 0.99]
    assert mid and all(" % -- " in t and " of " in t for t in mid)
    assert sorted(p.name for p in dest.iterdir()) == ["Tough Guy \U0001F602 [4v3FsM0BzWQ].mp4", "fake.pid", "logs"]
    log = (dest / "logs" / "download_competitor.log").read_text(encoding="utf-8")
    assert "chosen: -f 299+140" in log and "[Merger]" in log and "done:" in log
    assert not any(l.startswith("MRPROG ") for l in log.splitlines())    # progress lines are not logged


def test_a_failed_download_says_why_and_leaves_nothing(tmp_path, fake_ytdlp):
    dest = tmp_path / "proj"
    with pytest.raises(D.DownloadError) as e:
        D.download("https://www.youtube.com/shorts/4v3FsM0BzWQ", dest, "raw", log_path=dest / "dl.log",
                   probed=fake_ytdlp("private"))
    assert e.value.plain == D.PRIVATE and "Private video" in e.value.detail
    assert sorted(p.name for p in dest.iterdir()) == ["dl.log", "fake.pid"]
    assert "ERROR in plain words: " + D.PRIVATE in (dest / "dl.log").read_text(encoding="utf-8")


def test_stop_kills_the_download_and_removes_its_partial_files(tmp_path, fake_ytdlp):
    dest = tmp_path / "proj"
    dest.mkdir()
    other = dest / "Other video [abcdefghijk].f137.mp4.part"              # another download in the same folder
    other.write_bytes(b"keep me")
    cancel = threading.Event()
    box = {}

    def run():
        try:
            D.download("u", dest, "raw", progress=lambda f, t: cancel.set() if f > 0 else None, cancel=cancel,
                       probed=fake_ytdlp("hang"))
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
    t0 = time.time()
    th = threading.Thread(target=run)
    th.start()
    th.join(30)
    assert not th.is_alive() and isinstance(box.get("e"), D.Cancelled)
    assert time.time() - t0 < 20                                          # not the stand-in's 60 s
    assert sorted(p.name for p in dest.iterdir()) == ["Other video [abcdefghijk].f137.mp4.part", "fake.pid"]
    pid = int((dest / "fake.pid").read_text())
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
    assert str(pid) not in out                                            # killed


# ---------------------------------------------------------------------------------------------------------------------
# a RAW file dropped instead of a link
# ---------------------------------------------------------------------------------------------------------------------

def test_a_dropped_raw_on_the_same_drive_is_hard_linked(tmp_path):
    src = tmp_path / "my RAWs" / "Episode \U0001F602 1.mkv"
    src.parent.mkdir()
    src.write_bytes(b"r" * 1000)
    proj = tmp_path / "proj"
    seen = []
    got = D.take_local(src, proj, progress=lambda f, t: seen.append((f, t)), log_path=proj / "logs" / "dl_raw.log")
    assert got == proj / src.name and os.path.samefile(got, src) and src.stat().st_nlink == 2
    assert seen[-1][0] == 1.0 and "linked" in seen[-1][1]
    assert "linked (no copy)" in (proj / "logs" / "dl_raw.log").read_text(encoding="utf-8")
    assert D.take_local(src, proj) == got                                 # dropped again: the same file
    assert D.take_local(got, proj) == got                                 # already in the folder


def test_when_a_link_is_not_possible_it_is_copied_and_the_log_says_why(tmp_path, monkeypatch):
    src = tmp_path / "Episode 1.mkv"
    src.write_bytes(os.urandom(20 << 20))
    proj = tmp_path / "proj"

    def no_link(a, b):
        raise OSError(1, "Incorrect function")
    monkeypatch.setattr(D.os, "link", no_link)
    seen = []
    got = D.take_local(src, proj, progress=lambda f, t: seen.append((f, t)), log_path=proj / "l.log")
    assert got.read_bytes() == src.read_bytes() and not os.path.samefile(got, src)
    assert "copied, not linked: Incorrect function" in (proj / "l.log").read_text(encoding="utf-8")
    assert seen[-1] == (1.0, "copied -- 21.0 MB")
    monkeypatch.setattr(D, "cannot_link", lambda a, b: "another drive (D: and C:)")
    got2 = D.take_local(src, proj, log_path=proj / "l.log")
    assert got2.name == "Episode 1-2.mkv"                                 # the name is taken: -2
    assert "copied, not linked: another drive (D: and C:)" in (proj / "l.log").read_text(encoding="utf-8")


def test_another_drive_is_named_in_plain_words(tmp_path):
    reason = D.cannot_link(Path("D:/RAWs/x.mkv"), tmp_path)
    assert reason == f"another drive (D: and {str(tmp_path.resolve())[:2]})" or reason is None   # tmp on D: too


def test_stop_during_a_copy_leaves_nothing(tmp_path, monkeypatch):
    src = tmp_path / "big.mkv"
    src.write_bytes(os.urandom(40 << 20))
    proj = tmp_path / "proj"
    monkeypatch.setattr(D, "cannot_link", lambda a, b: "a network folder")
    cancel = threading.Event()
    with pytest.raises(D.Cancelled):
        D.take_local(src, proj, progress=lambda f, t: cancel.set(), cancel=cancel)
    assert list(proj.iterdir()) == []


# ---------------------------------------------------------------------------------------------------------------------
# yt-dlp kept new (pip is never run here)
# ---------------------------------------------------------------------------------------------------------------------

def test_ytdlp_is_updated_at_most_once_a_day_and_offline_is_no_problem(tmp_path, monkeypatch):
    calls = []
    versions = ["2026.08.19"]                                             # before the first update, then newer
    monkeypatch.setattr(D, "installed_version", lambda py=None: versions.pop(0) if len(versions) > 1 else versions[0])
    versions.append("2026.10.15")
    result = {"code": 0}

    def fake_run(cmd, timeout_s, cancel=None):
        calls.append(cmd)
        if result["code"] == "boom":
            raise RuntimeError("pip crashed")
        return result["code"], "", "" if result["code"] == 0 else "ERROR: No matching distribution (offline)"
    monkeypatch.setattr(D, "_run_capture", fake_run)
    msg = D.update_ytdlp(work=tmp_path)
    assert msg == "yt-dlp updated: 2026.08.19 -> 2026.10.15"
    assert calls[0][1:5] == ["-m", "pip", "install", "-U"] and "yt-dlp[default]" in calls[0]
    stamp = (tmp_path / "ytdlp_last_update.txt").read_text(encoding="utf-8").splitlines()
    assert stamp[1] == "ok" and stamp[2] == msg
    assert D.update_ytdlp(work=tmp_path).startswith("yt-dlp was checked today already") and len(calls) == 1
    assert D.update_ytdlp(force=True, work=tmp_path) == "yt-dlp is up to date (2026.10.15)" and len(calls) == 2
    result["code"] = 1
    assert D.update_ytdlp(force=True, work=tmp_path).startswith("could not check for a newer yt-dlp (no internet?)")
    assert "failed" in (tmp_path / "ytdlp_last_update.txt").read_text(encoding="utf-8")
    assert "failed less than an hour ago" in D.update_ytdlp(work=tmp_path) and len(calls) == 3
    result["code"] = "boom"
    assert D.update_ytdlp(force=True, work=tmp_path).startswith("could not check")   # never raises


def test_ytdlp_is_not_updated_while_a_download_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_active", 1)
    monkeypatch.setattr(D, "_run_capture", lambda *a, **k: pytest.fail("pip must not run"))
    assert "a download is running" in D.update_ytdlp(force=True, work=tmp_path)


# ---------------------------------------------------------------------------------------------------------------------
# a project's download step
# ---------------------------------------------------------------------------------------------------------------------

def _result(folder: Path, name: str, vid: str, duration: float) -> D.DownloadResult:
    p = folder / f"{name} [{vid}].mp4"
    p.write_bytes(b"v")
    return D.DownloadResult(p, name, vid, 1080, 1920, "avc1", "mp4a", 30.0, duration, "299+140", "why", size=1,
                            seconds=1.0)


def test_download_project_writes_the_step_and_the_files(tmp_path, monkeypatch):
    raw_src = tmp_path / "RAW episode.mkv"
    raw_src.write_bytes(b"raw")
    p = P.create(tmp_path / "root", competitor_url="https://www.youtube.com/shorts/4v3FsM0BzWQ", raw_file=raw_src)
    states = []

    def fake_download(url, dest, role, progress=None, cancel=None, log_path=None, probed=None):
        states.append(P.load(dest).step("download")["state"])
        progress(0.5, "half")
        return _result(Path(dest), "Tough Guy \U0001F602", "4v3FsM0BzWQ", 37.0)
    monkeypatch.setattr(D, "download", fake_download)
    seen = []
    out = D.download_project(p, progress=lambda r, f, t: seen.append((r, f, t)))
    d = P.load(p.folder).data
    assert states == ["running"] and ("competitor", 0.5, "half") in seen
    assert d["steps"]["download"]["state"] == "done" and d["steps"]["download"]["error"] is None
    assert d["competitor"]["file"] == "Tough Guy \U0001F602 [4v3FsM0BzWQ].mp4"
    assert d["competitor"]["title"] == "Tough Guy \U0001F602" and d["competitor"]["id"] == "4v3FsM0BzWQ"
    assert d["raw"]["file"] == "RAW episode.mkv" and os.path.samefile(p.folder / "RAW episode.mkv", raw_src)
    assert d["steps"]["download"]["competitor"]["format"] == "299+140"
    assert isinstance(out["competitor"], D.DownloadResult) and out["raw"] == p.folder / "RAW episode.mkv"
    assert (p.folder / "logs" / "download_raw.log").is_file()


def test_a_failed_download_is_written_in_plain_words_and_swapped_links_are_noticed(tmp_path, monkeypatch):
    p = P.create(tmp_path, competitor_url="https://youtu.be/w2fdx42TqPg", raw_url="https://youtu.be/4v3FsM0BzWQ")

    def private(url, dest, role, **k):
        if role == "raw":
            raise D.DownloadError(D.PRIVATE, "ERROR: [youtube] 4v3FsM0BzWQ: Private video")
        return _result(Path(dest), "Long one", "w2fdx42TqPg", 555.0)
    monkeypatch.setattr(D, "download", private)
    with pytest.raises(D.DownloadError):
        D.download_project(p)
    s = P.load(p.folder).step("download")
    assert s["state"] == "failed" and s["error"] == "RAW: " + D.PRIVATE
    assert s["competitor"]["format"] == "299+140"                        # the file that worked is remembered
    asked = []
    monkeypatch.setattr(D, "download", lambda url, dest, role, **k: asked.append(role) or private(url, dest, role))
    with pytest.raises(D.DownloadError):
        D.download_project(p)                                             # again: the competitor is already there
    assert asked == ["raw"]
    monkeypatch.setattr(D, "download", lambda url, dest, role, **k: _result(
        Path(dest), role, "w2fdx42TqPg" if role == "competitor" else "4v3FsM0BzWQ",
        555.0 if role == "competitor" else 37.0))
    D.download_project(p)
    s = P.load(p.folder).step("download")
    assert s["state"] == "done" and "were the links swapped?" in s["note"]
    same = P.create(tmp_path, competitor_url="https://youtu.be/w2fdx42TqPg",
                    raw_url="https://www.youtube.com/watch?v=w2fdx42TqPg")
    with pytest.raises(D.DownloadError, match="the same video"):
        D.download_project(same)
    assert P.load(same.folder).step("download")["state"] == "failed"
