"""Night 9: Scene Edit Detection always on (every RAW shot its own clip, --no-scene-split, the splits no cuts for learn
and check-all) and the action-caption suggestions."""
from __future__ import annotations

from match_cuts import cli, edit_score, learn
from match_cuts import export_xml_edl as ex


# ---------------------------------------------------------------------------------------------------------------------
# 2. Scene Edit Detection: on by default, --no-scene-split
# ---------------------------------------------------------------------------------------------------------------------

def test_scene_split_is_on_by_default_and_no_scene_split_turns_it_off():
    assert cli.config_from_args(cli.build_parser().parse_args([])).premiere_scene_cuts
    assert not cli.config_from_args(cli.build_parser().parse_args(["--no-scene-split"])).premiere_scene_cuts
    assert not cli.config_from_args(cli.build_parser().parse_args(["--no-scene-cuts"])).premiere_scene_cuts  # old
    assert "--no-scene-split" in cli.build_parser().format_help()


def test_every_raw_shot_inside_a_clip_becomes_its_own_clip_seamlessly(tmp_path):
    """Two RAW shot changes inside S01 (RAW frames 158-190): three V1 clips, each the next one's source running on
    (no frame skipped or shown twice), the same framing, A1 split at the same frames and linked one to one."""
    from test_export_xml_edl import _shots_cfg, premiere_cutlist
    from match_cuts.shots import shot_of
    changes = [168 * 1001 / 24000, 180 * 1001 / 24000]
    cl = premiere_cutlist()
    xml = tmp_path / "1_edit.xml"
    cfg = _shots_cfg(tmp_path, changes)
    res = ex.write_premiere_xml(cl, xml, cfg)
    assert [d["clip"] for d in res["scene_cuts"]] == ["S01", "S01"]
    x = ex.parse_premiere_xml(xml)
    a, b, c = x["clips"][:3]
    for p, q in ((a, b), (b, c)):
        assert p["end"] == q["start"] and q["in"] == p["out"]               # seamless
        assert p["motion"].get("scale") == q["motion"].get("scale")         # the same framing: one shot of the take
        assert p["motion"].get("center") == q["motion"].get("center")
    f = 60.0
    assert [shot_of(p["in"] / f, changes) for p in (a, b, c)] == [0, 1, 2]
    assert [(i["start"], i["end"]) for i in x["audio"][:3]] == [(p["start"], p["end"]) for p in (a, b, c)]
    assert ex.validate_premiere_exports(cl, xml, None, cfg)["ok"]
    assert not ex.premiere_link_problems(xml)[0]                           # each V1 piece linked to its own A1


def test_learn_does_not_count_scene_splits_as_cuts():
    C = learn.Clip
    tool = [C(0.0, 2.0, 100.0, 102.0), C(2.0, 3.0, 102.0, 103.0), C(3.0, 5.0, 110.0, 112.0)]   # a split at 102 s
    user = [C(0.0, 3.0, 100.0, 103.0), C(3.0, 5.0, 110.0, 112.0)]           # you kept the take whole
    js = learn.join_splits
    assert [(c.r0, c.r1) for c in js(tool)] == [(100.0, 103.0), (110.0, 112.0)]
    ch = learn.edit_changes(js(tool), js(user), tool, user)
    assert ch["clips"] == 2 and ch["starts"] == [0.0, 0.0] and ch["ends"] == [0.0, 0.0]
    assert ch["removed"] == 0 and ch["added"] == 0 and ch["inner"] == []
    # your own cut inside a take (video10: 0.29 s cut after the RAW's shot change) is still counted
    user2 = [C(0.0, 2.0, 100.0, 102.0), C(2.0, 2.71, 102.29, 103.0), C(2.71, 4.71, 110.0, 112.0)]
    ch2 = learn.edit_changes(js(tool), js(user2), tool, user2)
    assert ch2["inner"] == [0.29] and ch2["removed"] == 0 and ch2["added"] == 0
    lines = learn.summary({"edit": dict(ch2, speed={}, flip={}, key="picture"), "captions": {"count": 0},
                           "run": "r", "case": "c", "updated": False, "files": {}, "glossary_new": [],
                           "suggestions": [], "git": [], "changes": [], "tendencies": {}})
    assert any("1 cut(s) of yours inside one of the run's clips" in s and "scene splits" in s for s in lines)


