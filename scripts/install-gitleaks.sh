#!/bin/sh
# Install the gitleaks binary from its GitHub release, checksum-verified.
#
#   scripts/install-gitleaks.sh [VERSION] [BINDIR]
#
# VERSION defaults to the one pinned below, BINDIR to ~/.local/bin.
# Used by the `checks` workflow in CI and by people on their own machines.
# This is the free gitleaks binary (MIT), not the paid gitleaks-action.
set -eu

VERSION="${1:-8.30.1}"
BINDIR="${2:-$HOME/.local/bin}"
VERSION="${VERSION#v}"

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=darwin ;;
  *) echo "install-gitleaks: unsupported OS $(uname -s)" >&2; exit 2 ;;
esac
case "$(uname -m)" in
  x86_64 | amd64) arch=x64 ;;
  aarch64 | arm64) arch=arm64 ;;
  *) echo "install-gitleaks: unsupported CPU $(uname -m)" >&2; exit 2 ;;
esac

if command -v "$BINDIR/gitleaks" >/dev/null 2>&1 &&
  "$BINDIR/gitleaks" version 2>/dev/null | grep -q "^v\?$VERSION\$"; then
  echo "gitleaks $VERSION already installed in $BINDIR"
  exit 0
fi

base="https://github.com/gitleaks/gitleaks/releases/download/v$VERSION"
asset="gitleaks_${VERSION}_${os}_${arch}.tar.gz"
sums="gitleaks_${VERSION}_checksums.txt"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT INT TERM

curl -fsSL --retry 3 -o "$tmp/$asset" "$base/$asset"
curl -fsSL --retry 3 -o "$tmp/$sums" "$base/$sums"

expected="$(awk -v f="$asset" '$2 == f { print $1 }' "$tmp/$sums")"
if [ -z "$expected" ]; then
  echo "install-gitleaks: $asset is not listed in $sums" >&2
  exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$tmp/$asset" | awk '{ print $1 }')"
else
  actual="$(shasum -a 256 "$tmp/$asset" | awk '{ print $1 }')"
fi
if [ "$expected" != "$actual" ]; then
  echo "install-gitleaks: checksum mismatch for $asset" >&2
  exit 1
fi

mkdir -p "$tmp/x" "$BINDIR"
tar -xzf "$tmp/$asset" -C "$tmp/x" gitleaks
install -m 0755 "$tmp/x/gitleaks" "$BINDIR/gitleaks"
echo "gitleaks $("$BINDIR/gitleaks" version) installed in $BINDIR"
