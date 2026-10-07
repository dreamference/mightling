//! `mling-search "query" [-n N] [--json]`: search the web through this machine's SearXNG.

use std::process::ExitCode;

use clap::Parser;
use mling_web::search;

/// Search the web through the SearXNG instance on this machine.
#[derive(Parser)]
#[command(name = "mling-search", version)]
struct Args {
    /// Search terms
    #[arg(required = true, num_args = 1..)]
    query: Vec<String>,

    /// Results to return
    #[arg(
        short = 'n',
        long = "max-results",
        default_value_t = 5,
        allow_negative_numbers = true
    )]
    max_results: i64,

    /// Emit JSON instead of text
    #[arg(long)]
    json: bool,
}

fn main() -> ExitCode {
    let args = Args::parse();
    let level = mling_web::level();
    if let Some(message) = mling_web::refusal_now() {
        // Nothing is sent, and no hint: nothing is broken. `--json` says the level, so a script
        // can tell a refused search from an empty one.
        if args.json {
            let refused = serde_json::json!({"airgapped": level.name(), "error": message});
            println!("{}", serde_json::to_string_pretty(&refused).unwrap_or_default());
        } else {
            println!("❌ {message}");
        }
        return ExitCode::FAILURE;
    }
    match search::search(
        &search::searxng_url(),
        &args.query.join(" "),
        args.max_results,
        level,
    ) {
        Ok(payload) => {
            if args.json {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&payload).unwrap_or_default()
                );
            } else {
                print!("{}", search::render_text(&payload));
            }
            ExitCode::SUCCESS
        }
        Err(error) => {
            println!("❌ {}", error.error);
            if let Some(hint) = error.hint {
                println!("💡 {hint}");
            }
            ExitCode::FAILURE
        }
    }
}