def test_check_all_cut_score_does_not_count_a_scene_split():
    P = edit_score.Piece
    split = edit_score.Edit([P(0.0, 2.0, 100.0), P(2.0, 3.0, 102.0), P(3.0, 5.0, 110.0)], fps=60.0, raw_fps=24.0)
    whole = edit_score.Edit([P(0.0, 3.0, 100.0), P(3.0, 5.0, 110.0)], fps=60.0, raw_fps=24.0)
    assert [round(c.t, 3) for c in split.cuts()] == [round(c.t, 3) for c in whole.cuts()] == [3.0]


# ---------------------------------------------------------------------------------------------------------------------
# 5. action captions: the *...* placeholders filled with a suggested action
# ---------------------------------------------------------------------------------------------------------------------

def test_the_models_answer_becomes_an_action_caption_in_your_style_or_nothing():
    from match_cuts import action_captions as A
    assert A.tidy("Claps hands.") == "*claps hands*"
    assert A.tidy("He sits down") == "*sits down*"                       # yours have no subject
    assert A.tidy("*gives high five*") == "*gives high five*"
    assert A.tidy("Caption: laughing") == "*laughing*"
    for unsure in ("unsure", "Unsure.", "", "person", "The man is walking towards the door slowly",
                   "I cannot tell", "shakes hands, smiles"):
        assert A.tidy(unsure) is None, unsure


def test_your_action_captions_are_the_examples_and_learn_adds_new_ones(tmp_path):
    from match_cuts import action_captions as A
    f = tmp_path / "caption_actions.txt"
    f.write_text("# yours\n*claps hands*\n*sits down*\n*um*\n", encoding="utf-8")
    assert A.read_examples(f) == ["claps hands", "sits down"]               # a sound is no action to see
    assert A.add_examples(["*claps hands*", "*understands it*", "*ummm*", "*Everyone leaves*"], "video10", f) == \
        ["understands it", "Everyone leaves"]
    assert A.read_examples(f)[-2:] == ["understands it", "Everyone leaves"]
    assert A.action_texts("1\n00:00:00,000 --> 00:00:01,000\n*claps hands*\n\n2\n00:00:01,000 --> "
                          "00:00:02,000\n*...*\n") == ["claps hands"]
    assert len(A.read_examples()) >= 70                                    # the file collected tonight
    ex = A.pick_examples(A.read_examples())
    assert 10 <= len(ex) <= 30 and len({e.split()[0] for e in ex}) == len(ex)


def test_the_frames_of_a_placeholder_are_spread_over_it():
    from fractions import Fraction
    from match_cuts import action_captions as A
    assert A.frame_times(60, 120, Fraction(60), 4) == [1.125, 1.375, 1.625, 1.875]


def _srt(tmp_path):
    p = tmp_path / "2_captions.srt"
    p.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello there\n\n2\n00:00:01,000 --> 00:00:02,500\n*...*\n\n"
                 "3\n00:00:02,500 --> 00:00:03,000\nbye\n\n4\n00:00:03,000 --> 00:00:04,500\n*...*\n", encoding="utf-8")
    return p


