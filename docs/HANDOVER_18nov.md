# Handover -- 18 Nov 2026 (for a Claude Code session running on the PC, in this folder)

Read all of this before you change anything. It was written by the Claude session that worked on this project
through the remote app on 7-18 Nov. That session could not run the full tests or a real video; you can. The owner is
away for about 8 hours and wants to come back to a program that is cleaner, faster and more accurate, and that does
not stop.

## Who you work for

- One person (Gvidas). Windows. Not a native English speaker: write to him in plain, simple words, step by step, short.
  No jargon. When you need him, say exactly what to click or paste.
- Two computers, both named "Gvidas": the **PC** (Ryzen 9 9950X, RTX 5080, 96 GB; this one) and a **laptop**
  (RTX 4060 8 GB, 16 GB RAM). The PC shares `D:\Shared` (the laptop sees it as `\\192.168.101.11\Shared`, or
  `\\100.105.55.69\Shared` through Tailscale).
- He makes YouTube Shorts: the app recreates a competitor Short's cuts and captions from the original episode (the
  "RAW") for Premiere, enhances the picture with Topaz Video AI, and zips the project for his editor / a friend
  (Discord: every zip must be under 1 GB).

## Rules that are never broken (his words)

- "Never write the NexLev / Algrow keys anywhere." They live only in the env vars NEXLEV_API_KEY / ALGROW_API_KEY.
- "Never use torrent or pirate sites in the app."
- "Never use my channels' logins, cookies or AdsPower / Multilogin profiles."
- "Never delete my own projects, videos or fixed .prproj files." The app itself may remove a finished project FOLDER
  once its verified zip exists (he asked for that: "only the zip stays"). You, by hand, never delete his projects,
  videos, zips or Premiere files. If something should go, move it to a `_to_delete` folder next to it and list it.
- "NO RAW COULD BE IN THE TIMELINE EVERYTHING NEEDS TO BE IN TOPAZ ENHANCED."
- The order of one video, his decision on 17 Nov and final: **edit -> Topaz once (the edit's first clip to its last,
  10 s before and 10 s after) -> the enhanced picture into the edit -> zip.** Nothing before the edit, nothing twice.
  "just edit, then topaz 10 seconds before, 10 seconds after, and that's it. And upload it into zip file."
- Accurate edits, no small cuts, no flash frames.
- Do not ask him questions while he is away. Take the most reasonable reading, say which one you took, go on. The one
  exception: anything that cannot be undone and could go either way -- prepare it, describe it, leave it for him.

## The code

- `tools/app/mr_app/` -- the PySide6 desktop app (`MovieRecaps.pyw`). `core/` has the logic, `tools/` the pages,
  `ui/` the window. `tools/app/tests/` -- its tests (pytest).
- `tools/match_cuts/match_cuts/` -- the matching pipeline (stages S0..S10, `pipeline.py`), run by the app as
  `python -m match_cuts batch`. `tools/match_cuts/tests/` -- its tests.
- The style of this code base: every change carries a dated comment saying which real video showed the problem.
  Keep doing that. Commit messages are one plain sentence saying what changed for the user.
- Key files for the flow:
  - `mr_app/tools/download_tool.py` -- the "New video" rows (download, convert, template, enhance note, edit).
    `_enhance` now never cuts a piece before the edit (`AFTER_EDIT`).
  - `mr_app/core/flow.py` -- after the edit: `join_clips`, then `enhance_swap.cover_all` (cuts the Topaz piece from
    what the edit plays, +- `COVER_MARGIN_S` = 10 s), then `swap_in` when the piece is back.
  - `mr_app/core/enhance.py` -- pieces, the Enhance folder, locks, the Topaz worker (`Worker`, `enhance_one`,
    `run_topaz`), `watch` / `sweep` / `_resend` (takes results back, sends failed pieces again).
  - `mr_app/core/enhance_swap.py` -- `outside`, `cover_all`, `swap_in` (the finished edit plays the enhanced file;
    scale / position divided by how much bigger the enhanced picture is).
  - `mr_app/core/share_zip.py`, `archive.py`, `tidy_child.py` -- the zip (<= 950 MB, lighter copies), "only the
    zip stays", the clean-up round in its own low-priority process.
  - `match_cuts/excerpt.py` -- S1.5: the run analyses a short piece of the episode, not the whole episode.
    `KeepsPiece`: the 60 fps edit keeps playing that piece (moving it onto the movie was 1 frame off on 2-16 % of
    frames). `encode_piece`: a stretched (DVD, non-square pixel) RAW gets an encoded square-pixel piece.
  - `match_cuts/verify.py` -- S9, the final checks (slow: see the work list).

