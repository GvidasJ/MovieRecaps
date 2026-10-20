"""Why a run stopped, in plain words (day 13): the tool's console (its stderr), its log (extras\\match_cuts.log) and
its exit code -> what happened and what to do. ONE function for every place that shows a stopped run: the batch
summary, and in the app the queue row and its report, the New video row, History and problems_queue.md (the app
must never just say "details in ... match_cuts.log").

    r = explain(stderr, log_text, code)        # text only: nothing is read
    r = explain_run(run_dir, stderr, code)     # also reads the run's extras\\match_cuts.log / batch_console.log
    r.text    'The disk C: is full: the run stopped while converting the RAW.'
    r.todo    'Free space on C: (Board → Clean up), then press Try again.'
    r.line()  both, one line;  r.kind  DISK_FULL / MEMORY / ... / TOOL_STOP / UNKNOWN

What it knows -- the first that fits wins:

* the tool's own stops stay as they are: WRONG PAIR, PICTURE NOT FOUND, NOT IN THIS RAW (the competitor's scenes are
  not in this RAW), "The inputs look wrong ..." -- they are plain words already and say what to do;
* a full disk: "No space left on device", [Errno 28], Windows' "There is not enough space on the disk" (112), the
  tool's NoSpaceError, and an ffmpeg / ffprobe exit code that is -28 read as an unsigned 32-bit number (4294967268 =
  ENOSPC: ffmpeg 7 exits with its own error code). The drive comes from the line (the file it wrote) when it can;
* the tool's worker pool losing its state file (FileNotFoundError ...\\match_cuts_pool_...\\state-4.pkl): the pool's
  helper processes read that file from the temp folder (visual_match._spawn_map writes it there) -- a crash after C:
  filled up (said as certain when the console or the log also shows a full disk, else as the likely cause);
* the other ffmpeg / ffprobe codes: negative errno values (-12 out of memory, -2 a file not found, -13 access denied,
  -5 an input/output error, -22 an invalid setting, -27 a file too big, -38 a name too long ...) and FFmpeg's own
  AVERROR tags (-1094995529 INVALIDDATA = a damaged video, EOF = cut short, the *_NOT_FOUND ones = a missing part);
* Python / Windows errors: out of memory (also Windows' "paging file is too small"), the graphics card out of memory,
  a file another program holds, a file that is gone, a network folder that dropped, a disk error, a path too long, a
  missing part of the tool (No module named / DLL load failed -> setup_app.py);
* Windows' crash codes of the tool's own process (0xC0000005 a memory access error, 0xC00000FD a stack overflow,
  0xC0000409 a library stopped it, 0xC0000017 out of memory, 0xC0000135 / 0xC0000142 a library missing / not
  starting, 0xC000013A / 0x40010004 closed from outside, 3 without a result = an abort ...);
* anything else: the last meaningful ERROR line (never a Python traceback, never a library warning, never the
  "details in ..." line) in plain form, and "the full log: Open log".

Pure functions (explain_run reads two log tails and the drive's free space -- call it from a worker thread). No stage
module imports this file: editing it invalidates no cache."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

DISK_FULL, MEMORY, GPU_MEMORY = "disk full", "out of memory", "graphics card memory"
FILE_MISSING, FILE_BUSY, DISK_ERROR, NETWORK = "file missing", "file in use", "disk error", "network folder"
BROKEN_VIDEO, BAD_SETTING, PATH_TOO_LONG, FILE_TOO_BIG = "damaged video", "bad setting", "path too long", "file too big"
SETUP, CRASH, CLOSED, TOOL_STOP, UNKNOWN = "setup", "crash", "closed", "tool stop", "unknown"

TRY = "press Try again"
CLEAN_UP = "Board → Clean up"
OPEN_LOG = "Open log"
SETUP_LINE = r".venv\Scripts\python tools\app\setup_app.py"
MAX_TEXT = 260


@dataclass(frozen=True)
class Reason:
    """What happened (``text``) and what to do (``todo``), each one sentence; ``kind`` one of the constants above;
    ``detail`` the console / log line it was read from (for the log and problems_queue.md -- never shown alone);
    ``drive`` the full disk ('C:' or a network folder) when known."""
    text: str
    todo: str = ""
    kind: str = UNKNOWN
    detail: str = ""
    drive: str = ""

    def line(self) -> str:
        """Both sentences as one line ('The disk C: is full. Free space on C: ..., then press Try again.'); the
        tool's own stop message exactly as it is."""
        if self.kind == TOOL_STOP:
            return self.text
        return " ".join(x for x in (_sentence(self.text), _sentence(self.todo)) if x)


def _sentence(s: str) -> str:
    s = " ".join(str(s or "").split())
    if not s:
        return ""
    s = s[0].upper() + s[1:]
    return s if s[-1] in ".!?)" or s.endswith("Open log") else s + "."


# ---------------------------------------------------------------------------------------------------------------------
# exit codes: ffmpeg / ffprobe (negative errno or AVERROR tags), and Windows' crash codes of a process
# ---------------------------------------------------------------------------------------------------------------------