def test_a_sure_suggestion_replaces_its_placeholder_and_an_unsure_one_keeps_it(tmp_path, monkeypatch):
    from fractions import Fraction
    from types import SimpleNamespace
    from match_cuts import action_captions as A
    srt = _srt(tmp_path)
    (tmp_path / "1_edit.xml").write_text("<xmeml/>", encoding="utf-8")
    caps = [{"text": "hello there", "start": 0, "end": 60, "mode": "voice"},
            {"text": "*...*", "start": 60, "end": 150, "mode": "placeholder"},
            {"text": "bye", "start": 150, "end": 180, "mode": "voice"},
            {"text": "*...*", "start": 180, "end": 270, "mode": "placeholder"}]
    ctx = SimpleNamespace(cfg=SimpleNamespace(deliver=tmp_path, debug_dir=tmp_path, action_model="local"),
                          captions={"fps": "60", "captions": caps, "placeholders": [caps[1], caps[3]]}, cache=None)

    class Fake:
        name = "fake"

        def close(self):
            pass
    monkeypatch.setattr(A, "make_model", lambda name: Fake())
    monkeypatch.setattr(A, "suggest", lambda spots, fps, xml, model, ex, cache=None, raw=None, contexts=None: [
        A.Suggestion(60, 150, "*claps hands*", "Claps hands.", 0.91),
        A.Suggestion(180, 270, None, "unsure", 0.88, "no clear action")])
    info = A.fill_run(ctx)
    text = srt.read_text(encoding="utf-8")
    assert "*claps hands*" in text and text.count("*...*") == 1          # the unsure one stays a placeholder
    assert [r["text"] for r in info["suggested"]] == ["*claps hands*"] and info["kept"][0]["why"] == "no clear action"
    assert caps[1]["mode"] == "action" and caps[1]["suggested"] and caps[3]["mode"] == "placeholder"
    assert ctx.captions["placeholders"] == [caps[3]]
    assert (tmp_path / "captions.json").is_file()


def test_no_action_captions_keeps_every_placeholder(monkeypatch):
    from types import SimpleNamespace
    from match_cuts import action_captions as A, captions, pipeline
    called = []
    monkeypatch.setattr(captions, "run_captions", lambda ctx: {"path": "x.srt", "placeholders": [{"start": 1}]})
    monkeypatch.setattr(A, "fill_run", lambda ctx: called.append(1))
    for on, want in ((False, []), (True, [1])):
        called.clear()
        ctx = SimpleNamespace(cfg=SimpleNamespace(action_captions=on), captions={}, paths={}, warn=lambda m: None)
        pipeline.stage_captions(ctx)
        assert called == want
    args = cli.build_parser().parse_args(["--no-action-captions", "--action-model", "none"])
    cfg = cli.config_from_args(args)
    assert not cfg.action_captions and cfg.action_model == "none"
    assert "action_captions" not in cfg.analysis_params()                   # no cache key changes
    assert A.make_model("none") is None


def test_the_end_summary_lists_the_suggestions_to_check():
    from types import SimpleNamespace
    from match_cuts import report
    cap = {"placeholders": [], "actions": {"model": "Qwen/Qwen3-VL-2B-Instruct", "kept": [], "suggested": [
        {"start_tc": "00:00:03,467", "end_tc": "00:00:04,550", "text": "*claps hands*", "confidence": 0.9,
         "answer": "claps hands", "why": ""}]}}
    cap.update(mode="voice", reason="test", by_mode={"voice": 3}, fps="60")
    lines = report._captions(SimpleNamespace(captions=cap))
    assert any("Suggested actions" in ln for ln in lines) and any("`*claps hands*` (sure 0.90)" in ln for ln in lines)


def test_the_report_lists_the_suggestions_also_when_the_competitors_captions_are_copied():
    from types import SimpleNamespace
    from match_cuts import report
    act = {"model": "m", "kept": [], "suggested": [{"start_tc": "00:00:18,367", "end_tc": "00:00:19,733",
                                                     "text": "*walks away*", "confidence": 0.7, "check": 0.81,
                                                     "answer": "walks away", "why": ""}]}
    for mode in ("competitor", "voice"):
        cap = {"mode": mode, "reason": "test", "by_mode": {mode: 3, "placeholder": 1}, "fps": "60",
               "placeholders": [{"start_tc": "00:00:20,233", "end_tc": "00:00:23,300"}], "actions": act}
        text = "\n".join(report._captions(SimpleNamespace(captions=cap)))
        assert "**Suggested actions**" in text and "`*walks away*` (sure 0.70, asked again: yes 0.81)" in text, mode
        assert "**`*...*` placeholders** (silences over ~1 s — write the action there): 1" in text, mode


