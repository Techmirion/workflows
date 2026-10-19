# Techmirion/workflows

The shared automation of the Techmirion org: the one place where the git flow
(`git.md` sections 4 to 14) is implemented, so that no repo keeps its own copy.
Public, with no secrets in it, so public and private repos can both call it.

What is here:

| Path | What it is |
|---|---|
| `.github/workflows/{checks,assign-reviewers,cleanup,release-pr,release,sync-dev}.yml` | the reusable workflows (`on: workflow_call`) |
| `.github/workflows/self-*.yml` | this repo's own callers of the same workflows |
| `templates/` | ready-to-copy files for a repo: the five caller workflows (plus `sync-dev.yml`, see below), PR template, issue forms, CODEOWNERS, dependabot, pre-commit config, the `.gitignore` block of section 11 |
| `hooks/` | the git hooks: `pre-commit` (gitleaks), `prepare-commit-msg` (adds `(#N)`), `commit-msg` (checks the format, refuses `Co-Authored-By:`) |
| `.pre-commit-hooks.yaml` | the same hooks, for `pre-commit` |
| `labels.json` | the one label set (section 13), plus `wip` (section 6) |
| `scripts/tm.py` + `scripts/tmflow/` | all the logic, Python standard library only; the workflows only call it |
| `scripts/tmflow/doctor.py` | `tm.py doctor`: checks a clone before a commit, push or PR and fixes the safe things (below) |
| `scripts/install-hooks.sh` | installs the hooks as plain git hooks, no network |
| `scripts/install-gitleaks.sh` | installs the gitleaks binary (checksum-verified) into `~/.local/bin` |
| `tests/` | pytest for all of it |

## How a repo calls it

Copy `templates/.github/` into the repo (or let `repo-init` do it) and set the
repo's own commands in `checks.yml`:

```yaml
jobs:
  checks:
    uses: Techmirion/workflows/.github/workflows/checks.yml@v1
    with:
      test-command: "python3 -m pytest -q"
      lint-command: "ruff check ."
      workflows-ref: v1        # keep equal to the ref after @
```

| Caller | Trigger | Does |
|---|---|---|
| `checks.yml` | PR into dev or main (opened, synchronize, reopened, edited); push to dev or main | tests, lint, gitleaks and `Co-Authored-By:` trailers over the new commits (these fail); format checks of branch, commits, PR title and body as **warnings** |
| `assign-reviewers.yml` | `pull_request_target` opened, reopened | requests the owner from CODEOWNERS, or the deputy when the owner wrote the PR |
| `cleanup.yml` | PR closed and merged | deletes the head branch; never `dev`, `main`, the default branch, a fork's branch, or a branch with commits that were not merged |
| `release-pr.yml` | by hand (Actions tab or the hub) with a version | refuses unless main is an ancestor of dev and the version is above the last tag; opens `release: vX.Y.Z <summary>` with `Version: vX.Y.Z`, the merged PRs and one `Fixes #N` per completed issue (reverted PRs left out) |
| `release.yml` | push to main | finds the merged PR; version from `Version:` (release) or next patch (head `hotfix/*`); annotated tag; GitHub Release; fast-forwards dev, or opens `chore(release): sync main into dev` if dev moved |
| `sync-dev.yml` | push to main | **only for a repo not on the full flow** (no `release.yml`): fast-forwards dev to main, or opens `chore(release): sync main into dev` if dev moved; never tags or releases. Not copied by `repo-init` |

Why `sync-dev`: merging `dev` into `main` leaves the merge commit on `main`
only, so `dev` falls one commit behind with every merge. On the full flow
`release` brings it back. A repo that merges `dev` into `main` without a
release PR copies `templates/.github/workflows/sync-dev.yml` instead. Do not
use both: they would race.

`release-pr`, `release` and `sync-dev` need the automation App: the repo passes
`AUTOMATION_APP_ID` and `AUTOMATION_APP_KEY` as secrets (see the templates).
On the free plan, organisation secrets do not reach private repos, so private
repos need them as repo secrets (`repo-init` prints the commands).

Format rules (`scripts/tmflow/fmt.py`):

