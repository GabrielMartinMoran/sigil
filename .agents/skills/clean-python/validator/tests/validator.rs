use std::{fs, path::Path, process::Command};
use tempfile::TempDir;

fn put(root: &Path, path: &str, text: &str) {
    let path = root.join(path);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, text).unwrap();
}
fn run(root: &Path, workers: &str) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_clean-python-validator"))
        .arg("--workers")
        .arg(workers)
        .arg(root)
        .output()
        .unwrap()
}
#[test]
fn definitions_and_signatures() {
    let t = TempDir::new().unwrap();
    put(t.path(), "badName.py", "from elsewhere import Imported\nclass First:\n    def __init__(self, value): pass\n    async def method(cls, *, option):\n        def nested(arg): pass\nclass Second: pass\ndef public(x): pass\ndef _private() -> None: pass\n");
    let output = run(t.path(), "1");
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(!output.status.success());
    for needle in [
        "multiple-public-classes",
        "class-function-mix",
        "class-file-name",
        "module-name",
        "parameter-annotation",
        "return-annotation",
    ] {
        assert!(text.contains(needle), "missing {needle}: {text}");
    }
    assert!(!text.contains("parameter 'self'"));
    assert!(!text.contains("parameter 'cls'"));
}
#[test]
fn ignores_tracked_files_nested_negation_and_symlinks() {
    let t = TempDir::new().unwrap();
    assert!(Command::new("git")
        .arg("init")
        .arg("-q")
        .arg(t.path())
        .status()
        .unwrap()
        .success());
    put(t.path(), "tracked.py", "def broken(:\n");
    assert!(Command::new("git")
        .arg("-C")
        .arg(t.path())
        .args(["add", "tracked.py"])
        .status()
        .unwrap()
        .success());
    put(
        t.path(),
        ".gitignore",
        "tracked.py\n*.py\n!keep.py\n!nested/\n",
    );
    put(t.path(), "skip.py", "def broken(:\n");
    put(t.path(), "keep.py", "def good() -> None: pass\n");
    put(t.path(), "nested/.gitignore", "!keep.py\n");
    put(t.path(), "nested/keep.py", "def good() -> None: pass\n");
    #[cfg(unix)]
    std::os::unix::fs::symlink(t.path(), t.path().join("loop")).unwrap();
    let result = run(t.path(), "2");
    assert!(
        result.status.success(),
        "{} {}",
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    );
}
#[test]
fn clean_errors_and_parallel_are_stable() {
    let t = TempDir::new().unwrap();
    put(t.path(), "good.py", "def good(x: int) -> int: return x\n");
    assert!(run(t.path(), "1").status.success());
    put(t.path(), "z.py", "def bad(:\n");
    put(t.path(), "a.py", "def bad(x): pass\n");
    let a = run(t.path(), "1");
    let b = run(t.path(), "4");
    assert!(!a.status.success());
    assert_eq!(a.stdout, b.stdout);
    let text = String::from_utf8(a.stdout).unwrap();
    assert!(text.contains("parse-error"), "{text}");
    assert!(text.find("a.py").unwrap() < text.find("z.py").unwrap());
    let absent = run(&t.path().join("absent"), "1");
    assert!(!absent.status.success());
}
#[test]
fn names_are_independent_and_special_modules_exempt() {
    let t = TempDir::new().unwrap();
    put(t.path(), "Some_Class.py", "class SomeClass: pass\n");
    put(t.path(), "__init__.py", "def good() -> None: pass\n");
    put(t.path(), "__main__.py", "def good() -> None: pass\n");
    let out = run(t.path(), "1");
    let text = String::from_utf8(out.stdout).unwrap();
    assert!(text.contains("module-name"));
    assert!(!text.contains("class-file-name"), "{text}");
    assert!(!text.contains("__init__.py"));
    assert!(!text.contains("__main__.py"));
}

#[test]
fn nested_negation_reincludes_file_outside_git() {
    let t = TempDir::new().unwrap();
    put(t.path(), ".gitignore", "*.py\n");
    put(t.path(), "nested/.gitignore", "!include.py\n");
    put(t.path(), "nested/include.py", "def bad(:\n");
    put(t.path(), "nested/exclude.py", "def bad(:\n");
    let out = run(t.path(), "1");
    let text = String::from_utf8(out.stdout).unwrap();
    assert!(!out.status.success());
    assert!(text.contains("nested/include.py"), "{text}");
    assert!(!text.contains("exclude.py"), "{text}");
}

#[cfg(unix)]
#[test]
fn inaccessible_python_file_is_reported_when_permissions_apply() {
    use std::os::unix::fs::PermissionsExt;
    let t = TempDir::new().unwrap();
    let path = t.path().join("unreadable.py");
    put(t.path(), "unreadable.py", "def good() -> None: pass\n");
    fs::set_permissions(&path, fs::Permissions::from_mode(0o0)).unwrap();
    if fs::read(&path).is_err() {
        let out = run(t.path(), "1");
        assert!(!out.status.success());
        assert!(String::from_utf8(out.stdout)
            .unwrap()
            .contains("read-error"));
    }
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
}