## What was changed on 17-18 Nov (commits 4514721..HEAD on branch claude/new-session-ol4era)

Read `git log --stat 589f98f..HEAD`. In short:
1. The laptop finds the PC at the home address or the Tailscale one (`machine.PC_ADDRESSES`).
2. Division by zero in refine (two framing keys on one frame); a stopped run logs its traceback in match_cuts.log.
3. Topaz twice: fixed three ways -- the first piece is found when the edit plays only the piece (`_piece_group`),
   then no piece before the edit at all, then `KeepsPiece`.
4. False "frame / watermark file does not exist" notes for network paths (`export_xml_edl._url_path`).
5. The computer stays awake while the app works (`core/awake.py`).
6. "Only the zip stays" was stuck: `archive.check_zip` now accepts the normal RAW left out on purpose.
7. The clean-up round runs in its own process (`tidy_child.py`), 3 minutes after the start.
8. Topaz output 60 Mb/s (was 180). 10 s margin. The bar no longer stands at 99 % after a pause.
9. New channel TOMSCUTS (posts The Office); SceneProof's template from a new PSD.
10. A stretched RAW gets its piece (encoded); cut pictures in small batches and only a warning (laptop out of memory).
11. 18 Nov: a 4K piece is not doubled to 8K (`topaz_scale`: Apollo only), a hanging Topaz is stopped after 8 min and
    tried again with less VRAM, a lock of this computer's own closed app is free at once, the row shows Topaz
    working after the edit.

## What is NOT verified -- do this first

That session could only run part of the tests (a Linux VM without PyAV / PySide6, an old ffprobe) and never a real
video. So:
1. Run the full suites here, in the project's own environment (`.venv`): `tools/app/tests` and
   `tools/match_cuts/tests`. Fix real failures. Some old tests still describe the flow from before 17 Nov (a piece
   BEFORE the edit: `test_enhance_run20.py` rows / whole flow, `test_speed_7nov`, anything expecting the "sent"
   state before the edit, `180M`, the 30 s tidy start): update those tests to the new, decided flow -- do not put
   the old flow back.
