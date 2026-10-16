"""Format rules of git.md sections 4, 5, 6 and 14: branch names, commit subjects,
PR titles and PR bodies.

Standalone on purpose (no imports from the rest of tmflow): the git hooks copy
this file next to themselves.

Every check returns a list of problems (strings). An empty list means OK. The
callers decide what a problem means: CI turns them into warnings (git.md 14,
"a failed format check is a warning, never a blocker"), the commit-msg hook
refuses the commit.
"""
from __future__ import annotations

import re

#: Branch and commit types (git.md 4).
TYPES = ("feat", "fix", "chore", "docs", "refactor", "test", "hotfix")
#: PR titles may also use `release` (git.md 14), for release PRs only.
TITLE_TYPES = TYPES + ("release",)
#: Types that are "more than a chore" and so should start from an issue (git.md 3).
ISSUE_EXPECTED = ("feat", "fix", "refactor", "hotfix")
#: Branches the branch-name check never looks at (git.md 14).
EXEMPT_BRANCHES = ("dev", "main")
EXEMPT_BRANCH_PREFIXES = ("dependabot/",)

_TYPE_ALT = "|".join(TYPES)
_TITLE_TYPE_ALT = "|".join(TITLE_TYPES)

#: `type/N-topic` or `type/topic`. Topic: lowercase letters and digits, joined by
#: single `-`, `.` or `_`. No person names (cannot be checked), no uppercase.
BRANCH_RE = re.compile(
    rf"^(?P<type>{_TYPE_ALT})/"
    r"(?:(?P<issue>[0-9]+)-)?"
    r"(?P<topic>[a-z0-9]+(?:[-._][a-z0-9]+)*)$"
)

#: One issue reference: `#34` or `Owner/repo#34` (git.md 5).
REF = r"(?:#[0-9]+|[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+#[0-9]+)"
#: The optional trailing reference group: `(#34)`, `(#34, #40)`, `(Techmirion/other#7)`.
REFS_GROUP = rf"\((?P<refs>{REF}(?:, {REF})*)\)"
#: The place: lowercase, digits, `.`, `_`, `-`, `/`. Required (git.md 5: `type(place)`).
PLACE = r"[a-z0-9][a-z0-9._/-]*"

#: `type(place): message` with an optional ` (#N)` style reference at the end.
SUBJECT_RE = re.compile(
    rf"^(?P<type>{_TYPE_ALT})\((?P<place>{PLACE})\): "
    rf"(?P<msg>\S(?:.*?\S)??)(?: {REFS_GROUP})?$"
)
#: PR titles: the same, plus `release: vX.Y.Z <summary>` (git.md 9 and 14).
RELEASE_TITLE_RE = re.compile(
    r"^release: (?P<version>v[0-9]+\.[0-9]+\.[0-9]+)(?: (?P<summary>\S.*))?$"
)
#: A trailing parenthesis that looks like a reference but is not a valid one.
BAD_REF_TAIL_RE = re.compile(r"\([^()]*#[^()]*\)$")
#: Exemptions (git.md 14): merge and revert commits.
MERGE_SUBJECT_RE = re.compile(
    r"^Merge (?:pull request #[0-9]+ from |branch |branches |remote-tracking branch |tag |commit |[0-9a-f]{7,40} )"
)
REVERT_SUBJECT_RE = re.compile(r'^(?:Revert|Reapply) ".*"$')
#: Local-only helper commits that are squashed away before review (git.md 4).
AUTOSQUASH_RE = re.compile(r"^(?:fixup|squash|amend)! ")

#: PR body: `Fixes #N` / `Refs #N` on a line of its own (git.md 6).
BODY_REF_RE = re.compile(rf"^[ \t]*(?:[-*][ \t]+)?(?P<kw>Fixes|Refs)[ \t]+(?P<ref>{REF})\b", re.I | re.M)
#: The template's box, ticked: `- [x] No issue needed`.
NO_ISSUE_RE = re.compile(r"^[ \t]*[-*][ \t]+\[[xX]\][ \t]+No issue needed", re.I | re.M)
#: Every GitHub closing keyword, for the release body (people do write `Closes`).
CLOSING_RE = re.compile(
    rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)[ \t]*:?[ \t]+(?P<ref>{REF})\b", re.I
)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
#: A `Co-Authored-By:` trailer, in any case. Commits never carry one (git.md 5).
COAUTHOR_RE = re.compile(r"^[ \t]*co-authored-by[ \t]*:.*$", re.I | re.M)

