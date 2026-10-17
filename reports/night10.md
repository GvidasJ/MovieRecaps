# Night 10 (17 October): download, convert, channel templates, the watermark, the title check

## In short

Everything on your list is done, tested with your three channels and real YouTube videos, committed one item at a
time and pushed. In one line each:

1. **Media copy fix:** every run folder now always gets its own media in `extras\media` -- a hard link on the same
   drive, else a **copy** (another drive, a network folder, a USB drive), with "copied, not linked: <reason>" in the
   log; not enough room stops the run in plain words, and **the app warns before the start**.
2. **Download tab:** paste the competitor's and the RAW's links (or drop the RAW file), pick the channel, type the
   title; videos queue up; yt-dlp (in the .venv, updated daily) takes the **best H.264 + AAC MP4** (VP9 / AV1 only
   when bigger); a progress bar per file; **errors in plain words**; one folder per video in
   `input\downloads\<date>_<short name>` with `project.json` for part 2.
3. **Convert:** after every download, and a **Convert tab** for any video: H.264 kept or **rewrapped** to MP4
   (lossless), VP9 / AV1 / others to **visually lossless H.264**; each file **checked (frames and length) before the
   original is deleted**. Measured: the graphics card needs twice the file size for the same quality, so the
   processor is the default.
4. **Channel templates:** LouLaughs, MikeMemesYou, Flick707 from their PSDs (a new channel = a new folder); **your
   headline drawn in the PSD's exact style** -- **97-98 % pixel overlap with Photoshop's own headline** on every
   PSD (your aim: 94 %), the full 122 pt size, the natural 2-line break, shrunk only when nothing fits (and said so);
   live preview in the app; the template replaces the frame on V2.
5. **Watermark:** on its own track above the template, Scale 50 %, Opacity 75 %, **Blend Mode Overlay -- checked in
   Premiere Pro**: XML's "overlay" comes in as Normal, so the tool writes FCP's own name for it, `texturize`, which
   Premiere imports as Overlay (read back from the saved project).
6. **Title check:** the competitor's headline is read from its video (right on all 30 competitor files here) and
   shown next to your title; **a warning at 80 % of the same words**, in any order.
7. **Tests:** check-all of all 23 videos -- **exactly night 9's edits, captions and scores**; the tool's tests
   (1367, with the graphics card and the slow ones) and the app's (285) all pass; **a real run through the app with
   three real YouTube Short + source pairs, one per channel** (section 7).
8. **On the laptop:** `git pull`, then `.venv\Scripts\python tools\app\setup_app.py` (installs yt-dlp, psd-tools
   and deno if missing).
9. The PC was shut down at the end, as you asked.

## 1. Every output folder keeps its own media (item 1)

**What was wrong:** a RAW bigger than 2 GB on another drive than the output folder was not put into
`extras\media` -- 1_edit.xml pointed at it where it lay (the old `large_file_bytes` rule). Moving or deleting the
input then broke the edit.

**Now:** every run folder always gets its own media in `extras\media`, whatever the size:

* **a hard link** when the run folder is on the same drive as the video (instant, no extra space -- as before);
* **a copy** in every other case: another drive, a network folder (like `D:\Shared` over Wi-Fi), a USB drive (FAT /
  exFAT cannot hard-link). The run's log says it in one line, for example:
  `copied, not linked: another drive (C: -> D:): The system cannot move the file to a different disk drive`.
* **Not enough free space for the copy:** the run stops at once with that said (the sizes, the drive, what to do),
  and no half-written file is left behind.
