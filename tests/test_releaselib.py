"""Decision logic of release-pr, release and cleanup (git.md 8, 9, 12, 14)."""
import pytest

from tmflow import releaselib as rl

MAIN, DEV, M2 = "a" * 40, "b" * 40, "c" * 40


def merge(n, head, title, sha=None):
    return {"sha": sha or f"{n:040d}", "subject": f"Merge pull request #{n} from Techmirion/{head}",
            "body": title, "merge": True, "number": n, "head": f"Techmirion/{head}"}


def pr(n, head, title, body, base="dev"):
    return {"number": n, "head": head, "title": title, "body": body, "base": base}


def entries_for(*items):
    merges = [merge(n, head, title) for n, head, title, _ in items]
    prs = {n: pr(n, head, title, body) for n, head, title, body in items}
    return rl.collect_entries(merges, prs)


def plan_pr(entries, **kw):
    args = dict(version="v1.3.0", summary="search", tags=["v1.2.0", "v1"], main_sha=MAIN, dev_sha=DEV,
                main_is_ancestor_of_dev=True, entries=entries, open_release_prs=[])
    args.update(kw)
    return rl.plan_release_pr(**args)


# --- release-pr ---------------------------------------------------------------

def test_release_pr_title_and_body():
    entries, warnings = entries_for(
        (12, "fix/34-login", "fix(auth): redirect after login (#34)", "What\n\nFixes #34\n"),
        (15, "feat/40-search", "feat(search): greek stemming (#40)", "Refs #40\nFixes #41"),
        (16, "chore/ci", "chore(ci): pins", "- [x] No issue needed"),
    )
    assert warnings == []
    plan = plan_pr(entries)
    assert plan["title"] == "release: v1.3.0 search"
    body = plan["body"]
    assert body.splitlines()[0] == "Version: v1.3.0"
    assert "- #12 fix(auth): redirect after login (#34)" in body
    assert "- #16 chore(ci): pins" in body
    fixes = [ln for ln in body.splitlines() if ln.startswith("Fixes ")]
    assert fixes == ["Fixes #34", "Fixes #41"]  # Refs #40 does not complete #40


def test_title_without_summary():
    entries, _ = entries_for((12, "fix/34-x", "fix(a): b (#34)", "Fixes #34"))
    assert plan_pr(entries, summary="  ")["title"] == "release: v1.3.0"


def test_reverted_pr_does_not_close_its_issue():
    entries, _ = entries_for(
        (12, "fix/34-x", "fix(a): b (#34)", "Fixes #34"),
        (13, "feat/40-y", "feat(c): d (#40)", "Fixes #40"),
        (14, "revert-13-feat/40-y", 'Revert "feat(c): d (#40)"', "Reverts Techmirion/app#13\n\nRefs #40"),
    )
    body = plan_pr(entries)["body"]
    assert "Fixes #40" not in body
    assert "Fixes #34" in body
    assert "- #13 feat(c): d (#40) (reverted by #14)" in body
    assert "(reverts #13)" in body


def test_revert_of_a_revert_restores_the_issue():
    entries, _ = entries_for(
        (13, "feat/40-y", "feat(c): d (#40)", "Fixes #40"),
        (14, "revert-13", 'Revert "feat(c): d (#40)"', "Reverts #13"),
        (15, "revert-14", 'Revert "Revert ..."', "Reverts #14"),
    )
    assert "Fixes #40" in plan_pr(entries)["body"]


def test_sync_pr_merges_are_not_listed():
    merges = [merge(20, "main", "chore(release): sync main into dev"), merge(21, "fix/5-x", "fix(a): b (#5)")]
    prs = {20: pr(20, "main", "chore(release): sync main into dev", ""), 21: pr(21, "fix/5-x", "fix(a): b (#5)", "Fixes #5")}
    entries, _ = rl.collect_entries(merges, prs)
    assert [e.number for e in entries] == [21]


def test_direct_commit_on_dev_is_listed_with_a_warning():
    merges = [{"sha": "d" * 40, "subject": "oops pushed straight to dev", "body": "", "merge": False,
               "number": None, "head": None}]
    entries, warnings = rl.collect_entries(merges, {})
    assert warnings and "without a pull request" in warnings[0]
    assert "(not a pull request)" in plan_pr(entries)["body"]


