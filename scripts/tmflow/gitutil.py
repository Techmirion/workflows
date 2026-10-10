"""Small wrappers around the git command line (local facts only, no network)."""
from __future__ import annotations

import re
import subprocess

ZERO_SHA = "0" * 40

MERGE_PR_RE = re.compile(r"^Merge pull request #(?P<number>[0-9]+) from (?P<head>\S+)$")


class GitError(RuntimeError):
    pass


def git(*args: str, cwd: str | None = None, check: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def ok(*args: str, cwd: str | None = None) -> bool:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True).returncode == 0


def rev(ref: str, cwd: str | None = None) -> str:
    return git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=cwd).strip()


def try_rev(ref: str, cwd: str | None = None) -> str | None:
    p = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=cwd, capture_output=True, text=True,
    )
    return p.stdout.strip() if p.returncode == 0 else None


def is_ancestor(a: str, b: str, cwd: str | None = None) -> bool:
    """True if commit `a` is an ancestor of (or equal to) commit `b`."""
    p = subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=cwd, capture_output=True, text=True)
    if p.returncode in (0, 1):
        return p.returncode == 0
    raise GitError(f"git merge-base --is-ancestor {a} {b}: {p.stderr.strip()}")


def tags(cwd: str | None = None) -> list[str]:
    return [t for t in git("tag", "--list", cwd=cwd).split() if t]


def tags_at(sha: str, cwd: str | None = None) -> list[str]:
    return [t for t in git("tag", "--points-at", sha, cwd=cwd).split() if t]


def tag_commit(tag: str, cwd: str | None = None) -> str | None:
    return try_rev(f"refs/tags/{tag}", cwd=cwd)


def current_branch(cwd: str | None = None) -> str | None:
    p = subprocess.run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=cwd, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def commits(rng: str, cwd: str | None = None) -> list[dict]:
    """Commits in a range (`a..b`, or a single sha), newest first, with parents and author."""
    fmt = "%H%x1f%P%x1f%an%x1f%ae%x1f%s%x1f%b%x1e"
    out = git("log", f"--format={fmt}", rng, cwd=cwd) if ".." in rng else git("log", "-1", f"--format={fmt}", rng, cwd=cwd)
    result = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, parents, an, ae, subject, body = rec.split("\x1f")
        result.append({
            "sha": sha, "parents": parents.split(), "author": an, "email": ae, "subject": subject,
            "body": body.strip(),
        })
    return result


def first_parent_commits(base: str, head: str, cwd: str | None = None) -> list[dict]:
    """Commits on the first-parent line of `head` that are not in `base`, oldest first.

    On `dev` these are the PR merges, plus any commit pushed straight to `dev`.
    Each item: sha, subject, body, merge (bool), and when the subject is GitHub's
    default merge message, `number` (the PR) and `head` (`owner/branch`).
    """
    fmt = "%H%x1f%P%x1f%s%x1f%b%x1e"
    out = git("log", "--first-parent", "--reverse", f"--format={fmt}", f"{base}..{head}", cwd=cwd)
    items = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, parents, subject, body = rec.split("\x1f")
        item = {"sha": sha, "subject": subject, "body": body.strip(), "merge": len(parents.split()) > 1,
                "number": None, "head": None}
        m = MERGE_PR_RE.match(subject)
        if m and item["merge"]:
            item["number"] = int(m.group("number"))
            item["head"] = m.group("head")
        items.append(item)
    return items
