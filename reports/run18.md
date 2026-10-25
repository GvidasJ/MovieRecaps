# Run 18 (25 October): one task at a time

Started 05:29 (PC; you were at it). `shutdown /a`: "no shutdown was in progress" -- the PC stays on. C: 102.5 GB free at the start, D: 1795.7 GB. Ended 14:40: C: 120 GB free, D: 1725 GB; nothing of yours deleted; the PC left on. You came back at about 14:03 -- from then on everything ran at BelowNormal and nothing touched Premiere or your windows.

## The tasks

| # | task | state | how you use it |
|---|---|---|---|
| 1 | A1 voice: real Clip Gain + the right effects, no extra Volume | **done** (Premiere test passed on all 4 channels; no preset needed) | Open in Premiere from the app, save (Ctrl+S): the app closes Premiere once, writes everything, opens it again |
| 2 | The song on every channel | **done** (Premiere test passed on all 4 channels) | nothing: MikeMemesYou, Flick707 and LouLaughs edits get SceneProof's song on A2, with its effects |
| 3 | Cartoons: a repeat only cut when its sound repeats too | **done** (your case: 1.75 s of the competitor no longer cut; video40 plays 91 % of the RAW you kept instead of 85 %, video19 97 % instead of 92 %) | nothing: it is on (`MATCH_CUTS_REPEAT_SOUND=0` turns it off) |
| 4 | The lists stay when the app closes; History's three buttons | **done** | close the app as usual: everything comes back; History: Try again / another RAW / another competitor on every failure |
| 5 | Learn and your 60 fps sequences | **done** (it already compared by time; now a rule, and the card says so) | nothing |
| 6 | The Convert check's new rule on the real file | **done** (Fletch Lives converts) | nothing |
| 7 | Learn the queue + video35 / video37 | **done**: learned 7 of 7 (all 7 pass the check-all); **video35 and video37 pass now** (both failed on day 17); a flash at the very start, found by two new cases, is cut too | nothing |
| 8 | The "Mono + red" look | **done** | it is the new look; Settings -> Classic look: On goes back |
| 9 | Learn by yourself: whom each clip is centred on | **done, nothing turned on**: one narrow rule found (+3 clips in video37) but it undoes your video021 two-shot -- built as a switch, OFF | `MATCH_CUTS_CENTRE_SURE=1` to try it |

## Task 1 -- A1 voice: real Clip Gain + the right effects (done)

