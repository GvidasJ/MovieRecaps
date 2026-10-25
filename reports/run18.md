# Run 18 (25 October): one task at a time

Started 05:29 (PC; you were at it). `shutdown /a`: "no shutdown was in progress" -- the PC stays on. C: 102.5 GB free at the start, D: 1795.7 GB.

## The tasks

| # | task | state | how you use it |
|---|---|---|---|
| 1 | A1 voice: real Clip Gain + the right effects, no extra Volume | **done** (Premiere test passed on all 4 channels; no preset needed) | Open in Premiere from the app, save (Ctrl+S): the app closes Premiere once, writes everything, opens it again |
| 2 | The song on every channel | **done** (Premiere test passed on all 4 channels) | nothing: MikeMemesYou, Flick707 and LouLaughs edits get SceneProof's song on A2, with its effects |

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

## Tests so far

- After tasks 1-2: tool 1657 passed, 0 failed; app 1279 passed, 0 failed.
