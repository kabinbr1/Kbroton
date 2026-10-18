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