- branch `type/N-topic` or `type/topic`, type one of `feat fix chore docs refactor test hotfix`; exempt `dev`, `main`, `dependabot/*`;
- commit subject and PR title `type(place): message`, optional `(#N)`, `(#N, #M)`, `(Owner/repo#N)` at the end; PR titles may also be `release: vX.Y.Z <summary>`; exempt merges, reverts, Dependabot and the sync PR;
- no commit carries a `Co-Authored-By:` trailer, merges, reverts and bots included: the `commit-msg` hook refuses it and `checks` fails on it (`tm.py check-trailers`, also usable locally: `python3 scripts/tm.py check-trailers origin/dev..HEAD`);
- PR body: a line `Fixes #N` or `Refs #N`, or `- [x] No issue needed`.

## Doctor: check and fix before you push

`tm.py doctor` looks at a clone the way the checks will: the branch name, the
commits that are not on `dev` yet (format, `(#N)`, `Co-Authored-By:`, assistant
lines, `Fixes` in commits, the author), staged or committed secrets and
generated files, the hooks, and the open PR (title, body, base). It never
blocks: the exit code is 0 unless `--strict`.

```sh
python3 scripts/tm.py doctor /path/to/clone            # report
python3 scripts/tm.py doctor /path/to/clone --fix      # and fix the safe things
python3 scripts/tm.py doctor /path/to/clone --fix-pr   # and the PR title and base on GitHub
python3 scripts/tm.py doctor --json                    # for agents
```

`--fix` only ever: rewords commits that are **not on any remote** (drops
`Co-Authored-By:` and assistant lines and `Fixes #N` body lines, adds `(#N)` from
the branch; trees, authors and dates unchanged; it prints the old tip for undo),
renames a branch that is not on any remote when lowercasing makes it valid, and
unstages secret-like or generated files (never deletes them). Everything else it
reports with the command to run. Pushed history is never rewritten.

The profile follows `origin`: **flow** (a Techmirion repo whose `checks.yml`
calls this repo), **team** (a Techmirion repo off the flow: format findings are
notes), **live** (a `/shared/hub` repo: `#N` is a hub task and `closes #N` is
allowed, no PRs), **other** (only secrets, generated files and trailers).

`doctor.py` needs only `fmt.py`, so the team skill `techmirion-standards`
(`/shared/skills`) carries a copy: that is how every agent on the boxes runs it.

## Hooks

Plain git hooks, no network (what to use until the remote exists):

```sh
sh scripts/install-gitleaks.sh            # once per machine, into ~/.local/bin
sh scripts/install-hooks.sh /path/to/clone
```

Hooks refuse a bad commit message by default. `git config techmirion.hooks warn`
in a clone turns that into a warning: the commit is kept, and `tm.py doctor
--fix` cleans it up before the push. The gitleaks hook still refuses a secret,
and CI still fails on `Co-Authored-By:`.

Or through pre-commit, once this repo is on GitHub (`templates/.pre-commit-config.yaml`):

```sh
pip install pre-commit
pre-commit install --hook-type pre-commit --hook-type prepare-commit-msg --hook-type commit-msg
```

Uninstall the plain hooks first (`sh scripts/install-hooks.sh --uninstall .`),
or pre-commit keeps them as `.legacy` hooks and runs both. The pre-commit `rev`
is a full version (`v1.0.0`): pre-commit never updates a moving tag such as `v1`.

The hooks add `(#N)` on a `type/N-topic` branch, refuse a subject that does not
match the format, refuse `Fixes #N` in a commit (the release closes issues),
and refuse a commit with a secret in it, or when gitleaks is missing.

## repo-init

```sh
python3 scripts/tm.py repo-init --repo Techmirion/<repo> --clone /path/to/clone --owner <handle>        # dry run
python3 scripts/tm.py repo-init --repo Techmirion/<repo> --clone /path/to/clone --owner <handle> --apply
```

Applies section 13 with `gh api`: merge-commit-only, head-branch auto-delete off,
issues on, wiki and projects off, a `dev` branch, Dependabot alerts on and its
security PRs off, the labels, branch protection and secret scanning for public
repos, the three Apps, the files from `templates/`, and the hooks. Without
`--apply` it prints the commands and changes nothing.

Labels alone: `python3 scripts/tm.py labels --repo Techmirion/<repo>` (dry run), `--apply`, `--prune`.

## Versions

Callers use `@v1`, a moving major tag: the one tag that moves (git.md 12). A
change here is a PR into `dev`, released like any other repo; this repo's own
release workflow also moves `v1` (`move-major-tag: true`). Every third-party
action is pinned to a full commit SHA with its version in a comment.

## Developing

```sh
pip install -r requirements-dev.txt
python3 -m pytest -q
sh scripts/lint.sh        # actionlint, yamllint, shellcheck, python compile
```