def signed32(code: int) -> int:
    """An exit code as Windows reports it (unsigned 32-bit) back to the signed number the program returned:
    4294967268 -> -28."""
    c = int(code)
    return c - (1 << 32) if c >= (1 << 31) else c


def _tag(a, b, c, d) -> int:
    f = (lambda x: x if isinstance(x, int) else ord(x))
    return -(f(a) | f(b) << 8 | f(c) << 16 | f(d) << 24)


# errno numbers as ffmpeg's Windows builds (mingw-w64 / MSVC errno.h) and Python on Windows use them
ERRNO: dict[int, tuple[str, str]] = {
    1: (FILE_BUSY, "an operation was not allowed (EPERM)"),
    2: (FILE_MISSING, "a file was not found (ENOENT)"),
    5: (DISK_ERROR, "an input/output error (EIO)"),
    9: (DISK_ERROR, "a file was closed under it (EBADF)"),
    11: (MEMORY, "a resource was not available (EAGAIN)"),
    12: (MEMORY, "out of memory (ENOMEM)"),
    13: (FILE_BUSY, "access was denied (EACCES)"),
    16: (FILE_BUSY, "a file or device was busy (EBUSY)"),
    17: (FILE_BUSY, "a file was in the way (EEXIST)"),
    20: (FILE_MISSING, "a folder in the path is not a folder (ENOTDIR)"),
    21: (FILE_BUSY, "a folder was given where a file was expected (EISDIR)"),
    22: (BAD_SETTING, "an invalid argument (EINVAL)"),
    23: (MEMORY, "too many files open (ENFILE)"),
    24: (MEMORY, "too many files open (EMFILE)"),
    27: (FILE_TOO_BIG, "a file too big for the disk (EFBIG)"),
    28: (DISK_FULL, "no space left on the disk (ENOSPC)"),
    30: (FILE_BUSY, "a read-only disk (EROFS)"),
    32: (DISK_ERROR, "the other side of a pipe closed (EPIPE)"),
    38: (PATH_TOO_LONG, "a file name too long (ENAMETOOLONG)"),
    138: (NETWORK, "a connection timed out (ETIMEDOUT)"),
    107: (NETWORK, "a connection was refused (ECONNREFUSED)"),
    108: (NETWORK, "a connection was reset (ECONNRESET)"),
}

# FFmpeg's own error codes (libavutil/error.h: FFERRTAG = minus four characters)
AVERROR: dict[int, tuple[str, str]] = {
    _tag("I", "N", "D", "A"): (BROKEN_VIDEO, "invalid data in the video (AVERROR_INVALIDDATA)"),
    _tag("E", "O", "F", " "): (BROKEN_VIDEO, "the video ends too early (AVERROR_EOF)"),
    _tag("E", "X", "I", "T"): (CLOSED, "FFmpeg was told to stop (AVERROR_EXIT)"),
    _tag(0xF8, "D", "E", "C"): (BROKEN_VIDEO, "no decoder for the video's format (AVERROR_DECODER_NOT_FOUND)"),
    _tag(0xF8, "D", "E", "M"): (BROKEN_VIDEO, "not a video format FFmpeg reads (AVERROR_DEMUXER_NOT_FOUND)"),
    _tag(0xF8, "E", "N", "C"): (SETUP, "an encoder missing in this FFmpeg (AVERROR_ENCODER_NOT_FOUND)"),
    _tag(0xF8, "M", "U", "X"): (SETUP, "an output format missing in this FFmpeg (AVERROR_MUXER_NOT_FOUND)"),
    _tag(0xF8, "F", "I", "L"): (SETUP, "a filter missing in this FFmpeg (AVERROR_FILTER_NOT_FOUND)"),
    _tag(0xF8, "O", "P", "T"): (SETUP, "an option this FFmpeg does not know (AVERROR_OPTION_NOT_FOUND)"),
    _tag(0xF8, "P", "R", "O"): (SETUP, "a protocol missing in this FFmpeg (AVERROR_PROTOCOL_NOT_FOUND)"),
    _tag(0xF8, "S", "T", "R"): (BROKEN_VIDEO, "a picture or sound stream not in the file (AVERROR_STREAM_NOT_FOUND)"),
    _tag(0xF8, "B", "S", "F"): (SETUP, "a bitstream filter missing in this FFmpeg (AVERROR_BSF_NOT_FOUND)"),
    _tag("B", "U", "G", "!"): (CRASH, "an internal FFmpeg bug (AVERROR_BUG)"),
    _tag("B", "U", "G", " "): (CRASH, "an internal FFmpeg bug (AVERROR_BUG2)"),
    _tag("B", "U", "F", "S"): (CRASH, "a buffer too small inside FFmpeg (AVERROR_BUFFER_TOO_SMALL)"),
    _tag("E", "X", "T", " "): (CRASH, "an error in a library FFmpeg uses (AVERROR_EXTERNAL)"),
    _tag("P", "A", "W", "E"): (BROKEN_VIDEO, "a video feature FFmpeg does not support yet (AVERROR_PATCHWELCOME)"),
    _tag("U", "N", "K", "N"): (CRASH, "an unknown FFmpeg error (AVERROR_UNKNOWN)"),
    -0x2bb2afa8: (SETUP, "an experimental FFmpeg feature (AVERROR_EXPERIMENTAL)"),
    -0x636e6701: (BROKEN_VIDEO, "the video changed format part-way (AVERROR_INPUT_CHANGED)"),
    -0x636e6702: (BROKEN_VIDEO, "the output changed format part-way (AVERROR_OUTPUT_CHANGED)"),
}