@pytest.mark.parametrize("kw,needle", [
    ({"main_is_ancestor_of_dev": False}, "main is not an ancestor of dev"),
    ({"version": "v1.2.0"}, "not above the last release tag v1.2.0"),
    ({"version": "v1.1.9"}, "not above"),
    ({"version": "1.3"}, "not a version"),
    ({"version": "v1.3.0-rc1"}, "not a version"),
    ({"dev_sha": MAIN}, "nothing to release"),
    ({"open_release_prs": [{"number": 30}]}, "already open: #30"),
])
def test_release_pr_refusals(kw, needle):
    entries, _ = entries_for((12, "fix/34-x", "fix(a): b (#34)", "Fixes #34"))
    with pytest.raises(rl.Refused) as e:
        plan_pr(entries, **kw)
    assert any(needle in r for r in e.value.reasons), e.value.reasons


def test_nothing_but_a_sync_merge_is_nothing_to_release():
    with pytest.raises(rl.Refused):
        plan_pr([])


def test_first_release_without_tags():
    entries, _ = entries_for((1, "chore/start", "chore(repo): start", "- [x] No issue needed"))
    assert plan_pr(entries, tags=[], version="v0.1.0")["title"] == "release: v0.1.0 search"


def test_moving_major_tag_is_not_a_release_tag():
    entries, _ = entries_for((1, "chore/start", "chore(repo): start", ""))
    assert plan_pr(entries, tags=["v1"], version="v0.1.0")


# --- release (push to main) -----------------------------------------------------

def merged_pr(n, head, body="", title="release: v1.3.0 search", sha=M2):
    return {"number": n, "head": head, "base": "main", "merged": True, "merge_commit_sha": sha,
            "title": title, "body": body}


def plan_rel(prs, **kw):
    args = dict(sha=M2, prs=prs, tags=["v1.2.0"], tags_at_sha=[], release_exists=lambda t: False,
                sha_is_ancestor_of_dev=False, dev_is_ancestor_of_sha=True, open_sync_prs=[])
    args.update(kw)
    return rl.plan_release(**args)


def kinds(plan):
    return [a[0] for a in plan["actions"]]


def test_release_reads_the_version_and_fast_forwards_dev():
    plan = plan_rel([merged_pr(30, "dev", "Version: v1.3.0\n\nstuff\nFixes #34")])
    assert plan["kind"] == "release" and plan["version"] == "v1.3.0"
    assert kinds(plan) == ["tag", "release", "fast-forward-dev"]
    rel = plan["actions"][1]
    assert rel[2] == "v1.3.0 search"
    assert "Version:" not in rel[3] and "Fixes #34" in rel[3] and "Release PR: #30" in rel[3]


def test_release_opens_sync_pr_when_dev_moved():
    plan = plan_rel([merged_pr(30, "dev", "Version: v1.3.0")], dev_is_ancestor_of_sha=False)
    assert kinds(plan) == ["tag", "release", "sync-pr"]
    assert plan["actions"][2][1] == "chore(release): sync main into dev"


def sync(**kw):
    args = dict(sha=M2, sha_is_ancestor_of_dev=False, dev_is_ancestor_of_sha=True, open_sync_prs=[], body="b")
    args.update(kw)
    return rl.plan_sync(**args)


def test_plan_sync_fast_forwards_when_dev_is_behind():
    assert sync() == ([("fast-forward-dev", M2)], [])


def test_plan_sync_does_nothing_when_dev_contains_main():
    assert sync(sha_is_ancestor_of_dev=True) == ([], [])


def test_plan_sync_opens_one_sync_pr_when_dev_moved():
    assert sync(dev_is_ancestor_of_sha=False) == ([("sync-pr", "chore(release): sync main into dev", "b")], [])
    actions, warnings = sync(dev_is_ancestor_of_sha=False, open_sync_prs=[{"number": 31}])
    assert actions == [] and "already open: #31" in warnings[0]


def test_plan_sync_without_dev():
    actions, warnings = sync(dev_exists=False)
    assert actions == [] and "no `dev` branch" in warnings[0]


def test_sync_dev_body_passes_the_body_check():
    from tmflow import fmt
    assert fmt.check_body(rl.sync_dev_body(M2, {"number": 7})) == []
    assert "a push without a pull request" in rl.sync_dev_body(M2, None)


def test_existing_sync_pr_is_not_duplicated():
    plan = plan_rel([merged_pr(30, "dev", "Version: v1.3.0")], dev_is_ancestor_of_sha=False,
                    open_sync_prs=[{"number": 31}])
    assert kinds(plan) == ["tag", "release"]
    assert "already open: #31" in plan["warnings"][0]


