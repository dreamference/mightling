//! Standalone entry point for `ling-delegate` when invoked directly rather than via `ling delegate`.

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(ling_delegate::cli::run(&args));
}