# Windows' exit codes of a process that crashed or was closed (NTSTATUS); both spellings (signed / unsigned) work
_NT: dict[int, tuple[str, str]] = {
    0xC0000005: (CRASH, "the tool crashed (a memory access error inside a program library, 0xC0000005)"),
    0xC0000409: (CRASH, "the tool crashed (a program library stopped it, 0xC0000409)"),
    0xC00000FD: (CRASH, "the tool crashed (it ran out of stack memory, 0xC00000FD)"),
    0xC0000017: (MEMORY, "the tool ran out of memory (0xC0000017)"),
    0xC0000374: (CRASH, "the tool crashed (memory damaged inside a program library, 0xC0000374)"),
    0xC0000006: (DISK_ERROR, "the tool crashed: Windows could not read its program files from the disk (an in-page "
                             "error, 0xC0000006)"),
    0xC000001D: (CRASH, "the tool crashed (the processor does not know an instruction a program library uses, "
                        "0xC000001D)"),
    0xC0000094: (CRASH, "the tool crashed (a division by zero inside a program library, 0xC0000094)"),
    0xC0000135: (SETUP, "the tool could not start: a program library (DLL) is missing (0xC0000135)"),
    0xC0000142: (SETUP, "the tool could not start: a program library (DLL) failed to load (0xC0000142)"),
    0xC000041D: (CRASH, "the tool crashed (an error inside a callback of a program library, 0xC000041D)"),
    0xE06D7363: (CRASH, "the tool crashed (an unhandled error inside a C++ program library, 0xE06D7363)"),
    0xC000013A: (CLOSED, "the run was closed from outside (Ctrl+C or the console window closed, 0xC000013A)"),
    0x40010004: (CLOSED, "the run was closed from outside (Windows shutting down or signing out, or Task Manager, "
                         "0x40010004)"),
}
CRASH_CODES: dict[int, str] = {}
for _c, (_k, _w) in _NT.items():
    CRASH_CODES[_c] = _w
    CRASH_CODES[signed32(_c)] = _w
CRASH_KIND = {c: _NT[c if c >= 0 else c + (1 << 32)][0] for c in CRASH_CODES}
ABORT_WORDS = "the tool stopped by itself (exit code 3 without a result: an abort or a fatal Python error)"
INTERRUPTED = "the run was interrupted (Ctrl+C, or the window that ran it closed)"


def crash_words(code: int | None) -> str:
    """A process's crash exit code in plain words ('' for an ordinary exit code)."""
    return CRASH_CODES.get(int(code), "") if code is not None else ""


def program_code(code: int | None, program: str = "ffmpeg") -> tuple[str, str]:
    """(kind, words) of an ffmpeg / ffprobe exit code: a negative errno or an AVERROR tag (4294967268 -> -28 ->
    (DISK_FULL, 'no space left on the disk (ENOSPC)')); a crash code; ('', '') for 1 and anything unknown (then
    FFmpeg's own message says more)."""
    if code is None:
        return "", ""
    try:
        s = signed32(int(code))
    except (TypeError, ValueError):
        return "", ""
    if s in AVERROR:
        return AVERROR[s]
    if -200 < s < 0 and -s in ERRNO:
        return ERRNO[-s]
    shown = {"ffmpeg": "FFmpeg", "ffprobe": "FFprobe"}.get(str(program).lower(), str(program))
    if s == 69:
        return BROKEN_VIDEO, "too many errors while reading the video (exit 69)"
    if s == 255:
        return CLOSED, f"{shown} was stopped (exit 255)"
    w = crash_words(int(code))
    if w:
        return CRASH_KIND.get(int(code), CRASH), w.replace("the tool", shown, 1)
    return "", ""


# ---------------------------------------------------------------------------------------------------------------------
# reading the console and the log
# ---------------------------------------------------------------------------------------------------------------------

_TS = re.compile(r"^(?:\d{4}-\d\d-\d\d )?\d\d:\d\d:\d\d(?:[,.]\d+)? ")
_LEVEL = re.compile(r"^(?:DEBUG|INFO|WARNING|ERROR|CRITICAL) (?:match_cuts(?:\.\w+)* )?")
_LOG_ERROR = re.compile(r"^(?:\d{4}-\d\d-\d\d )?\d\d:\d\d:\d\d(?:[,.]\d+)? (?:ERROR|CRITICAL) ")
_LIBRARY = re.compile(r"^\s*(?:\[ ?(?:WARN|INFO|DEBUG)\b|\S+\.py:\d+: \w*Warning:|warnings\.warn\(|\S*Warning: |"
                      r"W\d{4} |I\d{4} )")
