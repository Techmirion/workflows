"""repo-init builds its plan without calling GitHub; a dry run changes nothing."""
import os
import subprocess
import sys

from conftest import ROOT
from tmflow import repoinit


def kinds(steps):
    return [s["kind"] for s in steps]


def test_private_repo_plan():
    steps = repoinit.plan("Techmirion/novit-ui")
    settings = steps[0]["argv"]
    for flag in ("allow_merge_commit=true", "allow_squash_merge=false", "allow_rebase_merge=false",
                 "delete_branch_on_merge=false", "has_issues=true", "has_wiki=false", "has_projects=false",
                 "merge_commit_title=MERGE_MESSAGE", "merge_commit_message=PR_TITLE", "default_branch=main"):
        assert flag in settings
    assert "ruleset" not in kinds(steps)
    assert ["gh", "api", "-X", "PUT", "repos/Techmirion/novit-ui/vulnerability-alerts"] in [s.get("argv") for s in steps]
    assert ["gh", "api", "-X", "DELETE", "repos/Techmirion/novit-ui/automated-security-fixes"] in [s.get("argv") for s in steps]
    assert kinds(steps).count("manual") >= 4  # free plan note, three Apps, secrets


def test_public_repo_gets_protection_with_app_bypass():
    steps = repoinit.plan("Techmirion/workflows", visibility="public", automation_app_id="123")
    rs = next(s for s in steps if s["kind"] == "ruleset")["stdin_json"]
    assert rs["conditions"]["ref_name"]["include"] == ["refs/heads/main", "refs/heads/dev"]
    types = [r["type"] for r in rs["rules"]]
    assert types == ["deletion", "non_fast_forward", "pull_request", "required_status_checks"]
    pr_rule = rs["rules"][2]["parameters"]
    assert pr_rule["required_approving_review_count"] == 1 and pr_rule["allowed_merge_methods"] == ["merge"]
    assert rs["bypass_actors"] == [{"actor_id": 123, "actor_type": "Integration", "bypass_mode": "always"}]
    assert any(s.get("stdin_json", {}).get("security_and_analysis") for s in steps)


def test_secrets_and_apps_when_given():
    steps = repoinit.plan("T/app", installations={"automation": "42"}, app_id_value="9", app_key_file="/k.pem")
    assert {"kind": "app", "what": "install the automation App on the repo", "installation": "42"} in steps
    key = next(s for s in steps if s.get("stdin_file"))
    assert key["argv"] == ["gh", "secret", "set", "AUTOMATION_APP_KEY", "--repo", "T/app"]
    assert "/k.pem" not in key["argv"]


def test_files_and_hooks_for_a_clone(tmp_path):
    steps = repoinit.plan("T/app", clone=str(tmp_path), owner="patriarxis")
    assert "files" in kinds(steps) and "local" in kinds(steps)
    for src, _ in repoinit.template_targets(str(tmp_path)):
        assert src.exists(), src
    text = repoinit.render_template(repoinit.TEMPLATES / ".github" / "CODEOWNERS", "patriarxis", None)
    assert "* @patriarxis @DEPUTY" in text


def test_cli_dry_run_never_calls_gh(tmp_path):
    d = tmp_path / "fakebin"
    d.mkdir()
    marker = tmp_path / "gh-was-called"
    (d / "gh").write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n")
    (d / "gh").chmod(0o755)
    clone = tmp_path / "clone"
    clone.mkdir()
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "tm.py"), "repo-init", "--repo", "T/app",
                        "--visibility", "public", "--clone", str(clone)],
                       capture_output=True, text=True, env={**os.environ, "PATH": f"{d}:{os.environ['PATH']}"})
    assert p.returncode == 0, p.stderr
    assert "DRY RUN" in p.stdout and "gh api -X PATCH repos/T/app" in p.stdout
    assert not marker.exists()
    assert list(clone.iterdir()) == []  # nothing written


def test_merge_gitignore_keeps_existing_entries():
    old = ".shots/\n__pycache__/\n*.bak*\n"
    new = repoinit.merge_gitignore(old, "# head\n.env\n__pycache__/\n.venv/\n")
    assert new.startswith(old)
    assert new.count("__pycache__/") == 1 and ".env\n" in new and ".venv/" in new
    assert repoinit.merge_gitignore(new, "# head\n.env\n") == new
