#!/usr/bin/env bash
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

require_command makepkg
require_command tar
mkdir -p "$OUTPUT_DIR"

work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
tree="$work_dir/tree"
mkdir -p "$tree"
prepare_tree "$tree"
create_source_archive "$tree" "$work_dir/kbroton-$VERSION.tar.gz"

cat > "$work_dir/PKGBUILD" <<EOF
pkgname=kbroton
pkgver=$VERSION
pkgrel=1
pkgdesc='Qt launcher for Windows games and apps with Proton'
arch=('any')
url='https://github.com/kabinbr1/Kbroton'
license=('GPL-3.0-or-later')
depends=('python>=3.10' 'pyside6')
source=("kbroton-\$pkgver.tar.gz")
sha256sums=('SKIP')

package() {
    install -dm755 "\$pkgdir"
    cp -a "\$srcdir/usr" "\$pkgdir/"
}
EOF

(
    cd "$work_dir"
    PKGDEST="$OUTPUT_DIR" makepkg --noconfirm --force --clean
)
printf 'Built Arch package in %s\n' "$OUTPUT_DIR"
