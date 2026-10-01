# Match cuts report: competitor.mp4 rebuilt from raw.mp4

## 1. Acceptance criteria

**Overall: FAIL**

| Criterion | Status | Evidence |
|---|---|---|
| 1. Full coverage | PASS | 98 segments (3 NOT-IN-RAW), 1787/1787 frames covered, 0 gaps, 0 overlaps (0 transitions) |
| 2. Frame-exact cuts | FAIL | 97 cuts: 91 verified both sides, 1 exceptions, 5 failed |
| 3. Frame-exact source frames | FAIL | AE sim: plan: 1683/1765 exact, 42 ambiguous-identical, 2 timing-tie, 38 re-assigned, 0 mismatched (97.8470% ok); plan vs cutlist: 0 differing frame(s) (0 transition, 22 placeholder/dip frames checked); visual: 1765 matched frames, min ZNCC 0.68485, median 0.99239, 11 below 0.9; 0 blend frames, 0 uniform, 22 placeholder frames checked [preview_recreation.mp4]; delivered preview: 1787 frames at 30/1 fps, 608x1080 |
| 4. Speed / framing / flip / rotation | FAIL | 95 raw segments: 34 problems, 13 exceptions |
| 5. Audio | FAIL | 40 segments measured, 58 explained exceptions, 40 failures |
| 6. After Effects | FAIL | mock run: 19/19 checks ok; aerender: After Effects is installed but recreated_edit.aep was not produced |
| 9.7 Determinism | PASS | cutlist re-assembled from caches is byte-identical; identical to the previous run |
| 9.8 Deliverables | FAIL | 9/10 deliverables present, exports validated, 0 stage errors |

## 2. Inputs

### Competitor

| property | value |
|---|---|
| file | C:\Users\gvida\Desktop\bh\MovieRecaps\input\competitor.mp4 |
| container | mp4 |
| video codec / profile | av1 Main |
| pixel format / range | yuv420p tv |
| coded size | 608×1080 |
| display size | 608×1080 |
| SAR / DAR | 1/1 / 76/135 |
| rotation | 0° |
| r_frame_rate / avg_frame_rate | 30/1 / 30/1 |
| nominal fps | 30/1 (30.000) |
| decoded frames | 1787 |
| duration | 00:59.567 (59.567s) |
| CFR / VFR | CFR (PTS jitter 0.000 frames) |
| start times (v / a) / A-V offset | 0.000000s / 0.000000s / 0.000 ms |
| edit list | no |
| audio | aac 44100 Hz × 2 ch |
| AE issues | vcodec: av1 is not H.264/ProRes |
| imported by AE | media/competitor_ref.mp4 |
| conform | transcoded — not AE-safe: vcodec: av1 is not H.264/ProRes -> transcoded to H.264 + AAC 48 kHz, CFR re-stamped by frame index at 30/1 fps, 608x1080, start 0 |
| conform verification | encode_fps=719.25, encode_seconds=2.485, fps_actual=30/1, fps_expected=30/1, frames_actual=1787, frames_expected=1787, frames_source=1787, informative=61, median_ssim=0.99912, method=restamp, min_margin=0.0, min_ssim=0.99853, n_failed=0, ok=True, samples=64, seconds=1.614 |

### RAW

| property | value |
|---|---|
| file | C:\Users\gvida\Desktop\bh\MovieRecaps\input\raw.mp4 |
| container | mp4 |
| video codec / profile | h264 High |
| pixel format / range | yuv420p tv |
| coded size | 1920×1080 |
| display size | 1920×1080 |
| SAR / DAR | 1/1 / 16/9 |
| rotation | 0° |
| r_frame_rate / avg_frame_rate | 24000/1001 / 24000/1001 |
| nominal fps | 24000/1001 (23.976) |
| decoded frames | 3445 |
| duration | 02:23.685 (143.685s) |
| CFR / VFR | CFR (PTS jitter 0.000 frames) |
| start times (v / a) / A-V offset | 0.000000s / 0.000000s / 0.000 ms |
| edit list | no |
| audio | aac 48000 Hz × 2 ch |
| AE issues | none (AE-safe) |
| imported by AE | media/raw.mp4 |
| conform | not needed — AE-safe; same into media/ unchanged |
| conform verification | frames=3445, identical=True, method=copy, ok=True |

Timeline: MAIN comp 608×1080 at 30/1 (30.000) (layout `match`, comp size `competitor`, fps mode `competitor`, AE time mode `auto`). Max cut error from the fps mode: 0.000 ms.

## 3. Detected layout

