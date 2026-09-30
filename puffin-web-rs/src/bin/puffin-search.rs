//! `puffin-search "query" [-n N] [--json]`: search the web through this machine's SearXNG.

use std::process::ExitCode;

use clap::Parser;
use puffin_web::search;

/// Search the web through the SearXNG instance on this machine.
#[derive(Parser)]
#[command(name = "puffin-search", version)]
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
    match search::search(
        &search::searxng_url(),
        &args.query.join(" "),
        args.max_results,
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
