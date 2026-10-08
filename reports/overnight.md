# Overnight: your habits from video017/018, the three video018 bugs, then the loop (8 October)

## In short

Four commits, all pushed to `claude/new-session-ol4era`: the task you left running, then three loop changes. Each loop
change was checked with check-all on the videos it changes (before that, offline on all 11), and each failure the
checks found was fixed or narrowed before its commit. I stopped there: the next candidates had no rule that held on
two videos, and nothing else could be finished and tested before 16:00.

| video | hard checks | captions exact | word errors | cuts reproduced (near) | tool-only | length gap | framing as yours |
|---|---|---|---|---|---|---|---|
| video018-fixed | FAIL -> **PASS** | 15 -> 10 / 49 | 22.4 -> **20.9** | 0/15 (+4) -> 0/15 (**+6**) | 5 -> **3** | 6.50 -> **5.02 s** | 9 % -> **37 %** |
| video017-fixed | PASS | 28 -> 23 / 43 | 11.0 | 4/12 (+2) | 1 | 10.30 -> **8.48 s** | 100 % |
| video1 | PASS | 14 -> **16** / 111 | 11.3 -> **10.0** | 6/29 (+11) -> **9/29** (+8) | 4 | 3.95 -> **3.33 s** | |
| spiderman-school | PASS | 10 / 64 | 8.2 | 2/21 (+10) -> **4/21** (+9) | 7 | 6.70 -> **6.42 s** | |
| video2 | PASS | 0 / 21 | 9.5 | 1/1 | 3 | 0.06 -> 0.02 s | |
| video3 | PASS | 5 -> 3 / 71 | 24.6 -> 25.5 | 0/11 (+1) -> **1/11** (+0) | 15 | -5.63 -> -9.68 s | |
| video4 | PASS | 8 -> 7 / 38 | 14.9 | 2/7 (+2) | 5 | -1.20 -> -1.35 s | |
| video5 | PASS | 1 -> 2 / 37 | 30.4 -> 32.9 | 1/14 (+3) | 2 | 3.88 -> **3.67 s** | |
| zendaya-age | PASS | 1 -> 2 / 19 | 27.5 -> **22.5** | 1/9 (+2) | 7 | 3.70 -> **3.52 s** | |
| deadpool (competitor's own edit) | PASS | 47 -> 46 / 60 | 2.4 -> 5.6 | | | | |
| zendaya (no key) | PASS | | | | | | |

Before: this morning (video017/018 on d675de5; the other cases Task 10's runs of the same pipeline code), all scored
against today's answer keys. After: the latest check-all of each video on the final code (or its identical earlier
run, section 2). Over your 9 answer keys: **cuts reproduced 17 -> 23 of 119**, tool-only cuts 49 -> 47, length gaps
41.9 -> 41.5 s summed (**36.3 -> 31.8 s** without video3, whose ending now matches yours and whose middle was already
cut tighter than yours). **Exact captions 82 -> 73 of 453** -- a loss I traced to re-transcription of the changed
audio (captions moving 1-2 frames across the "exact" line, words heard differently) and to video018's captions now
following the competitor's breaks and timing, not to caption logic that got worse; it is a loss in the score all the
same (sections 1 and 2).

- **The task you left running (d7605ca):** your habits from video017/018 (clip starts at the sound, the first frame
  never before the competitor's, one framing over alike shots) and the three video018 bugs (the cut in "minute?",
  the 1-frame flash, the audio check crash). video018 now passes every hard check, framing 9 % -> 37 % "as yours".
- **Loop 1 (5fcb71b): video018's lost "I have insurance" is back.** The competitor's sound plays on under two
  stuttering pictures after a cross dissolve; the tool now follows that sound and puts the pictures on it. Also 2 more
  of your cuts on video1 (9 of 29). The first version failed two hard checks (zendaya, zendaya-age); narrowed, those
  two are exactly as before.
- **Loop 2 (c57ad28): video018's green and pink captions are read.** The competitor writes each speaker in a colour of
  their own and the tool learned only one: 50 of 50 read now instead of 30, word errors 28.2 -> 20.9, no squeezed
  captions; exact captions 11 -> 10 (one was right only by luck before).
- **Loop 3 (c51c121): your ending.** The edit stops right after the sound holding its last word, as yours do: video017,
  video018 and video3 end within 0.02-0.10 s of yours (1.5, 2.1 and 4.0 s later before); lengths closer on video017
  and video018.
- **Looked at and not changed:** follow-mode caption timing, the first caption at frame 0, trimming the quiet before the
  first word, and the current task's rejected rules -- each with its numbers below.
- **Adobe:** only video017's before run (04:53-05:24) shared the GPU with a render; every other run had it to itself.

## 1. The task you left running

### Your habits (measured on video017 and video018, checked on the other answer keys)

From the cut lists and the projects of your finished video017 and video018, checked against the other answer keys
(video1-5, zendaya-age; deadpool's key is the competitor's own edit and spiderman-school is atypical, so neither counts
as your habit). All of it is replayed offline: the Premiere plan rebuilt from a run's cut list and cached analysis
gives the run's `1_edit.xml` back exactly, so a candidate rule is scored on 7 of your answer keys (98 of your cuts) in
about 15 seconds.

- **Clip starts:** your cuts start right where the sound begins (median 0.015 s before it); the tool started
  0.075 s before. At cuts both edits make, your start is later at 27 of 41 and earlier at only 2. Your clip ends
  already match the tool's (0.084 vs 0.085 s after the last sound).
- **The first frame of the edit:** you never start more than 0.024 s before the competitor's first frame, on all
  9 answer keys. Every time the tool started earlier (video1 -0.42 s, video018 -0.35 s with 16 frames of the shot
  before, video017 -0.23 s, video5 -0.08 s) you moved the start back.
- **Big moves out of speech:** you undid every outward move of 0.23 s or more that the tool made to keep a cut out of
  a sound (7 of 7); you kept the small ones (0.1-0.17 s).
- **The ending:** you stop right after the last line: video017 0.03 s after "Sorry", video018 0.04 s after the sound
  holding "insurance"; you drop the laughter and outro after it. On 7 of 8 answer keys you end before the tool did.
- **Pauses:** you cut them by meaning, not by length or loudness: no threshold on silence length, level or breaths
  separates the pauses you cut from the ones you keep.
- **Framing on a competitor that zooms all the time (video018: 18 of 26 segments, +2.8 %/s):** you never keyframe.
  You keep one framing over alike shots (video018's wide shots got one pasted framing, 98 % at the RAW centre), you
  zoom in on a face where the competitor did not, and you zoomed the split screen out. In the finished videos you use
  one Scale for the whole video, at or below the competitor's least zoom. No rule about *which moment* of the
  competitor's zoom to take holds across videos (the least zoom makes video018 worse: 25 % -> 3 % "as yours").

### What changed (commit d7605ca)

- **Clip starts:** `--pad-before` 0.05 -> 0.03 s. In the replay, 18 of your 98 cuts reproduced instead of 16, 35
  tool-only cuts instead of 38, the length gaps 34.7 -> 31.8 s summed over the 7 keys.
- **The edit's first frame** is never before the competitor's first frame (the speech-safe start of the first clip
  stops there).
- **Framing over alike shots:** across a RAW shot change a clip keeps the framing on screen when the new shot's own
  framing is alike (under `--min-move`, within 5 % zoom) and it still shows the person of the clip before -- your one
  framing for video018's wide shots.

The three video018 bugs:
- **The cut inside "minute?"** (XML SPEECH) was not a mistimed word: the sound after it is the show's laugh and music
  bed, counted as speech. S15's start moved 74 frames back to it, and the ripple both tracks share pulled S15's 115 %
  picture 85 frames back too, so the repeat removal cut A1 into the laugh. An audio line under a retimed picture is
  now extended by at most 0.1 s of picture; beyond that the cut moves into the clip.
- **The 1-frame flash** (XML FLASH): Premiere shows the RAW frame holding a clip's source time (it rounds down); the
  export rounded to the nearest 1/60 s tick, so a cut a third of a tick off a 29.97 fps shot change showed one frame
  of the next shot at the old framing. Picture cuts within a frame of a RAW shot change now land on it, and the flash
  check sees a framing jump inside one shot. The 13-frame "flash" at 00:00:05:53 was a false alarm (the check read a
  cross dissolve's frames as black) and is gone.
- **The audio check crash:** a 1-sample piece at 16 kHz whose speed rounds to 0 divided by zero; it is silent now,
  like a hold.

### Before / after per video (check-all, thorough)

Before: video017/018 on d675de5 this morning; the other 9 cases Task 10's runs of the same pipeline code; all scored
against today's answer keys. After: work/t13/after/runs (2 h 2 min for all 11).

| video | hard checks | captions exact | cuts reproduced (near) | tool-only | length gap | framing as yours |
|---|---|---|---|---|---|---|
| video018-fixed | FAIL -> **PASS** | 15 -> 11 / 49 | 0/15 (+4) -> 0/15 (+5) | 5 -> 4 | 6.50 -> 6.03 s | 9 % -> **37 %** |
| video017-fixed | PASS | 28 -> 23 / 43 | 4/12 (+2) | 1 | 10.30 -> 9.92 s | 100 % -> 100 % |
| video1 | PASS | 14 -> 15 / 111 | 6/29 (+11) -> **7/29** (+10) | 4 | 3.95 -> 3.28 s | |
| spiderman-school | PASS | 10 / 64 | 2/21 (+10) -> **4/21** (+9) | 7 | 6.70 -> 6.42 s | |
| video2 | PASS | 0 / 21 | 1/1 | 3 | 0.06 -> 0.02 s | |
| video3 | PASS | 5 -> 3 / 71 | 0/11 (+1) -> 1/11 (+0) | 15 | -5.63 -> -5.82 s | |
| video4 | PASS | 8 -> 7 / 38 | 2/7 (+2) | 5 | -1.20 -> -1.35 s | |
| video5 | PASS | 1 -> 2 / 37 | 1/14 (+3) | 2 | 3.88 -> 3.67 s | |
| zendaya-age | PASS | 1 -> 2 / 19 | 1/9 (+2) | 7 | 3.70 -> 3.52 s | |
| deadpool | PASS | 47 -> 46 / 60 | (its key is the competitor's own edit) | | | |
| zendaya | PASS | (no key) | | | | |

The caption counts that moved are re-transcription, not caption logic: the new cuts change each edit's audio a
little, and the transcript of it moves captions by 1-2 frames or hears a word differently, across the 2-frame "exact"
line in both directions (video017: of 28 captions in both runs, 10 at the same moment, 14 moved 1-2 frames, the
median against yours 0 frames in both; deadpool "This this is gonna" -> "This It's going"). One is a real fault the
new edit tipped over: on video018 six captions were squeezed into 0.6 s (see item 2).

### Tried for this task and not kept

- **Silence length, level or breath thresholds** (`--min-silence`, `--silence-db`, breaths as quiet): none separates
  the pauses you cut from the ones you keep -- you cut by meaning.
- **The competitor's least zoom** instead of its average over a clip: video018 drops from 25 % to 3 % "as yours" (the
  wider framing loses the speaker re-centring); it only helps video2 and video4.
- **Face zoom-ins** like yours on video018: one video only.
- **Splitting the non-word lead / tail off a sound that holds words** (0.15-0.20 s): video3 gets worse.
- **Laughter as non-speech:** more cuts you did not make.
- **The ending** (the first version: only inside the last audio piece): only video017 changed then -- revisited as
  loop item 3, after item 1 brought video018's last word back.

**Adobe:** video017's before run (04:53-05:24) ran while Media Encoder and After Effects rendered (GPU shared, the
run took 31 min). Photoshop was open 06:22-06:41 during a before run of video018 that I stopped and did not use.
Everything else -- video018's before run and every after and loop run -- had the GPU to itself (per-process GPU
memory checked every 15 s).

## 2. The loop

### Item 1 -- video018's lost "I have insurance" (kept: commit 5fcb71b)

What the competitor does at the end of video018 (its audio matched to the RAW every 0.1 s): S20's take plays on as
one sound from RAW 755.94 to 758.35 ("Yeah I have insurance") while the picture dissolves into S21, reframed on the
other person and 0.1 s back on the same take, then S22, 0.3 s back; then the sound and the picture jump together to
RAW 759.59 (S23-S25), where the sound runs 2-2.5 frames ahead of the pictures.

Two separate faults made the tool lose "insurance":
- The audio stage never tried S20's sound on S21-S22: a piece with a cross dissolve was no candidate for a continuing
  sound line, the dissolve's 2-frame overlap kept S20 from counting as S21's neighbour, and S22 (0.57 s at speed 1)
  was no candidate at all. So their sound followed their own (repeated) pictures, and the repeat removal cut the
  repeated sound -- "insurance" with it.
- With the line found, the export still played S21-S22's repeated pictures: the step that puts a picture on the RAW
  time of its sound (`slip_onto_line`) allowed 1-2 frames, and the B-roll step treated nothing next to a dissolve as
  a neighbour.

The fix (as committed):
- audio_align: neighbours across a cross dissolve are adjacent (its overlap frames are left out of the measurement),
  and a piece whose sound follows its own picture only weakly (corr < 0.5) or more than two competitor frames off it
  (a stutter) is a candidate. The line test itself is unchanged (a strong peak within ±10 ms that beats every other
  alignment).
- broll: a RAW piece whose sound is another clip's verified line, with no main-clip shot beside it, plays at the RAW
  time of its sound with its own framing when its picture is within 1 s of it (`BROLL_GAP_S`, the tool's existing
  limit between "the same take" and a cutaway) and that line is a take playing on at its own speed. Your video018
  cuts "insurance" in sync, on the competitor's S21 framing, so the framing stays the piece's own.

How it got there -- the first version failed two hard checks, and I narrowed it rather than drop it:
- Before any run, every new line on every video was matched against the competitor's actual sound (0.1 s windows,
  best RAW time): 11 pieces on 5 videos, all within 0.1 ms of the line they got. The lines were right.