VERSION_RE = re.compile(r"^v?(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)$")
#: Release tags. Exactly `vMAJOR.MINOR.PATCH`; the moving `v1` of the workflows repo is not one.
TAG_RE = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
#: The `Version:` line of a release PR body (git.md 9).
VERSION_LINE_RE = re.compile(r"^Version:[ \t]*(?P<version>v[0-9]+\.[0-9]+\.[0-9]+)[ \t]*$", re.M)


# --- branches -------------------------------------------------------------

def branch_exempt(name: str) -> bool:
    return name in EXEMPT_BRANCHES or name.startswith(EXEMPT_BRANCH_PREFIXES)


def parse_branch(name: str):
    """Return (type, issue or None, topic) for a valid branch name, else None."""
    m = BRANCH_RE.match(name)
    if not m:
        return None
    topic = m.group("topic")
    if m.group("issue") is None and topic.isdigit():
        return None  # `fix/34`: the topic is missing
    issue = int(m.group("issue")) if m.group("issue") is not None else None
    if issue == 0:
        return None
    return m.group("type"), issue, topic


def issue_from_branch(name: str):
    """The issue number carried by a `type/N-topic` branch, or None."""
    parsed = parse_branch(name or "")
    return parsed[1] if parsed else None


def check_branch(name: str) -> list[str]:
    if branch_exempt(name):
        return []
    parsed = parse_branch(name)
    if parsed is None:
        return [
            f"branch `{name}` does not match `type/N-topic` or `type/topic` "
            f"(type one of {', '.join(TYPES)}; topic in lowercase letters, digits and dashes)"
        ]
    btype, issue, _ = parsed
    if issue is None and btype in ISSUE_EXPECTED:
        return [f"branch `{name}`: a `{btype}` change starts from an issue, name it `{btype}/N-topic`"]
    return []


# --- subjects and titles --------------------------------------------------

def subject_exempt(subject: str) -> bool:
    return bool(MERGE_SUBJECT_RE.match(subject) or REVERT_SUBJECT_RE.match(subject))


def parse_subject(subject: str):
    m = SUBJECT_RE.match(subject)
    return m.groupdict() if m else None


def split_refs(refs: str | None) -> list[str]:
    return [r.strip() for r in refs.split(",")] if refs else []


def check_subject(subject: str, *, allow_autosquash: bool = False) -> list[str]:
    """Check one commit subject (the first line of the message)."""
    if subject_exempt(subject):
        return []
    if AUTOSQUASH_RE.match(subject):
        return [] if allow_autosquash else [f"`{subject}`: squash fixup!/squash! commits before review"]
    if subject.startswith("release"):
        return [f"`{subject}`: `release` is a type for release PR titles, not for commits"]
    parsed = parse_subject(subject)
    if parsed:
        if parsed["refs"] is None and BAD_REF_TAIL_RE.search(subject):
            return [f"`{subject}`: the reference at the end is malformed; use (#N), (#N, #M) or (Owner/repo#N)"]
        return []
    return [
        f"`{subject}` does not match `type(place): message (#N)` "
        f"(type one of {', '.join(TYPES)}; place in lowercase; reference optional)"
    ]


def check_title(title: str) -> list[str]:
    """Check a PR title: the commit format, or `release: vX.Y.Z <summary>`."""
    if RELEASE_TITLE_RE.match(title):
        return []
    if subject_exempt(title):
        return []
    parsed = parse_subject(title)
    if parsed:
        if parsed["refs"] is None and BAD_REF_TAIL_RE.search(title):
            return [f"PR title `{title}`: the reference at the end is malformed; use (#N), (#N, #M) or (Owner/repo#N)"]
        return []
    return [
        f"PR title `{title}` does not match `type(place): message (#N)` "
        f"(or `release: vX.Y.Z <summary>` for a release PR)"
    ]


