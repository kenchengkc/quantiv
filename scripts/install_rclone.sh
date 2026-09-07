#!/usr/bin/env bash
# Install the exact rclone release used by Quantiv CI and verify the vendor's
# signed SHA256SUMS before trusting the archive.

set -euo pipefail

RCLONE_VERSION="${RCLONE_VERSION:-1.75.1}"
RCLONE_SIGNING_FINGERPRINT="FBF737ECE9F8AB18604BD2AC93935E02FF3B54FA"
INSTALL_DIR="${RCLONE_INSTALL_DIR:-$HOME/.local/bin}"
BASE_URL="https://downloads.rclone.org/v${RCLONE_VERSION}"
ARCHIVE="rclone-v${RCLONE_VERSION}-linux-amd64.zip"

for command in curl gpg sha256sum unzip; do
  command -v "$command" >/dev/null || {
    echo "Required command missing: $command" >&2
    exit 1
  }
done

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

curl --fail --location --proto '=https' --tlsv1.2 \
  "$BASE_URL/$ARCHIVE" -o "$workdir/$ARCHIVE"
curl --fail --location --proto '=https' --tlsv1.2 \
  "$BASE_URL/SHA256SUMS" -o "$workdir/SHA256SUMS"
curl --fail --location --proto '=https' --tlsv1.2 \
  "https://www.craig-wood.com/nick/pub/pgp-key.txt" -o "$workdir/rclone-signing-key.asc"

GNUPGHOME="$workdir/gnupg"
export GNUPGHOME
mkdir -m 700 "$GNUPGHOME"
gpg --batch --import "$workdir/rclone-signing-key.asc" >/dev/null 2>&1
actual_fingerprint="$(gpg --batch --with-colons --fingerprint | awk -F: '$1 == "fpr" {print $10; exit}')"
if [ "$actual_fingerprint" != "$RCLONE_SIGNING_FINGERPRINT" ]; then
  echo "Unexpected rclone signing-key fingerprint: $actual_fingerprint" >&2
  exit 1
fi

gpg --batch --verify "$workdir/SHA256SUMS" >/dev/null 2>&1
(
  cd "$workdir"
  grep "  $ARCHIVE$" SHA256SUMS | sha256sum --check --strict -
)

unzip -q "$workdir/$ARCHIVE" -d "$workdir/unpacked"
mkdir -p "$INSTALL_DIR"
install -m 0755 \
  "$workdir/unpacked/rclone-v${RCLONE_VERSION}-linux-amd64/rclone" \
  "$INSTALL_DIR/rclone"

echo "$INSTALL_DIR" >> "${GITHUB_PATH:-/dev/null}"
"$INSTALL_DIR/rclone" version
