# Day 13 — helper "editnotes": small edit notes fixed by the tool itself (section 9.1 and 9.2)

All in `tools\match_cuts\match_cuts\export_xml_edl.py` (no cached stage: no re-run of any analysis), in one block of
new functions after `snap_to_shots` (`# Day 13 (section 9 ...)`), plus small hooks in `write_premiere_xml`,
`validate_premiere_exports`, `played_through`, `rate_problems` / `final_xml_problems`, and the report lines in
`pipeline.py` (`apply_final`, `hand_checks`), `cli.py` (end summary) and `report.py` (`_premiere_checks`). Each fix has a
switch at the top of the block: `A1_FOLLOWS_V1`, `END_FLASH_CUT`, `CUT_FLASH_MAX` (0 = off).

## What the tool now fixes by itself

| note | what was wrong | the fix |
|---|---|---|
| **9.2** `XML A1 S12: range 650-706 is not a V1 cut range` (Are We There Yet, 3 of them) | `snap_to_shots` moves a V1 cut one frame onto the RAW's own shot change; A1 kept the competitor's cut: a one-frame split edit everywhere, and under a V1 clip of several competitor cuts (S12+S13) an A1 range no V1 clip has | `follow_snapped_cuts`: A1 starts and ends with its V1 clip -- the A1 clip before ends where its picture now ends, the one after starts where its picture now starts, on the same source line (each picture's own sound, a frame more or less of it: still in sync). Not an audio line (another moment of the RAW than the picture). Only in the default mode (the competitor's cut points): with `--remove-silence` the speech-safe step put the A1 cuts off speech and they stay. Your video018 fix has its A1 cuts on the moved cuts too. |
| **9.1 (a)** the very end: `XML FLASH S26 at 00:00:24:22: 1 frame(s) of a different RAW shot` (Are We There Yet), video12 (2 frames at 00:00:32:00), video6 (1 frame at 00:00:21:00) -- check-all's two "competitor's own last frames" FAILs | the competitor's last clip ran a frame or two past its last shot: the edit's last frames show the first frames of the RAW's next shot; nothing comes after them, so no clip can cover them | `end_flash_frames` + `cut_end`: **1_edit.xml ends on that shot change** (V1, A1, markers, the frame / watermark tracks and the last caption end there). Only a sliver: the last clip's own take running on into a new shot shorter than 0.25 s. A real last cut of the competitor's (another moment of the RAW) is not touched. |
| **9.1 (b)** `XML FLASH S17+S18+S19+S20 at 00:00:07:12: 1 frame(s) of a RAW shot at the framing of another clip` + `3 frame(s) of a different RAW shot` (Family Guy S09E11) | not a flash: the cartoon itself has three tiny shots in a row (RAW frames 1 + 2 + 1, an effect), the edit plays them straight through in one take, the competitor's reframe is exactly on the first of them. The check judged the short shots one by one and left the middle one between halves of the others. | the check (`played_through`): a burst of short shots the edit plays straight through as a whole is the RAW's own -- all its changes become one in its middle (as night 4 did for one short shot). A cut into the burst is still a flash. |
| **9.1 (c)** a flash of 1-2 frames at a cut inside the edit (at the RAW's rate) | a clip's take ran 1-2 frames past its own shot, or the next clip started 1-2 frames before its own | `fix_cut_flashes`: the cut moves those frames onto the RAW's shot change (the clip whose shot they are plays them, on its own line; A1 moves with it; captions on that cut too). Kept only if the flash check of the whole edit then finds fewer problems and nothing new, no clip plays RAW another clip plays, and every A1 cut there can move with its picture. **No current case needs it** (all today's cut flashes were already handled by snap_to_shots / rerate_edit); it is the safety net for the next one. |

Every fix is written into the run's **report.md** (section *Premiere checks*: "**Fixed in the edit by itself**") and
the end summary ("Fixed in the edit by itself: N" under *Check by hand*), at the delivered 1_edit.xml's timecodes, e.g.
"1 flash frame removed at 00:00:24:22 (S26): the edit's last frame showed the first frame of the RAW's next shot --
1_edit.xml now ends on that shot change".

## Decisions (you were away)

* **The end sliver is cut only in the delivered 1_edit.xml at the RAW's rate**, not in the checked 60 fps edit
  (extras/edit_60fps.xml). Reason: check-all scores the 60 fps edit, and the captions are transcribed from it -- cutting
  it there would re-transcribe every such video (the known caption noise of any edit change). The 60 fps check still
  lists that flash in the log (as it did for flashes that vanish at the RAW's rate before), but the run's result reads
  the delivered XML. The XML RATE check knows those frames were cut on purpose (`end_cut`).
* **video6 vs video12**: in your video12 fix (night 11) you cut exactly those 2 frames -- the tool's 1_edit.xml now ends
  on RAW frame 1530, where your fix ends. In your video6 fix (night 7) you kept the 1 frame. I followed the newer fix and
  today's wish ("remove them when it's clearly a flash" -- the example in today's list, "1 flash frame removed at
  00:00:24:22 (S26)", is exactly such an ending): video6's 1_edit.xml is now 1 frame shorter than your fix. (Your
  "ignore my trims at the ending" is about what Learn reads from your fixes; nothing here is learned from them.)
* **A 60 fps RAW** (no re-rate step) keeps an end sliver (it would have to change the checked edit and the captions).
* **A sliver at the very start** of the edit is not cut (it would move every caption and cut by a frame).
* **video020-fixed** (`--fast`, the known 12.6 s OTHER VIDEO): not chased -- its flash sits next to another video's
  stretch, which must stay empty; the cut fixer only moves cuts between two clips. Still the known FAIL.