_DETAILS_IN = re.compile(r"^(?:match_cuts: )?details in \S", re.I)
_FFMPEG_CODE = re.compile(r"\b(?P<prog>ffmpeg|ffprobe)(?:\.exe)?(?: writer)? failed \((?P<code>-?\d+)\)", re.I)
_COMMAND_CODE = re.compile(r"command failed \((?P<code>-?\d+)\): (?P<cmd>\S+)", re.I)
_DISK_FULL_TECH = re.compile(r"no space left on device|\[errno 28\]|\berrno 28\b|\benospc\b|\[winerror 112\]|"
                             r"\bwinerror 112\b|\berror_disk_full\b|nospaceerror|error_handle_disk_full|"
                             r"\[winerror 39\]", re.I)
_DISK_FULL = re.compile(_DISK_FULL_TECH.pattern + r"|not enough space on the disk|there is not enough space on|"
                        r"not enough free space to copy", re.I)
_POOL = re.compile(r"match_cuts_pool_[A-Za-z0-9_]+[\\/]+state-\d+\.pkl", re.I)
_POOL_DIR = re.compile(r"(?P<dir>(?:[A-Za-z]:|\\\\[^\\\s'\"]+)[^'\"\n]*?)[\\/]+match_cuts_pool_", re.I)
_DRIVE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]):[\\/]")
_UNC = re.compile(r"(\\\\[A-Za-z0-9_.$-]+\\[A-Za-z0-9_.$ -]+)")
_QUOTED_PATH = re.compile(r"""['"](?P<p>(?:[A-Za-z]:|\\\\)[^'"\n]+)['"]""")
_TOOL_STOP = re.compile(r"^\s*(?:wrong pair\b|the inputs look wrong\b)|picture not found|not in this raw", re.I)
_INPUT_MISSING = re.compile(r"^(raw|competitor|voice-over) file not found:\s*(.+)$", re.I)
_PY_ERROR = re.compile(r"^(?:match_cuts: )?(?:ERROR: )?(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|"
                       r"Warning|Fault))(?::\s*(?P<msg>.*))?$")

# (kind, pattern) of the Python / Windows error messages; the first that fits an error line wins (the graphics card's
# memory before the computer's: torch says "CUDA out of memory")
_RULES: list[tuple[str, re.Pattern]] = [
    (GPU_MEMORY, re.compile(r"cuda out of memory|outofmemoryerror: cuda|cuda error: out of memory|"
                            r"cudaerrormemoryallocation|cublas_status_alloc_failed|cudnn_status_alloc_failed|"
                            r"out of memory on device|nvenc.*out of memory|hip out of memory", re.I)),
    (MEMORY, re.compile(r"\bmemoryerror\b|unable to allocate|\[errno 12\]|cannot allocate memory|"
                        r"not enough memory|out of memory|\[winerror 1455\]|paging file is too small|bad_alloc|"
                        r"\[winerror 1450\]|insufficient system resources|\[winerror 8\]|"
                        r"brokenprocesspool|process in the process pool was terminated abruptly", re.I)),
    (NETWORK, re.compile(r"\[winerror (?:53|59|64|67|121|1231|1232|51|1219|2250)\]|network path was not found|"
                         r"network name is no longer available|unexpected network error|network name cannot be "
                         r"found|semaphore timeout period has expired|network location cannot be reached", re.I)),
    (DISK_ERROR, re.compile(r"\[errno 5\]|input/output error|\bi/o error\b|\[winerror (?:23|1117|483|1006|21)\]|"
                            r"cyclic redundancy check|device is not ready|request could not be performed because "
                            r"of an i/o device error", re.I)),
    (PATH_TOO_LONG, re.compile(r"\[winerror 206\]|filename or extension is too long|file name too long|"
                               r"\[errno (?:36|38)\]|enametoolong|path too long", re.I)),
    (FILE_TOO_BIG, re.compile(r"\[errno 27\]|file too large|\befbig\b", re.I)),
    (SETUP, re.compile(r"no module named|dll load failed|modulenotfounderror|importerror|"
                       r"is not recognized as an internal or external command|ffmpeg(?:\.exe)?: not found|"
                       r"ffprobe(?:\.exe)?: not found|could not find (?:ffmpeg|ffprobe)|"
                       r"\[winerror (?:126|127|193)\]|the specified (?:module|procedure) could not be found|"
                       r"not a valid win32 application", re.I)),
    (FILE_BUSY, re.compile(r"\[winerror 32\]|being used by another process|\[errno 13\]|permission denied|"
                           r"access is denied|\[winerror 5\]|\bpermissionerror\b|read-only file system|"
                           r"\[winerror 33\]|locked a portion of the file", re.I)),
    (BROKEN_VIDEO, re.compile(r"invalid data found when processing input|moov atom not found|"
                              r"error while decoding|corrupt (?:input|packet|frame|decoded)|invalid nal unit|"
                              r"end of file|truncat(?:ed|ing) (?:packet|file)|could not find codec parameters|"
                              r"does not contain any stream|no such stream|header missing|"
                              r"not a video or it is damaged", re.I)),
    (FILE_MISSING, re.compile(r"\[errno 2\]|no such file or directory|\bfilenotfounderror\b|\[winerror [23]\]|"
                              r"cannot find the (?:file|path) specified|file not found", re.I)),
]


