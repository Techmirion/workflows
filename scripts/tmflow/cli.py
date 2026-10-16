"""Command line of the shared automation: `python3 scripts/tm.py <command> ...`.

The reusable workflows call these commands; people can run them locally too.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import checks, fmt, gitutil, labels, releaselib, repoinit, reviewers
from .forge import ForgeError, make_forge


def _gha() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def _emit(level: str, msg: str) -> None:
    if _gha():
        print(f"::{level}::" + msg.replace("%", "%25").replace("\n", "%0A"))
    else:
        print(f"{level}: {msg}", file=sys.stderr if level == "error" else sys.stdout)


def _summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def _forge(a):
    kw = {}
    if a.forge == "local":
        kw = {"origin": a.origin, "state": a.state, "workdir": a.workdir}
    return make_forge(a.forge, a.repo, **kw)


def _event(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _print_problems(problems, strict):
    for p in problems:
        print(f"{'error' if strict else 'warning'}: {p}")
    if not problems:
        print("ok")
    return 1 if (strict and problems) else 0


# --- format ---------------------------------------------------------------

def cmd_check_branch(a):
    return _print_problems(fmt.check_branch(a.name), a.strict)


def cmd_check_subject(a):
    return _print_problems(fmt.check_subject(a.subject), a.strict)


def cmd_check_title(a):
    return _print_problems(fmt.check_title(a.title), a.strict)


def cmd_check_body(a):
    body = Path(a.file).read_text(encoding="utf-8")
    return _print_problems(fmt.check_body(body, branch_issue=fmt.issue_from_branch(a.branch or "")), a.strict)


def cmd_check_commits(a):
    return _print_problems(checks.commit_problems(a.range, cwd=a.git_dir), a.strict)


def cmd_check_format(a):
    try:
        ev = _event(a.event)
        if a.event_name in ("pull_request", "pull_request_target") and "pull_request" in ev:
            problems = checks.pr_problems(ev, cwd=a.git_dir)
        elif a.event_name == "push":
            problems = checks.push_problems(ev, cwd=a.git_dir)
        else:
            problems = []
    except Exception as e:  # a format check must never turn into a blocker
        problems = [f"the format checks could not run: {type(e).__name__}: {e}"]
    checks.report(problems)
    return 0  # warnings only, never a blocker (git.md 14)


def cmd_check_trailers(a):
    """Fails on a `Co-Authored-By:` trailer (git.md 5): blocking, unlike the format checks."""
    try:
        if a.range:
            problems = checks.trailer_problems(a.range, cwd=a.git_dir)
        elif a.event and a.event_name:
            problems = checks.event_trailer_problems(a.event_name, _event(a.event), cwd=a.git_dir)
        else:
            problems = ["give a RANGE, or --event and --event-name"]
    except Exception as e:  # fail closed: an unchecked push is not a clean one
        problems = [f"the trailer check could not run: {type(e).__name__}: {e}"]
    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    for p in problems:
        if gha:
            msg = p.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title=trailers::{msg}")
        else:
            print(f"error: {p}")
    if not problems:
        print("trailers: no Co-Authored-By")
    return 1 if problems else 0


def cmd_secret_scan(a):
    ev = _event(a.event) if a.event else {}
    rng = checks.scan_range(a.event_name or "", ev)
    argv = [a.gitleaks, "git", "--redact", "--no-banner", "--verbose", "--log-level", "warn"]
    if rng:
        argv += ["--log-opts", rng]
    argv.append(a.source)
    print("gitleaks scan:", f"commits {rng}" if rng else "the whole history")
    return subprocess.run(argv).returncode


# --- reviewers and cleanup -----------------------------------------------

def cmd_assign_reviewers(a):
    forge = _forge(a)
    text = None
    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        text = forge.get_file(path, a.base)
        if text is not None:
            break
    co = reviewers.parse_codeowners(text or "")
    users, teams, warnings = reviewers.pick_reviewers(a.author, co["owner"], co["deputy"])
    for w in warnings:
        _emit("warning", w)
    if users or teams:
        forge.request_reviewers(a.pr, users, teams)
        _summary(f"PR #{a.pr} by {a.author}: review requested from {', '.join(users + ['team ' + t for t in teams])}")
    else:
        _summary(f"PR #{a.pr} by {a.author}: no reviewer requested")
    return 0


def cmd_cleanup(a):
    forge = _forge(a)
    if a.event:
        ev = _event(a.event)
        pr = ev["pull_request"]
        facts = {
            "number": pr["number"], "merged": bool(pr.get("merged")), "head": pr["head"]["ref"],
            "head_repo": (pr["head"].get("repo") or {}).get("full_name"),
            "base_repo": (pr["base"].get("repo") or {}).get("full_name"),
            "head_sha": pr["head"]["sha"],
            "default_branch": (ev.get("repository") or {}).get("default_branch") or "main",
        }
    else:
        pr = forge.get_pr(a.pr)
        facts = {"number": pr["number"], "merged": pr["merged"], "head": pr["head"], "head_repo": pr["head_repo"],
                 "base_repo": pr["base_repo"], "head_sha": pr["head_sha"], "default_branch": forge.default_branch()}
    branch_sha = forge.branch_sha(facts["head"]) if facts["head_repo"] == facts["base_repo"] else None
    others = [p for p in forge.open_prs(head=facts["head"]) if p["number"] != facts["number"]]
    delete, reason = releaselib.plan_cleanup(
        merged=facts["merged"], head=facts["head"], head_repo=facts["head_repo"], base_repo=facts["base_repo"],
        default_branch=facts["default_branch"], protected=a.protected.split(), branch_sha=branch_sha,
        pr_head_sha=facts["head_sha"], other_open_prs=len(others))
    if delete and not a.dry_run:
        forge.delete_branch(facts["head"])
    _summary(f"cleanup PR #{facts['number']}: {'delete' if delete else 'keep'} `{facts['head']}`: {reason}"
             + (" (dry run)" if a.dry_run and delete else ""))
    return 0


# --- releases ---------------------------------------------------------------

def _fetch(a):
    if a.fetch:
        gitutil.git("fetch", "-q", "--prune", "--tags", "--force", a.remote, cwd=a.git_dir)


def cmd_release_pr(a):
    forge = _forge(a)
    _fetch(a)
    main_ref, dev_ref = f"{a.remote}/main", f"{a.remote}/dev"
    main_sha, dev_sha = gitutil.rev(main_ref, cwd=a.git_dir), gitutil.rev(dev_ref, cwd=a.git_dir)
    anc = gitutil.is_ancestor(main_sha, dev_sha, cwd=a.git_dir)
    commits = gitutil.first_parent_commits(main_sha, dev_sha, cwd=a.git_dir) if anc else []
    prs = {c["number"]: forge.get_pr(c["number"]) or {} for c in commits if c["number"]}
    entries, warnings = releaselib.collect_entries(commits, prs)
    for w in warnings:
        _emit("warning", w)
    try:
        plan = releaselib.plan_release_pr(
            version=a.version, summary=a.summary, tags=gitutil.tags(cwd=a.git_dir), main_sha=main_sha,
            dev_sha=dev_sha, main_is_ancestor_of_dev=anc, entries=entries,
            open_release_prs=forge.open_prs(head="dev", base="main"))
    except releaselib.Refused as r:
        for reason in r.reasons:
            _emit("error", f"release-pr refused: {reason}")
        return 1
    if a.dry_run:
        _summary(f"(dry run) would open: {plan['title']}\n\n{plan['body']}")
        return 0
    pr = forge.create_pr(plan["title"], plan["body"], head="dev", base="main")
    _summary(f"opened release PR #{pr['number']}: {plan['title']}\n\n{plan['body']}")
    return 0


def _merged_prs_for(forge, sha, git_dir):
    prs = [p for p in forge.prs_for_commit(sha) if p.get("merged") and p.get("merge_commit_sha") == sha]
    if prs:
        return prs
    subject = gitutil.git("log", "-1", "--format=%s", sha, cwd=git_dir).strip()
    m = gitutil.MERGE_PR_RE.match(subject)
    if m:
        pr = forge.get_pr(int(m.group("number")))
        if pr and pr.get("merged") and pr.get("merge_commit_sha") == sha:
            return [pr]
    return []


def cmd_release(a):
    forge = _forge(a)
    _fetch(a)
    sha = gitutil.rev(a.sha, cwd=a.git_dir)
    dev_sha = gitutil.rev(f"{a.remote}/dev", cwd=a.git_dir)
    try:
        plan = releaselib.plan_release(
            sha=sha, prs=_merged_prs_for(forge, sha, a.git_dir), tags=gitutil.tags(cwd=a.git_dir),
            tags_at_sha=gitutil.tags_at(sha, cwd=a.git_dir), release_exists=forge.release_exists,
            sha_is_ancestor_of_dev=gitutil.is_ancestor(sha, dev_sha, cwd=a.git_dir),
            dev_is_ancestor_of_sha=gitutil.is_ancestor(dev_sha, sha, cwd=a.git_dir),
            open_sync_prs=forge.open_prs(head="main", base="dev"), move_major_tag=a.move_major_tag)
    except releaselib.Refused as r:
        for reason in r.reasons:
            _emit("error", f"release refused: {reason}")
        return 1
    for w in plan["warnings"]:
        _emit("warning", w)
    done = []
    tagger = forge.bot_identity(a.app_slug) if plan["actions"] and not a.dry_run else None
    for act in plan["actions"]:
        if a.dry_run:
            done.append(f"(dry run) {act[0]} {act[1]}")
            continue
        if act[0] == "tag":
            forge.create_tag(act[1], sha, act[2], tagger)
            done.append(f"tagged {sha[:7]} as {act[1]} (annotated)")
        elif act[0] == "release":
            forge.create_release(act[1], act[2], act[3])
            done.append(f"GitHub Release {act[2]}")
        elif act[0] == "move-tag":
            forge.move_tag(act[1], act[2])
            done.append(f"moved {act[1]} to {act[2][:7]}")
        elif act[0] == "fast-forward-dev":
            if forge.fast_forward("dev", act[1]):
                done.append(f"fast-forwarded dev to {act[1][:7]}")
            elif forge.open_prs(head="main", base="dev"):
                done.append("dev moved meanwhile; a sync PR is already open")
            else:
                pr = forge.create_pr(releaselib.SYNC_TITLE, releaselib.sync_body(plan["version"], {"number": plan["pr"]}),
                                     head="main", base="dev")
                done.append(f"dev moved meanwhile: opened sync PR #{pr['number']}")
        elif act[0] == "sync-pr":
            pr = forge.create_pr(act[1], act[2], head="main", base="dev")
            done.append(f"dev has moved: opened sync PR #{pr['number']} ({act[1]})")
    head = f"release: {plan['kind']}" + (f" {plan['version']} (PR #{plan.get('pr')})" if plan["version"] else "")
    _summary("\n".join([head] + ([f"- {d}" for d in done] or ["- nothing to do"])))
    return 0


# --- labels and repo-init ------------------------------------------------

def cmd_labels(a):
    desired = labels.load_labels(a.labels)
    if a.current_file:
        current = json.loads(Path(a.current_file).read_text(encoding="utf-8"))
    else:
        out = subprocess.run(["gh", "api", "--paginate", f"repos/{a.repo}/labels"],
                             capture_output=True, text=True, check=True).stdout
        current = labels.parse_pages(out)
    cmds = labels.gh_commands(a.repo, labels.plan_labels(desired, current, a.prune))
    if not cmds:
        print("labels: already in step")
    for argv in cmds:
        print(("running: " if a.apply else "(dry run) ") + subprocess.list2cmdline(argv))
        if a.apply:
            subprocess.run(argv, check=True, capture_output=True)
    return 0


def cmd_repo_init(a):
    installs = dict(x.split("=", 1) for x in (a.installation or []))
    steps = repoinit.plan(
        a.repo, visibility=a.visibility, clone=a.clone, owner=a.owner, deputy=a.deputy,
        automation_app_id=a.automation_app_id, installations=installs, app_id_value=a.app_id_value,
        app_key_file=a.app_key_file, hooks=a.hooks, prune_labels=a.prune_labels)
    if not a.apply:
        print(f"# repo-init {a.repo}: DRY RUN, nothing is changed. Add --apply to run it.\n")
        print(repoinit.describe(steps, a.repo))
        return 0
    repoinit.apply(a.repo, steps)  # pragma: no cover
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tm.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def forge_args(sp):
        sp.add_argument("--repo", required=True, help="owner/name")
        sp.add_argument("--forge", choices=("github", "local"), default="github")
        sp.add_argument("--origin", help="local forge: the bare repo")
        sp.add_argument("--state", help="local forge: the JSON state file")
        sp.add_argument("--workdir", help="local forge: a scratch clone")

    def git_args(sp):
        sp.add_argument("--git-dir", default=".", help="the clone to read git facts from")
        sp.add_argument("--remote", default="origin")
        sp.add_argument("--fetch", action="store_true", help="git fetch the remote first")

    for name, fn, arg in (("check-branch", cmd_check_branch, "name"), ("check-subject", cmd_check_subject, "subject"),
                          ("check-title", cmd_check_title, "title")):
        sp = sub.add_parser(name)
        sp.add_argument(arg)
        sp.add_argument("--strict", action="store_true", help="exit 1 on a problem")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("check-body")
    sp.add_argument("file")
    sp.add_argument("--branch")
    sp.add_argument("--strict", action="store_true")
    sp.set_defaults(fn=cmd_check_body)
    sp = sub.add_parser("check-commits")
    sp.add_argument("range")
    sp.add_argument("--git-dir", default=".")
    sp.add_argument("--strict", action="store_true")
    sp.set_defaults(fn=cmd_check_commits)

    sp = sub.add_parser("check-format", help="CI: format checks for a GitHub event (warnings only)")
    sp.add_argument("--event", required=True)
    sp.add_argument("--event-name", required=True)
    sp.add_argument("--git-dir", default=".")
    sp.set_defaults(fn=cmd_check_format)

    sp = sub.add_parser("check-trailers", help="refuse Co-Authored-By trailers in a range or a GitHub event (blocking)")
    sp.add_argument("range", nargs="?", help="a git range such as origin/dev..HEAD")
    sp.add_argument("--event")
    sp.add_argument("--event-name")
    sp.add_argument("--git-dir", default=".")
    sp.set_defaults(fn=cmd_check_trailers)

    sp = sub.add_parser("secret-scan", help="CI: gitleaks over the commits of a GitHub event")
    sp.add_argument("--event")
    sp.add_argument("--event-name")
    sp.add_argument("--gitleaks", default="gitleaks")
    sp.add_argument("--source", default=".")
    sp.set_defaults(fn=cmd_secret_scan)

    sp = sub.add_parser("assign-reviewers")
    forge_args(sp)
    sp.add_argument("--pr", type=int, required=True)
    sp.add_argument("--author", required=True)
    sp.add_argument("--base", required=True)
    sp.set_defaults(fn=cmd_assign_reviewers)

    sp = sub.add_parser("cleanup")
    forge_args(sp)
    sp.add_argument("--event", help="the pull_request event file (CI)")
    sp.add_argument("--pr", type=int, help="or a PR number")
    sp.add_argument("--protected", default="dev main", help="extra branches never deleted (dev and main always are)")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_cleanup)

    sp = sub.add_parser("release-pr")
    forge_args(sp)
    git_args(sp)
    sp.add_argument("--version", required=True)
    sp.add_argument("--summary", default="")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_release_pr)

    sp = sub.add_parser("release")
    forge_args(sp)
    git_args(sp)
    sp.add_argument("--sha", required=True, help="the commit pushed to main")
    sp.add_argument("--move-major-tag", action="store_true", help="workflows repo only: move vMAJOR")
    sp.add_argument("--app-slug", default=os.environ.get("APP_SLUG"))
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_release)

    sp = sub.add_parser("labels")
    sp.add_argument("--repo", required=True)
    sp.add_argument("--labels", help="label file (default: labels.json of this repo)")
    sp.add_argument("--current-file", help="read the current labels from this JSON file instead of gh")
    sp.add_argument("--prune", action="store_true", help="also delete labels outside the set")
    sp.add_argument("--apply", action="store_true", help="run the commands (default: print them)")
    sp.set_defaults(fn=cmd_labels)

    sp = sub.add_parser("repo-init", help="apply git.md 13 to a repo (dry run unless --apply)")
    sp.add_argument("--repo", required=True)
    sp.add_argument("--visibility", choices=("private", "public"), default="private")
    sp.add_argument("--clone", help="the owner's clone: gets the files and the hooks")
    sp.add_argument("--owner", help="GitHub handle of the repo owner (CODEOWNERS)")
    sp.add_argument("--deputy", help="GitHub handle of the deputy (CODEOWNERS)")
    sp.add_argument("--automation-app-id", help="numeric id of the automation App (ruleset bypass)")
    sp.add_argument("--installation", action="append", help="APP=INSTALLATION_ID, APP one of launchpad, hub, automation")
    sp.add_argument("--app-id-value", help="value for the repo secret AUTOMATION_APP_ID")
    sp.add_argument("--app-key-file", help="key file for the repo secret AUTOMATION_APP_KEY (read by gh, never printed)")
    sp.add_argument("--hooks", choices=("plain", "pre-commit", "none"), default="plain")
    sp.add_argument("--prune-labels", action="store_true")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(fn=cmd_repo_init)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (ForgeError, gitutil.GitError) as e:
        _emit("error", str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
