# Task 9: speed -- work in progress (stopped for a second PC restart, 6 Oct 2026, about 00:40)

## Where I stopped

Everything below is committed and pushed: the code is the frozen copy `work/frozen/t9c` the timing runs used. No run
is going on. The final timing batch (`work/t9/time/run_after.sh`, code `t9c`) was stopped because the PC had become
stuck and very laggy; the video1 run's log stops at 00:20, 3.5 minutes into refine.

**Timing runs that finished, or measured something:**

| run | when | code, files | what it measured | caveat |
|---|---|---|---|---|
| video2, the Task 8 code, from empty caches | 5 Oct 22:14-22:34 (after the first restart) | `t8k`, test-case files | **20.3 min** in all: index 2.5, exact search 1.3, per-frame search 6.0, refine 2.2, re-check 1.8, segments 1.8, exports + captions 0.7, verify 3.5 (1.8 of it the determinism re-run) | none known: 16 cores, nothing else running; its edit equals Task 8's exactly |
| video1, the Task 8 code ("before") | 5 Oct 22:40-23:05, stopped (as you asked) | `t8k`, full-size files | index 2.4 min; the old exact search still running after 21 min | stopped early |
| video1, the Task 8 code, Task 8's run | 5 Oct 10:19-15:15 | the code of that morning, test-case files | about 3.8 h: index 4.4 min, search 132 min, refine 15-21, re-check 34, segments 11, exports + captions 6.5, verify 23 | **approximate**: other runs and Adobe shared the machine, and before the first restart Windows used only 15 of the 16 cores, the CPU sometimes sat at very low usage and the PC was very laggy -- the old code was probably faster than this |
| video1, the new code without the overlap (item 5) | 5 Oct 23:15-23:37, stopped | `t9b`, full-size files | index 3.0 min, competitor features about 1.5, **exact search 12.5 min**, per-frame search about 20 (from its progress) | stopped to add item 5 |
| video1, the final code | 5 Oct 23:42 - 6 Oct 00:20, stopped | `t9c`, full-size files | index 3.0 min, **search stage 33.0 min** (features, exact search in 6 batches and the per-frame search side by side) | the PC became stuck and laggy during refine (log ends 00:20): the 33 min may be slower than it should be |