def _un_repr(p: str) -> str:
    """A path as Python's repr shows it ('C:\\\\Users\\\\x') back to one backslash each."""
    return p.replace("\\\\", "\\")


def _clean(line: str) -> str:
    """A console / log line without its time, level and logger name, 'match_cuts: ' and 'ERROR: '."""
    s = _TS.sub("", str(line or "").strip())
    s = _LEVEL.sub("", s)
    for pre in ("match_cuts: ", "ERROR: ", "match_cuts: "):
        if s.startswith(pre):
            s = s[len(pre):]
    return s.strip()


def _lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in str(text or "").splitlines()]


def _drive_of(*texts: str) -> str:
    """The drive the line names last ('C:'), a network folder ('\\\\server\\share'), or ''. The last path of an
    ffmpeg command line is the file it writes."""
    for t in texts:
        t = _un_repr(str(t or ""))
        found: list[tuple[int, str]] = [(m.start(), m.group(1).upper() + ":") for m in _DRIVE.finditer(t)]
        found += [(m.start(), m.group(1)) for m in _UNC.finditer(t)]
        if found:
            return max(found)[1]
    return ""


def _file_name(line: str) -> str:
    """The file name a Python error names ("... No such file or directory: 'C:\\\\x\\\\raw.mp4'" -> 'raw.mp4')."""
    m = _QUOTED_PATH.search(line or "")
    if not m:
        return ""
    p = _un_repr(m.group("p")).rstrip("\\/")
    return re.split(r"[\\/]", p)[-1] if p else ""


def _step(line: str) -> str:
    """'while converting the RAW' ... from the error line ('' when it does not say)."""
    low = (line or "").lower()
    if "conform raw" in low:
        return "while converting the RAW"
    if "conform comp" in low:
        return "while converting the competitor"
    if "ffprobe" in low:
        return "while reading a video"
    if "ffmpeg writer" in low:
        return "while writing a video"
    if "ffmpeg" in low:
        return "while FFmpeg worked on a video"
    if "stereo copy" in low:
        return "while making the stereo sound"
    return ""


def _strip_command(s: str) -> str:
    """An error line without the ffmpeg / ffprobe command line in it ('... failed (1): ffmpeg -v error ...')."""
    m = re.search(r"(\):|:)\s+\S*(?:ffmpeg|ffprobe)(?:\.exe)?\s+-\S", s, re.I)
    return s[:m.start() + len(m.group(1))].rstrip(":").rstrip() if m else s


def plain_line(line: str) -> str:
    """One ERROR line in plain form: no time / level / logger, no 'match_cuts:' / 'ERROR:', no command line, the
    exception's class name only when it says something ('RuntimeError: x' -> 'x', 'ValueError: x' -> 'x
    (ValueError)'), no '[Errno 2]' numbers; at most MAX_TEXT characters."""
    s = _strip_command(_clean(line))
    m = _PY_ERROR.match(s)
    if m:
        t, msg = m.group("type"), (m.group("msg") or "").strip()
        short = t.rsplit(".", 1)[-1]
        if msg and short in ("RuntimeError", "Exception", "OSError", "IOError", "StepError", "CalledProcessError"):
            s = msg
        elif msg:
            s = f"{msg} ({short})"
        else:
            s = short
    s = re.sub(r"\[(?:Errno|WinError) -?\d+\]\s*", "", s)
    s = _un_repr(s)
    s = " ".join(s.split())
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT - 3].rstrip() + "..."


def _console_errors(stderr: str) -> list[str]:
    """The console's lines that may say why it stopped, oldest first: cli's "match_cuts: ERROR: ..." and any line
    that is not a log line, a library warning, a traceback frame or "details in ..."."""
    out: list[str] = []
    for ln in _lines(stderr):
        s = ln.strip()
        if not s or _LIBRARY.match(ln) or _DETAILS_IN.match(s) or s.startswith(("Run folder", "Traceback", "File ")):
            continue
        if ln[:1].isspace():                       # a traceback frame / a source line
            continue
        if _TS.match(s) and not _LOG_ERROR.match(s):
            continue
        out.append(s)
    return out


def _log_errors(log: str) -> list[str]:
    """match_cuts.log's ERROR lines, oldest first (their first line only: never the traceback logged under them)."""
    return [ln.strip() for ln in _lines(log) if _LOG_ERROR.match(ln.strip())]


def _meaningful(line: str) -> bool:
    """A console line that says why the run stopped: an exception (cli's "match_cuts: ERROR: X: ..."), a fatal error,
    an ffmpeg / ffprobe failure."""
    return bool(_PY_ERROR.match(_clean(line)) or line.startswith(("match_cuts: ERROR:", "Fatal Python error"))
                or _FFMPEG_CODE.search(line) or _COMMAND_CODE.search(line))


def _error_lines(stderr: str, log: str) -> list[str]:
    """The lines that may say why it stopped, NEWEST FIRST: the console's (the exception that stopped the run is
    printed there, and only there), then the log's ERROR lines (older soft errors of steps that went on)."""
    return list(reversed(_console_errors(stderr))) + list(reversed(_log_errors(log)))


