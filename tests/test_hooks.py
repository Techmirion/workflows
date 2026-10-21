"""The three git hooks in a real temporary git repo (git.md 5 and 11)."""
import os
import random
import shutil
import string
import subprocess
import textwrap
from pathlib import Path

import pytest

from conftest import GITLEAKS, HAVE_GITLEAKS, ROOT, commit_file, git, subject

pytestmark = pytest.mark.skipif(not HAVE_GITLEAKS, reason="gitleaks binary not installed")


def env_with_gitleaks():
    d = os.path.dirname(GITLEAKS)
    return {"PATH": d + os.pathsep + os.environ["PATH"]}


def test_install_puts_three_hooks_and_the_library(hooked_repo):
    hooks = hooked_repo / ".git" / "hooks"
    for h in ("pre-commit", "prepare-commit-msg", "commit-msg"):
        assert os.access(hooks / h, os.X_OK)
        assert "techmirion-hook" in (hooks / h).read_text()
    assert (hooks / "tmflow" / "fmt.py").exists()


def test_ref_added_from_branch(hooked_repo):
    git(hooked_repo, "checkout", "-q", "-b", "fix/34-login-redirect")
    commit_file(hooked_repo, "a.txt", "1", "fix(auth): redirect after login", env=env_with_gitleaks())
    assert subject(hooked_repo) == "fix(auth): redirect after login (#34)"


def test_ref_not_duplicated_and_merged_into_group(hooked_repo):
    env = env_with_gitleaks()
    git(hooked_repo, "checkout", "-q", "-b", "fix/34-login-redirect")
    commit_file(hooked_repo, "a.txt", "1", "fix(auth): one (#34)", env=env)
    assert subject(hooked_repo) == "fix(auth): one (#34)"
    commit_file(hooked_repo, "b.txt", "1", "fix(auth): two (#40)", env=env)
    assert subject(hooked_repo) == "fix(auth): two (#34, #40)"


def test_amend_keeps_a_single_ref(hooked_repo):
    env = env_with_gitleaks()
    git(hooked_repo, "checkout", "-q", "-b", "feat/7-search")
    commit_file(hooked_repo, "a.txt", "1", "feat(search): greek stemming", env=env)
    git(hooked_repo, "commit", "-q", "--amend", "--no-edit", env=env)
    assert subject(hooked_repo) == "feat(search): greek stemming (#7)"


def test_chore_branch_gets_no_ref(hooked_repo):
    git(hooked_repo, "checkout", "-q", "-b", "chore/ci-pins")
    commit_file(hooked_repo, "a.txt", "1", "chore(ci): pin actions", env=env_with_gitleaks())
    assert subject(hooked_repo) == "chore(ci): pin actions"


def test_bad_subject_refused(hooked_repo):
    git(hooked_repo, "checkout", "-q", "-b", "fix/34-x")
    head = git(hooked_repo, "rev-parse", "HEAD").stdout
    p = commit_file(hooked_repo, "a.txt", "1", "fixed the login", check=False, env=env_with_gitleaks())
    assert p.returncode != 0
    assert "type(place): message (#N)" in p.stderr
    assert git(hooked_repo, "rev-parse", "HEAD").stdout == head


def test_warn_mode_keeps_the_commit_and_says_why(hooked_repo):
    git(hooked_repo, "config", "techmirion.hooks", "warn")
    git(hooked_repo, "checkout", "-q", "-b", "fix/34-x")
    p = commit_file(hooked_repo, "a.txt", "1", "fixed the login\n\nCo-Authored-By: Bot <b@example.org>",
                    check=False, env=env_with_gitleaks())
    assert p.returncode == 0
    assert "techmirion.hooks=warn" in p.stderr and "doctor --fix" in p.stderr
    assert "Co-Authored-By" in p.stderr
    assert subject(hooked_repo).startswith("fixed the login")


def test_warn_mode_still_scans_for_secrets(hooked_repo):
    git(hooked_repo, "config", "techmirion.hooks", "warn")
    head = git(hooked_repo, "rev-parse", "HEAD").stdout
    key = "AKIA" + "".join(random.choice(string.ascii_uppercase + "234567") for _ in range(16))
    p = commit_file(hooked_repo, "creds.py", f'aws_key = "{key}"\n', "chore(x): y", check=False, env=env_with_gitleaks())
    if p.returncode == 0:
        pytest.skip("gitleaks did not flag the sample key")
    assert git(hooked_repo, "rev-parse", "HEAD").stdout == head


def test_closing_keyword_in_commit_refused(hooked_repo):
    git(hooked_repo, "checkout", "-q", "-b", "fix/34-x")
    Path(hooked_repo, "a.txt").write_text("1")
    git(hooked_repo, "add", "a.txt")
    p = git(hooked_repo, "commit", "-q", "-m", "fix(auth): redirect", "-m", "Fixes #34", check=False, env=env_with_gitleaks())
    assert p.returncode != 0
    assert "do not use Fixes" in p.stderr


