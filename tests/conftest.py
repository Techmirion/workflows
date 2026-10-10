import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

GITLEAKS = shutil.which("gitleaks") or str(Path.home() / ".local" / "bin" / "gitleaks")
HAVE_GITLEAKS = os.path.exists(GITLEAKS)


@pytest.fixture(autouse=True)
def isolated_git(tmp_path_factory, monkeypatch):
    """Every test gets its own global git config: no user hooks path, no signing."""
    home = tmp_path_factory.mktemp("home")
    cfg = home / "gitconfig"
    cfg.write_text(
        "[user]\n\tname = Test Person\n\temail = test@example.invalid\n"
        "[init]\n\tdefaultBranch = main\n"
        "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n"
        "[advice]\n\tdetachedHead = false\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(k, raising=False)
    yield


def git(cwd, *args, check=True, env=None, input=None):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       env={**os.environ, **(env or {})}, input=input)
    if check and p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}{p.stdout}")
    return p


def commit_file(cwd, name, text, msg, check=True, env=None):
    Path(cwd, name).write_text(text)
    git(cwd, "add", name)
    return git(cwd, "commit", "-q", "-m", msg, check=check, env=env)


def subject(cwd, ref="HEAD"):
    return git(cwd, "log", "-1", "--format=%s", ref).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    commit_file(d, "README.md", "hello\n", "chore(repo): start")
    return d


@pytest.fixture
def hooked_repo(repo):
    p = subprocess.run(["sh", str(ROOT / "scripts" / "install-hooks.sh"), str(repo)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return repo
