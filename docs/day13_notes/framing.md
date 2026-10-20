# Day 13 -- helper "framing": section 8, re-frame onto the person speaking in more clips

## What I built

A new framing rule in `tools/match_cuts/match_cuts/export_xml_edl.py`, in its own block (`SPEAKER_CENTRE`,
`_speaker_centre`, `_speaker_strength`, `_shot_pieces`), called once at the very end of the framing in
`premiere_clips` (right after day 12's `_speaker_cut_off`). **On by default**; one run without it:
`MATCH_CUTS_SPEAKER_CENTRE=0` (`=1` forces it on). No other function changed.

In plain words: every clip is looked at one RAW shot at a time (as the XML's Scene Edit Detection cuts it); the pieces
of one take inside one RAW shot that show one framing are judged and moved together. When the person speaking there is
**clear**, the picture slides sideways -- zoom kept, the window always covered -- so this person's face sits in the
middle of the window; when their face is cut off at the window's edge, only until it is inside with a little margin
(a slide, not a jump onto another part of the picture: night 8's lesson from 021 S54 / S56, and video19's Family Guy
doctor -- 85 px instead of 466 px, which would have pushed Lois, a face the detection does not find, out). Each moved
clip says so in its XML comment and in the run's end summary ("centred on the person speaking (RAW x ..., Light-ASD
..., ...% of the speech): the picture moved ... px sideways, zoom kept" / "slid onto the person speaking, cut off at
the window's edge ...").

"Clear" (Light-ASD, the person SPEAKING -- never just the biggest face) means all of:

* Light-ASD's top speaking face on at least 60 % of the speech frames, its mean score at least 0.5 and at least 1.0
  above every other face there (two faces that might both be speaking is no clear speaker -- video11 at 44 s: the
  "speaker" 0.73 was 0.14 above the man next to him, and was the wrong man);
* a face with nobody else found next to it: mean score at least 1.5 (the one the competitor shows may be a face the
  detection missed -- 021 at 54 s: the man laughing in the middle was not found; 0.6 on the man beside him);
* the face detected on at least 98 % of those frames (cartoon faces flicker: video6's 82-95 %; live faces 100 %);
* the face at least 22 % of the picture's height (smaller: a wide shot -- video15's delivery room, 15-19 %: kept);
* the face inside the competitor's own window, and that window wider than the template's (the competitor showed more
  than the template can, so someone has to choose; a competitor that shows no more made its own choice).

Not moved, also: a move under 10 % of the window's width (centred enough: video11 at 43 s, 61 px, you left it; every
"centred enough" case on the keyed videos is under 65 px) or over 60 %; a close-up already shown whole (a face wider
than half the window: video7, you kept those -- without this guard video7 loses one); a move that would push out
someone the window shows whole (a two-shot stays: 021's two women at 82 s); a move that would hide the person of
another piece of the same run; a change under `--min-move` next to a neighbour in the same RAW shot -- read exactly as
the XML's own `--min-move` check reads it, so the rule never makes a hard check fail; a flipped / turned clip or a
replaced spot (B-roll / not in the RAW).

## How I tested it (and why it is safe enough to be on)

1. **Exact replays.** For every keyed case I ran check-all's own command once with the frozen day-12 code (all
   stages from the cache) and saved the inputs of the planned Premiere export (cut list, settings with the people /
   speech / shots context, silence plan). Re-writing the XML from those inputs with the rule off gives check-all's
   day-12 framing scores exactly (all cases), so the replay with the rule on is what check-all will see. The XML's own
   hard checks (validate_premiere_exports, without the EDL and the speech map) were run on both XMLs: the rule adds no
   hard-check failure anywhere. (Four were dumped the fast way, see below.)
2. **Pictures of every move** (RAW frame, the tool's window before = red, after = blue, yours = green, faces with
   their Light-ASD score): `work\t28\framing\pics\<tag>\<case>_moves.png`. Every move is onto the person speaking; none
   onto another person.
3. **The histories:** video6 (a cartoon, you kept the middle on all 9 clips): nothing moves (the flickering cartoon
   faces, unclear speakers). laptop004 (a cartoon, you moved re-framed clips back): nothing moves (no clear speaker).
   021 (re-framing onto "the speaker" put 3 of 5 clips on the wrong person on night 3): see the table.
4. Unit tests: `tools/match_cuts/tests/test_day13_framing.py` (the real layouts' numbers of video11, video14, video6,
   video15, the 021 situations).

## Results: check-all's framing column (clips framed as you did, within 40 px / 3 %)

