# Day 12 -- helper "look": the night-11 look and motion rules on today's widgets

## What I checked

Every widget added or changed today (`git diff 43239dd..HEAD -- tools/app/mr_app/ui tools/app/mr_app/tools`):
ui/torrent_panel.py (FileRow, HealthBars, _Stat, TorrentPicker, TorrentCard), ui/titles_block.py, ui/glide_list.py,
tools/learn_board.py (LCard, LCol, LearnColumns), tools/download_tool.py (DetLabel, HeadlineBox, DotButton,
ChannelButtons, AutoSwitch, StepCell, VideoRow, RowList, ShutdownWarning, the form), the queue's pair line / Swap /
amber check card in tools/match_cuts_tool.py, the board card's titles block and Use… menu, Settings' torrent rows.

1. Hover = Fader only, nothing moves: no `:hover` QSS anywhere in the app (grep). Every new hoverable widget is
   widgets.Button / Card or paints itself with motion.Fader (FileRow, LCard, AutoSwitch). Tested: hover in ~0.3 s,
   out ~0.48 s, geometry identical before / during / after.
2. Animations off: every new animation goes through Fader or checks motion.enabled() (GlideList, scroll_to_top,
   LCard open/close, the switch's knob). Tested: all instant.
3. Colours / fonts: every hard-coded colour is one of the reference's own (#2a2a2a health bars and switch, #181818
   file row hover, the .det colours, #1a1617 job hover, #2a1812 / #5a2f22 Just cuts pill). Section titles are
   W.SectionHeader (Bebas Neue); labels and times JetBrains Mono.
4. Window thread: torrent work, Clean up, headline / title saves, roles, problems' logs are all in ui/background.

## What I fixed

* `DownloadPage.xml_of` did a disk stat on EVERY row refresh (the run folder can be on the shared folder over the
  network). Now asked once in the background, remembered, the row refreshed. Open in Premiere checks in the
  background too; the row's Open folder (exists + startfile) runs in the background.
* Letter spacing: `W.mono(size, weight, spacing)` takes a PERCENT; today's labels passed 1.0 (+1 %) where the
  reference has .1em (+10 %): the detected-kind label, the step names, the form's small keys, the titles block's
  keys -- now 10 (5 for the compact key). The detected-kind label is 10 px like the reference.
* The form's YOUTUBE / TORRENT / FILE / EMPTY label switched colours at once; the reference fades it (.det
  transition .3s). It now paints its box and fades (Animations off: at once); its size never changes.

Tests: tools/app/tests/test_motion_day12.py (9 tests).

## Left as is (noted, not rule breaks)

* VideoRow / TorrentPicker / TorrentCard use the night-11 Card hover (fill + border + faint light) where the
  reference's .pipe / .tcard only lighten the border -- kept for one look across the app.
* StepCell's name colour changes at once on a state change (the reference fades it .4s) -- a state change, not a
  hover.
* Night-11 code reached from today's cards: LearnPage.take() stats the dropped .prproj / folder, and
  learnkit.mark_seen writes a small local state file, on the window's thread (single small local operations).
* NV.set_automatic writes the settings file on the window's thread (like Settings' Save always did).
