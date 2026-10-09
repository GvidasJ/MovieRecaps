# Overnight 2: your three rules, captions, --keep-speed, the fix list, batch mode (9 October)

## Re-import into Premiere

| file | why |
|---|---|
| `output/020/1_edit.xml` | re-exported tonight with your three rules (9 V1 clips, split at the RAW's shot changes, each linked to its own A1 clip; every A1 clip its own sound at 0 dB), the Flop flip, the timing fix; its RAW now inside `extras/media` (it pointed at `input/raw.mp4` before). Every XML check OK. Your `video020_fixed.prproj` is untouched next to it. |
| `output/019/1_edit.xml` | re-exported with the timing fix (S04 placed right), "I have insurance" back (A1 plays RAW 757.52-759.25 s on, like yours), "VR", 20 clips each linked to its A1 clip, every XML check OK; 6 cut moves of 0.23 s or more flagged CHECK BY HAND in its summary. One caption still to fix by hand: "Got gotta call" (should be "I gotta call", see 5). |

Older outputs (017, 018) were finished by you already; nothing there needs re-importing. The files they replace are
kept in each folder's `before_overnight2/`.

## In short

Eight commits plus this report, all pushed to `claude/new-session-ol4era` (8692c92 ... 12032ad), on top of the
two from before you went to sleep (the output/020 fixes and `--keep-speed`). Unit suite: 1168 passed, 94 skipped.

- **Your three rules are defaults** (2): a cut at every RAW shot change on V1 and A1 (checked on your 018 and 020
  projects: every detected change split), every A1 clip the picture's own sound at 0 dB, no clips under 10 frames.
- **Captions** (3): every difference of your 11 answer keys sorted by kind; splits are the biggest (185), then
  timing and wrong words. Kept: a break-model setting (split errors 203 -> 192, no video worse) and the wrong-word
  fixes from your list. After everything, check-all: 124 of 543 captions exact (119 before).
- **Your fix list** (5): video018's "insurance" is back on the full-size RAW, `check-all --full-size` catches
  test-copy-only fixes, "VR" (but "I gotta call" still reads "Got gotta call" on 019), big cut moves flagged, 019's S04 explained (re-export), the person
  check in chunks.
- **`batch <folder>`** (6), with run folders that keep working after you move or delete the input folder.
- **Worse:** video3's captions (rule b plays its RAW sound where the competitor had a voice-over); the new
  video020-fixed test case fails its hard checks on its 80 MB copy of the 4K RAW (the full-size 020 passes).

## 1. output/020 (done before you went to sleep: 80c69b9, 19c6d32)

The three Premiere bugs of output/020 were fixed and pushed before this night started:
- **Timing:** for a sped-up clip Premiere counts `<in>`/`<out>` on the sped-up clip (source time = in x speed). The
  writer and every reader now use that; in-points sit on the grid Premiere can start a sped-up clip on.
- **Flip:** written as FCP's Flop filter (what Premiere's own XML export writes), not "Horizontal Flip". *I can't
  run Premiere here: please confirm the clips import mirrored with no "not translated" message.*
- **Person check:** the competitor's own close-ups crop the forehead, so the face box poked out of the window and
  the check called the competitor's framing "someone else". A face now counts as shown with at most 15 % of its box
  past an edge. No clip of 020 is re-framed.

## 2. learn on video020_fixed.prproj, and your three rules

`learn` made the test case `tests/real/video020-fixed` (8692c92): your timeline (15 pieces), your 30 captions, the
competitor and an 80 MB copy of the 3 GB 4K RAW. `learn` found the project's RAW by `--run output/020` (the project
plays `input\raw.mp4`, not the run's media -- see 6). What it recorded, and what I did not count as your habits:

