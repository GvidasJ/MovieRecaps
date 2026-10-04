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
`opencv-python`; it works with the contrib wheel above). faster-whisper downloads its model (`small.en`,
~0.5 GB) from Hugging Face on the first run and caches it. Without these packages the run still completes
and the report says why `2_captions.srt` is missing.

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

Extra flags: `--input-dir DIR` (auto-detection folder, default `./input`), `--seed N`,
`--skip-preview`, `--skip-compare`, `--no-swap`, `--no-ae`, `--ae-timeout SECONDS`, `--version`.

Captions (see *Captions* below): `--captions auto|competitor|voice` (default `auto`), `--voiceover FILE`,
`--caption-model NAME` (default `small.en`), `--caption-recheck-model NAME` (default `medium.en`, `none` = off).
Premiere-only export: `--premiere`. Cuts never inside speech, silence removal (Premiere export and RAW-only runs):
`--pad-before S` (default 0.05: a clip starts this long before its first word), `--pad-after S` (default 0.15: a clip
ends this long after its last word), `--keep-silence`, `--min-silence S` (default 0.3), `--silence-db DB` (default: set
per video from its speech level and background noise; DB under the speech level overrides it). Repeats of RAW footage
or audio (see *Premiere* below): `--allow-repeats` keeps a moment over 0.5 s that plays twice. No `--competitor`: the
edit from the RAW alone (see *Without a competitor* below).

`--no-broll`: where the competitor cuts away (B-roll from your RAW or not in it) while the RAW audio keeps
playing, the export shows the RAW video that matches the audio instead, so the main clip plays through (see
*B-roll cutaways* below).

### Premiere (`--premiere`)

`1_edit.xml` is a 1080×1920 sequence at exactly 60.00 fps with the edit on V1, the RAW audio on A1 and
V2 and above empty. Every clip on V1 and A1 is simply `raw.mp4`: the same name, the same file and one shared master
clip, so Premiere's Project panel shows a single `raw.mp4` all the timeline clips are cut from (the RAW is always
copied as `extras/media/raw.mp4`, whatever the input file is called; the competitor is never in the project). The
segment ids (`S01+S02`) are in each clip's comments and in the markers. Two defaults of this mode (config
`premiere_static_framing` / `premiere_follow_audio`):

* **No camera movement.** Every clip holds one fixed Position and Scale — no keyframes on Position, Scale or
  Rotation, rotation 0. It is the competitor's framing for that clip (averaged over the clip when the competitor
  pans or zooms), scaled up only as much as needed and moved the least so it fully covers the template window
  (x 42–1039, y 555–1591).
* **Fewer reframes and cuts (`--min-move`, default 250).** After the fixed framing, a clip takes its own framing
  only when it is 250 px or more from the framing on screen, measured in the 1080×1920 sequence as the biggest
  movement of the picture's centre or of one of its edges (so a zoom counts by how far the edges move). Below
  that it keeps the previous clip's framing exactly — across real cuts too — changed only as little as needed if it
  would leave part of the window uncovered. Neighbouring pieces of one continuous RAW take (the next starts on the
  very source frame the previous ends on, same speed, no transition) that end up with the same framing become one
  clip, with no cut on V1 or A1; a jump in RAW time stays a cut. Each clip's comment says when its framing was
  kept from an earlier clip and which pieces it joins. `--min-move 0` gives every piece its own framing.
* **Face-centred where the competitor's framing cannot be used.** A stretch of clips sharing one framing that holds
  a replaced B-roll / NOT-IN-RAW / uncertain spot (its framing was only copied from a neighbour), or whose framing
  would leave part of the window uncovered, keeps its zoom and height but is moved sideways so the main person's
  face sits at the centre of the window: OpenCV's face detector (the cascades are in `match_cuts/face_models/`) on
  frames from the whole stretch, the main face being the largest one inside the view, the median of its position
  used. No face found: the framing is kept, moved only as needed to cover. `--min-move` applies again afterwards.
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
  (music, voice-over) the previous RAW clip keeps playing, with no RAW audio under it. V1 is never left empty; a
  `B-ROLL REPLACED` marker sits on every replaced spot and report.md lists them with timecodes. A RAW shot whose
  picture is within 1 s of its own audio (an A/V shift) is the main clip and stays as it is.

