#!/bin/sh
# Install the actionlint binary from its GitHub release, checksum-verified.
#
#   scripts/install-actionlint.sh [VERSION] [BINDIR]
#
# Used by this repo's own lint (scripts/lint.sh), locally and in CI.
set -eu

VERSION="${1:-1.7.12}"
BINDIR="${2:-$HOME/.local/bin}"
VERSION="${VERSION#v}"

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=darwin ;;
  *) echo "install-actionlint: unsupported OS $(uname -s)" >&2; exit 2 ;;
esac
case "$(uname -m)" in
  x86_64 | amd64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
  *) echo "install-actionlint: unsupported CPU $(uname -m)" >&2; exit 2 ;;
esac

if [ -x "$BINDIR/actionlint" ] && "$BINDIR/actionlint" -version 2>/dev/null | head -1 | grep -q "^$VERSION\$"; then
  echo "actionlint $VERSION already installed in $BINDIR"
  exit 0
fi

base="https://github.com/rhysd/actionlint/releases/download/v$VERSION"
asset="actionlint_${VERSION}_${os}_${arch}.tar.gz"
sums="actionlint_${VERSION}_checksums.txt"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT INT TERM

curl -fsSL --retry 3 -o "$tmp/$asset" "$base/$asset"
curl -fsSL --retry 3 -o "$tmp/$sums" "$base/$sums"

expected="$(awk -v f="$asset" '$2 == f { print $1 }' "$tmp/$sums")"
if [ -z "$expected" ]; then
  echo "install-actionlint: $asset is not listed in $sums" >&2
  exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$tmp/$asset" | awk '{ print $1 }')"
else
  actual="$(shasum -a 256 "$tmp/$asset" | awk '{ print $1 }')"
fi
if [ "$expected" != "$actual" ]; then
  echo "install-actionlint: checksum mismatch for $asset" >&2
  exit 1
fi

mkdir -p "$tmp/x" "$BINDIR"
tar -xzf "$tmp/$asset" -C "$tmp/x" actionlint
install -m 0755 "$tmp/x/actionlint" "$BINDIR/actionlint"
echo "actionlint $("$BINDIR/actionlint" -version | head -1) installed in $BINDIR"
