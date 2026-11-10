#!/bin/sh
set -eu
skill=$(realpath "$(dirname "$0")/../..")
tmp=$(mktemp -d)
trap 'chmod -R u+w "$tmp"; rm -rf "$tmp"' EXIT HUP INT TERM
mkdir "$tmp/bin" "$tmp/install" "$tmp/project"
"$skill/scripts/package.sh" "$tmp/skill.tar.gz"
if tar -tzf "$tmp/skill.tar.gz" | grep -q '^./validator/target/'; then
    echo 'archive contains target artifacts' >&2; exit 1
fi
tar -xzf "$tmp/skill.tar.gz" -C "$tmp/install"
chmod -R a-w "$tmp/install"
ln -s "$tmp/install" "$tmp/link"
for tool in realpath dirname mktemp rm; do ln -s "$(command -v "$tool")" "$tmp/bin/$tool"; done
# A controlled Cargo stub checks launcher wiring without fetching dependencies.
cat > "$tmp/bin/cargo" <<'STUB'
#!/bin/sh
case "$CARGO_TARGET_DIR" in "$INSTALL_ROOT"/*) exit 9;; esac
[ -d "$CARGO_TARGET_DIR" ] && [ -w "$CARGO_TARGET_DIR" ] || exit 8
case "$*" in *"$INSTALL_ROOT/validator/Cargo.toml"*) ;; *) exit 7;; esac
mkdir -p "$CARGO_TARGET_DIR/debug"
cat > "$CARGO_TARGET_DIR/debug/clean-python-validator" <<'BIN'
#!/bin/sh
printf 'scanned: %s\n' "$*"
BIN
chmod +x "$CARGO_TARGET_DIR/debug/clean-python-validator"
STUB
chmod +x "$tmp/bin/cargo"
ln -s "$(command -v mkdir)" "$tmp/bin/mkdir"
ln -s "$(command -v cat)" "$tmp/bin/cat"
ln -s "$(command -v chmod)" "$tmp/bin/chmod"
for entry in "$tmp/install/scripts/validate.sh" "$tmp/link/scripts/validate.sh"; do
    (cd / && INSTALL_ROOT="$tmp/install" PATH="$tmp/bin" "$entry" "$tmp/project") >"$tmp/out"
    grep -Fq "scanned: $tmp/project" "$tmp/out"
done
# Stub Cargo so the test checks failure handling without the network.
cat > "$tmp/bin/cargo" <<'STUB'
#!/bin/sh
exit 1
STUB
chmod +x "$tmp/bin/cargo"
set +e
(cd / && PATH="$tmp/bin" "$tmp/link/scripts/validate.sh" "$tmp/project") >"$tmp/out" 2>&1
status=$?
set -e
[ "$status" -eq 2 ] && grep -q 'validator not run: build or dependencies unavailable' "$tmp/out"
rm "$tmp/bin/cargo"
set +e
(cd / && PATH="$tmp/bin" "$tmp/link/scripts/validate.sh" "$tmp/project") >"$tmp/out" 2>&1
status=$?
set -e
[ "$status" -eq 2 ] && grep -q 'validator not run: Cargo is unavailable' "$tmp/out"
echo 'portable packaging and unavailable dependency checks passed'