### Cuts never inside speech (every Premiere export and RAW-only run)

A cut must never interrupt speech, whatever the competitor did (`match_cuts/speech.py`). Before the silences are cut,
every audio cut of the edit is placed by the speech of the RAW, not by the competitor:

* **Speech map of the RAW.** A sound is the 50 ms loudness at or above this video's silence threshold, with its soft
  start and end (the windows next to it still 3 dB over the background, at most 0.2 s: the soft "s" or "-ty five"
  a word starts or ends with). The words come from the RAW where the edit plays it (±3 s), transcribed with the
  recheck model (`--caption-recheck-model`, default `medium.en`; its timings are much closer than `small.en`'s), and
  the captions' model (`small.en`) as a second opinion. A dip in the loudness inside a word (the closure of a "t")
  is part of the word; the dip nearest each boundary between two words (within 0.25 s — the timings are often that
  far off) is the gap between them, however short. A sound either transcript heard a word in, or with a clear pitch
  for 0.05 s, is speech — "okay", "uh" and every other filler included; a sound with neither (a breath, a lip
  smack) is not speech, so a clip's start or end may leave it out, but no cut lands inside it either. Without
  faster-whisper every sound counts as speech.
* **A clip ends `--pad-after` (0.15 s) after its last word has completely finished and starts `--pad-before`
  (0.05 s) before its first** — inside the quiet there: a pause shorter than both pads is split between them, a
  breath right after the word stops the clip at the breath. A competitor cut that falls inside speech moves to the
  nearer end of that sound: the clip plays on to the end of it (it is extended, and everything after it moves
  later) or stops before it. A clip never shows again what the clip before it now shows: it starts after it, and a
  clip left with nothing new to play goes. Two pieces that end up playing one continuous take become one clip, with
  no cut. Both sides of a cross dissolve stay as they are. Where A1 jumps a few frames (at most 0.1 s) inside
  speech but the picture does not cut (an audio line), the audio line moves by those frames so A1 plays on.
* **A tiny jump at a cut inside speech plays on.** The competitor often cuts 1–3 frames out of a sentence (or shows
  them twice): a cut skipping or repeating at most 0.1 s of the RAW inside speech is closed — the clip before plays
  on to where the next one starts, or the next starts where the one before ends — so the sound never jumps; the
  picture still cuts there (a framing change). An audio line (sound that is not the picture's own) moves by the jump
  instead, along its whole chain, so the picture is not touched.
* **The hard check, on the final XML**: every audio cut of A1 (an item's start or end where the RAW does not play on)
  must land outside speech; one that lands inside fails the run (`XML SPEECH` in the report and the console, with
  the words there). On run 011 the competitor-placed cuts fail it 10 times; the new export passes.
* The end summary lists every cut that was moved (*Cuts moved off speech*: the clip, which edge, by how many frames,
  RAW before → after, the words there) and every clip removed because the clip before it now plays it;
  `extras/report.md` has the same list.

On run 011 (the files at the repo root), 9 of the 12 cut edges I moved by hand (leaving out where I dropped
"Okay", "Uh", "to" and the 1.5 s before "I have a daughter") come out within 2 frames of mine. The other three: I
kept 0.39 s after "age." and 0.04 s after "am" where the tool keeps `--pad-after` 0.15 s, and I ended S08b just
before "to" to drop it, where the tool keeps "a son to a married couple" playing.

### Silence removal (`--keep-silence` turns it off)

* **Silence across a cut, too.** The end of one clip and the start of the next together never keep more silence
  than `--pad-after` + `--pad-before` (0.2 s): longer, both sides are trimmed, however short the pause (the 0.3 s
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
  video's pauses are cut too, and loud and quiet recordings need no setting: on `input/raw_test.mp4` (speech
  -16.2 dBFS, background -42.2 dBFS) it is -33.1 dBFS. `--silence-db DB` replaces it with DB under the speech level
  (e.g. `--silence-db -20`).
* **Never inside a word**: the edit's audio is transcribed (word timings, the same `small.en` model as the captions,
  cached) and a cut only falls in a gap between two words. Each word's timing is trimmed to its audible part (6 dB
  over the background), so a timing that runs on into the pause does not keep the pause. Of each gap,
  `--pad-after` (0.15 s) after the word before it and `--pad-before` (0.05 s) before the word after it
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
is replaced only between two shots of the same line. When the RAW audio does not continue (music,
voice-over, the cutaway's own sound) the cutaway stays as the competitor has it.

Only what you import changes: `1_edit.xml` (with a `B-ROLL REPLACED` marker on every spot),
`recreated_edit.edl` and `cutlist.csv` (`debug/cutlist_no_broll.json` holds the export cut list).
`cutlist.json`, the preview / compare renders and the verification stay faithful to the competitor, so the
checks still prove every cut. The report's *B-roll cutaways* section lists every replaced and kept cutaway
with its competitor and 60 fps sequence timecodes.

### Captions

`2_captions.srt` is written on every run, timed frame-exactly on the 60.00 fps Premiere sequence
(competitor frame k = sequence frame 2k for a 30 fps competitor). The mode is chosen per clip:

* **competitor** (auto, when the layout finds burned-in captions; with or without `--premiere`): the competitor
  decides the **words** and **where captions split** where they already show 2+ words that pass my rules (the
  timing: see *Timed to the speech* below); my rules decide **how the
  text looks** (the hard rules below). Where the competitor shows **one word at a time**, the words are regrouped
  into 2–4 word captions by the voice-mode rules below (20 characters, pairs kept together, never a lone weak word)
  on the competitor's timing: `a` | `joke` → `a joke`, `Bronx` | `School` → `Bronx School`; a word left between
  two kept captions joins one of them, an interjection the competitor shows alone stays alone, and quoted words
  shown one by one get one pair of quotes (`“so dude what's”`). The caption band is read on
  every frame (the caption's fill colour is learned from the video, static title / logo / watermark text is
  masked): a new caption starts on the frame different words appear; a pop-in (the text growing over its first
  frames) or a word highlighted in another colour is not a new caption, the same text popping in again is. The
  text read is the majority of RapidOCR's readings of the caption's fully grown frames. The cut edit is also
  transcribed (word timings): a caption is split where a sentence ends or the speaker changes, at the word's own
  time; names follow the transcript (`SPIDER-MAN` → `Spider-Man`, `WAS` → `was`); full stops and commas go; a
  garbled reading (`We wre`) takes the word the transcript clearly heard (`We're`) and is listed. Speech the
  competitor left uncaptioned stays uncaptioned; a caption the OCR cannot read takes the words heard while it is on
  screen (listed).
* **voice** (only when the competitor has no burned-in captions): the cut edit's audio (RAW audio on the edit's
  cuts, never the raw clip) is transcribed with word timestamps (faster-whisper) and grouped by the rules of
  `caption-generator-prompt.md` at the repository root: 1–4 words, a new caption after 4 words / 20 characters / a pause > 0.25 s / at a
  standalone interjection, a word said again gets its own caption, names / number + unit / negation +
  verb kept together, a weak final word moved to the next caption (once per caption — the prompt's
  own example keeps "there is"), no full stops or commas (except inside numbers), back-to-back timing,
  `*...*` placeholders for silences over ~1 s, a new caption after every sentence end. `--voiceover FILE` captions
  your own narration instead.

**Timed to the speech of the final edit, both modes** (`captions.py`). Every caption starts within 2 frames of
its first word being spoken in the final edit — after every cut change (speech-safe cuts, padding, silences,
repeats). The captions' text is aligned, as one stream, with the RAW's words (medium.en) where A1 plays them, then
with the edit's own transcript; the first word's start moves to where its sound starts in the loudness (within
0.2 s; past an audio cut a word cannot straddle). A caption moved past its own end keeps its length, and
captions that were back to back stay back to back. Competitor mode keeps the competitor's words and splits on this
timing. A competitor caption whose words my edit does not play (the competitor's own audio, e.g. under a cutaway)
is left out, and speech of my edit no competitor caption covers is captioned from the transcript; both are listed.
The end summary lists every caption that is still off (*Captions off their first word*).

**Stutters**: the same short word said twice in a row inside one caption (`The the one that's`, `I I`, `a a`,
`to to`) is kept once (`The one that's`) and listed (*Caption stutters kept once*); a word repeated as separate
captions (`no` | `no` | `no`) is deliberate and stays.

**Grouping, both modes** (`captions.py`):

* **Never across a video cut**: the cuts are the V1 clip boundaries of the run's `1_edit.xml` (not where the same
  take simply runs on). A cut inside a caption splits it at the word boundary nearest the cut and the caption
  changes exactly on the cut's frame (`I` | `suggested`, `that I was` | `not a real student`); when two boundaries
  are about as near (within 0.1 s), the one outside a pair kept together wins (`my secret` ends on the cut rather
  than `my` | `secret`). This beats the lone-weak-word rule: a weak word or preposition cut off from its phrase
  stands alone right before the cut (`the school` | `for` | `genius kids`). A short clip with no speech between
  two cuts stays uncaptioned (no caption may stretch over it). In competitor mode a pause the transcript hears
  (over 0.25 s) also starts a caption (`next` | `to quite`).
* **Never a single weak word** (`a`, `the`, `to`, `of`, `I`, … — the weak-word list) **or preposition** (`about`,
  `at`, `from`, `into`, `with`): it joins the word(s) after it (`I` | `know` → `I know`); before a silence, a
  sentence end or an interjection it joins the caption before it; right before a video cut it stays alone.
  A caption of nothing but function words (`without the`) joins the next words whole.
* **Short captions**: a caption over 16 characters (my SRTs: median 11, 90% at 17 or less) splits at a natural
  break, the most even one: before a verb phrase (`what you're` | `talking about`, `they would` | `bring me up`;
  only with two words on each side, so `You're gonna lose` stays), before a preposition's phrase (`suggested` |
  `to Marvel`; never before `of`: `lost track of time` stays), before a new clause — `what`, `when`, `where`,
  `why`, `how`, `who`, `because`, `if`, and `that` when a clause follows it (`no idea` | `what you're`; not `that
  place`) — and around `and then` / `so then` / `but then`, which start their own caption (`a pretty girl` | `and
  then` | `she's like`). Never a split that leaves a lone weak word, preposition or subject. A caption that would
  end on a new clause's first words gives them to the next caption when they fit there (`I don't know if` | `he is
  coming` → `I don't know` | `if he is coming`).
