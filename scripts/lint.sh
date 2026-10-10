#!/bin/sh
# Lint this repo: workflows (actionlint), YAML (yamllint), shell (shellcheck), Python (compile).
#   sh scripts/lint.sh
set -eu
cd "$(dirname "$0")/.."

bindir="${RUNNER_TEMP:-$HOME/.local}/bin"
command -v actionlint >/dev/null 2>&1 || sh scripts/install-actionlint.sh 1.7.12 "$bindir" >/dev/null
PATH="$bindir:$PATH"

echo "== actionlint"
actionlint .github/workflows/*.yml templates/.github/workflows/*.yml

echo "== yamllint"
yamllint -s .

echo "== shellcheck"
shellcheck -s sh scripts/*.sh hooks/pre-commit

echo "== python"
python3 -m compileall -q scripts >/dev/null
python3 -c 'import ast, sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]' hooks/prepare-commit-msg hooks/commit-msg
echo "lint: all passed"