def _tool_stop(stop: str) -> bool:
    return bool(stop) and bool(_TOOL_STOP.search(stop))


def _todo_disk(drive: str) -> str:
    if drive:
        return f"free space on {drive} ({CLEAN_UP}), then {TRY}"
    return f"free some space on C: and on the project folder's drive ({CLEAN_UP}), then {TRY}"


def _disk_text(drive: str, step: str, free_gb: float | None = None) -> str:
    left = f" ({free_gb:.1f} GB free now)" if free_gb is not None else ""
    head = f"the disk {drive} is full{left}" if drive and drive.endswith(":") else \
        f"the network folder {drive} is full{left}" if drive else "a disk is full"
    return head + (f": the run stopped {step}" if step else "")


_TODO = {
    MEMORY: f"close other big programs (Premiere, a browser, another run), then {TRY}",
    GPU_MEMORY: f"close other programs that use the graphics card (Premiere, a game, another run), then {TRY}",
    FILE_MISSING: f"check that the RAW and the competitor are still where they were (not moved, renamed or "
                  f"deleted), then {TRY}",
    FILE_BUSY: f"close the program that has the file open (Premiere, a video player, an Explorer preview), then {TRY}",
    DISK_ERROR: f"check the drive (and its cable, or the network), then {TRY}",
    NETWORK: f"check that the shared folder opens in Explorer (the PC must be on), then {TRY}",
    BROKEN_VIDEO: f"download or copy that video again (or use another copy), then {TRY}",
    BAD_SETTING: f"{TRY}; if it stops the same way again, the full log: {OPEN_LOG}",
    PATH_TOO_LONG: f"use a shorter project or file name (or a folder nearer the top of the drive), then {TRY}",
    FILE_TOO_BIG: f"save to an NTFS drive (C: or D:), then {TRY}",
    SETUP: f"run setup_app.py again (in PowerShell: {SETUP_LINE}), then {TRY}",
    CRASH: f"{TRY}; if it crashes the same way again, the full log: {OPEN_LOG}",
    CLOSED: TRY,
    UNKNOWN: f"{TRY}; if it stops the same way again, the full log: {OPEN_LOG}",
}

_KIND_TEXT = {
    MEMORY: "the computer ran out of memory (RAM)",
    GPU_MEMORY: "the graphics card ran out of memory",
    FILE_MISSING: "a file the run needs is not there any more",
    FILE_BUSY: "Windows did not let the run open a file (another program has it open, or the folder is read-only)",
    DISK_ERROR: "a disk could not be read or written (an input/output error: the drive, its cable or the network)",
    NETWORK: "the shared folder on the network could not be reached (the connection dropped)",
    BROKEN_VIDEO: "a video file is damaged or not complete, so FFmpeg could not read it",
    BAD_SETTING: "FFmpeg refused a setting for this video",
    PATH_TOO_LONG: "a file path is too long for Windows (over 260 characters)",
    FILE_TOO_BIG: "a file is too big for this disk (a FAT32 drive holds files up to 4 GB)",
    SETUP: "a part of the tool is missing or broken on this computer",
}


def _kind_reason(kind: str, line: str, extra: str = "") -> Reason:
    """The Reason of a known kind for one error line (its file name / step / what it said added)."""
    text = _KIND_TEXT.get(kind, "")
    name = _file_name(line)
    if kind == FILE_MISSING and name:
        text = f"a file the run needs is not there any more ({name})"
    elif kind == FILE_BUSY and name:
        text = f"Windows did not let the run open {name} (another program has it open, or the folder is read-only)"
    elif kind == BROKEN_VIDEO and name:
        text = f"the video {name} is damaged or not complete, so FFmpeg could not read it"
    elif kind == SETUP and not extra:
        what = re.sub(r"\s*\((?:\w+Error|\w+Exception)\)$", "", plain_line(line))
        if what and len(what) < 140:
            text = f"{text} ({what})"
    step = _step(line)
    if step and kind not in (SETUP,):
        text = f"{text} {step}"
    if extra:
        text = f"{text} ({extra})"
    return Reason(text, _TODO.get(kind, _TODO[UNKNOWN]), kind, _clean(line)[:600])


# ---------------------------------------------------------------------------------------------------------------------
# the one function
# ---------------------------------------------------------------------------------------------------------------------

def explain(stderr: str = "", log: str = "", code: int | None = None, stop: str | None = None) -> Reason:
    """Why a run stopped: its console (``stderr`` -- or the run's batch_console.log), its log (match_cuts.log's
    text, the end of it is enough) and its exit code -> a Reason. ``stop``: the batch's stop message when the caller
    has it (match_cuts.batch.stop_message; read from ``stderr`` when None). Reads nothing; never raises."""
    try:
        return _explain(str(stderr or ""), str(log or ""), code, stop)
    except Exception as e:  # noqa: BLE001 - a reason must never break the queue
        return Reason(f"the run stopped (the reason could not be read: {type(e).__name__})", _TODO[UNKNOWN], UNKNOWN)


