use crate::diagnostic::{emit, Diagnostic};
use rustpython_ast::{self as ast, Visitor};

pub(crate) struct Signatures<'a> {
    pub(crate) path: &'a str,
    pub(crate) source: &'a str,
    pub(crate) out: &'a mut Vec<Diagnostic>,
}
impl Signatures<'_> {
    fn check(
        &mut self,
        name: &str,
        args: &ast::Arguments,
        returns: &Option<Box<ast::Expr>>,
        offset: usize,
    ) {
        let args = args
            .posonlyargs
            .iter()
            .chain(&args.args)
            .chain(&args.kwonlyargs)
            .map(|a| &a.def)
            .chain(args.vararg.iter().map(|a| a.as_ref()))
            .chain(args.kwarg.iter().map(|a| a.as_ref()));
        for arg in args {
            let name = arg.arg.as_str();
            if name != "self" && name != "cls" && arg.annotation.is_none() {
                emit(
                    self.out,
                    self.path,
                    crate::analysis::line(self.source, arg.range.start().into()),
                    "parameter-annotation",
                    format!("parameter '{name}' lacks an annotation"),
                );
            }
        }
        let missing = match (name, returns.as_deref()) {
            (_, None) => true,
            ("__init__", Some(ast::Expr::Constant(c))) => !matches!(c.value, ast::Constant::None),
            ("__init__", Some(_)) => true,
            _ => false,
        };
        if missing {
            emit(
                self.out,
                self.path,
                crate::analysis::line(self.source, offset),
                "return-annotation",
                format!(
                    "function '{name}' requires {}",
                    if name == "__init__" {
                        "a None return annotation"
                    } else {
                        "a return annotation"
                    }
                ),
            );
        }
    }
}
impl Visitor for Signatures<'_> {
    fn visit_stmt_function_def(&mut self, node: ast::StmtFunctionDef) {
        self.check(
            node.name.as_str(),
            &node.args,
            &node.returns,
            node.range.start().into(),
        );
        self.generic_visit_stmt_function_def(node);
    }
    fn visit_stmt_async_function_def(&mut self, node: ast::StmtAsyncFunctionDef) {
        self.check(
            node.name.as_str(),
            &node.args,
            &node.returns,
            node.range.start().into(),
        );
        self.generic_visit_stmt_async_function_def(node);
    }
}