2. Run ONE real video end to end from the command line (`tools/app/steps.py` / `python -m match_cuts batch`, the
   fake Topaz switch `MOVIERECAPS_FAKE_TOPAZ=1` exists for tests) and check the order: edit -> one piece cut from the
   edit's clips +- 10 s -> enhanced -> swapped in ("N of N V1 clips play it") -> zip under 1 GB -> folder gone after
   the tidy round. Old finished projects with their RAWs are in `D:\Shared\Projects` (zips) and `C:\T` (episodes).
   Never work inside a project the app is using; copy what you need to a scratch folder under `work\`.
3. Real cases to try the new code paths on:
   - a stretched DVD RAW (480p, SAR != 1): `encode_piece` + `same_frames` must pass and the run must use the piece
     (17 Nov "The Worst Bedtime Routine In Its Always Sunny": the first version was 1 frame off and fell back to the
     whole 23-minute episode);
   - a 4K RAW (18 Nov "The Funniest Shaq Impression of All Time": 3840x2160, froze the laptop at 0 %): scale 1, the
     swap must not halve scale / position (`bigger` in `swap_in`), `check_enhanced` must accept the same size;
   - Topaz "code 69: Conversion failed!" on the laptop (17 Nov, Bedtime, a 720x540 piece; the second try worked):
     the reason was never seen. `_run_topaz_once` keeps the last stderr lines in `e.details` and the failure file --
     make sure the full reason reaches `<name>_ENHANCE_FAILED.txt` and the row.

## The work list (in this order; commit after each item, tests green each time)

1. The verification above.
2. **It must not stop.** Go through every way a row can end without finishing and without saying why: an exception in
   a background thread, a Topaz failure, the Enhance folder or the share going away mid-run, the app closed mid-step,
   out of memory on the laptop (16 GB: `spawn workers`, the verify step). Each must either recover by itself or say
   in plain words what to press. Known gaps: an edit marked FAIL (files there) never gets Topaz (`outside`,
   `swap_in`, `_resend` all want `PJ.DONE`) -- decide and implement: a FAIL whose 1_edit.xml exists should still be
   enhanced, he opens those in Premiere too. The Settings file: something writes stale settings back when the app
   closes (a setting changed on disk while the app ran was lost on 17 Nov) -- find it.
3. **Faster.** Measure first (the stage times are in every `batch_console.log`; history in eta.py). Known slow spots:
   - S9 verify: 1-2 min on the PC, **13 min on the laptop** for a 46 s Short with 244 segments;
   - S5.2 visual search / S5.3 refine took 3x longer than usual on some runs (17 Nov, "The Best Business Idea (2)":
     search 771 s, refine 14 min on the PC -- find out why: the clean-up round's zip encodes on the same GPU?);
   - a pause + resume starts the running stage again from zero;
   - Topaz is the long part (about 3 fps on the 5080 for 1080p -> 4K 60). Do NOT change his Topaz look (Proteus /
     Apollo settings) -- but do not enhance what is not played: for a Short whose clips are spread over minutes
     (Business Idea (2): 57 s of clips, a 3:24 piece) enhance only the played ranges (+10 s at the outer ends, a
     couple of seconds at inner gaps) and map them in `swap_in`. This is the biggest win; test it hard.
   Never trade accuracy for speed.
4. **More accurate.** Every "needs a look" note is a place the edit may be wrong. From the projects' logs the common
   ones are: flash frames (1-8 frames of another shot at a cut), "the RAW skips 33 ms at the cut: one take, cut"
   (a small cut inside one take -- he wants no small cuts), "the person speaking is not in the window" (framing),
   audio misaligned by tens of ms, and 244-segment edits on 29.97 fps DVD RAWs. Collect them from
   `D:\Shared\Projects\*.zip` reports / `problems_queue.md`, find the top causes, fix what can be fixed with a test
   that fails before and passes after. Do not loosen a check to make a note go away.
5. **Clean.** Remove code nothing calls any more -- most of all the old "piece before the edit" path and what only
   it used (`find_scene` use from the row, the `G_ENHANCE` gate, `use_late` / late pieces if truly unreachable now,
   `WHOLE_RAW_S`). Prove each removal with the tests and a grep. Keep the dated comments of code that stays.
6. **Space.** Measure: `work\` (caches), `input\downloads`, `output\`, `D:\Shared\Enhance` (old `_ENHANCED.mp4` of
   finished videos, about 8 GB, and `_to_delete`), `D:\Shared\Projects` (stale `*.tmp` / `.lock` zip leftovers),
   `D:\Shared\fix_files` and `D:\Shared\logs_for_claude` (made by the remote session, not needed any more),
   `.git\stale_old` (lock files that session had to rename because it could not delete: safe to delete). Delete only
   what the app can make again (caches, temp files, those helper folders). Everything else: a list with sizes in
   your report. Make the app clean the Enhance folder's used pieces itself.
7. Write your report at the end of this file: what changed, what you tested and how, what you left and why, what he
   must do (plain words).

## State of the two computers right now

- PC: this repo, branch `claude/new-session-ol4era`, everything committed except `tools/match_cuts/match_cuts/eta.py`
  (a small uncommitted change from an earlier session: MOVIERECAPS_NIGHT_LAB -- look at it, commit it if right).
  Not pushed since 70ba6dd: run `git push` when your first items are green. The app on the PC is closed.
  `%APPDATA%\MovieRecaps\settings.json`: Topaz on "pc", Enhance folder `D:\Shared\Enhance`.
- Laptop: pulled up to 70ba6dd, plus three files changed by hand that are identical to commit 54f9aee
  (`core/enhance.py`, `tools/download_tool.py`, `match_cuts/excerpt.py`) -- but NOT the 4K / hanging-Topaz part of
  enhance.py and enhance_swap.py. He must run on the laptop, with the app closed:
  `git checkout -- tools` then `git pull`. Put that in your report. Its settings: Topaz on "laptop", projects on its
  own disk (`input\downloads`), Enhance folder `\\192.168.101.11\Shared\Enhance`.
- Waiting in `D:\Shared\Enhance`: "The Funniest Shaq Impression of All Time_all2" (laptop, the 4K one, stale lock)
  and "The Best Business Idea In Key and Peele (2)_all" (PC, a 3:24 piece, about 1 hour of Topaz). They are his
  videos: leave them in the line.
- While you work, the MovieRecaps app may be opened by him on either computer. Never edit files of a project that
  is running; the code you change only takes effect when he restarts the app.

---

# Report -- 18 Nov 2026, the session on the PC (Claude Code)

For Gvidas. The short part first, the details after it.

## What you must do

1. **PC:** nothing. Open the app as always. It uses the new code from its next start.
2. **Laptop:** close the app. Open a terminal in the MovieRecaps folder and type these two lines, one after the other:
   - `git checkout -- tools`
   - `git pull`

   Then open the app again. Until you do this the laptop still works, but with the old code: it enhances the whole
   piece (slower) and does not have today's fixes.
3. That is all. If you want disk space back, read "Space" below -- I did not delete the big things, you decide.

## What is different for you now

1. **Edits are 60 fps again.** Since 17 Nov ("Topaz after the edit") the edits were quietly made at the RAW's
   rate, not 60. Fixed.
2. **Fewer "needs a look" notes.** The tool already knew how to repair flash frames and "the RAW skips 33 ms: one
   take, cut" -- but only for edits at the RAW's rate. In 60 fps edits (all your Topaz videos since 7 Nov) these
   repairs never ran. Now they run there too. Test video (The Best Business Idea (2)): **9 notes before, 2 after**.
   The repair is only kept when the edit's own checks are not worse; if they are, the edit stays exactly as before.
3. **Topaz does much less work.** It enhances only what the edit plays (10 s before the first clip, 10 s after the
   last, 2 s around each jump inside), not everything in between. Business Idea (2): 3 parts instead of the whole
   3:24 piece. The look (Proteus / Apollo settings) is not changed. Checked with the real Topaz on this PC: every
   part starts on exactly the right frame. If any part looks wrong, the app enhances the whole piece like before.
4. **Topaz gets the real RAW frames.** 18 of your last 26 pieces were first re-encoded (8-bit) because the cut had
   only 2 s of room to find a keyframe. Now the piece is cut from the RAW's own frames; the extra seconds are not
   enhanced.
5. **The last step of an edit is fast.** "Double-checking" took 8 minutes on the PC in my test (13 on the laptop).
   Most of it were checks that only fill report.md -- they never changed the edit and never decided PASS / OK with
   a note / FAIL. With Settings -> Check videos OFF (your setting) they are not run: **481 s -> 29 s**. Every check
   of 1_edit.xml still runs. Want them back? Settings -> Check videos -> On.
6. **The zip keeps the Topaz picture good.** Before, the zip also held up to 275 MB of check pictures and the
   unused RAW piece, and the Topaz picture was squeezed to fit 1 GB (7 Mb/s in my test). Now those stay out first
   (the test: 31 Mb/s, zip 790 MB). The zip is also no longer made twice (the second time encoded the picture
   again on the graphics card while the next video was running).
7. **A video does not stop without a word any more:**
   - an edit marked FAIL that has its 1_edit.xml still gets Topaz (no zip is made of a FAIL, nothing is removed);
   - a Topaz piece that failed and could not be sent again is sent again later by itself (at most 3 times);
   - if the Enhance folder is away for a moment (PC asleep, network), the video waits -- it is no longer "failed";
   - Topaz's failure reason is kept in the project: `logs\topaz_failed.log` (before, it was deleted after one line
     was read); the row shows the line that says why (not "Conversion failed!"); an unknown failure is tried once
     more before it counts;
   - if the app itself has an error around an edit, the row becomes FAIL with "press Try again" and the queue goes
     on (before: the row stayed "running" for ever). The reason goes to `%APPDATA%\MovieRecaps\crash.log`.
8. **Settings are not written back old.** Pressing OK in Settings wrote ALL settings as they were when the app
   opened. Now only what you changed is written, and the dialog shows what the file says now.
9. **The Enhance folder cleans itself** once a day: pieces older than 7 days that no video waits for. A finished
   `_ENHANCED.mp4` is never deleted -- it is moved to `Enhance\_to_delete` for you to look at.
10. **Removed from the app:** the old way (the edit waits for a Topaz piece guessed before the edit) and its
    switch "Match on the normal RAW" in Settings. It is always "edit, then Topaz, then zip" now.
11. **More videos work on a short piece of the episode, not the whole episode.** Many H.265 episodes store a few
    pictures in a way that made the tool's cut say "the piece has 6458 frames, the plan 6455". Then the whole
    21-minute episode was analysed (slow), stayed in the project and went into the zip. Fixed. Second test video
    (The Most Jealous Group Leader): its zip went from **906 MB to 487 MB**, and the edit is a clean PASS (on
    12 Nov it had a flash-frame note; the tool repaired that note by itself now).
12. **A Topaz result is no longer refused for a wrong reason.** When a piece had to be re-encoded, it could start
    one frame late; the app then said "Topaz's result is not right: its first frame is at 0.033 s" three times and
    left the normal RAW in the timeline. Fixed (found on the second test video, with the fake Topaz).

## What I tested, and how

- All tests: **app 1477 passed, tool 1694 passed** (at the start 19 + 5 failed; most were old tests that still
  expected Flick707, the History tab, the old template -- updated to how the app is now).
- **Two real videos end to end, several times each** (copies of The Best Business Idea (2) and The Most Jealous
  Group Leader, in a scratch folder, fake Topaz): edit (60 fps) -> ONE piece cut after the edit -> Topaz -> "55 of
  55 V1 clips play it" / "12 of 12" -> zip 790 MB / 487 MB -> clean-up round: only the zip is left, the Enhance
  folder is empty. The second video was a fresh analysis: **622 s** for the edit (on 12 Nov: 1433 s on this PC).
- **Real Topaz on this PC:** 3 parts of a test clip (each exactly its frames, each on its own first frame); a
  4K piece (8 s of the waiting Shaq piece, a copy): stays 3840x2160, 60 fps, accepted.
- **A stretched DVD episode** (It's Always Sunny S02E06, 720x480): the encoded piece shows exactly the movie's
  frames at 3 places.
- The scripts and logs are in `work\h18` (e2e.py, real_topaz_parts.py, real_cases.py, the log files).

## What I did NOT do, and why

- **Pause + resume still starts the running step again from zero.** Not touched: it needs changes deep in the tool
  and I chose the bigger wins first.
- **"Search / refine 3x slower on some runs" (17 Nov):** I could not repeat it. The likely reason is the zip's
  encode on the same graphics card while the next video ran -- that now happens once instead of twice, and often
  not at all (the Topaz file is shorter).
- **Notes that are left:** short shots of the competitor's own (8 frames, 0.13 s) and "the person speaking is not
  in the window". I did not loosen any check. They need your fixed Premiere projects to learn from.
- **The laptop was not tested** (I am on the PC). Its 16 GB problem in the last step should be gone, because the
  heavy checks no longer run -- please tell the next session if it is not.
- The full edit time on a free PC was not measured: my runs used the caches of your 17 Nov run and the tests ran
  at the same time (the edit: 718 s before the changes, 270 s after, same conditions).
- The real Topaz was only run on short test clips (minutes), not on a whole video of yours.

## Things I found that you should know

- `The Worst Prank In The Office`: its piece is stuck. Its json was moved to `D:\Shared\Enhance\_to_delete` on
  17 Nov (by the earlier session), so nobody can take it. If you still want that video enhanced: press Try again on
  it. I left it as it is.
- The two big zips of 17 Nov (Business Idea: 5.4 GB and 3.0 GB) were not made by the app (no "HOW TO OPEN" inside,
  both copies of the Topaz file in them). The app will make its own (under 1 GB) when it tidies those projects.
- 37 of the 175 zips in `D:\Shared\Projects` are over 1 GB. They are old (before 7 Nov) or made by hand.
- Two Topaz installs are on the PC. `Topaz Video\ffmpeg.exe` does not start; the app uses `Topaz Video AI` (works).

## Space (measured 18 Nov)

Deleted by me (the handover said they are safe): `D:\Shared\fix_files` (39 MB), `D:\Shared\logs_for_claude`,
`.git\stale_old`, and two dead zip leftovers in `D:\Shared\Projects` (1.1 GB: two `.light0.tmp` files and their
`.lock` files, left by an app that was closed in the middle of a zip).

NOT deleted -- you decide:

| Folder | Size | What it is |
|---|---|---|
| `work\t19` | 249 GB | test runs of an earlier night (run 19), on C:. Can be made again |
| `D:\mr_cache` (the same as `work\cache`) | 212 GB | the tool's caches. The app cleans old ones itself |
| `D:\mr_checkall` | 83 GB | test runs (check-all) |
| `D:\mr_t19`, `D:\MovieRecaps_scratch`, `D:\mr_scratch` | 18 + 15 + 10 GB | test scratch |
| `work\t31`, `t16`, `pt_tool_final`, `t17`, `t8`, `t18` | 32 GB together | older test scratch |
| `D:\Shared\Projects` | 276 GB | your projects and zips |
| `D:\Shared\Enhance` | 9 GB | 7.3 GB are 9 old `_ENHANCED.mp4` (30 Oct - 11 Nov): the app moves them to `_to_delete` by itself now |
| `D:\Shared\_to_delete` | 4.9 GB | zips moved there earlier |
| `D:\Shared\Enhance\_to_delete` | 0.9 GB | old pieces moved there earlier |
| `finished\`, `output\` | 3 GB, 1 GB | in the repo folder |

Free now: C: 110 GB, D: 1.7 TB. Deleting `work\t19` alone gives C: 249 GB back.

## For the next Claude session

- Commits of today: 88ceadc, bad066e, 09e8dcc, 9eee3f5, e1be4a9, ef08b90 (and this report), pushed.
  `docs/night15_prompt.md` is the owner's own uncommitted edit: left alone.
- `excerpt.copy_packets` now leaves out packets shown before the plan's keyframe and reads past the packets a seek
  lands on before it (tests: `test_excerpt_open_gop_18nov.py`); `enhance._encoded_piece` starts on a frame.
- Seen but not changed: a Topaz result that fails its check is sent again as the very same piece (up to 3 times).
  If the reason is in the piece itself, all three fail the same way -- a second try could switch to another way of
  making the piece.
- New pieces: `enhance_swap.play_ranges` / `segments`, `enhance.enhance_parts` / `valid_ranges` / `_span_plan` /
  `clean_folder`, `enhance_swap.edit_made`, `core/trouble.py`, `settings.save` (merge), `share_zip.without_check_pictures`
  / `_from_old_zip`, `--no-recreation-checks` (`verify.not_run_reason`), `pipeline.fix_at_plan_rate` with
  `write_premiere_xml(fix_plan_rate=True)`. Tests: `test_handover_18nov.py`, `test_topaz_parts_18nov.py`,
  `test_zip_18nov.py`, `test_fix60_18nov.py`.
- Switches: `MOVIERECAPS_TOPAZ_RANGES=0` (the whole piece again), `pipeline.PLAN_RATE_FIXES = False` (no fixes on
  a 60 fps sequence).
- Not verified on real data: check-all's scores (it runs at the RAW's rate, so the 60 fps fixes should not move
  them, but nobody ran it today); a real run on the laptop; a whole real video through the real Topaz with parts.
- Gone: `match_normal()`, `MOVIERECAPS_MATCH_NORMAL`, `G_ENHANCE`, `edit_raw`, `find_scene`, `piece_length`,
  `WHOLE_RAW_S`, `EXTRA_S`; `make_piece(raw, name, out_dir, span, ...)` takes the span only.
