//! `ling-search "query" [-n N] [--json]`: search the web through this machine's SearXNG.
//! `ling-search --read "query" [--pages N]`: search, then read the top results and print extracts.

use std::process::ExitCode;

use clap::Parser;
use ling_web::read;
use ling_web::search;

/// Search the web through the SearXNG instance on this machine.
#[derive(Parser)]
#[command(name = "ling-search", version)]
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

    /// Also read the top results: print numbered sources and an extract of each page
    #[arg(long)]
    read: bool,

    /// With --read: pages to read (1 to 5)
    #[arg(long, default_value_t = read::DEFAULT_PAGES as i64, allow_negative_numbers = true, requires = "read")]
    pages: i64,

    /// What to search: general (the default), news, science, it, map, images, videos, music,
    /// files, social, books, dictionaries, wikimedia, movies, weather
    #[arg(long, short = 'c')]
    category: Option<String>,

    /// Only results from the last day, week, month or year (engines that cannot filter are skipped)
    #[arg(long, short = 't')]
    time: Option<String>,

    /// Result language: a code such as en, pt, uk, fr, de, es (default: detected from the query)
    #[arg(long, short = 'l')]
    lang: Option<String>,
}

fn main() -> ExitCode {
    let args = Args::parse();
    let level = ling_web::level();
    if let Some(message) = ling_web::refusal_now() {
        // Nothing is sent, and no hint: nothing is broken. `--json` says the level, so a script
        // can tell a refused search from an empty one.
        if args.json {
            let refused = serde_json::json!({"airgapped": level.name(), "error": message});
            println!(
                "{}",
                serde_json::to_string_pretty(&refused).unwrap_or_default()
            );
        } else {
            println!("❌ {message}");
        }
        return ExitCode::FAILURE;
    }
    let options = match search::SearchOptions::new(
        args.category.as_deref(),
        args.time.as_deref(),
        args.lang.as_deref(),
    ) {
        Ok(options) => options,
        Err(message) => {
            println!("❌ {message}");
            return ExitCode::from(2);
        }
    };
    if args.read {
        return match read::search_and_read_with(
            &search::searxng_url(),
            &args.query.join(" "),
            read::pages_wanted(args.pages),
            level,
            &options,
        ) {
            Ok(payload) => {
                if args.json {
                    println!(
                        "{}",
                        serde_json::to_string_pretty(&payload).unwrap_or_default()
                    );
                } else {
                    print!("{}", read::render_text(&payload));
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
        };
    }
    match search::search_with(
        &search::searxng_url(),
        &args.query.join(" "),
        args.max_results,
        level,
        ling_web::node_locator::remote_node().as_ref(),
        &options,
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