- **Speed:** every one of your clips plays at 100 % (each piece's source length = its timeline length). That undoes
  the competitor's 125 % -- not a habit; it is what `--keep-speed` does on request.
- **Flip / framing:** the flip and the person framing undo the run's bugs (above): not counted. One clip moved
  sideways (+96 px), on one video: no default.
- **Cuts:** 4 of 4 clip edges moved, 1 removed, 1 added (on one video; the suggestion "clips start later on 3 videos,
  median +0.13 s: --pad-before 0.00" comes from 017/018/video1 and was already the reason for 0.03; not changed).
- **Captions:** 43 words you added are almost all action captions (`*knocks on the door*`, `*gets caught*` ...): 13
  of your 30 captions. The competitor never shows them, so the tool cannot copy them; they stay `*...*`
  placeholders for you to write.

### Your three rules, checked and made defaults (aa7824f, d516984)

| rule | checked on | result | default |
|---|---|---|---|
| a. a cut at every RAW shot change (Scene Edit Detection) | your fixed 018 and 020 projects (017 has no change inside a clip) | every shot change the tool finds inside a clip you play was split (018: 3, 020: 2); none left unsplit. Your 2 other mid-take splits (018 at 734.35 s, 020 at 524.13 s) have no picture change at all (frame difference under 1.6, a real change: 35), so they were not Premiere detections the tool misses | on: V1 and its A1 split, each piece linked; `--no-scene-cuts` |
| b. every audio clip its own sound at normal volume | all 11 videos' A1 | the tool muted cutaways over music, left freezes silent and could put another moment's sound under a picture (an audio line) | on: each picture's own RAW sound at 0 dB; an audio line stays only where it carries on the sound already playing (018's "insurance", which your own edit does too); `--audio-lines` for the old A1 |
| c. no mini cuts | your shortest clips: 0.3 s (018, video5), once 10 frames (video3) | 020 had 3-9 frame clips | on: under 10 frames (1/6 s) joins the clip before (it plays on), else the clip after (starts earlier), never into another RAW shot; a real shot change keeps its cut; `--min-clip FRAMES` |

On 020 (re-run): 9 V1 clips, split at 506.09, 511.26, 513.85, 515.14, 517.23 and 521.52 s, every A1 clip the
picture's own sound with no level keyframes, every XML check OK. The batch run of 020 with `--fast` found one more
case: a shot change one frame after a 125 % clip started left one frame of the shot before at the new framing (a
flash) -- the clip before now plays on over it (d516984).

## 3. Captions

**Every difference, sorted** (last night's final run of each of your 11 answer keys, 543 of your captions; 119
reproduced exactly, the rest by kind):

| kind | count | what it is |
|---|---|---|
| split | 185 | the same words, broken into captions elsewhere. 122 times one of your captions is spread over several of the tool's, 63 times the tool's joins it with its neighbours' words |
| timing within 6 frames | 64 | the same caption, starting 3-6 frames off (the "exact" line is 2) |
| words partly wrong | 58 | a caption with some words misheard / missing |
| words wrong | 40 | other words entirely |
| action captions | 22 | your `*knocks on the door*` etc.; 13 of them on video020 |
| edit | 20 | the tool's edit does not play that moment (a cut, not a caption problem) |
| timing over 6 frames | 25 | |
| capitals | 7 | e.g. "Vr" for your "VR" |
| extra / punctuation / missing | 6 | |

**What I changed, most common kind first** (each measured on all 11 videos with an offline replay of the caption
assembly -- each run's own words, screen captions and edit; the replay gives the runs' own captions back, so a
change is scored without 3-hour runs):

- **Splits (the biggest kind):** the break model learned from `srt/` now trusts each word pair's own counts more
  (`SMOOTH` 3 -> 1) and leans less on the competitor's break (`COMP_BREAK_BONUS` 1 -> 0.5). Split errors 203 -> 192
  of 385 of your breaks, exact 118 -> 120, **no video worse** (video1 15 -> 17 exact, 57 -> 53 splits;
  spiderman-school 41 -> 37 splits; video4 23 -> 20 splits; the rest unchanged). I also tried: your answer keys
  added to what is learned (each video left out of its own model) -- worse on video017 (23 -> 14 exact) for little
  gain elsewhere; always grouping in your style instead of following the competitor's breaks -- better on video5 /
  video2, worse on video018; neither kept. Splits stay the biggest gap: on these keys your breaks are a choice
  among several good ones (the tool's caption lengths match yours: 2.1-2.3 words on average on both sides).
- **Timing:** looked at, not changed. Captions starting 1.5 frames earlier would make 11 more exact -- but nearly
  all of them on video1/3/4, whose answer keys are timed from final.mp4: their offset is the same +0.2 frame
  fraction on every caption, a measuring offset of those keys, not your style. On the keys timed from your projects
  (017, 018, 020) the median offset is 0 (017) and the change would cost video017 one caption.
- **Wrong words:** the two wrong-word bugs of your fix list (below: "I gotta call", the doubled "Bye"), and your
  capitals from the glossary ("VR").
- **Action captions:** cannot be made -- the competitor never shows them; they stay `*...*` placeholders where
  nothing is said, for you to write.

**After all of tonight's changes** (check-all, real runs: the captions are transcribed again from the new A1, so
they also move with rules a-c): **124 of 543 exact (119 before)**; per video in the table below. The biggest
change is video3, worse (see the table).

## 4. --keep-speed (done before the night: 19c6d32)

`--premiere --keep-speed`: every clip at 100 %, the same RAW moments in the same order (a clip the competitor
sped up to 125 % lasts 1.25 x as long), captions transcribed from that edit. On 020: 24.85 s before silences instead
of 19.92 s; at 100 % the silence removal applies (it is skipped on sped-up clips), so it came out 15.4 s --
`--keep-silence` keeps everything. Your own fixed 020 is also at 100 %, 19.58 s long.

## 5. Your fix list

- **video018's "insurance" ending on the full-size RAW (output/019) -- fixed (fcd4742).** Cause: on the full-size
  RAW the take before "insurance" comes out as two segments; its 0.35 s second piece (its window shortened by a
  dissolve) correlated 0.78 with the take's audio line, just under 0.8 (the smaller copy measured 0.81), and that one
  miss ended the line before "insurance" was tried. Such a short piece on the line's lag (0.7 or more, well above its
  sidelobe) is now pending, kept only when the next piece verifies. Re-run on the full-size RAW: A1 plays RAW
  757.52-759.25 s straight through (your edit: 757.62-759.27 s), "I have insurance" is captioned, every XML check OK.
  The same re-run ended on 3 frames of the next shot (0.05 s of --pad-after reaching into it: XML FLASH); a sliver
  after the last line's sound now goes with the ending.
