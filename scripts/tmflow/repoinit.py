"""repo-init: apply git.md section 13 to one repo, with `gh api`.

Dry run by default: it prints every command and changes nothing, and it does
not even call `gh`. With --apply it runs them as the person who is logged in to
`gh` (an org owner or the repo's admin). Never run it on a repo you do not own.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path

from . import labels as labelmod

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
RULESET_NAME = "techmirion: main and dev"
#: Check run name of the reusable `checks` workflow, as GitHub reports it for a
#: caller job named `checks`. To verify on GitHub (build.md S3).
CHECK_CONTEXT = "checks / checks"

SETTINGS = [
    "-f", "default_branch=main",
    "-F", "allow_merge_commit=true",
    "-F", "allow_squash_merge=false",
    "-F", "allow_rebase_merge=false",
    "-F", "allow_auto_merge=false",
    "-F", "delete_branch_on_merge=false",
    "-F", "allow_update_branch=true",
    "-f", "merge_commit_title=MERGE_MESSAGE",
    "-f", "merge_commit_message=PR_TITLE",
    "-F", "has_issues=true",
    "-F", "has_wiki=false",
    "-F", "has_projects=false",
]

#: Files a repo gets from templates/ (source relative to templates/, target relative to the clone).
TEMPLATE_FILES = [
    ".github/workflows/checks.yml",
    ".github/workflows/assign-reviewers.yml",
    ".github/workflows/cleanup.yml",
    ".github/workflows/release-pr.yml",
    ".github/workflows/release.yml",
    ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/bug.yml",
    ".github/ISSUE_TEMPLATE/feature.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
    ".pre-commit-config.yaml",
]


def ruleset(app_id: str | None) -> dict:
    rs = {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/main", "refs/heads/dev"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "pull_request", "parameters": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews_on_push": False,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_review_thread_resolution": False,
                "allowed_merge_methods": ["merge"],
            }},
            {"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": False,
                "required_status_checks": [{"context": CHECK_CONTEXT}],
            }},
        ],
        "bypass_actors": [],
    }
    if app_id:
        # The automation App fast-forwards dev and pushes tags (git.md 14).
        rs["bypass_actors"].append({"actor_id": int(app_id), "actor_type": "Integration", "bypass_mode": "always"})
    return rs


def plan(repo: str, *, visibility: str = "private", clone: str | None = None, owner: str | None = None,
         deputy: str | None = None, automation_app_id: str | None = None, installations: dict | None = None,
         app_id_value: str | None = None, app_key_file: str | None = None, hooks: str = "plain",
         prune_labels: bool = False) -> list[dict]:
    steps = [
        {"kind": "gh", "what": "merge commits only, head-branch auto-delete off, issues on, wiki and projects off",
         "argv": ["gh", "api", "-X", "PATCH", f"repos/{repo}", *SETTINGS]},
        {"kind": "ensure-dev", "what": "a dev branch, created from main if missing"},
        {"kind": "gh", "what": "Dependabot alerts on",
         "argv": ["gh", "api", "-X", "PUT", f"repos/{repo}/vulnerability-alerts"]},
        {"kind": "gh", "what": "Dependabot security-update PRs off (they would target main)",
         "argv": ["gh", "api", "-X", "DELETE", f"repos/{repo}/automated-security-fixes"]},
    ]
    if visibility == "public":
        steps.append({"kind": "gh", "what": "secret scanning and push protection on (public repo)",
                      "argv": ["gh", "api", "-X", "PATCH", f"repos/{repo}", "--input", "-"],
                      "stdin_json": {"security_and_analysis": {
                          "secret_scanning": {"status": "enabled"},
                          "secret_scanning_push_protection": {"status": "enabled"}}}})
        steps.append({"kind": "ruleset", "what": "branch protection on main and dev (public repo)",
                      "argv": ["gh", "api", "-X", "POST", f"repos/{repo}/rulesets", "--input", "-"],
                      "stdin_json": ruleset(automation_app_id)})
    else:
        steps.append({"kind": "manual", "what": "private repo on the free plan: no branch protection or secret "
                      "scanning is available; the checks and the platform's production rule hold the line"})
    steps.append({"kind": "labels", "what": "the label set of git.md 13" + (" (other labels deleted)" if prune_labels else ""),
                  "prune": prune_labels})
    for app in ("launchpad", "hub", "automation"):
        inst = (installations or {}).get(app)
        if inst:
            steps.append({"kind": "app", "what": f"install the {app} App on the repo", "installation": inst})
        else:
            steps.append({"kind": "manual", "what": f"install the {app} App on {repo}: org Settings > GitHub Apps > "
                          f"{app} > Configure > Repository access (or pass --installation {app}=ID)"})
    if app_id_value:
        steps.append({"kind": "gh", "what": "repo secret AUTOMATION_APP_ID (org secrets do not reach private repos on the free plan)",
                      "argv": ["gh", "secret", "set", "AUTOMATION_APP_ID", "--repo", repo, "--body", app_id_value]})
    else:
        steps.append({"kind": "manual", "what": f"repo secret: gh secret set AUTOMATION_APP_ID --repo {repo}   (prompts for the value)"})
    if app_key_file:
        steps.append({"kind": "gh", "what": "repo secret AUTOMATION_APP_KEY, read by gh from the key file (never printed)",
                      "argv": ["gh", "secret", "set", "AUTOMATION_APP_KEY", "--repo", repo], "stdin_file": app_key_file})
    else:
        steps.append({"kind": "manual", "what": f"repo secret: gh secret set AUTOMATION_APP_KEY --repo {repo} < /path/to/automation-app.pem"})
    if clone:
        steps.append({"kind": "files", "what": f"files of git.md 13 from templates/ into {clone} (existing files are kept)",
                      "clone": clone, "owner": owner, "deputy": deputy})
        if hooks == "plain":
            steps.append({"kind": "local", "what": "git hooks (plain, no network)",
                          "argv": ["sh", str(ROOT / "scripts" / "install-hooks.sh"), clone]})
        elif hooks == "pre-commit":
            steps.append({"kind": "local", "what": "git hooks through pre-commit", "cwd": clone,
                          "argv": ["pre-commit", "install"]})
    return steps


def template_targets(clone: str) -> list[tuple[Path, Path]]:
    return [(TEMPLATES / rel, Path(clone) / rel) for rel in TEMPLATE_FILES]


def merge_gitignore(existing: str, template: str) -> str:
    """Append the template's lines that `existing` lacks. Never removes an entry."""
    have = {ln.strip() for ln in existing.splitlines()}
    missing = [ln for ln in template.splitlines() if ln.strip() and not ln.startswith("#") and ln.strip() not in have]
    if not missing:
        return existing
    out = existing if existing.endswith("\n") or not existing else existing + "\n"
    return out + "\n# git.md section 11 (added by repo-init)\n" + "\n".join(missing) + "\n"


