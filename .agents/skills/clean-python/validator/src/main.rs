mod analysis;
mod cli;
mod diagnostic;
mod naming;
mod signatures;
mod traversal;

use std::process::ExitCode;

fn main() -> ExitCode {
    match cli::execute() {
        Ok(code) => ExitCode::from(code),
        Err(e) => {
            eprintln!("tool-error: {e}");
            ExitCode::from(2)
        }
    }
}
