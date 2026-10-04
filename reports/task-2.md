# Task 2 — Always show the person talking

## What changed

**Who is in the picture, and who is speaking** (`people.py`, new):

- **Faces: YuNet.** This is OpenCV's FaceDetectorYN, a modern CNN face detector under the MIT licence. It replaces the
  old Haar cascades, which have been removed. It runs on every frame the edit plays, at 25 frames a second and ±3 s
  around each clip. It runs on the CPU, at about 10 ms a frame, which is fast enough that the GPU wouldn't help.
  - **Why it's better:** on the Deadpool guest, YuNet's boxes sit tightly on the face, while Haar's leaned onto his
    ear and once took a mug for a face (`work/t2/s21_faces.jpg`).
- **Tracks.** A face is followed from frame to frame and never across a shot change in the RAW. A small face that
  never speaks is ignored, so the Spider-Man poster behind Zendaya and Tom no longer counts as a person.
- **Who speaks: Light-ASD.** This is an active-speaker model (CVPR 2023, MIT licence, about 1M parameters). It scores
  how well each face's mouth moves with the sound, frame by frame, and runs **on your RTX 5080 with CUDA** (the log
  says `speaking scored by Light-ASD on cuda`).
  - **Inputs:** 112×112 face crops and 13 MFCCs, computed exactly as in the authors' training. My MFCC code matches
    `python_speech_features` to 0.0.
  - **Speed:** scoring all faces of a video takes 18–32 s.
- **Each clip's speaker** is the face that is the top speaking face on most of the clip's speech frames. "Speech"
  comes from the existing speech map of the RAW. If no face's mouth goes with the sound (for example a voice off
  camera), the biggest face is used, as you asked.

**The check and the fix** (`speakers.py`, new, plus `export_xml_edl.py`):

1. **What must be in the window.** While someone speaks, that person's face must be fully inside the window
   x 42–1039, y 555–1591. The face box spans the 10th to 90th percentile of its edges over the speech, so a head
   turning for a frame doesn't count. When nobody speaks, at least one person must be inside.
2. **The fix when a clip fails.** The zoom is kept and the picture slides sideways to centre the person, still
   covering the window. It moves up or down only if the face is cut off at the top or bottom.
   - **Stretches of clips:** a stretch of clips that shares one framing gets one position, if one position shows
     everyone; otherwise each clip is centred on its own person.
   - **Priority:** this beats the competitor's framing and the 250 px rule.
3. **The 250 px rule** now holds a framing only inside one shot of the RAW. At a RAW shot change, the new shot
   chooses its framing fresh. A framing that would hide the clip's person is never held.
   - **Clip comments:** every clip affected by either rule says so in its Premiere comment.
   - **Shot changes:** these are found over the whole RAW, so a real cut in the RAW counts, not just a long jump in
     time.
4. **Hard check `XML PERSON`.** It re-reads every clip's framing from the final XML (Position, Scale, Flip, as Premiere
   reads them) and fails the run when a clip doesn't show its person. A clip with nobody in the picture and an
   `OTHER VIDEO` stretch are listed, not failed.
5. **End summary and `report.md`.** *The person speaking in the picture* says how many clips were checked and lists
   **every re-framed clip with its time in the edit**, who it shows, and how far the picture moved.
6. **RAW-only runs** (no competitor) get the same analysis, re-framing and check.