All 15 keyed videos, replayed exactly (the planned XML written again from each case's saved inputs, rule on vs off;
"hard checks" = the XML's own checks on both XMLs):

| video | framing before -> after | clips the rule moved (RAW s: tool's px -> new; yours) | hard checks |
|---|---|---|---|
| video11 | 1/8 -> **2/8** | 30.1-32.8: -15 -> +211 (you +188: same) | unchanged |
| video14 | 0/10 -> **4/10** | 45.1-53.2 (one shot, 4 clips): +0 -> -169 (you -154: same x4); 36.7-41.6: +402 -> +610 (you +651, closer); 30.1-32.7: +348 -> +476 (you +214: you left room on his right; the same man, now centred) | unchanged |
| laptop013 | 1/6 -> **2/6** | 38.0-40.3: +0 -> +244 (you +228: same); 33.8-38.0: +0 -> -276 (you -361, closer); 41.3-41.9: +0 -> -394 (the man; next to yours in the picture, but check-all compares your clip there at a moment the tool cut out) | unchanged |
| video021 | 8/21 -> 8/21 | none (wide shots, unclear speakers, replaced spots; the two-shot at 82 s and the laughing men at 54 s stay) | unchanged |
| video6 | 9/9 -> 9/9 | none (a cartoon: the faces flicker) | unchanged (1 known FAIL) |
| video12 | 4/16 -> 4/16 | none (long whip-pan takes: no clear speaker per take) | unchanged (1 known FAIL) |
| video15 | 12/21 -> 12/21 | none (wide shots; the husband alone at Light-ASD 1.0) | unchanged |
| video7 | 5/10 -> 5/10 | none (close-ups shown whole) | unchanged |
| laptop010 | 11/13 -> 11/13 | none (the competitor centres its speakers already) | unchanged |
| mike005 | 0/14 -> 0/14 | none | unchanged |
| laptop009, chocolate, video8, video9, video10 | 15/16, 5/5, 4/11, 4/4, 5/6 -> the same | none | unchanged |
| laptop004 (no framing key in the case: your project read) | 1/7 -> 1/7 | none (a cartoon, no clear speaker) | unchanged |

**check-all's keyed videos: 84/170 -> 90/170** (72/139 -> 74/139 on the 13 it had before video14 / video15); no clip of
any video worse; no new hard-check failure.

(chocolate, laptop009, mike005 and 021 were dumped the fast way -- the PC's memory was full most of the morning and my
runs only start with 30 GB free, the lead's rule: their cut list read from the day-12 run instead of computing the
segments stage again; checked on video14, whose fast and full dumps replay identically.)

Pictures: `work\t28\framing\pics\final\<case>_moves.png` (every move, three moments of each: the tool's window before =
red, after = blue, yours = green, faces with their Light-ASD mean), the overviews of your own moves in
`pics\<case>.png`, rough previews of the videos without a key in `pics\approx2\`.

Your newest fixes as test cases (video16 Always Sunny, video20 mindculpt) had no stage caches on this PC (the lead's
check-all had not reached them), so they could not be replayed.

## What I decided on my own (the user is away)

1. **Centre the person speaking** (not "slide just until they are in", day 12's way): your moves land closest to the
   speaker in the middle of the window -- video11 +188 / +222 px (the rule: +211 / +230), video14 -154 px on four
   clips (the rule: -169), laptop013 +228 (the rule: +244).
2. **On by default.** The replays: framing better on the keyed videos and no clip of any video worse, no new
   hard-check failure, and every move onto the person speaking (pictures). If the lead's check-all disagrees, one
   switch turns it off (`SPEAKER_CENTRE = False`, or `MATCH_CUTS_SPEAKER_CENTRE=0` for one run).
3. **Never against the XML's own `--min-move` check.** That check reads a clip cut at a RAW shot change as ending half
   a frame (one 60 fps tick) later, so about half of the RAW shot changes look like "the same shot" to it; a framing
   change under 250 px there passes only when the framing before does not show the person after the cut. The rule
   follows that reading exactly (it cost one of your moves: video11 at 46.6 s, +245 px). I did not change the check
   itself (other helpers edit the validation today; a hard check is not mine to loosen).
4. **Not attempted:** following the person speaking inside one long take that never changes shot (video12, The
   Office's whip pans: you cut the take into pieces and framed each on whoever spoke) -- that needs new cuts, not a
   framing rule; zooming (some of your fixes zoom too: mike005 62 % for the tool's 57.5 %).

## Not done

* The exact replays of chocolate, mike005, laptop009, 021 and the unkeyed videos, and a whole cached re-run as a last
  check: the PC's commit memory was at 0.5-25 GB (of 112) from 08:20 to 10:00 with the lead's check-all and other
  helpers' test runs; my runs only start at 30 GB free (the lead's rule). The rough previews above stand in for them;
  the lead's final check-all is the real check.
* Your newest two fixes (video16 Always Sunny, video20 mindculpt): no stage caches here yet, so not replayed.

## Tests

* `tools/match_cuts/tests/test_day13_framing.py`: 18 tests with the real layouts' numbers (video11's captain centred
  as you did; the unsure "speaker" of video11 at 44 s; the biggest face is never a speaker; centred enough at 61 px;
  video6's flickering cartoon face; video15's wide shot; the competitor's own close framing; a speaker the competitor
  did not show; video14's two pieces of one shot moving together; --min-move against a neighbour; the XML check's
  half-tick reading at a shot change; one RAW shot of a clip moving, a clip already framed per shot keeping its other
  shots; 021's two-shot and lone face; the switch; on by default).
* The whole tool suite with the graphics card hidden: 1438 passed, 96 skipped, 8 failed -- all 8 in the worker-pool
  tests (test_pools, test_refine, test_visual_match: workers that could not start while the PC's memory was full);
  those three files again a few minutes later: 71 passed, 4 skipped, 0 failed. So: 1446 passed, 96 skipped.
* test_day12.py, test_night8.py, test_speakers.py, test_export_xml_edl.py, test_keep_speed.py, test_night4.py,
  test_speech.py with the rule on: 176 passed, 4 skipped.

## Files

* `tools/match_cuts/match_cuts/export_xml_edl.py`: one new block right after day 12's `_speaker_cut_off` (constants,
  `_speaker_centre_on`, `_speaker_strength`, `_shot_pieces`, `_speaker_centre`) and three lines in `premiere_clips`
  after the day-12 call. Nothing else in the file changed.
* `tools/match_cuts/tests/test_day13_framing.py` (new), this file.
* My scripts (dump / replay / pictures): `work\t28\framing\scripts\`.
