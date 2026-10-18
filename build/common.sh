#!/usr/bin/env bash

BUILD_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$BUILD_DIR/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/dist}"
VERSION="${VERSION:-1.0.0}"

if [[ ! "$VERSION" =~ ^[0-9]+(\.[0-9]+)*$ ]]; then
    printf 'VERSION must contain only numbers separated by dots: %s\n' "$VERSION" >&2
    return 1
fi

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$1" >&2
        return 1
    fi
}

prepare_tree() {
    local destination="$1"
    local app_dir="$destination/usr/lib/kbroton"

    mkdir -p "$app_dir"
    install -m 0644 "$ROOT_DIR/main.py" "$ROOT_DIR/localization.py" "$app_dir/"
    install -Dm 0644 "$ROOT_DIR/assets/icons/kbroton-neon.png" \
        "$app_dir/assets/icons/kbroton-neon.png"
    install -Dm 0644 "$ROOT_DIR/assets/icons/kbroton-neon.png" \
        "$destination/usr/share/icons/hicolor/512x512/apps/kbroton.png"
    cp -R "$ROOT_DIR/translations" "$app_dir/translations"
    install -Dm 0755 "$BUILD_DIR/kbroton" "$destination/usr/bin/kbroton"
    install -Dm 0644 "$BUILD_DIR/kbroton.desktop" \
        "$destination/usr/share/applications/kbroton.desktop"
    install -Dm 0644 "$ROOT_DIR/README.md" \
        "$destination/usr/share/doc/kbroton/README.md"
}

create_source_archive() {
    local tree="$1"
    local archive="$2"

    tar -C "$tree" -czf "$archive" .
}