def render_template(src: Path, owner: str | None, deputy: str | None) -> str:
    text = src.read_text(encoding="utf-8")
    if src.name == "CODEOWNERS":
        text = text.replace("@OWNER", f"@{owner}" if owner else "@OWNER")
        text = text.replace("@DEPUTY", f"@{deputy}" if deputy else "@DEPUTY")
    return text


def describe(steps: list[dict], repo: str = "REPO") -> str:
    out = []
    for s in steps:
        out.append(f"# {s['what']}")
        if s["kind"] in ("gh", "ruleset", "local"):
            cmd = shlex.join(s["argv"])
            if "cwd" in s:
                cmd = f"(cd {shlex.quote(s['cwd'])} && {cmd})"
            if "stdin_json" in s:
                cmd += " <<'JSON'\n" + json.dumps(s["stdin_json"], indent=2) + "\nJSON"
            elif "stdin_file" in s:
                cmd += f" < {shlex.quote(s['stdin_file'])}"
            out.append(cmd)
        elif s["kind"] == "ensure-dev":
            out.append(f"gh api repos/{repo}/branches/dev  ||  gh api -X POST repos/{repo}/git/refs -f ref=refs/heads/dev -f sha=<main sha>")
        elif s["kind"] == "labels":
            out.append(f"scripts/tm.py labels --repo {repo} --apply" + (" --prune" if s.get("prune") else ""))
        elif s["kind"] == "app":
            out.append(f"gh api -X PUT user/installations/{s['installation']}/repositories/<repo id>")
        elif s["kind"] == "files":
            for src, dst in template_targets(s["clone"]):
                out.append(f"{'keep' if dst.exists() else 'copy'} {dst}")
            out.append(f"merge missing git.md 11 entries into {Path(s['clone']) / '.gitignore'}")
        elif s["kind"] == "manual":
            out.append("# (by hand)")
        out.append("")
    return "\n".join(out)


def _run(argv, stdin=None, cwd=None, check=True):
    return subprocess.run(argv, input=stdin, cwd=cwd, text=True, check=check, capture_output=True)


def apply(repo: str, steps: list[dict]) -> None:  # pragma: no cover - needs GitHub
    for s in steps:
        print(f"== {s['what']}")
        k = s["kind"]
        if k == "gh":
            if "stdin_file" in s:
                with open(s["stdin_file"], "rb") as f:
                    subprocess.run(s["argv"], stdin=f, check=True)
            else:
                _run(s["argv"], stdin=json.dumps(s["stdin_json"]) if "stdin_json" in s else None)
        elif k == "ruleset":
            names = _run(["gh", "api", f"repos/{repo}/rulesets", "--jq", ".[].name"]).stdout.split("\n")
            if RULESET_NAME in names:
                print("   already there")
            else:
                _run(s["argv"], stdin=json.dumps(s["stdin_json"]))
        elif k == "ensure-dev":
            if _run(["gh", "api", f"repos/{repo}/branches/dev"], check=False).returncode != 0:
                sha = _run(["gh", "api", f"repos/{repo}/git/ref/heads/main", "--jq", ".object.sha"]).stdout.strip()
                _run(["gh", "api", "-X", "POST", f"repos/{repo}/git/refs", "-f", "ref=refs/heads/dev", "-f", f"sha={sha}"])
            else:
                print("   already there")
        elif k == "labels":
            current = json.loads(_run(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/labels"]).stdout)
            current = [lab for page in current for lab in page]
            for argv in labelmod.gh_commands(repo, labelmod.plan_labels(labelmod.load_labels(), current, s.get("prune"))):
                _run(argv)
        elif k == "app":
            repo_id = _run(["gh", "api", f"repos/{repo}", "--jq", ".id"]).stdout.strip()
            _run(["gh", "api", "-X", "PUT", f"user/installations/{s['installation']}/repositories/{repo_id}"])
        elif k == "files":
            for src, dst in template_targets(s["clone"]):
                if dst.exists():
                    print(f"   keep {dst}")
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(render_template(src, s.get("owner"), s.get("deputy")), encoding="utf-8")
                shutil.copymode(src, dst)
                print(f"   wrote {dst}")
            gi = Path(s["clone"]) / ".gitignore"
            old = gi.read_text(encoding="utf-8") if gi.exists() else ""
            new = merge_gitignore(old, (TEMPLATES / "gitignore").read_text(encoding="utf-8"))
            if new != old:
                gi.write_text(new, encoding="utf-8")
                print(f"   updated {gi}")
        elif k == "local":
            subprocess.run(s["argv"], cwd=s.get("cwd"), check=True)
        elif k == "manual":
            print("   by hand, see above")