**Fixed first, because these test clips would otherwise fail** (pre-existing, from task 1's report). The Spider-Man
school export had two holes where V1 **and** A1 were both empty (14 and 8 frames), plus three audio cuts inside
speech.

- **The holes.** A 1-frame piece (S11) repeated the end of the clip before it, inside the word "kids,". Its start had
  to move past its own frame. That trim cut into the next clip, and the extensions belonged to no clip, which left a
  gap.
  - **Fix:** the clip before now plays on through the word, and the sliver goes.
- **Audio cut inside a word.** An audio end where the picture continues into a muted cutaway (S31, inside
  "Spider-Man.") was never moved.
  - **Fix:** it now plays on into the silence until the word ends, or stops before the word when the silence is
    too short.
- **Safety net.** An extension left without a clip is now logged as an error.

## Results

| video | V1 clips checked | re-framed to show the person speaking | all Premiere XML checks (incl. `XML PERSON`) | people analysis (RTX 5080) |
|---|---|---|---|---|
| Zendaya | 10: the speaker found by Light-ASD in all 10 | **7** | **pass** (the `OTHER VIDEO` stretch listed) | 17.6 s |
| Deadpool | 14: the speaker found in all 14 | 0 (the competitor already shows the speaker) | **pass** | 19.9 s |
| Spider-Man school, full-size RAW | 17: 16 speaker, 1 nobody speaking | 0 (already shows the speaker) | **pass** (it failed `SPEECH` and `FLASH` before; see above) | 32.1 s |

**Zendaya before and after.** Each image shows every clip's middle frame exactly as `1_edit.xml` places it, with the
template window outlined in yellow.

- **Before** (`reports/task-2/zendaya-before.jpg`): S02–S08 show Tom while Zendaya talks, and S18–S26 show Zendaya
  laughing while Tom talks.
- **After** (`reports/task-2/zendaya-after.jpg`): every clip shows the person speaking, with the face fully inside
  the window.

I checked the speaker decisions by eye on mouth crops (`work/t2/mouths.jpg`). For example, at 166.7–167.6 s Tom's
mouth moves while Zendaya laughs and turns away.

The re-framed Zendaya clips, as the end summary lists them:

```
00:00:00:00  S01+...+S10: re-framed to show the person speaking (RAW x 1407): the competitor's framing showed someone else -- the picture moved -1162 px sideways, zoom kept
00:00:13:02  S14+S15+S17: ... (RAW x 437) ... +1147 px sideways, zoom kept
00:00:14:26  S18 / 00:00:14:38  S19 / 00:00:14:48  S20 / 00:00:15:00  S21 / 00:00:15:04  S22+...+S26: ... Tom (RAW x 538-559), +1147 px
```

**Tests.**

- **Unit suite: 951 passed, 12 skipped, 0 failed.** The 16 new tests:
  - **The person check and the re-frame:** the zoom is kept, the person is centred, the picture still covers the
    window, a face cut off at the top moves down, and a face too wide for the window is reported.
  - **Faces and speakers:** tracks break at a RAW shot change, the dominant speaker and the biggest face are chosen
    as described, and the MFCC matches `python_speech_features` exactly. YuNet finds the two Zendaya guests and not
    the poster faces.
  - **The framing rules:** the 250 px hold applies only inside one shot and never hides the person, and a run of
    clips gets one position.
  - **The speech fixes:** the swallowed sliver, and the audio end next to a muted piece.
  - **Light-ASD on the real Zendaya RAW** picks Tom and Zendaya correctly. This test runs on the GPU in about 10 s.
- **Slow suite: 21 passed, 0 failed, 63 skipped** (7.5 min; the skips are the same opt-in film24 and Linux-ffmpeg checks as in task 1).

## What to check in Premiere

1. **Zendaya:** in the timeline, look at the 7 re-framed clips listed above, at 00:00:00:00, 13:02, 14:26, 14:38,
   14:48, 15:00 and 15:04. Each should show the person whose voice you hear, with their face inside the template
   window. Each clip's comment (Project panel or clip properties, *mastercomment2*) says what moved and why.
2. **Fresh framings at a RAW shot change:** where a clip now starts a new framing although the old 250 px rule would
   have held the previous one, its comment says `its own framing: a new shot of the RAW`. There is 1 such clip on
   Zendaya (S12+S13 at 00:00:10:19, an 8 px move; its second piece after a removed silence carries the same note)
   and 2 on Spider-Man school (S17+S18 at 00:00:21:24, 13 px; S23 at 00:00:29:33, 51 px). The moves are this small
   because the competitor framed those shots the same way. Tell me if you'd rather keep framings that are only a few
   pixels apart identical across a RAW cut.
3. **Spider-Man school:** at 00:00:17:23–00:00:17:38 there should now be no black frames and no cut-off word.
   - "kids, right. And" plays to a pause: the RAW changes shot right after "kids,", and the rule against flash
     frames shows at least 0.25 s of the new shot.
   - Around 00:00:39:4x, the audio stops just before "Spider-Man." where the picture plays on into the muted cutaway.
4. **Same speaker on every clip:** in Deadpool and Spider-Man school the competitor's framing already showed the
   speaker everywhere, so nothing moved there.

## Decisions I made for you

- **A clip where two people take turns:** I check its **main speaker**, the face that is the top speaker on most of
  its speech frames. One fixed framing can't show two people far apart, and splitting clips to add cuts wasn't asked
  for.
  - **Merged pieces:** pieces are checked **before** they are merged into one clip. So on Zendaya, S12+S13 (Zendaya
    talking) and S14+S15+S17 (Tom talking) became separate clips, each framed on its own speaker. Before, they were
    one framing.
- **What "fully inside" means:** the face box YuNet finds (forehead to chin), using the 10th–90th percentile of its
  edges over the speech, so one frame of a turning head doesn't fail a clip. Hair and shoulders may be cut.
- **Moving up or down:** only when the face is cut off at the top or bottom. Your rule said "sideways", and with the
  competitor's zooms that has never been needed on these videos.
- **Detector choice:** YuNet runs on the CPU (about 10 ms a frame). Running it on the GPU would save only a few
  seconds a video. Light-ASD runs on the GPU.
- **Light-ASD weights:** the authors' model fine-tuned on TalkSet, which is meant for videos in the wild. It is
  stored in `match_cuts/face_models/` with its MIT licence; YuNet's licence is there too.
- **When people can't be checked:** a clip with nobody in the picture (B-roll, an object) and an `OTHER VIDEO`
  stretch are listed, not failed, as for the other checks. Without PyTorch, the biggest face counts as the speaker,
  and the summary says so.
- **PyTorch:** installed as **2.11 with CUDA 13** (`torch 2.11.0+cu130`, `torchaudio 2.11.0+cu130`) into `.venv`. It
  supports the RTX 50 series (compute capability 12.0). I chose 2.11 rather than the newest 2.14 because
  `torchaudio` stops at 2.11. Task 4 may need it.
