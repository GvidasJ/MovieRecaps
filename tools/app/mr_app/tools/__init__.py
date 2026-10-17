"""The app's tools, one tab each, in the order of the work (night 10): Download -> Convert -> Template -> Match cuts.
To add one: write a class with ``key``, ``title``, ``ready`` and ``page(ctx)`` (a QWidget) in a module here and add it
to ``tools()``. A page with ``busy()`` / ``stop()`` is asked before the window closes."""
from __future__ import annotations


class Tool:
    key = ""
    title = ""
    ready = True                 # False: shown as a tab that says SOON

    def page(self, ctx):         # -> QWidget
        raise NotImplementedError


def tools() -> list[Tool]:
    from .coming_soon import ComingSoon
    from .download_tool import DownloadTool
    from .match_cuts_tool import MatchCutsTool
    return [DownloadTool(),
            ComingSoon("convert", "Convert", "Change a video's format with FFmpeg, with no quality loss "
                                             "(like Shutter Encoder)."),
            MatchCutsTool()]
