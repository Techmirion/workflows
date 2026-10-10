"""Format checks from GitHub events (warnings only), and the gitleaks range."""
import json
import os
import subprocess
import sys

from conftest import ROOT, commit_file, git
from tmflow import checks, gitutil


def sha(repo, ref="HEAD"):
    return git(repo, "rev-parse", ref).stdout.strip()


def pr_event(repo, base_sha, head_sha, head="fix/34-login", base="dev", title="fix(auth): redirect (#34)",
             body="Fixes #34", user="fotis"):
    return {"pull_request": {
        "number": 12, "title": title, "body": body, "user": {"login": user},
        "head": {"ref": head, "sha": head_sha, "repo": {"full_name": "T/app"}},
        "base": {"ref": base, "sha": base_sha, "repo": {"full_name": "T/app"}},
    }}


def branch_with(repo, name, *subjects):
    git(repo, "checkout", "-q", "-b", name)
    for i, s in enumerate(subjects):
        commit_file(repo, f"{name.replace('/', '_')}{i}.txt", str(i), s)
    return sha(repo)


def test_clean_pr_has_no_problems(repo):
    base = sha(repo)
    head = branch_with(repo, "fix/34-login", "fix(auth): redirect (#34)")
    assert checks.pr_problems(pr_event(repo, base, head), cwd=repo) == []


def test_bad_pr_lists_every_problem(repo):
    base = sha(repo)
    head = branch_with(repo, "Fotis-login", "fixed stuff", "fix(auth): fine (#34)")
    ev = pr_event(repo, base, head, head="Fotis-login", title="Login fix", body="no reference")
    problems = checks.pr_problems(ev, cwd=repo)
    text = "\n".join(problems)
    assert "branch `Fotis-login`" in text
    assert "PR title `Login fix`" in text
    assert "no `Fixes #N`" in text
    assert "`fixed stuff`" in text
    assert "fine (#34)" not in text


def test_merge_commits_in_a_pr_are_exempt(repo):
    git(repo, "checkout", "-q", "-b", "dev")
    base = sha(repo)
    branch_with(repo, "fix/34-login", "fix(auth): one (#34)")
    git(repo, "checkout", "-q", "dev")
    commit_file(repo, "d.txt", "d", "chore(dev): other")
    git(repo, "checkout", "-q", "fix/34-login")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", "dev")
    head = sha(repo)
    assert checks.pr_problems(pr_event(repo, base, head), cwd=repo) == []


def test_dependabot_and_sync_prs_are_exempt(repo):
    base = sha(repo)
    head = branch_with(repo, "dependabot/github_actions/x", "Bump x")
    ev = pr_event(repo, base, head, head="dependabot/github_actions/x", title="Bump x", body="", user="dependabot[bot]")
    assert checks.pr_problems(ev, cwd=repo) == []
    ev = pr_event(repo, base, head, head="main", base="dev", title="chore(release): sync main into dev", body="")
    assert checks.pr_problems(ev, cwd=repo) == []


def test_release_pr_checks(repo):
    base = sha(repo)
    head = branch_with(repo, "dev", "fix(a): b (#3)")
    good = pr_event(repo, base, head, head="dev", base="main", title="release: v1.0.0 first", body="Version: v1.0.0")
    assert checks.pr_problems(good, cwd=repo) == []
    bad = pr_event(repo, base, head, head="dev", base="main", title="Release 1.0", body="")
    text = "\n".join(checks.pr_problems(bad, cwd=repo))
    assert "release: vX.Y.Z" in text and "Version: vX.Y.Z" in text


def test_only_hotfix_or_release_into_main(repo):
    base = sha(repo)
    head = branch_with(repo, "fix/34-login", "fix(auth): redirect (#34)")
    text = "\n".join(checks.pr_problems(pr_event(repo, base, head, base="main"), cwd=repo))
    assert "only release PRs" in text
    git(repo, "checkout", "-q", "-b", "hotfix/34-now")
    ev = pr_event(repo, base, head, head="hotfix/34-now", base="main", title="fix(auth): now (#34)")
    assert checks.pr_problems(ev, cwd=repo) == []


def test_release_title_outside_a_release_pr_warns(repo):
    base = sha(repo)
    head = branch_with(repo, "fix/34-login", "fix(auth): redirect (#34)")
    ev = pr_event(repo, base, head, title="release: v9.9.9 sneaky")
    assert any("only for the release PR" in p for p in checks.pr_problems(ev, cwd=repo))


def test_push_checks_range_and_first_push(repo):
    before = sha(repo)
    commit_file(repo, "a.txt", "1", "bad subject")
    commit_file(repo, "b.txt", "1", "fix(a): good (#1)")
    after = sha(repo)
    problems = checks.push_problems({"before": before, "after": after}, cwd=repo)
    assert len(problems) == 1 and "bad subject" in problems[0]
    # a new branch (before = zeros): only the head commit
    assert checks.push_problems({"before": gitutil.ZERO_SHA, "after": after}, cwd=repo) == []
    # a branch deletion: nothing
    assert checks.push_problems({"before": after, "after": gitutil.ZERO_SHA}, cwd=repo) == []


def test_scan_range():
    ev = {"pull_request": {"base": {"sha": "a"}, "head": {"sha": "b"}}}
    assert checks.scan_range("pull_request", ev) == "a..b"
    assert checks.scan_range("push", {"before": "a", "after": "b"}) == "a..b"
    assert checks.scan_range("push", {"before": gitutil.ZERO_SHA, "after": "b"}) is None
    assert checks.scan_range("workflow_dispatch", {}) is None


def test_report_in_actions(capsys, monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    checks.report(["branch `x` is bad\nreally", "100% wrong"])
    out = capsys.readouterr().out
    assert "::warning title=format::branch `x` is bad%0Areally" in out
    assert "::warning title=format::100%25 wrong" in out
    assert "never blocking" in summary.read_text()


def test_check_format_cli_never_fails(repo, tmp_path):
    base = sha(repo)
    head = branch_with(repo, "Bad", "bad subject")
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps(pr_event(repo, base, head, head="Bad", title="bad", body="")))
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "tm.py"), "check-format", "--event", str(ev),
                        "--event-name", "pull_request", "--git-dir", str(repo)],
                       capture_output=True, text=True, env={**os.environ, "GITHUB_ACTIONS": "true"})
    assert p.returncode == 0
    assert p.stdout.count("::warning") >= 3


def test_check_format_cli_survives_a_broken_event(tmp_path):
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps({"pull_request": {"head": {"ref": "x", "sha": None}, "base": {"ref": "dev", "sha": None},
                                               "title": "t", "body": "", "user": {"login": "u"}}}))
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "tm.py"), "check-format", "--event", str(ev),
                        "--event-name", "pull_request"], capture_output=True, text=True)
    assert p.returncode == 0
    ev.write_text("not json")
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "tm.py"), "check-format", "--event", str(ev),
                        "--event-name", "pull_request"], capture_output=True, text=True)
    assert p.returncode == 0 and "could not run" in p.stdout
