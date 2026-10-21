import pytest

from tmflow import fmt


# --- branches (git.md 4, 14) ------------------------------------------------

@pytest.mark.parametrize("name,issue", [
    ("fix/34-login-redirect", 34),
    ("feat/7-greek-search", 7),
    ("hotfix/120-broken-sse", 120),
    ("fix/34-2fa-login", 34),
    ("docs/readme", None),
    ("chore/ci-pins", None),
    ("refactor/12-split.parts", 12),
    ("test/9-build_check", 9),
])
def test_valid_branches(name, issue):
    assert fmt.parse_branch(name) is not None
    assert fmt.issue_from_branch(name) == issue


@pytest.mark.parametrize("name", [
    "fix/34",                 # topic missing
    "fix/0-x",                # issue 0 does not exist
    "Fix/34-x",               # type in uppercase
    "fix/34-Login",           # uppercase topic
    "feature/34-x",           # not a type
    "fotis/34-x",             # person name instead of a type
    "fix-34-x",               # no slash
    "fix/34-x-",              # trailing dash
    "fix/34--x",              # double dash
    "fix/",                   # empty
    "wip/thing",
    "preview/demo",           # reserved for previews without a PR, not a PR branch
])
def test_invalid_branches(name):
    assert fmt.parse_branch(name) is None
    assert fmt.issue_from_branch(name) is None
    assert fmt.check_branch(name)


@pytest.mark.parametrize("name", ["dev", "main", "dependabot/github_actions/actions/checkout-7.0.1"])
def test_exempt_branches(name):
    assert fmt.check_branch(name) == []


def test_issue_expected_for_more_than_a_chore():
    assert fmt.check_branch("feat/search")          # a feature starts from an issue
    assert fmt.check_branch("hotfix/broken")
    assert fmt.check_branch("chore/ci") == []
    assert fmt.check_branch("docs/readme") == []


# --- subjects (git.md 5, 14) ------------------------------------------------

@pytest.mark.parametrize("s", [
    "fix(auth): redirect after login (#34)",
    "fix(auth): redirect after login",
    "feat(ui): two refs (#34, #40)",
    "fix(api): from another repo (Techmirion/other#7)",
    "fix(api): mixed (#34, Techmirion/other#7)",
    "chore(deps): bump actions/checkout from 6 to 7",
    "docs(readme): explain the loop",
    "refactor(build/py): split parts (#3)",
    "test(release.lib): ancestor check (#5)",
    "hotfix(sse): keep-alive pings (#120)",
    "fix(auth): handles (see docs)",
])
def test_valid_subjects(s):
    assert fmt.check_subject(s) == [], s


@pytest.mark.parametrize("s", [
    "fix: no place",
    "Fix(auth): capital type",
    "fix(Auth): capital place",
    "fix(auth):no space",
    "fix(auth):  two spaces",
    "fix(auth): ",
    "feature(ui): not a type",
    "style(css): not a type",
    "fix(auth)!: breaking marker is not part of our format",
    "brand: app name Novit (S2130)",
    "fix(auth): bad ref (#)",
    "fix(auth): bad ref (# 34)",
    "fix(auth): bad ref (#34,#40)",
    "release: v1.2.0 only for PR titles",
    "random text",
])
def test_invalid_subjects(s):
    assert fmt.check_subject(s), s


@pytest.mark.parametrize("s", [
    "Merge pull request #12 from Techmirion/fix/34-login-redirect",
    "Merge branch 'dev' into fix/34-login-redirect",
    "Merge remote-tracking branch 'origin/dev'",
    'Revert "fix(auth): redirect after login (#34)"',
    'Reapply "fix(auth): redirect after login (#34)"',
])
def test_exempt_subjects(s):
    assert fmt.subject_exempt(s)
    assert fmt.check_subject(s) == []


def test_autosquash_allowed_locally_but_warned_in_ci():
    s = "fixup! fix(auth): redirect after login (#34)"
    assert fmt.check_subject(s, allow_autosquash=True) == []
    assert fmt.check_subject(s)


def test_parse_subject_parts():
    p = fmt.parse_subject("fix(auth): redirect after login (#34, #40)")
    assert p["type"] == "fix" and p["place"] == "auth" and p["msg"] == "redirect after login"
    assert fmt.split_refs(p["refs"]) == ["#34", "#40"]