- The first check-all (6 videos) failed **zendaya** (XML SPEECH: an A1 cut inside "funny it's") and **zendaya-age**
  (XML FLASH: 1 frame). zendaya: its S20-S23 follow the own in-point line of S19, a 1.2x speed-up, not a take playing
  on; slipping them onto it split A1 inside a word -> the slip now needs a line anchored on a plain take at the
  line's speed. zendaya-age: S07 got a line for being 16 ms off its sound (a "beyond the tolerance" candidate), the
  line carried the short S08 after it into the B-roll fill, and one frame of it stayed at a cut -> candidates are
  now pieces weakly matched or more than two frames off (a stutter), not a frame or two off (a slip nobody sees).
- After narrowing, offline: deadpool, video5, zendaya and zendaya-age are exactly as before item 1 (every sound value
  and the export cut list), video1 keeps its new line with the export the check-all ran, video018 changes one piece
  (S02, 14 ms off, no longer re-timed) -- so video018 was run again.
- Not done: the slip where a main-clip shot sits beside the piece (it changed 9 pieces on video4 and zendaya-age from
  "replaced, framed like the main shot" to "slipped, own framing" -- nothing I can check against your framing).

check-all (against the runs of the commit before; video1 from the first item-1 run, whose export the narrowing does
not change; video018 run again on the final code; the other 9 videos identical to before):

