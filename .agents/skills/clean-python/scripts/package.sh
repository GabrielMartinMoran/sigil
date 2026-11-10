#!/bin/sh
# Create a portable archive without local Cargo build artifacts.
set -eu
if [ "$#" -ne 1 ]; then
    echo 'usage: package.sh OUTPUT.tar.gz' >&2
    exit 2
fi
script=$(realpath "$0")
skill=$(dirname "$(dirname "$script")")
output=$(realpath -m "$1")
case "$output" in "$skill"/*) echo 'output must be outside the skill directory' >&2; exit 2;; esac
tar --exclude='./validator/target' -czf "$output" -C "$skill" .