def test_editor_flow_gets_ref_from_commit_msg(hooked_repo, tmp_path):
    editor = tmp_path / "editor.sh"
    editor.write_text('#!/bin/sh\n{ echo "docs(readme): explain the loop"; cat "$1"; } > "$1.new" && mv "$1.new" "$1"\n')
    editor.chmod(0o755)
    git(hooked_repo, "checkout", "-q", "-b", "docs/12-readme")
    Path(hooked_repo, "a.txt").write_text("1")
    git(hooked_repo, "add", "a.txt")
    git(hooked_repo, "commit", "-q", env={**env_with_gitleaks(), "GIT_EDITOR": str(editor)})
    assert subject(hooked_repo) == "docs(readme): explain the loop (#12)"


def test_merge_no_ff_passes_without_ref(hooked_repo):
    env = env_with_gitleaks()
    git(hooked_repo, "checkout", "-q", "-b", "dev")
    git(hooked_repo, "checkout", "-q", "-b", "fix/34-x")
    commit_file(hooked_repo, "a.txt", "1", "fix(a): b", env=env)
    git(hooked_repo, "checkout", "-q", "dev")
    git(hooked_repo, "merge", "-q", "--no-ff", "--no-edit", "-m",
        "Merge pull request #12 from Techmirion/fix/34-x", "fix/34-x", env=env)
    assert subject(hooked_repo) == "Merge pull request #12 from Techmirion/fix/34-x"
    assert len(git(hooked_repo, "log", "-1", "--format=%P").stdout.split()) == 2


def test_merge_on_issue_branch_does_not_get_ref(hooked_repo):
    env = env_with_gitleaks()
    git(hooked_repo, "checkout", "-q", "-b", "dev")
    commit_file(hooked_repo, "d.txt", "1", "chore(dev): something", env=env)
    git(hooked_repo, "checkout", "-q", "-b", "fix/34-x", "main")
    commit_file(hooked_repo, "a.txt", "1", "fix(a): b", env=env)
    git(hooked_repo, "merge", "-q", "--no-ff", "--no-edit", "dev", env=env)
    assert subject(hooked_repo) == "Merge branch 'dev' into fix/34-x"


def test_revert_passes(hooked_repo):
    env = env_with_gitleaks()
    git(hooked_repo, "checkout", "-q", "-b", "fix/34-x")
    commit_file(hooked_repo, "a.txt", "1", "fix(a): b", env=env)
    git(hooked_repo, "revert", "--no-edit", "HEAD", env=env)
    assert subject(hooked_repo) == 'Revert "fix(a): b (#34)"'


def test_fixup_allowed_locally(hooked_repo):
    env = env_with_gitleaks()
    git(hooked_repo, "checkout", "-q", "-b", "fix/34-x")
    commit_file(hooked_repo, "a.txt", "1", "fix(a): b", env=env)
    Path(hooked_repo, "a.txt").write_text("2")
    git(hooked_repo, "add", "a.txt")
    git(hooked_repo, "commit", "-q", "--fixup", "HEAD", env=env)
    assert subject(hooked_repo) == "fixup! fix(a): b (#34)"


def _fake_token():
    rnd = random.Random(7)
    return "ghp_" + "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(36))


def test_gitleaks_refuses_a_staged_secret(hooked_repo):
    git(hooked_repo, "checkout", "-q", "-b", "fix/34-x")
    head = git(hooked_repo, "rev-parse", "HEAD").stdout
    p = commit_file(hooked_repo, "config.py", f'TOKEN = "{_fake_token()}"\n', "fix(a): b", check=False, env=env_with_gitleaks())
    assert p.returncode != 0
    assert git(hooked_repo, "rev-parse", "HEAD").stdout == head
    assert _fake_token() not in p.stdout + p.stderr  # --redact


def test_no_gitleaks_means_no_commit(hooked_repo, tmp_path):
    bare_path = "/usr/bin:/bin"
    assert shutil.which("gitleaks", path=bare_path) is None
    env = {"PATH": f"{os.path.dirname(shutil.which('git'))}:{os.path.dirname(shutil.which('python3'))}:{bare_path}",
           "HOME": str(tmp_path)}
    p = commit_file(hooked_repo, "a.txt", "1", "chore(a): b", check=False, env=env)
    assert p.returncode != 0
    assert "gitleaks is not installed" in p.stderr


