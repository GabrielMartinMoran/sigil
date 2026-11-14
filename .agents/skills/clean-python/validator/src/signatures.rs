use crate::diagnostic::{emit, Diagnostic};
use ruff_python_ast::{
    self as ast,
    visitor::{walk_stmt, Visitor},
};

pub(crate) struct Signatures<'a> {
    pub(crate) path: &'a str,
    pub(crate) source: &'a str,
    pub(crate) tokens: &'a ast::token::Tokens,
    pub(crate) out: &'a mut Vec<Diagnostic>,
}
impl Signatures<'_> {
    fn check(
        &mut self,
        name: &str,
        args: &ast::Parameters,
        returns: &Option<Box<ast::Expr>>,
        definition_line: usize,
    ) {
        let args = args
            .posonlyargs
            .iter()
            .chain(&args.args)
            .chain(&args.kwonlyargs)
            .map(|a| &a.parameter)
            .chain(args.vararg.iter().map(|a| a.as_ref()))
            .chain(args.kwarg.iter().map(|a| a.as_ref()));
        for arg in args {
            let name = arg.name.as_str();
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
            ("__init__", Some(ast::Expr::NoneLiteral(_))) => false,
            ("__init__", Some(_)) => true,
            _ => false,
        };
        if missing {
            emit(
                self.out,
                self.path,
                definition_line,
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
impl<'a> Visitor<'a> for Signatures<'_> {
    fn visit_stmt(&mut self, stmt: &'a ast::Stmt) {
        if let ast::Stmt::FunctionDef(node) = stmt {
            self.check(
                node.name.as_str(),
                &node.parameters,
                &node.returns,
                crate::analysis::definition_line(
                    self.source,
                    self.tokens,
                    &node.name,
                    if node.is_async {
                        ast::token::TokenKind::Async
                    } else {
                        ast::token::TokenKind::Def
                    },
                ),
            );
        }
        walk_stmt(self, stmt);
    }
}
