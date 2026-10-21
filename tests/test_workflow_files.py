"""Static rules for the workflow files and templates (git.md 13 and 14)."""
import re
from pathlib import Path

import pytest
import yaml

from conftest import ROOT
from tmflow import fmt, labels

REUSABLE = ["checks", "assign-reviewers", "cleanup", "release-pr", "release", "sync-dev"]
WF = ROOT / ".github" / "workflows"
TPL = ROOT / "templates" / ".github" / "workflows"
ALL = sorted(WF.glob("*.yml")) + sorted(TPL.glob("*.yml"))
USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<ref>\S+)(?P<comment>.*)$")


def load(p):
    return yaml.safe_load(p.read_text())


def on(doc):
    return doc.get("on", doc.get(True))  # PyYAML reads the key `on` as True


@pytest.mark.parametrize("name", REUSABLE)
def test_reusable_workflows_exist_and_are_workflow_call(name):
    doc = load(WF / f"{name}.yml")
    assert "workflow_call" in on(doc)
    inputs = on(doc)["workflow_call"]["inputs"]
    assert inputs["workflows-ref"]["default"] == "v1"
    assert inputs["workflows-repo"]["default"] == "Techmirion/workflows"


@pytest.mark.parametrize("path", ALL, ids=lambda p: str(p.relative_to(ROOT)))
def test_permissions_are_explicit(path):
    doc = load(path)
    assert "permissions" in doc, "top-level permissions missing"
    for job_id, job in doc["jobs"].items():
        if "uses" not in job:
            assert "permissions" in job, f"{job_id}: job permissions missing"


@pytest.mark.parametrize("path", ALL, ids=lambda p: str(p.relative_to(ROOT)))
def test_actions_pinned_to_full_sha_with_version_comment(path):
    for line in path.read_text().splitlines():
        m = USES_RE.match(line)
        if not m:
            continue
        ref = m.group("ref")
        if ref.startswith("./"):
            continue
        if ref.startswith("Techmirion/workflows/.github/workflows/"):
            assert ref.endswith("@v1"), line  # callers use the moving major tag (git.md 14)
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"not pinned to a SHA: {line}"
        assert re.search(r"#\s*v\d+\.\d+\.\d+\s*$", m.group("comment")), f"no version comment: {line}"


