"""The app's tools, one page each (night 11): Match cuts (its Queue and Learn), Board, Progress (Download, Convert,
Template; night 15: Find source next to Download) and History -- main_window.TABS puts them on the four tabs. To add one: write a class with ``key``,
``title``, ``ready`` and ``page(ctx)`` (a QWidget) in a module here, add it to ``tools()`` and to a tab in TABS. A
page with ``busy()`` / ``stop()`` is asked before the window closes."""
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
    from .history_tool import HistoryTool
    from .learn_tool import LearnTool
    from .match_cuts_tool import MatchCutsTool
    from .template_tool import TemplateTool
    return [MatchCutsTool(), LearnTool(), BoardTool(), DownloadTool(), FindSourceTool(), ConvertTool(), TemplateTool(),
            EnhanceTool(), HistoryTool()]