- **Tests that catch test-copy-only fixes (7437706):** `check-all --full-size` runs every case whose full-size
  original is on this machine on that original (case.json `full_raw`; `learn` writes it for every smaller copy it
  makes; video017/018/020-fixed point at their run folders' media). A fix that only works on the 80 MB copy fails
  there.
- **video018 --fast fails XML SPEECH/FLASH at the "insurance" stutter -- passes now.** The batch run of video018
  with `--fast` passed every XML check (speech, flash, repeat, link, person, ...).
- **"I to call Bye" -> "I gotta call": half fixed. "Vr" -> "VR": fixed (68dbaa7, fcd4742).** The recheck
  against the RAW replaced "gotta go" with "to call Bye" (its window heard the next "Bye" again): fixed -- the
  transcript now hears "I gotta call. Bye." and no "Bye" is doubled. But the recheck still replaces "I" with "got"
  (more confident, 0.84 against 0.46), so output/019 reads **"Got gotta call"**: my guard for "the start of the next
  word heard again" passes its unit test but does not catch the real case, and I ran out of time to trace it. Fix
  that one word by hand. "VR": your glossary had "Vr -> VR" from video018, but glossary words only pass
  where the audio prefers them, and capitals cannot be heard -- a capitals-only entry is now written as learned.
- **Cut moves of 0.23 s or more into speech -- flagged, not capped (b3cfdf9).** The summary and report mark each
  such move "CHECK BY HAND" (you undid all 7 on 017/018). Not capped: a capped cut lands inside the sound and fails
  the hard XML SPEECH check, so the run would fail instead.
- **output/019's S04 "at 00:00:-1:59":** nothing in our files prints that. In 1_edit.xml S04's `<start>` is -1,
  FCP7's way of saying "starts inside the transition before it" (a 2-frame dissolve at 394-396). But 019 was written
  before the timing fix: S04 plays at 99.44 %, and its old in/out spanned 164 frames where it fills 166, so Premiere
  could place it 2 frames off around that dissolve. The re-export uses the fixed encoding (re-import it).
- **The person check in chunks, identical results (b3cfdf9):** a stretch over 1 GB of decoded frames is analysed a
  chunk at a time (faces first, then their crops from the frames decoded again). Tested on the Zendaya RAW: the same
  tracks, boxes and speaking scores with the chunk set to one second.

## 6. Batch mode (b3cfdf9)

```
..\..\.venv\Scripts\python -m match_cuts batch "D:\to do" --out ..\..\output
```

Each subfolder of the folder (with `competitor.mp4` and `raw.mp4`) runs as its own `--premiere --fast` process,
one after another, into `<out>\<subfolder>\` (`-2`, `-3` when that exists); a failure or crash never stops the
batch; `batch_summary.md` / `.json` is rewritten after every video: run folder, run time, PASS / FAIL (the
Premiere deliverables' hard checks and coverage, as check-all -- the recreation criteria c2-c5 fail on every real
video and are left out), why it failed, whether 1_edit.xml uses media outside its folder, and the run's "check by
hand" list. Options for every run go after `--` (e.g. `-- --keep-speed`).

**Each output keeps working after you move or delete the input folder:** the RAW is placed in the run's
`extras\media\` as a hard link (same drive: no copy, no extra space). A RAW over 2 GB used to be referenced where it
lay -- that is why output/020's XML pointed at `input\raw.mp4`. Tested: video018 and video020
(`work\t17\batch_out`), then the input folder renamed: both XMLs point only at their own `extras\media\raw.mp4`,
which are still there (1.2 GB and 3.1 GB, not copied).

Result: video018 PASS (7m49s), video020 FAIL (6m56s) on one 1-frame flash at a scene cut -- fixed afterwards
(d516984).

## 7. The loop

No separate loop item: the time went into what check-all found (see the last section).

## Before / after per video

Before: last night's final runs (the code before tonight) scored by today's scorer. After: tonight's check-all
(the code of d516984; zendaya re-run on 12032ad). Captions exact / word errors; cuts: your cut
points reproduced within 2 frames (+ near), the tool's length minus yours.

| video | hard checks | captions exact | word errors | cuts reproduced (near) | length gap |
|---|---|---|---|---|---|
| video017-fixed | PASS -> PASS | 23 -> 23 / 43 | 11.0 -> 11.0 % | 4 (+2) -> 4 (+2) / 12 | +8.48 -> +8.48 s |
| video018-fixed | PASS -> PASS | 10 -> 10 / 49 | 20.9 -> 20.9 % | 0 (+6) -> 0 (+6) / 15 | +5.02 -> +5.02 s |
| video020-fixed (new) | (output/020: PASS) -> **FAIL** | 0 -> 1 / 30 | 61.8 -> 71.4 % | 0 (+1) -> 0 (+1) / 11 | +0.28 -> +0.23 s |
| video1 | PASS -> PASS | 16 -> **20** / 111 | 10.0 -> 11.2 % | 9 (+8) -> 6 (+11) / 29 | +3.33 -> +3.13 s |
| video2 | PASS -> PASS | 0 -> 0 / 21 | 9.5 -> 9.5 % | 1 -> 1 / 1 | +0.02 -> +0.02 s |
| video3 | PASS -> PASS | 3 -> 3 / 71 | 24.0 -> **47.5 %** | 1 (+0) -> **3** (+0) / 11 | -9.68 -> **+4.80 s** |
| video4 | PASS -> PASS | 7 -> 8 / 38 | 14.9 -> 16.1 % | 2 (+2) -> 2 (+2) / 7 | -1.35 -> -1.08 s |
| video5 | PASS -> PASS | 2 -> 2 / 37 | 34.2 -> 32.9 % | 1 (+3) -> 1 (+3) / 14 | +3.67 -> +3.67 s |
| zendaya-age | PASS -> PASS | 2 -> 2 / 19 | 22.5 -> 22.5 % | 1 (+3) -> 1 (+2) / 9 | +3.25 -> +3.80 s |
| spiderman-school | PASS -> PASS | 10 -> 9 / 64 | 8.2 -> 8.2 % | 4 (+9) -> 4 (+9) / 21 | +6.42 -> +6.43 s |
| deadpool (competitor's own edit) | PASS -> PASS | 46 -> 46 / 60 | 5.6 -> 5.6 % | | |
| zendaya (no key) | PASS -> PASS (failed in the first check-all, fixed: 12032ad) | | | | |

Totals: captions exact **119 -> 124** of 543; cuts reproduced 23 -> 22 of 130 (near 34 -> 36).

- **video3** is rule b at work: its competitor replaced the sound with a voice-over, and the tool used to mute those
  stretches and then cut them as silence. Now every picture plays its own RAW sound, as you asked, so those
  stretches stay: the edit is closer to yours in length and cuts (-9.7 s -> +4.8 s, 1 -> 3 of your cuts), but the
  RAW's own words there are captioned, which your captions do not have (word errors 24 -> 47.5 %). If you cut those
  stretches by hand, tell me what you keep there and I can make that the rule.
- **video020-fixed** is new tonight: its RAW is the 4K file squeezed into 80 MB for GitHub, and on that copy the
  analysis comes out far worse than on the real file (40 segments with speeds like 50 % and 120 %, where the
  full-size run finds 6 clean ones). Its run fails XML REPEAT / SPEECH / FLASH and one --min-move change on those
  segments. The full-size 020 passes every check (output/020, re-exported tonight); `check-all --full-size` runs this
  case on the real file. Whether the three new rules add to the small copy's failures I could not separate in time
  (it takes 67 minutes a run).
- video018 is unchanged on the small copy; on the full-size RAW (output/019) "insurance" is back (5).

## What did not work / was not done

- **video3's captions** got worse with rule b (above).
- **video020-fixed on the small copy fails its hard checks** (above): not traced to its cause; the full-size runs
  pass.
- **Action captions** (`*knocks on the door*`) cannot be generated: the competitor does not show them.
- **The loop (7):** no separate loop item. The time after items 1-6 went into what check-all found: two
  regressions of the new rules on zendaya (fixed, 12032ad), the video018 full-size "insurance" ending, the
  --fast flash on 020, and the ending flash on 019 -- each committed separately.
- **Not verified in Premiere** (I cannot run it here): the Flop flip, the scene cuts' linked pieces, and the
  re-imported 019/020 -- please check the flip and that each V1 piece moves with its A1 piece.
- **"Got gotta call"** in output/019 (5): the recheck's "I" -> "got" on the real audio is not caught yet.
- **Split errors** stay the largest caption gap (192 of 385 of your breaks differ); the settings grid found no rule
  that helps every video beyond the small change kept.
- The **"cap" half of "cap or flag big moves"**: flagged only (a cap would fail the speech check).