def _explain(stderr: str, log: str, code: int | None, stop: str | None) -> Reason:
    if stop is None:
        from .batch import stop_message
        stop = stop_message(stderr, code=code)
    stop = (stop or "").strip()
    if _tool_stop(stop):                                     # WRONG PAIR ... -- plain already, with what to do
        return Reason(stop, "", TOOL_STOP, stop)
    missing = _INPUT_MISSING.match(_clean(stop))             # cli's own "raw file not found: <path>"
    if missing:
        who = {"raw": "the RAW file", "competitor": "the competitor's file"}.get(missing.group(1).lower(),
                                                                              "the voice-over file")
        return Reason(f"{who} is not there any more ({missing.group(2).strip()})",
                      f"put it back where it was (or drop the video again), then {TRY}", FILE_MISSING, stop)
    # the newest first. The exception that stopped the run is printed only to the console ("match_cuts: ERROR: ..."):
    # when it is there, it alone decides; the log's ERROR lines (soft errors of steps that went on) only otherwise
    cons = list(reversed(_console_errors(stderr)))
    errs = cons if any(_meaningful(ln) for ln in cons) else cons + list(reversed(_log_errors(log)))
    every = _lines(stderr) + _lines(log)
    # ffmpeg / ffprobe exit codes in the error lines (the newest first)
    prog: tuple[str, str, str] | None = None                # (kind, words, line)
    for ln in errs:
        m = _FFMPEG_CODE.search(ln) or _COMMAND_CODE.search(ln)
        if not m:
            continue
        name = (m.groupdict().get("prog") or Path(_un_repr(m.groupdict().get("cmd") or "")).stem).lower()
        if name not in ("ffmpeg", "ffprobe"):
            continue
        k, w = program_code(int(m.group("code")), name)
        prog = (k, w, ln)
        break
    pool = next((ln for ln in reversed(every) if _POOL.search(ln)), "")
    full = [ln for ln in every if _DISK_FULL.search(ln) and not _LIBRARY.match(ln)]
    disk_line = prog[2] if prog and prog[0] == DISK_FULL else (full[-1] if full else "")
    if disk_line or (prog and prog[0] == DISK_FULL):
        # a full disk is the reason of everything after it (the pool's lost files, ffmpeg's failure, logging errors)
        drive = _drive_of(disk_line) or (_drive_of(prog[2]) if prog else "") or \
            (_drive_of(_POOL_DIR.search(_un_repr(pool)).group("dir")) if pool and _POOL_DIR.search(_un_repr(pool))
             else "")
        if pool:
            tmp = _temp_dir(pool)
            text = (f"the run crashed after {drive or 'C:'} filled up: the tool's helper processes could not read "
                    f"their working file in the temp folder" + (f" ({tmp})" if tmp else ""))
            return Reason(text, _todo_disk(drive or "C:"), DISK_FULL, _clean(pool)[:600], drive or "C:")
        step = _step(prog[2] if prog else disk_line)
        if not step:
            step = _step(next((ln for ln in errs if _step(ln)), ""))
        nospace = next((ln for ln in errs if "nospaceerror" in ln.lower()), "")
        if nospace:
            return Reason(plain_line(nospace), _todo_disk(drive), DISK_FULL, _clean(nospace)[:600], drive)
        return Reason(_disk_text(drive, step), _todo_disk(drive), DISK_FULL, _clean(disk_line)[:600], drive)
    if pool:
        tmp = _temp_dir(pool)
        drive = _drive_of(tmp) or "C:"
        text = (f"the run crashed: the tool's helper processes lost their working file in the temp folder"
                + (f" ({tmp})" if tmp else "") + f" -- this happens when {drive} is full (or when something empties "
                f"the temp folder during a run)")
        return Reason(text, _todo_disk(drive), DISK_FULL, _clean(pool)[:600], drive)
    if prog and prog[0]:
        k, w, ln = prog
        # what FFmpeg's code adds, in words (its code name is for the log only); the kinds that say it all, without
        extra = re.sub(r"\s*\((?:AVERROR_\w+|E[A-Z]+|exit -?\d+|0x[0-9A-Fa-f]+)\)$", "", w) \
            if k in (BROKEN_VIDEO, SETUP, BAD_SETTING) else ""
        r = _kind_reason(k, ln, extra)
        r = replace(r, detail=f"{w}: {r.detail}"[:600])
        if k == CRASH:
            r = replace(r, text=f"{w} {_step(ln)}".strip(), todo=_TODO[CRASH])
        elif k == CLOSED:
            r = replace(r, text=f"FFmpeg was stopped {_step(ln)}".strip(), todo=_TODO[CLOSED])
        return r
    # Python / Windows errors in the error lines (the newest first); FFmpeg's own words for its exit code 1
    for ln in errs:
        for kind, rx in _RULES:
            if rx.search(ln):
                if kind == FILE_MISSING and re.search(r"\[winerror 2\] the system cannot find the file specified\s*$",
                                                      ln, re.I):
                    return _kind_reason(SETUP, ln)                   # a program to start that is not there
                return _kind_reason(kind, ln)
    if prog:                                                     # ffmpeg's code 1 and no words we know
        return Reason(f"FFmpeg stopped {_step(prog[2]) or 'on a video'}: {plain_line(prog[2])}".strip(),
                      _TODO[UNKNOWN], UNKNOWN, _clean(prog[2])[:600])
    crash = crash_words(code)
    if code is not None and crash:
        k = CRASH_KIND.get(int(code), CRASH)
        return Reason(crash, _TODO.get(k, _TODO[CRASH]), k, f"exit code {code}")
    if code == 130 or any(_clean(ln).lower() in ("interrupted", "keyboardinterrupt") for ln in errs):
        return Reason(INTERRUPTED, TRY, CLOSED, "interrupted")
    fatal = next((ln.strip() for ln in reversed(_lines(stderr)) if ln.startswith("Fatal Python error:")), "")
    best = next((ln for ln in errs if _PY_ERROR.match(_clean(ln)) or _LOG_ERROR.match(ln)
                 or _clean(ln).lower().startswith("stopped:")), "")
    if not best and stop and stop not in ("see the log", ABORT_WORDS) and not stop.startswith("Fatal Python error"):
        best = stop
    if best:
        return Reason(f"the run stopped on a problem the app does not know yet: {plain_line(best)}", _TODO[UNKNOWN],
                      UNKNOWN, _clean(best)[:600])
    if fatal:
        return Reason("the tool crashed (a program library stopped it with a fatal error)", _TODO[CRASH], CRASH, fatal)
    if code == 3:
        return Reason(ABORT_WORDS, _TODO[CRASH], CRASH, "exit code 3")
    return Reason("the run stopped without saying why" + (f" (exit code {code})" if code is not None else ""),
                  _TODO[UNKNOWN], UNKNOWN, "")


