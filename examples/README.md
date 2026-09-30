# Example output — synthetic proof run (full profile)

`synthetic_full/` is the unmodified output of

```bash
python tools/match_cuts/tests/synth.py --profile full --out work/synthetic/full     # ~17 min, cached
python -m match_cuts --competitor work/synthetic/full/competitor.mp4 \
                     --raw work/synthetic/full/raw.mp4 --out examples/synthetic_full
```

on the synthetic test case of prompt Stage 1: a 3-minute 1920×1080 @ 29.97 RAW (every frame unique,
burned-in frame counter) and a 1080×1920 @ 30 competitor built **only with ffmpeg filtergraphs** — rounded
video box, logo/title/watermark, word-by-word captions, music under the original audio and 21 cuts
(same-shot jump cuts, out-of-order hook, a re-used moment, a 1.10× segment, a flipped segment, a slow
push-in, a punch-in, a 6-frame crossfade, a fullscreen segment and a 1 s NOT-IN-RAW insert). The known edit
is *measured* by pushing a frame-index "ID video" through the same filter chains.

Result (see `report.md`, `verify.json`): every competitor frame mapped to exactly the RAW frame the truth
says, cuts ±0 frames, speed 1.10 snapped exactly, flip / push-in keys / punch-in / crossfade (O, D=6) /
fullscreen box / NOT-IN-RAW range recovered, audio lag ≤ 1 ms per segment, all acceptance criteria PASS
(criterion 6 verified with the strict After Effects mock — After Effects is not available on Linux).

What is committed: `cutlist.json/.csv`, `build_ae_project.jsx`, `recreated_edit.xml/.edl`, `report.md`,
`verify.json` and `debug/` (mapping, scores, layout, cut images, decision log). Not committed (large, and
reproducible with the two commands above): `media/`, `preview_recreation.mp4`, `compare.mp4`. The absolute
paths inside the files are from the machine that produced them; the JSX finds its media relative to itself
first and otherwise asks you to locate the RAW.