def test_a_suggestion_the_model_doubts_when_asked_again_keeps_the_placeholder():
    from types import SimpleNamespace
    from match_cuts import action_captions as A, report
    assert A.decide("Claps hands.", 0.9, 0.8) == ("*claps hands*", "")
    assert A.decide("claps hands", 0.9, None) == ("*claps hands*", "")      # a model without check(): not asked
    text, why = A.decide("sits down", 0.74, 0.03)                            # the 2B's fallback when it is lost
    assert text is None and why == "the model doubted it when asked again (yes 0.03)"
    assert A.decide("claps hands", 0.3, 0.9) == (None, "the model was unsure")
    assert A.decide("looks up", 0.9, 0.9) == (None, "no clear action")
    assert len(A.CHECKS) == 2 and all("{action}" in q and "yes or no" in q for q in A.CHECKS)
    cap = {"placeholders": [], "mode": "voice", "reason": "test", "by_mode": {"voice": 3}, "fps": "60",
           "actions": {"model": "m", "suggested": [{"start_tc": "00:00:03,467", "end_tc": "00:00:04,550",
                                                     "text": "*claps hands*", "confidence": 0.94, "check": 0.73,
                                                     "answer": "claps hands", "why": ""}],
                       "kept": [{"start_tc": "00:00:07,000", "end_tc": "00:00:08,000", "text": None,
                                 "confidence": 0.74, "check": 0.03, "answer": "sits down",
                                 "why": "the model doubted it when asked again (yes 0.03)"}]}}
    lines = report._captions(SimpleNamespace(captions=cap))
    assert any("`*claps hands*` (sure 0.94, asked again: yes 0.73)" in ln for ln in lines)
    assert any("left as `*...*`: the model doubted it when asked again (yes 0.03) (it said 'sits down')" in ln
               for ln in lines)


def test_suggest_asks_the_model_again_only_about_a_suggestion(tmp_path, monkeypatch):
    from fractions import Fraction
    from match_cuts import action_captions as A, caption_score
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"x")

    class TL:
        def at(self, t):
            return ("raw", 100.0 + t)
    monkeypatch.setattr(caption_score.Timeline, "from_xml", staticmethod(lambda xml: TL()))
    monkeypatch.setattr(A, "raw_frames", lambda raw, ts: [object() for _ in ts])
    asked = []

    class Fake:
        name = "fake"

        def __init__(self, answers):
            self.answers = list(answers)

        def describe(self, images, examples, context=""):
            return self.answers.pop(0)

        def check(self, images, action):
            asked.append(action)
            return {"claps hands": 0.9, "sits down": 0.1}[action]
    got = A.suggest([(0, 60), (60, 120), (120, 180)], Fraction(60), tmp_path / "1_edit.xml",
                    Fake([("claps hands", 0.9), ("sits down", 0.8), ("looks up", 0.9)]), [], raw=raw)
    assert [g.text for g in got] == ["*claps hands*", None, None]
    assert asked == ["claps hands", "sits down"]                             # 'looks up' was never a suggestion
    assert got[1].check == 0.1 and "doubted" in got[1].why and got[2].why == "no clear action"