def _temp_dir(pool_line: str) -> str:
    m = _POOL_DIR.search(_un_repr(pool_line))
    if not m:
        return ""
    d = m.group("dir").strip().strip("'\"")
    i = max(d.rfind("'"), d.rfind('"'), d.rfind(" "))
    d = d[i + 1:] if i >= 0 and not re.match(r"^[A-Za-z]:\\", d) else d
    k = d.find(":\\")
    return d[k - 1:] if k > 0 else d


def plain_text(text: str) -> str:
    """A stored technical failure ('the run stopped (exit 2): ERROR: RuntimeError: conform raw: ffmpeg failed
    (4294967268): ...', an old project.json's) as the plain line; plain words stay as they are."""
    t = " ".join(str(text or "").split())
    if not t:
        return ""
    body = re.sub(r"^the run stopped \(exit -?\d+\):\s*", "", t)
    m = re.match(r"^the run stopped \(exit (-?\d+)\)", t)
    code = int(m.group(1)) if m else None
    technical = (_FFMPEG_CODE.search(body) or _COMMAND_CODE.search(body) or _POOL.search(body)
                 or _DISK_FULL_TECH.search(body) or _PY_ERROR.match(_clean(body)) or _DETAILS_IN.match(body)
                 or body.lower().startswith(("error:", "match_cuts:", "traceback")))
    if not technical:
        return t
    if _DETAILS_IN.match(body) and not (_FFMPEG_CODE.search(body) or _POOL.search(body)
                                        or _DISK_FULL_TECH.search(body)):
        return Reason("the run stopped; the reason is in its log", f"{TRY}; the full log: {OPEN_LOG}", UNKNOWN).line()
    body = re.sub(r"\s*(?:match_cuts: )?details in \S.*$", "", body)
    r = explain(stderr=body.replace(" match_cuts: ", "\nmatch_cuts: "), code=code, stop="")
    return r.line()


# ---------------------------------------------------------------------------------------------------------------------
# from a run folder (reads files: a worker thread)
# ---------------------------------------------------------------------------------------------------------------------

LOG_TAIL = 2 << 20


def _tail(p: Path, n: int = LOG_TAIL) -> str:
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - n))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _free_gb(drive: str) -> float | None:
    if not re.fullmatch(r"[A-Za-z]:", drive or ""):
        return None
    try:
        return shutil.disk_usage(drive + "\\").free / 1024 ** 3
    except OSError:
        return None


def explain_run(run_dir: str | os.PathLike | None, stderr: str = "", code: int | None = None,
                stop: str | None = None) -> Reason:
    """``explain`` with the run's own files: extras\\match_cuts.log (its end) and -- when ``stderr`` is empty (a run
    read back later) -- extras\\batch_console.log; a full local disk gets its free space now. Never raises."""
    log = ""
    if run_dir:
        extras = Path(run_dir) / "extras"
        log = _tail(extras / "match_cuts.log")
        if not stderr:
            stderr = _tail(extras / "batch_console.log")
    r = explain(stderr, log, code, stop)
    if r.kind == DISK_FULL and r.drive and "free now" not in r.text and not r.text.startswith("the run crashed"):
        free = _free_gb(r.drive)
        if free is not None and r.text.startswith(f"the disk {r.drive} is full"):
            r = replace(r, text=r.text.replace(f"the disk {r.drive} is full",
                                               f"the disk {r.drive} is full ({free:.1f} GB free now)", 1))
    return r
