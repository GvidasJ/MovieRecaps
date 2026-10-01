# Example output — synthetic proof runs

Two unmodified outputs of the current tool on the synthetic test cases (`tools/match_cuts/tests/synth.py`):

| folder | test case | what it proves |
|---|---|---|
| [`synthetic_full/`](synthetic_full/) | profile `full`: the prompt's Stage 1 test | a "clean" edit is rebuilt exactly — every check passes |
| [`synthetic_film24/`](synthetic_film24/) | profile `film24`: the regimes of the first real run | how the tool behaves on hard material: what it rebuilds exactly and what it reports honestly as not decidable |

Each folder was made with

```bash
python tools/match_cuts/tests/synth.py --profile <full|film24> --out work/synthetic/<profile>    # cached
python -m match_cuts --competitor work/synthetic/<profile>/competitor.mp4 \
                     --raw work/synthetic/<profile>/raw.mp4 --out examples/synthetic_<profile> --work <dir>
```

What is committed: `cutlist.json/.csv`, `build_ae_project.jsx`, `recreated_edit.xml/.edl`, `report.md`,
`verify.json` and `debug/` (mapping, scores, layout, cut images, decision log). Not committed (large, and
reproducible with the two commands above): `media/`, `preview_recreation.mp4`, `compare.mp4`,
`debug/low_confidence/`, `debug/verify_failures/`. The absolute paths inside the files are from the machine and
folder that produced them; the JSX finds its media relative to itself first and otherwise asks you to locate the
RAW.

## synthetic_full

A 3-minute 1920×1080 @ 29.97 RAW (every frame unique, burned-in frame counter) and a 1080×1920 @ 30 competitor
built **only with ffmpeg filtergraphs** — rounded video box, logo/title/watermark, word-by-word captions, music
under the original audio and 21 cuts (same-shot jump cuts, out-of-order hook, a re-used moment, a 1.10×
segment, a flipped segment, a slow push-in, a punch-in, a 6-frame crossfade, a fullscreen segment and a 1 s
NOT-IN-RAW insert). The known edit is *measured* by pushing a frame-index "ID video" through the same filter
chains.

Result (see `report.md`, `verify.json`): **every acceptance criterion PASS**. 22 segments (1
NOT-IN-RAW placeholder) cover all 1571 frames; the 21 cuts are verified on both sides; After Effects' frame rule
shows the measured RAW frame on 1535 of 1535 matched frames (picture ZNCC ≥ 0.990 everywhere); speed 1.10 snapped
exactly, flip, push-in keys, punch-in, crossfade (O, D=6), fullscreen box and NOT-IN-RAW range recovered; audio
within 0.77 ms per segment (the NOT-IN-RAW insert is the one explained exception); the JSX passes the strict After
Effects mock (After Effects is not available on Linux).

## synthetic_film24

A 23.976 fps RAW (1608 frames, 960×540) in a 30 fps 540×960 competitor (532 frames, 17.7 s) with what the first
real run met: the 23.976 → 30 pulldown, editor pans over moving RAW shots (parallax, an accelerating pan), a
RAW-native zoom + roll under a fixed framing, one time line across two RAW shot changes (one of them dark), a
framing snap-back at a RAW cut, a punch-in followed by a pan, two pan clips on one RAW line (the RAW shot carries a
legal disclaimer the competitor does not show), 3–5 frame flash chains, frame-blend 0.25× slow motion, a true
freeze under an animated caption, a foreign shot that only looks like the RAW, a sharpened gray-graded chain, a
genuine L-cut, and a competitor soundtrack 86 ms late against its picture.

Result (see `report.md`, `verify.json`): c1, c2, c4, c5 and c6 pass, **c3 fails** — on purpose,
with the evidence listed:

- *Exact where it can be*: After Effects' frame rule shows the measured RAW frame on 478 of 478 matched frames
  (30 of them on the dominant frame of a verified Frame Mix slow motion), and every matched frame is the RAW frame
  of the synthetic truth (`tests/test_synthetic.py` with `MATCH_CUTS_PROFILE=film24`); the −85.4 ms A/V offset is
  measured and confirmed (c5 explains it once, plus five pieces shorter than 0.5 s).
- *Reported, not hidden*: 54 frames in two **UNCERTAIN** segments — the foreign lookalike (372–395, best RAW ZNCC
  0.66–0.70) and the sharpened gray chain (466–495, ZNCC 0.80–0.83): neither a match nor NOT-IN-RAW, so they
  count against c3 and appear in AE as amber solids with a guide layer. 39 matched frames (427–465) score below
  ZNCC 0.9 because the RAW shot carries the legal disclaimer the competitor does not show. The temporal signature
  flags the true freeze at 415–425 (a held RAW frame under an animated caption; also the one c2 exception) and one
  jump at frame pair 122.
