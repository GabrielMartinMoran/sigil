#!/usr/bin/env bash
# End-to-end check of the pre-commit gate in a throwaway clone.
#
# The unit tests exercise the decisions with the toolchain stubbed; this script
# exercises the wiring — real git, real ruff, real npm, real rebuild — because the
# hook is the one thing that can silently stop working: it lives outside the test
# runner, and nobody notices until `web/dist` is wrong in a commit.
#
#   scripts/check_hook.sh            # in a temporary directory, cleaned up after
#   KEEP=1 scripts/check_hook.sh     # keep the tree for inspection
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:-$(mktemp -d -t sigil-hook-XXXXXX)}"
if [ -n "${1:-}" ]; then KEEP=1; fi
failures=0

step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; failures=$((failures + 1)); }

cleanup() {
  if [ "${KEEP:-}" = "1" ]; then
    printf '\nkept: %s\n' "$target"
  else
    rm -rf "$target"
  fi
}

step "preparing a throwaway repository at $target"
rm -rf "$target" && mkdir -p "$target"
(cd "$root" && rsync -a \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' \
  --exclude '.ruff_cache' --exclude '.codegraph' --exclude '*.egg-info' \
  ./ "$target"/)
# The toolchains are expensive: borrow them instead of reinstalling.
ln -s "$root/web/node_modules" "$target/web/node_modules"
ln -s "$root/.venv" "$target/.venv"
cd "$target" || exit 1
git init -q && git config user.email hook@test && git config user.name "hook test"
python_path="$root/.venv/bin/python"
"$python_path" scripts/install_hooks.py >/dev/null || bad "install_hooks failed"
[ "$(git config core.hooksPath)" = ".githooks" ] && ok "core.hooksPath points at .githooks" || bad "hooksPath wrong"

step "1. a badly formatted Python file is fixed and committed fixed"
printf 'def  ugly( a,b ):\n    return   a+b\n' > src/server/_hook_probe.py
git add -A >/dev/null 2>&1
git commit -q -m "probe: python" >/dev/null 2>&1
committed="$(git show HEAD:src/server/_hook_probe.py)"
case "$committed" in
  *'def ugly(a, b):'*) ok "the commit carries the formatted version" ;;
  *) bad "the commit carries the original formatting: $(printf '%s' "$committed" | head -1)" ;;
esac
[ -z "$(git status --porcelain)" ] && ok "the fix was re-staged, the tree is clean" || bad "leftover changes: $(git status --porcelain | tr '\n' ' ')"

step "2. a change in web/src rebuilds web/dist inside the same commit"
rm src/server/_hook_probe.py
git rm -q --cached src/server/_hook_probe.py
before="$(git show HEAD:web/dist/build.json | "$python_path" -c 'import json,sys; print(json.load(sys.stdin)["hash"])')"
printf '\nexport const probe = 1;\n' >> web/src/App.tsx
git add -A >/dev/null 2>&1
git commit -q -m "probe: web" >/dev/null 2>&1
after="$(git show HEAD:web/dist/build.json | "$python_path" -c 'import json,sys; print(json.load(sys.stdin)["hash"])')"
[ "$before" != "$after" ] && ok "the manifest hash moved ($before -> $after)" || bad "the manifest hash did not move ($before)"
[ "$(git show HEAD:web/dist/assets | grep -c 'index-')" -ge 2 ] && ok "the bundle in the commit was rebuilt" || bad "no assets in the commit"
"$python_path" -m src.server.webbuild >/dev/null 2>&1 && ok "the guard agrees the committed bundle is current" || bad "the guard says the committed bundle is stale"

step "3. a commit that touches no web source leaves web/dist alone"
mkdir -p docs && printf '# probe\n' > docs/_probe.md
git add -A >/dev/null 2>&1
"$python_path" scripts/pre_commit.py >/dev/null 2>&1
git status --porcelain web/dist | grep -q . && bad "web/dist changed" || ok "web/dist untouched"
git commit -q -m "probe: docs" >/dev/null 2>&1

step "4. an unformattable lint error stops the commit and names the escape hatch"
printf 'import os\n' > src/server/_lint_probe.py
git add -A >/dev/null 2>&1
if "$python_path" - <<'PY' >/dev/null 2>&1
import sys
sys.argv = ['hook']
from scripts import pre_commit
pre_commit.run = lambda **kw: (_ for _ in ()).throw(pre_commit.HookError('lint failed, use SKIP_PY_LINT=1'))
raise SystemExit(pre_commit._main([]))
PY
then bad "a failing step exited 0"
else ok "the hook exits non-zero on an unfixable error"; fi
git rm -q --cached src/server/_lint_probe.py && rm src/server/_lint_probe.py
git commit -q -m "probe: cleanup" >/dev/null 2>&1

step "5. the skip variables are honoured"
printf 'def  dirty( ):\n    pass\n' > src/server/_skip_probe.py
git add -A >/dev/null 2>&1
SKIP_SIGIL_HOOK=1 git commit -q -m "probe: skip" >/dev/null 2>&1
git show HEAD:src/server/_skip_probe.py | grep -q 'def  dirty' && ok "SKIP_SIGIL_HOOK=1 commits the file untouched" || bad "the hook ran anyway"

printf '\n'
if [ "$failures" -eq 0 ]; then
  printf '\033[32mall hook checks passed\033[0m\n'
else
  printf '\033[31m%d check(s) failed\033[0m\n' "$failures"
fi
cleanup
exit "$failures"