def test_foreign_hook_kept_and_uninstall(repo):
    hooks = repo / ".git" / "hooks"
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    p = subprocess.run(["sh", str(ROOT / "scripts" / "install-hooks.sh"), str(repo)], capture_output=True, text=True)
    assert p.returncode == 0
    assert (hooks / "pre-commit.pre-techmirion").read_text() == "#!/bin/sh\nexit 0\n"
    p = subprocess.run(["sh", str(ROOT / "scripts" / "install-hooks.sh"), "--uninstall", str(repo)], capture_output=True, text=True)
    assert p.returncode == 0
    assert not (hooks / "pre-commit").exists() and not (hooks / "commit-msg").exists()
    assert (hooks / "pre-commit.pre-techmirion").exists()


@pytest.mark.skipif(shutil.which("pre-commit") is None, reason="pre-commit not installed")
def test_same_hooks_through_pre_commit(tmp_path):
    """The .pre-commit-hooks.yaml of this repo, used by a consumer repo through pre-commit."""
    src = tmp_path / "workflows"
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    git(src, "init", "-q", "-b", "main")
    git(src, "add", "-A")
    git(src, "commit", "-q", "--no-verify", "-m", "chore(test): snapshot")
    rev = git(src, "rev-parse", "HEAD").stdout.strip()
    app = tmp_path / "app"
    app.mkdir()
    git(app, "init", "-q", "-b", "main")
    (app / ".pre-commit-config.yaml").write_text(textwrap.dedent(f"""\
        default_install_hook_types: [pre-commit, prepare-commit-msg, commit-msg]
        repos:
          - repo: {src}
            rev: {rev}
            hooks:
              - id: gitleaks
              - id: issue-ref
              - id: commit-format
        """))
    env = {**env_with_gitleaks(), "PRE_COMMIT_HOME": str(tmp_path / "pc-cache")}
    p = subprocess.run(["pre-commit", "install"], cwd=app, capture_output=True, text=True, env={**os.environ, **env})
    assert p.returncode == 0, p.stderr + p.stdout
    git(app, "checkout", "-q", "-b", "feat/5-thing")
    commit_file(app, "a.txt", "1", "feat(ui): a thing", env=env)
    assert subject(app) == "feat(ui): a thing (#5)"
    p = commit_file(app, "b.txt", "1", "bad message", check=False, env=env)
    assert p.returncode != 0


def test_coauthor_trailer_refused(hooked_repo):
    env = env_with_gitleaks()
    git(hooked_repo, "checkout", "-q", "-b", "chore/x")
    head = git(hooked_repo, "rev-parse", "HEAD").stdout
    Path(hooked_repo, "a.txt").write_text("1")
    git(hooked_repo, "add", "a.txt")
    p = git(hooked_repo, "commit", "-q", "-m", "chore(x): fine subject", "-m",
            "Co-Authored-By: Someone <s@example.invalid>", check=False, env=env)
    assert p.returncode != 0
    assert "Co-Authored-By" in p.stderr
    assert "format:" not in p.stderr  # the subject itself was fine
    assert git(hooked_repo, "rev-parse", "HEAD").stdout == head


def test_agent_line_refused(hooked_repo):
    env = env_with_gitleaks()
    git(hooked_repo, "checkout", "-q", "-b", "chore/x")
    Path(hooked_repo, "a.txt").write_text("1")
    git(hooked_repo, "add", "a.txt")
    p = git(hooked_repo, "commit", "-q", "-m", "chore(x): fine subject", "-m",
            "Claude-Session: https://claude.ai/code/session_x", check=False, env=env)
    assert p.returncode != 0
    assert "names the agent" in p.stderr


def test_coauthor_trailer_refused_on_a_merge(hooked_repo):
    env = env_with_gitleaks()
    git(hooked_repo, "checkout", "-q", "-b", "dev")
    git(hooked_repo, "checkout", "-q", "-b", "chore/x")
    commit_file(hooked_repo, "a.txt", "1", "chore(x): one", env=env)
    git(hooked_repo, "checkout", "-q", "dev")
    p = git(hooked_repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'chore/x' into dev", "-m",
            "Co-authored-by: Someone <s@example.invalid>", "chore/x", check=False, env=env)
    assert p.returncode != 0
    assert "Co-Authored-By" in p.stderr
    git(hooked_repo, "merge", "--abort")
    git(hooked_repo, "merge", "-q", "--no-ff", "--no-edit", "chore/x", env=env)
    assert subject(hooked_repo) == "Merge branch 'chore/x' into dev"


def test_coauthor_below_the_scissors_is_ignored(hooked_repo, tmp_path):
    editor = tmp_path / "editor.sh"
    editor.write_text('#!/bin/sh\n{ echo "chore(x): verbose"; cat "$1"; } > "$1.new" && mv "$1.new" "$1"\n')
    editor.chmod(0o755)
    Path(hooked_repo, "a.txt").write_text("Co-Authored-By: in the diff only\n")
    git(hooked_repo, "add", "a.txt")
    git(hooked_repo, "commit", "-q", "-v", env={**env_with_gitleaks(), "GIT_EDITOR": str(editor)})
    assert subject(hooked_repo) == "chore(x): verbose"
