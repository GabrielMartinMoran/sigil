pub(crate) fn normalized(name: &str) -> String {
    let leading = name.len() - name.trim_start_matches('_').len();
    let trailing = name.len() - name.trim_end_matches('_').len();
    if leading == name.len() {
        return name.to_lowercase();
    }
    format!(
        "{}{}{}",
        &name[..leading],
        name[leading..name.len() - trailing]
            .replace('_', "")
            .to_lowercase(),
        &name[name.len() - trailing..]
    )
}
pub(crate) fn snake(name: &str) -> bool {
    let mut chars = name.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_lowercase() || c == '_')
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
}
pub(crate) fn special(name: &str) -> bool {
    name.len() > 4 && name.starts_with("__") && name.ends_with("__")
}
