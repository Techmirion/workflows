"""The label set and its script (dry run), and the reviewer choice."""
import json
import os
import subprocess
import sys

from conftest import ROOT
from tmflow import labels, reviewers

SECTION_13 = {"bug", "feature", "chore", "docs", "deps",
              "priority:urgent", "priority:high", "priority:normal", "priority:low",
              "severity:S1", "severity:S2", "severity:S3", "severity:S4",
              "needs-triage", "blocked", "external"}


def test_label_set_matches_git_md_13_plus_wip():
    names = {lab["name"] for lab in labels.load_labels()}
    assert names == SECTION_13 | {"wip"}
    for lab in labels.load_labels():
        assert len(lab["color"]) == 6 and len(lab["description"]) <= 100


def test_plan_creates_updates_and_prunes():
    desired = [{"name": "bug", "color": "d73a4a", "description": "Something is broken"},
               {"name": "priority:high", "color": "d93f0b", "description": "Next"}]
    current = [{"name": "Bug", "color": "D73A4A", "description": "Something is broken"},
               {"name": "enhancement", "color": "a2eeef", "description": "New feature or request"}]
    ops = labels.plan_labels(desired, current)
    assert ops == [("update", "Bug", desired[0]), ("create", desired[1])]
    ops = labels.plan_labels(desired, current, prune=True)
    assert ("delete", "enhancement") in ops


def test_plan_is_empty_when_in_step():
    desired = labels.load_labels()
    assert labels.plan_labels(desired, [dict(d) for d in desired]) == []


def test_gh_commands_encode_label_names():
    cmds = labels.gh_commands("T/app", [("update", "priority:urgent", {"name": "priority:urgent", "color": "b60205",
                                                                    "description": "x"}),
                                        ("delete", "good first issue")])
    assert cmds[0][:4] == ["gh", "api", "-X", "PATCH"] and cmds[0][4] == "repos/T/app/labels/priority%3Aurgent"
    assert cmds[1][4] == "repos/T/app/labels/good%20first%20issue"


def fake_gh(tmp_path):
    d = tmp_path / "fakebin"
    d.mkdir()
    marker = tmp_path / "gh-was-called"
    (d / "gh").write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n")
    (d / "gh").chmod(0o755)
    return {**os.environ, "PATH": f"{d}:{os.environ['PATH']}"}, marker


def test_labels_cli_dry_run_changes_nothing(tmp_path):
    current = tmp_path / "current.json"
    current.write_text(json.dumps([{"name": "bug", "color": "d73a4a", "description": "Something is broken"},
                                   {"name": "documentation", "color": "0075ca", "description": "Docs"}]))
    env, marker = fake_gh(tmp_path)
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "tm.py"), "labels", "--repo", "T/app",
                        "--current-file", str(current), "--prune"], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert not marker.exists()
    assert p.stdout.count("(dry run) gh api -X POST repos/T/app/labels") == 16
    assert "(dry run) gh api -X DELETE repos/T/app/labels/documentation" in p.stdout
    assert "labels/bug" not in p.stdout


# --- reviewers -----------------------------------------------------------------

def test_parse_codeowners():
    co = reviewers.parse_codeowners("# comment\n* @fotis @marios\n/docs/ @someone\n")
    assert co == {"owner": "fotis", "deputy": "marios"}
    assert reviewers.parse_codeowners("* @a\n* @b @c  # later wins\n") == {"owner": "b", "deputy": "c"}
    assert reviewers.parse_codeowners("") == {"owner": None, "deputy": None}
    assert reviewers.parse_codeowners("* @fotis\n# deputy: TODO") == {"owner": "fotis", "deputy": None}


def test_pick_reviewers():
    assert reviewers.pick_reviewers("giannis", "fotis", "marios") == (["fotis"], [], [])
    assert reviewers.pick_reviewers("Fotis", "fotis", "marios") == (["marios"], [], [])
    assert reviewers.pick_reviewers("marios", "fotis", "marios") == (["fotis"], [], [])
    assert reviewers.pick_reviewers("dependabot[bot]", "fotis", None) == (["fotis"], [], [])
    users, teams, warnings = reviewers.pick_reviewers("fotis", "fotis", None)
    assert users == [] and teams == [] and "no deputy" in warnings[0]
    assert reviewers.pick_reviewers("x", "Techmirion/ui-team", None) == ([], ["ui-team"], [])
    assert reviewers.pick_reviewers("x", None, None)[2]