- **What happens now, on every edit** (any channel), after you press Ctrl+S on the imported edit (the same close-write-reopen as day 17's song, now for every edit, not only SceneProof):
  1. **Clip Gain**: each A1 clip gets its own gain = its own loudest peak to 0 dB (the tool measured it on the sound that clip plays; it is in the clip's comment, e.g. "Audio Gain +13.84 dB"). Written as the clip's real Clip Gain (`<Gain>` in the saved project).
  2. **Volume**: the extra Volume effects the XML made (one per clip, two or three stacked for a clip over +15 dB) are **removed**; only the clip's own built-in Volume stays, at **0 dB**.
  3. **Hard Limiter** (Maximum Amplitude 0 dB, **Input Boost 2.00 dB**, Look-Ahead 7 ms, Release 100 ms, Link Channels 1.00), then below it **Amplify +3.00 dB** -- copied object for object from your own `CorrectVolume` preset (the mono version: Premiere makes A1 a mono clip).
- The XML itself still carries the gain as a Volume (so the sound is right even before you save, and if you import the XML without the app). The save step turns it into the real Clip Gain.
- An edit imported twice into one project (two sequences): both get it. A project whose A1 clips aren't the edit's (moved by hand): nothing is changed, and the app says so.
- **Real test on a copy of day 17's saved project** (`work\t17\song\edit`): 18 A1 clips, 33 extra Volume effects removed, Clip Gain +11.27 to +59.58 dB written, Hard Limiter (0.68 = 2 dB) and Amplify (0.6875 = +3 dB) added under the Volume; the song's +5 / -1 dB kept.
- **Premiere test (05:49-05:53, you were away 10+ minutes):** one edit per channel, copied to `D:\mr_t18\<channel>` (your projects untouched), opened with the app's own Open in Premiere, saved with Ctrl+S as you would. Each time the app closed Premiere, wrote the project and opened it again, with **no message** from Premiere. Then Premiere saved each project **once more itself** (a marker added and undone, Ctrl+S), and that file -- Premiere's own writing -- was checked against the sound itself (each clip's peak measured again from its `raw_stereo.wav`, over the clip's own source range):

  | channel (edit) | A1 clips | extra Volumes removed | Clip Gain vs the clip's own peak | chain on every A1 clip |
  |---|---|---|---|---|
  | SceneProof (Awkward Helicopter Ride) | 41 | 82 | within 0.005 dB (+9.30 to +32.52 dB) | Volume 0 dB, Hard Limiter (Input Boost 2.00 dB, 7 ms, 100 ms), Amplify +3.00 dB |
  | MikeMemesYou (Most Honest Answer) | 12 | 25 | within 0.004 dB (+13.38 to +50.14 dB) | the same |
  | Flick707 (Funniest Background Squad) | 8 | 10 | within 0.004 dB (+10.69 to +15.77 dB) | the same |
  | LouLaughs (Islam Tried An Energy Drink (2)) | 3 | 3 | within 0.004 dB (+0.97 to +1.65 dB) | the same |

  So writing the effects straight into the project **works**: no preset drop is needed any more (the fallback was not used).
- **The A1 preset, if you ever need it by hand:** `CorrectVolume` (your own: Hard Limiter 2 dB + Amplify +3 dB) is now the A1 preset too. The old "MovieRecaps audio chain" (Input Boost 0 dB) is no longer named anywhere; delete it from Premiere's Presets if you like.
- **Tests:** 5 new (`test_a1_chain_run18.py`): each clip its own Clip Gain (within 0.1 dB), exactly one Volume (0 dB) then Hard Limiter then Amplify, the values; your preset's objects; an edit imported twice; a project that doesn't match left alone; nothing added twice; the save watch starts for any edit with A1 clips. Day 13 / night 11 tests now expect Input Boost 2 dB and the CorrectVolume preset.

## Task 2 -- the song on every channel (done)

- `assets\channels\MikeMemesYou`, `Flick707` and `LouLaughs` `channel.json` now have the same `"music"` as SceneProof: the same file (SceneProof's mp3, not copied), from 0.52 s, split at 12.32 s, Clip Gain +5 / -1 dB, Volume 8.5 dB, ending where the video ends. A1 is not touched.
- The song's clips get your **CorrectVolume** effects (Hard Limiter 2 dB + Amplify +3 dB) written into the saved project, the same way as A1 (Task 1).
- **Premiere test:** the same four projects (above). A2 in Premiere's own save: SceneProof, MikeMemesYou and LouLaughs 2 song clips (Clip Gain +5.00 / -1.00 dB), Flick707 1 (its video is only 12.33 s long, so it ends at the 12.32 s split: no second piece), each Volume 8.50 dB, then Hard Limiter (2 dB) and Amplify +3 dB. The song starts 0.52 s into the file (frame 12 at 23.976 fps), splits at 12.32 s and ends with the video. A1 unchanged by the song.
- **Tests:** 2 new (`test_song_all_channels_run18.py`); day 17's song test now also expects the two effects.

## Task 3 -- cartoons: a "repeat" is only cut when its sound repeats too

- **What really happened in your case** (`2026-10-24_The Funniest Phone Call of All Time (2)`, Family Guy S05E16): the competitor plays that scene at **103 %** (every sound window: speed 1.030), and the drawings are held (the phone call). The picture search put **new moments** of the competitor on RAW moments another piece already plays (the same drawing scores the same):
  - S16-S18 (15.97-17.07 s, "what do we do?" ...) all on RAW 128.73 s, which S15 already plays; the sound says they follow S15 straight on (RAW 129.22-130.33 s, exactly the gap the edit had there)
  - S08 / S09 (8.17-9.23 s) on RAW 119.56-120.5 s, the sound says 121.16-122.2 s (right after S07)
  - S22, S28, S30-S33: the same, 1-18 frame pieces jumping around the RAW
  - the repeat removal then **cut 4 of them out (1.75 s)**: those were the parts you missed.
- **The fix** (new `tools\match_cuts\match_cuts\repeat_sound.py`, a step of the Premiere plan; nothing of the cached analysis changes, so no case is analysed again):
  1. every piece on a RAW moment another piece plays is checked against the **competitor's own sound** there (its sound windows fitted with one straight line, at any speed -- 103 % here -- and the few frames the picture sits off the sound, measured on the pieces around it)
  2. **its sound is where its picture is** (the competitor really shows that moment twice, picture AND sound): a real repeat -- the repeat removal cuts it, as before
  3. **its sound is elsewhere**: the piece is put where its sound is -- right after the piece before it when that is where the sound is (one take) -- **only when its picture is the same there** (the picture scored on 5 frames at the new RAW moment, at most 0.02 below its own frames). A piece standing on that moment, itself off its own sound, moves too (a run of look-alikes)
  4. otherwise nothing changes (and a move that would land on another piece's RAW is undone)
  - **the silence removal, checked the same way:** in your case both silences it cuts (9.73 s, 0.20 s; 26.98 s, 0.72 s) are real pauses -- the competitor itself is quiet there too (-39 dB against its -19 dB median) -- and after the fix every piece's sound is where its picture is, so nothing of the competitor goes with them. I also built a general rule (no pause cut inside a piece whose picture is not where the competitor's sound is), but in the check-all it kept one pause you cut in **video37** (The Dictator: the competitor's re-edited interview), one caption and one cut fewer reproduced -- so by your rule it is **OFF** (`MATCH_CUTS_PAUSE_SOUND_CHECK=1` turns it on).
  - switch: `MATCH_CUTS_REPEAT_SOUND=0` turns the step off (ON by default).
- **Before -> after on your case** (the same caches, step off / on):

  | | before | after |
  |---|---|---|
  | pieces put where their sound is | -- | **11** (S08, S09, S16, S17, S18, S22, S28, S30, S31, S32, S33) |
  | repeat removal | **4 cut, 1.75 s** (S16, S17, S18 at 15.5-16.6 s; S09 at 8.3 s) | **0 cut** |
  | silence removal | 2 cut, 0.92 s | the same 2 (quiet in the competitor too) |
  | parts of the competitor missing from the edit | **1.75 s** | **none** (only the 2 pauses) |
  | 1_edit.xml | 26 clips, 33.33 s | **21 clips, 35.12 s** |
  | XML checks | every check OK; a warning "one continuous RAW take with the same framing, but two clips" | every check OK, no warning |

- **Tests:** 6 new (`test_run18_repeat_sound.py`): new words over a held drawing go where their sound is; a real repeat (its sound repeats too) is left to the repeat removal; a piece whose picture differs there stays; off by the switch / without sound; the real case's shape (103 %, the picture 0.06 s off the sound): one take; the silence check.
- **The deciding test: video40**, this very video as a check-all case with your own fix as the answer (learned this morning), run with the step off and on:

  | video40-fixed | step OFF | step ON (the default) |
  |---|---|---|
  | hard checks (both runs before today's join of a take left in two clips) | **FAIL** (a take left in two clips) | **PASS** |
  | repeats cut | 10 (2.27 s) | 1 (0.20 s) |
  | of the RAW you kept, it plays | 85.1 % (misses 4.46 s) | **90.9 %** (misses 2.71 s) |
  | RAW it plays that you cut | 3.88 s | 4.34 s |
  | framing / cuts | 0/14, 1/9 | 2/14, 1/9 |
  | captions exact / word errors | 2/48, 32.4 % | 1/48, 37.5 % |

- **The other cartoon it changed: video19** (Family Guy, "I can't believe it's not butter"): 11 pieces put where their sound is; it now plays **97.4 %** of the RAW you kept (day 17: 92.4 %; misses 0.58 s instead of 1.71 s), the same 1.46 s you cut; cuts 3 -> 4 of 8. Day 17 cut 4 bits you kept (RAW 35.03-35.45, 36.75-37.54, 44.54-44.71, 52.68-52.97 s); now that take plays straight, as in your fix.
- **Why the captions numbers went down there anyway:** they are compared by time, and both versions keep a 0.75 s pause you removed early on (RAW 30.49-31.24 s), which shifts every later caption. Day 17's 4 wrong cuts happened to shorten the edit by about the same, so its captions sat closer in time. The words themselves are transcribed again on every run (the known ±2-5 noise).
- **video34** (Family Guy, "Luck Not On His Side"): 4 pieces moved 0.3-0.4 s to where their sound is (S08, S09, S15, S16). It plays the same 92.9 % of the RAW you kept as day 17, plus 0.33 s more of a part you cut (RAW 36.66-38.08 s). The spoken captions are word for word the same; one action caption there now reads "\*looks at the van\*" instead of your "\*walks away\*" (the AI describes the frames it shows), and that one caption is the whole word-error change (34.1 -> 42.2 %) and the 2 -> 1 exact captions.
- **video6** (a cartoon too): 3 pieces moved to their sound; word errors 10.0 -> **8.3 %**, captions 18 -> 17 of 32 (the re-transcription), cuts and framing the same. **video2 and video4** (hard checks only): 1 piece each, PASS.
- So the step stays **ON**. The other cases of the check-all: the same as day 17 (the table below).

## Task 4 -- the lists stay when the app closes

- **What is kept** (new `tools\app\mr_app\core\rows_keep.py`): every row of the **Match cuts queue** (`%APPDATA%\MovieRecaps\queue_rows.json`) and of **New video** (`newvideo_rows.json`) -- running, paused, waiting, failed, stopped, finished -- with its RAW, competitor, headline, YouTube title, channel, Fast / Thorough, 100 / 125 %, its steps, its stop message and its result. Written when the app closes (and when a run ends or a row is removed, so a crash loses little), whole (a temporary file, then replaced).
- **When the app opens** every row is back as it was, with its buttons:
  - a video **being made** when you closed: paused by the close (night 15) -> back **PAUSED**, Resume goes on. One that could not be paused (its run had not started yet) -> back as a stop "the app was closed while it ran", with **Try again**
  - a New video step **going** when you closed (a download, a conversion, a template) -> back **stopped at that step** ("stopped when the app was closed"), **Try again** goes on from it
  - a New video row whose edit was in the queue finds its queue row again (the same project)
  - a torrent download that goes on finds its own kept row (not a second one)
- **History: every failure has Try again + Try with another RAW + Try with another competitor** -- Download failed, **Convert failed**, Template failed and Edit failed (before: only failed edits had the two "another" buttons, nothing had Try again). Try again on a project: its New video row tries that step again (a project without a row there gets one, made from its project.json); on a failure that never got a project folder (a broken link): the same video is added again. The New video failures logged without a project now keep their inputs for this.
- **Tests:** 5 new (`test_rows_keep_run18.py`): **closed and opened again with a failed convert row, a stopped download and a running edit: all three come back with their inputs and their buttons** (the running edit paused, with Resume; its New video row finds it); a step going at the close comes back stopped there and Try again goes on from it; a queue row that could not be paused comes back with Try again; History's three buttons on a Convert failed and on a logged Download failed (each one works); off by the switch. The app's other tests start with empty lists (`MOVIERECAPS_KEEP_ROWS=0` in their setup).

## Task 5 -- Learn and your 60 fps sequences

- **What I found:** Learn already compares **by time**: captions and cuts as moments of the RAW (through each edit's clips), positions in sequence pixels. Nothing counts the frame rate as a fix. On mik1 (your sequence 1080x1920 at **60 fps**, the tool's 29.97):
  - your captions against the tool's, where both start on the same word: the median difference is **+0.003 s**, no constant shift; the small ones (up to 0.017 s) are under one frame of 29.97 and were never counted (the limit is 0.08 s)
  - the **10 timings are real changes of yours**, each at least 0.1 s in the RAW: e.g. "okay yes" starts 0.10 s earlier, "were to" ends 0.54 s earlier, "in some kind" starts 0.40 s later, "*runs away*" ends 1.1 s earlier
  - the cuts: the 8 kept cuts moved 0.001-0.015 s (counted the same), 2 trims (0.23 s, 0.36 s), 1 moved (0.74 s); the 15 position moves go from -830 to +1187 px (which person each clip is centred on, not one shift)
- **What changed** (`core\learn_check.py`): it is now a rule, not luck -- a difference under **one frame of the slower of the two rates** is never counted, for the captions and for the cut trims, whatever the two rates are; and when your sequence runs at another rate the card says so: "your sequence runs at 60 fps, the tool's at 29.97 fps: compared by time -- the frame rate is not counted as a fix, and a difference under one frame of the slower rate (33 ms) is not counted".
- **mik1 learned again, before -> after:** captions "50 of 69 changed (9 words, 31 groupings, **10 timings**)" -> the same (the 10 are real); cuts "1 moved, 1 added, 2 removed" -> the same; position "15 of 17 moved" -> the same; plus the new frame-rate note.
- **Tests:** 2 new (`test_learn_fps_run18.py`): the same edit at 29.97 and at 60 fps -- an 8 ms cut move and a 16 ms caption move are not counted, a 0.2 s retime is.

## Task 6 -- the Convert check's new rule, on the real file (done)

- **Real run** (06:26-06:38): Convert on `input\downloads\2026-10-25_The Smoothest Office Flirt of All Time\Fletch.Lives.1989.720p.HDTV.mkv` (through a hard link: your file was never touched or moved; the original kept) -- **passed** in 12 min (the PC was busy with the check-all):
  - the picture: H.264 copied into MP4 (lossless), **136434 of 136434 frames, 5690.56 s both**
  - the sound (AC-3, 5 tracks) made AAC: **5692.70 s -> 5690.40 s** -- the original's sound ran 2.14 s past the last frame, the copy's ends 0.16 s before the picture's end: accepted by `099340c`'s rule (up to 10 s after the picture, within 0.25 s of its end)
  - my converted copy (5.4 GB) and the link were deleted afterwards; your file is as it was.
- **Sound cut inside the picture still stops it:** a new test with this file's own numbers -- its copy's sound 1.06 s or 90 s short inside the picture: "the sound is ... long instead of 5692.70 s" (a stop), as before.
- **Tests:** Convert's 49 tests pass (1 new: the real Fletch Lives numbers).

## Task 7 -- the learn queue, video35 and video37

- **Learned: 7 of 7** open lines of `D:\Shared\Projects\learn_queue.md` (now marked `[x]`; 0 open):

  | case | video | RAW |
  |---|---|---|
  | mik1-fixed | The Most Suspicious Question of All Time (3) | Reno 911 S05E11 |
  | video39-fixed | The Most Clueless Teacher In Comedy History | Everybody Hates Chris S03E07 |
  | flkoick1-fixed | The Most Questionable Kids Show Moment of All Time | Arthur S14E15 |
  | video40-fixed | The Funniest Phone Call of All Time (2) | Family Guy S05E16 (Task 3's case) |
  | video41-fixed | The Worst First Word of All Time | (a piece of the movie) |
  | video42-fixed | The Most Willing Victim of All Time | (a piece of the movie) |
  | video43-fixed | The Funniest Flight In British Comedy (3) | (a piece of the movie) |

  Each is a check-all case now (`tests\real\<case>`: your finished edit's captions and clips, the competitor, the RAW piece, its frame and watermark). From your captions: **26 new action captions** (`caption_actions.txt`). Learn's two glossary lines were not taken (below, "What I decided").
- **video35 (the 2-frame flash at 27.2 s):** the competitor's last 2 frames jump to another shot, so the edit's last clip was a 2-frame piece of another RAW shot -- a flash at the very end. The end check only knew a take running on into a new shot; now a **last clip of up to 0.1 s** that shows another RAW shot is cut too, as you did (your fix ends on the clip before it; `END_CLIP_FLASH_S`). **Result: PASS** (day 17: FAIL), every score as on day 17 (captions 4/33, word errors 15.7 %, cuts 3/9, framing 9/23).
- **video14 (a new hard FAIL from Task 3's step, caught by the check-all):** "S09+...+S21 / S22: one continuous RAW take with the same framing, but two clips" -- the step put a piece back where its sound is, and the take was left in two clips. Now two clips that play one continuous take (the same speed, mirror and framing) become one clip again, with their A1 (`join_seams`; never at a split made on purpose: a RAW shot change). It runs in the edit's **last step** (`last_steps`), which the XML check rebuilds the same way -- the first version ran only at the RAW's rate, so the checked 60 fps edit still had the two clips; the second compared more strictly than the check (the clip before was the last piece of a clip with a framing per RAW shot, and S22's framing equalled S21's only to the 7th digit).
- **video37 (the person speaking outside the window, 00:00:22:29 and 00:00:25:12, the same on day 17):** the day-12 and day-13 rules judged the pieces of that clip one by one (2 s each, "no clear speaker" in each), but the edit joins them into one clip (S26..S31, RAW 55.13-60.57 s) and the XML check judges the joined clip: the person speaking there is outside the window, nobody else in it. Now the edit's last step judges the **final clips exactly as that check does** (the same stretches, the person speaking or the biggest face, the same exceptions: someone else shown whole is the competitor's choice; a clip keeping the competitor's own framing) and moves a clip that would fail, zoom kept: **centred** on the person when the face is entirely outside the window (your fix of this clip: -650 px; centred: -666), **slid in** when it is only cut off at the edge (as the day-12 rule). The clips of one RAW shot with one framing move together; when a slide would make a cut next to it change under --min-move, the clip takes the framing of its neighbour in the same RAW shot if that shows the person (the take is then one clip again). `MATCH_CUTS_PERSON_CHECK_FIX=0` turns it off. (Its other failure on day 17, a 3-frame flash at the very end, is cut in 1_edit.xml at the RAW's rate and checked there.)
  **Result: PASS** (day 17: FAIL). RAW 55.13-57.85 s moved -667 px (yours: -650); RAW 57.85-60.57 s took the next clip's framing, -270 px (yours: -300 for that whole take). Framing 0/11 -> **2/11**; cuts 3/7 and word errors 11.5 % as before (captions 4 -> 3 of 44: the re-transcription).
- **Found by the new cases: a flash at the very start** (flkoick1, Arthur; video42): the edit opened a frame or two before a RAW shot change -- its first frame(s) the shot before (flkoick1: RAW 30.03 s, the next shot at 30.07 s; your fix starts at 30.113 s). The end of an edit already got such frames cut (day 13, video35 today); now the start does too, at the RAW's rate: those frames go, V1, A1, markers and the captions move that much earlier, and the edit's own checks compare it that way (`start_flash_frames` / `cut_start`; `START_FLASH_CUT` switches it). It only acts where the edit's first frames fail the flash check, so no case that passed changes. **flkoick1: FAIL -> PASS**, **video42: FAIL -> PASS** (captions, cuts and framing as before).
- **The check-all:** below.

## Task 8 -- the "Mono + red" look

- **It is the default now.** Settings -> **Classic look: On** goes back to today's look (it changes when the app opens again; the window says so). The setting is `look` in `settings.json` ("mono" / "classic").
- **What it is** (`ui\theme.py` `apply_look`, set once at the start before any widget is made -- the classic look's values are untouched):
  - near-black `#0a0a0a`, text `#dcdcdc`, dim `#6b6b6b`, thin lines `#1a1a1a` / `#333`; **red `#e5484d` only for failures, counts and the one main button**; green `#58c470` PASS / done; amber `#d6a12a` running (it replaces the blue)
  - **one monospace font** for the whole app (the bundled JetBrains Mono; Cascadia Mono or Consolas if it can't load), with Segoe UI Emoji after it for the emoji in titles
  - section titles uppercase with a little letter spacing and a small red square: `■ QUEUE`, `■ VIDEOS`, `■ LEARN FROM YOUR FIX`
  - the top: `MOVIE` + red `RECAPS`; the pages as uppercase words; the open page underlined with a thin red line; Learn's count in red (no box)
  - **lists instead of big boxes:** each video (queue, New video) is a row with thin lines between rows, a faint fill and the red left edge under the mouse (the hover rules kept); its status on the right: **red FAILED, amber 68%, green PASS**; a failed row's reason in red under it, with its buttons
  - **buttons:** square, uppercase mono; **one red filled button** per area (the main action: Make the video, Copy tonight's prompt, Save ...), all others outlined grey; no glow, no gradient, no sweeping light
  - **Learn's three columns:** one strip with a thin frame, thin dividers and red counts; the cards are rows with a line under each
  - no grid behind the pages, no glow under the rows, no circling border on the running row
- **The four extras you picked** (`ui\mono_parts.py`):
  1. **pipeline dots** on every video: `● DOWNLOAD ─── ● CONVERT ─── ◉ EDIT ─── ○ DONE` (● green done, ◉ amber running, ○ dim waiting, ✕ red where it failed)
  2. **block progress bars:** `■■■■■■■□□□ 68%  ~10 MIN` (amber while running, green when done, red when it failed)
  3. **coverage strip** on each finished or failed edit in the queue: a thin bar along the competitor's length -- green found in the RAW, red not in the RAW, amber unsure -- with 0:00 and the end time under it (read from the run's cut list)
  4. **Ctrl+K:** a box to type a few letters ("nv", "lea", "boa") and jump to any page or action -- New video, Match cuts queue, Learn, Board, History, Find source, Convert, Template, Settings, Start the queue (Space), Paste a link (Ctrl+V), Search this page (Ctrl+F) -- each with its shortcut shown
- **Not added** (as you said): no bottom status bar, no live log line, no numbered sections, no today counter in the header. Every page, button and feature is still there.
- **Pictures before -> after** (`reports\run18_look\`, drawn by Qt itself offscreen -- no picture of your desktop, your windows were never touched; a few demo rows on the queue and New video; your Board and History read-only):

  | page | before (classic) | after (Mono + red) |
  |---|---|---|
  | New video | `1_new_video_classic.png` | `1_new_video_mono.png` |
  | Match cuts queue | `2_match_cuts_queue_classic.png` | `2_match_cuts_queue_mono.png` |
  | Learn | `3_learn_classic.png` | `3_learn_mono.png` |
  | Board | `4_board_classic.png` | `4_board_mono.png` |
  | History | `5_history_classic.png` | `5_history_mono.png` |
  | Settings | `6_settings_classic.png` | `6_settings_mono.png` |

  (Offscreen, Qt draws no emoji at all -- blank in the classic pictures, small boxes in the mono ones; the real window draws them.) `tools\app\look_shots.py <folder> mono|classic` makes them again.
- **Tests:** 7 new (`test_look_run18.py`): your palette, classic unchanged, the setting; the dots, the blocks, the coverage strip from a cut list; Ctrl+K finds and runs; a queue row and a New video row drawn as a list with their dots, blocks, red FAILED and reason. The app's other tests run on the classic look (each test puts the theme back).

## Task 9 -- learn by yourself: whom each clip is centred on

- **What I looked at:** your framing of 101 clips in 13 scored cases (chocolate, laptop010, video11, 14, 15, 19, 24, 33-38: every clip the tool had a centring note for), each set against (1) the tool's own reason for leaving that clip's framing as it is (its log: "not centred on the person speaking: unclear speaker", "a close-up", ...) and (2) where your framing puts the face the tool found speaking. A clip counts as matching you when it is within 40 px of your framing (the check-all's framing score).
- **What centring the found speaker would do, by the tool's reason for not doing it:**

  | the tool's reason | your clips | would match you more | less | the same either way | off either way |
  |---|---|---|---|---|---|
  | unclear speaker | 39 | 2 | **7** | 5 | 25 |
  | a close-up | 18 | 1 | **5** | 5 | 7 |
  | centred already (under 104 px) | 14 | 2 | **4** | 7 | 1 |
  | the competitor's own framing | 12 | **3** | 1 | 2 | 6 |
  | would hide someone it shows | 7 | **3** | 1 | 0 | 3 |
  | a wide shot | 7 | 0 | **2** | 1 | 4 |
  | --min-move | 3 | 1 | 0 | 0 | 2 |

- **So no general "centre the speaker more" rule:** it loses as often as it wins. The biggest group (25 of the 39 "unclear speaker" clips) is framed by you on someone who is neither the competitor's choice nor the speaker the tool found -- a listener, someone reacting, or two people (below, with every face).
- **The one narrow pattern:** a **very clear** speaker (on 90 % or more of the speech, Light-ASD 3.5 or more, 3 or more above every other face) that the tool left because the competitor's own framing is no wider than the template, or because centring would hide someone else the competitor shows. You centred them in video37 (43-47 s, 3 clips) and video33 (S18, 2 clips), and no clip of this kind got worse. Built as a switch, **OFF** until tested: `CENTRE_SURE` (`MATCH_CUTS_CENTRE_SURE=1`). It can only act where the log shows such a clip: video33, video37, zendaya, video4 and (a new case) mik1.
- **The test** (the check-all of those cases again with the switch on, everything else the same):

  | case | switch off | switch on |
  |---|---|---|
  | video37-fixed | PASS, framing 2/11 | PASS, framing **5/11** (4 RAW shots centred; captions, word errors, cuts the same) |
  | video33-fixed | PASS, framing 6/24 | the same (its sure speakers' moves are under 104 px: "centred already") |
  | zendaya | PASS | the same edit (its one such clip: "centred already") |
  | video4 | PASS | the same edit (its 3 such clips: "a close-up" / "centred already") |

- **But it would undo one of your own fixes:** the tool's tests keep what you did in **video021 at 82.5 s** (night 3): two women in a pool, the one on the right speaking very clearly (Light-ASD 3.9) at the window's edge, the other shown whole -- **you kept the two-shot**. With the switch on, that test fails: the rule would centre her and push the other woman out. In video37 you did the opposite (centred the speaker). Your two fixes disagree and I can't yet tell them apart, so by your rule ("keep only if nothing gets worse") **the switch stays OFF**. `MATCH_CUTS_CENTRE_SURE=1` turns it on for one run if you want to try it.
- **Then with every face** (the people analysis the tool keeps in its cache: each face's box and speaking score frame by frame), all 253 clips you framed in the scored cases -- what sits in the window's middle under your framing:

  | faces in the clip | the top speaker | another face | between two faces | no face at the middle |
  |---|---|---|---|---|
  | none (cartoons, objects) | -- | -- | -- | 76 |
  | one | 78 | -- | -- | 45 |
  | two | 18 | 5 | 10 | 6 |
  | three or more | 1 | 4 | 4 | 6 |

  With two faces and a clear speaker you centre the speaker 16 times of 28 (between the two: 6). Where the speaker is unclear (11 clips) you choose differently each time (between: 4, the other face: 3, the speaker: 2, neither: 2). A "two-shot" rule (the middle between the two faces) would match you in 1 of those 11 and break 2 clips that match you now -- so no rule from this either.
- **Learned for good from this:** nothing new turned on. Where the tool and you differ is mapped (the tables above); your choices there follow the content (who reacts, whom the joke is on), not anything the face positions or speaking scores show.

## The check-all: day 17 -> run 18

Every case of day 17's check-all again on today's final code (its caches on D:, `D:\mr_checkall`), and the new cases from the learn queue (analysed for the first time: an hour or more each). "hard": the deliverables and every XML check; captions: exactly like yours / your captions; word errors: %; cuts: your cuts the tool makes; framing: your clips framed as you did (within 40 px).

**In short: day 17's 26 cases -- 24 PASS, 2 FAIL (video35, video37) -> all 26 PASS.** Every score the same as day 17 except the cases Task 3's step changed (video14 better; video19, video34, video6: see Task 3 -- what each plays against your edit, and why the caption numbers moved) and video37's framing (0 -> 2 of 11). The new cases: all PASS (flkoick1 and video42 after today's start-flash fix).

<!-- TABLE -->
| video | day 17: hard / captions / word errors / cuts / framing | run 18 | time |
|---|---|---|---|
| chocolate-fixed | PASS / 7/22 / 16.67 / 1/3 / 5/5 | PASS / 7/22 / 16.67 / 1/3 / 5/5 | 32m19s |
| flkoick1-fixed | -- (new) | PASS / 0/15 / 52.0 / 0/3 / 4/5 | 50m54s |
| laptop004-fixed | PASS (hard checks) | PASS (hard checks) | 3m48s |
| laptop010-fixed | PASS / 10/34 / 7.69 / 1/9 / 11/13 | PASS / 10/34 / 7.69 / 1/9 / 11/13 | 4m05s |
| laptop013-fixed | PASS / 12/22 / 13.33 / 1/5 / 2/6 | PASS / 12/22 / 13.33 / 1/5 / 2/6 | 7m54s |
| mik1-fixed | -- (new) | PASS / 8/69 / 13.33 / 8/13 / 2/15 | 77m41s |
| spiderman-school | PASS (hard checks) | PASS (hard checks) | 8m45s |
| video017-fixed | PASS (hard checks) | PASS (hard checks) | 8m56s |
| video020-fixed | PASS (hard checks) | PASS (hard checks) | 19m37s |
| video11-fixed | PASS / 14/23 / 15.38 / 2/3 / 2/8 | PASS / 14/23 / 15.38 / 2/3 / 2/8 | 4m43s |
| video14-fixed | PASS / 16/42 / 8.0 / 1/3 / 4/10 | PASS / 21/42 / 6.0 / 1/3 / 4/10 | 12m23s |
| video15-fixed | PASS / 7/22 / 33.93 / 3/8 / 12/21 | PASS / 7/22 / 33.93 / 3/8 / 12/21 | 8m51s |
| video19-fixed | PASS / 8/33 / 30.0 / 3/8 / 1/10 | PASS / 6/33 / 32.79 / 4/8 / 1/10 | 34m21s |
| video2 | PASS (hard checks) | PASS (hard checks) | 3m14s |
| video24-fixed | PASS / 14/42 / 42.39 / 4/7 / 1/14 | PASS / 14/42 / 42.39 / 4/7 / 1/14 | 6m57s |
| video3 | PASS (hard checks) | PASS (hard checks) | 25m34s |
| video30-fixed | PASS / 18/43 / 16.85 / 4/7 / 2/13 | PASS / 18/43 / 16.85 / 4/7 / 2/13 | 8m58s |
| video33-fixed | PASS / 28/81 / 15.8 / 3/6 / 6/24 | PASS / 28/81 / 15.82 / 3/6 / 6/24 | 5m25s |
| video34-fixed | PASS / 2/20 / 34.1 / 0/3 / 0/4 | PASS / 1/20 / 42.22 / 0/3 / 0/4 | 6m21s |
| video35-fixed | **FAIL** / 4/33 / 15.7 / 3/9 / 9/23 | PASS / 4/33 / 15.71 / 3/9 / 9/23 | 18m45s |
| video36-fixed | PASS / 0/32 / 27.2 / 0/3 / 6/6 | PASS / 0/32 / 27.16 / 0/3 / 6/6 | 24m54s |
| video37-fixed | **FAIL** / 4/44 / 11.5 / 3/7 / 0/11 | PASS / 3/44 / 11.49 / 3/7 / 2/11 | 9m41s |
| video38-fixed | PASS / 8/33 / 43.0 / 2/6 / 5/19 | PASS / 8/33 / 43.04 / 2/6 / 5/19 | 7m11s |
| video39-fixed | -- (new) | PASS / 9/38 / 3.8 / 2/6 / 4/8 | 48m00s |
| video4 | PASS (hard checks) | PASS (hard checks) | 17m17s |
| video40-fixed | -- (new) | PASS / 1/48 / 37.5 / 1/9 / 2/14 | 16m19s |
| video41-fixed | -- (new) | PASS / 6/46 / 29.73 / 2/6 / 8/17 | 63m44s |
| video42-fixed | -- (new) | PASS / 6/38 / 27.54 / 4/9 / 4/13 | 6m19s |
| video43-fixed | -- (new) | PASS / 6/36 / 24.68 / 0/0 / 0/3 | 187m19s |
| video6-fixed | PASS / 18/32 / 10.0 / 5/7 / 9/9 | PASS / 17/32 / 8.33 / 5/7 / 9/9 | 7m53s |
| video8-fixed | PASS / 0/16 / 9.09 / 1/4 / 4/11 | PASS / 0/16 / 9.09 / 1/4 / 4/11 | 3m38s |
| video9-fixed | PASS / 4/18 / 44.44 / 3/4 / 4/4 | PASS / 4/18 / 44.44 / 3/4 / 4/4 | 8m40s |
| zendaya | PASS (hard checks) | PASS (hard checks) | 2m45s |
<!-- /TABLE -->

## Still broken / not done

- **The general pause check** (Task 3: no pause cut where A1 isn't the competitor's sound) is built but OFF -- it made video37 worse.
- **The framing rule (Task 9):** no rule turned on. The one candidate (centre a very clear speaker even over the competitor's framing) helps video37 but undoes your video021 two-shot; where the speaker is unclear your choices don't follow the faces' positions or speaking scores.
- **Captions numbers move by themselves** (re-transcription on every run, ±2-5 exact captions): a change that only moves the picture can still show -1 / +1 captions.
- **App tests:** one animation timing test (a colour fade "part-way after 110 ms") can fail when the PC is very busy (it passed alone, 9 of 9); the torrent tests still run apart from the rest (they crash Qt when run together with everything under heavy load -- the known night-15 crash).

## What to do by hand in Premiere

- **Nothing for the sound**, when you open the edit with the app's **Open in Premiere**: press **Ctrl+S** once after it opens -- the app closes Premiere, writes A1's Clip Gain, the Hard Limiter (2 dB) and Amplify (+3 dB) on every A1 clip (and the song's gains, Volume and the same two effects on A2), and opens the project again. Then edit as usual.
- **Only if you import 1_edit.xml by hand** (not through the app): the sound is right (each A1 clip's gain is in a Volume), but the Hard Limiter + Amplify are not on: select the A1 clips (and the song's clips on A2) and drop your **CorrectVolume** preset on them.
- Optional: the old "MovieRecaps audio chain" preset (Input Boost 0 dB) is not used any more -- delete it from Premiere's Presets so it isn't picked by mistake.

## On the laptop

Close MovieRecaps, then in PowerShell:

```
cd C:\Users\gvida\Desktop\bh\MovieRecaps
git pull
.venv\Scripts\python tools\app\setup_app.py
```

- No new packages.
- The app opens in the new **Mono + red** look; Settings -> **Classic look: On** (then open the app again) goes back.
- Nothing to import in Premiere: the A1 chain and the song's effects are written into the project when you save (Ctrl+S) after Open in Premiere. (If you ever drop a preset by hand: `CorrectVolume` is the one for A1 now; the old "MovieRecaps audio chain" can be deleted from Premiere's Presets.)
- The queue and New video lists are kept per computer (`%APPDATA%\MovieRecaps`): the laptop keeps its own.

## What I decided on my own

1. **Task 1:** the XML still carries each A1 clip's gain as a Volume (so the sound is right right after the import, and with the XML imported by hand); the save step turns it into the real Clip Gain and removes those Volumes. The effects are copied from your own CorrectVolume preset (its mono version: Premiere makes A1 mono) -- written straight into the project worked in Premiere, so no preset drop is needed.
2. **Task 2:** the three channels use SceneProof's mp3 where it is (`../SceneProof/...` in their channel.json), not a copy.
3. **Task 3:** the general silence check (no pause cut where A1 isn't the competitor's sound) is built but OFF: it cost video37 a caption and a cut.
4. **Task 4:** the lists are written when the app closes and also when a run ends or a row is removed (a crash loses little); the app's tests start with empty lists.
5. **Task 5:** nothing counted the frame rate as a fix already; mik1's 10 timings are real -- I made the rule explicit instead of changing the numbers.
6. **Task 7:** Learn's two glossary lines (`god -> God`, a censored word) not taken, as on days 15-17 (a new glossary line re-transcribes every video); the 26 new action captions taken. The 7 new cases got `"style": "new"` and their run's frame and watermark, as day 17's.
7. **Task 8:** the new look is the default (you chose it); Classic look is one switch away. The pictures are drawn offscreen by Qt (never your desktop).
8. **Task 9:** the one rule found stays OFF because it undoes your video021 two-shot (a test from night 3), though it helps video37 -- "nothing may get worse" wins.
9. **The check-all ran while I fixed what it found** (video14's two clips, video37's person, the start flash): each case's row is from a run on code that has every fix able to act on it -- the later fixes only act where an XML check fails, so a case that passed can't change; the cases they did act on ran again on the final code (video14, video37, flkoick1, video42), and video14, video19 and video37 came out the same there again.
10. **The new cases:** first analysed today (48 minutes to 3 hours each with three streams on the graphics card); all 7 finished and passed.

## 5 new suggestions (in `D:\Shared\Projects\suggestions.md`, not built)

1. **Make a finished run's edit again in seconds** -- a rule change tried on all 34 cases in minutes, not hours (half a night)
2. **A marker on each clip whose speaker is unclear** -- where you re-frame most clips by hand, a Premiere marker points your eye (small)
3. **"Check my saved project" in the app** -- after Ctrl+S, the app says the A1 chain and the song are right before you render (small)
4. **"Clear finished" on the queue and New video** -- the kept lists don't grow forever (small)
5. **The coverage strip on History's rows too** -- weak matches visible at a glance (small)

## Tests

- After tasks 1-2: tool 1657 passed, 0 failed; app 1279 passed, 0 failed.
- **The final run (14:24-14:37, on the code as pushed): tool 1671 passed, 0 failed** (the graphics card hidden), then its GPU test files with the card: **57 passed**; **app 1238 passed, 0 failed**; the app's torrent tests apart: **58 passed**. `setup_app.py`: "done: everything is as the project expects".
- New today: 37 tests (A1 chain 5, the song 2, cartoon repeats 6, the kept lists 5, Learn's frame rate 2, Convert 1, the look 7, the person pass 5, the joined take 1 and the start flash 3; the end-flash test extended for video35).
