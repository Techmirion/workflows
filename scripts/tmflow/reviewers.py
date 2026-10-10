"""Who reviews a PR (git.md 2 and 7): the owner, or the deputy when the owner wrote it.

Convention for `.github/CODEOWNERS`: on the `*` line, the first handle is the
owner and the second is the deputy. Later lines win, as in GitHub.
"""
from __future__ import annotations


def parse_codeowners(text: str) -> dict:
    owner = deputy = None
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] != "*":
            continue
        handles = [p[1:] for p in parts[1:] if p.startswith("@")]
        owner = handles[0] if handles else None
        deputy = handles[1] if len(handles) > 1 else None
    return {"owner": owner, "deputy": deputy}


def pick_reviewers(author: str, owner: str | None, deputy: str | None):
    """Return (users, teams, warnings)."""
    warnings = []
    a = (author or "").lower()
    if not owner:
        return [], [], ["CODEOWNERS has no `*` line naming an owner: request a reviewer by hand"]
    if a == owner.lower():
        target = deputy
        if not target:
            warnings.append("the author is the owner and CODEOWNERS names no deputy: request a reviewer by hand")
    else:
        target = owner
    if not target or target.lower() == a:
        return [], [], warnings
    if "/" in target:
        return [], [target.split("/", 1)[1]], warnings
    return [target], [], warnings
