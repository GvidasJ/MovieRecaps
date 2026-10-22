"""The stages' names in plain words (night 15, task 4): what the app's queue row, its console and the report show
instead of "S2 probe+conform" or "S5.3 refine" (the S-number stays, small, next to it). No stage imports this
module, so no cache key depends on it."""
from __future__ import annotations

import re

# the tool's stage name (as logged: '== S5.2 visual search') -> (plain name, the short form for the console)
PLAIN: dict[str, tuple[str, str]] = {
    "S0 env": ("Getting ready", "Getting ready"),
    "S1 pair check": ("Checking the RAW and the competitor are the same scene", "Checking the pair"),
    "S1.5 piece of the movie": ("Cutting out the part of the movie the sound found", "Cutting out the movie's part"),
    "S2 probe+conform": ("Preparing the RAW (checking / converting)", "Preparing the RAW"),
    "S3 audio": ("Reading the sound of both videos", "Reading the sound"),
    "S3 proxies": ("Making small copies to search in", "Making small copies"),
    "S4 layout": ("Finding where the picture sits in the competitor", "Finding the picture area"),
    "S5.1 audio align": ("Matching the sound to the RAW", "Matching the sound"),
    "S5.2 visual search": ("Finding each competitor shot in the RAW", "Finding shots"),
    "S5.2 visual search (refined box)": ("Finding each competitor shot in the RAW (second look)", "Finding shots"),
    "S5.3 refine": ("Placing every frame exactly", "Placing every frame"),
    "S5.3 refine (refined box)": ("Placing every frame exactly (second look)", "Placing every frame"),
    "S5.4-S6 segments+cutlist": ("Deciding the cuts", "Deciding the cuts"),
    "S5.5 full-res re-check": ("Checking unsure frames at full size", "Checking unsure frames"),
    "S6 --fast comparison": ("Comparing with a slower, thorough search", "Comparing with a thorough search"),
    "S6 no-broll": ("Checking for shots that are not in the RAW", "Checking shots not in the RAW"),
    "S7 AE project": ("After Effects project (skipped for Premiere)", "After Effects project"),
    "S8 exports": ("Making the Premiere files", "Making the Premiere files"),
    "S8 Premiere XML": ("Making the Premiere files", "Making the Premiere files"),
    "S8 EDL": ("Making the Premiere files", "Making the Premiere files"),
    "S8.preview": ("Rendering a check video", "Rendering a check video"),
    "S8 framed preview": ("Rendering a check video", "Rendering a check video"),
    "S8.compare": ("Rendering a side-by-side video", "Rendering a side-by-side video"),
    "S8 captions": ("Writing the captions", "Writing the captions"),
    "S8 speed + frame": ("Putting on the speed and the frame", "Speed and frame"),
    "S8 validate exports": ("Checking the Premiere files", "Checking the files"),
    "S8 match-geometry render context": ("Making the Premiere files", "Making the Premiere files"),
    "S9 verify": ("Double-checking every cut against the competitor", "Double-checking cuts"),
    "S10 report": ("Writing the report", "Writing the report"),
}
# a stage not named above: by its number
BY_NUMBER: dict[str, tuple[str, str]] = {
    "S0": PLAIN["S0 env"], "S1": PLAIN["S1 pair check"], "S2": PLAIN["S2 probe+conform"],
    "S3": ("Reading the videos", "Reading the videos"), "S4": PLAIN["S4 layout"],
    "S5": ("Finding each competitor shot in the RAW", "Finding shots"), "S6": PLAIN["S5.4-S6 segments+cutlist"],
    "S7": PLAIN["S7 AE project"], "S8": PLAIN["S8 exports"], "S9": PLAIN["S9 verify"], "S10": PLAIN["S10 report"],
}
_CODE = re.compile(r"^\s*(S\d+(?:\.\d+)?(?:-S[\d.]+)?)")


def code_of(stage: str) -> str:
    """'S5.2 visual search' -> 'S5.2' ('' when it has no S-number)."""
    m = _CODE.match(stage or "")
    return m.group(1) if m else ""


def _find(stage: str) -> tuple[str, str] | None:
    stage = (stage or "").strip()
    if stage in PLAIN:
        return PLAIN[stage]
    for name in sorted(PLAIN, key=len, reverse=True):          # 'S5.2 visual search (refined box) ...'
        if stage.startswith(name):
            return PLAIN[name]
    code = code_of(stage)
    num = re.match(r"S\d+", code)
    return BY_NUMBER.get(num.group(0)) if num else None


def plain(stage: str) -> str:
    """The stage in plain words ('Finding each competitor shot in the RAW'); the tool's own name when unknown."""
    found = _find(stage)
    return found[0] if found else (stage or "").strip()


def short(stage: str) -> str:
    """The short plain form, for the console ('Finding shots')."""
    found = _find(stage)
    return found[1] if found else (stage or "").strip()
