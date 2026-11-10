# Clean Python validator pilot

This experimental Rust CLI checks a subset of clean-python conventions across a
Python project. It reports syntax and file-access errors as well as convention
violations; it does not check semantic type correctness.

## Run the validator

Run the POSIX entry point using its installed skill path, from any working
directory. It requires `realpath`, Cargo and a Rust toolchain, available Cargo
dependencies, and a writable temporary directory. The build output is placed
outside the skill and removed afterwards. The script does not install Rust.

```sh
/path/to/clean-python/scripts/validate.sh --workers 1 /path/to/project
```

If Cargo or dependencies are unavailable, the script reports that validation
did not run and exits 2. Continue other checks, and do not report a pass.
Existing `target` directories are ignored by Git, but external installers can
still copy them; inspect their artifacts and omit build outputs when packaging.

The CLI scans `.py` files recursively, including hidden files. It applies
applicable ancestor and nested `.gitignore` rules and excludes matching tracked
files. An ignored scan root or ignored ancestor excludes its contents. Inside a
Git repository, ancestor rules stop at the repository root. Outside a
repository, ancestor `.gitignore` rules also apply. The CLI does not follow
directory symlinks, rejects scan roots with a symlink in any path component,
and skips `.git` directories.
It does not apply global Git excludes, `.git/info/exclude`, or `.ignore` files.

## Rules and diagnostics

Diagnostics include a path, one-based line when available, rule code, and detail.
Global errors have context without an invented location. Results are sorted,
independent of worker count. Exit 0 means clean, 1 means violations, and 2 means
execution, read, traversal, or parse errors. Errors take precedence over
violations. Invalid CLI arguments and inaccessible roots go to standard error;
findings and scan errors go to standard output.

The pilot checks these rules:

- Top-level public classes cannot coexist with top-level public functions, and
  a module cannot define more than one public class. Names starting with `_`
  are private. Imports and nested definitions don't count for these rules.
- Every top-level public class name must match its file stem after lowercasing
  and removing internal underscores. Leading and trailing underscores remain
  significant. Non-dunder module names must independently use ASCII snake_case.
- Functions and methods, including nested, private, and async definitions,
  must annotate all parameters and their return type. Parameters named `self`
  or `cls` are exempt. An `__init__` return annotation must be the literal
  `None`.

The parser is `rustpython-parser` 0.4. Python syntax not supported by that
parser is reported as a parse error; the pilot doesn't claim compatibility with
all Python releases. A focused test covers Python 3.12 generic type syntax,
but it does not establish complete Python 3.12 grammar support. The parser project's README identifies it as superseded
by Ruff's parser. Review parser support before using the pilot as a quality
gate for newer Python syntax.

## Verify and measure

Run focused checks from the repository root:

```sh
cargo test --manifest-path skills/clean-python/validator/Cargo.toml --bin clean-python-validator
cargo test --manifest-path skills/clean-python/validator/Cargo.toml --test validator
```

For a small local smoke measurement, a debug build scanned 300 generated,
clean one-function modules in 0.02 seconds with one worker and less than 0.01
seconds with four workers. These are observations on one machine, not a
performance guarantee. Parser version: 0.4.0; Rust compiler: 1.99.1. The test
contains no network access.

To package the skill without its local Cargo artifacts, run
`/path/to/clean-python/scripts/package.sh /path/to/clean-python.tar.gz`.
This repository-controlled archive excludes `validator/target`; external skill
installers are not controlled by this script.
