"""The app's tools, one page each (night 11): Match cuts (its Queue and Learn) and Progress (Download, Convert,
Template; night 15: Find source next to Download) -- main_window.TABS puts them on the two tabs. 7 Nov (your call): no
Board and no History tab any more -- the Board's page is still made and works out of sight (main_window.HIDDEN: the
automatic "Uploaded" check, Clean up); the History page is not made (history_tool.py stays, unused). To add one: write
a class with ``key``, ``title``, ``ready`` and ``page(ctx)`` (a QWidget) in a module here, add it to ``tools()`` and to
a tab in TABS. A page with ``busy()`` / ``stop()`` is asked before the window closes."""
from __future__ import annotations


class Tool:
    key = ""
    title = ""
    ready = True                 # False: shown as a tab that says SOON

    def page(self, ctx):         # -> QWidget
        raise NotImplementedError


def tools() -> list[Tool]:
    from .board_tool import BoardTool
    from .convert_tool import ConvertTool
    from .download_tool import DownloadTool
    from .enhance_tool import EnhanceTool
    from .find_source_tool import FindSourceTool
    from .learn_tool import LearnTool
    from .match_cuts_tool import MatchCutsTool
    from .template_tool import TemplateTool
    return [MatchCutsTool(), LearnTool(), BoardTool(), DownloadTool(), FindSourceTool(), ConvertTool(), TemplateTool(),
            EnhanceTool()]