* XML PERSON and XML ENDING of the Family Guy run are not mine (left).

## Per case

| case | before | after |
|---|---|---|
| Are We There Yet (laptop run, published) | 3 × `XML A1 ... is not a V1 cut range`, `XML FLASH S26 at 00:00:24:22` | not re-run (see *Not done*); checked on its own files: the 3 A1 notes are the snapped cuts S11\|S12+S13, S16+S17\|S18+S19+S20, S21+S22\|S23+S24+S25 -- in the checked 60 fps edit A1 was cut a frame before V1 there (now cut with it; the same structure is a unit test). Your delivered 23.976 fps 1_edit.xml already had V1 and A1 together (the RAW's rate rounds both onto one frame): the notes came from the 60 fps check, which the last step does not repeat. On the delivered 1_edit.xml the end fix cuts 1 frame (ends 00:00:24:22) and the flash check then finds nothing. |
| Family Guy 1999-present (laptop run) | 2 × `XML FLASH S17+...` at 00:00:07:12 / 07:13 (and 07:31 / 07:32 at 60 fps) | the old check reproduces both notes exactly on its edit_60fps.xml and 1_edit.xml; the new check finds none on either (XML PERSON / XML ENDING stay) |
| video12-fixed | FAIL: `XML FLASH S30+S31+S32 at 00:00:32:00: 2 frame(s)` | 1_edit.xml 770 -> 768 frames, ends on RAW 1530 (your fix's end); 0 problems |
| video6-fixed | FAIL: `XML FLASH S08+S09+S10+S11 at 00:00:21:00: 1 frame(s)` | 1_edit.xml 505 -> 504 frames; 0 problems |
| laptop013-fixed | -- | edit_60fps.xml: A1 cut at 653 -> 654 with its snapped V1 cut (1_edit.xml unchanged) |
| laptop004, laptop010, video7, video8, video9, video11, video14, video15 | -- | identical V1 / A1 lists at both rates |

A sweep of every finished run's own files (all 28 day-12 check-all runs, every run in D:\Shared\Projects, output\
laptop_video10-19): A1 moves only in laptop013 (above) and, among your real runs, in Are We There Yet (its 3 notes),
Always Sunny "Surviving or Looting" (1 cut), mindculpt (1) and "interesting fact" (1) -- those three had no note (one
cut each, the sound a frame off its picture in the 60 fps edit); the end cut only in video6, video12 / laptop_video12
and Are We There Yet; the cut fixer nowhere (no such flash today).

How these were compared: the "framing" helper's pickles of each check-all case's export inputs
(work\t28\framing\dumps: the cut list, config and ripple of the first write_premiere_xml call of a cached run) replayed
on the CPU with the code before my change (git HEAD of day 12) and with mine: the 60 fps plan + its validation, then
1_edit.xml at the RAW's rate + its hard checks (scripts in my scratchpad: en_replay.py / en_cmp.py). The replayed old
code gives exactly day 12's XMLs (checked on video14 and laptop013). The flash check change was swept over every
day-12 check-all XML and every run in D:\Shared\Projects and output\laptop_video1x: only Family Guy changes.

## For the lead (merge / check by hand)

* Touched: `export_xml_edl.py` (new block after `snap_to_shots`; hooks in `write_premiere_xml` around the snap / re-rate
  steps and its return dict; `validate_premiere_exports` after its `snap_to_shots`; `played_through`; `rate_problems`
  and `final_xml_problems` got an `end_cut` argument), `pipeline.py` (`apply_final`: passes `end_cut`, keeps
  `edit_fixes` in ctx.premiere_xml; `hand_checks`: `edit_fixes`), `cli.py` (one block in the end summary), `report.py`
  (`_premiere_checks`), `tests/test_flash_018.py` (one expectation: A1 now moves with the snapped cuts).
* The captions follow the end cut through write_premiere_xml's `pos_map` (apply_final -> final_srt). If the
  "captions" helper changes how 2_captions.srt is written at the RAW's rate, it must keep reading that `pos_map` (or
  end the last caption at the result's `frames`).
* The 60 fps check's warning lines ("Premiere XML: 1 flash frame(s) ... the run fails", "XML/EDL re-parse validation
  failed: [...]") still name an end flash the delivered 1_edit.xml no longer has -- as they already did before for
  flashes that vanish at the RAW's rate (Always Sunny's S31). The run's result and its note read the delivered XML.
* Seen, not mine: `XML SYNC ... the RAW skips 42 ms at the cut: one take, cut` at the RAW's rate (video14-fixed in day
  12's checkallB run, Always Sunny's laptop run) -- a one-frame skip inside one take after the re-rate.

## Not done

* **A cached end-to-end check-all re-run** (the brief's main test): one try of video12 at 08:09 died in its analysis
  stage with `SystemError: <built-in function resize> returned a result with an exception set` -- the PC had run out of
  commit memory (the lead's check-all, the framing helper's dumps and mine together); a second try I stopped at 08:20
  when the lead asked (2 GB of commit memory left). The pipeline's last step (apply_final: 1_edit.xml at the RAW's rate,
  its hard checks, the SRT, the summary and report lines) is covered by a unit test on the CPU instead
  (`test_the_run_writes_and_lists_the_fix`). The lead's final check-all will show video6 / video12 passing.
* A re-run of the two real projects (not needed: their own files show the before / after).
