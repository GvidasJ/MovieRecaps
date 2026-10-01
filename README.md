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