def add_issue_ref(subject: str, issue: int) -> str:
    """Append `(#N)` to a subject, or add `#N` to its trailing reference group.

    Idempotent: a subject that already references `#N` is returned unchanged.
    Exempt subjects (merge, revert, fixup!) are returned unchanged.
    """
    subject = subject.rstrip()
    if not subject or subject_exempt(subject) or AUTOSQUASH_RE.match(subject):
        return subject
    ref = f"#{issue}"
    m = re.search(rf" {REFS_GROUP}$", subject)
    if m:
        refs = split_refs(m.group("refs"))
        if ref in refs:
            return subject
        return subject[: m.start()] + " (" + ", ".join([ref] + refs) + ")"
    if re.search(rf"(?<![\w/]){re.escape(ref)}\b", subject):
        return subject
    return f"{subject} ({ref})"


# --- message files (hooks) ------------------------------------------------

SCISSORS = "# ------------------------ >8 ------------------------"


def message_lines(text: str, comment_char: str = "#") -> list[str]:
    """The lines of a commit message as git will keep them (comments and scissors cut)."""
    out = []
    for line in text.splitlines():
        if line.startswith(SCISSORS) or line == SCISSORS:
            break
        if comment_char and line.startswith(comment_char):
            continue
        out.append(line)
    return out


def subject_of(text: str, comment_char: str = "#") -> str:
    for line in message_lines(text, comment_char):
        if line.strip():
            return line.strip()
    return ""


def add_ref_to_message(text: str, issue: int, comment_char: str = "#") -> str:
    """Add `(#N)` to the subject line of a whole message file. Keeps everything else."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith(SCISSORS):
            break
        if comment_char and line.startswith(comment_char):
            continue
        if line.strip():
            lines[i] = add_issue_ref(line, issue)
            return "\n".join(lines)
    return text


def coauthor_lines(text: str) -> list[str]:
    """Every `Co-Authored-By:` line of a commit message, stripped, in order."""
    return [m.group(0).strip() for m in COAUTHOR_RE.finditer(text or "")]


def check_no_coauthor(text: str) -> list[str]:
    """Refuse a `Co-Authored-By:` trailer (git.md 5). No exemption: merges, reverts and bots too."""
    if not coauthor_lines(text):
        return []
    return ["the message has a `Co-Authored-By:` trailer: commits never carry one (git.md 5), remove the line"]


# --- PR bodies --------------------------------------------------------------

def strip_comments(body: str) -> str:
    return HTML_COMMENT_RE.sub("", body or "")


def body_refs(body: str) -> list[tuple[str, str]]:
    """(keyword, ref) pairs from `Fixes #N` / `Refs #N` lines, comments removed."""
    return [(m.group("kw").capitalize(), m.group("ref")) for m in BODY_REF_RE.finditer(strip_comments(body))]


def no_issue_ticked(body: str) -> bool:
    return bool(NO_ISSUE_RE.search(strip_comments(body)))


def check_body(body: str, *, branch_issue: int | None = None) -> list[str]:
    refs = body_refs(body)
    if not refs and not no_issue_ticked(body):
        return ["PR body has no `Fixes #N` or `Refs #N` line, and \"No issue needed\" is not ticked"]
    problems = []
    if branch_issue is not None and refs and f"#{branch_issue}" not in {r for _, r in refs}:
        problems.append(f"the branch names issue #{branch_issue}, but the body has no `Fixes #{branch_issue}` or `Refs #{branch_issue}`")
    return problems


def closing_refs(body: str) -> list[str]:
    """Every issue a body closes, in GitHub's sense (any closing keyword), in order, unique."""
    seen, out = set(), []
    for m in CLOSING_RE.finditer(strip_comments(body)):
        ref = m.group("ref")
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


# --- versions ---------------------------------------------------------------

def parse_version(text: str):
    m = VERSION_RE.match((text or "").strip())
    if not m:
        return None
    return int(m.group("major")), int(m.group("minor")), int(m.group("patch"))


def format_version(v) -> str:
    return "v%d.%d.%d" % tuple(v)


def release_tags(tags) -> list[tuple[int, int, int]]:
    """The release versions among tag names, sorted ascending. `v1` and others are ignored."""
    return sorted(parse_version(t) for t in tags if TAG_RE.match(t))


def next_patch(tags):
    vs = release_tags(tags)
    major, minor, patch = vs[-1] if vs else (0, 0, 0)
    return (major, minor, patch + 1)


def version_from_body(body: str):
    m = VERSION_LINE_RE.search(strip_comments(body or ""))
    return parse_version(m.group("version")) if m else None
