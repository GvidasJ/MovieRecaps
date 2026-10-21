# Day 14 (quick): faster video creation

## What changed

1. **Conversion** (commit 653fc99)
   - A RAW whose timestamps only drift slowly (under 0.5 frame, every step one frame) is a constant frame rate now: **no conversion**, the original is used.
     - Ted 2 YTS x264 runs at exactly 23.976 fps. The tool compared it with 23.976024 and called it "VFR, 0.181 frames".
   - A real conversion is made **once**, on the graphics card (H.264 NVENC cq 17, preset p5), and V1 plays it. No second `raw_premiere.mp4`.
     - The CPU is used only when NVENC is missing or fails.
   - Ted 2 **x265 RBG** no longer stops ("could not decode source frames [0, 1] by PTS"):
     - its timestamps are scrambled (PTS = DTS), so it is converted in decoding order and its bogus edit list is ignored;
     - checked frame for frame and sample for sample against the x264 copy of the same film.
2. **Only the needed part of the movie** (commit c8137f8)
   - When the RAW is longer than 15 min and the sound check found the scene, the run analyses a piece:
     - the found places ± 5 min,
     - cut by stream copy from a keyframe,
     - its frames and sound checked against the movie.
   - `1_edit.xml` still plays the **original movie** at the right frames, also at 125 %. The piece is deleted at the end.
   - **Ted 2's piece run gave the same `1_edit.xml` clip for clip** as the whole-movie run.
   - The whole movie is used, as before, when:
     - the places are more than 40 % of the movie (every 21–25 min episode in check-all),
     - the file cannot be cut as it is,
     - the piece misses the competitor (NOT IN RAW, or a missing stretch whose sound is elsewhere in the movie).
   - Learn and the cut score read such runs in the movie's frames. `report.md` says where the piece was.
3. **Open in Premiere imports ONE XML** (commit facf0bb)
   - It imports `1_edit.xml` itself, with its sequence named after the title **without emoji**.
   - No `<title>_edit.xml` copy any more. Old copies of the same edit are removed when the run is opened or renamed. Your `.prproj` files are never touched.

## Time before -> after (Ted 2, 2 h 6 min, x264 5.1, the real competitor)

| | before | after |
|---|---|---|
| laptop, old code | **70.5 min** (S2 conversion 15.8 min + S8 second conversion 42.3 min) | not measured here: about 8 min expected (this PC's times x 1.2–2) |
| this PC, whole movie (fix 1 only) | 9.2 min (549 s) | |
| this PC, piece of the movie (fix 1 + 2) | | **5.4 min (322 s)** |

- **x265 conversion speed** (3-minute piece, this PC):
  - old way: 12.2 s (S2) + 36.2 s (S8)
  - now: 11.2 s, once
  - this PC's CPU is fast; the laptop gains more.
- **Tests:** all pass (tool 1583, app 1131). Check-all cannot change: none of its videos needs a conversion or gets a piece.

## On the laptop

Close MovieRecaps, then in PowerShell:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
git pull
```

No new packages. Start MovieRecaps again.

## Still open

- **Emoji in folder names still make Premiere's XML import fail** ("File Import Failure", reproduced on this PC this morning). Every project since day 13 has them. Plain folders and non-English letters import fine. Say if I should do this next: folder names without emoji, and the existing folders renamed safely.
- **Far-apart places:** these use the whole movie (no multi-piece cut yet).
- **Missing clips:** these are looked for in the rest of the movie by their sound only.
- **Disk:** C: had 33 GB free.
  - `powershell -ExecutionPolicy Bypass -File work\cleanup_day14.ps1` lists about 37 GB of old scratch (`work\t27`, `work\t28` except `t28\ld`, today's `work\t30`).
  - Add `-Delete` to delete it. I could not delete it myself.
