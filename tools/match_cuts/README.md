# match_cuts

Rebuild a competitor's short-form edit **frame-exactly from its RAW source** and deliver it as an
After Effects project (`build_ae_project.jsx` → `recreated_edit.aep`), plus a cut list, FCP7 XML, a
CMX3600 EDL, a frame-exact preview render, a side-by-side comparison and a verification report.

Nothing is guessed: every cut and source timestamp comes from audio + visual measurement, carries a
confidence, and is checked by Stage 9 verification. Whatever cannot be determined is flagged (for
example a `NOT-IN-RAW` placeholder, an *uncertain* segment or an *ambiguous-identical* frame).

The requirements live in [`MATCH_CUTS_PROMPT.md`](../../MATCH_CUTS_PROMPT.md); the module contract
is [`DESIGN.md`](DESIGN.md).

---------------------------------------------------------------------------------------------------

## Setup

Requirements: Python ≥ 3.10, **ffmpeg/ffprobe ≥ 5.1** on `PATH` (or set `FFMPEG` / `FFPROBE`).
Optional: **Node.js** (runs the generated `.jsx` in a strict ExtendScript/After Effects mock for
criterion 6), **After Effects CC 2019+** (runs the `.jsx` and renders with `aerender`).

```bash
python -m venv .venv
. .venv/bin/activate                     # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e tools/match_cuts          # the tool + everything it needs (numpy, scipy, OpenCV, PyAV, ...)
# optional extras:
pip install --no-deps scenedetect click platformdirs   # PySceneDetect cross-check of the cuts
pip install -e "tools/match_cuts[exports]"             # OpenTimelineIO re-parse check of XML/EDL
pip install -e "tools/match_cuts[dev]"                 # pytest, to run the tests
# captions (2_captions.srt): transcription + OCR, pip only (no system installs, also on Windows)
pip install -e "tools/match_cuts[captions]"            # faster-whisper + the OCR's own dependencies
pip install --no-deps rapidocr                          # RapidOCR without its opencv-python dependency
# captions on an NVIDIA GPU: the CUDA 12 libraries faster-whisper (CTranslate2) loads, and a CUDA build of
# PyTorch for the forced alignment (an RTX 50 series card needs CUDA 12.8 or newer: cu130 here)
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu130
```

Check the install with `python -c "import numpy, cv2, av; print('ok')"`. Python 3.11 or 3.12 is the
safest choice: brand-new Python releases sometimes lack prebuilt wheels for a dependency, and pip then
tries to compile it (on Windows that fails without Visual C++ build tools). The optional extras are not
needed to run the tool (OpenTimelineIO in particular may need a compiler); without them the run skips
those extra checks and says so.

Install exactly one OpenCV wheel (`opencv-contrib-python-headless` or `opencv-contrib-python`, never
together with `opencv-python`) — this is why PySceneDetect is installed with `--no-deps`. PySceneDetect
must use `backend='opencv'` (its PyAV backend crashes with PyAV ≥ 19).

Installing ffmpeg: Linux `apt install ffmpeg`, macOS `brew install ffmpeg`, Windows
`winget install Gyan.FFmpeg`.

Captions: RapidOCR is installed with `--no-deps` for the same OpenCV reason (its metadata asks for
`opencv-python`; it works with the contrib wheel above). faster-whisper downloads its models (large-v3, ~3 GB, and
large-v3-turbo, ~1.6 GB) from Hugging Face on the first run and caches them (`HF_HOME`); the aligner (~1.2 GB) comes
the same way (`TORCH_HOME`). Without a GPU everything runs on the CPU, more slowly. Without these packages the run
still completes and the report says why `2_captions.srt` is missing.

## Usage

```bash
python -m match_cuts --competitor X --raw Y --out Z [--layout match|fill|source] \
                     [--comp-size WxH|competitor] [--fps competitor|source] [--work DIR] \
                     [--workers N] [--force-conform] [--ae-time-mode auto|stretch|remap|frames]
                     [--audio-sync raw|competitor] [-v]
```

| flag | default | meaning |
|---|---|---|
| `--competitor X` | `./input/competitor.mp4` | the finished edit to rebuild |
| `--raw Y` | `./input/raw.mp4` | the source video it was cut from |
| `--out Z` | `./output` | deliverables folder |
| `--layout` | `match` | `match`: recreate the competitor's canvas, video box (position, size, rounded corners), background and per-shot crop/zoom. `fill`: full-screen 9:16 keeping the per-shot framing. `source`: cuts only, RAW size and RAW fps, no reframing |
| `--comp-size` | `competitor` | AE comp size; `competitor` = same pixels as the competitor, or e.g. `1080x1920`. In `match` mode it must keep the competitor's aspect (rejected otherwise) |
| `--fps` | `competitor` | `competitor`: the competitor's exact frame rate (exact cut timing). `source`: RAW frame rate; cuts are rounded to the nearest MAIN frame and the max error is reported (`cutlist.settings.fps_source_max_error_s`). On a different MAIN grid criterion 3 accepts, per MAIN frame, any RAW frame between the competitor frames bracketing that time; cut timing is exact only with `competitor` |
| `--work DIR` | `./work` | caches and intermediate files |
| `--workers N` | `0` | worker processes (0 = all CPUs) |
| `--force-conform` | off | transcode RAW to an AE-safe copy even if it is already AE-safe |
| `--ae-time-mode` | `auto` | how AE layers are timed: `stretch` (Time Stretch + Start Time), `remap` (time remapping), `frames` (one HOLD time-remap key per frame; immune to AE's time rounding), `auto` (stretch, with a per-layer switch to `frames` when the JSX's read-back self-check finds a mismatch) |
| `--audio-sync` | `raw` | which audio timing the export uses when the competitor's sound is shifted against its picture (see *A/V offset* below): `raw` keeps the RAW's own lip-sync; `competitor` copies the competitor's shift exactly |
| `-v` | off | debug logging on the console |

Extra flags: `--fast` (a quick run: see *Thorough by default* below), `--compare-fast` (a thorough run also says what it changed against `--fast`), `--check-determinism` (check 9.7 also re-runs the cut decisions from the caches and compares the cut list byte for byte -- as long as the segments stage again; check-all always), `--input-dir DIR` (auto-detection folder, default `./input`), `--seed N`,
`--skip-preview`, `--skip-compare`, `--no-swap`, `--no-ae`, `--ae-timeout SECONDS`, `--version`.

Captions (see *Captions* below): `--captions auto|competitor|voice` (default `auto`), `--voiceover FILE`,
`--caption-model NAME` (default `large-v3`), `--caption-check-model NAME` (default `large-v3-turbo`, `none` = off),
`--caption-recheck-model NAME` (default `large-v3`, `none` = off).
Premiere-only export: `--premiere`. Cuts never inside speech, silence removal (Premiere export and RAW-only runs):
`--pad-before S` (default 0.03: a clip starts this long before its first word; 0.05 until video017/018 -- your starts
sit right on the sound's onset), `--pad-after S` (default 0.05: a clip
ends this long after its last word; 0.15 until Task 8 -- at the cuts your finished videos and the tool both make you
leave 0.09-0.12 s earlier than that), `--keep-silence`, `--min-silence S` (default 0.3), `--silence-db DB` (default: set
per video from its speech level and background noise; DB under the speech level overrides it). Repeats of RAW footage
or audio (see *Premiere* below): `--allow-repeats` keeps a moment over 0.5 s that plays twice. No `--competitor`: the
edit from the RAW alone (see *Without a competitor* below).

`--no-broll`: where the competitor cuts away (B-roll from your RAW or not in it) while the RAW audio keeps
playing, the export shows the RAW video that matches the audio instead, so the main clip plays through (see
*B-roll cutaways* below).

### Thorough by default (`--fast` for quick runs)

A run matches the cuts as thoroughly as this PC allows: the GPU where it makes the matching faster or more exact, one
worker process per physical CPU core everywhere else (a second process on a core's other hardware thread makes the
matching slower, not faster: the same 120 searches take 32-34 s with 12-15 workers, 47-50 s with 16-30, on a
15-core / 30-thread CPU). `--fast` is the quick run.

