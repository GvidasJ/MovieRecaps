# Night 11 — MovieRecaps (14 hours, PC)

You have the whole night. I'm away and can't answer questions or approve anything. Never stop to ask me. When something is unclear, choose the safest sensible option, write it down in the report, and keep going.

Repo: `C:\Users\gvida\Desktop\bh\MovieRecaps`. Branch: `claude/new-session-ol4era`.
Visual reference for the app's look: `docs\ui_preview.html`. It's an HTML mock-up, so open it and read its CSS and JS. The real app is PySide6/Qt (`tools\app\MovieRecaps.pyw`), so rebuild the same look and motion in Qt. Do not embed a web page.

## 0. Start of the night (do first)

1. Run `shutdown /a`, then `shutdown /s /t 54000`. That's a 15-hour backup, so the PC turns off even if you get stuck.
2. Read `reports\night10.md` so you know where things stand.
3. Make a task list from the sections below and work **in this order**. If time runs short, the top items matter most.
4. Commit and push after every finished section, with a clear message. Never leave the repo broken.
5. Keep all existing tests passing. Add tests for new logic.

## 1. RAW / competitor: names first, wrong pair caught fast

- **Names win over length.** A file named `comp*` or `competitor*` is the competitor. A file named `raw*` is the RAW. Use length only when the names don't say.
- If the names don't say and the two lengths are within about 20% of each other, **don't fail**. In the app, show a small question card: "Which one is the RAW?" with the two file names to click. In batch or command-line mode, write a clear note and skip that video.
- **Quick wrong-pair check (seconds, not minutes).** Before the long run, sample a few frames from the competitor and look for them in the RAW (fast, low resolution). If almost nothing matches, stop that video **right away** and show a red **WRONG PAIR** pill with the text "This competitor doesn't seem to come from this RAW". Do not spend 20 minutes and then say 0%.
- Fix the bug where, after I add the RAW and competitor from somewhere other than the `input` folder, the RAW is missing from the media. Both files must always end up in the project.

## 2. Changeable save folder

- Add a **Settings** option "Save projects to". Defaults: PC `D:\Shared\Projects`, laptop `\\192.168.101.11\Shared\Projects` (detect the machine by its name; let me change the folder in Settings).
- Only finished projects go there: `<project>\edit`, `template.png`, `project.json`, `logs`, `title.txt`. Working files and caches stay on the local disk (fast).
- Media copy: use a hard link on the same drive, otherwise copy. UNC paths must work, and never `cd` into a UNC path from cmd.
- If the shared folder isn't reachable, save locally, show an amber note, and move the project to the shared folder later when it comes back.

## 3. Simpler Learn tab

- I should only have to **drop the fixed `.prproj`**. The app finds the matching run by itself (name, project.json, media inside the prproj).
- If I drop a project **folder**, use its `edit` subfolder automatically.
- Watch for a new `fixed.prproj` (or `*_fixed.prproj`) appearing in a project and offer "Learn from this?".
- Reset the Learn form completely on each new drop, so the old output folder never sticks.
- Clear errors in plain words, e.g. "I can't find the run for this project — pick it here".
- Auto names: the next free name `video13`, `video14`, … (check what already exists).
- A **check card** before learning shows what was found: RAW, competitor, the tool's version, my fixed version, and what differs (captions / cuts / position).
- A **Standing rules** box with rules that always apply, saved in a file.
- A **Wishes** box that saves to `requests.md`, for my ideas for future nights.
- `learn_queue.md` keeps a list of projects waiting to be learned.
- A **"Copy tonight's prompt"** button that builds a ready-to-paste Claude Code prompt from `learn_queue.md` + `requests.md`.

## 4. Learn from video11, video12 and chocolate

