# Task 9: speed -- work in progress (stopped for the PC restart, 5 Oct 2026, 22:15)

**Where I stopped:** everything below is committed and pushed. No run is going on (the video2 smoke test was stopped
after its edit was written, during its checks). Nothing was left half-written on disk that a run depends on.

## Done (in this commit)

1. **The exact nearest-neighbour search is 10 times faster, with the same results** (`gpu.KnnIndex`). The RAW
   index is kept on the GPU in float16 (SIFT values are whole numbers 0-255, exact in float16) and multiplied on the
   tensor cores with float32 sums: every product and partial sum is a whole number under 2^24, so every distance is
   exact, as before. The 24 nearest are then found in two stages (the two-stage search from Task 5's report):
   each block's distances in groups of 64, the 24 groups with the smallest minimum -- they must hold the 24 nearest
   -- and their members ranked by distance, then index. The index takes half the GPU memory it did.
   - On real data: 100,000 SIFT descriptors of video1's RAW against its 10 M-descriptor every-frame index:
     **34-38 s instead of 363 s, 0 differing rows** (indices and distances; measured while Adobe was rendering).
   - Its own exactness test (`tests/test_gpu.py`): against a brute force in whole numbers, with many equal
     distances, a last group and chunk cut short, k larger than a group, one chunk holding all 24 nearest in 24
     groups, and the extremes of uint8. Checked to fail when one group too few is kept.
2. **A normal run no longer re-runs the segments stage to check it is deterministic** (check 9.7): that re-run
   costs as long as the segments stage itself (13-17 min of video1's 22-minute verify) and changes nothing in the
   edit. check-all still does it on every video (`--check-determinism`, which any run can ask for). In a normal run
   9.7 compares the run with the previous run of the same inputs and settings (PASS / FAIL), else says N/A.
3. **The full-resolution scorer is 1.8 times faster, with the same numbers bit for bit** (`fullres.Scorer`): each
   framing is sampled once (the score at it and the next Gauss-Newton step from it share the samples; the old code
   sampled it twice), a mask's pixels are found once and shared, the pixel grid is kept per region, the 4x4
   system comes to the CPU in one copy, and `measure` / the 9.9 check take the start score from the refinement
   instead of scoring it again. 45 -> 25 ms per refinement at video1's size. Test: the old algorithm copied into
   `tests/test_fullres.py` as the reference, equal to the bit on four cases. This scorer is most of the segments
   stage (717 of 1,124 s on video1), all of the full-resolution re-check, and verify's 9.9.
4. **The search limited to where the audio places the competitor** (your message): implemented and tested, but
   **switched off by default in this commit** (`audio_region_margin_s = 0` in `config.py`), because on video2 it
   did not keep the result the same -- below.

## Where the time goes (before this task)

Your video1 run from empty caches (`work/t8/user/video1/001` + `002`): RAW index 4.4 min, visual search 83 min (59 of
it the exact neighbour search), refine 20 min, full-resolution re-check 30 min, segments 19 min, exports 3 min,
captions 1 min, verify 22 min (13 of it the determinism re-run) -- about 3 hours. Items 1-3 above take the 59, the
13 and part of the re-check / segments / verify time; refine (20 min, CPU) and the per-frame search work (19 min)
are untouched so far.

## The audio regions (switched off for now)

How it works: the confident audio hints (S5.1) give the RAW times the competitor's sound comes from; the RAW index
holds only those, +-30 s (`pipeline.hint_windows`). A competitor frame the audio places is searched in its audio
window, then in the regions; a frame the audio cannot place, and one with no strong anchor in the regions, is
searched in the whole RAW, whose index is built only then (`visual_match.run_searches`). Refine's rescue searches go
the same way. (The full-resolution re-check and refine's own measurements already only look at each frame's own
candidates, a few RAW frames around its match -- their time grows with the competitor's frames, not the RAW's
length.)

Why +-30 s: on the 8 test videos the farthest final picture lies 14.7 s from where the audio places it (video4's
cutaways), every other within 4 s; +-30 s keeps 5-13 % of your long RAWs (video1 10 %, video2 5 %, video3 9 %,
video4 13 %).

**The video2 smoke test** (the Task 9 code with the regions on, `work/frozen/t9a`, from empty caches, run folder
`work/t9/smoke/runs/001`):
- the index of the regions: 1,782 of the RAW's 36,610 frames (5 %), built in 9 s;
- **24 of the 281 competitor frames had no strong anchor in their audio window or the regions**, so the whole RAW's
  index was built for them (36,610 frames, 9.9 M descriptors: 10.7 min, with Adobe rendering on the CPU):
  - 17 match nothing anywhere -- as in Task 8: k 21, 25, 37, 38, 39, 40, 42, 43, 48, 49, 57, 72, 79, 85, 159, 172,
    176. The whole RAW never helps these;
  - 4 that Task 8 found in their audio window, the regional search missed, and the whole RAW found on the same RAW
    frame: k 44 (RAW 7492), 89 (7536), 174 (7634), 175 (7633);
  - k 64: the same near miss as in Task 8 (7430);
  - k 178: an anchor Task 8 did not have (7708, ZNCC 0.88);
- of all frames, 244 of 264 have the same best anchor as in Task 8; 20 differ, mostly by 1-2 RAW frames (k 30, 60,
  80, 96, 110, 118, 180, 193, 210, 222, 263, 268), by 4-5 (k 184, 195, 197, 201), by 10 (k 234), and k 23 and 157
  gained one;
- **the edit came out different**: 25 segments instead of 23, several cut points and speeds changed -- e.g. frames
  193-209 became one segment at 15 % speed where Task 8 has two at 100 %. So it does not meet your condition, and I
  switched it off.

Why the anchors change: with an index of the regions only, each descriptor's 24 nearest are taken from the regions.
The search's vote rule was made for the whole RAW: a descriptor whose nearest match lies elsewhere in the RAW did not
vote at all. In the regions it votes for its best match there, so the audio window gets more and noisier votes; 4
frames lost their true candidate that way, others moved by a frame or two, and refine then built partly different
tracks.

## What's next

1. **Tell the regions' effect from the caches' history**: Task 8's video2 analysis was cached during Task 8 by
   earlier versions of the code; a fresh run of the Task 8 code (`work/frozen/t8k`) on video2 shows how much of the
   difference is the regions'.
2. **Make the regional search give the whole search's votes**, or keep the whole RAW's neighbours. Options I see:
   - the whole RAW's neighbours with item 1's faster search (exactly the same results; video1's 59-minute search
     should drop to about 6 minutes) and the audio regions only where they cannot change a result;
   - the regions' index plus the information the vote rule needs from the rest of the RAW (whether a descriptor's
     nearest match lies outside the regions), from a much smaller sampled index of the whole RAW;
   then compare on all eight test videos (cut lists and `1_edit.xml`) before turning anything on.
3. **The fallback**: the 17 frames that match nothing anywhere should not cost a 10-minute index of the whole RAW --
   a frame with too few features, or with no candidate passing even near its own sound, will not be found elsewhere
   either. Decide from each frame's failure reason, keeping cutaways from farther away searched.
4. **Time video1 before and after**, from empty caches, with your full-size `finished\video1` files, both on a free
   GPU (tonight After Effects and Media Encoder were rendering: ~5 CPU threads and 6.7 GB of the GPU's memory).
5. Then the rest of the list: refine and the per-frame search (both CPU, 20 and 19 minutes on video1), README
   (the new search, `--check-determinism`, run times), check-all, the final report, commit and push. If a video
   cannot get under 25 minutes, the trade-off and which mode should be the default.

## Tests (this commit)

- Unit suite on this code with the regions on: 1,082 passed, 12 skipped; 6 CLI tests failed on their stub of the
  RAW index (it did not take the new `regions` argument) and pass after the stub was fixed (57 of 57); the
  load-sensitive MKV duration probe test failed once while Adobe rendered and passes alone (Task 10 fixes it).
  With the regions switched off (this commit): the 7 affected test files (search, scorer, CLI, check-all, verify,
  visual match, the thorough/fast settings) -- 250 passed.
- New: `tests/test_gpu.py` (the search's exactness), `test_visual_match.py` (the index of the audio regions holds
  the whole index's rows; no votes across the gap between two regions -- checked to fail without the gap rule; frames
  searched in the regions and in the whole RAW only when needed), `test_fullres.py` (the scorer equal to the old one
  bit for bit), `test_verify.py` / `test_testcases.py` (a normal run does not re-assemble its cut list; check-all
  asks for it).
- check-all was not run on this code yet.
