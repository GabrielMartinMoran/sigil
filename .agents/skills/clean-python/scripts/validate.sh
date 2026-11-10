#!/bin/sh
# Resolve the installed skill, including a symlink to this entry point.
set -u
script=$(realpath "$0") || { echo 'validator not run: cannot resolve script path' >&2; exit 2; }
skill=$(dirname "$(dirname "$script")")
if ! command -v cargo >/dev/null 2>&1; then
    echo 'validator not run: Cargo is unavailable; continue other checks' >&2
    exit 2
fi
target=$(mktemp -d "${TMPDIR:-/tmp}/clean-python-validator.XXXXXXXX") || {
    echo 'validator not run: cannot create build directory' >&2; exit 2;
}
trap 'rm -rf "$target"' EXIT HUP INT TERM
if ! CARGO_TARGET_DIR="$target" cargo build --quiet --locked --manifest-path "$skill/validator/Cargo.toml"; then
    echo 'validator not run: build or dependencies unavailable; continue other checks' >&2
    exit 2
fi
"$target/debug/clean-python-validator" "$@"
