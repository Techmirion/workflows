#!/bin/sh
# Install the Techmirion git hooks into a clone as plain git hooks.
# No network and no pre-commit needed (git.md 5 and 14).
#
#   sh scripts/install-hooks.sh [CLONE]              install (default: the current repo)
#   sh scripts/install-hooks.sh --uninstall [CLONE]  remove them (before switching to pre-commit)
#
# Installs pre-commit (gitleaks), prepare-commit-msg (adds (#N) from the
# branch name) and commit-msg (checks the format). A hook that is not ours is
# kept as <hook>.pre-techmirion and is no longer run.
set -eu

here="$(cd "$(dirname "$0")/.." && pwd)"
mode=install
if [ "${1:-}" = "--uninstall" ]; then
  mode=uninstall
  shift
fi
clone="${1:-.}"

if ! git -C "$clone" rev-parse --git-dir >/dev/null 2>&1; then
  echo "install-hooks: $clone is not a git repository" >&2
  exit 2
fi
hooks="$(cd "$clone" && git rev-parse --git-path hooks)"
case "$hooks" in
  /*) ;;
  *) hooks="$(cd "$clone" && pwd)/$hooks" ;;
esac
if [ -n "$(git -C "$clone" config core.hooksPath || true)" ]; then
  echo "install-hooks: note: core.hooksPath is set, installing into $hooks"
fi

if [ "$mode" = uninstall ]; then
  for h in pre-commit prepare-commit-msg commit-msg; do
    if [ -f "$hooks/$h" ] && grep -q "techmirion-hook" "$hooks/$h"; then
      rm -f "$hooks/$h"
      echo "removed $hooks/$h"
    fi
  done
  rm -rf "$hooks/tmflow"
  exit 0
fi

mkdir -p "$hooks/tmflow"
cp "$here/scripts/tmflow/__init__.py" "$here/scripts/tmflow/fmt.py" "$hooks/tmflow/"
for h in pre-commit prepare-commit-msg commit-msg; do
  if [ -e "$hooks/$h" ] && ! grep -q "techmirion-hook" "$hooks/$h"; then
    mv "$hooks/$h" "$hooks/$h.pre-techmirion"
    echo "install-hooks: kept your old $h as $h.pre-techmirion (it no longer runs)"
  fi
  cp "$here/hooks/$h" "$hooks/$h"
  chmod 0755 "$hooks/$h"
done
ver="$(git -C "$here" describe --tags --always --dirty 2>/dev/null || echo unknown)"
echo "$ver" > "$hooks/tmflow/VERSION"
echo "installed the Techmirion hooks ($ver) in $hooks"

if ! command -v gitleaks >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/gitleaks" ]; then
  echo "install-hooks: WARNING: gitleaks is not installed, so every commit will be refused." >&2
  echo "  install it with: sh $here/scripts/install-gitleaks.sh" >&2
fi