| video | hard checks | captions exact | cuts reproduced (near) | tool-only | length gap |
|---|---|---|---|---|---|
| video018-fixed | PASS | 11 -> 11 / 49 | 0/15 (+5) -> 0/15 (**+6**) | 4 -> **3** | 6.03 -> 7.15 s |
| video1 | PASS | 15 -> 16 / 111 | 7/29 (+10) -> **9/29** (+8) | 4 | 3.28 -> 3.33 s |

On video018 "Yeah I have insurance" is back, in the edit (RAW 755.93 -> 761.38 in one take) and the captions. The
length gap grows because the edit now keeps that line and the laugh after it -- as you do -- and still the
competitor's last 2 s after them (item 3). Framing "as yours" 37.3 -> 35.8 %: the two restored pieces keep the
competitor's reframing on the other person, not your 263 % close-up. Word errors 24.3 -> 28.2 come from the
re-transcription of parts item 1 does not touch ("woah / Well wait a minute" -> "Whoa / wait a minute"), most in the
stretch whose green captions were unread (item 2). Unit suite: 1142 passed, 94 skipped.

### Item 2 -- video018's captions in green and pink (kept: commit c57ad28)

The competitor writes one speaker's captions in yellow, the other's in green, and a laugh in pink. The caption reader
learns ONE fill colour (yellow here) and was blind to the rest: 30 of its 50 captions read, the stretches between
filled from the transcript. The reader now learns another colour where none of the colours found so far shows a
letter, when that colour makes whole captions of its own for at least 0.5 s, the size of the main colour's, that read
as words (OCR: a word of two or more letters at 0.8 or surer on at least half of up to 8 samples). On all 11 test
videos it learns exactly video018's three colours and one colour everywhere else -- the same reading as before there.
Rejected on the way: zendaya (a mauve detail read as "でんで" / nothing) and zendaya-age (a beige edge read as "一"),
both stopped by the "reads as words" test; and a looser "no caption-sized letters" condition, which learned video1's
and video4's highlight colours.

