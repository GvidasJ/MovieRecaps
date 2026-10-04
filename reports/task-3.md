# Task 3 — Second video (verified; done in commit `14d0ef4`)

## Verified on the Zendaya clip (`tests/real/zendaya`)

The competitor shows another interview between competitor frames 151 and 316. It isn't in your RAW, and its speech
isn't in the RAW audio. A fresh `--premiere` run on the current code (`work/runs/task3/zendaya/001`):

| requirement | result |
|---|---|
| V1 **and** A1 empty for exactly that length | sequence frames 289–619: **330 frames = 5.50 s**, the competitor's stretch exactly. No V1 or A1 item overlaps it; the clip before ends on frame 289 and the next starts on 619 |
| an `OTHER VIDEO` marker | `OTHER VIDEO – not in RAW (00:00:04:49–00:00:10:19)`. Its comment holds the competitor's timecodes (00:00:05:01–00:00:10:16) and what it says |
| its captions come from the competitor | the competitor shows "I can't really explain it" and "I haven't got the words" there. `2_captions.srt` has `I can't really` / `explain it` / `I haven't got` / `the words`: the competitor's words, split by your caption rules, timed to the competitor's audio. The summary says "captions: 4 copied from the competitor" |
| the checks allow the gap | `XML OTHER VIDEO` OK (it fails only a stretch cut shorter or played over), `XML GAP` OK, `XML FLASH` OK (an empty stretch of another video is allowed at any length), `XML SILENCE` OK (no silence is cut inside it), `XML LINK` OK, `XML PERSON` OK. 9.8 deliverables **PASS** |
| … and list it | see below: before this task only the summary's own *Other video* section and the person check listed it |

## What I changed

You asked for the gap, audio, link and framing checks to each **list** the stretch. Before this task, only the end
summary's *Other video (not in RAW)* section and the new person check (task 2) did. The audio, link and gap checks
allowed it silently. Each check now lists it:

- **Audio check:** *V1 clips without their audio on A1 (on purpose)* shows `OTHER VIDEO 00:00:04:49-00:00:10:19
  (5.50 s): A1 empty on purpose -- another video's sound goes there`.
- **Link check:** *Linked clips: … 1 not linked on purpose* shows `OTHER VIDEO …: V1 and A1 empty on purpose --
  nothing to link`.
- **Gap, flash and silence checks:** `report.md` → *Premiere XML checks* has a new list, *Left empty on purpose*,
  with `OTHER VIDEO …: V1 empty on purpose -- no clip to cover the window, not a black flash, no silence cut`.
- **Framing (person) check:** it already listed `OTHER VIDEO …: another video's stretch, no clip -- not checked`
  (task 2).

`tests/test_other_video.py` now also asserts that the gap, audio and link checks each list the stretch exactly once.

## Test results

- Unit suite: **951 passed, 12 skipped, 0 failed** (the same platform-only skips as before).
- Slow suite: **21 passed, 0 failed, 63 skipped** (7.5 min; the same opt-in skips as before).
- The task-3 tests (`test_other_video.py`, 11 tests; `test_zendaya.py`; the Zendaya fixtures) all pass.

## What to check in Premiere

- In the Zendaya edit, the 5.50 s gap at 00:00:04:49–00:00:10:19 is empty on V1 and A1 and has the `OTHER VIDEO`
  marker. That's where the other interview goes.
- Its 4 captions sit in the gap, timed to the other interview's speech.

## Found while verifying (for task 4)

**`srt/zendaya-tom-holland.srt` is not the `tests/real/zendaya` clip.**

- **What the SRT says:** "Name, surname and age… Zendaya Coleman… And I am 25… Tom Holland… a son to a married
  couple… And I have a daughter…".
- **What the test clip says:** "my favourite is that one where you just start singing… the Billy Elliot one… I can't
  really explain it".
- **Where the SRT belongs:** the video of **run 011** (`output/011`, a 19 s competitor cut from a 90.75 s RAW). Its
  own captions start "Name | surname and | age | Zendaya Coleman | And I am 25". This is also the video of
  `generated_edit.xml`, `my_fixed_edit.xml` and `raw_audio.m4a` at the repo root.

In task 4 I use run 011's video (`output/011/extras/media/`) as the answer key for `zendaya-tom-holland.srt`, as a new
committed test case. `tests/real/zendaya` stays a test clip for the edit, the framing and the other video.
