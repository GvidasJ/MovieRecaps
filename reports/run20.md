# Run 20 (29 October, on the LAPTOP): enhance only the scene's piece, by itself

Started 00:05 on the laptop (you couldn't use the PC). C: had **47 GB free** at the start. The laptop is not shut down at
the end.

## In one minute

- **Built:** the PC cuts the scene's piece (2 minutes at most) and puts it in the Enhance folder; the laptop's app sees
  it, runs **Topaz Video AI** on it with your settings, and puts `<name>_ENHANCED.mp4` back; the PC checks it, copies
  it into the project and makes the edit with it. No answer in 1 hour (or a failure): the edit is made with the normal
  RAW and the row says why; a late piece is used only when you press **"Use the enhanced piece"**.
- **The two patches** from the PC (A1 left alone, no song) are in (`git am`: both applied cleanly).
- **Tested:** 21 new tests (fake Topaz) pass; the real piece and the real Topaz 3.4.3 work on this laptop.
  **Not finished:** the match-cuts comparison on the 60 fps piece -- stopped because the laptop ran out of memory
  (details below).
- **Speed:** this laptop needs about **1 h 45 min** for a 2-minute 1080p piece (1/50 of real time). See "What I
  decided".

## Step 1 -- the PC's two changes

`git am` of both patches worked (`d8c21d6` A1 left alone, `bf17aa7` no song). Then all tests, before any change of mine:

- **app: 1316 passed, 0 failed** (12 skipped) -- the two patches broke nothing.
- **tool: 1634 passed, 8 failed, 16 errors.** None of these come from the patches (they changed only app files): they
  are this laptop's own -- 16 `test_render_preview` tests crash in ffmpeg **9.0.2**'s `drawtext` ("Fontconfig error:
  Cannot load default config file", exit 0xC0000005; the PC has ffmpeg 8), and 8 worker-pool tests
  (`test_pools`, `test_visual_match`, `test_refine`) see their "spawn" workers run in the main process here. On the PC
  (run 19) all 1684 passed.

## What was built, and how you use it

### On the PC (nothing to do: it is on by itself)

1. You make a video as always (New video: links or files, channel, headline).
2. Right after the conversion, the row makes the **piece**: the quick sound check finds where the competitor's scene
   is in the RAW, and a piece of **at most 2 minutes** around it is cut (the scene in the middle, the rest margin),
   **lossless** -- the RAW's own packets from the keyframe before, its frames checked against the RAW at 3 places
   (as day 14's piece). Its sound is made AAC (Topaz's own ffmpeg cannot put the RAW's exact PCM sound in an mp4).
   It goes to **`D:\Shared\Enhance\<project>_TOENHANCE.mp4`** with **`<project>_TOENHANCE.json`** (where the piece
   starts in the RAW, its rate, length, size, which project; written last, so the laptop never takes half a piece).
3. The row shows **`● DOWNLOAD ─── ● CONVERT ─── ● TEMPLATE ─── ◉ ENHANCE ─── ○ EDIT ─── ○ DONE`**; its pill says
   "waiting for the laptop", and its line "The scene's piece is with the laptop (enhancing on the laptop · 34 % ·
   about 12 min left) -- the edit starts when it comes back". The template / headline go on meanwhile.
4. When **`<project>_ENHANCED.mp4`** appears, the PC checks it: the **same length** as the piece (within one of its
   frames), **60 fps**, **2x** its size, its first frames decode. Then it is copied into the project
   (`<project>\enhance\raw_enhanced.mp4` -- one copy; your downloaded RAW stays where it was, untouched), the
   `_TOENHANCE` / `_ENHANCED` files are deleted from the Enhance folder, and the edit starts by itself with the
   enhanced piece as its RAW. The step then says "enhanced on the laptop: 3840x2160, 60 fps".
5. **Not enhanced** -- the edit is made with the normal RAW and the row says why:
   - the scene is longer than 2 minutes (places far apart, a montage), or the sound check can't find it;
   - the RAW can't be cut losslessly (a variable frame rate, an mkv ...);
   - the laptop wrote `<project>_ENHANCE_FAILED.txt` (its reason in plain words, e.g. "the graphics card ran out of
     memory", "Topaz says you are not logged in");
   - the enhanced file is wrong (not 2x / not 60 fps / another length / doesn't decode);
   - **nothing came back within 1 hour**: "not enhanced: the laptop did not answer in 1 hour". If the piece comes
     later it is **not** used by itself: the row shows **"Use the enhanced piece"**, which checks it, copies it in and
     makes the edit again with it.
6. **Settings -> Enhance with Topaz -> "Enhance the scene on the laptop first"**: On by default on the PC (Off: no
   piece, the edit as before).

### On the laptop (nothing to do: it is on by itself)

- The app looks at `\\192.168.101.11\Shared\Enhance\` **every 30 s and when it starts**. A new `_TOENHANCE.mp4` that
  nobody has taken is taken (a lock file `<project>_TOENHANCE.lock`, made so that only one computer can make it; the
  laptop rewrites it every 5 s with its progress, and a lock nobody rewrote for 15 min -- the laptop was turned off --
  is free again).
- It runs **Topaz Video AI 3.4.3** from the command line -- Topaz's own `ffmpeg.exe` with its filters, found by itself
  in `C:\Program Files\Topaz Labs LLC\Topaz Video AI\`, its models in `C:\ProgramData\Topaz Labs LLC\Topaz Video
  AI\models` -- with your settings exactly:
  - **Proteus** (`prob-3`), Manual: revert compression **80**, improve detail **80**, sharpen **60**, reduce noise **80**,
    dehalo **10**, anti-alias/deblur **-60**, add noise **0**, recover original detail **10**; **2x** the piece's own size;
  - **Apollo** (`apo-8`) to **60 fps**, slow motion none, replace duplicate frames off;
  - no stabilization, no motion deblur, no grain;
  - **H.265 Main10, constant 180 Mb/s, mp4**, sound copied; the piece's start and length stay the same.
- It writes `<project>_ENHANCING.part.mp4`, checks it, and renames it **`<project>_ENHANCED.mp4`** (the PC never sees a
  half-written file). A failure writes `<project>_ENHANCE_FAILED.txt` (the reason, then Topaz's last lines).
- **Progress -> Enhance** shows "Enhancing <name> on this laptop · 34 % · about 12 min left", the last ones it did,
  the Enhance folder, whether Topaz was found, **Test Topaz** and **Look now**.
- **Settings -> Enhance with Topaz**: "Enhance pieces for the PC with Topaz" (On by default on the laptop, Off on the
  PC), the Topaz line ("Topaz Video AI found: ..." or why not, in amber), **Test Topaz** (2 seconds of a test clip:
  "Topaz works: 2 s of 960x540 at 23.976 fps became 1920x1080 at 60 fps (118 frames) in N s"), and the
  **Enhance folder** (empty = this computer's default: `D:\Shared\Enhance` on the PC,
  `\\192.168.101.11\Shared\Enhance` on the laptop).

## Test results

**App tests with the feature:** 1333 passed, 1 failed (`test_tabs` listed the pages; the new Enhance page was
missing from its list -- fixed), then the tab, settings and enhance tests again: 68 passed. **21 new tests**
(`tools/app/tests/test_enhance_run20.py`, all pass, fake Topaz):

- the piece: at most 2 minutes, the scene in the middle, keyframe to keyframe, inside the RAW at its start and end, a
  RAW under 2 min whole; a scene over 2 min, or keyframes too far apart: not enhanced, with the reason;
- **the whole flow, both sides in one test** (a 3:20 test movie + a 10 s competitor cut from it): the PC finds the
  scene by sound and makes the piece (frames SSIM 1.000 against the RAW); the laptop's worker takes it, fake Topaz,
  checks, renames; the PC checks it, copies it into the project, clears the Enhance folder, and the edit's job uses
  it as the RAW;
- the row in the window: `◉ ENHANCE`, "waiting for the laptop", the edit starts by itself when the piece is back;
- **the 1-hour fallback** (the clock moved on): the edit starts with the normal RAW, "not enhanced: the laptop did not
  answer in 1 hour"; a late piece is not used, the row offers "Use the enhanced piece", which makes the edit again
  with it;
- the laptop's failure file gives the row's reason; Topaz missing: the failure file says so;
- **a broken enhanced file** (not 2x / not 60 fps / too short / junk): not used, the normal RAW;
- **two computers taking the same piece**: 8 at once, exactly one gets it; a lock left by a stopped computer is taken
  over after 15 min; a piece without its json (still being written) or with a result is never taken;
- the exact Topaz command (every number of your settings), Test Topaz, "Topaz not installed", the switches' defaults
  per computer, the folder setting, the row keeping its Enhance step after a restart.

**On this laptop, for real:**

- **The real piece** from your Office Flirt project (Fletch Lives, 1:35:01, 1080p; only read, the piece written to
  `C:\T\enhance_test`): the scene found by sound at 7:11-7:21, piece **6:15-8:13 (1:59, 2849 frames)** cut in
  **16.5 s**, its frames identical to the film's at 3 places (SSIM 1.000).
- **The real Topaz 3.4.3** on an 18 s piece of it (7:09-7:27, 1920x1040): **3840x2080, 60 fps, H.265 Main10**,
  1082 frames = 18.03 s (the piece: 18.06 s -- within one frame; Apollo stops at the last real frame), in
  **15.7 minutes**. On a 2 s 960x540 clip: 1920x1080, 60 fps, 118 frames. So the command and the settings work.
- Topaz uses about **5 GB of RAM** and 4.2 of the 8 GB on the graphics card, at 100 %.

**Not finished (stopped for lack of memory):** a second real Topaz run *through the app's worker* (it had reached
29 %, with "about 12 min left" shown correctly) and **the match-cuts comparison** (the edit on the normal RAW vs. on
the 60 fps 2x piece). I ran them at the same time; Claude Code stopped both because the laptop ran critically low on
memory (16 GB: Topaz ~5 GB + a match-cuts run + the session), and said not to start them again by myself. The first
worker try had ended after 19 s with "the graphics card ran out of memory" (while a test suite ran next to it); the
same command run alone worked -- so on the laptop **Topaz should run alone**. The 2-minute 60 fps 2x piece made with
the fake Topaz from the real piece worked (1:04, 3840x2080, 60 fps); the cut comparison on it is what remains
(`work\run20\compare_cuts.py` is ready for it). My test files are deleted (75 MB of pieces kept in
`C:\T\enhance_test` for the re-run); C: had **47 GB free** at the end, as at the start.

## What I decided (you couldn't answer)

- **The 1-hour rule and a slow laptop:** at 1/50 of real time, a 2-minute 1080p piece takes ~1 h 45 min, so a strict
  hour would never use it. The edit waits past 1 hour **only while the laptop is visibly still working on that piece**
  (its lock rewritten every 5 s; the row shows its % and "over 1 hour, still working"), up to 4 hours. A laptop that
  never took the piece, or stopped, still means the normal RAW after 1 hour. (A 720p RAW is about 4x faster.) One
  number to change for a strict hour: `MAX_WAIT_S` in `core\enhance.py`.
- **The piece's sound is AAC 320 kb/s**, not the RAW's PCM: Topaz's ffmpeg refuses PCM in an mp4 ("codec not
  currently supported in container"). The picture is still the RAW's own packets.
- **Your downloaded RAW stays** in the project folder untouched (I never delete your videos); the enhanced piece is
  `enhance\raw_enhanced.mp4` and project.json says the edit uses it. The edit then plays the 2-minute piece, not the
  whole film.
- **Filter order:** Proteus first, then Apollo (the enhanced frames are interpolated; Proteus works on 2.5x fewer
  frames: faster). Graphics card 0, one extra model instance, NVENC preset p5.
- The piece is made right after the conversion (the laptop starts while you type the headline); the step is shown
  just before EDIT on the row.
- The app's tests switch both enhance switches off unless a test turns them on (`MOVIERECAPS_ENHANCE=0`), so no old
  test makes pieces on the PC.

## Laptop steps, and how to test the real Topaz

On the **PC**, when you are back: `git pull --rebase`, then `.venv\Scripts\python tools\app\setup_app.py` (the PC's
two local commits are the same as the patches, so the rebase drops them).

On the **laptop** (it already has this code): `git pull`, then `.venv\Scripts\python tools\app\setup_app.py`.

1. Open MovieRecaps, **Settings -> Enhance with Topaz**: "Enhance pieces for the PC with Topaz" = **On**; the line
   says "Topaz Video AI found: C:\Program Files\Topaz Labs LLC\Topaz Video AI". Press **Test Topaz**: after about a
   minute it turns green: "Topaz works: 2 s of 960x540 at 23.976 fps became 1920x1080 at 60 fps (118 frames) ...".
   Red = the reason (for example: log in to Topaz once).
2. **Close other big programs** on the laptop (Topaz needs ~5 GB of RAM and most of the graphics card).
3. On the PC: make one real video (a 720p RAW is best for the first try). Its row: `◉ ENHANCE`, "waiting for the
   laptop". Within 30 s the laptop's **Progress -> Enhance** says "Enhancing <name> on this laptop · 3 % · about 40
   min left", and the PC's row shows the same %.
4. When it is done: the PC's row says "enhanced on the laptop: 2560x1440, 60 fps" (for 720p) and the edit starts by
   itself; `D:\Shared\Enhance` is empty again. Open it in Premiere: a 60 fps sequence, a sharper picture.

## Not done yet

- The match-cuts run on a 60 fps 2x piece vs. the normal RAW (same cuts within 1 frame?) -- stopped for memory; it
  must run alone (~15-20 min on the laptop). Until then it is **not checked** that the tool's 1_edit.xml makes a
  60 fps sequence from a 60 fps RAW and fits a 3840x2080 picture in the 2160x3840 frame.
- One real Topaz run through the app's worker from start to end (the command itself is proven, the worker's progress
  too).
- "Test Topaz" pressed in the real window (its code ran in the tests with the fake Topaz; the real command by hand).
