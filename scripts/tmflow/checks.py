"""The format checks of the `checks` workflow (git.md 14), from a GitHub event.

Every problem is a warning. Nothing here fails the job: the free plan cannot
make a format rule blocking, so the owner holds the line (git.md 6).
"""
from __future__ import annotations

import os

from . import fmt, gitutil
from .releaselib import is_release_pr, is_sync_pr


def _is_bot(login: str | None) -> bool:
    return bool(login) and login.endswith("[bot]")


def commit_problems(rng: str, cwd: str | None = None) -> list[str]:
    problems = []
    for c in gitutil.commits(rng, cwd=cwd):
        if len(c["parents"]) > 1:
            continue  # merge commits are exempt
        if c["author"] == "dependabot[bot]" or c["email"].endswith("dependabot[bot]@users.noreply.github.com"):
            continue
        for p in fmt.check_subject(c["subject"]):
            problems.append(f"commit {c['sha'][:7]}: {p}")
        if not fmt.subject_exempt(c["subject"]):
            refs = fmt.closing_refs(c["subject"] + "\n" + c.get("body", ""))
            if refs:
                problems.append(f"commit {c['sha'][:7]} closes {', '.join(refs)}: commits do not use "
                                "Fixes/Closes (git.md 5), the PR body does")
    return problems


def pr_problems(event: dict, cwd: str | None = None) -> list[str]:
    pr = event["pull_request"]
    head, base = pr["head"]["ref"], pr["base"]["ref"]
    title, body = pr.get("title") or "", pr.get("body") or ""
    user = (pr.get("user") or {}).get("login")
    view = {"head": head, "base": base}
    dependabot = head.startswith("dependabot/") or user == "dependabot[bot]"
    sync, release = is_sync_pr(view), is_release_pr(view)
    problems: list[str] = []
    if dependabot or sync:
        return problems
    if release:
        if not fmt.RELEASE_TITLE_RE.match(title):
            problems.append("a release PR (dev -> main) is titled `release: vX.Y.Z <summary>`")
        if fmt.version_from_body(body) is None:
            problems.append("a release PR needs a `Version: vX.Y.Z` line in its body")
        return problems
    problems += fmt.check_branch(head)
    problems += fmt.check_title(title)
    if fmt.RELEASE_TITLE_RE.match(title):
        problems.append("`release:` titles are only for the release PR from dev into main")
    if base == "main" and not head.startswith("hotfix/"):
        problems.append(f"only release PRs (from dev) and hotfix/* PRs go into main, not `{head}`")
    if base not in ("dev", "main"):
        problems.append(f"PRs go into dev (or a hotfix into main), not `{base}`")
    problems += fmt.check_body(body, branch_issue=fmt.issue_from_branch(head))
    base_sha, head_sha = pr["base"].get("sha"), pr["head"].get("sha")
    if not base_sha or not head_sha:
        problems.append("the event has no base or head commit: commits not checked")
        return problems
    try:
        problems += commit_problems(f"{base_sha}..{head_sha}", cwd=cwd)
    except gitutil.GitError as e:
        problems.append(f"could not read the commits {base_sha[:7]}..{head_sha[:7]}: {e}")
    return problems


def push_range(event: dict) -> str | None:
    """The commits a push added, as a git range, or None to mean "the head commit only"."""
    before, after = event.get("before"), event.get("after")
    if not after or after == gitutil.ZERO_SHA:
        return None
    if not before or before == gitutil.ZERO_SHA:
        return None
    return f"{before}..{after}"


def push_problems(event: dict, cwd: str | None = None) -> list[str]:
    after = event.get("after")
    if not after or after == gitutil.ZERO_SHA:
        return []
    rng = push_range(event)
    try:
        if rng and gitutil.ok("cat-file", "-e", f"{event['before']}^{{commit}}", cwd=cwd):
            return commit_problems(rng, cwd=cwd)
        return commit_problems(after, cwd=cwd)
    except gitutil.GitError as e:
        return [f"could not read the pushed commits: {e}"]


def scan_range(event_name: str, event: dict) -> str | None:
    """The `--log-opts` range for gitleaks, or None for a scan of the whole history."""
    if event_name in ("pull_request", "pull_request_target") and "pull_request" in event:
        pr = event["pull_request"]
        return f"{pr['base']['sha']}..{pr['head']['sha']}"
    if event_name == "push":
        return push_range(event)
    return None


def report(problems: list[str], *, title: str = "format") -> None:
    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    for p in problems:
        if gha:
            msg = p.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::warning title={title}::{msg}")
        else:
            print(f"warning: {p}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Format checks (warnings, never blocking)\n\n")
            f.write("".join(f"- {p}\n" for p in problems) if problems else "All format checks passed.\n")
            f.write("\n")
    if not problems:
        print("format checks: all passed")
