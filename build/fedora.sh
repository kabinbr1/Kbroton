#!/usr/bin/env bash
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

require_command rpmbuild
require_command tar
mkdir -p "$OUTPUT_DIR"

work_dir="$(mktemp -d)"
trap 'rm -rf -- "$work_dir"' EXIT
mkdir -p "$work_dir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
tree="$work_dir/tree"
mkdir -p "$tree"
prepare_tree "$tree"
create_source_archive "$tree" "$work_dir/SOURCES/kbroton-$VERSION.tar.gz"

cat > "$work_dir/SPECS/kbroton.spec" <<EOF
Name:           kbroton
Version:        $VERSION
Release:        1%{?dist}
Summary:        Qt launcher for Windows games and apps with Proton
License:        NOASSERTION
BuildArch:      noarch
Requires:       python3 >= 3.10
Requires:       python3-pyside6
Source0:        %{name}-%{version}.tar.gz

%description
Kbroton manages and launches Windows games and apps using Proton.

%prep

%build

%install
mkdir -p %{buildroot}
tar -xzf %{SOURCE0} -C %{buildroot}

%files
/usr/bin/kbroton
/usr/lib/kbroton
/usr/share/applications/kbroton.desktop
/usr/share/doc/kbroton/README.md
EOF

rpmbuild --define "_topdir $work_dir" -bb "$work_dir/SPECS/kbroton.spec"
cp "$work_dir"/RPMS/noarch/kbroton-"$VERSION"-1*.noarch.rpm "$OUTPUT_DIR/"
printf 'Built Fedora RPM in %s\n' "$OUTPUT_DIR"
