# MovieRecaps

**match_cuts** — rebuild a competitor's short-form edit frame-exactly from the raw source video and deliver
it as an After Effects project (plus cut list, FCP7 XML / EDL, preview render, side-by-side comparison and a
verification report). Requirements: [`MATCH_CUTS_PROMPT.md`](MATCH_CUTS_PROMPT.md).

- Tool, setup, usage, troubleshooting: [`tools/match_cuts/README.md`](tools/match_cuts/README.md)
- Design contract: [`tools/match_cuts/DESIGN.md`](tools/match_cuts/DESIGN.md)
- Proof on synthetic data: [`examples/`](examples/README.md)

Quick start (Python ≥ 3.10, ffmpeg ≥ 5.1 recommended):

```bash
python -m venv .venv && . .venv/bin/activate      # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e tools/match_cuts                   # installs the tool and all it needs
pip install --no-deps scenedetect click platformdirs   # optional cut cross-check
# put the two videos in ./input/ (competitor.mp4 = the short edit, raw.mp4 = the source), then:
python -m match_cuts --competitor input/competitor.mp4 --raw input/raw.mp4 --out output
```

Then in After Effects: *File → Scripts → Run Script File…* → `output/build_ae_project.jsx`.

With `--premiere`: import `output/recreated_edit.xml` and `output/captions.srt` into Premiere, upgrade the
captions to graphics, save, then style them all at once with
`python -m match_cuts restyle "<project>.prproj"` (writes `<project>_styled.prproj`; see
[Restyle the captions](tools/match_cuts/README.md#restyle-the-captions-in-premiere-restyle)).

While it runs, the console prints a line at least every 30 seconds. At the end, read `output/report.md`:
the first table says which checks passed. Amber `UNCERTAIN` solids and `MISSING - not in RAW` solids in the AE
project mark the frames you still have to fill by hand (see *What the layers mean* in the
[tool README](tools/match_cuts/README.md#running-the-result-in-after-effects)). Windows: use PowerShell, and close
`cutlist.csv` (Excel) and the preview video before you run the tool again. More help:
[Troubleshooting](tools/match_cuts/README.md#troubleshooting).
