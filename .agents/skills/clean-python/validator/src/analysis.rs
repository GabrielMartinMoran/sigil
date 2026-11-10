use crate::{
    diagnostic::{emit, Diagnostic},
    naming::{normalized, snake, special},
    signatures::Signatures,
};
use rustpython_ast::{self as ast, Visitor};
use rustpython_parser::Parse;
use std::{fs, path::Path};

pub(crate) fn analyze(path: &Path, root: &Path) -> Vec<Diagnostic> {
    let display = path
        .strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .into_owned();
    let mut out = Vec::new();
    let source = match fs::read_to_string(path) {
        Ok(s) => s,
        Err(e) => {
            emit(&mut out, &display, 0, "read-error", e.to_string());
            return out;
        }
    };
    let module = match ast::Suite::parse(&source, &display) {
        Ok(m) => m,
        Err(e) => {
            emit(
                &mut out,
                &display,
                line(&source, e.offset.into()),
                "parse-error",
                e.error.to_string(),
            );
            return out;
        }
    };
    let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
    if !special(stem) && !snake(stem) {
        emit(
            &mut out,
            &display,
            1,
            "module-name",
            "module name is not snake_case",
        );
    }
    let mut classes = Vec::new();
    let mut functions = Vec::new();
    for stmt in &module {
        match stmt {
            ast::Stmt::ClassDef(c) if !c.name.as_str().starts_with('_') => classes.push(c),
            ast::Stmt::FunctionDef(f) if !f.name.as_str().starts_with('_') => {
                functions.push(f.name.as_str())
            }
            ast::Stmt::AsyncFunctionDef(f) if !f.name.as_str().starts_with('_') => {
                functions.push(f.name.as_str())
            }
            _ => (),
        }
    }
    if classes.len() > 1 {
        emit(
            &mut out,
            &display,
            line(&source, classes[1].range.start().into()),
            "multiple-public-classes",
            "multiple public classes at module level",
        );
    }
    if !classes.is_empty() && !functions.is_empty() {
        emit(
            &mut out,
            &display,
            line(&source, classes[0].range.start().into()),
            "class-function-mix",
            "public classes and functions coexist at module level",
        );
    }
    for class in classes {
        if normalized(class.name.as_str()) != normalized(stem) {
            emit(
                &mut out,
                &display,
                line(&source, class.range.start().into()),
                "class-file-name",
                format!("class '{}' does not match module name", class.name),
            );
        }
    }
    let mut visitor = Signatures {
        path: &display,
        source: &source,
        out: &mut out,
    };
    for stmt in module {
        visitor.visit_stmt(stmt);
    }
    out
}

pub(crate) fn line(source: &str, offset: usize) -> usize {
    1 + source.as_bytes()[..offset.min(source.len())]
        .iter()
        .filter(|&&b| b == b'\n')
        .count()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::naming::{normalized, snake, special};
    #[test]
    fn name_normalization_preserves_edge_underscores() {
        assert_eq!(normalized("My_Class"), normalized("my_class"));
        assert_ne!(normalized("_MyClass"), normalized("my_class"));
        assert_ne!(normalized("MyClass_"), normalized("my_class"));
        assert!(snake("_good_name2"));
        assert!(!snake("BadName"));
        assert!(special("__main__"));
        assert!(!special("__not_special_"));
    }
    #[test]
    fn recognizes_public_module_definitions_only() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("visible.py");
        fs::write(&path, "from external import Other\nclass Visible: pass\nclass _Hidden: pass\ndef _hidden() -> None: pass\n").unwrap();
        assert!(analyze(&path, temp.path()).is_empty());
        fs::write(
            &path,
            "class Visible: pass\nclass Other: pass\nasync def public() -> None: pass\n",
        )
        .unwrap();
        let result = analyze(&path, temp.path());
        assert!(result.iter().any(|d| d.rule == "multiple-public-classes"));
        assert!(result.iter().any(|d| d.rule == "class-function-mix"));
    }
    #[test]
    fn class_matching_and_module_snake_case_are_independent() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("Bad_Name.py");
        fs::write(&path, "class BadName: pass\n").unwrap();
        let result = analyze(&path, temp.path());
        assert!(result.iter().any(|d| d.rule == "module-name"));
        assert!(!result.iter().any(|d| d.rule == "class-file-name"));
        fs::write(&path, "class Different: pass\n").unwrap();
        let result = analyze(&path, temp.path());
        assert!(result.iter().any(|d| d.rule == "module-name"));
        assert!(result.iter().any(|d| d.rule == "class-file-name"));
        let special = temp.path().join("__main__.py");
        fs::write(&special, "def run() -> None: pass\n").unwrap();
        assert!(analyze(&special, temp.path()).is_empty());
    }
    #[test]
    fn parses_python_312_generic_type_syntax() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("modern.py");
        fs::write(
            &path,
            "type Alias[T] = list[T]\ndef identity[T](value: T) -> T: return value\n",
        )
        .unwrap();
        assert!(analyze(&path, temp.path()).is_empty());
    }
    #[test]
    fn checks_all_signature_forms_and_init_none() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("example.py");
        fs::write(&path, "class Example:\n    def __init__(self, a: int) -> int: pass\n    async def run(cls, p, /, x: int, *args, flag, **kwargs) -> None:\n        def nested(missing): pass\n").unwrap();
        let result = analyze(&path, temp.path());
        assert_eq!(
            result
                .iter()
                .filter(|d| d.rule == "parameter-annotation")
                .count(),
            5
        );
        assert_eq!(
            result
                .iter()
                .filter(|d| d.rule == "return-annotation")
                .count(),
            2
        );
        fs::write(
            &path,
            "class Example:\n    def __init__(self) -> None: pass\n",
        )
        .unwrap();
        assert!(analyze(&path, temp.path()).is_empty());
    }
}
