#!/usr/bin/env python3
"""tm doctor: check a clone against the team rules, and fix what is safe to fix.

For people and agents, before a commit, a push or a pull request (git.md 4, 5, 6
and 11; ai-guardrails.md 8). It never blocks: the exit code is 0 unless
--strict is given.

    python3 scripts/tm.py doctor               # report
    python3 scripts/tm.py doctor --fix         # also fix the safe things locally
    python3 scripts/tm.py doctor --fix-pr      # also fix the PR title and base on GitHub
    python3 scripts/tm.py doctor --json        # for agents

What --fix may change, and nothing else:
- the messages of commits that are not on any remote yet: drops `Co-Authored-By:`
  and assistant lines, drops `Fixes #N` lines from the body, adds `(#N)` from the
  branch name. Trees, authors and dates stay as they were; the old tip is printed
  for undo. Pushed commits are never rewritten.
- the name of a branch that is not on any remote, when lowercasing it makes it valid;
- secret-like and generated files that are staged: unstaged, never deleted.

Standalone on purpose (standard library and `fmt` only): the team skill
`techmirion-standards` ships a copy of this file next to `fmt.py`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from tmflow import fmt  # noqa: E402

LEVELS = ("error", "warn", "info")
PROFILES = ("flow", "team", "live", "other")

#: Lines an assistant adds to a commit message. Work done with an assistant is said in the
#: PR, not in the commit (git.md 5, ai-guardrails.md 3).
ASSISTANT_RE = re.compile(
    r"^[ \t]*(?:claude-session[ \t]*:.*|(?:🤖[ \t]*)?generated (?:with|by) \[?(?:claude|omicron|pi)\b.*)$",
    re.I | re.M,
)
#: A body line that only closes issues: `Fixes #34`, `closes #34, #40`.
CLOSING_LINE_RE = re.compile(
    rf"^[ \t]*(?:[-*][ \t]+)?(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)[ \t]*:?[ \t]+{fmt.REF}"
    rf"(?:[ \t]*,[ \t]*(?:and[ \t]+)?{fmt.REF})*[ \t]*\.?[ \t]*$",
    re.I,
)
#: Never in a repo (git.md 11, data-and-secrets.md 0). Paths only are reported, never contents.
SECRET_PATTERNS = (
    (re.compile(r"(?:^|/)\.env(?:\.(?!example$|sample$|template$|dist$)[^/]+)?$"), ".env file"),
    (re.compile(r"\.(?:key|pem|p12|pfx)$"), "key or certificate"),
    (re.compile(r"(?:^|/)id_(?:rsa|ecdsa|ed25519)$"), "SSH key"),
    (re.compile(r"(?:^|/)CONNECT-[^/]*$"), "connection file"),
    (re.compile(r"(?:^|/)\.mcp\.json$"), "connector config"),
)
JUNK_PATTERNS = (
    (re.compile(r"(?:^|/)node_modules/"), "node_modules"),
    (re.compile(r"(?:^|/)\.?venv/"), "virtual environment"),
    (re.compile(r"(?:^|/)__pycache__/|\.pyc$"), "Python cache"),
    (re.compile(r"(?:^|/)\.pytest_cache/"), "pytest cache"),
    (re.compile(r"(?:^|/)\.(?:omicron|claude)/"), "AI scratch"),
    (re.compile(r"\.bak[^/]*$"), "backup copy"),
    (re.compile(r"(?:^|/)\.shots/"), "screenshots"),
    (re.compile(r"(?:^|/)\.DS_Store$"), ".DS_Store"),
)
AGENT_NAMES = {"claude", "claude code", "omicron", "pi", "assistant", "ai", "bot"}
BAD_EMAIL_RE = re.compile(r"(?:\.local|localhost|\.localdomain|@anthropic\.com|@openai\.com)$", re.I)


# --- git ----------------------------------------------------------------------

class Git:
    def __init__(self, cwd: str):
        self.cwd = cwd

    def run(self, *args, input=None, env=None):
        return subprocess.run(["git", *args], cwd=self.cwd, capture_output=True, text=True,
                              input=input, env={**os.environ, **(env or {})})

    def out(self, *args):
        p = self.run(*args)
        return p.stdout.strip() if p.returncode == 0 else None

    def lines(self, *args):
        o = self.out(*args)
        return [x for x in o.split("\n") if x] if o else []


class Commit:
    def __init__(self, sha: str, raw: str):
        self.sha = sha
        header, _, self.message = raw.partition("\n\n")
        self.tree, self.parents, self.author, self.committer = None, [], "", ""
        for line in header.split("\n"):
            key, _, val = line.partition(" ")
            if key == "tree":
                self.tree = val
            elif key == "parent":
                self.parents.append(val)
            elif key == "author":
                self.author = val
            elif key == "committer":
                self.committer = val

    @property
    def short(self):
        return self.sha[:7]

    @property
    def subject(self):
        return fmt.subject_of(self.message, "")

    @property
    def is_merge(self):
        return len(self.parents) > 1

    def ident(self, which):
        m = re.match(r"^(?P<name>.*) <(?P<email>[^>]*)> (?P<date>\d+ [+-]\d{4})$", getattr(self, which))
        return m.groupdict() if m else None


# --- findings -----------------------------------------------------------------

def finding(code, level, msg, *, commit=None, fix=None, auto=None):
    """`auto` names the switch that fixes it (`--fix`, `--fix-pr`), or None: the agent fixes it."""
    return {"code": code, "level": level, "message": msg, "commit": commit, "fix": fix, "auto": auto}


def detect_profile(g: Git, root: str):
    url = g.out("remote", "get-url", "origin") or ""
    if url.startswith("/shared/hub/") or "/shared/hub/" in url:
        return "live", os.path.basename(url.rstrip("/")).removesuffix(".git")
    m = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?/?$", url)
    if m and m.group(1).lower() == "techmirion":
        slug = f"Techmirion/{m.group(2)}"
        checks = os.path.join(root, ".github", "workflows", "checks.yml")
        try:
            on_flow = "Techmirion/workflows" in open(checks, encoding="utf-8").read()
        except OSError:
            on_flow = False
        return ("flow" if on_flow else "team"), slug
    return "other", (os.path.basename(root) if not url else url)


def find_base(g: Git, branch: str | None, given: str | None):
    if given:
        return given if g.out("rev-parse", "--verify", "--quiet", given + "^{commit}") else None
    cands = []
    if branch in ("dev", "main", "master"):
        cands.append(f"origin/{branch}")
    if branch and branch.startswith("hotfix/"):
        cands.append("origin/main")
    cands += ["origin/dev", "origin/main", "origin/master"]
    for c in cands:
        if g.out("rev-parse", "--verify", "--quiet", c + "^{commit}"):
            return c
    return None


def normalise_branch(name: str) -> str:
    n = name.strip().lower()
    head, sep, tail = n.partition("/")
    tail = re.sub(r"[^a-z0-9._-]+", "-", tail).strip("-._")
    tail = re.sub(r"([-._])[-._]+", r"\1", tail)
    return head + sep + tail


def fixed_message(msg: str, *, issue: int | None, add_ref: bool, drop_closing: bool) -> str:
    """The message with the safe fixes applied. The subject's wording is never changed."""
    out, subject_seen = [], False
    for line in msg.split("\n"):
        if fmt.COAUTHOR_RE.fullmatch(line) or ASSISTANT_RE.fullmatch(line):
            continue
        if not subject_seen and line.strip():
            subject_seen = True
            if add_ref and issue is not None and not fmt.check_subject(line.strip()):
                line = fmt.add_issue_ref(line.strip(), issue)
            out.append(line)
            continue
        if subject_seen and drop_closing and CLOSING_LINE_RE.fullmatch(line):
            continue
        out.append(line)
    text = "\n".join(out).rstrip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text + "\n"