def test_dev_already_contains_main():
    plan = plan_rel([merged_pr(30, "dev", "Version: v1.3.0")], sha_is_ancestor_of_dev=True)
    assert kinds(plan) == ["tag", "release"]


def test_release_without_version_line_refused():
    with pytest.raises(rl.Refused):
        plan_rel([merged_pr(30, "dev", "no version here")])


def test_release_version_not_above_last_tag_refused():
    with pytest.raises(rl.Refused):
        plan_rel([merged_pr(30, "dev", "Version: v1.2.0")])


def test_tag_on_another_commit_refused():
    with pytest.raises(rl.Refused):
        plan_rel([merged_pr(30, "dev", "Version: v1.3.0")], tags=["v1.2.0", "v1.3.0"])


def test_hotfix_gets_next_patch_and_sync_pr():
    plan = plan_rel([merged_pr(41, "hotfix/40-sse", "Fixes #40", title="fix(sse): keep-alive (#40)")],
                    tags=["v1.2.0", "v1.3.0", "v1"], dev_is_ancestor_of_sha=False)
    assert plan["kind"] == "hotfix" and plan["version"] == "v1.3.1"
    assert kinds(plan) == ["tag", "release", "sync-pr"]
    assert plan["actions"][1][2] == "v1.3.1 (hotfix)"
    assert "Fixes #40" in plan["actions"][1][3]


def test_hotfix_before_any_release():
    plan = plan_rel([merged_pr(3, "hotfix/2-x", "Fixes #2", title="fix(a): b (#2)")], tags=[])
    assert plan["version"] == "v0.0.1" and plan["warnings"]


def test_rerun_is_idempotent():
    plan = plan_rel([merged_pr(30, "dev", "Version: v1.3.0")], tags=["v1.2.0", "v1.3.0"],
                    tags_at_sha=["v1.3.0"], release_exists=lambda t: True, sha_is_ancestor_of_dev=True)
    assert plan["version"] == "v1.3.0" and kinds(plan) == []


def test_push_without_pr_is_only_flagged():
    plan = plan_rel([])
    assert plan["kind"] == "none" and kinds(plan) == [] and "bypassed the flow" in plan["warnings"][0]


def test_pr_from_another_branch_into_main_is_not_released():
    plan = plan_rel([merged_pr(9, "fix/3-x", "Fixes #3", title="fix(a): b (#3)")])
    assert plan["kind"] == "none" and kinds(plan) == []


def test_unmerged_or_other_commit_prs_ignored():
    p = merged_pr(30, "dev", "Version: v1.3.0", sha="e" * 40)
    assert plan_rel([p])["kind"] == "none"


def test_move_major_tag_for_the_workflows_repo():
    plan = plan_rel([merged_pr(30, "dev", "Version: v1.3.0")], move_major_tag=True)
    assert ("move-tag", "v1", M2) in plan["actions"]


# --- cleanup ----------------------------------------------------------------------

BASE = dict(merged=True, head="fix/34-x", head_repo="T/app", base_repo="T/app", default_branch="main",
            protected=(), branch_sha="f" * 40, pr_head_sha="f" * 40, other_open_prs=0)


def cleanup(**kw):
    return rl.plan_cleanup(**{**BASE, **kw})


def test_cleanup_deletes_a_merged_branch():
    assert cleanup() == (True, "`fix/34-x` was merged")


@pytest.mark.parametrize("head", ["dev", "main"])
def test_cleanup_never_deletes_dev_or_main(head):
    assert cleanup(head=head, protected=())[0] is False
    assert cleanup(head=head, protected=(), default_branch="trunk")[0] is False


@pytest.mark.parametrize("kw", [
    {"merged": False},
    {"head": "trunk", "default_branch": "trunk"},
    {"head": "release-notes", "protected": ("release-notes",)},
    {"head_repo": "someone/fork"},
    {"head_repo": None},
    {"branch_sha": None},
    {"branch_sha": "0" * 40},
    {"other_open_prs": 1},
])
def test_cleanup_keeps(kw):
    assert cleanup(**kw)[0] is False


def test_sync_and_release_pr_detection():
    assert rl.is_sync_pr({"head": "main", "base": "dev"})
    assert not rl.is_sync_pr({"head": "main", "base": "main"})
    assert rl.is_release_pr({"head": "dev", "base": "main"})
    assert not rl.is_release_pr({"head": "hotfix/1-x", "base": "main"})