@pytest.mark.parametrize("path", ALL, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_expressions_inside_run_scripts(path):
    """Inputs reach scripts through env:, never pasted into the shell (script injection)."""
    for job in load(path)["jobs"].values():
        for step in job.get("steps", []):
            assert "${{" not in step.get("run", ""), step.get("name")


@pytest.mark.parametrize("path", ALL, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_secrets_inherit_and_no_default_token_for_writes(path):
    text = path.read_text()
    assert "secrets: inherit" not in text
    doc = load(path)
    for job in doc["jobs"].values():
        perms = job.get("permissions") or {}
        if isinstance(perms, dict) and perms.get("contents") == "write":
            # only cleanup deletes branches with the job token; tags, PRs and dev use the App
            assert "cleanup" in path.name, path


def test_writes_go_through_the_automation_app():
    for name in ("release-pr", "release", "sync-dev"):
        doc = load(WF / f"{name}.yml")
        steps = doc["jobs"][name]["steps"]
        app = next(s for s in steps if s.get("id") == "app")
        assert app["uses"].startswith("actions/create-github-app-token@")
        assert app["with"]["private-key"] == "${{ secrets.AUTOMATION_APP_KEY }}"
        run = steps[-1]
        assert run["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
        assert doc["jobs"][name]["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("name", REUSABLE)
def test_templates_call_the_shared_workflow(name):
    doc = load(TPL / f"{name}.yml")
    (job,) = doc["jobs"].values()
    assert job["uses"] == f"Techmirion/workflows/.github/workflows/{name}.yml@v1"
    assert job.get("with", {}).get("workflows-ref") == "v1"


def test_template_triggers():
    assert set(on(load(TPL / "checks.yml"))) == {"pull_request", "push"}
    assert on(load(TPL / "checks.yml"))["push"]["branches"] == ["dev", "main"]
    assert "edited" in on(load(TPL / "checks.yml"))["pull_request"]["types"]
    assert "pull_request_target" in on(load(TPL / "assign-reviewers.yml"))
    assert on(load(TPL / "cleanup.yml"))["pull_request"]["types"] == ["closed"]
    assert "workflow_dispatch" in on(load(TPL / "release-pr.yml"))
    assert on(load(TPL / "release.yml"))["push"]["branches"] == ["main"]
    assert on(load(TPL / "sync-dev.yml"))["push"]["branches"] == ["main"]


def test_sync_dev_is_not_copied_by_repo_init():
    """A repo on the full flow syncs through release; running both would race."""
    from tmflow import repoinit
    assert ".github/workflows/sync-dev.yml" not in repoinit.TEMPLATE_FILES
    assert ".github/workflows/release.yml" in repoinit.TEMPLATE_FILES


def test_sync_dev_says_what_is_missing_without_the_app_secrets():
    steps = load(WF / "sync-dev.yml")["jobs"]["sync-dev"]["steps"]
    assert "AUTOMATION_APP_KEY" in steps[0]["run"] and "exit 1" in steps[0]["run"]
    assert steps[1].get("id") == "app"


def test_self_callers_use_local_workflows():
    for p in WF.glob("self-*.yml"):
        (job,) = load(p)["jobs"].values()
        assert job["uses"].startswith("./.github/workflows/"), p
    (job,) = load(WF / "self-release.yml")["jobs"].values()
    assert job["with"]["move-major-tag"] is True


# --- issue forms, PR template, dependabot ------------------------------------

def test_issue_forms_use_the_label_set():
    names = {lab["name"] for lab in labels.load_labels()}
    for form in ("bug", "feature"):
        doc = load(ROOT / "templates" / ".github" / "ISSUE_TEMPLATE" / f"{form}.yml")
        assert set(doc["labels"]) <= names
        assert "needs-triage" in doc["labels"]
    bug = load(ROOT / "templates" / ".github" / "ISSUE_TEMPLATE" / "bug.yml")
    sev = next(b for b in bug["body"] if b.get("id") == "severity")
    assert [o[:2] for o in sev["attributes"]["options"]] == ["S1", "S2", "S3", "S4"]
    assert load(ROOT / "templates" / ".github" / "ISSUE_TEMPLATE" / "config.yml")["blank_issues_enabled"] is False


def test_pr_template_matches_the_body_check():
    text = (ROOT / "templates" / ".github" / "pull_request_template.md").read_text()
    assert fmt.check_body(text)  # untouched template warns
    assert fmt.check_body(text.replace("Fixes #\n", "Fixes #34\n")) == []
    assert fmt.check_body(text.replace("- [ ] No issue needed", "- [x] No issue needed")) == []
    # the template names no agent, ticked or not (git.md 6)
    assert "AI assistant" not in text
    assert fmt.check_no_agent(text.replace("[ ]", "[x]")) == []


def test_own_pr_template_is_the_template():
    own = (ROOT / ".github" / "pull_request_template.md").read_text()
    assert own == (ROOT / "templates" / ".github" / "pull_request_template.md").read_text()
    for line in ("It changes a UI:", "It changes snapshots or golden files:"):
        assert line in own


def test_dependabot_template():
    doc = load(ROOT / "templates" / ".github" / "dependabot.yml")
    for u in doc["updates"]:
        assert u["target-branch"] == "dev"
        assert u["labels"] == ["deps"]
        assert u["commit-message"]["prefix"] == "chore(deps)"
        assert u["groups"]
    assert fmt.check_subject("chore(deps): bump the github-actions group with 2 updates") == []


def test_pre_commit_files():
    hooks = load(ROOT / ".pre-commit-hooks.yaml")
    assert [h["id"] for h in hooks] == ["gitleaks", "issue-ref", "commit-format"]
    for h in hooks:
        assert (ROOT / h["entry"]).exists()
    tpl = load(ROOT / "templates" / ".pre-commit-config.yaml")
    assert tpl["repos"][0]["rev"] == "v1.0.0"
    assert set(tpl["default_install_hook_types"]) == {"pre-commit", "prepare-commit-msg", "commit-msg"}


def test_gitignore_template_covers_section_11():
    lines = set((ROOT / "templates" / "gitignore").read_text().splitlines())
    for must in (".env", ".env.*", "*.key", ".venv/", "node_modules/", "dist/", ".omicron/", ".claude/", "*.bak"):
        assert must in lines, must


def test_every_path_in_template_list_exists():
    from tmflow import repoinit
    for rel in repoinit.TEMPLATE_FILES:
        assert (ROOT / "templates" / rel).exists(), rel
    assert Path(ROOT / "templates" / "gitignore").exists()


def test_checks_fail_on_a_coauthor_trailer():
    steps = load(WF / "checks.yml")["jobs"]["checks"]["steps"]
    trailer = [s for s in steps if "check-trailers" in s.get("run", "")]
    assert len(trailer) == 1
    assert "continue-on-error" not in trailer[0]
    assert trailer[0]["if"] == "${{ !cancelled() }}"