* **The app warns before the start** when a drive has no room for the run folders going there (the runs to one
  drive are added up: the copies of the RAW and the competitor when they cannot be linked, plus about 1 GB for the
  run's own files).

**Tested:** unit tests (a link that works, a link that fails -> a copy with the reason, not enough space -> a plain
stop, the reasons for another drive / a network folder / a drive that cannot link); a real check of the rule on
C: (linked) and D: (copied, the reason logged; a second run finds its own copy and does not copy again); and a
**real tool run with its run folder on D:** (your video10 test case from C:): both videos copied into its
`extras\media`, the log line there, every check PASS, and the batch's own check "media outside the run folder"
empty. The network share `\\<this PC>\Shared` could not be written from my login here (the share is open to the
account `render` only -- probably the laptop's login), so the network case is covered by the unit test of the rule.

## The new flow in the app (items 2-4)

The app now has four tabs, in the order of the work: **Download | Convert | Template | Match cuts** (it opens on
Download). Pictures: `work\t25\app_*_tab.png` (each tab empty), `work\t25\app_shots\template_*.png` (the Template
tab with each channel, a shrunk title and a title too close to a real competitor's headline),
`work\t25\e2e\run_snl\*.png` (the real test while it ran).

**One video, start to end:**

1. **Download tab:** paste the competitor's link and the RAW's link (or drop the RAW file, or paste its path), pick
   the channel, type the title (or later), press **Add to the queue**. Add as many as you like.
2. The app reads both links (the competitor's title names the folder), checks there is room on the drive, and makes
   the video's **project folder**: `input\downloads\<date>_<short name>` (for example
   `input\downloads\2026-10-17_a-lot-to-catch-up-on`) with `project.json` (the state of every step) and `logs\`.
3. **Download** of both files, each with its own progress bar ("34 % -- 12.3 MB of 80.1 MB, 4.1 MB/s, about 0:14
   left"), then **convert** of both (section 3), then the competitor's **headline** is read and your title checked.
4. With **"Then make the edit by itself"** (on by default) the video joins the **Match cuts** queue and the queue
   starts by itself: your channel's template with your title is made (`template.png` in the project folder) and the
   edit runs with it as the frame and the channel's watermark on top, into `edit` in the project folder (`edit-2`
   when run again). A channel without a title waits in the Match cuts queue until you type it.
5. Every Match cuts row has a **Template** button (the same editor as the Template tab, with the competitor's
   headline next to your title) and a line saying which template and title the edit will use.

## 2. Download (item 2)

* **yt-dlp is installed in the .venv** (2026.08.19, the newest) and **updated by the app at most once a day**,
  before a download (pip, quietly; offline it keeps the installed one). On the laptop `setup_app.py` installs it.
  YouTube needs a JavaScript program for yt-dlp: this PC's deno is used (on the laptop, setup installs deno into the
  venv when there is none).
* **The format** is chosen by the app itself, testable: the **best H.264 + AAC in MP4** (most pixels, then frame
  rate, then bitrate); **VP9 / AV1 only when that version is bigger** than every H.264 one (more pixels, or the same
  at a clearly higher frame rate) -- then it is converted (item 3); SDR before HDR; the **original language sound**
  before YouTube's automatic dubs (one test video had an English auto-dub listed first); the picture and sound in one
  file only when nothing better is there. Each project's `project.json` says what was taken and why ("H.264
  1080x1920 30 fps (2.7 Mb/s) + AAC 130 kb/s -- the VP9 version is not bigger"; a 4K video: "VP9 3840x2160 --
  bigger than any H.264 (best H.264: 1920x1080)").
* **A progress bar per file** with plain text ("34 % -- 12.3 MB of 80.1 MB, 4.1 MB/s, about 0:14 left"), **Stop**
  (gone in a third of a second, partial files removed), the whole yt-dlp output in the project's `logs\`.
* **Errors in plain words** -- tried for real tonight:

  | the link | the app says |
  |---|---|
  | `youtube.com/watch?v=thisisnotvalid` | the link is wrong: a YouTube video's id has 11 letters and digits, this one is 'thisisnotvalid' |
  | a private video | this video is private: only its owner (and people they shared it with) can watch it |
  | a removed video | this video is not available: it was removed or made unavailable (or the link is wrong) |
  | `example.com`, a Google search | the link is wrong: there is no video at it (check that you copied the whole link) |
  | `notaurl` | the link is wrong: 'notaurl' is not a web address (it should start with https://) |
  | a channel's page (`@LouLaughs/shorts`) | this link is a playlist or a channel, not one video: open the video and copy its own link |

  and, tested with yt-dlp's real messages (they could not be made to happen here): **"this video is blocked in your
  country (region blocked)"**, age-restricted, YouTube's "are you a bot" check (wait and try again), members only,
  a live stream not finished, YouTube limiting downloads, no internet, an out-of-date yt-dlp (403), no JavaScript
  runtime, the disk full, DRM.
* **A RAW file instead of a link** (drop it, choose it, or paste its path): it goes into the project folder -- a hard
  link on the same drive (instant, no space; your file stays where it is), else a copy ("copied, not linked: another
  drive").
* **Real downloads tonight** (YouTube did not block this PC once): three of LouLaughs' newest Shorts with normal
  videos as RAWs (by the helper), and the three SNL pairs of the end-to-end test: a 111 s Short in 3.8 s, its
  13-minute RAW (119 MB) in 8.5 s; a 4K VP9 RAW (1.08 GB) in 51 s.

## 3. Convert (item 3)

Automatic after every download (both files), and the **Convert tab** for any video you drop:

* **H.264 or HEVC with AAC in an MP4: kept as it is** (nothing to do). This is what YouTube's H.264 downloads are.
* **H.264 / HEVC in another container** (MKV, WebM, MOV ...): **rewrapped** to MP4 -- the picture copied bit for bit
  (100 % lossless); Opus / AC-3 sound made AAC (Premiere reads them badly).
* **VP9, AV1, VP8, MPEG-2, DivX, WMV ...: converted to visually lossless H.264**: x264 CRF 13 (preset slow), the
  same size, frame rate and frames (each frame keeps its time), the colour tags copied, the sound copied (AAC) or AAC
  320 kb/s (5.1 stays 5.1).
* **Checked before the original goes:** the same number of frames, the same length (within a frame; the sound
  within 0.15 s), the same size and rate. Only then is the original deleted and the new file given its name with
  `.mp4`. A failed check keeps the original and removes the bad copy (said in plain words).
* **Graphics card or processor -- measured, as you asked ("the GPU if the quality is the same, else CPU"):** three
  real samples (grainy film from your video7 RAW, a VP9 Short, a 4K VP9 YouTube RAW), each encoded both ways and
  compared with its source frame by frame (SSIM, PSNR, VMAF):

  | sample | x264 CRF 13 (processor) | NVENC at about the same size | NVENC at the same quality |
  |---|---|---|---|
  | film 1080p | 7.0 Mb/s, PSNR 55.45 dB, VMAF 98.81 | 8.5 Mb/s (1.2x): **1.3 dB worse**, VMAF 98.37 | cq 17: 12.4 Mb/s = **1.8x the size** |
  | Short 1080x1920 (VP9) | 6.2 Mb/s, 53.31 dB | 7.2 Mb/s (1.2x): the same | cq 17: 14.5 Mb/s = **2.3x the size** |
  | 4K (VP9) | 34.3 Mb/s, 55.49 dB | 32.6 Mb/s: 0.14 dB worse | about **1.7-1.8x the size** |

  The graphics card reaches the same quality only at about **twice the file size**, so **the processor is the
  default** ("Auto"). The Convert tab can still choose the graphics card (NVENC p7, cq 17: never worse than the
  processor on these samples, about twice as big). Not much faster here anyway: 4K encodes ran at about 20 frames a
  second on the processor and 35 on the graphics card (both under load) -- an 11-minute 4K VP9 RAW takes about 17
  minutes to convert. YouTube offers H.264 up to 1080p, so this only happens for RAWs bigger than 1080p (or with
  no H.264 at all).

## 4. Channel templates with your headline (item 4)

**What you get:** pick the channel and type the title (emojis allowed); the template is drawn live in the app and,
for the edit, written as a 1080x1920 PNG that replaces the frame on V2 (the channel's watermark goes above it, item
5). A new channel is a new folder `assets\channels\<Channel>\` (`template.psd` + `watermark.png`); the three
channels also have a `channel.json` with their link for part 2.

**The base (the card without the example headline):** your rule checked on all three PSDs: with the headline layer
hidden, the area x 150-2010, y 740-1035 is plain white -- 548,700 of 548,700 pixels white and opaque -- so the base
is the PSD's merged image with that rectangle painted white (the fallback to drawing the visible layers without the
text layer is there for a future PSD that is not white there). The video window keeps its transparency: on the
1080x1920 PNG the tool's own frame finder finds it at x 22-1057, y 527-1736, fully transparent inside, fully opaque
around. Flick707's merged image shows Flick707 (its SceneProof layers are hidden), as you said. The base is cached
(`work\channel_cache`; a changed PSD is read again by itself).

**The headline, in the PSD's own style:** NeueMontreal-Bold 122 px (122 pt at the PSD's 72 ppi), Faux Bold,
tracking +10, black, centred on the type layer's own anchor (x 1085.46), line 1's baseline at 849 and line 2's at
996 on the PSD = **424.5 and 498 on the 1080x1920 sequence**, letters **46 px** tall there. What Photoshop's Faux
Bold does was measured on your PSDs (every letter's outline grows 1.5 px and every character moves 3.29 px further)
and the font's own kerning is used (Photoshop's "Metrics" kerning; Pillow cannot do either, so the letters are drawn
from the font's outlines). Emojis are Noto Color Emoji -- the 2021 version (v2.034), which has the same drawings as
Photoshop's emoji font in your PSDs (the newest one redrew 😭 and the hands) -- each sequence one picture, as in
Photoshop (👮‍♂️, skin tones, flags).

**The line break:** 2 lines, broken in the most balanced place where both lines are inside 920 px (1840 on the PSD),
preferably not after a small word (a, the, to ...) and preferably after a comma; the emojis stay with the last word.
All six of your real headlines get exactly your break ("When dad tries to be / the tour guide 💀😭" ...). Enter in
the title = your own break. **Shrunk only when no break fits**, and the app says it in amber ("shrunk to 97 %: no
break keeps both lines inside 920 px").

**How close it is to Photoshop** -- each PSD's own example headline drawn by the app and compared pixel by pixel
with Photoshop's own rendering of that text layer (2160x3840; overlap of the ink, your chat measure):

| PSD | line 1 | line 2 (letters) | emojis | whole headline | line widths (Photoshop / ours, px) |
|---|---|---|---|---|---|
| LouLaughs | **97.9 %** | 97.7 % | 99.2 % | **98.0 %** | 1806 / 1807, 1830 / 1831 |
| MikeMemesYou | **97.2 %** | 98.2 % | 99.3 % | **97.9 %** | 1718 / 1717, 1522 / 1523 |
| Flick707 | **97.9 %** | 97.7 % | 99.3 % | **98.2 %** | 1205 / 1204, 1143 / 1144 |
| Example.psd (never used for tuning) | 97.2 % | 97.8 % | 99.2 % | 97.7 % | 1636 / 1635, 1629 / 1630 |

Your aim was at least 94 %: the worst line is 97.2 %. (On the same measure the chat's `headline_reference.py` gets
70-85 % on line 1, so its "94 %" was measured some other way.) Against the PSDs' merged images (the whole template)
the letters match 97.1-97.4 %. Proof pictures: `work\t25\headline\proof_*.png` (Photoshop on top, ours in the
middle, the differences below).

Against your two finished frames (1080x1920): the letters are the same size (46 px, baselines 424.5 / 498), but each
of those videos has its headline about one pixel off the PSD's spot (MikeMemesYou: 1 px lower and right; LouLaughs
line 1: 1 px left), so they overlap 85-86 % as they are and 92-94 % shifted by that pixel. I kept the PSD's spot.
The running man in your MikeMemesYou frame is 🏃‍♂️ (man running), not 🏃.

**Speed:** the live preview redraws in 5-16 ms per key press; the 1080x1920 PNG takes 0.25 s; a PSD is read once
(1.5-4 s) and then cached.

## 5. The watermark in the edit (item 5)

**Done, and it really is Overlay in Premiere.** With a channel, `1_edit.xml` now has the channel's
`watermark.png` on **its own track above the template** (V3; V2 is the template), **over the whole edit**, exactly
like your `WTAERMARK_FLICK707_V2` project: **Scale 50 %** (the 2160x3840 PNG filling the 1080x1920 sequence),
centred, **Opacity 75 %**, **Blend Mode Overlay**.

**How Overlay gets through the XML:** the FCP7 XML that Premiere imports has a "composite mode" on each clip. I
tested what Premiere Pro 2024 on this PC really does with it: I imported test edits with the watermark written four
different ways, saved the project and read the clip's Blend Mode back out of the saved .prproj (the same place your
own project keeps it; your watermark there has the values 19 / 15 = Overlay):

| written in the XML | what Premiere made of it |
|---|---|
| `<compositemode>overlay</compositemode>` | Normal |
| a blend setting inside the Opacity effect | Normal |
| **`<compositemode>texturize</compositemode>`** | **Overlay (19 / 15)** |
| `<compositemode>multiply</compositemode>` (to check the test) | Multiply |

"texturize" is Final Cut Pro 7's own name for Overlay -- Premiere's XML importer has the list of FCP's names
(add, subtract, difference, multiply, screen, texturize, hard light, soft light, darken, lighten ...), and
"overlay" is not one of them. So the tool writes `texturize`. Then the final proof: a real run of the tool
(your video10 test case with the LouLaughs watermark) opened in Premiere and saved: the watermark clip has
**Opacity 75, Blend Mode Overlay, Scale 50** (screenshot: `work\t25\watermark\premiere_video10_wm.png` -- V3
watermark.png, V2 frame.png, V1 the clips, and LOULAUGHS blended into the picture). And once more at the end on a
real channel edit made by the app from downloaded videos, with the LouLaughs template on V2: the same values
(section 7, `work\t25\e2e\premiere_loulaughs_real.png`).

* The tool's new option: `--watermark <png>` (the app adds it for the channel you pick). The PNG is copied into
  `extras\media\watermark.png` (the run folder keeps its own media).
* A new hard check, **XML WATERMARK**, fails the run when the top track does not hold exactly the watermark over
  the whole edit with Overlay, 75 % and that scale -- also after `--speed 125` and the RAW's own frame rate.
* `preview_recreation.mp4` shows the watermark the same way (Overlay at 75 %).
* Without a channel nothing changes (the default template, no watermark): the check-all videos give exactly the
  same edits as night 9 (section 7).

## 6. The title check (item 6)

**What it does:** as soon as the competitor is downloaded (or when you drop a competitor's video on the Template
tab), the app reads the competitor's **headline from its video** and shows it next to your title, live while you
type, with one line: "40 % the same words as the competitor's headline -- different enough", or in red
"**84 % the same words as the competitor's headline (in another order) -- change your title more**". It warns at
**80 % or more of the same words, in any order** (counted with repeats, against the longer of the two titles). No
"different topic" check, as you said. The warning also comes before the edit starts (with the other checks).

**How the headline is read** (the tool's own OCR, RapidOCR, on the processor; 1-3 s per video):

1. 8 frames spread over the video; the rows that change between them are the video window; everything still above
   it is the band with the headline.
2. The band is read on up to 5 frames (it stops after 3 that agree); only lines that read the same at the same
   place on most frames count -- captions and the picture change, the headline does not.
3. The channel's header is dropped: the `@handle` line, the channel name next to it, anything on their rows.
4. What is left is split into blocks; the biggest block is the headline (a logo and a headline are always further
   apart than two lines of one headline -- measured on your competitors).
5. Only when there is nothing above the window: the still band below it, then text that stays on the picture itself
   (then it is shown as "read from the picture -- check it").

**Tested on every competitor video in the project** (30 files; some are the same video twice):

* **25 headlines, all read right**, word for word -- white cards, black bands, coloured letters, tweet style, curly
  quotes, a 3:4 frame (mike005): "When Joe Became The Freakiest Guy In The Pool", "Bro was a slow processor",
  "Nobody let bro finish his sentence", "ALMOST 30 YEARS AGO, YET SOMEHOW STILL ACCURATE", "I'm out" ...
* **5 without a headline** (video4, video9, zendaya and two copies): rightly "none found".
* Emojis are left out of the words (the OCR does not read them); the channel names and handles were dropped every
  time.

**Decided on my own:**

* Two words of 5+ letters that differ by one letter count as the same (an OCR misreading, or a plural:
  "hospital" / "hospitals"). For a "too close" check that is the safe side.
* `#hashtags` are left out of both titles, so `#shorts #fyp` cannot hide a copied title.
* "dont" and "don't" are the same word.

## 7. Tests (item 7)

### check-all: nothing got worse than night 9

The tool's code after items 1 and 5 (frozen as `work\snap10a`), all 23 test videos, the same split as nights 6-9
(the new-style cases thorough and scored against your fixes, the old ones `--fast` with the hard checks only), two
streams, 08:22-09:15 (53 min -- slower than night 9's 47 because the whole unit suite and two helpers ran next to
it on the processor; nothing else was on the graphics card):

* **All 23 videos give exactly night 9's edit**: every clip of the 60 fps plan and of the delivered `1_edit.xml`,
  and **the same captions, byte for byte** (`work\t25\compare_runs.py` against night 9's final run).
* **The same hard-check results**: 21 of 23 pass; the two that do not are the known ones (video020 `--fast` and
  video6's last frame, XML FLASH -- the same as nights 7, 8 and 9).
* **The same scores**, line for line: captions exactly as yours 132/373, cuts reproduced 16/75, framing 62/110.
* **Every run folder holds its own media** (the batch's "media outside the run folder" check is empty for all 23).
  That is expected: the test videos sit on the same drive as the runs, so they are linked as before, and without a
  channel nothing in the edit changed.

### Unit tests

* The tool (`tools\match_cuts\tests`), with the graphics card hidden, next to check-all: **1343 passed, none
  failed** (97 skipped: they need the graphics card or are the slow ones). Then the whole suite again on the final
  code with the graphics card free and the slow end-to-end tests switched on: **1367 passed, none failed** (22 min;
  73 skipped -- they need Linux, files that are not in this checkout, or optional tools). New tonight:
  `test_night10.py` (the watermark: its track, Overlay, 75 %, the scale, the checks, the command line, the preview's
  Overlay) and the media-copy tests in `test_conform.py`.
* The app (`tools\app\tests`): **285 passed** -- the download (format choice, plain-word errors with yt-dlp's real
  messages, the progress, Stop, a dropped RAW), the convert (keep / rewrap / encode on real little videos, the frame
  check, the original kept when the check fails), the project folder, the headline (your six real headlines' breaks,
  shrinking, emojis, each PSD's own headline against Photoshop's pixels), the channels, the title check (word rules,
  the 80 % edge, real competitor videos), the step runner, and the tabs themselves.

### The whole flow through the app, with real YouTube videos and your three channels

To test downloading for real I needed real competitor Shorts **together with the real videos they were cut from**.
SNL's YouTube channel posts both, so I downloaded only the sound of its 15 newest Shorts and 40 newest videos and
matched them (a clear match: 0.79-0.996 against 0.06-0.12 for unrelated pairs; `work\t25\e2e\find_pair.py`). Three
pairs, one per channel, were then given to the app's own window by a test program, exactly as you would: links
pasted, channel picked, title typed, Add -- three times in a row (`work\t25\e2e\drive_multi.py`; the projects in
`work\t25\e2e\projects`):

| channel | competitor (Short) | RAW (the video it was cut from) | your title (typed) |
|---|---|---|---|
| LouLaughs | "a lot to catch up on" (1:51) | Weekend Update ft. Jeremy Culhane and Jane Wickline (13:05) | The fact that he had this much to catch up on is hilarious 📰😭 |
| MikeMemesYou | "who should go?" (2:32) | Young Rell's Crew (6:14) | The fact that nobody in the crew wanted to go is hilarious 🙋‍♂️😭 |
| Flick707 | "the part is hers" (0:25) | Dakota Johnson Is Ready To Host SNL for the Third Time (1:57) | When she finally got the part 💀😭 |

What happened, all by itself:

* **Download:** all six files in 58 s (the 13-minute RAW, 119 MB, in 8.5 s), H.264 + AAC MP4 each ("the VP9 version
  is not bigger"), so the **convert** step kept them as they are ("already H.264 MP4") -- the Convert tab's real
  files are below.
* **The project folders** were named after the Shorts (`2026-10-17_a-lot-to-catch-up-on`, `..._who-should-go`,
  `..._the-part-is-hers`), each with `project.json`, `logs\`, `template.png` and `edit\`.
* **The title check:** SNL's Shorts have no headline (the picture fills the frame) -- the app said so ("no headline:
  the picture fills the frame and no text stays on it"). On your own competitors it reads them right (section 6).
* **The templates:** LouLaughs and Flick707 at full size; MikeMemesYou's long title was **shrunk to 97 %** ("no
  break keeps both lines inside 920 px"), the man-raising-hand emoji drawn as one picture.
* **The edits** started by themselves, one after the other:

  | channel | result | time |
  |---|---|---|
  | LouLaughs | **PASS** | 56 min |
  | MikeMemesYou | **OK with a note**: one spot where the person speaking is outside the window (look at it in Premiere) | 61 min |
  | Flick707 | **OK with a note**: one flash frame of another shot at 00:00:20:23 | 7 min |

  Each `1_edit.xml`: V1 the clips, **V2 the channel's template with the typed headline**, **V3 its watermark
  (texturize = Overlay, 75 %, Scale 50)**; the template and the watermark copied into the run's `extras\media`; the
  checks XML FRAME and XML WATERMARK pass. The two notes are the tool's usual "look at it in Premiere" notes on these
  new videos, not tonight's changes. The times are long because these Shorts are long (1:51 and 2:32, up to 80
  clips -- two to five times your usual competitors): the frame-exact refine step alone took 20-27 minutes, and the
  first edit shared the processor for 25 minutes with a 4K conversion of the helper's test.
* **In Premiere** (the LouLaughs edit, opened and saved): V3 watermark.png with Opacity 75, **Blend Mode Overlay**,
  Scale 50; V2 the template; the program monitor shows the finished Short
  (`work\t25\e2e\premiere_loulaughs_real.png`). Frames of the three previews: `work\t25\e2e\lou_preview_pair.png`,
  `mike_flick_preview_pair.png`.
* A Windows notification for each finished download and for the queue ("1 PASS, 2 OK with a note").
* **`steps.py` on the same projects:** `status` lists the three with every step's state; `titlecheck` on deadpool's
  competitor gives "100 % the same words -- change your title more" for a copied title; `template --channel Flick707
  --title ... --out x.png` writes a template on its own.

### The Convert tab with real YouTube files

The SNL Short "the part is hers" downloaded three times in YouTube's other formats and dropped on the Convert tab:

| the file | what the tab did | checked | time |
|---|---|---|---|
| VP9 + Opus in WebM (1080x1920) | converted to visually lossless H.264 (x264), sound to AAC | 599 of 599 frames, the same length | 3 s |
| AV1 + AAC in MP4 | converted to H.264 (same name), AAC copied | 599 of 599 frames | 3 s |
| H.264 + Opus in MKV | **rewrapped** to MP4, picture copied bit for bit, Opus to AAC | 599 of 599 frames | 1 s |

The new files: H.264 High, 1080x1920, 23.976 fps, the BT.709 colour tags kept, AAC; the originals deleted only after
their check. SSIM of the VP9 one against its source: 0.993 (visually lossless).

## 8. Committed, pushed -- and on the laptop (item 8)

Everything is committed and pushed to the branch `claude/new-session-ol4era`, one commit per item:

| commit | item |
|---|---|
| `dbac9b6` | 1 -- media copy fix |
| `bd414c5` | 5 (the tool's part) -- the watermark track, Overlay |
| `16d2333` | 2 -- the Download tab, the project folder, `steps.py` |
| `0f98a03` | 3 -- convert and the Convert tab |
| `5221f93` | 4 -- channel templates with headlines, `assets\` |
| `56516c9` | 6 -- the title check, the app's README |
| `d0986dc` | 3 (follow-up) -- FFmpeg at below-normal priority, the GPU-vs-CPU result |
| `6f6d931` | 7 (fixes found by the real test) -- Premiere's project name for a project's edit, the room a conversion needs |
| (last) | this report |

On the laptop, in PowerShell, in the project folder:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
git status
git pull
.venv\Scripts\python tools\app\setup_app.py
```

* `git status` first: if it lists files under `tools\` as changed by you, keep a copy, then `git stash` before
  `git pull` and `git stash pop` after. Your `input\`, `output\` and `finished\` folders are never touched.
* `git pull` brings about 200 MB this time: the three channels' PSDs (45 MB each), your Example.psd and the emoji
  font. That is normal.
* **`setup_app.py` installs what is new and missing** -- yt-dlp (the downloader), psd-tools (reads the PSDs) and,
  when the laptop has no deno, **deno** (yt-dlp needs it to read YouTube; about 40 MB, into the project's venv) --
  and refreshes the desktop shortcut. One line, once.
* Then double-click **MovieRecaps**: it opens on the **Download** tab.
* The first time a channel is used on the laptop its PSD is read once (a few seconds); after that it is instant.
* The laptop converts on its processor like the PC (the measured default). A RAW you drop from another drive is
  copied into the video's folder (said in its log); from the same drive it is linked.

## 9. What I decided on my own

1. **The time budget:** your message says 12 hours and, in item 9, "when the 10 hours are up". I took the shorter
   one: wrap up by 10 hours at the latest (17:50). Everything was done well before that.
2. **Helpers in parallel:** three helpers wrote the download/convert/project modules, the headline/channel modules
   and the title check, each with its own tests and notes (`work\t25\download_convert`, `headline`, `title_check`);
   I wrote the tool changes (items 1 and 5), the app's tabs, the step runner, and checked and tested it all.
3. **The commits:** one per item, in your order 1, 2, 3, 4, 6 -- except that the tool part of **item 5 was
   committed right after item 1**, so check-all could test the final tool code while I built the app (the app's part
   of item 5, the watermark per channel, came with item 2's step runner).
4. **A project's edit goes into its own folder** (`<project>\edit`, `edit-2` ...), not into `output\`: for part 2's
   board on D:\Shared\Projects everything of one video must be in one folder. Videos you drop on Match cuts
   yourself still go to `output\<name>` as before.
5. **The longer video is the RAW** also for links: if the competitor's link gives the longer video, the two are
   swapped in project.json after the download, and it says so.
6. **"Then make the edit by itself" is on by default** in the Download tab; a channel without a title is not
   started (it waits in the Match cuts queue for its title). Without a channel the edit uses the old default
   template, without a watermark, exactly as before.
7. **The watermark's Scale "50 %"** is the PNG scaled to fill the sequence -- 50 % for your 2160x3840 watermark on
   the 1080x1920 sequence, as in your project; a 2160x3840 sequence would get 100 %, which looks the same.
8. **Two lines always** for a title of two or more words (your style), one line for a single word; your own line
   break (Enter) wins.
9. **The emoji font is the 2021 Noto Color Emoji**, the same drawings as Photoshop's emoji font in your PSDs.
10. **The title check:** words of 5+ letters one letter apart count as the same, hashtags are left out (both to
    catch a copy, not to excuse one); the check never blocks anything -- it warns, before the start too.
11. **Your `input\downloads` stays empty tonight:** the real tests made their project folders in
    `work\t25\e2e\projects` (the same code; only the root differs).
12. **Assets in git:** `assets\` (the three PSDs, the watermarks, the fonts, your reference files: about 190 MB) is
    committed, so `git pull` brings the channels to the laptop.

## 10. Ready for part 2 (the project board and the "uploaded" check)

* **One folder per video**, `project.json` in it: the links, the files, the channel, the title, and every step's
  state (`download`, `convert`, `title_check`, `template`, `edit`: waiting / running / done / failed / skipped),
  when it started and finished, **on which computer**, the details (formats chosen, how each file was converted,
  the competitor's headline and the share of the same words, the template's lines, the edit's result and run
  folder) and the error in plain words. It is written safely (a temp file moved over the old one, under a small
  lock file) so two programs -- or the PC and the laptop over the network -- never half-write it or lose each
  other's steps.
* **Every step on its own**, the same code as the app's tabs (`tools\app\mr_app\core\flow.py`), also on the
  command line: `steps.py new | download | convert | titlecheck | template | edit | status` (the app's README has the
  lines).
* **Where the projects go** is one setting: the environment variable `MOVIERECAPS_PROJECTS` (unset: `input\downloads`;
  part 2: `D:\Shared\Projects`). Everything inside a project folder is stored by name, so the folder can move.
* **The channels' links** are saved for the "uploaded" check: `assets\channels\<Channel>\channel.json`
  (`https://www.youtube.com/@LouLaughs`, `@MikeMemesYou`, `@Flick707` with "Shorts only").

## 11. Check by hand

1. **The app:** double-click MovieRecaps. It opens on **Download**: paste a competitor's link and its RAW's link,
   pick the channel, type the title, press Add. Watch it download, convert and start the edit by itself; the
   result lands in `input\downloads\<date>_<name>\edit`.
2. **The template in Premiere:** import one of tonight's real test edits into your project as usual, for example
   `work\t25\e2e\projects\2026-10-17_a-lot-to-catch-up-on\edit\1_edit.xml` (LouLaughs): V2 is the template with the
   headline, V3 the watermark -- select it: Effect Controls should say Opacity 75 %, Blend Mode Overlay, Scale 50.
3. **The Template tab:** try a few of your real titles, a very long one (it shrinks and says so in amber), and
   drop a competitor's video to see its headline read.
4. **The laptop:** the four lines in section 8.

## 12. Left for next time

1. **Part 2:** the project board in `D:\Shared\Projects` for both computers and the "uploaded" check -- the folders,
   `project.json`, the steps and the channel links are ready for it. Note: the share `\\<PC>\Shared` is open only to
   the account `render` (the laptop's, I assume); this PC's own login cannot write to it over the network.
2. **YouTube's bot check** never came tonight; if it ever does, the app says to wait. Signing in (cookies) is not
   built in -- a decision for part 2 (Chrome and Edge do not let other programs read their cookies while open).
3. **Emojis newer than 2021** (🫨, 🩷 ...) are left out with a warning (Photoshop's own emoji font lacks them too).
   A newer emoji font can be dropped into `assets\fonts` -- no code change -- but it draws 😭 differently.
4. **Your two finished frames** have their headline one pixel off the PSD's spot (section 4). If you want the
   template to match your finished videos rather than the PSD, it is one constant.
5. **Long competitors are slow**: tonight's 2-minute SNL Shorts took about an hour each (the frame-exact refine step
   alone 20-27 minutes). Your usual 30-60 s competitors are much quicker, but if you start using longer ones, that
   step is the one to speed up.

## 13. The PC

* **Premiere Pro 2024** was opened by me three times, for a minute or two each (08:10-08:14 the two blend-mode
  test imports, 08:19-08:21 the watermark run, 11:19-11:21 the real LouLaughs edit), always with nothing else running on
  the graphics card, and closed again. Premiere saved those test projects in the folder of your last project
  (`D:\Downloads\e4\Copied_watch_POPW`): I deleted my three test projects there again; your files there are
  untouched. No other Adobe program was open tonight (only Adobe's background services).
* **check-all** had the graphics card to itself; the processor was shared with the unit suite and the helpers, so
  its times (53 min) are not free-PC times. The first real edit shared the processor with a 4K conversion for 25
  minutes.
* A test folder on `D:\Shared` (the run on another drive) was removed again after the test.
* **Left in `work\t25`** (about 6 GB, can all be deleted once you have looked): the check-all runs, the three real
  projects of the end-to-end test (`e2e\projects`, the Premiere check uses one), the helpers' notes and measurements,
  the test pictures. I deleted tonight's own bulky test videos (about 4 GB). `work\t25\media_copy` (0.6 GB) is a
  stage cache of my D: test runs -- delete it if you like (I leave cache deletion to you). The stage caches in
  `work\cache` and the check-all caches I left alone, as always.
* **Disk:** C: had 64 GB free at the start and about 49 GB at the end: 10.7 GB are the stage caches of the three
  SNL videos of the real test (in `work\cache`, only useful if those videos run again) and about 6 GB `work\t25`.
  To get the caches' space back: `.venv\Scripts\python work\t25\prune_e2e_caches.py` lists them (178 files),
  the same with `--delete` removes them.
* At the end, as you asked, I checked that no run was still going and that this report was saved, committed and
  pushed, then cancelled any scheduled shutdown (`shutdown /a`) and shut the PC down (`shutdown /s /t 120`).