def classify_path(path: str):
    for rx, what in SECRET_PATTERNS:
        if rx.search(path):
            return "secret", what
    for rx, what in JUNK_PATTERNS:
        if rx.search(path):
            return "junk", what
    return None, None


def hooks_state(g: Git, root: str) -> str:
    if g.out("config", "core.hooksPath"):
        return "custom"
    hooks = g.out("rev-parse", "--git-path", "hooks") or ".git/hooks"
    hooks = hooks if os.path.isabs(hooks) else os.path.join(root, hooks)
    try:
        text = open(os.path.join(hooks, "commit-msg"), encoding="utf-8", errors="replace").read()
    except OSError:
        return "missing"
    if "techmirion-hook" in text:
        return "techmirion"
    if "pre-commit" in text:
        return "pre-commit"
    return "other"


def gh_pr(cwd: str):
    if not shutil.which("gh"):
        return None, "gh is not installed"
    try:
        p = subprocess.run(["gh", "pr", "view", "--json", "number,title,body,baseRefName,url,state"],
                           cwd=cwd, capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"gh failed: {type(e).__name__}"
    if p.returncode != 0:
        err = (p.stderr or "").strip().split("\n")[0]
        return None, err or "no pull request for this branch"
    try:
        return json.loads(p.stdout), None
    except ValueError:
        return None, "gh returned no JSON"


# --- diagnosis ----------------------------------------------------------------

class State:
    pass


def diagnose(cwd: str, *, base_arg=None, profile_arg=None, check_pr=True) -> State:
    s = State()
    g = s.g = Git(cwd)
    s.root = g.out("rev-parse", "--show-toplevel")
    s.findings = []
    s.busy, s.issue, s.pr, s.title_fix = None, None, None, None
    if not s.root:
        s.findings.append(finding("R0", "error", f"{cwd} is not a git repository"))
        s.profile, s.repo, s.branch, s.base, s.commits, s.unpushed = "other", cwd, None, None, [], set()
        return s
    s.profile, s.repo = detect_profile(g, s.root)
    if profile_arg:
        s.profile = profile_arg
    s.branch = g.out("symbolic-ref", "--quiet", "--short", "HEAD")
    s.base = find_base(g, s.branch, base_arg)
    s.issue = fmt.issue_from_branch(s.branch or "") if s.profile in ("flow", "team") else None
    rng = f"{s.base}..HEAD" if s.base else "HEAD"
    shas = g.lines("rev-list", "--reverse", "--topo-order", rng)
    if not s.base:
        shas = shas[-50:]
    s.commits = [Commit(x, g.out("cat-file", "commit", x) or "") for x in shas]
    remotes = g.lines("remote")
    s.unpushed = set(g.lines("rev-list", rng, "--not", "--remotes")) if remotes else set(shas)
    gd = g.out("rev-parse", "--absolute-git-dir") or os.path.join(s.root, ".git")
    s.busy = next((n for n in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")
                   if os.path.exists(os.path.join(gd, n))), None)
    strict_fmt = s.profile == "flow"
    fmt_level = "warn" if strict_fmt else "info"
    team = s.profile in ("flow", "team", "live")
    F = s.findings.append

    if s.profile == "other":
        F(finding("R1", "info", "not a Techmirion repo (origin is not github.com/Techmirion nor /shared/hub): "
                  "only secrets, generated files and Co-Authored-By are checked"))
    if s.busy:
        F(finding("R2", "info", f"a git operation is in progress ({s.branch or 'detached'}: {s.busy}); finish it before --fix"))
    if s.branch is None:
        F(finding("R3", "info", "detached HEAD: check out a branch before committing"))

    # branch (git.md 4)
    if s.profile in ("flow", "team") and s.branch and not fmt.branch_exempt(s.branch):
        probs = fmt.check_branch(s.branch)
        if probs:
            on_remote = bool(g.out("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{s.branch}"))
            norm = normalise_branch(s.branch)
            can = not on_remote and norm != s.branch and not fmt.check_branch(norm) \
                and not g.out("rev-parse", "--verify", "--quiet", f"refs/heads/{norm}")
            m = re.match(r"^([a-z]+)/(.+)$", norm)
            guess = f"{m.group(1)}/N-{m.group(2)}" if m and m.group(1) in fmt.TYPES else "type/N-topic"
            fix = f"git branch -m {norm}" if can else (
                f"git branch -m {guess} (N = its issue: `gh issue list`, or `gh issue create`; type one of "
                + ", ".join(fmt.TYPES) + ")"
                + ("; the old name is on origin: push the new one, then ask the person before deleting the old" if on_remote else ""))
            F(finding("B1", fmt_level, probs[0], fix=fix, auto="--fix" if can else None))
    if s.profile == "flow" and s.branch in ("dev", "main") and s.unpushed:
        F(finding("B2", "warn", f"{len(s.unpushed)} commit(s) on `{s.branch}` that are not pushed: on the flow every change "
                  "goes through a branch and a PR (git.md 4)",
                  fix=f"git switch -c type/N-topic && git branch -f {s.branch} origin/{s.branch}   (moves them; ask first if unsure)"))
    if s.profile == "live" and s.branch == "dev" and s.unpushed:
        F(finding("L1", "info", f"{len(s.unpushed)} commit(s) not pushed: a push to dev of a /shared/hub repo goes live "
                  "for the whole team within a minute; push only with the person's yes, after the repo's own checks "
                  "(/shared/hub/README.md)"))

    # commits (git.md 5)
    issue = s.issue
    for c in s.commits:
        pushed = c.sha not in s.unpushed
        where = " (already pushed: leave it, history is not rewritten after a push)" if pushed else ""
        auto = None if pushed else "--fix"
        msg = c.message
        if fmt.coauthor_lines(msg):
            why = "CI fails on it" if s.profile == "flow" else "commits never carry one"
            F(finding("C3", "error", f"`{c.subject}` has a Co-Authored-By trailer: {why} (git.md 5){where}",
                      commit=c.short, fix="remove the trailer" if not pushed else "tell the person", auto=auto))
        if ASSISTANT_RE.search(msg):
            F(finding("C6", "warn", f"`{c.subject}` has an assistant line (Claude-Session, Generated with …): "
                      f"say it in the PR instead (ai-guardrails.md 3){where}", commit=c.short, auto=auto))
        if c.is_merge or fmt.subject_exempt(c.subject):
            continue
        if fmt.AUTOSQUASH_RE.match(c.subject):
            F(finding("C7", "info", f"`{c.subject}`: squash it before review (`git rebase -i --autosquash {s.base or 'BASE'}`)",
                      commit=c.short))
            continue
        probs = fmt.check_subject(c.subject)
        if probs and team:
            F(finding("C1", fmt_level, probs[0] + where, commit=c.short,
                      fix=None if pushed else "reword it: `git commit --amend` for the last commit, "
                      f"`git rebase -i {s.base or 'BASE'}` (reword) for older ones; keep the meaning"))
        elif issue is not None and not pushed and fmt.add_issue_ref(c.subject, issue) != c.subject:
            F(finding("C2", fmt_level, f"`{c.subject}` lacks `(#{issue})` from the branch name (git.md 5)",
                      commit=c.short, auto="--fix"))
        if s.profile in ("flow", "team") and fmt.closing_refs(msg):
            body_only = not fmt.closing_refs(c.subject)
            F(finding("C4", fmt_level, f"`{c.subject}` closes {', '.join(fmt.closing_refs(msg))}: commits do not use "
                      f"Fixes/Closes, the PR body does (git.md 5){where}", commit=c.short,
                      fix=None if body_only else "reword the subject without the keyword",
                      auto=auto if body_only else None))
        body = [x for x in fmt.message_lines(msg, "")[1:] if x.strip()
                and not fmt.COAUTHOR_RE.fullmatch(x) and not ASSISTANT_RE.fullmatch(x) and not CLOSING_LINE_RE.fullmatch(x)]
        if body and s.profile in ("flow", "team"):
            F(finding("C5", "info", f"`{c.subject}` has a body: commits are one line (git.md 5); the why goes in the PR",
                      commit=c.short))
        a = c.ident("author")
        if a and (a["name"].strip().lower() in AGENT_NAMES or BAD_EMAIL_RE.search(a["email"])):
            F(finding("A1", "warn", f"`{c.subject}` is authored by `{a['name']} <{a['email']}>`: commits are under the "
                      "responsible person's own GitHub identity (ai-guardrails.md 3)", commit=c.short,
                      fix=None if pushed else "git config user.name/user.email, then "
                      "`git commit --amend --reset-author --no-edit` (last commit) or reword with rebase -x"))

    # files (git.md 11)
    added = set(g.lines("diff", "--name-only", "--diff-filter=A", rng)) if s.commits else set()
    staged = set(g.lines("diff", "--cached", "--name-only", "--diff-filter=AM"))
    groups = {}
    for path in sorted(added | staged):
        kind, what = classify_path(path)
        if kind:
            groups.setdefault((kind, what, path in staged and path not in added), []).append(path)
    for (kind, what, only_staged), paths in sorted(groups.items()):
        shown = ", ".join(paths[:5]) + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")
        level = "error" if kind == "secret" else "warn"
        if only_staged:
            F(finding("F1" if kind == "secret" else "F2", level, f"staged {what}: {shown} (git.md 11)",
                      fix="unstage it and add it to .gitignore" + ("; do not open or print it" if kind == "secret" else ""),
                      auto="--fix"))
        else:
            F(finding("F1" if kind == "secret" else "F2", level, f"committed {what}: {shown} (git.md 11)",
                      fix=("STOP: tell the person, do not print the value; a pushed secret is revoked first "
                           "(data-and-secrets.md 8)") if kind == "secret" else
                      "git rm -r --cached <path>, add it to .gitignore, commit that"))

    # hooks (git.md 5, new-repo.md)
    if s.profile in ("flow", "team"):
        st = hooks_state(g, s.root)
        if st in ("missing", "other"):
            F(finding("H1", "info", "the Techmirion git hooks are not installed in this clone",
                      fix="sh <workflows clone>/scripts/install-hooks.sh .   (needs gitleaks; "
                      "`git config techmirion.hooks warn` makes them warn instead of refuse)"))

    # up to date
    if s.base and s.branch and f"origin/{s.branch}" != s.base:
        behind = g.out("rev-list", "--count", f"HEAD..{s.base}")
        if behind and behind != "0":
            F(finding("S1", "info", f"{behind} commit(s) on {s.base} are not in this branch: bring it up to date "
                      f"before the PR (`git fetch && git rebase {s.base}` if nothing is pushed, else `git merge {s.base}`)"))

    # pull request (git.md 6)
    if check_pr and s.profile in ("flow", "team") and s.branch and not fmt.branch_exempt(s.branch) \
            and g.out("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{s.branch}"):
        pr, err = gh_pr(s.root)
        s.pr = pr
        if pr and pr.get("state") == "OPEN":
            want = "main" if s.branch.startswith("hotfix/") else "dev"
            tprobs = fmt.check_title(pr.get("title", ""))
            single = [c for c in s.commits if not c.is_merge and not fmt.subject_exempt(c.subject)]
            title_fix = None
            if tprobs and len(single) == 1 and not fmt.check_subject(single[0].subject):
                title_fix = single[0].subject
            s.title_fix = title_fix
            if tprobs:
                F(finding("P1", fmt_level, tprobs[0], fix=f"gh pr edit {pr['number']} --title \"{title_fix or 'type(place): message (#N)'}\"",
                          auto="--fix-pr" if title_fix else None))
            for p in fmt.check_body(pr.get("body", ""), branch_issue=issue):
                F(finding("P2", fmt_level, p, fix=f"gh pr edit {pr['number']} --body-file <file>: `Fixes #{issue or 'N'}` if this PR "
                          "completes the issue, `Refs #N` if it is part of it, or tick \"No issue needed\""))
            if pr.get("baseRefName") != want:
                F(finding("P3", "warn", f"PR #{pr['number']} targets `{pr.get('baseRefName')}`, it should target `{want}` (git.md 6, 9)",
                          fix=f"gh pr edit {pr['number']} --base {want}", auto="--fix-pr"))
        elif err and "no pull requests found" not in err.lower():
            F(finding("P0", "info", f"pull request not checked: {err}"))
    return s


# --- fixes --------------------------------------------------------------------

def apply_fixes(s: State, *, fix_pr: bool) -> list[str]:
    g, done = s.g, []
    codes = {f["code"] for f in s.findings if f["auto"]}
    if s.busy or s.branch is None:
        return ["nothing fixed: " + ("a git operation is in progress" if s.busy else "detached HEAD")]

    # staged files
    to_unstage = []
    for f in s.findings:
        if f["code"] in ("F1", "F2") and f["auto"]:
            to_unstage += [p for p in g.lines("diff", "--cached", "--name-only", "--diff-filter=AM") if classify_path(p)[0]]
            break
    if to_unstage:
        g.run("restore", "--staged", "--", *sorted(set(to_unstage)))
        done.append(f"unstaged {len(set(to_unstage))} file(s): " + ", ".join(sorted(set(to_unstage))[:5]))

    # commit messages
    if codes & {"C2", "C3", "C4", "C6"}:
        add_ref = s.profile in ("flow", "team")
        drop_closing = s.profile in ("flow", "team")
        new_msgs = {}
        for c in s.commits:
            if c.sha not in s.unpushed:
                continue
            m = fixed_message(c.message, issue=s.issue if not c.is_merge else None,
                              add_ref=add_ref and not c.is_merge, drop_closing=drop_closing and not c.is_merge)
            if m.rstrip("\n") != c.message.rstrip("\n"):
                new_msgs[c.sha] = m
        if new_msgs:
            old_head = g.out("rev-parse", "HEAD")
            mapping = {}
            for c in s.commits:
                parents = [mapping.get(p, p) for p in c.parents]
                if c.sha not in new_msgs and parents == c.parents:
                    mapping[c.sha] = c.sha
                    continue
                a, cm = c.ident("author"), c.ident("committer")
                env = {"GIT_AUTHOR_NAME": a["name"], "GIT_AUTHOR_EMAIL": a["email"], "GIT_AUTHOR_DATE": a["date"],
                       "GIT_COMMITTER_NAME": cm["name"], "GIT_COMMITTER_EMAIL": cm["email"], "GIT_COMMITTER_DATE": cm["date"]}
                args = ["commit-tree", c.tree]
                for p in parents:
                    args += ["-p", p]
                p = g.run(*args, "-F", "-", input=new_msgs.get(c.sha, c.message), env=env)
                if p.returncode != 0:
                    return done + [f"stopped: could not rewrite {c.short}: {p.stderr.strip()}"]
                mapping[c.sha] = p.stdout.strip()
            new_head = mapping[old_head]
            p = g.run("update-ref", "-m", "tm doctor --fix: reword unpushed commits",
                      f"refs/heads/{s.branch}", new_head, old_head)
            if p.returncode != 0:
                return done + [f"stopped: could not move {s.branch}: {p.stderr.strip()}"]
            done.append(f"reworded {len(new_msgs)} unpushed commit(s) on {s.branch} (trees unchanged); "
                        f"undo: git reset --soft {old_head[:12]}")

    # branch name
    for f in s.findings:
        if f["code"] == "B1" and f["auto"]:
            new = f["fix"].split()[-1]
            p = g.run("branch", "-m", new)
            if p.returncode == 0:
                done.append(f"renamed branch {s.branch} to {new}")
                s.branch = new

    # pull request
    if fix_pr and s.pr:
        n = str(s.pr["number"])
        for f in s.findings:
            if f["code"] == "P1" and f["auto"] and getattr(s, "title_fix", None):
                p = subprocess.run(["gh", "pr", "edit", n, "--title", s.title_fix], cwd=s.root, capture_output=True, text=True)
                done.append(f"PR #{n}: title set to `{s.title_fix}`" if p.returncode == 0 else f"PR #{n}: title not changed: {p.stderr.strip()}")
            if f["code"] == "P3" and f["auto"]:
                want = f["fix"].split()[-1]
                p = subprocess.run(["gh", "pr", "edit", n, "--base", want], cwd=s.root, capture_output=True, text=True)
                done.append(f"PR #{n}: base set to `{want}`" if p.returncode == 0 else f"PR #{n}: base not changed: {p.stderr.strip()}")
    return done


# --- output -------------------------------------------------------------------

def as_dict(s: State, fixed) -> dict:
    return {
        "repo": s.repo, "profile": s.profile, "branch": s.branch, "base": s.base,
        "commits": len(s.commits), "unpushed": len(s.unpushed),
        "findings": s.findings, "fixed": fixed,
        "counts": {lv: sum(1 for f in s.findings if f["level"] == lv) for lv in LEVELS},
    }


def print_report(s: State, fixed):
    d = as_dict(s, fixed)
    print(f"tm doctor · {d['repo']} · profile {d['profile']} · branch {d['branch'] or '(detached)'}"
          f" · base {d['base'] or '(none)'} · {d['commits']} commit(s), {d['unpushed']} not pushed")
    for line in fixed:
        print(f"  FIXED  {line}")
    order = {lv: i for i, lv in enumerate(LEVELS)}
    for f in sorted(s.findings, key=lambda f: order[f["level"]]):
        where = f" {f['commit']}" if f["commit"] else ""
        print(f"  {f['level'].upper():5}  {f['code']}{where}  {f['message']}")
        if f["auto"]:
            print(f"         fix: run again with {f['auto']}")
        elif f["fix"]:
            print(f"         fix: {f['fix']}")
    c = d["counts"]
    autos = sum(1 for f in s.findings if f["auto"])
    print(f"summary: {c['error']} error(s), {c['warn']} warning(s), {c['info']} note(s)"
          + (f"; {autos} fixable by the doctor" if autos else "") + (" · clean" if not (c["error"] or c["warn"]) else ""))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tm.py doctor", description=__doc__.split("\n\n")[0])
    p.add_argument("path", nargs="?", default=".", help="the clone (default: the current folder)")
    p.add_argument("--fix", action="store_true", help="fix the safe local things (see --help)")
    p.add_argument("--fix-pr", action="store_true", help="--fix, and also the PR title and base on GitHub")
    p.add_argument("--base", help="compare with this ref instead of origin/dev (origin/main for hotfix/*)")
    p.add_argument("--profile", choices=PROFILES, help="override the detected profile")
    p.add_argument("--no-pr", action="store_true", help="do not ask GitHub about the pull request")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--strict", action="store_true", help="exit 1 if an error or a warning is left")
    a = p.parse_args(argv)
    cwd = os.path.abspath(a.path)
    kw = dict(base_arg=a.base, profile_arg=a.profile, check_pr=not a.no_pr)
    s = diagnose(cwd, **kw)
    fixed = []
    if a.fix or a.fix_pr:
        fixed = apply_fixes(s, fix_pr=a.fix_pr)
        if fixed:
            s = diagnose(cwd, **kw)
    if a.json:
        print(json.dumps(as_dict(s, fixed), indent=2, ensure_ascii=False))
    else:
        print_report(s, fixed)
    left = any(f["level"] in ("error", "warn") for f in s.findings)
    return 1 if (a.strict and left) else 0


if __name__ == "__main__":
    sys.exit(main())