| | the default (thorough) | `--fast` |
|---|---|---|
| RAW index | every RAW frame (SIFT, 500 features each, up to 12 M in all), searched **exactly** on the GPU (the FLANN kd-trees on the CPU find the true nearest neighbour only ~60 % of the time): the index in float16 on the tensor cores with float32 sums (exact for SIFT's whole numbers), the 24 nearest found in two stages -- each block's distances in groups of 64, the 24 groups with the smallest minimum, their members ranked -- the same neighbours as ranking every column, about 10 times faster | 10 frames a second (3 for a RAW over 10 min), 2 M features in all (searched on the GPU too when there is one) |
| competitor frames searched | every one | every 3rd (refine fills in the rest) |
| slightly uncertain frames | re-checked at full resolution on the GPU before the cuts are decided (in `full_res_workers` = 4 processes sharing the GPU, each frame's numbers the same as in one): refine's low-margin / confounded / tied frames and the 2 frames on each side of every RAW jump (the first frames after a jump cut in a fast pan are blurred on the proxy); each candidate RAW frame with its own framing refined at full size, following a score still rising past the candidates' edge. The re-check narrows which RAW frames a frame may show, and decides against the proxy only when full resolution is clearly sure (better by more than 0.01 ZNCC, at least 0.9) | -- |
| where a cut goes (criterion 2) | at full resolution: two RAW frames on the two sides -- which one the competitor shows, each with its framing refined (in a fast pan both models' framings are off at a cut); one RAW frame -- its framing as delivered | on the proxy |
| each clip's framing (keys) | every matched frame's framing measured at full resolution (sub-pixel, the transition frames of a fast pan included), the keys fitted to those | refine's per-frame measurements on the proxy |
| a clip's phase (which competitor frames show a repeated RAW frame) | where the proxy leaves a choice (a tie, or a range of phases showing other frames), each frame's candidates scored at full resolution, their framing refined, and the phase solved again with those scores; kept when it fits better by 0.01 in all | the proxy's measurements |
| verification | also every frame and every cut at full resolution (9.9): each frame as delivered, the framing error the refinement finds, a neighbouring RAW frame that fits better (a failure beyond 0.01, unless it is the repeat cadence one constant-speed clip cannot follow: the competitor shows one picture on two frames where the time line steps, or the time line shows one picture on two frames where the competitor moves on -- one RAW frame twice, or two RAW frames the RAW file itself repeats, as a 30 fps file of 25 fps footage does; a repeat is measured at full resolution where refine did not, and counts only between two real changes), and each cut (a failure when a frame next to it fits the other side's model better by 0.01, unless the competitor repeats a picture across the cut) | the proxy checks |
| speech-safe cuts (speech map) | large-v3 | large-v3-turbo |
| framing check (who speaks) | YuNet faces and Light-ASD on every RAW frame the edit plays, at 25 fps (Light-ASD's own rate), faces found at 960 px wide (full width was tried on tests/real/zendaya: the same person found speaking on 688 of 699 frames -- the other 11 an overlap where both speak -- and only two more tracks, a 34 px background face and an 11-frame fragment, for 4x the pixels to search) | the same |
| end summary | `Run time` and what the full-resolution pass changed; with `--compare-fast` (check-all always) also `Against --fast`: what the thorough analysis changed against the one a --fast run makes of the same video (made from the same caches, a few minutes more) and how long each takes | `Run time` |

Without a CUDA GPU the default samples the RAW like `--fast` (an every-frame index needs the exact GPU search), skips
the full-resolution pass, and says so.

The full-resolution steps run in `full_res_workers` = 4 processes sharing the GPU. Before they start, the run gives
back the GPU memory it no longer needs (the speech models, loaded again when needed), and each process keeps at most
512 MB of RAW frames and 256 MB of competitor frames on the GPU (`fullres.RAW_CACHE_BYTES`, `COMP_CACHE_BYTES`), so a
long 1080p RAW fits a 16 GB card. When the GPU's memory still runs out (another program using it, an Adobe render),
Windows lends it some of the PC's memory: much slower, the same numbers.

Measured on an RTX 5080 with a 16-core CPU (Task 9), on the finished videos at full size, from empty caches, one run at a time on a free GPU: video1 (a 23.5-minute RAW at 700x480, 3,384 competitor frames) takes 70 minutes thorough and 26 with `--fast`; video2 (24.4 minutes, 281 frames) 15 and 5; video3 (24.2 minutes, 1,249 frames) 14 and 6; video4 (12.7 minutes at 1280x720 and 59.94 fps, 1,078 frames) 47 and 19. The search grows with the RAW's length (every RAW frame is indexed) and with the competitor's frames; refine and the full-resolution steps grow with the competitor's frames and the RAW's resolution.

### Premiere (`--premiere`)

`1_edit.xml` is a 1080×1920 sequence at exactly 60.00 fps with the edit on V1, the RAW audio on A1 and
V2 and above empty. (A competitor whose frame rate does not divide 60 gets the whole multiple of its rate nearest to
it, so every cut lands on one of its frames: a 24 fps competitor a 48 fps sequence, 25 fps a 50 fps one, and an NTSC
competitor the NTSC version of that -- 29.97 fps a 59.94 fps sequence, 23.976 fps 47.952; the end summary says
so.) A speed change of at most 0.25 s inside one continuous take of the competitor's -- a slow-motion or hold
sliver, a frame-rate artifact rather than an edit -- plays at 100 % here, so the take runs on as one and its
sound is never slowed inside a word; so does a picture glitch of at most 0.25 s inside one take, its picture at
most 0.1 s off the take (it plays on the take's line, picture and sound) -- unless the competitor's sound there
follows a line of its own, off the take's: then it stays as the competitor has it. `cutlist.json` keeps both as the
competitor has them. Every clip on V1 and A1 is simply `raw.mp4`: the same name, the same file and one shared master
clip, so Premiere's Project panel shows a single `raw.mp4` all the timeline clips are cut from (the RAW is always
copied as `extras/media/raw.mp4`, whatever the input file is called; the competitor is never in the project). The
segment ids (`S01+S02`) are in each clip's comments and in the markers.

**Linked clips.** Every V1 clip imports linked to its own A1 clip (the `<link>`s Premiere writes in its own XML), so
moving, trimming or cutting a clip takes its audio with it. Each V1 clip is linked to the A1 clip it overlaps most;
audio shifted a few frames from its picture (an A1 cut moved to close a jump) stays linked to that picture. Where one
take of audio runs under several V1 clips (the picture changes framing or repeats frames while the audio plays on),
A1 is split at those V1 cuts into seamless pieces — the source runs on, nothing is heard — one for each clip; where
an A1 cut falls inside a V1 clip, that clip is split there the same way. Only a clip with no audio under it (a
freeze, muted B-roll: silent on purpose), an A1 clip under an empty V1 and another video's stretch (`OTHER VIDEO`)
stay unlinked. The hard check `XML LINK` fails the run unless every V1 clip with audio under it is linked to exactly
one A1 clip and every A1 clip under a picture to exactly one V1 clip, the same pair in both, overlapping. The end
summary says how many are linked (*Linked clips: 14 of 14 V1 clips linked to their own A1 clip*) and lists every clip
left unlinked on purpose; `report.md` section *Premiere XML checks* has one row per hard check on `1_edit.xml` (`XML
ITEM`, `GAP`, `REPEAT`, `SPEECH`, `FLASH`, `SILENCE`, `OTHER VIDEO`, `LINK`) with its result. In Premiere, with
*Linked Selection* on (the chain button at the top left of the timeline), clicking a V1 clip selects its audio too.

Two defaults of this mode (config `premiere_static_framing` / `premiere_follow_audio`):

* **No camera movement.** Every clip holds one fixed Position and Scale — no keyframes on Position, Scale or
  Rotation, rotation 0. It is the competitor's framing for that clip (averaged over the clip when the competitor
  pans or zooms), scaled up only as much as needed and moved the least so it fully covers the template window
  (x 42–1039, y 555–1591).
* **Fewer reframes and cuts (`--min-move`, default 250).** After the fixed framing, a clip takes its own framing
  only when it is 250 px or more from the framing on screen, measured in the 1080×1920 sequence as the biggest
  movement of the picture's centre or of one of its edges (so a zoom counts by how far the edges move). Below
  that it keeps the previous clip's framing exactly — across real cuts too — changed only as little as needed if it
  would leave part of the window uncovered. Across a shot change of the RAW the framing is held only when the new
  shot's own framing is alike (under 250 px away, within 5 % zoom: `SHOT_HOLD_ZOOM`) and the framing on screen
  still shows the person of the clip before -- your habit: one framing for alike shots (video018's wide shots,
  zendaya-age, video1-3); otherwise the new shot chooses its framing fresh (a close-up after a wide shot). A
  framing that would not show the clip's person speaking is never held (below). Neighbouring pieces of one continuous RAW take (the next starts on the
  very source frame the previous ends on, same speed, no transition) that end up with the same framing become one
  clip, with no cut on V1 or A1; a jump in RAW time stays a cut. Each clip's comment says when its framing was
  kept from an earlier clip and which pieces it joins. `--min-move 0` gives every piece its own framing.
* **The person speaking is always in the picture** (`match_cuts/people.py`, `speakers.py`). Where the edit plays the
  RAW (+- 3 s), every 1/25 s: faces found by YuNet (OpenCV's FaceDetectorYN, a modern CNN detector, replacing the Haar
  cascades), tracked from frame to frame (never across a RAW shot change; a small face that never speaks -- a poster,
  a photo -- is no person), and who speaks found by active-speaker detection: Light-ASD (CVPR 2023) scores how well
  each face's mouth moves with the sound, on the GPU (PyTorch CUDA; the CPU without one). Each clip's speaker is the
  face that is the top speaking face on most of its speech frames (the RAW's speech map); unclear -- nobody's mouth
  goes with the sound, e.g. a voice off camera -- the biggest face. While someone speaks, that person's face (the
  10th-90th percentile of its edges over the speech) must be fully inside the window x 42-1039, y 555-1591; when
  nobody speaks, at least one person must. A clip that fails keeps its zoom and is moved sideways to centre that
  person, still covering the window (up or down only if the face is cut off there) -- one position for a stretch of
  clips sharing a framing when one shows all their people, else each clip its own. This beats the competitor's
  framing and `--min-move`. The same move frames a stretch the competitor's framing cannot be used for at all (a
  replaced B-roll / NOT-IN-RAW / uncertain spot -- not a 1-2 frame cutaway the clip's own take plays on through,
  which keeps the take's framing -- or a framing that leaves the window uncovered); without the people
  analysis (no PyTorch, an error: said in the summary) the main face is centred there (`faces.main_face_x`, YuNet).
  The hard check `XML PERSON` re-reads every clip's framing from the final XML and fails the run when a clip does not
  show its person; a clip with nobody in the picture and another video's stretch are listed, not failed. The end
  summary lists every re-framed clip with its time in the edit (*The person speaking in the picture*), and each
  clip's comment says why. On the Zendaya interview 7 of 10 clips are re-framed (the competitor showed the listener's
  reaction); on Deadpool and Spider-Man school the competitor's framing already shows the speaker everywhere.
* **Every clip covers the window — checked on the final XML.** Premiere reads a clip's Motion `<center>` in units
  of the *source* frame (1920×1080 for the RAW), not the sequence: Position = sequence centre + center × source
  size. (Writing it in sequence units put S21 at Position 1735.8 instead of 1212.6, its left edge at x 431.) After
  writing, every clip's picture edges are computed from the XML's own Scale / Rotation / Center values and the
  source size in it; a clip that leaves any of x 42–1039, y 555–1591 uncovered fails the run (`XML GAP` in the
  report and the console).
* **Every item imports — checked on the final XML.** Every V1 and A1 item must have whole-frame start / end / in /
  out, start < end and in < out, out − in equal to its length at its speed, in / out inside its media's length, and
  no overlap with the items next to it; two audio clips never play at the same moment. Any item that breaks this
  fails the run (`XML ITEM` in the report and the console) — Premiere would skip it ("invalid start/end"). A
  reversed clip is written the way Premiere reads it: the source range ascending (in < out) with the Time Remap
  `reverse` flag (an earlier version wrote in > out, and Premiere dropped S10's audio).
* **Every V1 clip has its audio on A1**, unless it was removed on purpose — a freeze (a frozen picture plays no
  audio) or a cutaway the competitor showed over music / voice-over (picture only). Those are listed in the end
  summary (*V1 clips without their audio on A1*); any other V1 clip without audio fails the run.
* **No RAW footage or audio plays twice** (`match_cuts/repeats.py`). A stutter at a cut — the end of one clip and
  the start of the next showing the same RAW frames or playing the same RAW audio, up to 0.5 s — is always
  trimmed so nothing plays twice: from the start of the next clip when the repeat is there, else from the end of the
  clip before (a clip that only repeats goes). The same RAW moment over 0.5 s twice anywhere loses one copy: the one
  out of chronological order compared with the rest of the edit (a hook at the start), else the later one;
  `--allow-repeats` keeps those. The removed ranges are cut out like silences: the sequence closes up, A1 fades
  over the cut, markers and captions move with it, and two sides left playing one continuous take become one clip.
  The end summary lists every removed repeat with both times (*Repeats*); a repeat left in the final XML that is
  not allowed fails the run (`XML REPEAT`). A freeze placed at 100 % (marked RETIME, to redo by hand) is not
  counted.
* **No flash frames** (`match_cuts/shots.py`). The RAW is often edited itself: its own shot changes are found where
  the edit plays it (thumbnails of every frame; a change where the picture jumps far more than it moves around
  it). The padding of a clip stops at a shot change, and no clip starts or ends with a piece of a shot shorter than
  0.25 s: a sliver with no speech in it is cut away, one the speech runs into is shown 0.25 s; a silence cut keeps
  no such sliver either (nor a sliver of a clip's framing, nor a frame of an empty V1). The check on the final XML
  fails the run on any run of frames of one RAW shot under 0.25 s at a cut (`XML FLASH`). The Zendaya run's frame at
  00:00:06:54 (S11 ran one frame into the RAW's next shot at 3.76 s) is caught by it and no longer made.
* **B-roll follows the audio.** Every NOT-IN-RAW, B-roll or uncertain spot (and dip) shows the RAW video of the
  audio playing there, so you see the person saying it: the neighbouring shot's time line when the audio simply
  continues, else the RAW moment the audio alignment found for it — split at every audio cut when the editor
  trimmed pauses under the cutaway — each checked by correlation. Where the audio there is not from the RAW
  (music, voice-over) the previous RAW clip keeps playing, with no RAW audio under it. V1 is left empty only for
  another video (below); a `B-ROLL REPLACED` marker sits on every replaced spot and report.md lists them with
  timecodes. A RAW shot whose picture is within 1 s of its own audio (an A/V shift) is the main clip and stays as it is.
* **Another video (rare).** Sometimes the competitor uses footage of a second video you did not give (an interview
  clip, say). A NOT-IN-RAW stretch with no RAW audio under it whose competitor audio has **speech** (two or more
  words Whisper hears surely: music or a word or two misheard in noise is not speech) comes from that other video —
  it is not B-roll. V1 **and** A1 stay empty for exactly its length, in its place, with an
  `OTHER VIDEO – not in RAW (start–end)` marker (the edit's timecodes; the comment gives the competitor's and what it
  says). The previous clip never plays over it and no silence is removed inside it (it counts as sound: the pads
  around it are kept). Its captions are the competitor's own when it has them, else transcribed from the
  competitor's audio there, and timed to that audio (my edit's own audio is transcribed with the stretch taken out).
  The checks allow the stretch (`XML OTHER VIDEO` only fails a stretch cut shorter or played over) and list it: the end
  summary (*Other video (not in RAW), left empty on purpose*), the audio check (*V1 clips without their audio on A1*),
  the link check (*not linked on purpose*), the person check (*not checked*) and `report.md`'s *Left empty on
  purpose* (the gap, flash and silence checks). The competitor's audio is transcribed once, whole, to
  look for that speech (a short cut-out piece can make Whisper miss speech or invent some). The Zendaya clip is such
  a case: its 5.5 s "I can't really explain it. I haven't got the words." (competitor 00:00:05:01–00:00:10:16).

### Cuts never inside speech (every Premiere export and RAW-only run)

A cut must never interrupt speech, whatever the competitor did (`match_cuts/speech.py`). Before the silences are cut,
every audio cut of the edit is placed by the speech of the RAW, not by the competitor:

* **Speech map of the RAW.** A sound is the 50 ms loudness at or above this video's silence threshold, with its soft
  start and end (the windows next to it still 3 dB over the background, at most 0.2 s: the soft "s" or "-ty five"
  a word starts or ends with). The words come from the RAW where the edit plays it (±3 s), transcribed with the
  recheck model (`--caption-recheck-model`, default `large-v3`, its words timed by forced alignment), and the
  captions' model as a second opinion when it is another one. A dip in the loudness inside a word (the closure of a "t")
  is part of the word; the dip nearest each boundary between two words (within 0.25 s — the timings are often that
  far off) is the gap between them, however short. A sound either transcript heard a word in, or with a clear pitch
  for 0.05 s, is speech — "okay", "uh" and every other filler included; so is a sound starting at most 0.08 s after
  a word with a pitch for 0.02 s: that word's end, its timing cut short (the "-kay" of "OK?"), one sound with it. A
  sound with neither (a breath, a lip smack) is not speech, so a clip's start or end may leave it out, but no cut
  lands inside it either. Without faster-whisper every sound counts as speech.
* **A clip ends `--pad-after` (0.05 s) after its last word has completely finished and starts `--pad-before`
  (0.03 s) before its first** — inside the quiet there: a pause shorter than both pads is split between them, a
  breath right after the word stops the clip at the breath. A competitor cut that falls inside speech moves to the
  nearer end of that sound: the clip plays on to the end of it (it is extended, and everything after it moves
  later) or stops before it. A clip never shows again what the clip before it now shows: it starts after it, and a
  clip left with nothing new to play goes. Two pieces that end up playing one continuous take become one clip, with
  no cut. Both sides of a cross dissolve stay as they are -- except a dissolve of at most 0.1 s whose cut is inside
  speech: it becomes a hard cut, moved out of the word like any other (33 ms of two pictures mixed are not
  missed; `cutlist.json` keeps the dissolve). Where A1 jumps a few frames (at most 0.1 s) inside
  speech but the picture does not cut (an audio line), the audio line moves by those frames so A1 plays on.
* **A tiny jump at a cut inside speech plays on.** The competitor often cuts 1–3 frames out of a sentence (or shows
  them twice): a cut skipping or repeating at most 0.1 s of the RAW inside speech is closed — the clip before plays
  on to where the next one starts, or the next starts where the one before ends — so the sound never jumps; the
  picture still cuts there (a framing change). An audio line (sound that is not the picture's own) moves by the jump
  instead, along its whole chain, so the picture is not touched.
* **A sliver the clip before plays through goes.** When a clip's start has to move past its own frames (a frame or
  two repeating the end of the clip before, inside a word), the clip before plays on to the end of the word and the
  sliver goes -- its frames are never trimmed out of the next clip and no extension is left without a clip (on the
  Spider-Man school clip this left holes of 14 and 8 frames on V1 and A1). An A1 edge where V1 does not cut and the
  other side is a muted piece (a cutaway over music) moves on its own: the audio plays on into the silence to the end
  of the word, or stops before the word when that silence is too short.
* **The hard check, on the final XML**: every audio cut of A1 (an item's start or end where the RAW does not play on)
  must land outside speech; one that lands inside fails the run (`XML SPEECH` in the report and the console, with
  the words there). On run 011 the competitor-placed cuts fail it 10 times; the new export passes.
* The end summary lists every cut that was moved (*Cuts moved off speech*: the clip, which edge, by how many frames,
  RAW before → after, the words there) and every clip removed because the clip before it now plays it;
  `extras/report.md` has the same list.

On run 011 (the files at the repo root), 9 of the 12 cut edges I moved by hand (leaving out where I dropped
"Okay", "Uh", "to" and the 1.5 s before "I have a daughter") come out within 2 frames of mine with that session's
`--pad-after` 0.15 s (7 with today's 0.05). The other three: I kept 0.39 s after "age." and 0.04 s after "am", and I
ended S08b just before "to" to drop it, where the tool keeps "a son to a married couple" playing. My finished
Zendaya-age edit, made later, leaves 0.12 s earlier than 0.15 s at the cuts it shares with the tool -- like my other
finished videos: hence 0.05.

### Silence removal (`--keep-silence` turns it off)

* **Silence across a cut, too.** The end of one clip and the start of the next together never keep more silence
  than `--pad-after` + `--pad-before` (0.08 s): longer, both sides are trimmed, however short the pause (the 0.3 s
  `--min-silence` is for pauses inside a clip). The check on the final XML fails the run on a cut with more
  (`XML SILENCE`, two frames of rounding allowed; an edge held 0.25 s from a RAW shot change against a flash frame
  may keep more). Silence is the quiet of the RAW's speech map (above) — the same quiet the speech check knows, so a
  breath is never cut as silence.

Every silence of **my** edit's audio is cut out of `1_edit.xml` — measured on the RAW audio under my clips (A1),
never on the competitor's, so music it added does not count as speech. In competitor mode this happens after the
competitor's cuts are recreated and moved off speech (above; also where the competitor kept the pause), with the
recheck model's word timings of the RAW; without a competitor (below) the RAW alone is cut this way.

* **Silence** = the short-window loudness (50 ms RMS, every 10 ms; a louder blip under 0.08 s is a click, not
  speech — single peaks never count) below this video's silence threshold for longer than `--min-silence`
  (default 0.3 s), outside every transcribed word.
* **The threshold adapts to each video**: its speech level (the loudness of its loudest 5% of 50 ms windows) and its
  background noise (its quietest 10%, digital silence ignored) are measured, and the threshold sits a third of the
  way from the noise up to the speech (at least 3 dB above the noise, at least 6 dB under the speech). A noisy
  video's pauses are cut too, and loud and quiet recordings need no setting: on the Deadpool RAW `tests/real/deadpool/raw.mp4` (speech
  -16.2 dBFS, background -42.2 dBFS) it is -33.1 dBFS. `--silence-db DB` replaces it with DB under the speech level
  (e.g. `--silence-db -20`).
* **Never inside a word**: the edit's audio is transcribed (word timings, the captions' model, cached) and a cut only falls in a gap between two words. Each word's timing is trimmed to its audible part (6 dB
  over the background), so a timing that runs on into the pause does not keep the pause. Of each gap,
  `--pad-after` (0.05 s) after the word before it and `--pad-before` (0.03 s) before the word after it
  are kept (at the very start and end of the edit there is no word to protect). The soft end of a word that trails
  off under the threshold (still 3 dB over the background, at most 0.2 s) belongs to the word, so the pads are kept
  after it. Without
  faster-whisper the cuts come from loudness alone and the summary says so. Cut points land on whole 60 fps frames,
  rounded inwards (never more than the silence), and never inside a cross dissolve.
* No clicks: A1 fades out over the last frame before every cut and in over the first frame after it (Audio Levels
  keyframes); the cuts lie inside silences, so only the room tone is touched.
* Everything after a removed silence moves earlier: clips spanning one are split around it (each piece keeps its
  clip's fixed framing, so every clip still covers the window and `--min-move` still holds), markers move, the
  sequence gets shorter. The XML check validates the cut sequence against the plan.
* Captions: competitor captions keep their splits (the hard rules aside, see *Captions*) — their times move with
  the cuts; a caption completely inside a removed silence is dropped and listed. Voice captions are transcribed from the
  cut edit.
* The end summary shows the settings used for this video (speech level, background noise, threshold and how it
  was set, minimum length, padding, how many words were timed), the total removed, the new length and every
  removed silence (its time in the edit before removal, its length, where the cut is now); `extras/report.md` has
  the same table.

### Without a competitor (`--raw` only)

`python -m match_cuts --raw RAW.mp4 --out ..\..\output` (no `--competitor`) makes the edit from the RAW alone: its
speech kept, its silences cut out as above, in the usual numbered run folder (`1_edit.xml`, `2_captions.srt`,
`extras/`; the `restyle` command works the same). Every stretch of speech shows the RAW scaled to cover the template
window with the main person's face at the window's centre (the largest face, then the same person while they stay
in view), one fixed framing per clip, and `--min-move` holds the framing until it would move 250 px. Captions: voice
mode with the RAW recheck, transcribed from the cut edit. (Before, `--raw` without `--competitor` looked for the
competitor in `--input-dir`; now it means "no competitor".)

### B-roll cutaways (`--no-broll`)

A cutaway is a piece whose picture leaves the main clip — B-roll from another moment of the RAW, a
NOT-IN-RAW insert or an uncertain piece — right next to a main-clip shot (a RAW shot whose audio follows
its own picture). The main clip's time line (that shot's RAW time map, extended forward from the shot
before or backward from the shot after) is tested against the competitor's audio under the cutaway with
the same check as the continuous audio lines: a strong correlation within ±10 ms that beats every other
alignment, after calibrating the competitor's A/V offset on the main-clip shot itself. When it passes,
the cutaway becomes the RAW video of that line, framed like the main-clip shot, and is joined with it into
one continuous clip when framing and speed continue unchanged; a cutaway too short to measure (≤ 2 frames)
is replaced only between two shots of the same line, or between two cutaways replaced by the same line. When the
RAW audio does not continue (music, voice-over, the cutaway's own sound) the cutaway stays as the competitor has it
-- unless it is shorter than a shot can be (0.25 s) and its sound cannot be measured: then the clip before plays on
over it (it would be a flash frame). A shot of the main clip whose own sound plays under it (a strong correlation
up to 0.1 s off its picture: too far for the ±10 ms above) is the main clip, never a cutaway.

Only what you import changes: `1_edit.xml` (with a `B-ROLL REPLACED` marker on every spot),
`recreated_edit.edl` and `cutlist.csv` (`debug/cutlist_no_broll.json` holds the export cut list).
`cutlist.json`, the preview / compare renders and the verification stay faithful to the competitor, so the
checks still prove every cut. The report's *B-roll cutaways* section lists every replaced and kept cutaway
with its competitor and 60 fps sequence timecodes.

### Captions

`2_captions.srt` is written on every run, timed frame-exactly on the 60.00 fps Premiere sequence
(competitor frame k = sequence frame 2k for a 30 fps competitor), **in your caption style**, learned from your own
SRTs in `srt/` (`match_cuts/caption_style.json`; after adding SRTs, rebuild it with `python -m match_cuts.caption_style`;
the SRTs of the answer-key videos are left out of it, so check-all scores videos the style has not seen). Your style
(618 captions in 15 SRTs): 1 word 24 %, 2 words 41 %, 3 words 27 %, 4 words 8 %; median 10 characters, 90 % at 16 or
fewer, at most 20; median 0.53 s on screen; no full stops or commas; 60 % start lower case; back to back.

**The words** are what is said in the cut edit (its audio: the RAW audio on the edit's cuts, never the raw clip):

* **Speech recognition** (`asr.py`; see *Speech recognition* below): Whisper **large-v3** on the GPU
  (`--caption-model`), with the words of `caption_allowlist.txt` as vocabulary hints (not the glossary's: see *Learn
  from your corrections*). Every word is then timed by **forced alignment** (`align.py`: torchaudio's MMS_FA wav2vec2
  model, on the GPU, 20 ms steps; a word after a pause starts on its first sound), within a frame.
* **A second model** (`--caption-check-model`, default `large-v3-turbo`; `none` turns it off) transcribes the same
  audio. Where the two disagree: the same words written two ways, the reduced form wins ("gonna": a model writes it
  only when it heard it); otherwise the competitor's caption read clearly there decides when it agrees with exactly
  one version (with the words on either side); otherwise the best model's version stays, marked unsure for the
  recheck below. A stock phrase only one model heard (Whisper's well-known inventions over laughter or music:
  "Thanks for watching", "Thanks for joining us") is dropped.
* **What the screen says** (competitor captions): where a caption read clearly on screen has other words than the
  models heard ("SO AS A JOKE" for "There was a joke", "the X-Force" for "X-Force"), both readings of the phrase (the
  same words heard on either side) are scored by **both** speech models against the audio (the likelihood of each
  text, teacher-forced; the RAW's audio where the edit plays the phrase in order). The screen's reading replaces the
  heard one only when both models find it the more likely, so a misread screen, or a word the competitor wrote that
  nobody said, stays out. Where the screen writes the spoken form ("WANNA") and the transcript the full one ("want
  to"), the screen's form is used.
* **Unclear words are rechecked against the RAW** (below).
* **Another video's stretch** (see *B-roll*): the competitor's audio there is transcribed and captioned; the stretch
  is the only gap the captions keep.

**The captions**, one of three ways (chosen per clip, the end summary says which):

* **follow**: the competitor's captions are already in your style (mixed case, 2–4 words). Its caption breaks and
  timing are kept, with the words heard in place of the words read: a word goes with the caption on screen when it
  is said (where the competitor switches captions in mid-word, the words around the switch go where the two
  captions' text says). A caption starts where my edit plays the moment of the RAW the competitor's caption starts
  on, in the competitor's picture time (a cutaway placed by where the competitor's sound plays is moved by its
  measured A/V offset, so the whole edit runs on one clock: Deadpool's file plays its sound 54 ms after the
  picture). Where the two edits differ between a caption's start and its first word, the caption is timed on the
  words my edit plays: when the competitor cuts between them, the caption starts as long before my edit plays the
  take after that cut as the competitor's starts before its cut (one frame before the cut: on the cut); when my
  edit leaves out the caption's first moment (a pause it cut), it starts as long before its first word as the
  competitor's did, never before the word before it ends.
* **regroup**: the competitor writes in capitals or one word at a time. The words heard are grouped in your style by
  a model of where you break between two words (learned from your SRTs by word and by kind of word, plus the pause
  between them, and the caption lengths you use), the best grouping by dynamic programming; never across a sentence
  end or a video cut. Where the competitor's caption changes between two words (matched by the moment of the RAW
  both edits play), a break there is likelier: on the answer keys you break there 44 % of the time, inside one of
  its captions 26 %. A caption starts on the frame its first word begins in, or on the cut when its first word
  comes at most 0.5 s after a video cut.
* **voice**: no burned-in captions, or `--voiceover FILE`; the same grouping as regroup.

In every mode the captions are **back to back**: a caption ends where the next one starts, a pause over 1 s gets a
`*...*` placeholder (the action goes there), and no caption ever overlaps the next. With no transcript at all (no
speech model can run here), the competitor's captions are copied as read and the older rules apply (a caption split
at a sentence end and on every video cut, never a lone weak word, pairs like `a joke` / `of Science` kept together,
captions over 16 characters split at a natural break).

**Capitals**: only `I`, names, acronyms and the first word of a caption after a real pause in speech (over 0.5 s:
the transcript's gap, else the gap before the caption); in follow mode the competitor's capital on a caption's first
word is kept. Names: the transcript's capitals in mid-sentence (`School`, `Science`), words the word list only writes
capitalised (`Bronx`, `Parker`), unknown capitalised words (`Keanu`), and a capitalised word next to a name.

**Stutters**: the same short word said twice in a row inside one caption (`The the one that's`, `I I`) is kept once
and listed (*Caption stutters kept once*); a word repeated as separate captions (`no` | `no` | `no`) stays.

**The hard rules** (`caption-generator-prompt.md`, *Hard rules*) are a final check on every caption file before it
is written (`match_cuts/caption_rules.py`); a file that still breaks rules 1–4 is never written:

| rule | check | fixed (counted as *changed*) | otherwise (counted as *flagged*, listed) |
|---|---|---|---|
| 1 one sentence | no `?` / `!` / `.` with more text after it | split there | — |
| 2 one speaker | no sentence end the transcript heard inside a caption (no speaker labels: a reply starts a new sentence) | split there, at the word's time | — |
| 3 casing inside a word | no `yoU` | `you` (the transcript's casing, else the word list's) | — |
| 4 capitals | no ALL-CAPS word but acronyms; capitals only for `I`, names, acronyms, after a pause | lower case (`WAS` → `was`, `PETER PARKER` → `Peter Parker`) | — |
| 5 real words | every token in the word list, a name, a number or an interjection | competitor mode: a reading that is not a word and was not read clearly takes the word the transcript clearly heard; screen noise (`1`, `V`, `_`) is left out | listed, never guessed (deliberate misspellings stay) |
| 6 length | spoken: 20 characters / 5 words; `*actions*`: 24 | split at a word (no weak ending) | one word over 20 characters |
| 7 weak words | no caption of a single weak word; no weak last word where it can move | joined / moved to the next words | kept where it ends a sentence, a silence / interjection follows, it is kept together with the word before it, or the caption already gave one |
| 8 no gaps | `end[i] == start[i+1]`, no overlap | closed in every mode (a pause over 1 s: a `*...*` placeholder) | another video's stretch stays a gap |
| 9 kept together | no pair kept together split between two captions (copied captions; the style grouping decides its own breaks) | regrouped (copied one-word captions) | split only where the cap or a video cut forces it |
| 10 video cuts | no caption across a cut of `1_edit.xml` (V1 clip boundaries) | split on the cut / its edge moved onto the cut | — |

**Acronyms** (`caption_allowlist.txt` next to this README; one word or phrase per line, extend it): `AI`, `MJ`, `MCU`, plus
the acronyms the word list writes in capitals (`FBI`, `NASA`, `TV`); never a word that is also an ordinary word
(`AS`, `WAS`, `IT`). Words listed there are written exactly as listed (also `iPhone`, a name or a deliberate
misspelling the word list does not know); a phrase there is never split across captions. The word list is SCOWL (`match_cuts/wordlist/`, see its licence file).
The end summary says how many captions each rule changed or flagged (*Caption rules*), and *Captions worth a look*
lists every flag, every word taken from the transcript and every piece of screen noise left out.

**Unclear speech is double-checked against the RAW** (every mode). A word the transcription of the edit is unsure
about (heard with low confidence, with music or noise under it, less than 12 dB above the sound bed around it, with an
edit point cutting into it, or where the second model heard something else) is transcribed again from the RAW
footage the edit plays there (the edit's own audio map: J/L cuts, speed changes and audio lines are followed), with
3 s of context on each side so the model hears the whole sentence rather than the cut piece
(`--caption-recheck-model`, default `large-v3`; `none` turns it off). The RAW's words are mapped back onto the edit's
timeline and the versions are compared word by word: where they agree (or say the same words two ways, "gonna" /
"going to") the word is confirmed; where they differ the RAW's version is used when it is clearly more confident, and
a competitor caption read clearly at that spot decides when exactly one version agrees with it, together with the
words on either side. A word the RAW repeats at the edge of its window (the same word the edit's transcript has right
there) is not added twice. Nothing is guessed: a spot still unsure after that keeps the best version and is listed
under *Captions worth a look* with its time and the alternatives heard (edit, RAW, caption). The end summary says how
many words were rechecked and how many changed; the report lists every change, every second-model decision and every
reading of the screen that was scored. With `--voiceover`, the voice-over file itself is the source.

The report's *Captions* section lists, in competitor mode, the competitor's writing conventions, the captions
written from the transcript because they could not be read and the readings the OCR was unsure of; in voice mode,
the style check, captions at the 24-character cap, the `*...*` timecodes and possible mis-transcriptions / doubled /
missing words — flagged, never corrected; in both, the hard-rules table (changed / flagged per rule) and every row
the rules listed. Speakers are not told apart by voice (faster-whisper has no diarisation): rule 2 relies on the
sentence ends the transcript hears.

### Speech recognition

The speech models run on the GPU when there is one (`asr.py`: faster-whisper / CTranslate2 for Whisper, PyTorch for
the forced alignment and the other engines), else on the CPU, and the end summary says which, e.g. *Speech
recognition: large-v3 on the GPU (NVIDIA GeForce RTX 5080) ... words timed by forced alignment (cuda)*. A model that
fails on the GPU runs again on the CPU, and a model that cannot run at all falls back to `small.en`; both are said in
the summary. Measured on the three answer-key videos with `python -m match_cuts.asr_bench` (word errors against your
SRTs; the full table with timing is `reports/task-4/asr_bench.md`):

| model | word errors | Deadpool / Spider-Man / Zendaya-age | word starts: own timing → aligned | speed (RTX 5080) |
|---|---|---|---|---|
| Whisper large-v3 (default) | **4.2 %** | 1.6 / 4.1 / 12.8 % | 87 → 40 ms | 8× real time |
| Whisper large-v3-turbo (second model) | 6.5 % | 8.9 / 2.7 / 12.8 % | 43 → 39 ms | 27× |
| Whisper medium.en | 7.1 % | 7.3 / 4.8 / 15.4 % | 43 → 35 ms | 9× |
| Whisper small.en (fallback, the old default) | 8.1 % | 6.5 / 6.8 / 17.9 % | 40 → 36 ms | 13× |
| NVIDIA Parakeet TDT 0.6B v2 | 7.4 % | 7.3 / 4.8 / 17.9 % | 50 → 34 ms | 214× |
| NVIDIA Parakeet TDT 0.6B v3 | 8.1 % | 6.5 / 4.1 / 28.2 % | 62 → 34 ms | 20× |
| NVIDIA Canary-Qwen 2.5B | 8.7 % | 4.0 / 9.5 / 20.5 % | no timing → 37 ms | 1× |
| Cohere Transcribe | not run (gated on Hugging Face) | | | |

Word starts: the average distance from each word that opens one of your captions to that caption's start. The clips
are short (18–39 s), so the speeds include each model's per-call overhead; a long video runs faster.

Whisper large-v3 is the most accurate here and is the default (`--caption-model`); large-v3-turbo is the second
model (`--caption-check-model`); `small.en` (the previous default) is the fallback. Forced alignment brings
large-v3's word starts from 87 ms to 40 ms off your caption starts on average (MMS_FA; the English-only wav2vec2
aligner was no better). The allowlist's words are passed as hints. NVIDIA Parakeet and Canary-Qwen run through
NeMo / transformers (`pip install "nemo_toolkit[asr]" transformers`), only for the benchmark; Cohere Transcribe is
gated on Hugging Face (accept its terms, `hf auth login`, then it runs too).

**A/V offset.** Many short-form edits play their sound a little early or late against the picture (for
example −85 ms). match_cuts measures this shift **once per run** (`cutlist.audio.av_offset`: accepted when at least
3 segments and 2 s of audio, 70 % of their weight, agree, and no single segment moves it by more than 2 ms or a
quarter of a RAW frame, whichever is more) and the report
shows it in one line, e.g. *"Audio sync: competitor audio is 85.4 ms later than its picture, relative to RAW's
own A/V sync (lag -85.4 ms, interval -86.6 … -84.2 ms, 16 segment(s), coverage 100%; a property of the input
files, measured); … export keeps RAW lip-sync (--audio-sync raw)"*. It is a property of the competitor file,
not an error of the recreation: criterion 5 checks every segment against it and lists it as one explained
exception.

- `--audio-sync raw` (default): the AE project uses the RAW audio in its own lip-sync. Extra audio-only
  layers (`Sxx  audio (J/L cut)`) appear only where the editor really let the sound start before / end after
  the picture cut (J/L cut).
- `--audio-sync competitor`: every segment's sound goes on its own audio-only layer, shifted so it sounds
  exactly like the competitor. The preview follows the same rule; FCP7 XML / EDL get separate audio events at
  the nearest whole frame (the sub-frame rest is written as a comment next to each event).
- Either way, sound that keeps playing under a video-only slow motion, freeze, uncertain range or placeholder
  is exported as one continuous audio-only layer (`Sxx-Syy  audio (audio line: …)`) instead of silence.

**Input auto-detection** (prompt Configuration): the competitor is the *portrait* file, failing that the
*shorter* one. If `--competitor`/`--raw` look reversed they are swapped with a warning (`--no-swap`
disables this). If the default files do not exist, `./input` is scanned for exactly two videos. Two files
with the same orientation and duration are genuinely ambiguous: the tool stops and asks you to pass both
flags.

**Exit code** (DESIGN §7 D5):

| code | meaning | headline |
|---|---|---|
| `0` | every acceptance criterion is `pass` / `pass_with_exceptions` and no Stage 9 check failed | `PASS` |
| `1` | an acceptance criterion or a Stage 9 check (incl. `9.8 deliverables`) failed, or an input file changed during the run (still being downloaded or copied, re-exported: `inputs unchanged`) | `FAIL` |
| `2` | the run itself failed: missing/ambiguous inputs, a crashed stage (see `extras/match_cuts.log` in the run folder) | none (`match_cuts: ERROR: …` on stderr) |
| `3` | nothing failed, but a criterion could not be verified (`not_available`, e.g. no Node.js for the JSX mock), or a hard check of `1_edit.xml` could not run because its analysis failed (the speech map, the RAW's shot changes, who speaks) | `PASS (criterion 6 not verified: …)` / `PASS (not checked: …)` |

A wrapper script should treat `0` and `3` as "the recreation is correct as far as it could be checked"
(`3` is the normal result on a machine without Node.js / After Effects). Ctrl-C exits with `130`.

The final summary prints one line per acceptance criterion, the output paths and the warnings:

```
match_cuts result: PASS
  c1 coverage                    PASS   21 segments (1 NOT-IN-RAW), 1800/1800 frames covered, ...
  c2 frame-exact cuts            PASS   20 cuts: 20 verified both sides, 0 exceptions, 0 failed
  c3 frame-exact source frames   PASS*  AE sim: plan: 1732/1740 exact, 8 timing-tie ...
  c4 speed / framing             PASS   20 raw segments: 0 problems, 0 exceptions
  c5 audio                       PASS*  19 segments measured, max |lag| 0.21 ms, 2 explained exceptions
  c6 After Effects               PASS   mock run: 13/13 checks ok (mock only: After Effects not installed)
  9.7 determinism                N/A    cut list not re-assembled from the caches (check-all does: --check-determinism)
  (PASS* = passed with listed, explained exceptions; a second run of the same inputs: 9.7 PASS, identical to the previous run)
```

### Restyle the captions in Premiere (`restyle`)

After a `--premiere` run: import `1_edit.xml` and `2_captions.srt` from the run folder into Premiere, drag the
captions onto the sequence, upgrade them to graphics (*Upgrade caption to graphic*) and save the project. Then:

```
python -m match_cuts restyle "C:\path\to\my edit.prproj"
```

It writes `3_captions_styled.prproj` into the newest numbered run folder (`--out output\003` for another one;
the original project is never written to; Premiere finds the media by the full paths the project already holds)
with every plain
caption in the POPW style: Verdana Bold 58 white, two strokes and a drop shadow, the same position as the
reference, the Scale pop 88% → 100% starting on each caption's first frame and lasting exactly as long as the
donor's (0.1333 s: 8 frames of the 60 fps sequence), and the dots and commas stripped (`Mr.` → `Mr`, `C.I.D.`
→ `CID`; a dot or comma between two digits stays: `£4.50`, `15,000`).

The work is done by the four scripts in `match_cuts/restyle_scripts/` (see `restyle-prompt.md` at the repo
root); `match_cuts/restyle.py` only chooses their arguments and checks the result:

1. unpacks the `.prproj` (gzipped XML) with Python's gzip module;
2. counts plain (text only) and styled (Motion, Graphic Group, Text) caption clips on every video track and
   restyles the track with the most plain captions;
3. takes the style from a styled caption on another track of the project (`capfix.py`, or `capfix_xdonor.py`
   from another sequence) or, when there is none, from `reference/popw_reference.prproj` (`capfix_xdonor.py`,
   after `injectstyle.py` adds the POPW style item to a project that has none);
4. runs `capverify.py` and checks that every keyframe keeps the donor's timing to the tick: **when anything is
   wrong nothing is written**, and the problems are printed (exit code 1);
5. repacks the project and prints a short report: how many captions were styled, the punctuation cleaned, and
   text worth a look (doubled words, a caption ending on a weak word, over 24 characters, `*...*` placeholders
   left in). These are reported, never changed.

`--donor PROJECT.prproj` takes the style from another correctly styled project; `--overwrite` replaces an
existing `3_captions_styled.prproj` (without it the run stops rather than overwrite one you may have worked in).

### Learn from your finished videos (`learn <folder>`)

```
..\..\.venv\Scripts\python -m match_cuts learn finished
```

`finished\` holds one folder per finished video: `final.mp4` (the video as you exported it), `competitor.mp4`,
`raw.mp4` and `project.prproj` (for your captions); `topaz.mp4` and `project.aep` may be there too. A folder with no
`final.mp4` -- your cuts, audio cuts, framing and captions directly in `project.prproj` (no After Effects comp) -- is
read from the project instead: the project's clips of the RAW (the media whose size, frame rate and length are
`raw.mp4`'s; the template overlay and other footage are not your edit) give your audio cuts (A1: the answer key) and
your cuts and framing (V1: the part of the RAW the template window shows, from each clip's Position and Scale), its
caption track your captions, as they are. `learn` works on every folder in it (or on the one folder you give):

- **Two runs of the tool per video**, in `work\learn\` (a finished run of the same two files there is used again;
  `--fresh` for new ones, `--fast` for quick ones): the tool on `competitor.mp4` + `raw.mp4` -- what it makes now
  -- and on `final.mp4` + `raw.mp4`: your finished video matched against the RAW frame by frame, the way the tool
  matches a competitor. **Your cuts and framing are read from the picture**, so it works whatever the project holds
  (an After Effects comp, an enhanced sound file on A1). Silence is never measured on `final.mp4` (its sound is
  enhanced): the tool's speech checks use `raw.mp4`.
- **The files must belong together, by their content** (no name inside a project is ever used): `final.mp4` and
  `competitor.mp4` must each show the RAW for at least half their time, and play the same part of it (at least 30 %
  of what your video plays). A folder missing a file, or whose files do not belong together, is skipped, and the
  summary says which and why.
- **Your captions** are the project's caption graphics, used only when they are this video's: at least 60 % of them
  show on `final.mp4`'s screen (read the way the tool reads a competitor's captions, from the row of text that
  changes most -- not a handle or watermark that stays) within 0.25 s. Otherwise none are used and the summary says
  so. `--final-only <folder>` takes them from `final.mp4`'s screen and leaves the
  project out (a project changed after the export). Only what the finished video shows counts: a hidden track (its
  eye closed or muted) and a disabled clip never do. When you upgrade an imported caption track to graphics, Premiere
  keeps that track hidden next to them with the text from before your edits, and a template can bring hidden caption
  tracks of another video; if all your captions are hidden, the summary names the track.
- **`topaz.mp4`**, when there is one, is told by its content: your edited picture (the length of `final.mp4`,
  cutting where it cuts) or the RAW enhanced, with its size, frame rate and sound.
- **What it compares**: your cuts against the tool's (the cut score, see check-all) and the competitor's (which of
  its cuts you kept), the three edits' lengths, your clips' starts and ends against the tool's, and your framing
  against the competitor's (where your picture looks into the RAW, how much of it it shows). Caption words you
  changed go into the glossary as below; a kind of change made on several videos becomes a suggested default.
- **A test case** in `tests/real/<folder name>/`: `competitor.mp4`, `raw.mp4` (smaller over 100 MB),
  `answer_edit.json` (your timeline from `final.mp4`: the cut answer key, and what `answer.srt` is timed on),
  `answer.srt` (your captions, when they could be read), `case.json` and `learned.json`. The `finished\` folder
  itself is never committed: only the test case is.

### Learn from your corrections (`learn`)

After you finish a video in Premiere:

```
..\..\.venv\Scripts\python -m match_cuts learn "<your finished project>.prproj"
```

It compares your finished project with what the tool generated for that run (`1_edit.xml` and `2_captions.srt`).

- **The run** is found from your project: its clips play the RAW from the run's `extras\media\` folder (the older
  flat `output\media\` layout too). `--run <folder>` names it when the media moved.
  - The run's RAW must be the video your project plays: the same size, frame rate and length, as Premiere recorded
    them in the project. The older flat folder keeps only its latest run, so a project made from an earlier one is
    refused with the difference, for example `1920x1080 against 1280x720; 23.976 fps against 59.940`. Nothing is
    compared with the wrong run.
- **Caption words you changed go into a glossary**, `caption_glossary.txt` next to `caption_allowlist.txt`, one
  `heard -> written` per line, with the videos each came from.
  - Words are compared in order, not by time, because a moved cut shifts every caption after it. A change counts
    only between 2 unchanged words on each side, and at most 3 words long; a longer rewrite does not count.
  - Only corrections go in: a changed word spelled like the heard one ("zendeya" -> "Zendaya", "want to" ->
    "wanna"), or the capitals of a name or an acronym ("tom" -> "Tom"). A plain word in capitals ("like" -> "LIKE":
    your emphasis) and other words ("eventually" -> "and then": a one-off mishearing) are kept in the test case's
    `learned.json` only.
  - The glossary is not given to the speech models as hot words. A hot word changes how the whole transcript is
    punctuated and capitalised, also in videos that never say it: with `others` and `Tobey` as hot words, Deadpool
    had 42 of 60 captions exactly instead of 47. Only `caption_allowlist.txt` goes to them.
  - Where a model still hears the old words, the written form replaces them only where the audio fits: both models
    score both readings of the phrase against the audio, and the written form must be at most 1 nat less likely
    (about a third as likely; you corrected it before). Words are never replaced blindly.
  - Where the competitor's screen shows exactly what you once corrected the heard words to, the screen's reading
    needs only to fit the audio, not to be likelier.
  - Edit or delete lines of the file freely.
- **Your cut and framing changes are recorded** in `learned.json`.
  - Cuts are compared on the sound: A1 plays the RAW in both edits, even when your picture is an After Effects comp.
    Recorded: each clip's start and end moved (RAW seconds), clips removed, clips added.
  - Framing is compared on the picture clips, when your project still has the RAW's own clips: moved sideways (px)
    or zoomed (%).
  - A kind of change you make on 3 or more videos (on 30 % of a video's clips, at least 3) becomes a **suggested
    new default**, printed with the videos it comes from, for example `clips end earlier on 3 videos (...; median
    -0.19 s): --pad-after 0.00 instead of 0.05`. Nothing is ever changed for you. For a finished video (`learn
    <folder>`) only the cuts you and the tool both make are compared (how you trim each), not its clips one by one.
- **A test case** in `tests/real/<name>/`: `competitor.mp4`, `raw.mp4`, `answer.srt` (your captions: the answer
  key), `answer_edit.json` (your timeline: what the RAW plays where), `case.json` and `learned.json`.
  - Your captions are the ones your finished video shows: a visible caption track, else your caption graphics. A
    hidden track or a disabled clip never counts. If all your captions are on a hidden track, `learn` names it and
    writes no `answer.srt`; an empty key would score every run against nothing.
  - check-all then scores every video you ever corrected.
  - A case of the same competitor is updated, not doubled: your new answer key replaces the old one, and `git diff`
    shows what changed before you push.
  - The name comes from the competitor's file (`--name` to choose).
- **A RAW over 100 MB** is copied smaller automatically: the same width, height and frame rate, every frame kept,
  and the audio as it is (H.264 at a capped bit rate, about 90 MB). GitHub refuses files over 100 MB.
- At the end it prints a short summary and the exact git commands that push the new case, for example:

```
cd "C:\Users\you\MovieRecaps"
git add "tests/real/zendaya-interview" "tools/match_cuts/caption_glossary.txt"
git commit -m "Test case zendaya-interview: learned from my finished edit"
git push
```

## Outputs

Each run gets its own numbered folder in `--out` (`output\001`, `output\002`, ...: the next free number, so a
new video never overwrites the previous one). At its top only the files you use, numbered in the order you use
them; everything else in `extras\`. The console ends with a short summary: these paths, what to check by hand
(the `B-ROLL REPLACED` spots, the uncertain / NOT-IN-RAW / retimed spots with their sequence timecodes, another
video's stretches left empty, the captions worth a look) and the run folder.

```
output/001/
  1_edit.xml               the Premiere sequence (FCP7 XML; points at extras/media/ by full path)
  2_captions.srt           captions on the 60 fps sequence (copied from the competitor or made from the voice-over)
  3_captions_styled.prproj written by `python -m match_cuts restyle` (see above)
output/001/extras/
  build_ae_project.jsx     run in After Effects -> builds and saves recreated_edit.aep next to itself
  recreated_edit.aep       only when After Effects is installed on this machine (Stage 7.6)
  ae_time_check.txt        written by the .jsx in After Effects: per RAW layer, AE's own sourceTime() vs the plan
  media/                   RAW (or its AE-safe conformed copy raw_ae.mov / raw_ae.mp4) + competitor_ref.mp4
  cutlist.json             the single source of truth (prompt Stage 6 schema + extras)
  cutlist.csv              one row per segment (timecodes as in the report: drop-frame for 29.97/59.94)
  recreated_edit.edl       CMX3600 EDL (cuts + M2 speed lines)
  preview_recreation.mp4   frame-exact render of the recreation from RAW (MAIN size / fps / layout)
  compare.mp4              competitor | recreation | amplified difference; frame number, timecode and segment
                           in a label strip above each panel (never over the picture)
  report.md                a plain-language summary first (what passed, what failed and why, what to check by
                           hand in AE, headlines), then criteria, inputs (incl. MP4 edit lists, iTunSMPB,
                           stream durations), layout, segment table, edit-style breakdown, warnings, timings
  verify.json              every Stage 9 check and acceptance criterion with its evidence
  debug/                   mapping.png, scores.png, layout.png, layout_refine.png, cuts/cut_XX.png,
                           low_confidence/k#####.png, verify_failures/k#####.png,
                           decisions.jsonl (this run's evidence, cached stages replayed with cached=true)
  match_cuts.log           this run's full debug log
work/
  cache/<stage>/<key>.*    content-addressed caches (key = input file hashes + analysis parameters; the
                           anchors / FrameMap also the layout geometry + overlay masks they were matched with)
  decisions.jsonl          every decision with its evidence (truncated at the start of each run; cached
                           stages replay their stored records; copied to extras/debug/)
  frame_map.npz            m(k): the RAW frame, scores, ranges and transform of every competitor frame
  layout.json, ae_plan.json, ae_mock_runs.json, verify_zncc.npy, verify_rerun/
```

`cutlist.json` notes: frame indices are integers, intervals half-open `[comp_in, comp_out)`, frame
rates exact rationals (`"30000/1001"`), seconds have 9 decimals. `transform` maps RAW pixels (after the
horizontal flip when `flip_h`) to competitor pixels, CORNER convention. `raw_in_seconds` is the
phase-solved RAW time at `comp_in`: any value inside the feasible interval `raw_in_interval` reproduces
every measured frame under AE's floor rule (`raw_in_interval_both`: also under round-to-nearest). Inside
it the phase is chosen from the AUDIO when the segment's audio correlates confidently (`audio.phase_source
= "audio"`, `audio.lag_ms_video` = the lag the video placement would have had), otherwise the midpoint
of the breakpoint cell with the most slack (a cell = the raw_in values that show exactly the same RAW frame
on every frame of the segment; for exact frames the interval centre); this removes the systematic
quarter-frame audio offset of the centre (8.3 ms at 30p, 10.4 ms at 24p). The audio-chosen `raw_in` stays
in its cell with a margin of `min(cell / 2, max(5 % of the cell, ae_slack_tol_frames))` — never an integer
number of milliseconds, which realigns with NTSC frame boundaries — so the frames stay exact while an
in-point on the edge — an NLE cut at a RAW shot boundary — stays within ~2 ms in audio. Static /
ambiguous-identical shots, whose interval can span seconds, also get a wide audio search centred on the
feasible interval and covering all of it (half-width up to 60 s). `ae_margin_ms` is the exact AE floor-rule
slack of the written `raw_in` over every frame of the segment (ms). The only wall-clock values are in `provenance.timings`, which the determinism check
ignores; everything else is identical on a re-run.

## Pipeline

| stage | module | what |
|---|---|---|
| S0 | `pipeline.check_env` | OS, ffmpeg/ffprobe versions, Python packages, Node, After Effects / aerender search |
| S2 | `probe`, `conform` | ffprobe + a full decode pass per file; AE-unsafe files (VP9/AV1/HEVC, WebM, Opus, VFR, start offsets, edit lists, rotation, SAR ≠ 1) are conformed to `media/` and verified; analysis then uses **only** the files AE imports |
| S3 | `proxies` | memory-mapped grayscale proxies, 16 kHz mono analysis audio (original-rate audio for the final audio check) |
| S4 | `layout` | static mask, video box + corner radius, background, zones, caption/overlay masks, layout periods (fullscreen / split / PiP); after S5.3 the box is re-fitted against the warped RAW (`refine_box_from_raw`) and S5.2–5.3 re-run once if it changed |
| S5.1 | `audio_align` | FFT cross-correlation of 1 s windows (log-mel + onset), speed-scaled windows, sample-precise refine |
| S5.2 | `visual_match` | SIFT index of RAW, voting, RANSAC (also against the flipped RAW), ZNCC-verified anchors |
| S5.3 | `refine` | frame-exact m(k) with masked ZNCC, track transforms, ambiguous-identical ranges, rescue search |
| S5.4–5.5 | `segment` | DP cut placement, speed snap, transitions, framing keys, PySceneDetect cross-check |
| S5.6 | `audio_align` | per-segment lag, J/L offsets, pitch preservation, added music/SFX/VO |
| S6 | `phase_solve` + `pipeline` | exact phase LP per segment -> `raw_in_seconds`; `cutlist.json` |
| S7 | `export_ae` | AE plan (every number the JSX sets), ES3 `build_ae_project.jsx`, strict mock runs |
| S8 | `export_xml_edl`, `render_preview` | CSV, FCP7 XML, EDL (+ re-parse validation), preview and compare renders |
| S9 | `verify` | criteria c1..c6 and checks 9.1..9.7 |
| S10 | `report` | `report.md` and the summary |

Re-runs are fast: the expensive stages are cached in `work/cache`. Changing only export settings
(`--layout`, `--comp-size`, `--fps`, `--ae-time-mode`) never recomputes the analysis. A cached result is only ever
reused by the code that computed it: every key holds a fingerprint of that code (`common.STAGE_CODE`: the stage's
modules and the package modules they import -- not the pipeline, exports or report), so after an update the stages
whose code changed are computed again, the rest is reused (the version numbers in `common.STAGE_VERSION` still count
too). A cache file a reset or a power loss left damaged is computed again (every file is on the disk before it is
published). To force a stage to recompute, delete `work/cache/<stage>/`.

## Verification (Stage 9) and the acceptance criteria

**In plain words.** After the analysis, Stage 9 checks the result again with methods that do not trust the
analysis:

- **c1 coverage** — every competitor frame belongs to exactly one segment, placeholder or *uncertain* range.
- **c2 cuts** — at each cut, the frame before it really belongs to the left clip and the frame after it to the
  right clip. It also asks the opposite question ("is there a cut at all?"): if one clip's time line explains
  both sides just as well, the cut is reported as *spurious*.
- **c3 source frames** — After Effects' frame rule is simulated on every frame and must show the measured RAW
  frame (≥ 99 %). Two extra checks look only at the competitor and the recreation:
  - *temporal signature*: where the competitor repeats a frame (pulldown) or moves, the recreation must do the
    same (a *motion mismatch* is a held RAW frame while the competitor moves);
  - *±1 refit*: the RAW frames just before and after the chosen one are tried with their own framing; if a
    neighbour fits better, the chosen frame is wrong.
  Frames of an *uncertain* range always count as failures here.
- **c4 speed / framing** — speed, scale, position and rotation are measured again from scratch and must agree
  with the segment (± 1 % scale, ± 4 px).
- **c5 audio** — each segment's sound must line up with the competitor within ± 10 ms after the run's A/V
  offset; anything else needs an explanation from a fixed list (too short, not in RAW, music, …).
- **c6 After Effects** — the `.jsx` is run (in a strict mock without After Effects; with After Effects, also
  rendered and compared frame by frame).

A criterion is `pass`, `pass_with_exceptions` (every exception is listed with its reason), `fail` or
`not_available`. The details per criterion:

| criterion | checked by |
|---|---|
| **c1 coverage** | 9.1: segments + labelled NOT-IN-RAW placeholders tile `[0, N)` exactly; overlaps only where a measured transition of exactly that length explains them; raw segments must carry a RAW mapping |
| **c2 frame-exact cuts** | an independent per-cut check: the last frame of A scores higher against A's model (AE sampling rule + A's transform) than against B's model extended back, and the first frame of B the reverse, each RAW frame with its framing RE-MEASURED by ECC (never the neighbour's key held at its boundary); no-cut alternative: one side's time line extended over the other side (framing re-measured) that explains both within the score noise, or the same RAW frames with a continuous re-measured framing, is a *spurious cut*; a cut between the two frames of a competitor repeat pair fails; a 1-2 frame segment far off the line its neighbours share must beat that line (else *suspected misidentification*); crossfades: the fitted alpha ramp; NOT-IN-RAW neighbours: the placeholder frame must stay below `none_thresh` under *every* hypothesis its neighbours offer (each one's time line extended and its boundary frame held, the neighbour across the placeholder too); `uncertain` neighbours claim nothing (only the RAW side is checked); 9.4 writes `debug/cuts/cut_XX.png` (k-1..k+2, competitor over recreation) |
| **c3 frame-exact source frames** | 9.2: which RAW frame AE shows on every comp frame, simulated from the exact AE plan **and** from the values the JSX actually set in the mock run, must equal refine's MEASURED m(k) (before segmentation) on ≥ 99 % of matched frames; listed exception classes: ambiguous-identical, timing-tie, and frames segmentation re-assigned to its model; a plan that disagrees with the cut list always fails. Crossfade frames (both layers + opacity), dips and NOT-IN-RAW placeholders are checked too; a verified frame-blend path (AE Frame Mix) is judged by the dominant frame of its mix; every frame of an `uncertain` segment is a criterion-3 failure (neither matched nor NOT-IN-RAW, never an exception). 9.3: masked ZNCC of competitor vs a match-geometry recreation on every frame (each frame in its own box ROI; fullscreen periods on the whole canvas minus active zones) ≥ `verify_zncc`, failures in `debug/verify_failures/`; the delivered `preview_recreation.mp4` is always probed (frame count, fps). Two references the model cannot fool: 9.2b the **temporal signature** -- the competitor's own frame pairs labelled repeat / move (comp-only, noise floor measured per shot) against the recreation's (a recreation that repeats a RAW frame where the competitor moves, changes it where the competitor repeats, or jumps much more / less fails; a hold against a moving competitor is a *motion mismatch*); 9.2c the **±1 refit** -- RAW j-1 / j / j+1 each with its own ECC framing: a neighbour that beats the shown frame by more than the noise means the wrong RAW frame with a compensating framing. Masks come from the layout only (captions / text overlays), never from refine's residual masks; two MEASURED exclusions are listed: **moving competitor text** (a word sliding over the picture that the recreation never shows) is left out of the temporal signature, so a true freeze under an animated caption is no motion mismatch; a **RAW-only overlay** (a burned-in disclaimer / subtitle / logo bug your RAW has and the competitor does not: static in RAW coordinates where both play, a RAW graphic the competitor lacks, small) is left out of 9.2b / 9.2c / 9.3 and every frame it explains is listed (`RAW-only overlay at x,y,w,h in RAW px over frames a-b`) -- the frame must still match everywhere else, and a wrong frame, a misframing or a large mismatch is never explained this way. A timing tie counts only where the segment's own position sits on a frame boundary (never on a freeze); re-assigned frames are listed with their reason and class (within noise / outside noise / systematic run), never exempt |
| **c4 speed / framing** | speed inside the feasible range of the segment's frame constraints (± 0.5 %) and snapped whenever a snap value reproduces refine's measured frames; framing MEASURED independently (ECC from perturbed starts and a global phase-correlation start, ≥ 5 samples per segment, every frame of segments ≤ 6 frames) vs the segment model within ± 1 % scale / ± 4 px; an unmeasurable frame fails when the model scores below `verify_zncc` or its gradient-domain score is far below the neighbouring segments' (dark / low-texture misframing); flip must beat the mirrored hypothesis; rotation consistent |
| **c5 audio** | 9.5: per-segment lag of the rebuilt RAW audio vs the competitor's within ± 10 ms — the residual after the run's measured A/V offset (`cutlist.audio.av_offset`; 0 with `--audio-sync competitor`, whose recreation carries it), which verify re-measures itself and must confirm (else fail); a confirmed offset in raw sync is ONE run-level explained exception `av_offset`. Otherwise an explanation from the closed list `too_short, not_in_raw, audio_replaced, pitch_preserved, music_dominated, no_audio` (a confident correlation at a wrong lag always fails; `music_dominated` is only accepted when the per-segment audio analysis found it, and a wide ±2 s search catches grossly misaligned audio). Segments shorter than 0.5 s are checked as aggregated runs of consecutive pieces; an inverted audio range fails |
| **c6 After Effects** | the `.jsx` in the strict mock (no error alert; MAIN frame rate, duration, work area; saved `recreated_edit.aep` next to the script; one layer per segment with the planned name/startTime/stretch/in/out; the *media missing* scenario aborts cleanly after the relink dialog) + 9.6 `aerender` frame-by-frame comparison with the preview when AE is installed, and the JSX's After Effects source-time check (`ae_time_check.txt`; frame-exact layers AE still maps elsewhere fail). On Linux `pass` means *mock-verified* |
| 9.7 determinism | with `--check-determinism` (check-all always): segmentation → phase solve → audio → cut list re-run from the cached FrameMap/AudioHints in a fresh context; canonical JSON (without `provenance.timings`) must be byte-identical. Always: when the previous run's `cutlist.json` came from the same inputs, parameters and tool/stage versions it is compared too (a difference fails); a normal run with neither says N/A (the re-run costs as long as the segments stage and changes nothing in the edit) |
| 9.8 deliverables | every file of the deliverables tree exists (unless explicitly skipped, e.g. `--skip-preview`, or the `.aep` without AE), XML/EDL re-parse validation passed, no stage error |

Statuses: `pass`, `pass_with_exceptions` (every exception listed and explained), `fail`,
`not_available` (e.g. no Node for the mock, no AE for aerender).

Exit codes (the table under *Usage*): `0` everything passed; `1` a criterion or check failed (or an input file
changed during the run: `inputs unchanged`); `2` the run itself failed (bad inputs, a crashed stage); `3` nothing
failed but a criterion could not be verified (headline `PASS (criterion 6 not verified: …)`, e.g. Node.js missing so
the JSX was never executed), or a hard check of `1_edit.xml` could not run because its analysis failed (`PASS (not
checked: no audio cut inside speech (the speech map failed: …))`: the run never says a plain PASS then).

## Running the result in After Effects

1. Keep `build_ae_project.jsx` and `media/` together (copy the whole output folder).
2. **File → Scripts → Run Script File…** → `build_ae_project.jsx`.
3. It builds the project (`01 Comps`, `02 Source`, `03 Reference`), the `Recreated Edit` comp at the
   competitor's exact size, frame rate and duration, and saves `recreated_edit.aep` next to the script.
4. Guide layers (never rendered) outline the header, title, caption and watermark zones; coloured
   `MISSING - not in RAW` solids mark the ranges you have to fill; comp markers sit on every cut.
5. The `REFERENCE - competitor` layer on top is a switched-off guide layer in *Difference* mode:
   switch it on and black means the recreation matches.

**What the layers mean**

| you see | what it is | what to do |
|---|---|---|
| a RAW layer with *Time Stretch* | a normal segment: the clip plays at the measured speed from `raw_in_seconds` | nothing |
| a RAW layer with *Time Remap* and a key on **every** frame (hold keys) | a *frame-exact* layer: the segment's timing sits so close to a frame boundary that After Effects' own time rounding could show the neighbouring frame, so each frame is pinned (`--ae-time-mode auto` does this by itself; common for a 23.976 fps RAW in a 30 fps edit) | do not edit the keys; move the whole layer if needed |
| a layer whose name ends in `FRAME MIX`, with *Frame Blending → Frame Mix* switched on | the competitor used blended slow motion (each frame is a mix of two RAW frames); the path was verified frame by frame | keep frame blending on |
| an **amber** solid `UNCERTAIN - best RAW …` with a comp marker `… UNCERTAIN`, under a guide layer `GUIDE - best RAW evidence: …` | an *uncertain* range: the best RAW frame found looks similar but not similar enough to call it a match (score between `none_thresh` 0.60 and `match_thresh` 0.90), and not different enough to call it NOT-IN-RAW. The guide layer shows the best evidence (visible in the viewer, never rendered) | compare the guide layer with the `REFERENCE - competitor` layer. If it shows the right footage, duplicate the guide layer, switch *Guide Layer* off and delete the amber solid; otherwise find the shot by hand. Until then these frames count as criterion-3 failures |
| a coloured `MISSING - not in RAW` solid | footage that is not in the RAW at all (every hypothesis scores below 0.60) | fill it with other footage |
| an audio-only layer `Sxx  audio (…)` | sound of a J/L cut, a continuous audio line, or (with `--audio-sync competitor`) the shifted sound of a segment | nothing |

Headless: macOS `osascript -e 'tell application "Adobe After Effects 2024" to DoScriptFile "/abs/path/build_ae_project.jsx"'`,
Windows `"C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\AfterFX.exe" -r C:\abs\path\build_ae_project.jsx`.
When After Effects is installed on the machine running match_cuts, this happens automatically (stage
S7.6: After Effects opens, runs the script and saves `recreated_edit.aep`; the console says so and waits up
to `--ae-timeout` seconds, default 600) and `aerender` renders the comp for check 9.6. If After Effects
shows a dialog (*save the current project?*, or the scripting-permission alert below), answer it there.
**Ctrl+C** during that wait skips only this step and the run continues; `--no-ae` turns it off — then run
`extras/build_ae_project.jsx` (in the run folder) yourself.

## Troubleshooting

**"Could not save recreated_edit.aep"** — enable *Preferences → Scripting & Expressions → Allow
Scripts to Write Files and Access Network* (in versions before 16.1: *Preferences → General*), then run
the script again.

**`UnicodeEncodeError: 'charmap' codec can't encode character …` (Windows)** — fixed: every text file
(report, cut list, XML/EDL, logs) is written as UTF-8 whatever the system code page. Update with
`git pull` and rerun; the analysis is cached, so only the stages whose code changed are redone. On an older
copy, `$env:PYTHONUTF8 = "1"` in PowerShell before the run works around it.

**Media not found / relink** — the script looks for the media next to itself (`media/…`), then at the
absolute path recorded at export time, then opens *Locate the RAW video*. RAW files above
`large_file_bytes` (2 GB) are not copied into `media/`; they are referenced by absolute path, so keep
them where they are or relink when asked.

**VFR, start offsets, WebM/VP9/AV1/HEVC/Opus** — these are conformed automatically to
`media/raw_ae.mov` (ProRes 422 LT, same resolution and frame rate, CFR, start 0, PCM audio; H.264
CRF 12 in `raw_ae.mp4` for RAWs longer than 10 minutes) and verified (frame count + ≥ 50 PTS-sampled
frames matched by SSIM, plus — for VFR — a content check that every source frame the timing requires is
really shown, independent of the ffmpeg rule). A VFR competitor is timed on its nominal rate using the
frame displayed at each output time; millisecond-rounded timestamps (MKV/WebM, OBS recordings) are treated
as quantised so no frame is lost. ffmpeg older than 5.1 works (`-vsync 0` instead of `-fps_mode`). A
truncated or partially downloaded input is detected (decoded length vs the header) and warned about —
otherwise its missing tail would look like NOT-IN-RAW footage. The report's *Inputs* section lists every
issue found and the conform decision.

**Fullscreen shots inside a boxed edit** — detected as layout periods; those segments carry their own
`box` (the whole canvas) and are placed directly in the main comp above the Video Box (no rounded mask),
in the AE project and in the preview. A dissolve between a boxed and a fullscreen shot is recreated as
such (the fullscreen layer fades in / out above the Video Box); the detected period boundary usually falls
inside the dissolve, and those frames — like a 1-2 frame sliver where the boundary is off by a frame or
two — are logged as explained, not warned. Split-screen / picture-in-picture regions are detected and reported
but not recreated (criterion 1 becomes `pass_with_exceptions`, listed under *Anything AE can't
reproduce*).

**The run looks stuck** — long stages print a line at least every 30 s (`S5.3 refine: eval: 340/1200 tasks
done (1 min 05 s)` or `S9 verify: still running (2 min 00 s)`), so a quiet console for minutes means a real
problem. Worker processes are watched: when a worker gets no result back for 5 minutes, or a worker process
dies (for example Windows or Linux closes it because memory runs out), the run prints

```
WARNING S5.3 refine: eval: spawn pool: no result for 300 s after 412/1200 tasks - stopped the worker pool;
running the remaining 788 of 1200 tasks in this process (identical results, only slower)
```

and continues in the main process with exactly the same results. After two such stops in one run, the rest of
the run does not use worker processes at all. Worker pools are sized by the memory free for new processes -- on
Windows the smaller of the free RAM and the commit charge left, because Windows refuses allocations past the commit
limit (with After Effects holding 47 GB of a 96 GB PC, 28 workers of 1 GB each crashed with *Insufficient memory*):
about 1 GB a worker; the console says when it uses fewer. If the machine is short of memory, close other programs or run
with `--workers 2` (fewer processes, less memory). `--workers 1` never starts worker processes.
(Settings: `pool_stall_timeout_s`, `pool_max_failures`, `progress_log_s` in `config.py`.)

**Windows notes**

- Worker processes are *spawned* on Windows (and macOS); results are identical to Linux, each pool needs a
  few seconds to start, and the first start can be slower while the virus scanner checks Python. To force a
  start method: PowerShell `$env:MATCH_CUTS_START_METHOD = "spawn"` (Linux: `export
  MATCH_CUTS_START_METHOD=spawn`).
- Close the outputs in other programs before a re-run: Excel locks `cutlist.csv` and some video players lock
  `preview_recreation.mp4` / `compare.mp4`. match_cuts retries for a few seconds, then stops with
  *"cannot replace …: the file is in use … Close the program that has it open"*.
- Paths with spaces or non-English letters (`C:\Users\Žygimantas\…`) work. Keep the whole project path short
  (well under 260 characters), e.g. `C:\work\MovieRecaps`, because some Windows tools still fail on long
  paths.
- A RAW on another drive than the output folder is fine: it is copied (or referenced by its absolute path when
  it is larger than 2 GB) into the run folder's `extras\media\`.

**AE shows a different frame rate than expected** — AE sometimes misreads the rate of a file; the JSX
compares the imported `frameRate` with the exact rate from the cut list and sets
`mainSource.conformFrameRate` on any real difference (beyond AE's float32 rounding; a warning with the
drift in frames when it is more than cosmetic), and checks the frame count exactly. It never conforms to
the comp rate: a 29.97 fps source inside a 30 fps edit plays at speed 1.000. Runtime warnings are listed in
the final alert and stored in the comment of the `Recreated Edit` comp.

**Off-by-one frames in AE on some segments** — every RAW layer whose exact floor-rule slack (the distance
of its RAW positions to a frame boundary, over every frame) is below `ae_slack_tol_frames` is exported with
frame-exact time remapping (HOLD keys) in `--ae-time-mode auto`. *Phase pinned by cadence (±0.083 ms)* in
the report is information, not a problem: a 23.976 source in a 30 fps edit pins `raw_in` to a 1/6 ms window
whenever a segment crosses a pulldown slip. *AE-rule-sensitive* segments (a slack below the tolerance although
more was possible, or such layers kept in a forced `--ae-time-mode stretch|remap`) are one warning. The JSX
re-checks every RAW layer twice -- from the values AE stored and with After Effects' own `sourceTime()` -- and
switches mismatching stretch layers to frame-exact time remapping; its per-layer residuals are in
`ae_time_check.txt` next to the script. To force frame-exact remapping for every layer, re-export with
`--ae-time-mode frames`.

**A criterion failed** — start with `report.md` (*Warnings*, *Verification details*), then
`verify.json`, `debug/mapping.png` (every segment should be a straight line, every cut a jump),
`debug/scores.png`, `debug/cuts/`, `debug/verify_failures/` and the evidence in `debug/decisions.jsonl`.

| failing | usual causes and fixes |
|---|---|
| c1 coverage | a gap: frames no model explains (check `debug/low_confidence/`; lower `none_thresh` only if the frames are really in RAW); an unexplained overlap: a transition not detected (see `transition_search`, `blend_rel`) |
| c2 cuts | wrong speed snap moving the boundary (compare `speed_measured` / `speed_range` in the cut list); a missed punch-in (`punch_scale_step`, `punch_pos_step`); caption masks too small so text dominates the score (`overlay_dilate_px`) |
| c3 source frames | frame-rate interpretation (probe section of the report: nominal fps, VFR), a start-time offset (conform, `v_start_time`), a missed flip, a transform convention problem (framing numbers in the segment table vs the competitor), masks too small; frames listed as *ambiguous-identical* or *timing-tie* are allowed exceptions; a frame failing only because your RAW carries a burned-in graphic the competitor lacks is listed as a RAW-only overlay (mask it in AE) |
| c4 speed / framing | a speed left unsnapped although a common value fits (`speed_snap_tol`); animated framing simplified too coarsely (`rdp_pos_tol`, `rdp_scale_tol`); rotation below `rotation_min_deg` dropped |
| c5 audio | 'A/V offset not confirmed' (compare `cutlist.audio.av_offset` with the c5 `av_offset` details in `verify.json`), J/L cuts (per-segment `audio.in_offset_frames` / `out_offset_frames`, relative to the measured switch baseline), added music or voice-over (codes `music_dominated`, `audio_replaced`), pitch-preserved speed changes (`pitch_preserved`; AE's stretch changes pitch — apply *Time-Stretch* to the audio in AE) |
| c6 After Effects | the mock names the failing JSX step; `not_available` means Node is missing (install Node ≥ 18 to enable the mock check) |
| 9.7 determinism | a non-seeded random step or an unordered iteration in segmentation / phase solve / audio analysis; `verify.json` lists the differing JSON paths |

Stop only when everything passes or the report states precisely what is impossible (for example
"frames 612–655 are stock footage that isn't in RAW").

## Thresholds

Every threshold lives in `match_cuts/config.py` (`Config`); none is hard-coded in a module. Values
below are the defaults (tuned on the synthetic test in `tests/test_synthetic.py`); when a default is
changed, update this table with the reason.

| group | parameter | default | meaning |
|---|---|---|---|
| export | `ae_slack_tol_frames` | 0.01 | exact AE floor-rule slack (RAW frames, every frame of a layer) below which `--ae-time-mode auto` exports the layer frame-exact (AE's time resolution is unverified; FX-10, replaces `ae_min_margin_ms` = 1 ms, which measured only the interval edges) |
| export | `large_file_bytes` | 2 GiB | RAW above this is referenced by absolute path instead of copied |
| proxies | `raw_proxy_width` / `comp_proxy_scale` / `comp_proxy_max_width` | 640 / 0.5 / 640 | analysis proxy sizes |
| proxies | `proxy_budget_bytes` / `min_proxy_width` / `long_raw_s` | 3 GiB / 256 / 2700 s | long-RAW handling (sparse proxies around audio hints) |
| layout | `static_std_thresh` / `dynamic_frac_thresh` | 2.0 / 0.5 | static-pixel and video-box detection |
| layout | `overlay_dilate_px` / `overlay_resid_thresh` | 3 / 40 | overlay masks (comp proxy px / 8-bit residual) |
| audio | `audio_window` / `audio_hop` / `audio_min_conf` | 1.0 s / 0.25 s / 1.3 | coarse alignment windows and confidence (peak / second peak) |
| audio | `audio_speed_min` / `audio_speed_max` / `audio_speed_step` | 0.90 / 1.30 / 0.01 | speed-scaled windows |
| visual | `sift_nfeatures` / `raw_index_fps_short` / `raw_index_fps_long` | 500 / 10 / 3 | RAW index |
| visual | `lowe_ratio` / `ransac_reproj_px` / `min_inliers` / `min_inlier_ratio` | 0.75 / 3.0 / 12 / 0.30 | anchor verification (plus masked ZNCC ≥ `match_thresh` − `anchor_zncc_slack` 0.05) |
| refine | `match_thresh` / `none_thresh` | 0.90 / 0.60 | masked ZNCC to accept a match / below which EVERY hypothesis must score for NOT-IN-RAW; in between a frame is *unresolved* (an `uncertain` segment) unless the detail-sensitive second score (`detail_margin`) promotes it |
| refine | `identical_thresh` / `identical_mad` | 0.9995 / 0.75 | ambiguous-identical RAW neighbours |
| refine | `refine_radius` / `score_blur` / `grad_weight` | 3 / 1.0 / 0.0 | candidate window, blur sigma, gradient ZNCC weight (graded material) |
| refine | `uniform_std` / `low_conf_thresh` | 4.0 / 0.5 | dip/flash detection / low-confidence thumbnails |
| refine | `ecc_pyramid_levels` / `ecc_pyramid_min_side` | 3 / 40 px | coarse-to-fine framing measurement (proxy, 1/2, 1/4 while the template keeps 40 px) |
| refine | `line_time_tol` / `line_min_inlier_frac` | 2 frames / 0.7 | time-line-first runs: anchors within ±2 RAW frames of a snap-speed line; a track follows one line when 70 % of its points do |
| refine | `path_median` / `anchor_time_delta` / `near_miss_inliers` | 5 / 0.003 / 6 | framing-path outlier window; anchor time ambiguity; RANSAC near-misses that may only join an existing time line |
| refine | `temporal_refine_max_slope` | 0.95 | refine measures the competitor's repeat cadence only where a time line can repeat RAW frames (RAW frames per comp frame <= this) |
| segments | `speed_snap_values` / `speed_snap_tol` | 1.00 1.05 1.10 1.15 1.20 1.25 1.50 2.00 and inverses / 0.3 % | speed snapping |
| segments | `lambda_cut` / `lambda_unsnapped` | 1.0 / 3.0 | DP costs |
| segments | `punch_scale_step` / `punch_pos_step` | 0.01 / 4 px | punch-in cut detection |
| segments | `step_confirm_frames` / `lambda_repeat_cut` / `union_track_window` | 2 / 1.0 / 6 | a framing step is a cut only when the pixels confirm it on up to 2 frames per side; extra DP cost of a cut inside a competitor repeat pair; union-test trigger window (>= 3 refine tracks) |
| segments | `transition_search` / `blend_rel` | 20 / 0.5 | crossfade detection |
| segments | `framing_scale_spread` / `framing_pos_spread` | 0.3 % / 1.5 px | constant vs animated framing |
| segments | `rdp_pos_tol` / `rdp_scale_tol` / `rotation_min_deg` | 0.5 px / 0.1 % / 0.2° | keyframe simplification, rotation |
| verify | `verify_zncc` / `audio_lag_tol_ms` / `frame_exact_min` | 0.90 / 10 ms / 0.99 | Stage 9 thresholds |
| verify | `temporal_gap_ratio` / `temporal_growth_ratio` / `temporal_mag_ratio` / `temporal_shot_cc` | 2.5 / 1.5 / 3.0 / 0.8 | temporal signature: repeat vs move cluster gap, motion growth over two frames, comp vs recreation residual ratio, shot change (all but `temporal_mag_ratio` also drive refine's comp-only repeat labels) |
| verify | `verify_refit_margin` / `verify_union_frames` / `verify_excursion_frames` | 0.01 / 2 / 3 | ±1 refit margin floor (with 3 x the measured noise), c2 no-cut frames per side, excursion distance |
| verify | `verify_framing_min_samples` / `verify_framing_all_max` / `verify_low_score_margin` | 5 / 6 / 0.02 | c4 sampling, unconverged low-score rule |
| verify | `verify_overlay_raw_span` / `verify_overlay_static` / `verify_overlay_persist` | 5 / 6.0 / 0.8 | RAW-only overlay: static over >= 5 RAW frames (per-pixel range, 8-bit), different on >= 80 % of the frames |
| verify | `verify_overlay_grad_ratio` / `verify_overlay_max_frac` / `verify_overlay_comp_var` | 2.0 / 0.15 / 8.0 | RAW-only overlay: RAW edge energy >= 2x the competitor's, <= 15 % of the picture, only where the competitor plays |
| run | `pool_stall_timeout_s` / `pool_max_failures` / `progress_log_s` | 300 s / 2 / 30 s | hang protection: a worker pool with no result for this long (or a dead worker) is stopped and its remaining tasks run in the main process (same results); after this many stops no more pools; a progress line at least this often. Never part of the cache keys |

Verification constants not (yet) in `Config` (read with a fallback): crossfade alpha tolerance 0.15
(`verify_alpha_tol`), minimum audio correlation 0.3 for a valid lag (`verify_audio_min_corr`),
correlation 0.8 above which a wrong lag always fails (`verify_audio_strong_corr`), and the longest RAW
(900 s, `verify_full_rate_max_s`) whose original-rate audio is loaded for the final audio check (longer
RAWs use the 16 kHz analysis audio).

Changed defaults: none yet.

## Tests

```bash
cd tools/match_cuts
../../.venv/bin/python -m pytest -q -m "not slow"      # unit tests, each file < 60 s   (Windows: ..\..\.venv\Scripts\python)
../../.venv/bin/python -m pytest -q -m slow --runslow  # end to end on synthetic video (slow, minutes each)
```

**check-all** runs every test video in `tests/real/` through the whole tool and prints a scorecard (also saved as
`work/check-all/scorecard.json`, and appended to `history.jsonl` there, so each row shows the change since the last
run):

```bash
cd tools/match_cuts
../../.venv/bin/python -m match_cuts check-all                    # every case (Windows: ..\..\.venv\Scripts\python)
../../.venv/bin/python -m match_cuts check-all --cases deadpool   # some of them
../../.venv/bin/python -m match_cuts check-all --rescore          # score the newest runs again, no new run
```

A case is a folder with the competitor and RAW (or `case.json` naming them) and, for a video you corrected, the
answer key: your `answer.srt` and your edit's timeline (`answer_edit.xml`, or `answer_edit.json` pieces). Each row
shows the run's time, the hard checks (the run's own exit code and deliverables) and the **caption score** against
your SRT: captions reproduced exactly (the same text, starting within 2 frames of where your caption's first moment
plays in the tool's edit), word errors (where both edits play the same moment) and breaks of your caption style (over
20 characters / 4 words, a full stop or comma, a gap or overlap), with the differences by kind. Where the answer key
is your own edit (not the competitor's timeline), the **cut score** too: how many of your cut points the tool's edit
reproduces -- the RAW moment the picture leaves and the one it cuts to, both within 2 frames of your video (compared
by the RAW, so the two edits may run in another order); the ones it makes trimmed otherwise (within 0.5 s) are
counted apart -- and how much longer or shorter the tool's edit is. The end summary of a normal run shows the same
scores when the competitor is one of these videos.

The real clips the tests use live in `tests/real/` at the repository root (`zendaya/`, `deadpool/` -- the Deadpool clip
and its RAW, once `input/competitor.mp4` + `input/raw_test.mp4` --, `spiderman-school/competitor.mp4`,
`zendaya-age/`, and `video1/` ... `video4/`: four of your finished videos, made by `learn finished` -- the competitor,
the RAW (a smaller copy over 100 MB), your edit as the cut key and, where your captions could be read, your captions;
`finished/` itself is never committed), never in `input/`, so the videos you work on there do not change what the
tests check. The suite runs on Linux and Windows:
the conftest sets `MATCH_CUTS_NO_AE=1` (After Effects counts as not installed: no test, nor a CLI run it starts, ever
opens the After Effects of the machine), text is drawn with the DejaVu fonts (the system's on Linux, matplotlib's copy
elsewhere), and what needs fork / `/proc` (Linux), ffmpeg's `flite` source or OpenTimelineIO (no wheel for Python 3.14)
is skipped where missing.

`tests/test_caption_spans.py` checks the Premiere competitor captions: exact caption timing on a synthetic clip in
the real competitor's style (pop-in, highlighted word, word-by-word growth, the same word twice, `*laughs*`), the
text rules, and the acceptance run on `tests/real/spiderman-school/competitor.mp4` (every caption real words, none under 0.1 s).
`tests/test_export_xml_edl.py` checks the fixed framing (no keyframes, rotation 0, the window covered), the
`--min-move` rule (a 100 px pan in one take joins the clips, a 150 px reframe across a real cut keeps the
framing, 300 px and a 30 % zoom reframe, coverage kept with the least change), the Premiere `<center>` units,
the hard gap check (it fails S21's old values: x 42–431 uncovered), face-centred stretches, and S21 on
`tests/real/deadpool/raw.mp4` (within 50 px of the hand-fixed Position 1083, no clip leaving a gap) and
`tests/test_broll.py` the B-roll-follows-the-audio default (trimmed audio under a cutaway, music, glitches, dips).

`tests/test_restyle.py` runs the restyle on `reference/plain_captions.prproj` and checks that the result
matches `reference/popw_reference.prproj` caption for caption, passes `capverify.py`, keeps the donor's pop timing
to the tick and changes nothing else; also a project without a style item, a donor on another track, a trimmed
donor, a capverify failure (nothing written), numbers keeping their dots and commas, and the CLI under a locale
that is not UTF-8 (as on Windows).

`tests/test_broll.py` checks `--no-broll` on synthetic audio: three cutaways over the main clip's continuing
RAW audio (B-roll from the RAW, a NOT-IN-RAW insert, a 2-frame flash) are replaced and joined into the main
clip, one over music is kept, the faithful cut list is untouched, the Premiere XML validates with a marker
per replaced spot, and the report lists them with timecodes.

`tests/test_captions.py` checks the caption rules against the ten reference SRTs in `srt/` (their style
statistics, a byte-exact SRT round trip, regrouping their words: ≥ 80 % of the captions come out exactly,
every rule holds, the 29 weak endings are fixed except 4 that a rule keeps); `tests/test_caption_ocr.py`
reads a short synthetic clip with burned-in captions (pop-in, colour highlight, the same word twice, static
text) and checks every caption's text and first / last frame.

`tests/test_synthetic.py` builds a synthetic RAW and a competitor made with ffmpeg filtergraphs (jump
cuts, an out-of-order hook, a re-used moment, a 1.10× segment, a flipped segment, a push-in, a punch-in,
a 6-frame crossfade, a 1 s NOT-IN-RAW insert, captions, title, logo, music) and asserts that the whole
CLI recovers the known edit exactly.
