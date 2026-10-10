"""Where pull requests, tags and releases live.

`GitHubForge` talks to the GitHub REST API with a token from the environment
(in CI: the short-lived token of the automation App, or the job's own token).
`LocalForge` plays the same part against a local bare repository and a JSON
file, so the whole flow can be rehearsed without GitHub (the dry run, tests).
It also simulates what only GitHub does (merging a PR with GitHub's default
message, closing keywords on the default branch); those parts are marked and
are exactly what has to be verified on GitHub itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import fmt


class ForgeError(RuntimeError):
    pass


def _pr(raw: dict) -> dict:
    """Normalise a GitHub pull request object."""
    head = raw.get("head") or {}
    base = raw.get("base") or {}
    return {
        "number": raw.get("number"),
        "title": raw.get("title") or "",
        "body": raw.get("body") or "",
        "state": raw.get("state"),
        "merged": bool(raw.get("merged") or raw.get("merged_at")),
        "merge_commit_sha": raw.get("merge_commit_sha"),
        "head": head.get("ref"),
        "head_sha": head.get("sha"),
        "head_repo": ((head.get("repo") or {}).get("full_name")),
        "base": base.get("ref"),
        "base_repo": ((base.get("repo") or {}).get("full_name")),
        "user": ((raw.get("user") or {}).get("login")),
    }


class GitHubForge:
    def __init__(self, repo: str, token: str | None = None, api_url: str | None = None):
        if "/" not in repo:
            raise ForgeError(f"repo must be owner/name, got {repo!r}")
        self.repo = repo
        self.owner = repo.split("/")[0]
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            raise ForgeError("no token: set GH_TOKEN")
        self.api = (api_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")

    # -- transport --------------------------------------------------------
    def _req(self, method: str, path: str, data=None, ok=(200, 201, 204), missing_ok=False):
        url = path if path.startswith("http") else f"{self.api}{path}"
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "techmirion-workflows",
            **({"Content-Type": "application/json"} if body is not None else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if missing_ok and e.code == 404:
                return None
            detail = e.read().decode(errors="replace")[:500]
            raise ForgeError(f"{method} {path}: HTTP {e.code} {detail}") from None

    def _r(self, path: str) -> str:
        return f"/repos/{self.repo}{path}"

    # -- reads ------------------------------------------------------------
    def get_pr(self, number: int) -> dict | None:
        raw = self._req("GET", self._r(f"/pulls/{number}"), missing_ok=True)
        return _pr(raw) if raw else None

    def prs_for_commit(self, sha: str) -> list[dict]:
        raw = self._req("GET", self._r(f"/commits/{sha}/pulls?per_page=100")) or []
        return [_pr(p) for p in raw]

    def open_prs(self, head: str | None = None, base: str | None = None) -> list[dict]:
        q = {"state": "open", "per_page": "100"}
        if head:
            q["head"] = f"{self.owner}:{head}"
        if base:
            q["base"] = base
        raw = self._req("GET", self._r("/pulls?" + urllib.parse.urlencode(q))) or []
        return [_pr(p) for p in raw]

    def branch_sha(self, branch: str) -> str | None:
        raw = self._req("GET", self._r(f"/git/ref/heads/{urllib.parse.quote(branch)}"), missing_ok=True)
        return raw["object"]["sha"] if raw else None

    def default_branch(self) -> str:
        return self._req("GET", self._r(""))["default_branch"]

    def get_file(self, path: str, ref: str) -> str | None:
        import base64
        raw = self._req("GET", self._r(f"/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref)}"), missing_ok=True)
        if not raw or raw.get("encoding") != "base64":
            return None
        return base64.b64decode(raw["content"]).decode("utf-8", errors="replace")

    def release_exists(self, tag: str) -> bool:
        return self._req("GET", self._r(f"/releases/tags/{urllib.parse.quote(tag)}"), missing_ok=True) is not None

    def bot_identity(self, app_slug: str | None):
        if not app_slug:
            return None
        login = f"{app_slug}[bot]"
        raw = self._req("GET", f"/users/{urllib.parse.quote(login)}", missing_ok=True)
        if not raw:
            return None
        return {"name": login, "email": f"{raw['id']}+{login}@users.noreply.github.com"}

    # -- writes -----------------------------------------------------------
    def create_pr(self, title: str, body: str, head: str, base: str) -> dict:
        return _pr(self._req("POST", self._r("/pulls"), {
            "title": title, "body": body, "head": head, "base": base,
        }))

    def create_tag(self, tag: str, sha: str, message: str, tagger: dict | None = None) -> None:
        """An annotated tag: a tag object, then the ref pointing at it."""
        data = {"tag": tag, "message": message, "object": sha, "type": "commit"}
        if tagger:
            data["tagger"] = tagger
        obj = self._req("POST", self._r("/git/tags"), data)
        self._req("POST", self._r("/git/refs"), {"ref": f"refs/tags/{tag}", "sha": obj["sha"]})

    def move_tag(self, tag: str, sha: str) -> None:
        """Point a moving major tag (`v1`, workflows repo only) at a commit."""
        path = self._r(f"/git/refs/tags/{urllib.parse.quote(tag)}")
        if self._req("GET", self._r(f"/git/ref/tags/{urllib.parse.quote(tag)}"), missing_ok=True):
            self._req("PATCH", path, {"sha": sha, "force": True})
        else:
            self._req("POST", self._r("/git/refs"), {"ref": f"refs/tags/{tag}", "sha": sha})

    def create_release(self, tag: str, name: str, body: str) -> None:
        self._req("POST", self._r("/releases"), {
            "tag_name": tag, "name": name, "body": body, "draft": False, "prerelease": False,
            "make_latest": "true",
        })

    def fast_forward(self, branch: str, sha: str) -> bool:
        """Move a branch to `sha` only if that is a fast-forward (force=false)."""
        try:
            self._req("PATCH", self._r(f"/git/refs/heads/{urllib.parse.quote(branch)}"), {"sha": sha, "force": False})
            return True
        except ForgeError as e:
            if "HTTP 422" in str(e):
                return False
            raise

    def delete_branch(self, branch: str) -> None:
        self._req("DELETE", self._r(f"/git/refs/heads/{urllib.parse.quote(branch)}"))

    def request_reviewers(self, number: int, users: list[str], teams: list[str]) -> None:
        self._req("POST", self._r(f"/pulls/{number}/requested_reviewers"), {
            "reviewers": users, "team_reviewers": teams,
        })


class LocalForge:
    """A stand-in for GitHub: a bare repo as `origin` and a JSON state file.

    State: issues and PRs share one number sequence, like GitHub.
    """

    def __init__(self, repo: str, origin: str, state_file: str, workdir: str, default_branch: str = "main"):
        self.repo = repo
        self.owner = repo.split("/")[0]
        self.origin = origin
        self.state_file = Path(state_file)
        self.workdir = Path(workdir)
        self._default = default_branch
        if not self.state_file.exists():
            self._save({"next": 1, "issues": {}, "prs": {}, "releases": {}, "reviews": {}, "log": []})
        if not (self.workdir / ".git").exists():
            self.workdir.parent.mkdir(parents=True, exist_ok=True)
            self._git_raw("clone", "-q", origin, str(self.workdir), cwd=None)
            self._git("config", "user.name", "techmirion-automation[bot]")
            self._git("config", "user.email", "automation@example.invalid")
            self._git("config", "commit.gpgsign", "false")
            self._git("config", "tag.gpgsign", "false")

    # -- plumbing ---------------------------------------------------------
    def _load(self) -> dict:
        return json.loads(self.state_file.read_text())

    def _save(self, st: dict) -> None:
        self.state_file.write_text(json.dumps(st, indent=1, sort_keys=True))

    def _git_raw(self, *args, cwd):
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        if p.returncode != 0:
            raise ForgeError(f"git {' '.join(args)}: {p.stderr.strip()}")
        return p.stdout

    def _git(self, *args):
        return self._git_raw(*args, cwd=str(self.workdir))

    def _fetch(self):
        self._git("fetch", "-q", "--prune", "--tags", "--force", "origin")

    def _log(self, st, what):
        st["log"].append(what)

    # -- issues (sandbox only) ---------------------------------------------
    def create_issue(self, title: str, labels=()) -> int:
        st = self._load()
        n = st["next"]
        st["next"] = n + 1
        st["issues"][str(n)] = {"number": n, "title": title, "state": "open", "labels": list(labels), "closed_by": None}
        self._log(st, f"issue #{n} opened: {title}")
        self._save(st)
        return n

    def issue(self, n: int) -> dict:
        return self._load()["issues"][str(n)]

    # -- reads ------------------------------------------------------------
    def get_pr(self, number: int) -> dict | None:
        return self._load()["prs"].get(str(number))

    def prs_for_commit(self, sha: str) -> list[dict]:
        return [p for p in self._load()["prs"].values() if p.get("merge_commit_sha") == sha]

    def open_prs(self, head: str | None = None, base: str | None = None) -> list[dict]:
        return [p for p in self._load()["prs"].values()
                if p["state"] == "open" and (head is None or p["head"] == head) and (base is None or p["base"] == base)]

    def branch_sha(self, branch: str) -> str | None:
        out = self._git("ls-remote", "origin", f"refs/heads/{branch}").split()
        return out[0] if out else None

    def default_branch(self) -> str:
        return self._default

    def get_file(self, path: str, ref: str) -> str | None:
        self._fetch()
        p = subprocess.run(["git", "show", f"origin/{ref}:{path}"], cwd=self.workdir, capture_output=True, text=True)
        return p.stdout if p.returncode == 0 else None

    def release_exists(self, tag: str) -> bool:
        return tag in self._load()["releases"]

    def bot_identity(self, app_slug):
        return {"name": "techmirion-automation[bot]", "email": "automation@example.invalid"}

    # -- writes -----------------------------------------------------------
    def create_pr(self, title: str, body: str, head: str, base: str, user: str = "techmirion-automation[bot]") -> dict:
        st = self._load()
        n = st["next"]
        st["next"] = n + 1
        pr = {
            "number": n, "title": title, "body": body, "state": "open", "merged": False,
            "merge_commit_sha": None, "head": head, "head_sha": self.branch_sha(head), "head_repo": self.repo,
            "base": base, "base_repo": self.repo, "user": user,
        }
        st["prs"][str(n)] = pr
        self._log(st, f"PR #{n} opened: {head} -> {base}: {title}")
        self._save(st)
        return pr

    def merge_pr(self, number: int, merger: str = "fotis") -> str:
        """SIMULATED GitHub merge button: --no-ff, GitHub's default message, closing keywords.

        GitHub closes `Fixes #N` issues only when the PR's base is the default branch.
        """
        st = self._load()
        pr = st["prs"][str(number)]
        if pr["state"] != "open":
            raise ForgeError(f"PR #{number} is not open")
        self._fetch()
        self._git("checkout", "-q", "-B", pr["base"], f"origin/{pr['base']}")
        msg = f"Merge pull request #{number} from {self.owner}/{pr['head']}"
        self._git("merge", "-q", "--no-ff", "--no-edit", "-m", msg, "-m", pr["title"], f"origin/{pr['head']}")
        sha = self._git("rev-parse", "HEAD").strip()
        self._git("push", "-q", "origin", f"HEAD:refs/heads/{pr['base']}")
        pr.update(state="closed", merged=True, merge_commit_sha=sha, head_sha=self.branch_sha(pr["head"]))
        closed = []
        if pr["base"] == self._default:
            for ref in fmt.closing_refs(pr["body"]):
                if ref.startswith("#") and ref[1:] in st["issues"]:
                    iss = st["issues"][ref[1:]]
                    if iss["state"] == "open":
                        iss.update(state="closed", closed_by=f"PR #{number}")
                        closed.append(ref)
        self._log(st, f"PR #{number} merged by {merger} as {sha[:7]}" + (f", closed {', '.join(closed)}" if closed else ""))
        self._save(st)
        return sha

    def create_tag(self, tag: str, sha: str, message: str, tagger: dict | None = None) -> None:
        self._fetch()
        self._git("tag", "-a", tag, sha, "-m", message)
        self._git("push", "-q", "origin", f"refs/tags/{tag}")
        st = self._load()
        self._log(st, f"tag {tag} -> {sha[:7]} (annotated)")
        self._save(st)

    def move_tag(self, tag: str, sha: str) -> None:
        self._fetch()
        self._git("tag", "-f", tag, sha)
        self._git("push", "-q", "--force", "origin", f"refs/tags/{tag}")
        st = self._load()
        self._log(st, f"moving tag {tag} -> {sha[:7]}")
        self._save(st)

    def create_release(self, tag: str, name: str, body: str) -> None:
        st = self._load()
        st["releases"][tag] = {"name": name, "body": body}
        self._log(st, f"GitHub Release {name}")
        self._save(st)

    def fast_forward(self, branch: str, sha: str) -> bool:
        p = subprocess.run(["git", "push", "-q", "origin", f"{sha}:refs/heads/{branch}"],
                           cwd=self.workdir, capture_output=True, text=True)
        st = self._load()
        self._log(st, f"fast-forward {branch} -> {sha[:7]}: {'ok' if p.returncode == 0 else 'refused (not a fast-forward)'}")
        self._save(st)
        return p.returncode == 0

    def delete_branch(self, branch: str) -> None:
        if branch in ("dev", "main"):
            raise ForgeError(f"refusing to delete {branch}")
        self._git("push", "-q", "origin", "--delete", branch)
        st = self._load()
        self._log(st, f"branch {branch} deleted")
        self._save(st)

    def request_reviewers(self, number: int, users: list[str], teams: list[str]) -> None:
        st = self._load()
        st["reviews"][str(number)] = {"users": users, "teams": teams}
        self._log(st, f"PR #{number}: review requested from {', '.join(users + teams)}")
        self._save(st)


def make_forge(kind: str, repo: str, **kw):
    if kind == "github":
        return GitHubForge(repo)
    if kind == "local":
        return LocalForge(repo, kw["origin"], kw["state"], kw["workdir"])
    raise ForgeError(f"unknown forge {kind!r}")