- Find them through `learn_queue.md` and the Learn drops, or search `D:\Shared` and `D:\Shared\Projects` (chocolate was moved there from the laptop). If one is missing, write that in the report and continue.
- Learn **only**: captions (words, timing, look), cuts, and video position/scale.
- **Ignore:** the headline PNG (it's the same in all videos), memes, music, all audio, pitch, and my trims at cut points and at the ending (I do those by hand).
- Re-run those three after the changes and report how close the tool now gets to my fixed versions (before → after numbers).

## 5. Audio chain on every audio clip

This replaces the old rule "the tool never adjusts audio". Put these effects on **every** audio clip in the output, **in this exact order**:

1. Audio Gain: **Normalize All Peaks to 0 dB**
2. **Volume 0 dB**
3. **Hard Limiter**: Maximum Amplitude **0 dB**, Input Boost **0 dB**, Look-ahead **7 ms**, Release **100 ms**, Link Channels **1.00**
4. **Amplify +4.5 dB**
5. **Panner 0** (center)

Keep this order even though Amplify after the limiter can push peaks above 0 dB. Mention it once in the report. Check in a real Premiere import that the effects appear with these exact values.

## 6. Project board (A to Z)

- Columns: **Link → Downloaded → Converted → Template → Edit made → Fixed → Rendered → Uploaded**.
- Cards are **draggable** between columns, with smooth movement and a drop slot.
- Each card shows the channel color dot, the name, and the **date and time of every step** (e.g. "Downloaded 18 Oct 02:44").
- Each card keeps its **video title**, with a **Copy** button and a `title.txt` in the project. I forget titles, so show the title everywhere the project appears.
- A ★ **pin** on a card keeps it at the top of its column.
- PC and laptop both use the board from the shared folder at the same time. Use lock files so two machines never write the same project at once. A stale lock (older than about 2 hours with no heartbeat) can be taken over.
- A **Clean up** button removes finished junk (temp files, old caches) from projects, but it asks first, shows the size, and never deletes `edit`, `template.png`, `project.json` or `title.txt`.
- Above the board, a small **weekly chart per channel**: 7 bars (Mon–Sun) of uploads per day, in the channel color, with "N this week".

## 7. Automatic "Uploaded" check

- Every 30 minutes (and at app start), read the **Shorts** tabs only with yt-dlp (flat playlist, quick):
  - https://www.youtube.com/@LouLaughs/shorts
  - https://www.youtube.com/@MikeMemesYou/shorts
  - https://www.youtube.com/@Flick707/shorts
- Match the Short titles to the board's saved titles (ignore emoji, case and punctuation). An exact match moves the card to **Uploaded** with the date. A near match **asks first** with a small card, "Is this the same video?", and Yes/No.
- No network → skip quietly and try again later.

## 8. hard_cases folder

Run every case in the `hard_cases` folder. Fix what you can and add each as a test. For anything still failing, write in the report what's wrong and what would fix it.

## 9. Look and motion (match `docs\ui_preview.html`)

**Brand**
- New "match cut" logo: a play triangle split diagonally, left half white, right half red and slightly offset, inside red viewfinder corners. Use it for the app icon too.
- Name written as **Movie** (white) **Recaps** (red).
- Colors: dark background, red `#e5484d`, coral accent `#ff7a59`. Channel dots: LouLaughs amber `#f0a84a`, MikeMemesYou red `#e5484d`, Flick707 violet `#a78bfa`. "TITLE" labels in coral.
- Section titles in Bebas Neue (bundle the font).

**Hover rules (very important)**
- Hovers only **fade**. Nothing moves, grows or jumps.
- Fade in about **0.3 s**, fade out about **0.45–0.5 s**, curve cubic-bezier(.4,0,.2,1) (Qt: `QEasingCurve.BezierSpline`, or OutCubic as the closest).
- Queue rows on hover: a red left edge (its own fading layer), a dark red border, a slightly warmer background, a faint glow, and the title box border lights up. Plus a very faint warm light sweep (about 3% reddish).
- Buttons: a red fill sweeps in from the left. Primary buttons use a red→coral gradient.
- Viewfinder corner brackets fade in on board cards on hover.
- Drop zone: clean, **no glow and no sweep**.
- Animations must work even when Windows "Animation effects" is off: use your own QPropertyAnimation / QVariantAnimation, not system effects.
- The window must **never freeze**. All work runs off the UI thread.
- Add a Settings switch "Animations on/off".

**Tabs**
- Tabs: **Match cuts · Board · Progress · History**.
- Tab hover is about 0.45 s. A red underline grows from the middle of the active tab. No sliding line.
- Switching pages: the new page glides in from the side of the tab you clicked, in about 0.45 s, with no blur.

**Progress and status**
- Progress bars glide every frame and never jump, including the jump to 100% at the end, with a soft shimmer while running.
- Status pills, tinted: grey waiting, blue running (with a pulsing dot), green PASS, amber note, red FAIL / PICTURE NOT FOUND / WRONG PAIR.
- The running row gets a slowly circling red border ("living border").
- When a video finishes: a short green "That's a wrap" sweep over the row plus a toast.
- Board columns get thin colored top lines.

**Cinematic extras (keep)**
- Intro animation under 1.3 s at app start: the corners draw, the two triangle halves slide together, and the name fades in, **all centered**. Replay it from Settings.
- Slate line on each queue row: `SCENE 01 · TAKE 1 · date`.
- A timecode clock in the top status bar.
- A very soft vignette.
- **Dates on everything**: queue rows, cards, history, logs.

**Motivation strip (under the quote, on the Match cuts tab)**
- **Today**: a ring split into N pieces (daily goal, default 6). Each Short uploaded today fills one piece in its channel's color, with a big number "4 / 6", text "2 more to hit today's goal", and a per-channel split "2 Lou · 1 Mike · 1 Flick707". When the goal is hit, the box gets a green border and a sweep, plus a toast.
- **This month**: a big number "87 / 150 Shorts" (monthly goal, default 150), a row of small squares (one per day; stronger red = more Shorts, today outlined in coral, future days dashed, hover shows "Oct 12 · 5 Shorts"), and green text "▲ 21 ahead of September" (or amber if behind).
- Counts come from the board's Uploaded dates. Goals are editable in Settings.

**Quote line (under the top bar)**
- Quotes come from an editable `quotes.txt`, one per line: `Morning | text | author` (author optional; parts: Morning 05:00–12:00, Afternoon 12:00–18:00, Evening 18:00–05:00, and `Goal` for goal-hit lines).
- The quote changes **3 times a day**: once in the morning, once in the afternoon, once in the evening. Each part shows one quote picked from its list, and it stays the same for that whole part, even if I restart the app. Remember the pick in a small file and don't repeat yesterday's quote. Show a small tag with the part of the day ("Morning") next to the quote. The change happens with a soft fade, and a click shows the next quote from the same part. When the daily goal is hit, show a `Goal` line.
- Start `quotes.txt` with the quotes from `docs\ui_preview.html` (the `Q` and `goalQ` lists), including "I just never thought I couldn't." — McQueen.

**Small extras (keep)**
- Windows **taskbar progress**: the app's taskbar icon fills with the overall progress (ITaskbarList3 via ctypes/comtypes).
- **Drag rows** in the queue to change the order.
- **Right-click** a queue row: Open folder, Open in Premiere, Copy title, Run again (Thorough), Remove.
- **Keyboard shortcuts** with small key hints: Space = Start, Del = remove the row under the mouse, Ctrl+V = paste a YouTube link (goes to Download).
- **History tab**: a timeline of everything (downloaded, edit made, PASS/notes, uploaded…) with date and time, channel color, and a search box.

**NOT wanted (don't add)**
- Black cinema bars, thumbnails on board cards, a mouse spotlight or glow that follows the cursor, focus mode, sounds, a before/after slider, a money goal, a streak counter, "next channel" progress, and a glow or sweep on the drop zone.

## 10. Finish

1. Run all tests. Open the app and click through every tab. Use the Qt test tools or screenshots to check that hovers fade (no jumps), page switches glide, nothing freezes, and the intro is centered.
2. Run `.venv\Scripts\python tools\app\setup_app.py` so the desktop shortcut and icon are updated.
3. Write `reports\night11.md` in plain, simple English:
   - what's done
   - what's not done and why
   - before → after numbers for video11 / video12 / chocolate
   - hard_cases results
   - anything I must check by hand
   - **laptop steps** (exact commands)
4. Commit and push.
5. Run `shutdown /a`, then `shutdown /s /t 120`. Do this even if you finish early.

## Later (NOT tonight, just keep in `requests.md`)

- A **Stats** tab with channel analytics: first public stats with yt-dlp (views, likes, subscribers, fastest-growing Shorts), later private YouTube Studio stats through a Google login.
- Automating the Topaz hand-off between the PC and laptop.
