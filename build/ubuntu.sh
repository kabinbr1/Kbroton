#!/usr/bin/env bash
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

require_command dpkg-deb
require_command install
mkdir -p "$OUTPUT_DIR"

work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
package_dir="$work_dir/kbroton"
prepare_tree "$package_dir"

mkdir -p "$package_dir/DEBIAN"
{
    cat <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Kbroton
Source: https://github.com/kabinbr1/Kbroton

Files: *
Copyright: 2026 Kbroton contributors
License: GPL-3.0-or-later
EOF
    sed 's/^/ /' "$ROOT_DIR/LICENSE"
} > "$package_dir/usr/share/doc/kbroton/copyright"

cat > "$package_dir/DEBIAN/control" <<EOF
Package: kbroton
Version: $VERSION
Section: games
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-pyside6.qtwidgets
Maintainer: Kbroton contributors
Description: Qt launcher for Windows games and apps with Proton
 Kbroton manages and launches Windows games and apps using Proton.
EOF

dpkg-deb --root-owner-group --build "$package_dir" \
    "$OUTPUT_DIR/kbroton_${VERSION}_all.deb"
printf 'Built %s\n' "$OUTPUT_DIR/kbroton_${VERSION}_all.deb"
