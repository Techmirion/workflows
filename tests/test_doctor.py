"""tm doctor in real temporary repos (git.md 4, 5, 11; ai-guardrails.md 8)."""
import json
import subprocess
import sys

import pytest

from conftest import ROOT, commit_file, git, subject
from tmflow import doctor

TM = [sys.executable, str(ROOT / "scripts" / "tm.py"), "doctor"]


@pytest.fixture
def flow(tmp_path, repo):
    """A clone with an origin that has main and dev, on a fresh issue branch."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "push", "-q", "origin", "main:dev")
    git(repo, "fetch", "-q", "origin")
    git(repo, "checkout", "-q", "-b", "fix/34-login-redirect", "origin/dev")
    return repo


def run(path, *args):
    p = subprocess.run([*TM, str(path), "--profile", "flow", "--no-pr", "--json", *args],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def codes(d):
    return sorted(f["code"] for f in d["findings"])


def message(cwd, ref="HEAD"):
    return git(cwd, "log", "-1", "--format=%B", ref).stdout


def test_clean_branch_has_no_errors_or_warnings(flow):
    commit_file(flow, "a.txt", "1", "fix(auth): redirect after login (#34)")
    d = run(flow)
    assert d["counts"]["error"] == 0 and d["counts"]["warn"] == 0
    assert d["profile"] == "flow" and d["base"] == "origin/dev" and d["unpushed"] == 1


def test_fix_rewords_unpushed_commits_and_keeps_tree_and_author(flow):
    commit_file(flow, "a.txt", "1", "fix(auth): redirect after login\n\nFixes #34\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
                "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n",
                env={"GIT_AUTHOR_NAME": "Real Person", "GIT_AUTHOR_EMAIL": "real@example.org"})
    tree = git(flow, "rev-parse", "HEAD^{tree}").stdout
    before = run(flow)
    assert {"C2", "C3", "C4", "C6"} <= set(codes(before))
    after = run(flow, "--fix")
    assert any("reworded 1" in x for x in after["fixed"])
    assert message(flow).strip() == "fix(auth): redirect after login (#34)"
    assert git(flow, "rev-parse", "HEAD^{tree}").stdout == tree
    assert git(flow, "log", "-1", "--format=%an <%ae>").stdout.strip() == "Real Person <real@example.org>"
    assert after["counts"]["error"] == 0 and after["counts"]["warn"] == 0


def test_pushed_commits_are_never_rewritten(flow):
    commit_file(flow, "a.txt", "1", "fix(auth): one (#34)\n\nCo-Authored-By: Bot <b@example.org>\n")
    git(flow, "push", "-q", "-u", "origin", "HEAD")
    head = git(flow, "rev-parse", "HEAD").stdout
    d = run(flow, "--fix")
    assert git(flow, "rev-parse", "HEAD").stdout == head
    c3 = [f for f in d["findings"] if f["code"] == "C3"]
    assert c3 and c3[0]["auto"] is None and "already pushed" in c3[0]["message"]


def test_merge_in_range_is_kept_when_an_earlier_commit_is_reworded(flow):
    commit_file(flow, "a.txt", "1", "fix(auth): one\n\nCo-Authored-By: Bot <b@example.org>\n")
    git(flow, "checkout", "-q", "-b", "side", "origin/dev")
    commit_file(flow, "b.txt", "1", "fix(auth): side (#34)")
    git(flow, "checkout", "-q", "fix/34-login-redirect")
    git(flow, "merge", "-q", "--no-ff", "side", "-m", "Merge branch 'side'")
    run(flow, "--fix")
    parents = git(flow, "log", "-1", "--format=%P").stdout.split()
    assert len(parents) == 2
    assert subject(flow, "HEAD^1") == "fix(auth): one (#34)"
    assert "Co-Authored-By" not in message(flow, "HEAD^1")
    assert subject(flow, "HEAD^2") == "fix(auth): side (#34)"


def test_bad_subject_is_reported_not_rewritten(flow):
    commit_file(flow, "a.txt", "1", "Fixed the login thing")
    d = run(flow, "--fix")
    c1 = [f for f in d["findings"] if f["code"] == "C1"]
    assert c1 and c1[0]["level"] == "warn" and c1[0]["auto"] is None
    assert subject(flow) == "Fixed the login thing"


def test_branch_rename_only_when_not_on_a_remote(flow):
    git(flow, "branch", "-m", "Fix/34-Login-Redirect")
    commit_file(flow, "a.txt", "1", "fix(auth): one (#34)")
    d = run(flow, "--fix")
    assert "renamed branch Fix/34-Login-Redirect to fix/34-login-redirect" in d["fixed"]
    assert d["branch"] == "fix/34-login-redirect"


def test_branch_without_issue_gets_a_suggestion(flow):
    git(flow, "branch", "-m", "feat/greek-search")
    d = run(flow)
    b1 = [f for f in d["findings"] if f["code"] == "B1"][0]
    assert b1["auto"] is None and "feat/N-greek-search" in b1["fix"]


def test_staged_secret_is_unstaged_never_deleted(flow):
    (flow / ".env").write_text("TOKEN=x\n")
    git(flow, "add", ".env")
    d = run(flow)
    f1 = [f for f in d["findings"] if f["code"] == "F1"][0]
    assert f1["level"] == "error" and "TOKEN" not in json.dumps(d)
    d = run(flow, "--fix")
    assert "F1" not in codes(d)
    assert (flow / ".env").exists()
    assert git(flow, "diff", "--cached", "--name-only").stdout.strip() == ""


def test_committed_junk_is_reported(flow):
    (flow / "node_modules").mkdir()
    commit_file(flow, "node_modules/x.js", "1", "chore(deps): oops")
    assert "F2" in codes(run(flow))


def test_env_example_is_fine(flow):
    commit_file(flow, ".env.example", "TOKEN=\n", "docs(env): example (#34)")
    assert "F1" not in codes(run(flow))


def test_live_profile_allows_hub_task_keywords(flow):
    git(flow, "checkout", "-q", "-B", "dev", "origin/dev")
    commit_file(flow, "a.txt", "1", "fix(nav): one, closes #28")
    p = subprocess.run([*TM, str(flow), "--profile", "live", "--no-pr", "--json"], capture_output=True, text=True)
    d = json.loads(p.stdout)
    assert "C4" not in codes(d) and "L1" in codes(d)


def test_strict_exit_code(flow):
    commit_file(flow, "a.txt", "1", "bad subject")
    p = subprocess.run([*TM, str(flow), "--profile", "flow", "--no-pr", "--strict"], capture_output=True, text=True)
    assert p.returncode == 1
    p = subprocess.run([*TM, str(flow), "--profile", "flow", "--no-pr"], capture_output=True, text=True)
    assert p.returncode == 0 and "C1" in p.stdout


def test_standalone_copy_runs_without_the_rest_of_tmflow(flow, tmp_path):
    pkg = tmp_path / "skill" / "tmflow"
    pkg.mkdir(parents=True)
    for n in ("__init__.py", "fmt.py", "doctor.py"):
        (pkg / n).write_text((ROOT / "scripts" / "tmflow" / n).read_text())
    p = subprocess.run([sys.executable, str(pkg / "doctor.py"), str(flow), "--no-pr"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "tm doctor" in p.stdout


@pytest.mark.parametrize("line", [
    "Co-Authored-By: Claude <noreply@anthropic.com>",
    "Claude-Session: abc",
    "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
])
def test_fixed_message_drops_assistant_lines(line):
    out = doctor.fixed_message(f"fix(a): b\n\n{line}\n", issue=3, add_ref=True, drop_closing=True)
    assert out == "fix(a): b (#3)\n"


def test_fixed_message_keeps_a_bad_subject_word_for_word():
    assert doctor.fixed_message("Fixed it\n", issue=3, add_ref=True, drop_closing=True) == "Fixed it\n"