#[test]
fn subdirectory_scan_applies_ancestor_gitignore_to_tracked_and_untracked() {
    let t = TempDir::new().unwrap();
    let repo = t.path().join("repo");
    fs::create_dir(&repo).unwrap();
    assert!(Command::new("git")
        .arg("init")
        .arg("-q")
        .arg(&repo)
        .status()
        .unwrap()
        .success());
    put(&repo, "sub/tracked.py", "def broken(:\n");
    assert!(Command::new("git")
        .arg("-C")
        .arg(&repo)
        .args(["add", "sub/tracked.py"])
        .status()
        .unwrap()
        .success());
    put(
        &repo,
        ".gitignore",
        "sub/tracked.py\nsub/untracked.py\nsub/nested/*.py\n",
    );
    put(&repo, "sub/untracked.py", "def broken(:\n");
    put(&repo, "sub/nested/.gitignore", "!included.py\n");
    put(&repo, "sub/nested/included.py", "def broken(:\n");
    put(&repo, "sub/visible.py", "def broken(:\n");
    // An ancestor outside the repository must not affect Git's ignore rules.
    put(t.path(), ".gitignore", "visible.py\n");
    // Other ignore sources must remain disabled.
    put(&repo, ".git/info/exclude", "visible.py\n");
    put(&repo, "sub/.ignore", "visible.py\n");
    let global_ignore = t.path().join("global-ignore");
    fs::write(&global_ignore, "visible.py\n").unwrap();
    let config = t.path().join("git-config");
    fs::write(
        &config,
        format!("[core]\n    excludesFile = {}\n", global_ignore.display()),
    )
    .unwrap();
    let global = Command::new(env!("CARGO_BIN_EXE_clean-python-validator"))
        .env("GIT_CONFIG_GLOBAL", config)
        .arg(repo.join("sub"))
        .output()
        .unwrap();
    assert!(String::from_utf8(global.stdout)
        .unwrap()
        .contains("visible.py"));
    let one = run(&repo.join("sub"), "1");
    let many = run(&repo.join("sub"), "4");
    assert!(!one.status.success());
    assert_eq!(one.stdout, many.stdout);
    let text = String::from_utf8(one.stdout).unwrap();
    assert!(!text.contains("tracked.py"), "{text}");
    assert!(!text.contains("untracked.py"), "{text}");
    assert!(text.contains("nested/included.py"), "{text}");
    assert!(text.contains("visible.py"), "{text}");
}

#[test]
fn subdirectory_scan_applies_ancestor_gitignore_outside_git() {
    let t = TempDir::new().unwrap();
    put(t.path(), ".gitignore", "sub/ignored.py\n");
    put(t.path(), "sub/ignored.py", "def broken(:\n");
    put(t.path(), "sub/visible.py", "def broken(:\n");
    let output = run(&t.path().join("sub"), "1");
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(!output.status.success());
    assert!(!text.contains("ignored.py"), "{text}");
    assert!(text.contains("visible.py"), "{text}");
}

#[test]
fn subdirectory_roots_follow_git_ignored_parent_and_negation_semantics() {
    let cases = [
        ("sub/\n", "", "sub", true),
        ("sub/\n", "", "sub/deep", true),
        ("sub/**\n", "", "sub", true),
        ("/sub/deep/\n", "", "sub/deep", true),
        ("sub/\n!sub/\n", "", "sub", false),
        (
            "sub/\n!sub/deep/tracked.py\n!sub/deep/untracked.py\n",
            "",
            "sub/deep",
            true,
        ),
        ("sub/*\n!sub/deep/\n", "", "sub/deep", false),
        ("sub/*\n!sub/deep/\nsub/deep/\n", "", "sub/deep", true),
        ("sub/deep/\n", "!deep/\n", "sub/deep", false),
        ("sub/\n", "!deep/\n", "sub/deep", true),
    ];
    for (root_ignore, nested_ignore, scan, ignored) in cases {
        let t = TempDir::new().unwrap();
        let repo = t.path();
        assert!(Command::new("git")
            .arg("init")
            .arg("-q")
            .arg(repo)
            .status()
            .unwrap()
            .success());
        for name in ["tracked.py", "untracked.py"] {
            put(repo, &format!("sub/deep/{name}"), "def broken(:\n");
        }
        assert!(Command::new("git")
            .arg("-C")
            .arg(repo)
            .args(["add", "sub/deep/tracked.py"])
            .status()
            .unwrap()
            .success());
        put(repo, ".gitignore", root_ignore);
        put(repo, "sub/.gitignore", nested_ignore);
        for name in ["tracked.py", "untracked.py"] {
            let status = Command::new("git")
                .arg("-C")
                .arg(repo)
                .args([
                    "check-ignore",
                    "--no-index",
                    "-q",
                    &format!("sub/deep/{name}"),
                ])
                .env("GIT_CONFIG_GLOBAL", "/dev/null")
                .env("GIT_CONFIG_NOSYSTEM", "1")
                .status()
                .unwrap();
            assert_eq!(
                status.success(),
                ignored,
                "bad fixture: {root_ignore:?} {nested_ignore:?} {scan}"
            );
        }
        let result = run(&repo.join(scan), "2");
        let text = String::from_utf8(result.stdout).unwrap();
        if ignored {
            assert!(
                result.status.success(),
                "ignored root: {root_ignore:?} {nested_ignore:?} {scan}: {text}"
            );
            assert!(text.is_empty(), "{text}");
        } else {
            assert!(
                !result.status.success(),
                "visible root: {root_ignore:?} {nested_ignore:?} {scan}: {text}"
            );
            assert!(
                text.contains("tracked.py") && text.contains("untracked.py"),
                "{text}"
            );
        }
    }
}

