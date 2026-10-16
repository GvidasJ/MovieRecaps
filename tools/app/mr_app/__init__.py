"""MovieRecaps desktop app: one window for the project's tools, one tab each (night 9: Match cuts; Download and
Convert come later). It only runs the tools as they are -- every Match cuts job is the same command line as
``python -m match_cuts batch`` runs for a video, and its result is read with the batch's own checks."""

APP_NAME = "MovieRecaps"
APP_ID = "MovieRecaps.Desktop.1"          # Windows: the taskbar icon and the notifications' sender
VERSION = "1.0"