# --- PR titles ----------------------------------------------------------------

@pytest.mark.parametrize("t", [
    "release: v1.4.0 search and login",
    "release: v0.1.0",
    "fix(auth): redirect after login (#34)",
    "chore(release): sync main into dev",
])
def test_valid_titles(t):
    assert fmt.check_title(t) == []


@pytest.mark.parametrize("t", ["release: 1.4.0", "release v1.4.0", "release: v1.4", "Update README.md", "fix login"])
def test_invalid_titles(t):
    assert fmt.check_title(t)


# --- adding the reference (prepare-commit-msg) --------------------------------

@pytest.mark.parametrize("before,after", [
    ("fix(auth): redirect", "fix(auth): redirect (#34)"),
    ("fix(auth): redirect (#34)", "fix(auth): redirect (#34)"),
    ("fix(auth): redirect (#40)", "fix(auth): redirect (#34, #40)"),
    ("fix(auth): redirect (#40, #34)", "fix(auth): redirect (#40, #34)"),
    ("fix(auth): redirect (Techmirion/other#7)", "fix(auth): redirect (#34, Techmirion/other#7)"),
    ("fix(auth): handle #34 inline", "fix(auth): handle #34 inline"),
    ("fix(auth): not #345", "fix(auth): not #345 (#34)"),
    ("fix(auth): other repo Techmirion/x#34", "fix(auth): other repo Techmirion/x#34 (#34)"),
    ("Merge branch 'dev' into fix/34-x", "Merge branch 'dev' into fix/34-x"),
    ('Revert "fix(auth): x (#34)"', 'Revert "fix(auth): x (#34)"'),
    ("fixup! fix(auth): x", "fixup! fix(auth): x"),
    ("", ""),
    ("not even the format", "not even the format (#34)"),
])
def test_add_issue_ref(before, after):
    assert fmt.add_issue_ref(before, 34) == after
    assert fmt.add_issue_ref(fmt.add_issue_ref(before, 34), 34) == after  # idempotent


def test_add_ref_to_message_keeps_body_and_comments():
    text = "fix(auth): redirect\n\nWhy it was broken.\n# Please enter the commit message\n"
    assert fmt.add_ref_to_message(text, 34) == "fix(auth): redirect (#34)\n\nWhy it was broken.\n# Please enter the commit message\n"


def test_add_ref_to_message_editor_template_is_left_alone():
    text = "\n# Please enter the commit message for your changes.\n# On branch fix/34-x\n"
    assert fmt.add_ref_to_message(text, 34) == text
    assert fmt.subject_of(text) == ""


def test_message_lines_stop_at_scissors():
    text = "fix(a): b\n\nbody\n" + fmt.SCISSORS + "\ndiff --git a b\nFixes #3\n"
    assert fmt.message_lines(text) == ["fix(a): b", "", "body"]


def test_custom_comment_char():
    text = "; comment\nfix(a): b\n"
    assert fmt.subject_of(text, ";") == "fix(a): b"


# --- PR bodies (git.md 6) -------------------------------------------------------

TEMPLATE = """## Issue

<!-- One line: `Fixes #N` if this PR completes the issue, `Refs #N` if it is part of the work. -->
Fixes #

- [ ] No issue needed (a typo, a chore or a dependency bump)
"""


def test_untouched_template_warns():
    assert fmt.check_body(TEMPLATE)


def test_fixes_line_passes():
    assert fmt.check_body(TEMPLATE.replace("Fixes #\n", "Fixes #34\n")) == []
    assert fmt.check_body("Refs #34") == []
    assert fmt.check_body("- fixes #34") == []
    assert fmt.check_body("Fixes Techmirion/other#7") == []


def test_no_issue_box_ticked_passes():
    assert fmt.check_body(TEMPLATE.replace("- [ ] No issue needed", "- [x] No issue needed")) == []
    assert fmt.check_body(TEMPLATE.replace("- [ ] No issue needed", "- [X] No issue needed")) == []


def test_refs_inside_html_comment_do_not_count():
    assert fmt.check_body("<!-- Fixes #34 -->")


def test_fixes_mid_sentence_does_not_count():
    assert fmt.check_body("This PR fixes #34 I think")


