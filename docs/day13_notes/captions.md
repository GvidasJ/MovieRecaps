# Day 13 -- helper "captions": 6.1 (captions a little too early) and 9.3 (word pairs split across two captions)

## 6.1 What your fixes show

Measured on **all** your fixes that still have their run (23: every keyed test case with a `run`, plus the five
newest -- survive / add / monkey / butter / cop .prproj, read straight from the projects with prproj.py): each of
your captions paired with the tool caption it came from by its words (the app's `learn_check.align_words`), its
start compared as a RAW moment through each edit's own clips (so a cut you moved does not count as a caption move).
Scripts and data: `work\t28\captions\`.

* **The one-frame Premiere shift is real, and you leave it.** On every 23.976 sequence the captions you did not
  touch sit exactly one frame before the SRT's frame -- Premiere's SRT import (night 11's finding): video7 19 of 23,
  video12 22 of 34, video14 26 of 32, video18 6 of 6, video9 7 of 9. On 29.97 (video17, Tobey) they sit **on** the
  SRT's frame (15 of 22): Premiere does not shift there. 25 / 30 / 60: on the frame (30: the few whose ms falls just
  below their frame one early).
* **What you do move is not one frame.** video16 (Always Sunny): 30 of 37 captions moved **later**, median 8 frames
  (0.33 s). mindculpt: 17 of 27 moved **earlier** in its second half, median 7 frames (up to 13). In both, your
  starts land on the moment the word is heard (within about a frame), and the tool's had been 6-8 frames early /
  10-12 frames late there. Where the tool's captions were already on the words you left them (video14, video7,
  video12, video18 ...: only the import frame). Older 60 fps fixes show only small moves of 1-5 frames of 60 fps.
* **So "fixing the one-frame shift" does not match what you do** (6.1's question): delivering the SRT so Premiere
  lands each caption on its own frame (night 11's R-D1, `(frame + 0.8) / 23.976`) would put every caption one frame
  later than where you accepted it -- e.g. video7's captions as Premiere places them 14 of 36 exactly, landing on
  their own frame 2 of 36. **R-D1 stays off** (decision). No delivery change was made, so the Premiere import check
  was not needed and not done (I did not open Premiere).

**Why the tool's captions were off in video16 and mindculpt (found today):** the captions are timed on a transcript
of the caption step's own rendering of the edit's audio -- built from the cut list (the After Effects model of the
competitor's edit: every piece at the competitor's speed) -- while 1_edit.xml plays every RAW clip at 100 %. The two
run apart where the competitor changed the speed (most likely the whole cause; not proven piece by piece):
video16's competitor has pieces at 50 % / 67 % right where its offset starts (4.5-6.3 s of the edit), mindculpt's
plays 13 pieces at 105 % (its offset grows through them), laptop009's runs at 125 %. Transcribing 1_edit.xml's own
sound again here (CPU, the tool's aligned transcription) shows it word by word: video16's first 16 words 0.00 s
apart, then 98 words -0.27 +- 0.01 s to the end; mindculpt -0.04 ... -0.10 s up to 14 s, then +0.16 growing to
+0.47 s; laptop009 a drift to -0.22 s at its very end. The other check-all videos are on the sound (within 0.05 s),
apart from chocolate's sung jingle (no one offset) and three words of video021 (0.45 s, too short a stretch).

## 6.1 What I built

`match_cuts/post_captions.py` (new; in **no cached stage** -- no stage module imports it, a test checks it: no
re-transcription for check-all) and one call in `pipeline.stage_captions` (`post_captions_step`, never fails a run):

* After the caption step wrote 2_captions.srt (still on the 60 fps planning grid, before --speed and the RAW's rate),
  every transcript word (captions.json `words_final`) is paired with the same word of the **RAW's own transcript
  where 1_edit.xml's A1 plays it** (`captions.speech_starts` on the speech map: what Premiere plays). A caption's
  offset: the paired words within 2 s of its start, when at least 5 of them agree (60 % within 0.1 s of their
  median). Neighbouring captions off the same way by **0.15 s or more** make a stretch; a stretch of at least 3
  captions whose offset reaches **0.25 s** somewhere moves -- each caption by its own offset, start and end together,
  as you drag a block. Nothing moves across a video cut: an edge on a cut stays there while its caption stays on
  its side; a caption heard after the cut starts on it and keeps going (video16's "bread": you put it on the cut);
  one the move leaves under 0.1 s joins the caption on its other side (mindculpt's "by" | "himself" -> "by
  himself", exactly your caption -- at the RAW's rate a caption that short would lose its words). Back to back
  stays back to back.
* Never moved: copied captions (timed to the speech already), `*...*` / actions, another video's stretches, a
  voice-over's captions, and anything when the speech map or the transcript is missing.
* Why 0.25 s / a stretch (decision, from your fixes): you moved video16's 0.27 s and mindculpt's 0.16-0.47 s; you
  kept laptop009's drift to 0.22 s and every caption within ~0.1 s; one lone caption off (video021's stammer
  "I've-") is left alone.
* Report: *Captions moved onto the sound* lists every move ("`to China` moved 0.45 s earlier"); the log says it
  once; captions.json gets `post`.

## 9.3 What your fixes show, and what I built

Rule 9 flags were a note only. Over your fixes the tool flagged 50 split pairs you could compare: you **joined 16 and
kept 34 split** -- "join every flagged pair" would be wrong 2 times in 3. What you join consistently:

* a **determiner** (a, an, the, my, your) left at the end of a caption: "you're a | lighting guy" -> "a lighting
  guy", "up a | flashlight" -> "a flashlight", "still in the | water?" -> "the water?", "My | sister's been",
  "your | beautiful baby" -> "your beautiful", "on the | island together" -> "on the island", "my | daddy saved" ->
  "My daddy" (7 joined, 1 kept: "with all the | goofy shit");
* a **name split in two**: "all over Hong | Kong" -> "Hong Kong", "Rodney | King riots" (2 joined; "Nestle | Crunch",
  two one-word sung captions, kept).

`post_captions.pairs_fix` (run before the timing fix) does exactly these, and only when no pause (> 0.25 s), comma or
sentence end is heard between the two words, no video cut is within 0.25 s, and every caption it makes keeps the
caption rules (20 characters, 4 words / 5 starting with a weak word, 2+ words at most 1.5 s on screen, at least 2
frames): the determiner moves to the next caption (or, when that would leave one weak word alone -- "on" | "the
island" -- the noun comes back instead, as you did, and when the noun was all the next caption held the three go
together: "from the children", "on my ultrasound" as in your video021 / video15); a lone determiner takes the next
word (both into one when that was all the next caption held); a name's first word joins its second. A stammer ("a-"), a quote ("'A"), an
interjection ("No") never counts; a name needs another word before it in its caption. The report lists each one under
*Words kept together, fixed by the tool*. Not done (decision): the other kinds of pairs (verb + preposition,
adjective + noun, pronoun + verb ...) -- you keep most of them split.

## How it was tested (offline, no GPU)

Copies of day 12's final check-all runs (`work\t27\checkallB\S|T\runs\<case>\001`), the planned SRT rewritten by the
two fixes, scored with caption_score exactly as check-all does ("placed" = as Premiere places them: the check-all
number on 23.976 keys; "exact" = at the SRT's own times); plus the five newest fixes on their own runs against your
projects. For the timing fix the RAW's words where A1 plays them were stood in for by 1_edit.xml's own sound
transcribed on the CPU with the tool's aligned transcription (the pipeline uses the speech map: the same kind of
words, aligned the same way); the --speed case (mike005) on its 100 % plan, as in the pipeline. (These CPU
transcriptions ran several at once and crowded the PC's memory this morning; after the lead's word no more were
started -- laptop004's 100 % stand-in was not made: it is an old-style case check-all does not score.)

**Day 12's check-all runs** (the 16 scored cases; "exactly" at the SRT's own times / "placed" as Premiere places
them = check-all's number):

| case | your sequence | exactly | placed | word errors | pairs joined | captions moved |
|---|---|---|---|---|---|---|
| chocolate-fixed | 30 | 7 -> 7 | 7 -> 7 | 16.7 -> 16.7 % | 1 | 0 |
| deadpool | 60 | 45 -> **46** | 45 -> **46** | 4.0 -> 4.0 % | 1 | 0 |
| laptop009-fixed | 60 | 21 -> **22** | 21 -> **22** | 6.8 -> 6.8 % | 1 | 0 |
| laptop010-fixed | 60 | 10 -> 10 | 10 -> 10 | 7.7 -> 7.7 % | 1 | 0 |
| laptop013-fixed | 23.976 | 12 -> 12 | 12 -> 12 | 13.3 -> 13.3 % | 1 | 0 |
| mike005-fixed | 60 (125 %) | 9 -> 9 | 9 -> 9 | 13.0 -> 13.0 % | 0 | 0 |
| video021-fixed | 60 | 21 -> **22** | 21 -> **22** | 13.5 -> 13.5 % | 2 | 0 |
| video10-fixed | 23.976 | 0 -> 0 | 0 -> 0 | 31.6 -> 31.6 % | 0 | 0 |
| video11-fixed | 23.976 | 4 -> 4 | 14 -> 14 | 15.4 -> 15.4 % | 0 | 0 |
| video12-fixed | 23.976 | 11 -> 11 | 8 -> 8 | 4.1 -> 4.1 % | 0 | 0 |
| video14-fixed | 23.976 | 15 -> 15 | 16 -> 16 | 9.0 -> 9.0 % | 0 | 0 |
| video15-fixed | 25 | 5 -> **7** | 5 -> **7** | 33.9 -> 33.9 % | 1 | 0 |
| video6-fixed | 60 | 18 -> 18 | 18 -> 18 | 10.0 -> 10.0 % | 0 | 0 |
| video7-fixed | 23.976 | 2 -> 2 | 14 -> 14 | 10.7 -> 10.7 % | 0 | 0 |
| video8-fixed | 23.976 | 1 -> 1 | 0 -> 0 | 9.1 -> 9.1 % | 0 | 0 |
| video9-fixed | 23.976 | 0 -> 0 | 4 -> 4 | 44.4 -> 44.4 % | 0 | 0 |
| **all 16** | | **181 -> 186** | **204 -> 209 of 533** | none higher | 8 | 0 |

No case lower on either count or on word errors. (The "exactly" column is also what R-D1 would give the 23.976
keys: video7 14 -> 2, video11 14 -> 4 -- why it stays off.)

**The five newest fixes on their own runs** (the runs you fixed, against your projects; they are new test cases
video16-20 without a check-all run yet):

| fix | your sequence | exactly as you have them | word errors | pairs joined | captions moved | within a frame of yours |
|---|---|---|---|---|---|---|
| video16 Always Sunny | 23.976 | 7 -> **16** of 56 | 18.8 -> 17.9 % | 0 | 50 | 7 -> 16 |
| video17 Tobey | 29.97 | 13 -> 13 of 32 | 6.9 -> 6.9 % | 0 | 0 (on the sound) | 13 -> 13 |
| video18 monkey | 23.976 | 6 -> **8** of 25 | 9.3 -> 9.3 % | 1 ("on the island" / "together") | 0 | 6 -> 8 |
| video19 butter | 23.976 | 7 -> 7 of 33 | 28.8 -> 28.8 % | 0 | 0 | 8 -> 8 |
| video20 mindculpt | 23.976 | 9 -> **10** of 55 | 16.5 -> 16.5 % | 3 ("all over" / "Hong Kong", "my daddy" / "saved", "my daddy'll") | 25 + 1 joined ("by himself") | 9 -> **15** |

mindculpt's moved captions now start 1-3 frames from yours instead of 8-13 (you dragged blocks a little after the
words in places), so most count as "closer", not yet "exactly". The pair fix alone on all 23 original runs you
fixed: no case lower; video021 +1, video15 +2, video18 +2 (laptop004 / laptop010 / laptop013 / mindculpt changed
without a count change).

Tests: `tests/test_day13_captions.py` (21 new: pairing, offsets, stretches, cuts, placeholders, other video,
every pair kind and every guard, the step rewriting the SRT and captions.json, the report lines, the step never
failing a run, the module in no cached stage); every caption test passes; the whole tool suite (GPU hidden):
**1448 passed, 97 skipped** (52 min on a busy PC; with `PYTHONUTF8=1` as `_env.sh` sets it -- without it
test_report reads its report as cp1252 and fails, before my change too). App suite (untouched by me): 978 passed,
5 skipped, 1 failed on a Windows file lock (PermissionError on the test's own learn_waiting.json) that passes alone,
as do the two timing tests the busy PC slowed in an earlier run. A basetemp under the session's long Temp path breaks
17 app tests on Windows' 260-character paths: use a short one (`work\t28\<helper>\pt_app`).

## For the lead

* Files: new `match_cuts/post_captions.py`, `tests/test_day13_captions.py`; small edits in `pipeline.py` (one call
  in `stage_captions` + the function `post_captions_step` above `prepare_frame`), `report.py` (`_post_captions` +
  two `+ _post_captions(cap)` in `_captions`), the tool README (rule 9's row; "On the sound you get" after the three
  ways). No cached-stage module changed: check-all re-uses every cache.
* check-all's numbers move only through the planned captions (`captions_60fps.srt`). On the old cases expect
  pair joins (deadpool, laptop009, video021, video15: +1 / +1 / +1 / +2) and almost no timing moves; on video16 /
  video20 (mindculpt) the timing moves. The pipeline's speech map may pair a few more or fewer words than my CPU
  stand-in: if check-all shows a timing move on an old case that loses a caption, `post_captions.STRETCH_SHIFT_S`
  (0.25) is the one knob.
* Later (not done): the real cure for 6.1 is to transcribe the sound 1_edit.xml plays (its A1 at 100 %) instead of
  the competitor-speed rendering (`captions.run_captions` builds `y16` with `build_audio(cl, ...)`) -- a captions.py
  change, so a full re-transcription for check-all (about +1 h). It would also give the right *words* in
  speed-changed pieces (today the transcript hears them at the competitor's speed).
