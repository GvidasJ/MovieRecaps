# Task 9: speed

## In short

- **A normal run now takes** (your `finished\` videos at full size, from empty caches, on a free GPU with nothing
  else running, all 16 cores; the code frozen at commit 1a022dc):

  | video | thorough (the default) | `--fast` | before (Task 8's code, thorough, the same files) |
  |---|---|---|---|
  | video1 | **70.0 min** | **25.7 min** | not measured to the end: about 3.8 h in Task 8 (see *Run times*) |
  | video2 | **14.6 min** | **5.3 min** | **17.2 min** |
  | video3 | **14.4 min** | **5.9 min** | not measured |
  | video4 | **46.5 min** | **19.2 min** | **57.3 min** |

- **Under 25 minutes:** video2 and video3 in thorough mode, and all four with `--fast` (video1 at 25.7 minutes, at
  the limit). **Not under 25 minutes in thorough mode: video1 (70 min) and video4 (46.5 min).** video1's competitor
  is 56 s at 60 fps -- 3,384 frames, every one searched in a 23.5-minute RAW and refined; video4's RAW is 12.7
  minutes of 1280x720 at 59.94 fps -- 45,735 frames to index, and every full-resolution step costs three times what
  it costs on the other videos' RAWs of about 640x480.
- **My recommendation: keep thorough as the default**: you asked in Task 5 for the best result by default. Use
  `--fast` on a video like video1 or video4 when you need it within 25 minutes. The difference is real but small.
  Thorough has fewer clips with their audio out of sync on all four videos and a closer picture on video2 and video4,
  and only thorough checks every frame and cut at full resolution. Against your own finished edits the two modes
  come out about even. The details are in *Thorough or `--fast`* below.
- **The results did not change.** On the same full-size files, the Task 8 code and the Task 9 code give the identical
  cut list and the identical `1_edit.xml` for video2 and video4. In check-all, all eight test videos give the cut list
  of Task 8's final runs, pass every hard check, and score the same: 73 of 292 captions word for word, 12 of your 78
  cuts reproduced.
- **One part got slower:** verify's full-resolution check of every frame (9.9) on video4: 57 seconds in one process
  before, 236 seconds in the four GPU processes now. The four processes needed nearly all of the GPU's memory
  (15.4 of 16.3 GB) on video4's 1280x720 RAW. On the small RAWs they are a little faster (video2: 26 s -> 21 s), and
  on check-all's smaller copy of video4 (the same 1280x720) the same check took 52 s at 15.1 GB, without the GPU
  borrowing memory. So the step slows down when the card gets this close to full. The run as a whole is still faster,
  57.3 -> 46.5 minutes; Task 10 lowers that memory (see `reports/review.md`).
- Found on the way: video4's thorough run on the full-size files put two flash frames into `1_edit.xml` (1 and 4
  frames of other RAW moments, and the hard check failed). The cause is a bug in the B-roll step that Task 8 added,
  not this task's code: the Task 8 code puts the same two flash frames there. It is fixed in Task 10 (see
  `reports/review.md`).

## Run times

Every run: the full-size files from `finished\`, empty caches, one run at a time, a free GPU, an idle PC (16 cores,
Adobe closed). Started 6 Oct 07:23 (after the third interruption), finished 10:45. Minutes per stage:

| run | in all | start (probe, audio, proxies, layout) | search | refine | re-check | segments | exports | captions | verify |
|---|---|---|---|---|---|---|---|---|---|
| video1 thorough | **70.1** | 0.7 | 19.4 | 17.9 | 12.0 | 9.0 | 2.1 | 0.7 | 8.2 |
| video1 `--fast` | **25.7** | 0.6 | 3.3 | 11.5 | -- | 1.8 | 1.9 | 0.7 | 5.8 |
| video2 thorough | **14.6** | 0.5 | 7.6 | 1.8 | 1.1 | 1.5 | 0.4 | 0.2 | 1.4 |
| video2 `--fast` | **5.3** | 0.5 | 1.5 | 1.2 | -- | 0.4 | 0.4 | 0.2 | 1.0 |
| video3 thorough | **14.4** | 0.6 | 6.6 | 2.0 | 1.5 | 1.3 | 1.1 | 0.3 | 0.9 |
| video3 `--fast` | **5.9** | 0.6 | 0.9 | 2.0 | -- | 0.4 | 1.1 | 0.3 | 0.6 |
| video4 thorough | **46.5** | 1.3 | 14.2 | 12.4 | 4.1 | 4.9 | 1.2 | 0.5 | 8.0 |
| video4 `--fast` | **19.2** | 1.3 | 3.8 | 8.2 | -- | 0.7 | 1.2 | 0.5 | 3.6 |

The videos: video1 a 23.5-min RAW (700x480, 25 fps) and 3,384 competitor frames; video2 24.4 min (624x480) and 281
frames; video3 24.2 min (640x480) and 1,249 frames; video4 12.7 min (1280x720, 59.94 fps) and 1,078 frames.

- The thorough search stage is mostly the index of every RAW frame and the exact search over it: 6.6-7.6 minutes
  even for video2's 281 frames, because the RAWs are 24 minutes long.
- GPU memory peaked at 12.5 GB of 16 on video1 and at **15.4 GB** on video4, in verify's full-resolution check with
  4 processes on its 1280x720 RAW. That check also took four times as long there as in one process (*In short*).
  When the GPU's own memory runs out, Windows lends it some of the PC's memory, which is much slower but gives the
  same numbers. That is the likely cause. On check-all's Deadpool (a 1080p RAW) the card did run out: Windows lent
  it up to 3.1 GB of the PC's memory during the full-resolution steps. Task 10 fixes both: the run gives back its
  speech models before these steps, and each process's frame caches are bounded in bytes.
- **Before** (the Task 8 code), measured the same way, on the same files, from empty caches:
  - video2: **17.2 minutes**, against 14.6 now. With only 281 competitor frames, the exact search is a small part of
    video2's run: building the index of its 24-minute RAW and the per-frame search take most of it. The gain there
    is the determinism re-run a normal run no longer does (1.6 min), the faster re-check and verify's GPU processes.
  - video4: **57.3 minutes**, against 46.5 now: search 20.0 -> 14.2, refine 13.4 -> 12.4, re-check 4.6 -> 4.1,
    segments 5.7 -> 4.9, verify 10.6 -> 8.0. The old verify included the 5.6-minute determinism re-run; without it,
    verify got slower, because its full-resolution check took 236 seconds instead of 57 (*In short*).
  - video1 was not run to the end with the old code, because it takes hours. Task 8's own run took about 3.8 hours
    (a search stage of 132 minutes), but other runs and Adobe shared the PC then, so that number is too high. The
    comparisons I could make cleanly for video1: the old exact search was still running after 21 minutes, where the
    whole new search stage (the index, the exact search and the per-frame search) takes 19.4. The old determinism
    re-run cost 13-17 minutes of verify, and a normal run no longer does it (item 4 below).
  - video3 was not measured with the old code.
- From one run to the next the same stage can take a few minutes more or less: video1's search stage took 24.8
  minutes in the stopped run before this batch, 19.4 in this one, on the same code and files.
- What would still save time: verify is 5.8 of the 25.7 minutes of video1's `--fast` run, and 3.6 minutes of that
  is one check (9.2c, which fits each clip's framing again). I did not change it in this task, because every change
  would need all eight timing runs again.

## Thorough or `--fast`: the trade-off

`--fast` takes 2.4 to 2.8 times less time. What it gives up, measured on the same eight runs (competitor = how close
`1_edit.xml` is to the competitor video; your edit = the answer keys `learn` made from your finished videos):

| | video1 | video2 | video3 | video4 |
|---|---|---|---|---|
| time, thorough / fast | 70.0 / 25.7 min | 14.6 / 5.3 | 14.4 / 5.9 | 46.5 / 19.2 |
| competitor: frames matched, median similarity | 3,376 / 3,376, 0.985 / 0.985 | 281 / 281, **0.962 / 0.952** | 695 / 771, 0.828 / 0.829 | **748 / 718**, **0.994 / 0.990** |
| competitor: frames below 0.9 similarity | 1 / 1 | **3 / 8** | (most of video3 is not in the RAW) | 58 / 58 |
| competitor: cuts verified on both sides | 25 of 25 / 25 of 28 | 9 of 23 / 6 of 19 | 16 of 21 / 17 of 22 | 19 of 40 / 20 of 30 |
| competitor: clips with their sound off the picture | **0 / 1** (15 ms) | **0 / 1** (128 ms) | **4 / 7** (about 0.5 s) | **0 / 1** (21 ms) |
| your edit: captions word for word | 10 / 8 of 111 | -- | -- | 4 / 6 of 38 |
| your edit: your cuts reproduced | 5 / 8 of 29 | 1 / 1 of 1 | 0 / 0 of 11 | 2 / 2 of 7 |
| full-resolution check of every frame and cut (9.9) | yes / no | yes / no | yes / no | yes / no |

- **Audio:** on every video `--fast` has more clips whose sound is measurably off their picture.
  - video1: 15 ms and video4: 21 ms (about a frame, hard to hear), where thorough has none.
  - video2: 128 ms, a lip-sync error you could notice, where thorough has none.
  - video3: clips about half a second off in both modes, 7 in `--fast` against 4 in thorough.
- **Picture:** thorough is closer on video2 (median similarity 0.962 against 0.952, 3 frames below 0.9 against 8)
  and on video4 (0.994 against 0.990, 30 more frames matched). video1 is even, and video3 is too, since most of it is
  not in the RAW at all.
- **Cuts:** mixed. On video1 thorough verifies all 25 of its cuts on both sides, `--fast` 25 of 28. On video4
  thorough splits the edit into more pieces (40 cuts against 30), and more of its cuts cannot be verified on both
  sides (21 against 10).
- **Your own finished edits:** about even. `--fast` reproduced more of your video1 cuts (8 against 5 of 29) and
  more of video4's captions word for word (6 against 4 of 38). Thorough got more of video1's captions (10 against 8
  of 111). Your finished videos are your own edit of the RAW, not the competitor's, so these numbers say little about
  which mode matches the competitor better.
- The two modes often cut a frame or two apart. Only 3 of video2's 23 cut points are on the same frame in both (7
  within 2 frames); on video1 it is 23 of 25.

So **I recommend keeping thorough as the default**, as you asked in Task 5 for the best result by default. It is
somewhat closer to the competitor, and it checks itself frame by frame at full resolution. Its advantage is modest,
though. For a video like video1 (thousands of competitor frames) or video4 (a long HD RAW), `--fast` is a
reasonable choice when you need it within 25 minutes. Then look at its audio sync and its cuts a little more closely
in Premiere.

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

## The same results?

Each change was built to give exactly the same numbers (see above). I also checked the whole tool end to end: the
Task 8 code and the Task 9 code on the same full-size files, from empty caches, one after the other on the idle PC.

- **video2: the same edit.** The cut list is identical (24 segments), and so is `1_edit.xml` (file paths and names
  aside). Both runs have the same full-resolution note (9.9: the cut at frame 178), so that note comes from the
  full-size files, not from this task. Task 8's run of the smaller test-case copy did not have it.
- **video4: the same edit**, cut list and `1_edit.xml` identical (41 segments). Both runs also have the same
  two flash frames that fail the export's hard check (1 and 4 frames of other RAW moments under S08's speech). So
  they are not from this task: they come from a bug in the B-roll step that Task 8 added, which the full-size files
  expose and the test-case copy did not. Task 10 fixes it.
- **check-all** (all eight test videos, the test-case copies, from empty caches) against Task 8's final runs: **the
  cut list is identical on all eight**, and so are the scores (captions word for word: deadpool 47/60,
  spiderman-school 10/64, video1 7/111, video4 8/38, zendaya-age 1/19; your cuts reproduced: spiderman-school 2/21,
  video1 6/29, video2 1/1, video3 0/11, video4 2/7, zendaya-age 1/9). Every hard check passes on all eight; the
  full-resolution notes (9.9) are the same as in Task 8's runs (video3 and video4 fail it there too). check-all ran
  in three parts, because you needed the PC for a render twice:
  - deadpool, spiderman-school and video1: on a free PC (6 Oct 13:36-16:36);
  - video2 and video3: while Adobe Media Encoder and After Effects were rendering (19:40-20:40; After Effects used
    about 5 cores and Media Encoder 2 the whole time, and the GPU's memory was full and borrowing 5-10 GB from the
    PC's). Their run times say nothing; their results are identical, and no GPU step fell back in their logs;
  - video4, zendaya and zendaya-age: on a free PC again (7 Oct 06:19-08:04).
  The combined scorecard: `work/t9/check-all/scorecard_all.txt`.

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
takes 2.5-3 minutes to build, and the exact search over it takes 12.5 minutes for video1's 3.4 M queries (it took
about 113 before), now hidden behind the per-frame search (item 5).

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

What I did instead is items 3 and 5: the stages that already run on the GPU now keep it busy, and the GPU's exact
search runs while the CPU workers check frames, instead of before them. One CPU-side change I tried (building the
Gauss-Newton arrays without `np.c_`) gave no measurable gain outside the profiler, so I took it out.

## The interruptions

The timing batch had to start from the beginning four times: two PC restarts, the PC needed for other work, and an
accidental shutdown on 6 Oct. After each one I deleted the half-finished run folders and their partial caches, so no
half-written file could feed a later run. Only this last batch (07:23-10:45 on 6 Oct) counts. All eight of its runs
finished on an idle PC.

## Tests

- **Unit suite** on the Task 9 code (a fresh checkout of commit 1a022dc): **1,092 passed, 12 skipped, none failed**.
  The first run had 11 failures. All were the caption tests that compare the reference subtitle files in `srt/`
  byte for byte: git's Windows default rewrote those files' line ends in the fresh checkout. With the files as they
  are stored, all 11 pass. A fresh clone on Windows would hit the same, so Task 10 tells git not to convert them.
  The skips are the usual ones: Linux's `fork` and `/proc` (6), OpenTimelineIO (2), plan hashes recorded with Linux's
  fonts (2), a POSIX shell fake and an ffmpeg without `flite`. The "flaky" probe test passed this time (Task 10
  fixes its cause).
- **Slow suite** (the whole tool on synthetic video): **21 passed, 63 skipped, none failed**, once one test was
  fixed. That test checks that a run's cut list re-assembles identically from its caches (check 9.7). Since this
  task a normal run no longer does that re-assembly (item 4), so the test now asks for it with
  `--check-determinism`, as check-all does. On the Task 9 code: 16 passed, 62 skipped (opt-in film24 checks).
- **The same edits as the Task 8 code**: video2 and video4 at full size, identical (*The same results?*).
- **check-all** (thorough, with the determinism re-run and the `--fast` comparison, on the frozen Task 9 code): all
  eight test videos pass every hard check, with the cut lists and scores of Task 8's final runs (*The same results?*).