#[test]
fn ignored_scan_root_outside_git_is_excluded() {
    let t = TempDir::new().unwrap();
    put(t.path(), ".gitignore", "sub/\n");
    put(t.path(), "sub/deep/tracked.py", "def broken(:\n");
    put(t.path(), "sub/deep/untracked.py", "def broken(:\n");
    for scan in ["sub", "sub/deep"] {
        let result = run(&t.path().join(scan), "1");
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stdout)
        );
    }
}

#[test]
fn ancestor_walk_does_not_scan_sibling_files() {
    let t = TempDir::new().unwrap();
    assert!(Command::new("git")
        .arg("init")
        .arg("-q")
        .arg(t.path())
        .status()
        .unwrap()
        .success());
    put(t.path(), "bad.py", "def bad(:\n");
    put(t.path(), "other/bad.py", "def bad(:\n");
    put(t.path(), "sub/good.py", "def good() -> None: pass\n");
    let result = run(&t.path().join("sub"), "1");
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stdout)
    );
}

#[cfg(unix)]
#[test]
fn symlink_scan_root_does_not_traverse_directory() {
    let t = TempDir::new().unwrap();
    put(t.path(), "actual/bad.py", "def bad(:\n");
    std::os::unix::fs::symlink(t.path().join("actual"), t.path().join("link")).unwrap();
    let result = run(&t.path().join("link"), "1");
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("scan root is a symlink"));
    assert!(!String::from_utf8_lossy(&result.stdout).contains("parse-error"));
}

#[cfg(unix)]
#[test]
fn scan_root_rejects_symlink_in_any_component() {
    let t = TempDir::new().unwrap();
    put(t.path(), "actual/child/bad.py", "def bad(:\n");
    put(t.path(), "good/clean.py", "def clean() -> None: pass\n");
    std::os::unix::fs::symlink("actual", t.path().join("link")).unwrap();
    for path in ["link/child", "link/../good", "./link/child"] {
        let result = Command::new(env!("CARGO_BIN_EXE_clean-python-validator"))
            .current_dir(t.path())
            .arg(path)
            .output()
            .unwrap();
        assert!(!result.status.success(), "accepted {path}");
        assert!(
            String::from_utf8_lossy(&result.stderr).contains("symlink"),
            "{path}"
        );
        assert!(result.stdout.is_empty(), "scanned {path}");
    }
    let result = run(&t.path().join("link/child"), "1");
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("symlink"));
    assert!(result.stdout.is_empty());
    for path in ["actual/child/../child", "./good"] {
        let result = Command::new(env!("CARGO_BIN_EXE_clean-python-validator"))
            .current_dir(t.path())
            .arg(path)
            .output()
            .unwrap();
        assert!(
            !String::from_utf8_lossy(&result.stderr).contains("symlink"),
            "rejected {path}"
        );
        if path == "./good" {
            assert!(result.status.success());
        } else {
            assert!(String::from_utf8_lossy(&result.stdout).contains("parse-error"));
        }
    }
}

#[test]
fn statuses_help_and_unicode_multiline_locations() {
    let t = TempDir::new().unwrap();
    let bin = env!("CARGO_BIN_EXE_clean-python-validator");
    assert_eq!(
        Command::new(bin).arg("--help").status().unwrap().code(),
        Some(0)
    );
    put(
        t.path(),
        "example.py",
        "# café\ndef f(\n    value\n): pass\n",
    );
    let out = run(t.path(), "1");
    assert_eq!(out.status.code(), Some(1));
    let text = String::from_utf8(out.stdout).unwrap();
    assert!(
        text.contains("example.py:3: parameter-annotation:"),
        "{text}"
    );
    put(t.path(), "broken.py", "def invalid(:\n");
    assert_eq!(run(t.path(), "1").status.code(), Some(2));
    assert_eq!(run(&t.path().join("missing"), "1").status.code(), Some(2));
}

#[test]
fn module_wide_findings_point_to_relevant_definitions() {
    let t = TempDir::new().unwrap();
    put(
        t.path(),
        "example.py",
        "# comment\n\nclass Example: pass\nclass Other: pass\ndef f() -> None: pass\n",
    );
    let out = run(t.path(), "1");
    let text = String::from_utf8(out.stdout).unwrap();
    assert!(
        text.contains("example.py:4: multiple-public-classes:"),
        "{text}"
    );
    assert!(text.contains("example.py:3: class-function-mix:"), "{text}");
}
