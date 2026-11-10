use crate::{analysis::analyze, traversal};
use rayon::prelude::*;
use std::{env, path::PathBuf};

pub(crate) fn execute() -> Result<u8, String> {
    let mut args = env::args_os().skip(1);
    let mut workers = None;
    let mut root: Option<PathBuf> = None;
    while let Some(arg) = args.next() {
        if arg == "--help" || arg == "-h" {
            println!("usage: clean-python-validator [--workers N] ROOT\nExit codes: 0 clean, 1 violations, 2 errors");
            return Ok(0);
        } else if arg == "--workers" {
            let raw = args.next().ok_or("--workers requires a positive integer")?;
            let value: usize = raw
                .to_str()
                .ok_or("invalid worker count")?
                .parse()
                .map_err(|_| "invalid worker count")?;
            if value == 0 {
                return Err("worker count must be positive".into());
            }
            workers = Some(value);
        } else if root.is_none() {
            root = Some(arg.into());
        } else {
            return Err("usage: clean-python-validator [--workers N] ROOT".into());
        }
    }
    let root = root.ok_or("usage: clean-python-validator [--workers N] ROOT")?;
    let (absolute_root, paths, mut diagnostics) = traversal::collect(&root)?;
    let mut pool = rayon::ThreadPoolBuilder::new();
    if let Some(n) = workers {
        pool = pool.num_threads(n);
    }
    let pool = pool
        .build()
        .map_err(|e| format!("cannot create worker pool: {e}"))?;
    diagnostics.extend(pool.install(|| {
        paths
            .par_iter()
            .flat_map_iter(|path| analyze(path, &absolute_root))
            .collect::<Vec<_>>()
    }));
    diagnostics.sort();
    for d in &diagnostics {
        if d.path.is_empty() {
            println!("{}: {}", d.rule, d.detail);
        } else if d.offset == 0 {
            println!("{}: {}: {}", d.path, d.rule, d.detail);
        } else {
            println!("{}:{}: {}: {}", d.path, d.offset, d.rule, d.detail);
        }
    }
    Ok(
        if diagnostics
            .iter()
            .any(|d| matches!(d.rule, "parse-error" | "read-error" | "traversal-error"))
        {
            2
        } else if diagnostics.is_empty() {
            0
        } else {
            1
        },
    )
}