* **Never split** (unless the 20-character / 4-word cap leaves no choice): `a` / `an` / `the` / `this` / `my` /
  `your` + the word after it (`a joke`, `the school`; the adjectives and the noun too: `a pretty girl`, `a high
  school`), a pronoun + its verb (`I know`, `we went`, `you are`), a verb + its preposition (`talking about`,
  `looking at`; not `to`), `and then` / `so then` / `but then`, a preposition + its object (`of Science`, `to the
  front`), short set phrases (`no idea`, `I know`, `you know`, `I mean`, `of course`, `thank you`), names of
  two or more capitalised words (`Bronx School`, `Bronx High School of Science`), a name, number + unit, negation +
  verb, and every phrase in `caption_allowlist.txt`. No pair reaches across a pause over 0.25 s, a comma or a
  sentence end. A weak last word moves to the next caption with the words kept together with it (`to one of
  the` | `songs` → `to one` | `of the songs`).
* **Capitals**: only `I`, names, acronyms and the first word of a caption after a real pause in speech (over
  0.5 s: the transcript's gap, else the gap before the caption). A sentence the transcript starts with no pause
  before it stays lower case (`joke` | `And` | `Marvel` → `and Marvel`). Names: the transcript's capitals in
  mid-sentence (`School`, `Science`), words the word list only writes capitalised (`Bronx`, `Parker`), unknown
  capitalised words (`Keanu`), and a capitalised word next to a name.

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
| 8 no gaps | `end[i] == start[i+1]` | voice mode: closed (on the cut when a video cut falls in the gap) | competitor mode keeps the competitor's silences; a clip with no speech between two cuts stays uncaptioned |
| 9 kept together | no pair kept together split between two captions | competitor mode: regrouped (one word at a time) | split only where the cap or a video cut forces it |
| 10 video cuts | no caption across a cut of `1_edit.xml` (V1 clip boundaries) | split on the cut / its edge moved onto the cut | — |

**Acronyms** (`caption_allowlist.txt` next to this README; one word or phrase per line, extend it): `AI`, `MJ`, `MCU`, plus
the acronyms the word list writes in capitals (`FBI`, `NASA`, `TV`); never a word that is also an ordinary word
(`AS`, `WAS`, `IT`). Words listed there are written exactly as listed (also `iPhone`, a name or a deliberate
misspelling the word list does not know); a phrase there is never split across captions. The word list is SCOWL (`match_cuts/wordlist/`, see its licence file).
The end summary says how many captions each rule changed or flagged (*Caption rules*), and *Captions worth a look*
lists every flag, every word taken from the transcript and every piece of screen noise left out.

**Unclear speech is double-checked against the RAW** (voice mode, and the transcript fallbacks of competitor mode).
A word the transcription of the edit is unsure about — heard with low confidence (mumbling), with music or noise
under it (less than 12 dB above the sound bed around it), or with an edit point cutting into it — is transcribed
again from the RAW footage the edit plays there (the edit's own audio map: J/L cuts, speed changes and audio lines
are followed), with 3 s of context on each side so the model hears the whole sentence rather than the cut piece, by
a bigger model (`--caption-recheck-model`, default `medium.en`, downloaded once; `none` turns it off) for these
spots only. The RAW's words are mapped back onto the edit's timeline (a word cut off at an edit point keeps the part
the edit plays) and the two versions are compared word by word: where they agree (or say the same words two ways,
"gonna" / "going to") the word is confirmed; where they differ the RAW's version is used when it is clearly more
confident, and a competitor caption read clearly at that spot is a third opinion that decides when exactly one
version agrees with it, together with the words on either side. RAW windows that overlap are transcribed once. Nothing is guessed: a spot still unsure after that keeps the best version
and is listed under *Captions worth a look* with its time and the alternatives heard (edit, RAW, caption). The end
summary says how many words were rechecked and how many changed; the report lists every change. With
`--voiceover`, the voice-over file itself is the source.

The report's *Captions* section lists, in competitor mode, the competitor's writing conventions, the captions
written from the transcript because they could not be read and the readings the OCR was unsure of; in voice mode,
the style check, captions at the 24-character cap, the `*...*` timecodes and possible mis-transcriptions / doubled /
missing words — flagged, never corrected; in both, the hard-rules table (changed / flagged per rule) and every row
the rules listed. Speakers are not told apart by voice (faster-whisper has no diarisation): rule 2 relies on the
sentence ends the transcript hears. faster-whisper is used instead of WhisperX because WhisperX needs
PyTorch and an alignment model, a heavy and fragile install on Windows; faster-whisper installs with pip
alone and gives word timestamps.

**A/V offset.** Many short-form edits play their sound a little early or late against the picture (for
example −85 ms). match_cuts measures this shift **once per run** (`cutlist.audio.av_offset`) and the report
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
| `1` | an acceptance criterion or a Stage 9 check (incl. `9.8 deliverables`) failed | `FAIL` |
| `2` | the run itself failed: missing/ambiguous inputs, a crashed stage (see `extras/match_cuts.log` in the run folder) | none (`match_cuts: ERROR: …` on stderr) |
| `3` | nothing failed, but a criterion could not be verified (`not_available`, e.g. no Node.js for the JSX mock) | `PASS (criterion 6 not verified: …)` |

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
  9.7 determinism                PASS   cutlist re-assembled from caches is byte-identical
  (PASS* = passed with listed, explained exceptions)
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

## Outputs

Each run gets its own numbered folder in `--out` (`output\001`, `output\002`, ...: the next free number, so a
new video never overwrites the previous one). At its top only the files you use, numbered in the order you use
them; everything else in `extras\`. The console ends with a short summary: these paths, what to check by hand
(the `B-ROLL REPLACED` spots, the uncertain / NOT-IN-RAW / retimed spots with their sequence timecodes, the
captions worth a look) and the run folder.

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
(`--layout`, `--comp-size`, `--fps`, `--ae-time-mode`) never recomputes the analysis. To force a stage to
recompute, delete `work/cache/<stage>/` (stage versions in `common.STAGE_VERSION` invalidate caches
automatically when an algorithm changes).

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
| 9.7 determinism | segmentation → phase solve → audio → cut list re-run from the cached FrameMap/AudioHints in a fresh context; canonical JSON (without `provenance.timings`) must be byte-identical. When the previous run's `cutlist.json` came from the same inputs, parameters and tool/stage versions it is compared too (a difference fails) |
| 9.8 deliverables | every file of the deliverables tree exists (unless explicitly skipped, e.g. `--skip-preview`, or the `.aep` without AE), XML/EDL re-parse validation passed, no stage error |

Statuses: `pass`, `pass_with_exceptions` (every exception listed and explained), `fail`,
`not_available` (e.g. no Node for the mock, no AE for aerender).

Exit codes (the table under *Usage*): `0` everything passed; `1` a criterion or check failed; `2` the
run itself failed (bad inputs, a crashed stage); `3` nothing failed but a criterion could not be verified
(headline `PASS (criterion 6 not verified: …)`, e.g. Node.js missing so the JSX was never executed).

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
`git pull` and rerun; the analysis is cached, so only the exports and the report are redone. On an older
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
the run does not use worker processes at all. If the machine is short of memory, close other programs or run
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
../../.venv/bin/python -m pytest -q -m "not slow"      # unit tests, each file < 60 s
../../.venv/bin/python -m pytest -q tests/test_synthetic.py   # Stage 1 end-to-end (slow, minutes)
```

`tests/test_caption_spans.py` checks the Premiere competitor captions: exact caption timing on a synthetic clip in
the real competitor's style (pop-in, highlighted word, word-by-word growth, the same word twice, `*laughs*`), the
text rules, and the acceptance run on `input/competitor.mp4` (every caption real words, none under 0.1 s).
`tests/test_export_xml_edl.py` checks the fixed framing (no keyframes, rotation 0, the window covered), the
`--min-move` rule (a 100 px pan in one take joins the clips, a 150 px reframe across a real cut keeps the
framing, 300 px and a 30 % zoom reframe, coverage kept with the least change), the Premiere `<center>` units,
the hard gap check (it fails S21's old values: x 42–431 uncovered), face-centred stretches, and S21 on
`input/raw_test.mp4` (within 50 px of the hand-fixed Position 1083, no clip leaving a gap) and
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