Offline on video018 (the captions rebuilt the way the run builds them, scored against yours): all 50 competitor
captions read (at 0.91-1.00), word errors 24.3 -> 17.8, no squeezed captions, exact captions 11 -> 10 -- "to almonds
Joe?" was exact only by luck (you merged the competitor's "to almonds" | "Joe?"; the old transcript fill happened to
make one caption of them), and "an emergency" now has the competitor's timing, 4 frames before the word.

check-all (items 2 and 3 checked together on the three videos they change -- video017, video018, video3 -- to fit the
16:00 deadline; item 2 alters only video018's caption reading, item 3 only the endings): video018 passes every hard
check; 48 captions on the competitor's breaks, none from the transcript, none squeezed; word errors 28.2 -> 20.9;
exact captions 11 -> 10 (as offline: "to almonds Joe?" and "an emergency" lost, "the whole" won). Unit suite: 1146
passed, 94 skipped.

### Item 3 -- your ending (kept: commit c51c121)

You stop right after the last line: video017 0.03 s after the sound of "Sorry", video018 0.04 s after the sound
holding "insurance", video3 right after "very lonely!". The tool played on through whatever the competitor kept after
its last line -- laughter, a reaction, an outro -- because only silence was cut at the end, and a laugh is loud. Now
the edit ends `--pad-after` after the sound holding its last word, and the rest goes like a trailing silence (never
into a cross dissolve or another video's stretch; `--keep-silence` keeps it too).

Where each edit ends, against where yours does (the Premiere plan rebuilt offline from each run, every answer key):

| video | before | after |
|---|---|---|
| video017 | +1.47 s | **+0.03 s** |
| video018 | +2.12 s | **+0.03 s** (with item 1: the edit ends at RAW 759.250, yours at 759.267) |
| video3 | +3.98 s | **+0.10 s** |
| video1, video4, video5, zendaya-age, deadpool | -0.10 ... +0.65 s | unchanged |

No cut changes anywhere. video3's total length gap grows (-5.8 -> -9.7 s) because the tool already cuts more than
you in its middle (15 cuts you did not make) and the 4 s of noise it kept at the end was hiding that.

check-all (with item 2, on the same three videos):

| video | hard checks | the edit ends (yours) | length gap | cuts / captions |
|---|---|---|---|---|
| video017-fixed | PASS | RAW 111.533 -> **110.100** (110.067) | 9.92 -> **8.48 s** | unchanged |
| video018-fixed | PASS | RAW 761.383 -> **759.250** (759.267) | 7.15 -> **5.02 s** | unchanged (captions: item 2) |
| video3 | PASS | RAW 385.300 -> **381.417** (381.317) | -5.82 -> -9.68 s | unchanged |

### What I looked at and did not change

- **Captions starting on their first word in follow mode.** Where the competitor captions your way (2-3 words, mixed
  case: video018, video2, video5, deadpool), the tool copies its timing; the others are grouped from the words and
  start on the first word. Your captions start on the first word too (median 1 frame before it on video017, 0 on
  video018) but spread widely (video018: only 18 of 40 within 2 frames of the word; video2 and video5 ~2-3 frames
  before it); video018's competitor shows them 4.4 frames early. Starting follow-mode captions on the word centres
  the timing (video018: median -3.0 -> 0.0 frames against yours) but brings no more captions within 2 frames (11 of
  25 either way), and video2 / video5 have too few captions with your text to tell (2 and 6). Not changed.
- **The first caption at the edit's very first frame.** All 10 answer keys start their first caption at 0.000; the
  tool's starts 3-15 frames in on 7. Moving it to frame 0 wins spiderman-school's first caption and loses video3's: the
  tool's edit starts a few frames before yours there, so the moment is not the same. Not changed.
- **Trimming the quiet before the first word whatever its length** (a pause under --min-silence was kept at the very
  start): changes nothing -- every edit already starts at its first sound. What remains: your edit starts 0.083 s
  (5 frames) after the competitor's first frame on video017, video018 and video3 (0.047 s on video1), the tool on it.
  I could not explain it from the RAW; noted only.
- **A caption fix for crammed captions.** After this task, video018's six captions from "I got to get" to "I gotta
  call" were squeezed into 0.6 s (the after run). Cause: the competitor's green captions there were unread, so those
  words had no caption of their own on screen; follow mode gives such words to the caption before or after (by the
  edit's time, then by matching the screen text), and a re-transcription that timed "I" 0.7 s earlier tipped the
  tie the other way. Item 2 reads those captions; on the other 10 videos no run has more than one short caption in a
  row, so the fallback itself is left as it is.

## 3. Decisions I made on my own

- **Which changes count.** A bug fix that helps one video and changes no other counts (items 1 and 2: video018's lost
  line and its unread captions); a habit or default needs at least two videos (item 3: three).
- **The current task was committed although some caption counts dropped.** Every drop I traced is the edit's audio
  re-transcribed (captions moving 1-2 frames across the "exact" line, a word heard differently) or video018's
  unread green captions (item 2); none is caption logic this task changed. The cuts, the framing and the hard checks
  it targets improved.
- **Item check-alls ran on the videos the item can change.** You need the PC back at 16:15, and a full check-all takes
  2 hours (mostly the full-resolution verification). Before each item I ran its code offline on all 11 videos (the
  sound lines, the export cut list, the caption colours, the replayed endings): the videos where nothing changes were
  left out of its check-all, and the report says which.
- **Item 1 kept narrow:** the slip of a piece onto its sound applies only where no main-clip shot sits beside it. The
  wider version changed 9 pieces on video4 and zendaya-age from "framed like the main shot" to "own framing", which I
  cannot check against your framing there.
- **Item 3 judged by where the edit ends**, not by the whole edit's length: on video3 the ending now matches yours to
  0.1 s while the total gap grows, because the tool's middle cuts more than you and the tail it played was hiding it.
- **Caption metrics move ±2-5 per video on any edit change** (re-transcription), so I looked at the caption diffs
  themselves before calling anything a regression.

## 4. What is left (noticed, not started: no time to finish and test before 16:00)

- **video3's middle:** the tool makes 15 cuts you did not make there (your edit is 5.8 s longer). The pauses it cuts
  are ones you kept -- the opposite of video017 / video018, where you cut more than the tool. No rule found yet.
- **The 5-frame start:** your edits start 0.083 s after the competitor's first frame on video017, video018 and video3;
  the tool starts on it.
- **Crammed captions when a competitor caption is unread:** follow mode gives the words of a stretch with no read
  caption to the caption before or after; reading more colours (item 2) removes video018's case, the fallback itself
  is unchanged.
- **Framing on a competitor that zooms all the time:** video018 is 37 % "as yours"; what is left there is your own
  face zoom-ins and the split screen zoomed out -- seen on one video only.

## 5. Commits (branch claude/new-session-ol4era, pushed)

- d7605ca -- your habits from video017/018 as defaults, and the three video018 bugs
- 5fcb71b -- Loop 1: video018's lost "I have insurance" (sound lines under a stuttering picture; the slip onto them)
- c57ad28 -- Loop 2: captions in more than one colour
- c51c121 -- Loop 3: your ending
- (this report)

Run folders: work/t13/before, work/t13/after (the task), work/t14/item1, item1b, item23 (the loop); the analysis
scripts are in work/t13 and work/t14 (not committed).
