//! `ling-fetch "https://…" [--max-chars N] [--json]`: print a web page as readable text.
//! `ling-fetch --download <file> [--max-bytes N] "https://…"`: save a PDF for `ling-docs`.

use std::process::ExitCode;

use clap::Parser;
use ling_web::fetch;

/// Fetch a URL and print its readable text.
#[derive(Parser)]
#[command(name = "ling-fetch", version)]
struct Args {
    /// Absolute http(s) URL. A Google share link (share.google, maps.app.goo.gl, goo.gl/maps) is
    /// resolved to the search or place it opens, without loading Google's page
    url: String,

    /// Characters to return (at most 100000)
    #[arg(long, default_value_t = fetch::DEFAULT_MAX_CHARS, allow_negative_numbers = true)]
    max_chars: i64,

    /// Emit JSON (url, final_url, status, content_type, title, text, truncated) instead of text
    #[arg(long)]
    json: bool,

    /// Save the body to this new file instead, only if it is a PDF (pdf_search's download)
    #[arg(long, value_name = "FILE")]
    download: Option<std::path::PathBuf>,

    /// With --download: the largest body kept, in bytes
    #[arg(long, default_value_t = fetch::DOWNLOAD_DEFAULT_BYTES, requires = "download")]
    max_bytes: u64,
}

fn main() -> ExitCode {
    let args = Args::parse();
    if let Some(message) = ling_web::refusal_now() {
        // As `ling-search --json`: the level, so a script can tell a refusal from a failure.
        if args.json && args.download.is_none() {
            let level = ling_web::level().name();
            print_json(&serde_json::json!({"airgapped": level, "error": message}));
        } else {
            println!("❌ {message}");
        }
        return ExitCode::FAILURE;
    }
    if let Some(dest) = &args.download {
        return match fetch::download_pdf(&args.url, dest, args.max_bytes) {
            Ok(saved) => {
                println!("{}", serde_json::to_string(&saved).unwrap_or_default());
                ExitCode::SUCCESS
            }
            Err(error) => {
                println!("❌ {error}");
                ExitCode::FAILURE
            }
        };
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
            if args.json {
                print_json(&serde_json::json!({"error": error}));
            } else {
                println!("❌ {error}");
            }
            ExitCode::FAILURE
        }
    }
}

fn print_json(value: &serde_json::Value) {
    println!(
        "{}",
        serde_json::to_string_pretty(value).unwrap_or_default()
    );
}