- Layout kind: **boxed** (recreated in `match` mode)
- Canvas: #000000
- Video box: x 30.00, y 316.00, w 548.00, h 569.37 (competitor px, CORNER convention), corner radius 48.00 px
- Background: solid (color #000000, gray 0.0)

| zone | x | y | w | h | frames | notes |
|---|---|---|---|---|---|---|
| title | 70 | 82 | 466 | 228 | all |  multicolour; text block above the box; colours #fcfcfc 31%, #fd0605 15%, #fc0bfc 13% |
| other | 258 | 106 | 186 | 42 | all |  multicolour; text above the box; colours #fafafa 63%, #d2d2d2 12%, #02aaf2 9% |
| captions | 82 | 622 | 444 | 44 | 4–1787 |  121 caption events; white text with dark outline, median glyph height 18 px |
| watermark | 246 | 908 | 114 | 16 | all |  below the box; colours #eeeeee 42%, #f40b07 26%, #cfcfcf 18%, #d80d09 13% |

- Captions: 121 caption events, frames 4–1787 (masked out of matching; placeholder guides in AE)
- Other overlaid text / stickers: 10 text events
- Layout periods: 0–1787 boxed

![layout](debug/layout.png)

## 4. Segments

| # | comp in–out (tc / frames) | duration | RAW in–out (tc) | speed | flip | scale / position | transition | confidence | notes |
|---|---|---|---|---|---|---|---|---|---|
| S01 | 00:00:00:00–00:00:01:09 (0–39) | 39f / 1.300s | 00:00:06:14–00:00:07:20 (raw_in 6.600125s) | 1.0000 |  | animated (2 keys, linear) |  | 0.27 | animated framing: 2 keys, scale 0.5458->0.5460, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [38]; 36 ambiguous-identical |
| S02 | 00:00:01:09–00:00:01:11 (39–41) | 2f / 0.067s | 00:00:13:22–00:00:13:23 (raw_in 13.945198s) | 1.0000 |  | animated (2 keys, linear) |  | 0.98 | animated framing: 2 keys, scale 0.5459->0.5460, easing linear; audio: too_short |
| S03 | 00:00:01:11–00:00:01:14 (41–44) | 3f / 0.100s | 00:00:14:01–00:00:14:02 (raw_in 14.068271s) | 1.0000 |  | animated (2 keys, linear) |  | 0.95 | animated framing: 2 keys, scale 0.5447->0.5465, easing linear; audio: too_short |
| S04 | 00:00:01:14–00:00:01:19 (44–49) | 5f / 0.167s | 00:00:14:02–00:00:14:06 (raw_in 14.135021s) | 1.0000 |  | animated (2 keys, linear) |  | 0.98 | animated framing: 2 keys, scale 0.5456->0.5470, easing linear; audio: too_short |
| S05 | 00:00:01:19–00:00:01:20 (49–50) | 1f / 0.033s | 00:00:14:05–00:00:14:05 (raw_in 14.232969s) | 1.0000 |  | s 0.5445 · (-267.5, 304.1) · rot 0.32° |  | 0.96 | audio: too_short |
| S06 | 00:00:01:20–00:00:01:24 (50–54) | 4f / 0.133s | 00:00:14:08–00:00:14:10 (raw_in 14.351854s) | 1.0000 |  | animated (2 keys, linear) |  | 0.96 | animated framing: 2 keys, scale 0.5457->0.5458, easing linear; audio: too_short; J/L audio 0/-2f |
| S07 | 00:00:01:24–00:00:01:29 (54–59) | 5f / 0.167s | 00:00:14:09–00:00:14:13 (raw_in 14.426979s) | 1.0000 |  | animated (2 keys, linear) |  | 0.97 | animated framing: 2 keys, scale 0.5457->0.5461, easing linear; audio: too_short; J/L audio -2/0f |
| S08 | 00:00:01:29–00:00:02:00 (59–60) | 1f / 0.033s | 00:00:14:12–00:00:14:12 (raw_in 14.524927s) | 1.0000 |  | s 0.5461 · (-260.2, 311.9) · rot -0.80° |  | 0.88 | audio: too_short |
| S09 | 00:00:02:00–00:00:02:04 (60–64) | 4f / 0.133s | 00:00:14:16–00:00:14:18 (raw_in 14.693896s) | 1.0000 |  | animated (2 keys, linear) |  | 0.95 | animated framing: 2 keys, scale 0.5454->0.5455, easing linear; audio: too_short |
| S10 | 00:00:02:04–00:00:02:05 (64–65) | 1f / 0.033s | 00:00:14:17–00:00:14:17 (raw_in 14.733469s) | 1.0000 |  | s 0.5399 · (-158.3, 314.2) · rot -0.26° |  | 0.93 | audio: too_short |
| S11 | 00:00:02:05–00:00:02:06 (65–66) | 1f / 0.033s | 00:00:14:20–00:00:14:20 (raw_in 14.858594s) | 1.0000 |  | s 0.5475 · (-113.8, 295.0) · rot 0.48° |  | 0.92 | audio: too_short |
| S12 | 00:00:02:06–00:00:02:08 (66–68) | 2f / 0.067s | 00:00:14:22–00:00:14:22 (raw_in 14.935771s) | 1.0000 |  | animated (2 keys, linear) |  | 0.94 | animated framing: 2 keys, scale 0.5469->0.5480, easing linear; audio: too_short |
| S13 | 00:00:02:08–00:00:02:10 (68–70) | 2f / 0.067s | 00:00:14:22–00:00:14:22 (raw_in 14.935771s) | 1.0000 |  | animated (2 keys, linear) |  | 0.99 | animated framing: 2 keys, scale 0.5456->0.5458, easing linear; audio: too_short |
| S14 | 00:00:02:10–00:00:02:19 (70–79) | 9f / 0.300s | 00:00:16:21–00:00:17:03 (raw_in 16.896146s) | 1.0000 |  | s 0.5462 · (-92.8, 306.5) |  | 0.98 | audio: too_short; J/L audio 0/2f |
| S15 | 00:00:02:19–00:00:03:11 (79–101) | 22f / 0.733s | 00:00:17:06–00:00:17:23 (raw_in 17.293875s) | 1.0000 |  | animated (4 keys, linear) |  | 0.48 | animated framing: 4 keys, scale 0.5463->0.5465, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [81, 91, 92, 93]; J/L audio 2/0f |
| S16 | 00:00:03:11–00:00:03:18 (101–108) | 7f / 0.233s | 00:00:18:00–00:00:18:05 (raw_in 18.027542s) | 1.0000 |  | animated (2 keys, linear) |  | 0.99 | animated framing: 2 keys, scale 0.5462->0.5465, easing linear; J/L audio 0/8f |
| S17 | 00:00:03:18–00:00:03:28 (108–118) | 10f / 0.333s | 00:00:18:06–00:00:18:13 (raw_in 18.289271s) | 1.0000 |  | animated (3 keys, linear) |  | 0.60 | animated framing: 3 keys, scale 0.5462->0.5465, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [109]; audio: too_short; J/L audio 8/0f |
| S18 | 00:00:03:28–00:00:04:10 (118–130) | 12f / 0.400s | 00:00:18:13–00:00:18:22 (raw_in 18.593792s) | 1.0000 |  | animated (3 keys, linear) |  | 0.69 | animated framing: 3 keys, scale 0.5464->0.5469, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [120, 123, 129]; AE-rule-sensitive; audio: too_short |
| S19 | 00:00:04:10–00:00:05:10 (130–160) | 30f / 1.000s | 00:00:21:06–00:00:22:05 (raw_in 21.297542s) | 1.0000 |  | animated (3 keys, linear) |  | 0.69 | animated framing: 3 keys, scale 0.5457->0.5462, easing linear; 19 ambiguous-identical; J/L audio 0/1f |
| S20 | 00:00:05:10–00:00:08:01 (160–241) | 81f / 2.700s | 00:00:24:20–00:00:27:12 (raw_in 24.866625s) | 1.0000 |  | s 0.5458 · (-210.7, 306.6) |  | 0.96 | AE-rule-sensitive; J/L audio 1/1f |
| S21 | 00:00:08:01–00:00:09:24 (241–294) | 53f / 1.767s | 00:00:28:06–00:00:30:00 (raw_in 28.297667s) | 1.0000 |  | animated (7 keys, linear) |  | 0.96 | animated framing: 7 keys, scale 0.5456->0.5458, easing linear; PySceneDetect change at 266: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; J/L audio 1/2f |
| S22 | 00:00:09:24–00:00:11:00 (294–330) | 36f / 1.200s | 00:00:30:21–00:00:32:01 (raw_in 30.933000s) | 1.0000 |  | animated (3 keys, linear) |  | 0.98 | animated framing: 3 keys, scale 0.5455->0.5458, easing linear; J/L audio 2/2f |
| S23 | 00:00:11:00–00:00:11:11 (330–341) | 11f / 0.367s | 00:00:33:14–00:00:33:22 (raw_in 33.626458s) | 1.0000 |  | animated (2 keys, linear) |  | 0.87 | animated framing: 2 keys, scale 0.5459->0.5459, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [340]; PySceneDetect change at 333: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 336: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 338: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; J/L audio 2/6f |
| S24 | 00:00:11:11–00:00:11:19 (341–349) | 8f / 0.267s | 00:00:34:02–00:00:34:07 (raw_in 34.121688s) | 1.0000 |  | animated (2 keys, linear) |  | 0.32 | animated framing: 2 keys, scale 0.5459->0.5459, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [341, 342, 343, 344, 345, 348]; audio: too_short; J/L audio 6/1f |
| S25 | 00:00:11:19–00:00:12:08 (349–368) | 19f / 0.633s | 00:00:34:10–00:00:35:00 (raw_in 34.460958s) | 1.0000 |  | s 0.6924 · (13.5, 227.7) |  | 0.98 | J/L audio 1/0f |
| S26 | 00:00:12:08–00:00:15:10 (368–460) | 92f / 3.067s | 00:00:35:01–00:00:38:02 (raw_in 35.095792s) | 1.0000 |  | s 0.7371 · (-789.7, 203.4) |  | 0.29 | 79 ambiguous-identical |
| S27 | 00:00:15:10–00:00:17:03 (460–513) | 53f / 1.767s | 00:00:38:03–00:00:39:20 (raw_in 38.165792s) | 1.0000 |  | s 0.6924 · (13.4, 227.7) |  | 0.98 |  |
| S28 | 00:00:17:03–00:00:17:16 (513–526) | 13f / 0.433s | 00:00:39:21–00:00:40:07 (raw_in 39.932958s) | 1.0000 |  | s 0.5460 · (-246.7, 306.6) |  | 0.97 | J/L audio 0/2f |
| S29 | 00:00:17:16–00:00:18:19 (526–559) | 33f / 1.100s | 00:00:40:12–00:00:41:14 (raw_in 40.567458s) | 1.0000 |  | s 0.5460 · (-246.7, 306.6) |  | 0.97 | J/L audio 2/0f |
| S30 | 00:00:18:19–00:00:19:01 (559–571) | 12f / 0.400s | 00:00:41:15–00:00:41:23 (raw_in 41.666708s) | 1.0000 |  | s 0.7372 · (-790.3, 203.4) |  | 0.98 | AE-rule-sensitive; audio: too_short; J/L audio 0/1f |
| S31 | 00:00:19:01–00:00:19:22 (571–592) | 21f / 0.700s | 00:00:43:12–00:00:44:04 (raw_in 43.561750s) | 1.0000 |  | s 0.5460 · (-247.1, 306.7) |  | 0.93 | low-margin frames re-assigned to the segment model's RAW frame: [591]; PySceneDetect change at 581: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 586: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; 6 ambiguous-identical; J/L audio 1/2f |
| S32 | 00:00:19:22–00:00:19:26 (592–596) | 4f / 0.133s | 00:00:44:01–00:00:44:03 (raw_in 44.102458s) | 1.0000 |  | s 0.5460 · (-247.1, 306.7) |  | 0.49 | low-margin frames re-assigned to the segment model's RAW frame: [592]; 4 ambiguous-identical; audio: too_short; J/L audio 2/0f |
| S33 | 00:00:19:26–00:00:20:05 (596–605) | 9f / 0.300s | MISSING - not in RAW (00:00:19:26-00:00:20:05) |  |  |  |  | 0.47 | no RAW match (NOT-IN-RAW placeholder); audio: not_in_raw |
| S34 | 00:00:20:05–00:00:20:07 (605–607) | 2f / 0.067s | 00:00:44:16–00:00:44:17 (raw_in 44.725948s) | 1.0000 |  | s 0.5367 · (-120.4, 321.2) |  | 0.41 | low-margin frames re-assigned to the segment model's RAW frame: [606]; audio: too_short |
| S35 | 00:00:20:07–00:00:20:10 (607–610) | 3f / 0.100s | 00:00:44:16–00:00:44:17 (raw_in 44.715521s) | 1.0000 |  | s 0.5579 · (-155.0, 293.6) |  | 0.62 | low-margin frames re-assigned to the segment model's RAW frame: [609]; audio: too_short; J/L audio 0/3f |
| S36 | 00:00:20:10–00:00:20:15 (610–615) | 5f / 0.167s | 00:00:44:21–00:00:45:00 (raw_in 44.924062s) | 1.0000 |  | animated (2 keys, linear) |  | 0.69 | animated framing: 2 keys, scale 0.5251->0.5367, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [613]; audio: too_short; J/L audio 3/-3f |
| S37 | 00:00:20:15–00:00:20:18 (615–618) | 3f / 0.100s | 00:00:44:22–00:00:45:00 (raw_in 44.980385s) | 1.0000 |  | animated (2 keys, linear) |  | 0.96 | animated framing: 2 keys, scale 0.5579->0.5628, easing linear; audio: too_short; J/L audio -3/0f |
| S38 | 00:00:20:18–00:00:21:02 (618–632) | 14f / 0.467s | 00:00:45:02–00:00:45:12 (raw_in 45.132771s) | 1.0000 |  | s 0.7373 · (17.0, 203.4) |  | 0.98 | audio: too_short |
| S39 | 00:00:21:02–00:00:21:17 (632–647) | 15f / 0.500s | 00:00:45:13–00:00:46:00 (raw_in 45.596917s) | 1.0000 |  | animated (6 keys, linear) |  | 0.89 | animated framing: 6 keys, scale 0.5461->0.5463, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [644]; 2 ambiguous-identical |
| S40 | 00:00:21:17–00:00:21:20 (647–650) | 3f / 0.100s | 00:00:46:01–00:00:46:02 (raw_in 46.100271s) | 1.0000 |  | s 0.5464 · (-69.4, 306.4) |  | 0.84 | audio: too_short |
| S41 | 00:00:21:20–00:00:21:21 (650–651) | 1f / 0.033s | 00:00:46:15–00:00:46:15 (raw_in 46.682052s) | 1.0000 |  | s 0.5486 · (-83.9, 299.5) · rot 0.69° |  | 0.57 | audio: too_short |
| S42 | 00:00:21:21–00:00:21:23 (651–653) | 2f / 0.067s | 00:00:46:05–00:00:46:05 (raw_in 46.258729s) | 1.0000 |  | s 0.5514 · (-88.9, 305.3) |  | 0.89 | audio: too_short |
| S43 | 00:00:21:23–00:00:21:24 (653–654) | 1f / 0.033s | 00:00:46:07–00:00:46:07 (raw_in 46.348385s) | 1.0000 |  | s 0.5461 · (-98.7, 306.4) |  | 0.86 | audio: too_short |
| S44 | 00:00:21:24–00:00:21:25 (654–655) | 1f / 0.033s | 00:00:46:06–00:00:46:06 (raw_in 46.306677s) | 1.0000 |  | s 0.5461 · (-98.7, 306.4) |  | 0.75 | audio: too_short |
| S45 | 00:00:21:25–00:00:21:26 (655–656) | 1f / 0.033s | 00:00:46:18–00:00:46:18 (raw_in 46.807177s) | 1.0000 |  | s 0.5438 · (-98.3, 298.4) · rot 0.61° |  | 0.55 | 1 ambiguous-identical; audio: too_short |
| S46 | 00:00:21:26–00:00:21:28 (656–658) | 2f / 0.067s | 00:00:46:08–00:00:46:09 (raw_in 46.394281s) | 1.0000 |  | s 0.5460 · (-113.4, 306.4) |  | 0.84 | audio: too_short |
| S47 | 00:00:21:28–00:00:21:29 (658–659) | 1f / 0.033s | 00:00:46:11–00:00:46:11 (raw_in 46.515219s) | 1.0000 |  | s 0.5460 · (-113.4, 306.4) |  | 0.71 | audio: too_short |
| S48 | 00:00:21:29–00:00:22:00 (659–660) | 1f / 0.033s | 00:00:46:09–00:00:46:09 (raw_in 46.431802s) | 1.0000 |  | s 0.5494 · (-130.8, 306.1) |  | 0.69 | audio: too_short |
| S49 | 00:00:22:00–00:00:22:01 (660–661) | 1f / 0.033s | 00:00:46:11–00:00:46:11 (raw_in 46.515219s) | 1.0000 |  | s 0.5461 · (-128.1, 306.3) |  | 0.66 | audio: too_short |
| S50 | 00:00:22:01–00:00:22:06 (661–666) | 5f / 0.167s | 00:00:46:13–00:00:46:16 (raw_in 46.609146s) | 1.0000 |  | animated (3 keys, linear) |  | 0.32 | animated framing: 3 keys, scale 0.5456->0.5483, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [662, 663, 664]; 2 ambiguous-identical; audio: too_short |
| S51 | 00:00:22:06–00:00:23:25 (666–715) | 49f / 1.633s | 00:00:47:02–00:00:48:17 (raw_in 47.166250s) | 1.0000 |  | s 0.7374 · (11.8, 203.3) |  | 0.72 | 6 ambiguous-identical; J/L audio 0/1f |
| S52 | 00:00:23:25–00:00:24:17 (715–737) | 22f / 0.733s | 00:00:52:15–00:00:53:08 (raw_in 52.695875s) | 1.0000 |  | s 0.5460 · (-246.5, 306.6) |  | 0.96 | J/L audio 1/0f |
| S53 | 00:00:24:17–00:00:26:02 (737–782) | 45f / 1.500s | 00:00:53:09–00:00:54:20 (raw_in 53.430708s) | 1.0000 |  | s 0.5456 · (1.6, 306.8) |  | 0.97 | PySceneDetect change at 740: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 755: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 761: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut |
| S54 | 00:00:26:02–00:00:28:02 (782–842) | 60f / 2.000s | 00:00:54:21–00:00:56:20 (raw_in 54.932708s) | 1.0000 |  | s 0.5459 · (-246.6, 306.7) |  | 0.43 | low-margin frames re-assigned to the segment model's RAW frame: [835, 836, 837, 838]; PySceneDetect change at 806: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; 4 ambiguous-identical; J/L audio 0/6f |
| S55 | 00:00:28:02–00:00:29:24 (842–894) | 52f / 1.733s | 00:00:57:20–00:00:59:12 (raw_in 57.893833s) | 1.0000 |  | s 0.5462 · (-246.9, 306.4) |  | 0.85 | PySceneDetect change at 860: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; 18 ambiguous-identical; J/L audio 6/0f |
| S56 | 00:00:29:24–00:00:31:12 (894–942) | 48f / 1.600s | 00:00:59:13–00:01:01:03 (raw_in 59.628667s) | 1.0000 |  | s 0.5461 · (-19.9, 306.2) |  | 0.98 |  |
| S57 | 00:00:31:12–00:00:32:11 (942–971) | 29f / 0.967s | 00:01:01:04–00:01:02:02 (raw_in 61.229667s) | 1.0000 |  | s 0.5460 · (-246.8, 306.4) |  | 0.82 |  |
| S58 | 00:00:32:11–00:00:33:27 (971–1017) | 46f / 1.533s | 00:01:02:03–00:01:03:15 (raw_in 62.197833s) | 1.0000 |  | s 0.5461 · (-19.8, 306.2) |  | 0.98 |  |
| S59 | 00:00:33:27–00:00:35:07 (1017–1057) | 40f / 1.333s | 00:01:03:16–00:01:04:23 (raw_in 63.732500s) | 1.0000 |  | s 0.7378 · (11.9, 202.9) |  | 0.97 |  |
| S60 | 00:00:35:07–00:00:38:01 (1057–1141) | 84f / 2.800s | 00:01:05:00–00:01:07:18 (raw_in 65.066750s) | 1.0000 |  | animated (6 keys, ease_in_out) |  | 0.95 | animated framing: 6 keys, scale 0.5455->0.5461, easing ease_in_out; PySceneDetect change at 1104: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 1106: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 1110: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 1116: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 1118: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 1119: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 1125: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 1129: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 1135: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; PySceneDetect change at 1138: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; AE-rule-sensitive |
| S61 | 00:00:38:01–00:00:39:10 (1141–1180) | 39f / 1.300s | 00:01:07:19–00:01:09:01 (raw_in 67.861625s) | 1.0000 |  | s 0.7376 · (11.9, 203.2) |  | 0.97 | J/L audio 0/2f |
| S62 | 00:00:39:10–00:00:39:22 (1180–1192) | 12f / 0.400s | MISSING - not in RAW (00:00:39:10-00:00:39:22) |  |  |  |  | 0.11 | no RAW match (NOT-IN-RAW placeholder); audio: not_in_raw |
| S63 | 00:00:39:22–00:00:39:23 (1192–1193) | 1f / 0.033s | 00:01:09:12–00:01:09:12 (raw_in 69.579927s) | 1.0000 |  | s 0.7376 · (11.9, 203.2) |  | 0.75 | audio: too_short |
| S64 | 00:00:39:23–00:00:39:24 (1193–1194) | 1f / 0.033s | MISSING - not in RAW (00:00:39:23-00:00:39:24) |  |  |  |  | 0.31 | no RAW match (NOT-IN-RAW placeholder); audio: not_in_raw |
| S65 | 00:00:39:24–00:00:40:04 (1194–1204) | 10f / 0.333s | 00:01:09:17–00:01:09:17 (raw_in 69.788469s) | freeze |  | s 0.7376 · (11.9, 203.2) |  | 0.97 | freeze frame on RAW 1673; 10 timing-tie; audio: too_short; J/L audio 0/-8f |
| S66 | 00:00:40:04–00:00:40:08 (1204–1208) | 4f / 0.133s | 00:01:09:22–00:01:10:00 (raw_in 70.007521s) | 1.0000 |  | animated (2 keys, linear) |  | 0.73 | animated framing: 2 keys, scale 0.7365->0.7376, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [1205]; audio: too_short; J/L audio -8/0f |
| S67 | 00:00:40:08–00:00:40:09 (1208–1209) | 1f / 0.033s | 00:01:10:00–00:01:10:00 (raw_in 70.080427s) | 1.0000 |  | s 0.7376 · (11.9, 203.2) |  | 0.98 | audio: too_short |
| S68 | 00:00:40:09–00:00:42:13 (1209–1273) | 64f / 2.133s | 00:01:10:05–00:01:12:07 (raw_in 70.298292s) | 1.0000 |  | s 0.7376 · (11.9, 203.2) |  | 0.95 | J/L audio 0/1f |
| S69 | 00:00:42:13–00:00:43:11 (1273–1301) | 28f / 0.933s | 00:01:13:12–00:01:14:10 (raw_in 73.600292s) | 1.0000 |  | s 0.5460 · (-246.8, 306.7) |  | 0.99 | J/L audio 1/1f |
| S70 | 00:00:43:11–00:00:43:27 (1301–1317) | 16f / 0.533s | 00:01:20:12–00:01:21:00 (raw_in 80.598583s) | 1.0000 |  | s 0.5458 · (-23.7, 306.6) |  | 0.92 | J/L audio 1/0f |
| S71 | 00:00:43:27–00:00:45:10 (1317–1360) | 43f / 1.433s | 00:01:21:01–00:01:22:10 (raw_in 81.133417s) | 1.0000 |  | s 1.9878 · (-2399.0, -1245.4) |  | 0.77 |  |
| S72 | 00:00:45:10–00:00:46:21 (1360–1401) | 41f / 1.367s | 00:01:22:11–00:01:23:19 (raw_in 82.566667s) | 1.0000 |  | s 0.5460 · (-128.7, 306.8) |  | 0.98 | AE-rule-sensitive; 4 ambiguous-identical; J/L audio 0/1f |
| S73 | 00:00:46:21–00:00:47:01 (1401–1411) | 10f / 0.333s | 00:01:24:14–00:01:24:21 (raw_in 84.694208s) | 1.0000 |  | s 0.5458 · (-364.2, 306.7) |  | 0.98 | PySceneDetect change at 1406: inside a continuous mapping (same RAW line, no transform/flip change): motion, lighting, caption or overlay change -- not a cut; J/L audio 1/10f |
| S74 | 00:00:47:01–00:00:47:13 (1411–1423) | 12f / 0.400s | 00:01:25:00–00:01:25:06 (raw_in 85.119868s) | 0.6667 |  | animated (4 keys, ease_out) |  | 0.83 | animated framing: 4 keys, scale 0.5410->0.5461, easing ease_out; audio: too_short; J/L audio 10/0f |
| S75 | 00:00:47:13–00:00:47:15 (1423–1425) | 2f / 0.067s | 00:01:25:08–00:01:25:08 (raw_in 85.422854s) | 1.0000 |  | animated (2 keys, linear) |  | 0.90 | animated framing: 2 keys, scale 0.5459->0.5460, easing linear; audio: too_short |
| S76 | 00:00:47:15–00:00:47:16 (1425–1426) | 1f / 0.033s | 00:01:25:08–00:01:25:08 (raw_in 85.429094s) | 1.0000 |  | s 0.5418 · (-142.3, 308.6) |  | 0.90 | audio: too_short |
| S77 | 00:00:47:16–00:00:47:20 (1426–1430) | 4f / 0.133s | 00:01:25:11–00:01:25:13 (raw_in 85.547979s) | 1.0000 |  | s 0.5458 · (-198.5, 306.6) |  | 0.92 | audio: too_short |
| S78 | 00:00:47:20–00:00:47:22 (1430–1432) | 2f / 0.067s | 00:01:25:12–00:01:25:13 (raw_in 85.600115s) | 1.0000 |  | s 0.5458 · (-198.5, 306.6) |  | 0.94 | audio: too_short |
| S79 | 00:00:47:22–00:00:47:25 (1432–1435) | 3f / 0.100s | 00:01:25:16–00:01:25:18 (raw_in 85.771135s) | 1.0000 |  | animated (2 keys, linear) |  | 0.91 | animated framing: 2 keys, scale 0.5462->0.5465, easing linear; audio: too_short |
| S80 | 00:00:47:25–00:00:47:27 (1435–1437) | 2f / 0.067s | 00:01:25:17–00:01:25:18 (raw_in 85.808656s) | 1.0000 |  | animated (2 keys, linear) |  | 0.98 | animated framing: 2 keys, scale 0.5460->0.5462, easing linear; audio: too_short |
| S81 | 00:00:47:27–00:00:48:00 (1437–1440) | 3f / 0.100s | 00:01:25:20–00:01:25:21 (raw_in 85.923354s) | 1.0000 |  | animated (2 keys, linear) |  | 0.97 | animated framing: 2 keys, scale 0.5496->0.5531, easing linear; audio: too_short; J/L audio 0/-1f |
| S82 | 00:00:48:00–00:00:48:04 (1440–1444) | 4f / 0.133s | 00:01:25:21–00:01:25:23 (raw_in 85.973438s) | 1.0000 |  | animated (2 keys, linear) |  | 0.93 | animated framing: 2 keys, scale 0.5345->0.5461, easing linear; audio: too_short; J/L audio -1/0f |
| S83 | 00:00:48:04–00:00:48:09 (1444–1449) | 5f / 0.167s | 00:01:26:00–00:01:26:03 (raw_in 86.115313s) | 1.0000 |  | animated (3 keys, linear) |  | 0.73 | animated framing: 3 keys, scale 0.5461->0.5604, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [1444]; audio: too_short |
| S84 | 00:00:48:09–00:00:48:12 (1449–1452) | 3f / 0.100s | 00:01:26:04–00:01:26:05 (raw_in 86.257021s) | 1.0000 |  | s 0.5296 · (-385.9, 325.3) · rot -0.43° |  | 0.61 | low-margin frames re-assigned to the segment model's RAW frame: [1451]; audio: too_short; J/L audio 0/2f |
| S85 | 00:00:48:12–00:00:48:25 (1452–1465) | 13f / 0.433s | 00:01:27:11–00:01:27:21 (raw_in 87.566896s) | 1.0000 |  | s 0.5458 · (-187.1, 306.6) |  | 0.98 | audio: too_short; J/L audio 2/2f |
| S86 | 00:00:48:25–00:00:50:08 (1465–1508) | 43f / 1.433s | 00:01:30:00–00:01:31:09 (raw_in 90.100708s) | 1.0000 |  | s 0.5456 · (-286.3, 306.7) |  | 0.64 | low-margin frames re-assigned to the segment model's RAW frame: [1495, 1498]; 11 ambiguous-identical; J/L audio 2/0f |
| S87 | 00:00:50:08–00:00:52:07 (1508–1567) | 59f / 1.967s | 00:01:31:10–00:01:33:08 (raw_in 91.533292s) | 1.0000 |  | s 0.5461 · (-215.3, 306.4) |  | 0.95 | AE-rule-sensitive; J/L audio 0/1f |
| S88 | 00:00:52:07–00:00:53:14 (1567–1604) | 37f / 1.233s | 00:01:35:04–00:01:36:09 (raw_in 95.297333s) | 1.0000 |  | animated (2 keys, linear) |  | 0.98 | animated framing: 2 keys, scale 0.9288->0.9297, easing linear; J/L audio 1/2f |
| S89 | 00:00:53:14–00:00:54:03 (1604–1623) | 19f / 0.633s | 00:01:37:01–00:01:37:16 (raw_in 97.165333s) | 1.0000 |  | s 0.5459 · (-246.7, 306.5) |  | 0.98 | J/L audio 2/2f |
| S90 | 00:00:54:03–00:00:55:07 (1623–1657) | 34f / 1.133s | 00:01:39:14–00:01:40:16 (raw_in 99.700083s) | 1.0000 |  | s 0.5460 · (-246.9, 306.5) |  | 0.98 | AE-rule-sensitive; J/L audio 2/1f |
| S91 | 00:00:55:07–00:00:56:26 (1657–1706) | 49f / 1.633s | 00:01:49:02–00:01:50:16 (raw_in 109.194750s) | 1.0000 |  | s 0.5462 · (-151.2, 306.1) |  | 0.88 | 8 ambiguous-identical; J/L audio 1/3f |
| S92 | 00:00:56:26–00:00:58:02 (1706–1742) | 36f / 1.200s | 00:02:04:09–00:02:05:13 (raw_in 124.533292s) | 1.0000 |  | s 0.5459 · (-246.5, 306.5) |  | 0.26 | AE-rule-sensitive; J/L audio 3/0f |
| S93 | 00:00:58:02–00:00:58:17 (1742–1757) | 15f / 0.500s | 00:02:05:14–00:02:06:01 (raw_in 125.727000s) | 1.0000 |  | animated (4 keys, linear) |  | 0.82 | animated framing: 4 keys, scale 0.9345->0.9390, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [1749, 1750] |
| S94 | 00:00:58:17–00:00:58:18 (1757–1758) | 1f / 0.033s | 00:02:06:01–00:02:06:01 (raw_in 126.178135s) | 1.0000 |  | s 0.9348 · (-70.3, -103.7) |  | 0.93 | audio: too_short |
| S95 | 00:00:58:18–00:00:58:20 (1758–1760) | 2f / 0.067s | 00:02:06:07–00:02:06:08 (raw_in 126.432573s) | 1.0000 |  | s 0.9348 · (-70.3, -103.7) |  | 0.89 | audio: too_short |
| S96 | 00:00:58:20–00:00:58:28 (1760–1768) | 8f / 0.267s | 00:02:06:08–00:02:06:13 (raw_in 126.463938s) | 1.0000 |  | animated (3 keys, linear) |  | 0.98 | animated framing: 3 keys, scale 0.9347->0.9348, easing linear; audio: too_short; J/L audio 0/4f |
| S97 | 00:00:58:28–00:00:59:04 (1768–1774) | 6f / 0.200s | 00:02:06:15–00:02:06:18 (raw_in 126.751708s) | 1.0000 |  | animated (3 keys, linear) |  | 0.62 | animated framing: 3 keys, scale 0.9348->0.9349, easing linear; low-margin frames re-assigned to the segment model's RAW frame: [1769, 1771]; AE-rule-sensitive; audio: too_short; J/L audio 4/1f |
| S98 | 00:00:59:04–00:00:59:17 (1774–1787) | 13f / 0.433s | 00:02:06:22–00:02:07:08 (raw_in 127.064688s) | 1.0000 |  | s 0.9348 · (-3.0, -104.2) |  | 0.98 | audio: too_short; J/L audio 1/0f |

Mapping plot (competitor time → RAW time): ![mapping](debug/mapping.png)

Scores: ![scores](debug/scores.png)

## 5. Edit-style breakdown

- Segments: 98 (95 from RAW), cuts: 97
- Shot length: mean 0.62s, median 0.30s (min 0.03s, max 3.07s)
- RAW used: 1381 of 3445 frames (40.1 %)
- RAW ranges cut out (largest first): 00:02:07:09–00:02:23:13 (388f, 16.18s), 00:01:50:17–00:02:04:09 (328f, 13.68s), 00:01:40:17–00:01:49:02 (201f, 8.38s), 00:00:00:00–00:00:06:14 (158f, 6.59s), 00:00:07:21–00:00:13:22 (145f, 6.05s), 00:01:14:11–00:01:20:12 (145f, 6.05s), 00:00:48:18–00:00:52:15 (93f, 3.88s), 00:00:22:06–00:00:24:20 (62f, 2.59s), 00:00:18:23–00:00:21:06 (55f, 2.29s), 00:01:27:22–00:01:30:00 (50f, 2.09s), 00:00:14:23–00:00:16:21 (46f, 1.92s), 00:01:37:17–00:01:39:14 (45f, 1.88s)
- Order: non-chronological — S05, S07, S08, S10, S32, S35, S37, S42, S44, S46, S48, S78, S80 jump back in RAW time
- Re-used RAW moments: frames 338–338 in S03 and S04, frames 341–341 in S04 and S05, frames 345–346 in S06 and S07, frames 348–348 in S07 and S08, frames 353–353 in S09 and S10, frames 358–358 in S12 and S13, frames 445–445 in S17 and S18, frames 1057–1059 in S31 and S32, frames 1072–1073 in S34 and S35, frames 1078–1080 in S36 and S37, frames 1119–1119 in S41 and S50, frames 1113–1113 in S46 and S48, frames 1115–1115 in S47 and S49, frames 1680–1680 in S66 and S67, frames 2048–2048 in S75 and S76, frames 2052–2053 in S77 and S78, frames 2057–2058 in S79 and S80, frames 2061–2061 in S81 and S82, frames 3025–3025 in S93 and S94, frames 3032–3032 in S95 and S96
- Speed factors: 0.667× (1 segment), 1.000× (93 segments)
- Freeze / reverse / ramp (time-remapped): S65
- Zoom punch-ins: 20 (S09→S10 ×0.990, S10→S11 ×1.014, S24→S25 ×1.268, S25→S26 ×1.065, S26→S27 ×0.939, S27→S28 ×0.789, S29→S30 ×1.350, S34→S35 ×1.040, S37→S38 ×1.322, S38→S39 ×0.741, S58→S59 ×1.351, S59→S60 ×0.740, S60→S61 ×1.351, S70→S71 ×3.642, S71→S72 ×0.275, S80→S81 ×1.013, S81→S82 ×0.987, S82→S83 ×1.026, S83→S84 ×0.945, S92→S93 ×1.720)
- Animated zooms/pans: 35 (S01, S02, S03, S04, S06, S07, S09, S12, S13, S15, S16, S17, S18, S19, S21, S22, S23, S24, S36, S37, S39, S50, S60, S66, S74, S75, S79, S80, S81, S82, S83, S88, S93, S96, S97)
- Horizontal flips: 0
- Rotation: 17 segments (S05, S06, S07, S08, S09, S10, S11, S12, S36, S41, S45, S79, S81, S82, S83, S84, S93)
- Transitions: hard cuts only
- Captions: 121 events, typical duration 0.50s (band y≈636px, height≈24px)
- Static overlays: other, title, watermark
- Other overlaid text / stickers: text ×10
- Added audio (not recreated): music 00:00:00:00–00:00:59:17 (-7.9 dB)
- Audio: status ok; J-cut at comp frame 54 (S06|S07): audio leads by 2 frames; L-cut at comp frame 79 (S14|S15): audio trails by 2 frames; L-cut at comp frame 108 (S16|S17): audio trails by 8 frames; L-cut at comp frame 160 (S19|S20): audio trails by 1 frames; L-cut at comp frame 241 (S20|S21): audio trails by 1 frames; L-cut at comp frame 294 (S21|S22): audio trails by 2 frames; L-cut at comp frame 330 (S22|S23): audio trails by 2 frames; L-cut at comp frame 341 (S23|S24): audio trails by 6 frames; L-cut at comp frame 349 (S24|S25): audio trails by 1 frames; L-cut at comp frame 526 (S28|S29): audio trails by 2 frames; L-cut at comp frame 571 (S30|S31): audio trails by 1 frames; L-cut at comp frame 592 (S31|S32): audio trails by 2 frames; L-cut at comp frame 610 (S35|S36): audio trails by 3 frames; J-cut at comp frame 615 (S36|S37): audio leads by 3 frames; L-cut at comp frame 715 (S51|S52): audio trails by 1 frames; L-cut at comp frame 842 (S54|S55): audio trails by 6 frames; L-cut at comp frame 1180 (S61|S62): audio trails by 2 frames; J-cut at comp frame 1204 (S65|S66): audio leads by 8 frames; L-cut at comp frame 1273 (S68|S69): audio trails by 1 frames; L-cut at comp frame 1301 (S69|S70): audio trails by 1 frames; L-cut at comp frame 1401 (S72|S73): audio trails by 1 frames; L-cut at comp frame 1411 (S73|S74): audio trails by 10 frames; J-cut at comp frame 1440 (S81|S82): audio leads by 1 frames; L-cut at comp frame 1452 (S84|S85): audio trails by 2 frames; L-cut at comp frame 1465 (S85|S86): audio trails by 2 frames; L-cut at comp frame 1567 (S87|S88): audio trails by 1 frames; L-cut at comp frame 1604 (S88|S89): audio trails by 2 frames; L-cut at comp frame 1623 (S89|S90): audio trails by 2 frames; L-cut at comp frame 1657 (S90|S91): audio trails by 1 frames; L-cut at comp frame 1706 (S91|S92): audio trails by 3 frames; L-cut at comp frame 1768 (S96|S97): audio trails by 4 frames; L-cut at comp frame 1774 (S97|S98): audio trails by 1 frames; added music comp frames 0-1786 (-7.9 dB re original, -11.8 dBFS)

## 6. Warnings

- Low-confidence frames (conf < 0.5): 13 — 93 (00:00:03:03), 644 (00:00:21:14), 820 (00:00:27:10), 822-823 (00:00:27:12), 834 (00:00:27:24), 836 (00:00:27:26), 1494-1495 (00:00:49:24), 1724-1725 (00:00:57:14), 1727 (00:00:57:17), 1729 (00:00:57:19) — see `debug/low_confidence/`
- Ambiguous-identical frames (neighbouring RAW frames identical): 193 — 0-22 (00:00:00:00), 26-37 (00:00:00:26), 141-159 (00:00:04:21), 368-371 (00:00:12:08), 374-396 (00:00:12:14), 405-409 (00:00:13:15), 413-459 (00:00:13:23), 586-590 (00:00:19:16), 593-595 (00:00:19:23), 640-641 (00:00:21:10), 655 (00:00:21:25), 661 (00:00:22:01), 675-677 (00:00:22:15), 680-682 (00:00:22:20), 800-801 (00:00:26:20), 834 (00:00:27:24), 842-859 (00:00:28:02), 1367-1370 (00:00:45:17), 1496-1497 (00:00:49:26), 1499-1505 (00:00:49:29), ... +2 more runs
- Timing-tie frames (AE floor/round may differ by one frame): 10 — 1194-1203 (00:00:39:24)
- Low-margin frames (best RAW frame beats its neighbours by < 0.001): 194 — 0-34 (00:00:00:00), 36-37 (00:00:01:06), 80 (00:00:02:20), 87-88 (00:00:02:27), 110 (00:00:03:20), 158 (00:00:05:08), 372-377 (00:00:12:12), 379-382 (00:00:12:19), 387-389 (00:00:12:27), 392-394 (00:00:13:02), 405-413 (00:00:13:15), 417 (00:00:13:27), 419 (00:00:13:29), 423 (00:00:14:03), 429 (00:00:14:09), 432 (00:00:14:12), 434-438 (00:00:14:14), 444 (00:00:14:24), 447-459 (00:00:14:27), 675-687 (00:00:22:15), ... +20 more runs
- Re-assigned by segmentation (the segment model's RAW frame replaced refine's measured best frame; counted against criterion 3): 38 — 38 (00:00:01:08), 81 (00:00:02:21), 91-93 (00:00:03:01), 109 (00:00:03:19), 120 (00:00:04:00), 123 (00:00:04:03), 129 (00:00:04:09), 340-345 (00:00:11:10), 348 (00:00:11:18), 591-592 (00:00:19:21), 606 (00:00:20:06), 609 (00:00:20:09), 613 (00:00:20:13), 644 (00:00:21:14), 662-664 (00:00:22:02), 835-838 (00:00:27:25), 1205 (00:00:40:05), 1444 (00:00:48:04), 1451 (00:00:48:11), 1495 (00:00:49:25), ... +4 more runs — k 38: measured 181 → model 188 (score gap 0.0001); k 81: measured 415 → model 416 (score gap 0.0007); k 91: measured 423 → model 424 (score gap 0.0008); k 92: measured 424 → model 425 (score gap 0.0006); k 93: measured 424 → model 425 (score gap 0.0028); k 109: measured 438 → model 439 (score gap 0.0007); k 120: measured 446 → model 447 (score gap 0.0113); k 123: measured 448 → model 449 (score gap 0.0127) …
- NOT-IN-RAW ranges: 596–604 (00:00:19:26–00:00:20:05), 1180–1191 (00:00:39:10–00:00:39:22), 1193–1193 (00:00:39:23–00:00:39:24)
- AE-rule-sensitive segments (tiny phase margin; use `--ae-time-mode frames` if AE is off by a frame): S18 (0.083333 ms), S20 (0.083333 ms), S30 (0.083333 ms), S60 (0.083333 ms), S72 (0.083333 ms), S87 (0.083333 ms), S90 (0.083333 ms), S92 (0.083333 ms), S97 (0.083333 ms)
- Frames not reproduced exactly (s9_2): AE plan: 38 re-assigned by segmentation (38, 81, 91-93, 109, 120, 123, 129, 340-345, ... +16 more runs); mock record: 38 re-assigned by segmentation (38, 81, 91-93, 109, 120, 123, 129, 340-345, ... +16 more runs)
- Anything AE can't reproduce: nothing detected

All warnings:

- S01: the audio implies raw_in 6.513187s, 86.9 ms outside the video-feasible interval (kept at 6.600125s)
- S15: the audio implies raw_in 17.213359s, 80.5 ms outside the video-feasible interval (kept at 17.293875s)
- S16: the audio implies raw_in 17.946708s, 80.8 ms outside the video-feasible interval (kept at 18.027542s)
- S19: the audio implies raw_in 21.213375s, 84.2 ms outside the video-feasible interval (kept at 21.297542s)
- S21: the audio implies raw_in 28.213479s, 84.2 ms outside the video-feasible interval (kept at 28.297667s)
- S22: the audio implies raw_in 30.846812s, 86.2 ms outside the video-feasible interval (kept at 30.933000s)
- S23: the audio implies raw_in 33.546810s, 79.6 ms outside the video-feasible interval (kept at 33.626458s)
- S25: the audio implies raw_in 34.380145s, 80.8 ms outside the video-feasible interval (kept at 34.460958s)
- S26: the audio implies raw_in 35.013478s, 82.3 ms outside the video-feasible interval (kept at 35.095792s)
- S27: the audio implies raw_in 38.080145s, 85.6 ms outside the video-feasible interval (kept at 38.165792s)
- S28: the audio implies raw_in 39.846811s, 86.1 ms outside the video-feasible interval (kept at 39.932958s)
- S29: the audio implies raw_in 40.480145s, 87.3 ms outside the video-feasible interval (kept at 40.567458s)
- S31: the audio implies raw_in 43.480145s, 81.6 ms outside the video-feasible interval (kept at 43.561750s)
- S39: the audio implies raw_in 45.513480s, 83.4 ms outside the video-feasible interval (kept at 45.596917s)
- S51: the audio implies raw_in 47.080146s, 86.1 ms outside the video-feasible interval (kept at 47.166250s)
- S52: the audio implies raw_in 52.613916s, 82.0 ms outside the video-feasible interval (kept at 52.695875s)
- S53: the audio implies raw_in 53.347247s, 83.5 ms outside the video-feasible interval (kept at 53.430708s)
- S54: the audio implies raw_in 54.847249s, 85.5 ms outside the video-feasible interval (kept at 54.932708s)
- S55: the audio implies raw_in 57.813908s, 79.9 ms outside the video-feasible interval (kept at 57.893833s)
- S56: the audio implies raw_in 59.547251s, 81.4 ms outside the video-feasible interval (kept at 59.628667s)
- S57: the audio implies raw_in 61.147250s, 82.4 ms outside the video-feasible interval (kept at 61.229667s)
- S58: the audio implies raw_in 62.113916s, 83.9 ms outside the video-feasible interval (kept at 62.197833s)
- S59: the audio implies raw_in 63.647249s, 85.3 ms outside the video-feasible interval (kept at 63.732500s)
- S61: the audio implies raw_in 67.780584s, 81.0 ms outside the video-feasible interval (kept at 67.861625s)
- S68: the audio implies raw_in 70.213916s, 84.4 ms outside the video-feasible interval (kept at 70.298292s)
- S69: the audio implies raw_in 73.513916s, 86.4 ms outside the video-feasible interval (kept at 73.600292s)
- S70: the audio implies raw_in 80.514354s, 84.2 ms outside the video-feasible interval (kept at 80.598583s)
- S71: the audio implies raw_in 81.047687s, 85.7 ms outside the video-feasible interval (kept at 81.133417s)
- S73: the audio implies raw_in 84.614339s, 79.9 ms outside the video-feasible interval (kept at 84.694208s)
- S86: the audio implies raw_in 90.014354s, 86.4 ms outside the video-feasible interval (kept at 90.100708s)
- S88: the audio implies raw_in 95.214354s, 83.0 ms outside the video-feasible interval (kept at 95.297333s)
- S89: the audio implies raw_in 97.081020s, 84.3 ms outside the video-feasible interval (kept at 97.165333s)
- S91: the audio implies raw_in 109.114792s, 80.0 ms outside the video-feasible interval (kept at 109.194750s)
- S93: the audio implies raw_in 125.648333s, 78.7 ms outside the video-feasible interval (kept at 125.727000s)
- NOT-IN-RAW: comp frames 596-604 (00:00:19:26-00:00:20:05) - placeholder 'MISSING - not in RAW (00:00:19:26-00:00:20:05)'
- NOT-IN-RAW: comp frames 1180-1191 (00:00:39:10-00:00:39:22) - placeholder 'MISSING - not in RAW (00:00:39:10-00:00:39:22)'
- NOT-IN-RAW: comp frames 1193-1193 (00:00:39:23-00:00:39:24) - placeholder 'MISSING - not in RAW (00:00:39:23-00:00:39:24)'
- 9 segment(s) have a phase margin below 1 ms (S18, S20, S30, S60, S72, S87, S90, S92, S97): the JSX re-checks them in After Effects and switches to frame-exact remapping if AE stores times differently; --ae-time-mode frames forces it
- verification: c2_cuts: cut S34|S35 at frame 607: A_last (k=606): own 0.89732 vs other 0.928294
- verification: c2_cuts: cut S62|S63 at frame 1192: placeholder_frame: placeholder vs extended S63 model must be < none_thresh 0.6
- verification: c2_cuts: cut S63|S64 at frame 1193: placeholder_frame: placeholder vs extended S63 model must be < none_thresh 0.6
- verification: c2_cuts: cut S64|S65 at frame 1194: placeholder_frame: placeholder vs extended S65 model must be < none_thresh 0.6
- verification: c2_cuts: cut S82|S83 at frame 1444: B_first (k=1444): own 0.684951 vs other 0.956303
- verification: c3_source_frames: plan: only 97.8470% of 1765 matched frames show the measured m(k) (< 99%); first differences at k = [38, 81, 91, 92, 93, 109, 120, 123, 129, 340]
- verification: c3_source_frames: mock record: only 97.8470% of 1765 matched frames show the measured m(k) (< 99%); first differences at k = [38, 81, 91, 92, 93, 109, 120, 123, 129, 340]
- verification: c3_source_frames: 11 matched frames below ZNCC 0.9: [[606, 606], [610, 610], [1104, 1104], [1194, 1195], [1421, 1421], [1444, 1444], [1451, 1451], [1742, 1744]]
- verification: c4_speed_framing: S03: framing off on frames [[42, 42]] (max scale err 0.45%, pos 4.17 px, rot 0.265°)
- verification: c4_speed_framing: S06: framing off on frames [[51, 51]] (max scale err 0.01%, pos 4.10 px, rot 0.108°)
- verification: c4_speed_framing: S06: independently measured framing differs from the segment model on 1/1 sampled frames [53] (max scale err 0.04%, pos 4.64 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S08: independently measured framing differs from the segment model on 1/1 sampled frames [59] (max scale err 0.55%, pos 6.29 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S12: independently measured framing differs from the segment model on 1/2 sampled frames [67] (max scale err 0.63%, pos 5.95 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S18: framing off on frames [[120, 120]] (max scale err 0.06%, pos 4.66 px, rot 0.000°)
- verification: c4_speed_framing: S21: framing off on frames [[266, 266]] (max scale err 0.07%, pos 5.58 px, rot 0.000°)
- verification: c4_speed_framing: S34: independently measured framing differs from the segment model on 2/2 sampled frames [605, 606] (max scale err 0.95%, pos 7.65 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S35: independently measured framing differs from the segment model on 2/2 sampled frames [607, 609] (max scale err 1.58%, pos 5.94 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S36: framing off on frames [[610, 610]] (max scale err 2.43%, pos 9.77 px, rot 0.214°)
- verification: c4_speed_framing: S36: independently measured framing differs from the segment model on 2/3 sampled frames [610, 614] (max scale err 2.67%, pos 15.28 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S39: framing off on frames [[644, 644]] (max scale err 0.02%, pos 5.14 px, rot 0.000°)
- verification: c4_speed_framing: S39: independently measured framing differs from the segment model on 2/6 sampled frames [632, 646] (max scale err 0.05%, pos 4.86 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S40: independently measured framing differs from the segment model on 2/2 sampled frames [647, 649] (max scale err 0.07%, pos 5.01 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S46: independently measured framing differs from the segment model on 1/2 sampled frames [656] (max scale err 0.01%, pos 4.94 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S47: independently measured framing differs from the segment model on 1/1 sampled frames [658] (max scale err 0.23%, pos 5.37 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S48: independently measured framing differs from the segment model on 1/1 sampled frames [659] (max scale err 0.35%, pos 5.17 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S50: framing off on frames [[662, 662]] (max scale err 0.45%, pos 4.64 px, rot 0.000°)
- verification: c4_speed_framing: S60: framing off on frames [[1100, 1107]] (max scale err 0.04%, pos 20.79 px, rot 0.000°)
- verification: c4_speed_framing: S74: framing off on frames [[1413, 1421]] (max scale err 0.42%, pos 18.26 px, rot 0.000°)
- verification: c4_speed_framing: S74: independently measured framing differs from the segment model on 2/2 sampled frames [1417, 1420] (max scale err 0.46%, pos 7.58 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S77: independently measured framing differs from the segment model on 1/1 sampled frames [1429] (max scale err 0.01%, pos 10.78 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S79: framing off on frames [[1433, 1433]] (max scale err 0.00%, pos 6.52 px, rot 0.082°)
- verification: c4_speed_framing: S81: framing off on frames [[1438, 1438]] (max scale err 0.98%, pos 9.17 px, rot 0.741°)
- verification: c4_speed_framing: S81: independently measured framing differs from the segment model on 1/1 sampled frames [1439] (max scale err 0.61%, pos 1.46 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S82: framing off on frames [[1442, 1442]] (max scale err 1.43%, pos 20.45 px, rot 0.452°)
- verification: c4_speed_framing: S83: framing off on frames [[1446, 1446]] (max scale err 1.14%, pos 1.87 px, rot 0.258°)
- verification: c4_speed_framing: S83: independently measured framing differs from the segment model on 2/2 sampled frames [1444, 1447] (max scale err 5.35%, pos 34.73 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S84: independently measured framing differs from the segment model on 1/2 sampled frames [1451] (max scale err 0.10%, pos 21.18 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S93: framing off on frames [[1742, 1746], [1748, 1751]] (max scale err 0.49%, pos 31.50 px, rot 0.923°)
- verification: c4_speed_framing: S93: independently measured framing differs from the segment model on 4/5 sampled frames [1742, 1747, 1750, 1756] (max scale err 0.49%, pos 31.46 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S96: framing off on frames [[1761, 1761]] (max scale err 0.02%, pos 4.37 px, rot 0.000°)
- verification: c4_speed_framing: S96: independently measured framing differs from the segment model on 1/3 sampled frames [1760] (max scale err 0.05%, pos 5.27 px, tolerance 1% / 4 px)
- verification: c4_speed_framing: S97: framing off on frames [[1770, 1772]] (max scale err 0.01%, pos 6.71 px, rot 0.000°)
- verification: c5_audio: S01: audio confidently misaligned (lag -86.94 ms, corr 1.00)
- verification: c5_audio: S15: audio confidently misaligned (lag -80.52 ms, corr 0.95)
- verification: c5_audio: S16: audio confidently misaligned (lag -80.83 ms, corr 0.94)
- verification: c5_audio: S19: audio confidently misaligned (lag -84.17 ms, corr 0.98)
- verification: c5_audio: S20: audio confidently misaligned (lag -86.48 ms, corr 0.97)
- verification: c5_audio: S21: audio confidently misaligned (lag -84.19 ms, corr 0.97)
- verification: c5_audio: S22: audio confidently misaligned (lag -86.19 ms, corr 0.97)
- verification: c5_audio: S23: audio confidently misaligned (lag -79.65 ms, corr 0.93)
- verification: c5_audio: S25: audio confidently misaligned (lag -80.81 ms, corr 0.97)
- verification: c5_audio: S26: audio confidently misaligned (lag -82.31 ms, corr 0.99)
- verification: c5_audio: S27: audio confidently misaligned (lag -85.65 ms, corr 0.99)
- verification: c5_audio: S28: audio confidently misaligned (lag -86.15 ms, corr 0.99)
- verification: c5_audio: S29: audio confidently misaligned (lag -87.31 ms, corr 0.99)
- verification: c5_audio: S31: audio confidently misaligned (lag -81.61 ms, corr 0.95)
- verification: c5_audio: S39: audio confidently misaligned (lag -83.44 ms, corr 0.94)
- verification: c5_audio: S51: audio confidently misaligned (lag -86.10 ms, corr 0.96)
- verification: c5_audio: S52: audio confidently misaligned (lag -81.96 ms, corr 0.98)
- verification: c5_audio: S53: audio confidently misaligned (lag -83.46 ms, corr 0.98)
- verification: c5_audio: S54: audio confidently misaligned (lag -85.46 ms, corr 0.98)
- verification: c5_audio: S55: audio confidently misaligned (lag -79.92 ms, corr 0.99)
- verification: c5_audio: S56: audio confidently misaligned (lag -81.42 ms, corr 0.94)
- verification: c5_audio: S57: audio confidently misaligned (lag -82.42 ms, corr 0.97)
- verification: c5_audio: S58: audio confidently misaligned (lag -83.92 ms, corr 0.98)
- verification: c5_audio: S59: audio confidently misaligned (lag -85.25 ms, corr 0.98)
- verification: c5_audio: S60: audio confidently misaligned (lag -86.17 ms, corr 0.96)
- verification: c5_audio: S61: audio confidently misaligned (lag -81.04 ms, corr 0.94)
- verification: c5_audio: S68: audio confidently misaligned (lag -84.38 ms, corr 0.94)
- verification: c5_audio: S69: audio confidently misaligned (lag -86.37 ms, corr 0.99)
- verification: c5_audio: S70: audio confidently misaligned (lag -84.23 ms, corr 0.96)
- verification: c5_audio: S71: audio confidently misaligned (lag -85.73 ms, corr 0.97)
- verification: c5_audio: S72: audio confidently misaligned (lag -85.65 ms, corr 0.99)
- verification: c5_audio: S73: audio confidently misaligned (lag -79.87 ms, corr 0.98)
- verification: c5_audio: S86: audio confidently misaligned (lag -86.35 ms, corr 0.98)
- verification: c5_audio: S87: audio confidently misaligned (lag -85.60 ms, corr 0.99)
- verification: c5_audio: S88: audio confidently misaligned (lag -82.98 ms, corr 0.97)
- verification: c5_audio: S89: audio confidently misaligned (lag -84.31 ms, corr 0.96)
- verification: c5_audio: S90: audio confidently misaligned (lag -85.73 ms, corr 0.97)
- verification: c5_audio: S91: audio confidently misaligned (lag -79.96 ms, corr 0.97)
- verification: c5_audio: S92: audio lag -84.96 ms / corr 0.73 outside ±10.0 ms and unexplained
- verification: c5_audio: S93: audio confidently misaligned (lag -78.67 ms, corr 0.98)
- verification: c6_after_effects: recreated_edit.aep missing
- verification: s9_8_deliverables: recreated_edit.aep (After Effects is installed) missing (output\recreated_edit.aep)

## 7. Verification details

| Check | Status | Summary |
|---|---|---|
| s9_1_coverage | PASS | 98 segments (3 NOT-IN-RAW), 1787/1787 frames covered, 0 gaps, 0 overlaps (0 transitions) |
| s9_2_ae_sim | FAIL | plan: 1683/1765 exact, 42 ambiguous-identical, 2 timing-tie, 38 re-assigned, 0 mismatched (97.8470% ok); plan vs cutlist: 0 differing frame(s) (0 transition, 22 placeholder/dip frames checked) \| mock record: 1683/1765 exact, 42 ambiguous-identical, 2 timing-tie, 38 re-assigned, 0 mismatched (97.8470% ok); plan vs cutlist: 0 differing frame(s) (0 transition, 22 placeholder/dip frames checked) |
| s9_3_visual | FAIL | 1765 matched frames, min ZNCC 0.68485, median 0.99239, 11 below 0.9; 0 blend frames, 0 uniform, 22 placeholder frames checked [preview_recreation.mp4]; delivered preview: 1787 frames at 30/1 fps, 608x1080 |
| s9_4_cut_images | PASS | 97/97 cut images in output\debug\cuts |
| s9_5_audio | FAIL | 40 segments measured, 58 explained exceptions, 40 failures |
| s9_6_ae_render | FAIL | After Effects is installed but recreated_edit.aep was not produced |
| s9_7_determinism | PASS | cutlist re-assembled from caches is byte-identical; identical to the previous run |
| s9_8_deliverables | FAIL | 9/10 deliverables present, exports validated, 0 stage errors |

Visual ZNCC over matched frames (preview_recreation.mp4): min 0.68485, p1 0.91702, p5 0.96385, median 0.99239, mean 0.98802; threshold 0.9.

| ZNCC bin | frames |
|---|---|
| -1.00-0.50 | 0 |
| 0.50-0.80 | 1 |
| 0.80-0.90 | 10 |
| 0.90-0.95 | 43 |
| 0.95-0.98 | 82 |
| 0.98-0.99 | 486 |
| 0.99-1.01 | 1143 |
Failure thumbnails: `debug/verify_failures/` (11 frames).

Audio per segment (original rate 48000 Hz; tolerance ±10.0 ms):

| segment | result | lag ms | corr | code |
|---|---|---|---|---|
| S01 | fail | -86.937 | 0.9951 |  |
| S02 | exception |  |  | too_short |
| S03 | exception |  |  | too_short |
| S04 | exception |  |  | too_short |
| S05 | exception |  |  | too_short |
| S06 | exception |  |  | too_short |
| S07 | exception |  |  | too_short |
| S08 | exception |  |  | too_short |
| S09 | exception |  |  | too_short |
| S10 | exception |  |  | too_short |
| S11 | exception |  |  | too_short |
| S12 | exception |  |  | too_short |
| S13 | exception |  |  | too_short |
| S14 | exception |  |  | too_short |
| S15 | fail | -80.517 | 0.9457 |  |
| S16 | fail | -80.834 | 0.9364 |  |
| S17 | exception |  |  | too_short |
| S18 | exception |  |  | too_short |
| S19 | fail | -84.167 | 0.9839 |  |
| S20 | fail | -86.479 | 0.9731 |  |
| S21 | fail | -84.187 | 0.967 |  |
| S22 | fail | -86.188 | 0.9739 |  |
| S23 | fail | -79.647 | 0.9258 |  |
| S24 | exception |  |  | too_short |
| S25 | fail | -80.813 | 0.9674 |  |
| S26 | fail | -82.313 | 0.9897 |  |
| S27 | fail | -85.646 | 0.9894 |  |
| S28 | fail | -86.147 | 0.9903 |  |
| S29 | fail | -87.313 | 0.9871 |  |
| S30 | exception |  |  | too_short |
| S31 | fail | -81.605 | 0.9454 |  |
| S32 | exception |  |  | too_short |
| S33 | exception |  |  | not_in_raw |
| S34 | exception |  |  | too_short |
| S35 | exception |  |  | too_short |
| S36 | exception |  |  | too_short |
| S37 | exception |  |  | too_short |
| S38 | exception |  |  | too_short |
| S39 | fail | -83.437 | 0.9383 |  |
| S40 | exception |  |  | too_short |
| S41 | exception |  |  | too_short |
| S42 | exception |  |  | too_short |
| S43 | exception |  |  | too_short |
| S44 | exception |  |  | too_short |
| S45 | exception |  |  | too_short |
| S46 | exception |  |  | too_short |
| S47 | exception |  |  | too_short |
| S48 | exception |  |  | too_short |
| S49 | exception |  |  | too_short |
| S50 | exception |  |  | too_short |
| S51 | fail | -86.105 | 0.9625 |  |
| S52 | fail | -81.959 | 0.9751 |  |
| S53 | fail | -83.46 | 0.9774 |  |
| S54 | fail | -85.458 | 0.9771 |  |
| S55 | fail | -79.921 | 0.9852 |  |
| S56 | fail | -81.417 | 0.9364 |  |
| S57 | fail | -82.417 | 0.9701 |  |
| S58 | fail | -83.917 | 0.9829 |  |
| S59 | fail | -85.25 | 0.9769 |  |
| S60 | fail | -86.167 | 0.9624 |  |
| S61 | fail | -81.041 | 0.9361 |  |
| S62 | exception |  |  | not_in_raw |
| S63 | exception |  |  | too_short |
| S64 | exception |  |  | not_in_raw |
| S65 | exception |  |  | too_short |
| S66 | exception |  |  | too_short |
| S67 | exception |  |  | too_short |
| S68 | fail | -84.375 | 0.9414 |  |
| S69 | fail | -86.375 | 0.9935 |  |
| S70 | fail | -84.229 | 0.9601 |  |
| S71 | fail | -85.729 | 0.9738 |  |
| S72 | fail | -85.646 | 0.9923 |  |
| S73 | fail | -79.868 | 0.9767 |  |
| S74 | exception |  |  | too_short |
| S75 | exception |  |  | too_short |
| S76 | exception |  |  | too_short |
| S77 | exception |  |  | too_short |
| S78 | exception |  |  | too_short |
| S79 | exception |  |  | too_short |
| S80 | exception |  |  | too_short |
| S81 | exception |  |  | too_short |
| S82 | exception |  |  | too_short |
| S83 | exception |  |  | too_short |
| S84 | exception |  |  | too_short |
| S85 | exception |  |  | too_short |
| S86 | fail | -86.354 | 0.9762 |  |
| S87 | fail | -85.604 | 0.9912 |  |
| S88 | fail | -82.98 | 0.9715 |  |
| S89 | fail | -84.313 | 0.9642 |  |
| S90 | fail | -85.729 | 0.9746 |  |
| S91 | fail | -79.958 | 0.97 |  |
| S92 | fail | -84.958 | 0.7324 |  |
| S93 | fail | -78.667 | 0.9788 |  |
| S94 | exception |  |  | too_short |
| S95 | exception |  |  | too_short |
| S96 | exception |  |  | too_short |
| S97 | exception |  |  | too_short |
| S98 | exception |  |  | too_short |

Cuts (competitor vs recreation images in `debug/cuts/cut_XX.png`):

| cut | frame | kind | status |
|---|---|---|---|
| 01: S01\|S02 | 39 (00:00:01:09) | hard | pass |
| 02: S02\|S03 | 41 (00:00:01:11) | hard | pass |
| 03: S03\|S04 | 44 (00:00:01:14) | hard | pass |
| 04: S04\|S05 | 49 (00:00:01:19) | hard | pass |
| 05: S05\|S06 | 50 (00:00:01:20) | hard | pass |
| 06: S06\|S07 | 54 (00:00:01:24) | hard | pass |
| 07: S07\|S08 | 59 (00:00:01:29) | hard | pass |
| 08: S08\|S09 | 60 (00:00:02:00) | hard | pass |
| 09: S09\|S10 | 64 (00:00:02:04) | hard | pass |
| 10: S10\|S11 | 65 (00:00:02:05) | hard | pass |
| 11: S11\|S12 | 66 (00:00:02:06) | hard | pass |
| 12: S12\|S13 | 68 (00:00:02:08) | hard | pass |
| 13: S13\|S14 | 70 (00:00:02:10) | hard | pass |
| 14: S14\|S15 | 79 (00:00:02:19) | hard | pass |
| 15: S15\|S16 | 101 (00:00:03:11) | hard | pass |
| 16: S16\|S17 | 108 (00:00:03:18) | hard | pass |
| 17: S17\|S18 | 118 (00:00:03:28) | hard | pass |
| 18: S18\|S19 | 130 (00:00:04:10) | hard | pass |
| 19: S19\|S20 | 160 (00:00:05:10) | hard | pass |
| 20: S20\|S21 | 241 (00:00:08:01) | hard | pass |
| 21: S21\|S22 | 294 (00:00:09:24) | hard | pass |
| 22: S22\|S23 | 330 (00:00:11:00) | hard | pass |
| 23: S23\|S24 | 341 (00:00:11:11) | hard | pass |
| 24: S24\|S25 | 349 (00:00:11:19) | hard | pass |
| 25: S25\|S26 | 368 (00:00:12:08) | hard | pass |
| 26: S26\|S27 | 460 (00:00:15:10) | hard | pass |
| 27: S27\|S28 | 513 (00:00:17:03) | hard | pass |
| 28: S28\|S29 | 526 (00:00:17:16) | hard | pass |
| 29: S29\|S30 | 559 (00:00:18:19) | hard | pass |
| 30: S30\|S31 | 571 (00:00:19:01) | hard | pass |
| 31: S31\|S32 | 592 (00:00:19:22) | hard | pass |
| 32: S32\|S33 | 596 (00:00:19:26) | raw_to_placeholder | pass |
| 33: S33\|S34 | 605 (00:00:20:05) | placeholder_to_raw | pass |
| 34: S34\|S35 | 607 (00:00:20:07) | hard | fail |
| 35: S35\|S36 | 610 (00:00:20:10) | hard | pass |
| 36: S36\|S37 | 615 (00:00:20:15) | hard | pass |
| 37: S37\|S38 | 618 (00:00:20:18) | hard | pass |
| 38: S38\|S39 | 632 (00:00:21:02) | hard | pass |
| 39: S39\|S40 | 647 (00:00:21:17) | hard | pass |
| 40: S40\|S41 | 650 (00:00:21:20) | hard | pass |
| 41: S41\|S42 | 651 (00:00:21:21) | hard | pass |
| 42: S42\|S43 | 653 (00:00:21:23) | hard | pass |
| 43: S43\|S44 | 654 (00:00:21:24) | hard | pass |
| 44: S44\|S45 | 655 (00:00:21:25) | hard | pass |
| 45: S45\|S46 | 656 (00:00:21:26) | hard | pass |
| 46: S46\|S47 | 658 (00:00:21:28) | hard | pass |
| 47: S47\|S48 | 659 (00:00:21:29) | hard | pass |
| 48: S48\|S49 | 660 (00:00:22:00) | hard | pass |
| 49: S49\|S50 | 661 (00:00:22:01) | hard | pass |
| 50: S50\|S51 | 666 (00:00:22:06) | hard | pass |
| 51: S51\|S52 | 715 (00:00:23:25) | hard | pass |
| 52: S52\|S53 | 737 (00:00:24:17) | hard | pass |
| 53: S53\|S54 | 782 (00:00:26:02) | hard | pass |
| 54: S54\|S55 | 842 (00:00:28:02) | hard | pass |
| 55: S55\|S56 | 894 (00:00:29:24) | hard | pass |
| 56: S56\|S57 | 942 (00:00:31:12) | hard | pass |
| 57: S57\|S58 | 971 (00:00:32:11) | hard | pass |
| 58: S58\|S59 | 1017 (00:00:33:27) | hard | pass |
| 59: S59\|S60 | 1057 (00:00:35:07) | hard | pass |
| 60: S60\|S61 | 1141 (00:00:38:01) | hard | pass |
| 61: S61\|S62 | 1180 (00:00:39:10) | raw_to_placeholder | pass |
| 62: S62\|S63 | 1192 (00:00:39:22) | placeholder_to_raw | fail |
| 63: S63\|S64 | 1193 (00:00:39:23) | raw_to_placeholder | fail |
| 64: S64\|S65 | 1194 (00:00:39:24) | placeholder_to_raw | fail |
| 65: S65\|S66 | 1204 (00:00:40:04) | hard | pass |
| 66: S66\|S67 | 1208 (00:00:40:08) | hard | pass |
| 67: S67\|S68 | 1209 (00:00:40:09) | hard | pass |
| 68: S68\|S69 | 1273 (00:00:42:13) | hard | pass |
| 69: S69\|S70 | 1301 (00:00:43:11) | hard | pass |
| 70: S70\|S71 | 1317 (00:00:43:27) | hard | pass |
| 71: S71\|S72 | 1360 (00:00:45:10) | hard | pass |
| 72: S72\|S73 | 1401 (00:00:46:21) | hard | pass |
| 73: S73\|S74 | 1411 (00:00:47:01) | hard | pass |
| 74: S74\|S75 | 1423 (00:00:47:13) | hard | pass |
| 75: S75\|S76 | 1425 (00:00:47:15) | hard | pass |
| 76: S76\|S77 | 1426 (00:00:47:16) | hard | pass |
| 77: S77\|S78 | 1430 (00:00:47:20) | hard | pass |
| 78: S78\|S79 | 1432 (00:00:47:22) | hard | pass |
| 79: S79\|S80 | 1435 (00:00:47:25) | hard | pass |
| 80: S80\|S81 | 1437 (00:00:47:27) | hard | pass |
| 81: S81\|S82 | 1440 (00:00:48:00) | hard | pass |
| 82: S82\|S83 | 1444 (00:00:48:04) | hard | fail |
| 83: S83\|S84 | 1449 (00:00:48:09) | hard | pass |
| 84: S84\|S85 | 1452 (00:00:48:12) | hard | pass |
| 85: S85\|S86 | 1465 (00:00:48:25) | hard | pass |
| 86: S86\|S87 | 1508 (00:00:50:08) | hard | pass |
| 87: S87\|S88 | 1567 (00:00:52:07) | hard | pass |
| 88: S88\|S89 | 1604 (00:00:53:14) | hard | pass |
| 89: S89\|S90 | 1623 (00:00:54:03) | hard | pass |
| 90: S90\|S91 | 1657 (00:00:55:07) | hard | pass |
| 91: S91\|S92 | 1706 (00:00:56:26) | hard | pass |
| 92: S92\|S93 | 1742 (00:00:58:02) | hard | pass |
| 93: S93\|S94 | 1757 (00:00:58:17) | hard | pass |
| 94: S94\|S95 | 1758 (00:00:58:18) | hard | pass |
| 95: S95\|S96 | 1760 (00:00:58:20) | hard | exception |
| 96: S96\|S97 | 1768 (00:00:58:28) | hard | pass |
| 97: S97\|S98 | 1774 (00:00:59:04) | hard | pass |

After Effects mock run:

- [x] jsx ran without exception
- [x] no strict-mock violations
- [x] no error alerts
- [x] one balanced undo group
- [x] MAIN comp created
- [x] MAIN frameRate == main_fps
- [x] MAIN duration == frames * frameDuration
- [x] work area == whole comp
- [x] MAIN width
- [x] MAIN height
- [x] saved <script dir>/recreated_edit.aep
- [x] one RAW video layer per segment
- [x] every plan layer present with the plan's name/startTime/stretch/in/out
- [x] media_missing: no exception
- [x] media_missing: File.openDialog called
- [x] media_missing: aborted without saving
- [x] media_missing: clean abort message
- [x] new_project_null: clean abort
- [x] no_marker_property: still builds and saves

Failures:

- c2_cuts: cut S34|S35 at frame 607: A_last (k=606): own 0.89732 vs other 0.928294
- c2_cuts: cut S62|S63 at frame 1192: placeholder_frame: placeholder vs extended S63 model must be < none_thresh 0.6
- c2_cuts: cut S63|S64 at frame 1193: placeholder_frame: placeholder vs extended S63 model must be < none_thresh 0.6
- c2_cuts: cut S64|S65 at frame 1194: placeholder_frame: placeholder vs extended S65 model must be < none_thresh 0.6
- c2_cuts: cut S82|S83 at frame 1444: B_first (k=1444): own 0.684951 vs other 0.956303
- c3_source_frames: plan: only 97.8470% of 1765 matched frames show the measured m(k) (< 99%); first differences at k = [38, 81, 91, 92, 93, 109, 120, 123, 129, 340]
- c3_source_frames: mock record: only 97.8470% of 1765 matched frames show the measured m(k) (< 99%); first differences at k = [38, 81, 91, 92, 93, 109, 120, 123, 129, 340]
- c3_source_frames: 11 matched frames below ZNCC 0.9: [[606, 606], [610, 610], [1104, 1104], [1194, 1195], [1421, 1421], [1444, 1444], [1451, 1451], [1742, 1744]]
- c4_speed_framing: S03: framing off on frames [[42, 42]] (max scale err 0.45%, pos 4.17 px, rot 0.265°)
- c4_speed_framing: S06: framing off on frames [[51, 51]] (max scale err 0.01%, pos 4.10 px, rot 0.108°)
- c4_speed_framing: S06: independently measured framing differs from the segment model on 1/1 sampled frames [53] (max scale err 0.04%, pos 4.64 px, tolerance 1% / 4 px)
- c4_speed_framing: S08: independently measured framing differs from the segment model on 1/1 sampled frames [59] (max scale err 0.55%, pos 6.29 px, tolerance 1% / 4 px)
- c4_speed_framing: S12: independently measured framing differs from the segment model on 1/2 sampled frames [67] (max scale err 0.63%, pos 5.95 px, tolerance 1% / 4 px)
- c4_speed_framing: S18: framing off on frames [[120, 120]] (max scale err 0.06%, pos 4.66 px, rot 0.000°)
- c4_speed_framing: S21: framing off on frames [[266, 266]] (max scale err 0.07%, pos 5.58 px, rot 0.000°)
- c4_speed_framing: S34: independently measured framing differs from the segment model on 2/2 sampled frames [605, 606] (max scale err 0.95%, pos 7.65 px, tolerance 1% / 4 px)
- c4_speed_framing: S35: independently measured framing differs from the segment model on 2/2 sampled frames [607, 609] (max scale err 1.58%, pos 5.94 px, tolerance 1% / 4 px)
- c4_speed_framing: S36: framing off on frames [[610, 610]] (max scale err 2.43%, pos 9.77 px, rot 0.214°)
- c4_speed_framing: S36: independently measured framing differs from the segment model on 2/3 sampled frames [610, 614] (max scale err 2.67%, pos 15.28 px, tolerance 1% / 4 px)
- c4_speed_framing: S39: framing off on frames [[644, 644]] (max scale err 0.02%, pos 5.14 px, rot 0.000°)
- c4_speed_framing: S39: independently measured framing differs from the segment model on 2/6 sampled frames [632, 646] (max scale err 0.05%, pos 4.86 px, tolerance 1% / 4 px)
- c4_speed_framing: S40: independently measured framing differs from the segment model on 2/2 sampled frames [647, 649] (max scale err 0.07%, pos 5.01 px, tolerance 1% / 4 px)
- c4_speed_framing: S46: independently measured framing differs from the segment model on 1/2 sampled frames [656] (max scale err 0.01%, pos 4.94 px, tolerance 1% / 4 px)
- c4_speed_framing: S47: independently measured framing differs from the segment model on 1/1 sampled frames [658] (max scale err 0.23%, pos 5.37 px, tolerance 1% / 4 px)
- c4_speed_framing: S48: independently measured framing differs from the segment model on 1/1 sampled frames [659] (max scale err 0.35%, pos 5.17 px, tolerance 1% / 4 px)
- c4_speed_framing: S50: framing off on frames [[662, 662]] (max scale err 0.45%, pos 4.64 px, rot 0.000°)
- c4_speed_framing: S60: framing off on frames [[1100, 1107]] (max scale err 0.04%, pos 20.79 px, rot 0.000°)
- c4_speed_framing: S74: framing off on frames [[1413, 1421]] (max scale err 0.42%, pos 18.26 px, rot 0.000°)
- c4_speed_framing: S74: independently measured framing differs from the segment model on 2/2 sampled frames [1417, 1420] (max scale err 0.46%, pos 7.58 px, tolerance 1% / 4 px)
- c4_speed_framing: S77: independently measured framing differs from the segment model on 1/1 sampled frames [1429] (max scale err 0.01%, pos 10.78 px, tolerance 1% / 4 px)
- c4_speed_framing: S79: framing off on frames [[1433, 1433]] (max scale err 0.00%, pos 6.52 px, rot 0.082°)
- c4_speed_framing: S81: framing off on frames [[1438, 1438]] (max scale err 0.98%, pos 9.17 px, rot 0.741°)
- c4_speed_framing: S81: independently measured framing differs from the segment model on 1/1 sampled frames [1439] (max scale err 0.61%, pos 1.46 px, tolerance 1% / 4 px)
- c4_speed_framing: S82: framing off on frames [[1442, 1442]] (max scale err 1.43%, pos 20.45 px, rot 0.452°)
- c4_speed_framing: S83: framing off on frames [[1446, 1446]] (max scale err 1.14%, pos 1.87 px, rot 0.258°)
- c4_speed_framing: S83: independently measured framing differs from the segment model on 2/2 sampled frames [1444, 1447] (max scale err 5.35%, pos 34.73 px, tolerance 1% / 4 px)
- c4_speed_framing: S84: independently measured framing differs from the segment model on 1/2 sampled frames [1451] (max scale err 0.10%, pos 21.18 px, tolerance 1% / 4 px)
- c4_speed_framing: S93: framing off on frames [[1742, 1746], [1748, 1751]] (max scale err 0.49%, pos 31.50 px, rot 0.923°)
- c4_speed_framing: S93: independently measured framing differs from the segment model on 4/5 sampled frames [1742, 1747, 1750, 1756] (max scale err 0.49%, pos 31.46 px, tolerance 1% / 4 px)
- c4_speed_framing: S96: framing off on frames [[1761, 1761]] (max scale err 0.02%, pos 4.37 px, rot 0.000°)
- c4_speed_framing: S96: independently measured framing differs from the segment model on 1/3 sampled frames [1760] (max scale err 0.05%, pos 5.27 px, tolerance 1% / 4 px)
- c4_speed_framing: S97: framing off on frames [[1770, 1772]] (max scale err 0.01%, pos 6.71 px, rot 0.000°)
- c5_audio: S01: audio confidently misaligned (lag -86.94 ms, corr 1.00)
- c5_audio: S15: audio confidently misaligned (lag -80.52 ms, corr 0.95)
- c5_audio: S16: audio confidently misaligned (lag -80.83 ms, corr 0.94)
- c5_audio: S19: audio confidently misaligned (lag -84.17 ms, corr 0.98)
- c5_audio: S20: audio confidently misaligned (lag -86.48 ms, corr 0.97)
- c5_audio: S21: audio confidently misaligned (lag -84.19 ms, corr 0.97)
- c5_audio: S22: audio confidently misaligned (lag -86.19 ms, corr 0.97)
- c5_audio: S23: audio confidently misaligned (lag -79.65 ms, corr 0.93)
- c5_audio: S25: audio confidently misaligned (lag -80.81 ms, corr 0.97)
- c5_audio: S26: audio confidently misaligned (lag -82.31 ms, corr 0.99)
- c5_audio: S27: audio confidently misaligned (lag -85.65 ms, corr 0.99)
- c5_audio: S28: audio confidently misaligned (lag -86.15 ms, corr 0.99)
- c5_audio: S29: audio confidently misaligned (lag -87.31 ms, corr 0.99)
- c5_audio: S31: audio confidently misaligned (lag -81.61 ms, corr 0.95)
- c5_audio: S39: audio confidently misaligned (lag -83.44 ms, corr 0.94)
- c5_audio: S51: audio confidently misaligned (lag -86.10 ms, corr 0.96)
- c5_audio: S52: audio confidently misaligned (lag -81.96 ms, corr 0.98)
- c5_audio: S53: audio confidently misaligned (lag -83.46 ms, corr 0.98)
- c5_audio: S54: audio confidently misaligned (lag -85.46 ms, corr 0.98)
- c5_audio: S55: audio confidently misaligned (lag -79.92 ms, corr 0.99)
- c5_audio: S56: audio confidently misaligned (lag -81.42 ms, corr 0.94)
- c5_audio: S57: audio confidently misaligned (lag -82.42 ms, corr 0.97)
- c5_audio: S58: audio confidently misaligned (lag -83.92 ms, corr 0.98)
- c5_audio: S59: audio confidently misaligned (lag -85.25 ms, corr 0.98)
- c5_audio: S60: audio confidently misaligned (lag -86.17 ms, corr 0.96)
- c5_audio: S61: audio confidently misaligned (lag -81.04 ms, corr 0.94)
- c5_audio: S68: audio confidently misaligned (lag -84.38 ms, corr 0.94)
- c5_audio: S69: audio confidently misaligned (lag -86.37 ms, corr 0.99)
- c5_audio: S70: audio confidently misaligned (lag -84.23 ms, corr 0.96)
- c5_audio: S71: audio confidently misaligned (lag -85.73 ms, corr 0.97)
- c5_audio: S72: audio confidently misaligned (lag -85.65 ms, corr 0.99)
- c5_audio: S73: audio confidently misaligned (lag -79.87 ms, corr 0.98)
- c5_audio: S86: audio confidently misaligned (lag -86.35 ms, corr 0.98)
- c5_audio: S87: audio confidently misaligned (lag -85.60 ms, corr 0.99)
- c5_audio: S88: audio confidently misaligned (lag -82.98 ms, corr 0.97)
- c5_audio: S89: audio confidently misaligned (lag -84.31 ms, corr 0.96)
- c5_audio: S90: audio confidently misaligned (lag -85.73 ms, corr 0.97)
- c5_audio: S91: audio confidently misaligned (lag -79.96 ms, corr 0.97)
- c5_audio: S92: audio lag -84.96 ms / corr 0.73 outside ±10.0 ms and unexplained
- c5_audio: S93: audio confidently misaligned (lag -78.67 ms, corr 0.98)
- c6_after_effects: recreated_edit.aep missing
- s9_8_deliverables: recreated_edit.aep (After Effects is installed) missing (output\recreated_edit.aep)

## 8. How to open in After Effects

1. Copy the whole output folder (the `.jsx` finds `media/` next to itself; keep them together).
2. After Effects → **File → Scripts → Run Script File…** → `build_ae_project.jsx`.
3. The script creates a new project with the `Recreated Edit` comp and saves `recreated_edit.aep` next to the script. If saving fails, enable **Preferences → Scripting & Expressions → Allow Scripts to Write Files and Access Network** (in versions before 16.1: Preferences → General) and run it again.
4. If the RAW media is not found next to the script (`media/raw.mp4`), the script tries the absolute path and then opens a *Locate the RAW video* dialog (relink).
5. Reference layer: `REFERENCE – competitor` sits on top as a guide layer in *Difference* mode, switched off. Turn its video switch on: black means the recreation matches the competitor exactly. Guide layers never render.
6. Guide layers outline the header / title / caption / watermark zones — drop your own assets there. `MISSING – not in RAW` solids mark the ranges that have to be filled with your own footage.
7. Alternative route: import `recreated_edit.xml` (FCP7 XML) in Premiere Pro or DaVinci Resolve.

## 9. Outputs

| output | path | what |
|---|---|---|
| cutlist | cutlist.json | cut list (source of truth) |
| jsx | build_ae_project.jsx | After Effects build script |
| csv | cutlist.csv | cut list, one row per segment |
| xml | recreated_edit.xml | FCP7 XML (Premiere / Resolve) |
| edl | recreated_edit.edl | CMX3600 EDL |
| preview | preview_recreation.mp4 | frame-exact preview render |
| compare | compare.mp4 | competitor \| recreation \| difference |
| verify | verify.json | verification results |
| report | report.md | this report |
| media | media | AE-imported media |
| debug | debug | debug plots, cut images, failure thumbnails |
| decisions | debug\decisions.jsonl | decision log (evidence) |
| log | ..\work\match_cuts.log | run log |
| frame_map | ..\work\frame_map.npz | per-frame mapping m(k) |

## 10. Environment and timings

- OS: Windows-11-10.0.26200-SP0, Python 3.14.3, ffmpeg 8.0, Node v25.2.0
- After Effects: C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\AfterFX.exe; aerender: C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\aerender.exe
- match_cuts 0.1.0

| stage | seconds |
|---|---|
| S0 env | 0.13 |
| S2 probe+conform | 0.36 |
| S3 audio | 0.01 |
| S5.1 audio align | 0.01 |
| S3 proxies | 0.00 |
| S4 layout | 0.01 |
| S5.4-S6 segments+cutlist | 5.56 |
| S7 AE project | 0.46 |
| S8.preview | 12.44 |
| S8.compare | 25.42 |
| S8 exports | 37.91 |
| S9 verify | 19.49 |
| total | 64.53 |
