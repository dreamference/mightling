//! `ling-fetch "https://…" [--max-chars N] [--json]`: print a web page as readable text.

use std::process::ExitCode;

use clap::Parser;
use ling_web::fetch;

/// Fetch a URL and print its readable text.
#[derive(Parser)]
#[command(name = "ling-fetch", version)]
struct Args {
    /// Absolute http(s) URL
    url: String,

    /// Characters to return (at most 100000)
    #[arg(long, default_value_t = fetch::DEFAULT_MAX_CHARS, allow_negative_numbers = true)]
    max_chars: i64,

    /// Emit JSON (url, final_url, status, content_type, title, text, truncated) instead of text
    #[arg(long)]
    json: bool,
}

fn main() -> ExitCode {
    let args = Args::parse();
    if let Some(message) = ling_web::refusal_now() {
        println!("❌ {message}");
        return ExitCode::FAILURE;
    }
    match fetch::fetch(&args.url, args.max_chars) {
        Ok(page) => {
            if args.json {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&page).unwrap_or_default()
                );
            } else {
                print!("{}", fetch::render_text(&page, args.max_chars));
            }
            ExitCode::SUCCESS
        }
        Err(error) => {
            println!("❌ {error}");
            ExitCode::FAILURE
        }
    }
}
