"""The app's tools, one tab each. To add one (the video downloader, the converter): write a class with ``key``,
``title``, ``ready`` and ``page(ctx)`` (a QWidget) in a module here and add it to ``tools()``."""
from __future__ import annotations


class Tool:
    key = ""
    title = ""
    ready = True                 # False: shown as a tab that says SOON

    def page(self, ctx):         # -> QWidget
        raise NotImplementedError


def tools() -> list[Tool]:
    from .coming_soon import ComingSoon
    from .match_cuts_tool import MatchCutsTool
    return [MatchCutsTool(),
            ComingSoon("download", "Download", "Download a video (yt-dlp): the best picture and sound, one click."),
            ComingSoon("convert", "Convert", "Change a video's format with FFmpeg, with no quality loss "
                                             "(like Shutter Encoder).")]
