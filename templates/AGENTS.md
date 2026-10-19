# REPO

<!--
Notes for whoever works on this repo. People and AI assistants read this same file:
pi and others read AGENTS.md, and CLAUDE.md imports it for Claude. Keep it short and true.
Team rules live in the handbook (Techmirion/handbook): link to them, do not copy them here.
Replace each comment with the real thing, or delete the section.
-->

<!-- One or two sentences: what this is and who uses it. -->

Owner and deputy: [`.github/CODEOWNERS`](.github/CODEOWNERS).

## Read first

- [`README.md`](README.md): what it is and how to run it.
- `PLAN.md`, if there is one: the plan, and the decisions (D1, D2, …) with their reasons.
- The handbook: `git.md` (the flow), `testing.md`, `ai-guardrails.md`, and `web.md` for a
  web interface.
- Agents: the team skill `techmirion-standards` (on the boxes:
  `/shared/skills/skills/techmirion-standards/SKILL.md`). Run its doctor before every push
  and fix what it reports (`ai-guardrails.md` section 8). No `Co-Authored-By:` or other
  assistant lines in commits.

## Commands

```sh
# install
# run it
# the check, the same commands as .github/workflows/checks.yml
```

## Where things are

<!-- The folders that matter, what belongs in each, and where new code goes. -->

## Rules for this repo

<!--
Only what is particular to this repo, one line each: a contract other code depends on, data
or wording that must not change, a service that must not be loop-tested. A mistake an
assistant made twice here earns one line.
-->

## Things that bite

<!-- Gotchas found the hard way, one line each, with the reason. -->