def test_with_no_sure_action_a_laugh_is_suggested_and_other_reactions_are_not(tmp_path, monkeypatch):
    from fractions import Fraction
    from match_cuts import action_captions as A, caption_score
    assert A.decide_reaction("laughing", 0.9, 0.9) == ("*laughing*", "")
    assert A.decide_reaction("Shocked.", 0.9, 0.9) == (None, "no clear action")   # 'shocked': its fallback when lost
    assert A.decide_reaction("laughing", 0.9, 0.2)[0] is None and A.decide_reaction("laughing", 0.3, 0.9)[0] is None
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"x")

    class TL:
        def at(self, t):
            return ("raw", 100.0 + t)
    monkeypatch.setattr(caption_score.Timeline, "from_xml", staticmethod(lambda xml: TL()))
    monkeypatch.setattr(A, "raw_frames", lambda raw, ts: [object() for _ in ts])
    calls = []

    class Fake:
        name = "fake"

        def __init__(self):
            self.answers = [("looks up", 0.9), ("looks up", 0.9), ("claps hands", 0.9)]
            self.reactions = [("laughing", 0.95), ("shocked", 0.9)]

        def describe(self, images, examples, context=""):
            return self.answers.pop(0)

        def react(self, images, context=""):
            calls.append("react")
            return self.reactions.pop(0)

        def check(self, images, action, questions=None):
            calls.append((action, questions is A.REACTION_CHECKS))
            return 0.9
    got = A.suggest([(0, 60), (60, 120), (120, 180)], Fraction(60), tmp_path / "1_edit.xml", Fake(), [], raw=raw)
    assert [g.text for g in got] == ["*laughing*", None, "*claps hands*"]
    assert got[0].answer == "laughing" and got[0].check == 0.9 and got[1].why == "no clear action"
    # the reaction is asked only with no sure action; only 'laughing' is asked again (with its own questions)
    assert calls == ["react", ("laughing", True), "react", ("claps hands", False)]


def test_a_generic_answer_keeps_the_placeholder():
    from match_cuts import action_captions as A
    for generic in ("looks up", "Stares ahead.", "leans forward", "smiles at the camera", "gestures with hands"):
        assert A.tidy(generic) is None, generic
    assert A.tidy("laughs hard") == "*laughs hard*" and A.tidy("enters the room") == "*enters the room*"


def test_the_model_hears_what_is_said_around_the_moment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from match_cuts import action_captions as A
    assert A.context_of("lady doesn't go", "The lady's fifteen") == \
        " Just before it, someone says: \"lady doesn't go\". Just after it: \"The lady's fifteen\"."
    assert A.context_of("", "") == ""
    _srt(tmp_path)
    (tmp_path / "1_edit.xml").write_text("<xmeml/>", encoding="utf-8")
    caps = [{"text": "hello there", "start": 0, "end": 60, "mode": "voice"},
            {"text": "*...*", "start": 60, "end": 150, "mode": "placeholder"},
            {"text": "bye", "start": 150, "end": 180, "mode": "voice"},
            {"text": "*...*", "start": 180, "end": 270, "mode": "placeholder"}]
    ctx = SimpleNamespace(cfg=SimpleNamespace(deliver=tmp_path, debug_dir=tmp_path, action_model="local"),
                          captions={"fps": "60", "captions": caps, "placeholders": [caps[1], caps[3]]}, cache=None)
    seen = {}

    class Fake:
        name = "fake"
    monkeypatch.setattr(A, "make_model", lambda name: Fake())

    def fake_suggest(spots, fps, xml, model, ex, cache=None, raw=None, contexts=None):
        seen["contexts"] = contexts
        return [A.Suggestion(a, b, None, "unsure", 0.9, "no clear action") for a, b in spots]
    monkeypatch.setattr(A, "suggest", fake_suggest)
    A.fill_run(ctx)
    assert seen["contexts"] == [A.context_of("hello there", "bye"), A.context_of("bye", "")]


def test_the_2b_model_on_every_computer_and_the_4b_on_request(monkeypatch):
    """The same suggestions on the PC and the laptop (the 2B fits both; the 4B next to a second run spilled out of the
    PC's card: 9 minutes for 3 placeholders)."""
    from match_cuts import action_captions as A
    monkeypatch.delenv("MATCH_CUTS_NO_ACTION_MODEL", raising=False)
    assert A.make_model("local").name == A.MODEL_ID == "Qwen/Qwen3-VL-2B-Instruct"
    assert A.make_model("big").name == A.MODEL_BIG == "Qwen/Qwen3-VL-4B-Instruct"
    assert A.make_model("Qwen/Qwen3-VL-8B-Instruct").name == "Qwen/Qwen3-VL-8B-Instruct"
    monkeypatch.setenv("MATCH_CUTS_NO_ACTION_MODEL", "1")
    assert A.make_model("local") is None                                    # the unit tests