def test_branch_issue_missing_from_body_warns():
    assert fmt.check_body("Fixes #40", branch_issue=34)
    assert fmt.check_body("Refs #34\nFixes #40", branch_issue=34) == []


def test_closing_refs():
    body = "Fixes #1\ncloses #2\nResolved: #3\nfixed #1\nRefs #9\n<!-- Fixes #8 -->\nprefix #7\nFix Techmirion/o#4"
    assert fmt.closing_refs(body) == ["#1", "#2", "#3", "Techmirion/o#4"]


# --- versions (git.md 12) ---------------------------------------------------------

def test_versions():
    assert fmt.parse_version("v1.2.3") == (1, 2, 3)
    assert fmt.parse_version("1.2.3") == (1, 2, 3)
    for bad in ("v1.2", "v01.2.3", "v1.2.3-rc1", "", "vx.y.z"):
        assert fmt.parse_version(bad) is None
    assert fmt.release_tags(["v1", "v0.9.0", "v0.10.0", "latest", "v1.0.0-rc1"]) == [(0, 9, 0), (0, 10, 0)]
    assert fmt.next_patch(["v0.9.0", "v0.10.0", "v1"]) == (0, 10, 1)
    assert fmt.next_patch([]) == (0, 0, 1)


def test_version_from_body():
    assert fmt.version_from_body("Version: v1.4.0\n\nstuff") == (1, 4, 0)
    assert fmt.version_from_body("intro\nVersion:   v2.0.0  \n") == (2, 0, 0)
    assert fmt.version_from_body("Version: 1.4.0") is None
    assert fmt.version_from_body("<!-- Version: v9.9.9 -->") is None
    assert fmt.version_from_body("") is None


# --- Co-Authored-By (git.md 5) ----------------------------------------------

@pytest.mark.parametrize("text", [
    "fix(a): b\n\nCo-Authored-By: Someone <s@example.invalid>",
    "fix(a): b\n\nco-authored-by: Someone <s@example.invalid>\n",
    "fix(a): b\n\n  CO-AUTHORED-BY : Someone",
    "fix(a): b\n\nSigned-off-by: Me <m@example.invalid>\nCo-authored-by:Someone",
    "Merge branch 'x' into dev\n\nCo-Authored-By: Someone",
])
def test_coauthor_trailer_found(text):
    assert len(fmt.coauthor_lines(text)) == 1
    problems = fmt.check_no_coauthor(text)
    assert len(problems) == 1 and "Co-Authored-By" in problems[0]


@pytest.mark.parametrize("text", [
    "fix(a): b",
    "fix(a): b\n\nWritten together, co-authored by the team.",
    "fix(a): b\n\nSigned-off-by: Me <m@example.invalid>",
    "docs(git): say that Co-Authored-By: lines are refused",  # not at the start of a line
    "",
])
def test_no_coauthor_trailer(text):
    assert fmt.coauthor_lines(text) == []
    assert fmt.check_no_coauthor(text) == []


# --- no agent named (git.md 5) ----------------------------------------------

@pytest.mark.parametrize("text", [
    "fix(a): b\n\nClaude-Session: https://claude.ai/code/session_x",
    "fix(a): b\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    "## Checklist\n\n- [x] An AI assistant did part of this work: Claude (Cowork).",
    "body\n\n  generated by Omicron",
    "body\n\nMade with Copilot",
    "body\n\nhttps://claude.ai/code/session_01ABC",
])
def test_agent_line_found(text):
    assert len(fmt.agent_lines(text)) == 1
    problems = fmt.check_no_agent(text, what="the PR body")
    assert len(problems) == 1 and problems[0].startswith("the PR body names the agent")


@pytest.mark.parametrize("text", [
    "docs(agents): CLAUDE.md imports AGENTS.md",
    "fix(a): b\n\nthe file is generated with pip-compile",
    "- [ ] An AI assistant did part of this work: <!-- say which -->",
    "<!-- 🤖 Generated with [Claude Code](https://claude.com/claude-code) -->",
    "feat(doctor): the agent runs it before every push",
])
def test_agent_line_not_found(text):
    assert fmt.agent_lines(text) == []
    assert fmt.check_no_agent(text) == []
