#[derive(Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct Diagnostic {
    pub(crate) path: String,
    pub(crate) offset: usize,
    pub(crate) rule: &'static str,
    pub(crate) detail: String,
}

pub(crate) fn emit(
    out: &mut Vec<Diagnostic>,
    path: &str,
    offset: usize,
    rule: &'static str,
    detail: impl Into<String>,
) {
    out.push(Diagnostic {
        path: path.to_owned(),
        offset,
        rule,
        detail: detail.into(),
    });
}
