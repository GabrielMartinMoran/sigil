use crate::diagnostic::{emit, Diagnostic};
use ignore::WalkBuilder;
use std::{
    fs,
    path::{Path, PathBuf},
};

pub(crate) fn collect(root: &Path) -> Result<(PathBuf, Vec<PathBuf>, Vec<Diagnostic>), String> {
    let mut component_path = PathBuf::new();
    for component in root.components() {
        component_path.push(component.as_os_str());
        if fs::symlink_metadata(&component_path)
            .map_err(|e| format!("cannot inspect scan root {}: {e}", component_path.display()))?
            .file_type()
            .is_symlink()
        {
            return Err(format!(
                "scan root is a symlink: {}",
                component_path.display()
            ));
        }
    }
    if !root.is_dir() {
        return Err(format!(
            "scan root is not an accessible directory: {}",
            root.display()
        ));
    }
    // Within a Git repository, stop applying .gitignore files above its root.
    // Outside Git, keep supporting .gitignore files without requiring a repository.
    let absolute_root = fs::canonicalize(root)
        .map_err(|e| format!("cannot resolve scan root {}: {e}", root.display()))?;
    let git_root = absolute_root
        .ancestors()
        .find(|dir| dir.join(".git").exists());
    let in_git = git_root.is_some();
    // Walk the ancestor chain so that an ignored scan root, or an ignored
    // parent of it, cannot be bypassed by starting the walker at that root.
    let walk_root = git_root.unwrap_or_else(|| absolute_root.ancestors().last().unwrap());
    let selected_root = absolute_root.clone();
    let mut paths = Vec::new();
    let mut diagnostics = Vec::new();
    let mut builder = WalkBuilder::new(walk_root);
    builder
        .hidden(false)
        .ignore(false)
        .git_ignore(true)
        .git_global(false)
        .git_exclude(false)
        .parents(true)
        .require_git(in_git)
        .follow_links(false)
        .filter_entry(move |entry| {
            entry.file_name() != ".git"
                && (entry.path().starts_with(&selected_root)
                    || selected_root.starts_with(entry.path()))
        });
    for entry in builder.build() {
        match entry {
            Ok(entry) => {
                if let Some(err) = entry.error() {
                    emit(
                        &mut diagnostics,
                        &entry.path().to_string_lossy(),
                        0,
                        "traversal-error",
                        err.to_string(),
                    );
                }
                if entry.file_type().is_some_and(|t| t.is_file())
                    && entry.path().extension().is_some_and(|ext| ext == "py")
                {
                    paths.push(entry.into_path());
                }
            }
            Err(e) => emit(&mut diagnostics, "", 0, "traversal-error", e.to_string()),
        }
    }
    Ok((absolute_root, paths, diagnostics))
}
