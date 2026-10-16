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
