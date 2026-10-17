"""The whole flow against a local bare repo, through the CLI the workflows call.

GitHub is replaced by LocalForge: merges are done the way GitHub's merge button
does them, and closing keywords close issues only on the default branch. Those
two simulations are what build.md S3 must verify on GitHub itself.
"""
import json
import subprocess

import pytest

from conftest import HAVE_GITLEAKS, ROOT, commit_file, git
from tmflow import cli, gitutil
from tmflow.forge import LocalForge

pytestmark = pytest.mark.skipif(not HAVE_GITLEAKS, reason="gitleaks binary not installed")
REPO = "Techmirion/sandbox"


@pytest.fixture
def world(tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    commit_file(seed, "README.md", "sandbox\n", "chore(repo): start")
    git(seed, "push", "-q", "origin", "main")
    git(seed, "push", "-q", "origin", "main:dev")
    me = tmp_path / "me"
    git(tmp_path, "clone", "-q", str(origin), str(me))
    subprocess.run(["sh", str(ROOT / "scripts" / "install-hooks.sh"), str(me)], check=True, capture_output=True)
    forge = LocalForge(REPO, str(origin), str(tmp_path / "state.json"), str(tmp_path / "bot"))
    return {"tmp": tmp_path, "origin": origin, "me": me, "forge": forge,
            "fargs": ["--forge", "local", "--origin", str(origin), "--state", str(tmp_path / "state.json"),
                      "--workdir", str(tmp_path / "bot")]}


def tm(world, *args):
    cmd, rest = args[0], list(args[1:])
    return cli.main([cmd, "--repo", REPO, *world["fargs"], *rest])


def git_args(world):
    return ["--git-dir", str(world["tmp"] / "bot"), "--fetch"]


def work(world, branch, base, *subjects):
    me = world["me"]
    git(me, "fetch", "-q", "origin")
    git(me, "checkout", "-q", "-B", branch, f"origin/{base}")
    for i, s in enumerate(subjects):
        commit_file(me, f"{branch.replace('/', '_')}_{i}.txt", s, s)
    git(me, "push", "-q", "origin", branch)
    return git(me, "log", "-1", "--format=%s").stdout.strip()


def remote(world, ref):
    return git(world["origin"], "rev-parse", ref).stdout.strip()


def test_issue_to_release_to_hotfix(world, capsys):
    f = world["forge"]

    # issue -> branch -> commits with (#N) -> PR into dev -> merge
    n = f.create_issue("login redirect is wrong", ["bug"])
    assert work(world, f"fix/{n}-login-redirect", "dev", "fix(auth): redirect after login") == f"fix(auth): redirect after login (#{n})"
    pr = f.create_pr(f"fix(auth): redirect after login (#{n})", f"Fixes #{n}", f"fix/{n}-login-redirect", "dev", user="fotis")
    f.merge_pr(pr["number"])
    assert f.issue(n)["state"] == "open"  # merging into dev does not close it (git.md 8)
    merge_subject = git(world["origin"], "log", "-1", "--format=%s", "dev").stdout.strip()
    assert merge_subject == f"Merge pull request #{pr['number']} from Techmirion/fix/{n}-login-redirect"

    # cleanup deletes the merged branch, never dev
    assert tm(world, "cleanup", "--pr", str(pr["number"])) == 0
    assert f.branch_sha(f"fix/{n}-login-redirect") is None

    # release-pr
    assert tm(world, "release-pr", "--version", "v0.1.0", "--summary", "first", *git_args(world)) == 0
    rel = f.open_prs(head="dev", base="main")[0]
    assert rel["title"] == "release: v0.1.0 first"
    assert rel["body"].startswith("Version: v0.1.0\n")
    assert f"Fixes #{n}" in rel["body"] and f"- #{pr['number']} fix(auth)" in rel["body"]
    assert tm(world, "release-pr", "--version", "v0.1.0", *git_args(world)) == 1  # already open

    # merge the release, then the release workflow: tag, Release, dev fast-forward
    sha = f.merge_pr(rel["number"])
    assert f.issue(n)["state"] == "closed"  # SIMULATED: GitHub closes Fixes #N on the default branch
    assert tm(world, "release", "--sha", sha, *git_args(world)) == 0
    assert git(world["origin"], "cat-file", "-t", "v0.1.0").stdout.strip() == "tag"  # annotated
    assert remote(world, "v0.1.0^{commit}") == sha
    assert remote(world, "dev") == remote(world, "main") == sha
    assert "v0.1.0" in json.loads((world["tmp"] / "state.json").read_text())["releases"]
    assert tm(world, "release", "--sha", sha, *git_args(world)) == 0  # rerun: nothing new
    assert tm(world, "cleanup", "--pr", str(rel["number"])) == 0
    assert f.branch_sha("dev") is not None  # never deleted

    # hotfix from main while dev moves on
    h = f.create_issue("SSE stream drops", ["bug", "severity:S2"])
    work(world, f"hotfix/{h}-sse", "main", "fix(sse): keep-alive pings")
    hp = f.create_pr(f"fix(sse): keep-alive pings (#{h})", f"Fixes #{h}", f"hotfix/{h}-sse", "main", user="fotis")
    k = f.create_issue("greek search", ["feature"])
    work(world, f"feat/{k}-search", "dev", "feat(search): greek stemming")
    kp = f.create_pr(f"feat(search): greek stemming (#{k})", f"Fixes #{k}", f"feat/{k}-search", "dev", user="giannis")
    f.merge_pr(kp["number"])
    hsha = f.merge_pr(hp["number"])
    assert tm(world, "release", "--sha", hsha, *git_args(world)) == 0
    assert remote(world, "v0.1.1^{commit}") == hsha
    sync = f.open_prs(head="main", base="dev")
    assert len(sync) == 1 and sync[0]["title"] == "chore(release): sync main into dev"

    # no release while the hotfix is not back in dev
    assert tm(world, "release-pr", "--version", "v0.2.0", *git_args(world)) == 1
    assert "main is not an ancestor of dev" in capsys.readouterr().err

    f.merge_pr(sync[0]["number"])
    assert tm(world, "cleanup", "--pr", str(sync[0]["number"])) == 0
    assert f.branch_sha("main") is not None
    assert tm(world, "release-pr", "--version", "v0.1.1", *git_args(world)) == 1  # not above v0.1.1
    assert tm(world, "release-pr", "--version", "v0.2.0", *git_args(world)) == 0
    body = f.open_prs(head="dev", base="main")[0]["body"]
    assert f"Fixes #{k}" in body and f"Fixes #{h}" not in body and "sync main into dev" not in body


def test_sync_dev_outside_the_release_flow(world):
    """A repo without release.yml: dev merged into main by hand, then sync-dev."""
    f, me = world["forge"], world["me"]
    work(world, "feat/site", "dev", "feat(site): first page")
    p = f.create_pr("feat(site): first page", "- [x] No issue needed", "feat/site", "dev")
    f.merge_pr(p["number"])
    git(me, "fetch", "-q", "origin")
    git(me, "checkout", "-q", "-B", "main", "origin/main")
    git(me, "merge", "-q", "--no-ff", "-m", "feat(site): merge dev into main", "origin/dev")
    git(me, "push", "-q", "origin", "main")
    sha = remote(world, "main")
    assert remote(world, "dev") != sha

    assert tm(world, "sync-dev", "--sha", sha, *git_args(world)) == 0
    assert remote(world, "dev") == sha  # fast-forwarded
    assert git(world["origin"], "tag").stdout.strip() == ""  # never tags
    assert tm(world, "sync-dev", "--sha", sha, *git_args(world)) == 0  # rerun: nothing to do
    assert f.open_prs(head="main", base="dev") == []

    # dev moves on before the sync runs: one sync PR, never two
    git(me, "commit", "-q", "--allow-empty", "-m", "chore(site): straight on main")
    git(me, "push", "-q", "origin", "main")
    work(world, "chore/more", "dev", "chore(site): more")
    f.merge_pr(f.create_pr("chore(site): more", "- [x] No issue needed", "chore/more", "dev")["number"])
    sha2 = remote(world, "main")
    assert tm(world, "sync-dev", "--sha", sha2, *git_args(world)) == 0
    assert tm(world, "sync-dev", "--sha", sha2, *git_args(world)) == 0
    (sync,) = f.open_prs(head="main", base="dev")
    assert sync["title"] == "chore(release): sync main into dev"
    assert "a push without a pull request" in sync["body"]
    f.merge_pr(sync["number"])
    assert gitutil.ok("merge-base", "--is-ancestor", sha2, remote(world, "dev"), cwd=str(world["origin"]))


def test_first_parent_commits_reads_pr_merges(world):
    f = world["forge"]
    work(world, "chore/one", "dev", "chore(a): one")
    p = f.create_pr("chore(a): one", "- [x] No issue needed", "chore/one", "dev")
    f.merge_pr(p["number"])
    bot = world["tmp"] / "bot"
    git(bot, "fetch", "-q", "origin")
    items = gitutil.first_parent_commits("origin/main", "origin/dev", cwd=bot)
    assert [(i["number"], i["head"], i["body"]) for i in items] == [(p["number"], "Techmirion/chore/one", "chore(a): one")]