**Timing runs that must run again** (from empty caches, full-size `finished\` files, one at a time on a free,
healthy PC: `CODE=work/frozen/t9c TAG=after bash work/t9/time/run_after.sh`, after deleting `work/t9/time/STOP`):
video1, video2, video3 and video4, each thorough and `--fast` -- all eight. Then: the comparison of video2's new
edit with the Task 8 code's (the identity check from empty caches), check-all, the run-time table below, commit.


## In short

(pending: the timing runs above)

(pending)

## What changed -- each with the same results

1. **The exact nearest-neighbour search is 10 times faster** (`gpu.KnnIndex`). The RAW index is kept on the GPU in
   float16 (SIFT values are whole numbers 0-255, exact in float16) and multiplied on the tensor cores with float32
   sums: every product and partial sum is a whole number under 2^24, so every distance is exact, as before. The 24
   nearest are found in two stages (the two-stage search from Task 5's report): each block's distances in groups of
   64, the 24 groups with the smallest minimum -- they must hold the 24 nearest -- and only their members ranked, by
   distance, then index. The index takes half the GPU memory it did.
   - On real data: 100,000 SIFT descriptors of video1's RAW against its 10 M-descriptor index: 34-38 s instead of
     363 s, **0 differing rows** (indices and distances).
   - Its own exactness test (`tests/test_gpu.py`): against a brute force in whole numbers -- many equal distances, a
     last group and chunk cut short, k larger than a group, one chunk holding all 24 nearest in 24 different groups,
     the extremes of uint8. Checked to fail when one group too few is kept.
2. **The full-resolution scorer is 1.8 times faster, the same numbers bit for bit** (`fullres.Scorer`): each framing
   is sampled once (the score at it and the next Gauss-Newton step from it share the samples), a mask's pixels are
   found once, the pixel grid is kept, the 4x4 system comes to the CPU in one copy, and the start score is not
   computed twice. 45 -> 25 ms per refinement at video1's size. Test: the old algorithm, copied into
   `tests/test_fullres.py`, as the reference.
3. **The GPU stages run in 4 processes sharing the GPU** (`full_res_workers`): the full-resolution re-check, the
   segments stage's full-resolution requests and verify's 9.9 each did their frames one after the other in one
   process, which left the GPU mostly idle while it decoded frames, launched small kernels and waited on them. Each
   frame's numbers do not depend on the others, so they are now computed in 4 processes and the decisions are made in
   the same order from the same numbers. The segments stage asks for them beforehand (`SideScorer.prefetch`), keeping
   its "first start decides" rule for each frame's framing. Tests: the re-check in 3 processes decides exactly as in
   one; prefetched requests and verify's frames measured in GPU processes equal the ones computed in this process.
4. **A normal run no longer re-runs the segments stage just to check it is deterministic** (check 9.7): that re-run
   cost as long as the segments stage (13-17 min of video1's verify) and changes nothing in the edit. check-all still
   does it on every video (`--check-determinism`); a normal run compares itself with its previous run of the same
   inputs (PASS / FAIL), else says N/A.
5. **The exact search runs on the GPU while the CPU checks the frames before** (`visual_match.run_searches`): the
   competitor's frames are searched in 6 batches, and while the 16 CPU workers check one batch's candidates, a thread
   finds the next batch's neighbours on the GPU -- the GPU and the CPU were each idle while the other worked (video1:
   12.5 min of exact search, then about 20 min of checking). Each query's neighbours do not depend on the other
   queries searched with it, and every frame's check is seeded on its own, so the anchors are the same as in one
   batch (test: 6 batches against 1, the anchors and the rejected candidates equal).

(Pending: video2 from empty caches with the new code, compared with the Task 8 code's run above.)

## Your two questions

### Limiting the search to where the audio places the competitor (tried, removed)

I built it: the RAW index held only the RAW times the audio alignment places the competitor at, +-30 s (5-13 % of
your long RAWs); a frame the audio could not place, or whose picture was not there, was searched in the whole RAW.
On video2 its edit came out different: 25 segments instead of 23, several cut points and speeds changed. A fresh run
of the Task 8 code on video2 reproduces Task 8's edit exactly, so the whole difference was the regions'.

Why it cannot keep the results: each descriptor of a competitor frame votes for the RAW frames of its 24 nearest
neighbours, and the vote rule was made for the whole RAW -- a descriptor whose nearest match lies elsewhere in the
RAW does not vote at all. Only the whole RAW can tell where a descriptor's nearest match is; with the regions only,
the same descriptor votes for its best match inside them, the audio window gets more and noisier votes, and 4 of
video2's frames lost their true candidate that way. (24 of video2's 281 frames fell back to the whole RAW: 17 that
match nothing anywhere, 4 the regions missed, 1 near miss, 1 new anchor -- the details are in this report's history,
commit d8b8cd4.)

So I removed it. With item 1 the RAW's length costs little now anyway: the index of every frame of a 24-minute RAW
takes 2.5-3 minutes to build, and the exact search over it takes 12.5 minutes for video1's 3.4 M queries (it took about 113 before), now hidden behind the per-frame search (item 5).

### Moving the slow CPU stages to the GPU

I profiled them (one process, so the profile sees all of it):

| stage (video2) | where the time goes |
|---|---|
| the per-frame search (S5.2) | 89 % re-estimating each candidate's framing: a Gauss-Newton fit in numpy (72 %: least-squares solves 21 %, array arithmetic 30 %), OpenCV's ECC (11 %) |
| refine (S5.3) | 51 % scoring candidates (OpenCV warps and blurs, ZNCC sums in numpy), 35 % OpenCV's ECC |
| the RAW index | OpenCV's SIFT |

None of these can move to the GPU with identical results: OpenCV's warp rounds sample positions to 1/32 pixel in
fixed point and its filters and ECC sum in their own order, numpy's least squares use LAPACK's SVD; a GPU version
agrees to about 1e-10, not bit for bit, and these numbers are compared against thresholds. Nor would it clearly save
time: each fit is one frame's region at proxy size, which 16 CPU workers already process in parallel, and on the GPU
each pays kernel-launch and transfer overhead -- I estimate about 0.6 times the throughput of the 16 workers, unless
many frames are batched together, which means rewriting the search's per-frame logic.

What I did instead is items 3 and 5: the stages that already run on the GPU now keep it busy, and the GPU's exact search runs while the CPU workers check frames, instead of before them. One CPU-side change I tried
(building the Gauss-Newton arrays without `np.c_`) gave no measurable gain outside the profiler, so I took it out.

## Run times

(pending: the timing runs above)

## Tests

Unit suite on this code before item 5: 1,090 passed, 12 skipped, 1 failed -- the MKV/FLV probe test (it passes alone; it fails in the full suite even with nothing else running, so it is not only load: Task 10). After item 5: the search, refine and CLI test files, 112 passed. check-all: not run yet on this code.
