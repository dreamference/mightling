//! `ling-signal`, the small binary the bridge's system account runs from /usr/local/lib/mightling
//! (specs/DREAMFERENCE_MIGHTLING_SIGNAL.md §2, §6). The commands are `cli.rs`'s, the same ones
//! `ling signal …` runs; this binary also accepts the bridge's own (`serve`, `init`, …).

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let invocation = ling_signal::cli::Invocation { bridge: std::env::current_exe().ok(), bridge_commands: true };
    std::process::exit(ling_signal::cli::run(&args, &invocation));
}
